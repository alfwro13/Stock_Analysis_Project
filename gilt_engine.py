import io
import re
import time
import logging
import requests
import pandas as pd
from datetime import datetime, timezone
from typing import Optional

from config import HISTORICAL_DIR
from utils import write_parquet_atomic

logger = logging.getLogger(__name__)

FT_MAX_DAILY_CHANGE_PTS = 2.0
_FT_CHANGE_PATTERN = re.compile(
    r"Today(?:'|&#39;|&#x27;|\u2019)s\s+Change\s*</span>\s*<span[^>]*>(?:\s*<[^>]+>)*\s*([+\-\u2212]?\d+(?:\.\d+)?)\s*/",
    re.DOTALL | re.IGNORECASE,
)


class GiltDataService:

    def __init__(self) -> None:
        self.boe_url = "https://www.bankofengland.co.uk/boeapps/database/_iadb-FromShowColumns.asp?csv.x=yes"
        self.ft_url = "https://markets.ft.com/data/bonds/tearsheet/summary"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5"
        }
        self.parquet_path = HISTORICAL_DIR / "UK_GILT_BASELINE.parquet"

    def _get_with_retry(self, url: str, *, params=None, timeout: int, retries: int = 3) -> requests.Response:
        """GET with exponential backoff (1 s, 2 s, 4 s); raises on final failure; no finally block — requests manages its own connection pool."""
        for attempt in range(retries):
            try:
                response = requests.get(url, params=params, headers=self.headers, timeout=timeout)
                response.raise_for_status()
                return response
            except requests.RequestException as e:
                if attempt == retries - 1:
                    raise
                wait = 2 ** attempt
                logger.warning("Request to %s failed (attempt %d/%d): %s. Retrying in %ds.", url, attempt + 1, retries, e, wait)
                time.sleep(wait)

    def fetch_historical_boe(self, start_date: str = "01/Jan/2020") -> Optional[pd.DataFrame]:
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
            logger.info("Querying Bank of England historical archive: %s -> %s", start_date, current_date_str)
            response = self._get_with_retry(self.boe_url, params=payload, timeout=15)
            
            df = pd.read_csv(io.BytesIO(response.content))
            if df.empty or "DATE" not in df.columns or "IUDMNPY" not in df.columns:
                logger.warning("Bank of England IADB returned an empty or malformed dataset.")
                return None

            df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
            df = df.dropna(subset=["DATE"])
            df["IUDMNPY"] = pd.to_numeric(df["IUDMNPY"], errors="coerce")
            df = df.dropna(subset=["IUDMNPY"])

            if df.empty:
                logger.warning("Bank of England IADB dataset contained no usable rows after cleaning.")
                return None

            df = df.rename(columns={"DATE": "Date", "IUDMNPY": "Close"})
            return df[["Date", "Close"]]
        except Exception:
            logger.exception("Failed to extract historical data layers from BoE.")
            return None

    @staticmethod
    def _parse_ft_change(html_content: str) -> Optional[float]:
        match = _FT_CHANGE_PATTERN.search(html_content)
        if not match:
            return None
        change = float(match.group(1).replace("\u2212", "-"))
        if abs(change) >= FT_MAX_DAILY_CHANGE_PTS:
            logger.warning("FT.com Today's Change rejected as out of range: %s", change)
            return None
        return change

    def fetch_live_ft_yield(self) -> Optional[float]:
        quote = self.fetch_live_ft_quote()
        return None if quote is None else quote[0]

    def fetch_live_ft_quote(self) -> Optional[tuple[float, Optional[float]]]:
        """(yield %, FT's own change since the previous close in percentage points or None), or None when no yield could be read."""
        payload = {"s": "UK10YG"}
        try:
            logger.info("Scraping live session snapshot via FT.com: %s?s=UK10YG", self.ft_url)
            response = self._get_with_retry(self.ft_url, params=payload, timeout=12)
            html_content = response.text
            
            match = re.search(
                r'Yield[^<]*<span[^>]*class="[^"]*mod-ui-data-list__value[^"]*"[^>]*>\s*([\d\.,\-]+)%?\s*</span>',
                html_content, re.DOTALL | re.IGNORECASE
            )
            if not match:
                match = re.search(
                    r'<span[^>]*>Yield</span>\s*<span[^>]*class="[^"]*value[^"]*"[^>]*>\s*([\d\.,\-]+)%?\s*</span>',
                    html_content, re.DOTALL | re.IGNORECASE
                )
            if not match:
                match = re.search(
                    r'Yields?</th>\s*<td[^>]*>\s*([\d\.,\-]+)%?\s*</td>',
                    html_content, re.DOTALL | re.IGNORECASE
                )
            if not match:
                match = re.search(r'yield\s*:\s*([\d\.]+)', html_content, re.IGNORECASE)

            if match:
                yield_val = float(match.group(1).replace(",", "").strip())
                if 0.0 < yield_val < 25.0:
                    logger.info("FT.com parsing success. Isolated Live Yield: %s%%", yield_val)
                    return yield_val, self._parse_ft_change(html_content)

            # Price-node fallback when the Yield label is absent from the page
            price_match = re.search(r'class="mod-ui-data-list__value[^"]*">\s*([\d\.]+)', html_content)
            if price_match:
                yield_val = float(price_match.group(1))
                if 0.0 < yield_val < 25.0:
                    logger.info("Fallback UI extractor executed. Isolated Live Yield: %s%%", yield_val)
                    return yield_val, self._parse_ft_change(html_content)
                logger.warning("Fallback UI extractor rejected out-of-range value: %s", yield_val)

            logger.warning("Financial Times content layout has shifted. Yield token missed.")
            return None
        except requests.RequestException as e:
            logger.warning("Financial Times request failed after retries, keeping the stored value: %s: %s", type(e).__name__, e)
            return None
        except Exception:
            logger.exception("Unexpected error reading the Financial Times quote.")
            return None

    def sync_gilt_data(self) -> bool:
        df_boe = self.fetch_historical_boe()
        if df_boe is None or df_boe.empty:
            logger.error("Sovereign sync terminated: Historical data frame returned empty.")
            return False

        df_boe = df_boe.set_index("Date").sort_index()
        df_boe = df_boe[~df_boe.index.duplicated(keep="last")]

        try:
            write_parquet_atomic(df_boe, self.parquet_path)
            logger.info("Synchronized BoE gilt baseline: Written %d rows to local Parquet storage.", len(df_boe))
            return True
        except Exception as e:
            logger.error("Failed to persist unified Parquet matrix: %s", e)
            return False
