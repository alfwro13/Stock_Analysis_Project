# gilt_engine.py
import re
import logging
import requests
import pandas as pd
from datetime import datetime
from typing import Optional

from config import HISTORICAL_DIR

logger = logging.getLogger(__name__)

class GiltDataService:
    """
    Isolated data ingestion service dedicated to parsing live UK 10-Year 
    Gilt yields exclusively via FT.com, outputting to Parquet for vectorized math.
    """
    def __init__(self) -> None:
        self.ft_url = "https://markets.ft.com/data/bonds/tearsheet/summary"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5"
        }
        self.parquet_path = HISTORICAL_DIR / "UK_GILT_BASELINE.parquet"

    def fetch_live_ft_yield(self) -> Optional[float]:
        """Fetches and isolates the live yield from FT.com using cascading regex."""
        payload = {"s": "UK10YG"}
        try:
            logger.info(f"Dispatching tracking request to FT.com: {self.ft_url}?s=UK10YG")
            response = requests.get(self.ft_url, params=payload, headers=self.headers, timeout=12)
            response.raise_for_status()
            html_content = response.text
            
            # Cascading Extraction Patterns
            match = re.search(r'Yield[^<]*<span[^>]*class="[^"]*mod-ui-data-list__value[^"]*"[^>]*>\s*([\d\.,\-]+)%?\s*</span>', html_content, re.DOTALL | re.IGNORECASE)
            if not match:
                match = re.search(r'<span[^>]*>Yield</span>\s*<span[^>]*class="[^"]*value[^"]*"[^>]*>\s*([\d\.,\-]+)%?\s*</span>', html_content, re.DOTALL | re.IGNORECASE)
            if not match:
                match = re.search(r'<th[^>]*>.*?Yield.*?</th>\s*<td[^>]*>\s*([\d\.,\-]+)%?\s*</td>', html_content, re.DOTALL | re.IGNORECASE)

            if match:
                scraped_val = match.group(1).replace(",", "").strip()
                yield_float = float(scraped_val)
                if 0.0 < yield_float < 25.0:
                    logger.info(f"FT.com parsing success. Isolated Live UK10Y Yield: {yield_float}%")
                    return yield_float
            logger.warning("FT.com structure did not match regex sequences.")
        except Exception as e:
            logger.error(f"FT data layer exception: {str(e)}")
        return None

    def sync_gilt_data(self) -> bool:
        """Synchronizes the latest yield data into the local Parquet storage."""
        live_yield = self.fetch_live_ft_yield()
        if live_yield is None:
            logger.error("Sync routine halted: Yield extraction returned null.")
            return False

        today = pd.Timestamp(datetime.now().date())
        
        if self.parquet_path.exists():
            df = pd.read_parquet(self.parquet_path)
        else:
            df = pd.DataFrame(columns=['Close'])
            df.index.name = 'Date'

        if not df.empty:
            last_date = df.index[-1]
            gap_days = (today - last_date).days
            if gap_days > 0:
                dates = [last_date + pd.Timedelta(days=d) for d in range(1, gap_days + 1)]
                new_data = pd.DataFrame({'Close': [live_yield] * gap_days}, index=dates)
                df = pd.concat([df, new_data])
            else:
                df.loc[today, 'Close'] = live_yield
        else:
            # Cold boot: Seed 60 days of history to satisfy the QuantEngine correlation math requirements
            logger.info("Cold boot detected. Seeding 60-day baseline for correlation math.")
            dates = [today - pd.Timedelta(days=d) for d in range(60, -1, -1)]
            df = pd.DataFrame({'Close': [live_yield] * 61}, index=dates)

        df.index.name = 'Date'
        # Drop duplicates in case of same-day multiple runs
        df = df[~df.index.duplicated(keep='last')]
        df.to_parquet(self.parquet_path, engine='pyarrow')
        logger.info("UK Gilt Parquet synchronized successfully.")
        return True