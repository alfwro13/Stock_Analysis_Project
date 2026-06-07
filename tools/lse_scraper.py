import os
import re
import csv
import sys
import time
import random
import logging
import pandas as pd
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from yahoo_engine import yahoo_engine

script_dir = Path(__file__).parent.resolve()
error_log_path = script_dir / "err.txt"

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

formatter = logging.Formatter('%(asctime)s - LSE_SCRAPER - %(levelname)s - %(message)s')

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

file_handler = logging.FileHandler(error_log_path, mode='a')
file_handler.setLevel(logging.ERROR)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

def clean_company_name(name: str) -> str:
    # Strips LSE par value suffixes (ORD, NPV) and title-cases all-caps names while preserving mixed-case acronyms.
    if pd.isna(name) or name == 'Unknown':
        return 'Unknown'

    name = str(name)

    # strip common LSE share class suffixes (e.g., " ORD 10P", " NPV")
    name = re.sub(r'\s+ORD\b.*$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+NPV\b.*$', '', name, flags=re.IGNORECASE)

    name = name.strip(',. ')

    letters_only = re.sub(r'[^a-zA-Z]', '', name)
    if letters_only and letters_only.isupper():
        name = name.title()
        name = re.sub(r'\bPlc\b', 'plc', name)
        name = re.sub(r'\bLlc\b', 'LLC', name)
        name = re.sub(r'\bLtd\b', 'Ltd', name)
        name = re.sub(r'\bUk\b', 'UK', name)

    return name

def fetch_lse_data(url: str) -> Optional[pd.DataFrame]:
    logger.info(f"Initiating download of LSE master list from: {url}")
    try:
        # LSE spreadsheet has 3 title/metadata rows before the real headers
        df = pd.read_excel(url, header=3)
        
        # Strict scrub: Drop any rows where 'Mnemonic' is NaN (formatting artifacts)
        initial_count = len(df)
        df = df.dropna(subset=['Mnemonic'])
        final_count = len(df)
        
        logger.info(f"Successfully loaded LSE data. Dropped {initial_count - final_count} empty/formatting rows. {final_count} securities remain.")
        return df
    except Exception as e:
        logger.error(f"Critical error fetching LSE data: {e}")
        return None

def transform_lse_data(df: pd.DataFrame) -> pd.DataFrame:
    logger.info("Starting base transformation and normalization.")

    clean_df = df.copy()  # avoid SettingWithCopyWarning

    clean_df['Mnemonic'] = clean_df['Mnemonic'].astype(str).str.rstrip('.')
    # internal dots → hyphens for Yahoo Finance compatibility (BT.A → BT-A.L)
    clean_df['Mnemonic'] = clean_df['Mnemonic'].str.replace('.', '-', regex=False)
    clean_df['Mnemonic'] = clean_df['Mnemonic'] + '.L'

    clean_df['Currency'] = clean_df['Currency'].replace({'GBX': 'GBp'})

    clean_df = clean_df.rename(columns={
        'Mnemonic': 'ticker',
        'Issuer Name': 'company_name',
        'Currency': 'currency'
    })
    
    logger.info("Base transformation complete.")
    return clean_df

def enrich_and_save(df: pd.DataFrame, output_filepath: str) -> None:
    # Writes to CSV incrementally with checkpointing so interrupted runs resume from the last processed ticker.
    logger.info("Initiating stateful Yahoo Finance enrichment phase.")

    path_obj = Path(output_filepath)
    path_obj.parent.mkdir(parents=True, exist_ok=True)

    processed_tickers = set()
    file_exists = path_obj.exists()

    if file_exists:
        try:
            existing_df = pd.read_csv(path_obj)
            if 'ticker' in existing_df.columns:
                processed_tickers = set(existing_df['ticker'].dropna().astype(str))
                logger.info(f"Found existing data. Resuming operation. Skipping {len(processed_tickers)} already processed tickers.")
        except Exception as e:
            logger.error(f"Could not read existing checkpoint file. Proceeding cautiously. Error: {e}")

    required_columns = ['ticker', 'company_name', 'sector', 'industry', 'currency', 'country', 'exchange']
    total_tickers = len(df)

    with open(path_obj, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=required_columns)

        if not file_exists or os.path.getsize(path_obj) == 0:
            writer.writeheader()

        for index, row in df.iterrows():
            ticker = row['ticker']

            base_company_name = str(row['company_name']) if pd.notna(row['company_name']) else 'Unknown'
            currency = str(row['currency']) if pd.notna(row['currency']) else 'Unknown'

            if ticker in processed_tickers:
                logger.info(f"Skipping [{index + 1}/{total_tickers}]: {ticker} (Already present in CSV)")
                continue

            try:
                info = yahoo_engine.get_ticker_info(ticker) or {}

                yf_name = info.get('shortName') or info.get('longName')
                raw_name = yf_name if yf_name else base_company_name
                company_name = clean_company_name(raw_name)

                sector = info.get('sector') or 'Unclassified'
                industry = info.get('industry') or 'Unclassified'
                country = info.get('country') or 'Unknown'

                if country == "United Kingdom":
                    country = "UK"
                elif country == "United States":
                    country = "US"

                logger.info(f"Enriched [{index + 1}/{total_tickers}]: {ticker} | {company_name} | {sector} | {industry} | {country}")

            except Exception as e:
                logger.error(f"API failure for {ticker}: {e}")
                company_name = clean_company_name(base_company_name)
                sector = 'Unclassified'
                industry = 'Unclassified'
                country = 'Unknown'

            finally:
                # f.flush() forces each row to disk immediately so a crash loses at most one row.
                writer.writerow({
                    'ticker': ticker,
                    'company_name': company_name,
                    'sector': sector,
                    'industry': industry,
                    'currency': currency,
                    'country': country,
                    'exchange': 'LSE'
                })
                f.flush()

                sleep_time = random.uniform(1, 3)
                time.sleep(sleep_time)

    logger.info("Yahoo Finance enrichment and export phase complete.")

def main() -> None:
    url = "https://docs.londonstockexchange.com/sites/default/files/documents/List%20of%20SETS%20securities_0.xls"
    output_filepath = "data/imports/uk_universe.csv"

    logger.info("Starting Stateful LSE Scraper Job.")

    raw_df = fetch_lse_data(url)
    if raw_df is None or raw_df.empty:
        logger.error("Extraction failed or returned empty data. Aborting job.")
        return

    clean_df = transform_lse_data(raw_df)
    enrich_and_save(clean_df, output_filepath)

    logger.info("Stateful LSE Scraper Job finished successfully.")

if __name__ == "__main__":
    main()