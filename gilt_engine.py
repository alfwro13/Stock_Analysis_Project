# gilt_engine.py
import io
import re
import logging
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import Optional

from config import HISTORICAL_DIR

logger = logging.getLogger(__name__)

class GiltDataService:
    """
    Production Fixed Income Data service combining official Bank of England 
    historical database extraction with real-time Financial Times scraping.
    Outputs directly to a unified Parquet matrix for vectorized math.
    """

    def __init__(self) -> None:
        self.boe_url = "https://www.bankofengland.co.uk/boeapps/database/_iadb-FromShowColumns.asp?csv.x=yes"
        self.ft_url = "https://markets.ft.com/data/bonds/tearsheet/summary"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5"
        }
        self.parquet_path = HISTORICAL_DIR / "UK_GILT_BASELINE.parquet"

    def fetch_historical_boe(self, start_date: str = "01/Jan/2020") -> Optional[pd.DataFrame]:
        """Queries the Bank of England IADB endpoint to fetch true historical spot data."""
        current_date_str = datetime.now(timezone.utc).strftime("%d/%b/%Y")
        payload = {
            "Datefrom": start_date,
            "Dateto": current_date_str,
            "SeriesCodes": "IUDMNPY",
            "CSVF": "TN",
            "UsingCodes": "Y",
            "VPD": "Y",
            "VFD": "N"
        }
        try:
            logger.info(f"Querying Bank of England historical archive: {start_date} -> {current_date_str}")
            response = requests.get(self.boe_url, params=payload, headers=self.headers, timeout=15)
            response.raise_for_status()
            
            df = pd.read_csv(io.BytesIO(response.content))
            if df.empty or "DATE" not in df.columns:
                logger.warning("Bank of England IADB returned an empty dataset matrix.")
                return None
            
            # Vectorized structural cleaning
            df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
            df = df.dropna(subset=["DATE"])
            df["IUDMNPY"] = pd.to_numeric(df["IUDMNPY"], errors="coerce")
            df = df.dropna(subset=["IUDMNPY"])
            
            # Rename to match standard Quant Engine layout
            df = df.rename(columns={"DATE": "Date", "IUDMNPY": "Close"})
            return df[["Date", "Close"]]
        except Exception as e:
            logger.error(f"Failed to extract historical data layers from BoE: {str(e)}")
            return None

    def fetch_live_ft_yield(self) -> Optional[float]:
        """Scrapes the live session yield from FT.com using cascading regex patterns."""
        payload = {"s": "UK10YG"}
        try:
            logger.info(f"Scraping live session snapshot via FT.com: {self.ft_url}?s=UK10YG")
            response = requests.get(self.ft_url, params=payload, headers=self.headers, timeout=12)
            response.raise_for_status()
            html_content = response.text
            
            # Pattern 1: Target standard modern FT UI data structures
            match = re.search(
                r'Yield[^<]*<span[^>]*class="[^"]*mod-ui-data-list__value[^"]*"[^>]*>\s*([\d\.,\-]+)%?\s*</span>', 
                html_content, re.DOTALL | re.IGNORECASE
            )
            # Pattern 2: Secondary target for data key-value metrics
            if not match:
                match = re.search(
                    r'<span[^>]*>Yield</span>\s*<span[^>]*class="[^"]*value[^"]*"[^>]*>\s*([\d\.,\-]+)%?\s*</span>', 
                    html_content, re.DOTALL | re.IGNORECASE
                )
            # Pattern 3: Fallback target for table cellular content layouts
            if not match:
                match = re.search(
                    r'Yields?</th>\s*<td[^>]*>\s*([\d\.,\-]+)%?\s*</td>', 
                    html_content, re.DOTALL | re.IGNORECASE
                )
            # Pattern 4: Generalized broad metrics match fallback
            if not match:
                match = re.search(r'yield\s*:\s*([\d\.]+)', html_content, re.IGNORECASE)

            if match:
                yield_val = float(match.group(1).replace(",", "").strip())
                if 0.0 < yield_val < 25.0:
                    logger.info(f"FT.com parsing success. Isolated Live Yield: {yield_val}%")
                    return yield_val
            
            # Extraction fallback: Extract primary price node if specific yield labels are masked
            price_match = re.search(r'class="mod-ui-data-list__value[^"]*">\s*([\d\.]+)', html_content)
            if price_match:
                yield_val = float(price_match.group(1))
                logger.info(f"Fallback UI extractor executed. Isolated Live Yield: {yield_val}%")
                return yield_val

            logger.warning("Financial Times content layout has shifted. Yield token missed.")
            return None
        except Exception as e:
            logger.error(f"Network transport error hitting Financial Times data layers: {str(e)}")
            return None

    def sync_gilt_data(self) -> bool:
        """
        Executes the synchronized pipeline run, blending official BoE history 
        with live FT data, and writing directly to the Parquet baseline ledger.
        """
        # 1. Pull complete historical time-series from the Central Bank
        df_boe = self.fetch_historical_boe()
        if df_boe is None or df_boe.empty:
            logger.error("Sovereign sync terminated: Historical data frame returned empty.")
            return False

        df_boe = df_boe.set_index("Date")

        # 2. Extract current real-time close indicators from Financial Times
        live_yield = self.fetch_live_ft_yield()
        if live_yield is not None:
            today_dt = pd.Timestamp(datetime.now(timezone.utc).date())
            
            # Map weekend close metrics directly back to Friday's active session
            current_weekday = today_dt.weekday()
            if current_weekday == 5:    # Saturday
                target_date = today_dt - pd.Timedelta(days=1)
            elif current_weekday == 6:  # Sunday
                target_date = today_dt - pd.Timedelta(days=2)
            else:
                target_date = today_dt
                
            # Insert the current live streaming tick
            df_boe.loc[target_date, "Close"] = live_yield
            
            # 3. Check and resolve processing latency gaps between BoE and FT
            last_boe_date = df_boe.index[-2] if target_date in df_boe.index else df_boe.index[-1]
            gap_days = (target_date - last_boe_date).days
            
            # Only pad weekend calendar days (Saturday=5, Sunday=6).
            # Do NOT retroactively fill working-day gaps — BoE data simply hasn't been published.
            if gap_days > 1:
                 weekend_fills = 0
                 for d in range(1, gap_days):
                    fill_date = last_boe_date + pd.Timedelta(days=d)
                    if fill_date.weekday() >= 5:  # Saturday or Sunday only
                        df_boe.loc[fill_date, "Close"] = live_yield
                        weekend_fills += 1
                    if weekend_fills:
                        logger.info(f"Padded {weekend_fills} weekend days with live FT yield.")

        # Ensure index sorting and structure match engine specs
        df_boe.index = pd.to_datetime(df_boe.index)
        df_boe = df_boe.sort_index()
        df_boe = df_boe[~df_boe.index.duplicated(keep="last")]
        df_boe.index.name = "Date"

        try:
            df_boe.to_parquet(self.parquet_path, engine="pyarrow")
            logger.info(f"Synchronized Hybrid Pipeline: Written {len(df_boe)} rows to local Parquet storage.")
            return True
        except Exception as e:
            logger.error(f"Failed to persist unified Parquet matrix: {str(e)}")
            return False