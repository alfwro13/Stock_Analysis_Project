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

# ---------------------------------------------------------
# Logging Configuration: Console (INFO) + File (ERROR)
# ---------------------------------------------------------
script_dir = Path(__file__).parent.resolve()
error_log_path = script_dir / "err.txt"

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Formatter for all logs
formatter = logging.Formatter('%(asctime)s - LSE_SCRAPER - %(levelname)s - %(message)s')

# Console Handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# File Handler for Errors only
file_handler = logging.FileHandler(error_log_path, mode='a')
file_handler.setLevel(logging.ERROR)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

def clean_company_name(name: str) -> str:
    """
    Strips LSE par value garbage and fixes all-caps names while preserving 
    properly formatted acronyms from Yahoo Finance.
    """
    if pd.isna(name) or name == 'Unknown':
        return 'Unknown'
        
    name = str(name)
    
    # 1. Strip common LSE share class suffixes (e.g., " ORD 10P", " NPV")
    name = re.sub(r'\s+ORD\b.*$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+NPV\b.*$', '', name, flags=re.IGNORECASE)
    
    # 2. Clean trailing commas, dots, or spaces
    name = name.strip(',. ')
    
    # 3. Smart Capitalization
    # Check if the letters in the string are entirely uppercase
    letters_only = re.sub(r'[^a-zA-Z]', '', name)
    if letters_only and letters_only.isupper():
        # Title case it
        name = name.title()
        
        # Fix standard corporate suffixes mapped by regex
        name = re.sub(r'\bPlc\b', 'plc', name)
        name = re.sub(r'\bLlc\b', 'LLC', name)
        name = re.sub(r'\bLtd\b', 'Ltd', name)
        name = re.sub(r'\bUk\b', 'UK', name)
        
    return name

def fetch_lse_data(url: str) -> Optional[pd.DataFrame]:
    """
    Downloads and performs the initial scrub of the LSE SETS security list.
    """
    logger.info(f"Initiating download of LSE master list from: {url}")
    try:
        # The LSE spreadsheet contains 3 rows of title/metadata before the actual headers
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
    """
    Normalizes tickers, standardizes currencies, and maps base columns.
    Handles LSE class shares (e.g., BT.A -> BT-A.L).
    """
    logger.info("Starting base transformation and normalization.")
    
    # Copy to avoid SettingWithCopyWarning
    clean_df = df.copy()
    
    # 1. Strip any trailing dots
    clean_df['Mnemonic'] = clean_df['Mnemonic'].astype(str).str.rstrip('.')
    
    # 2. Replace internal dots with hyphens for Yahoo Finance compatibility (BT.A -> BT-A)
    clean_df['Mnemonic'] = clean_df['Mnemonic'].str.replace('.', '-', regex=False)
    
    # 3. Append the London exchange suffix
    clean_df['Mnemonic'] = clean_df['Mnemonic'] + '.L'
    
    # Map currencies strictly
    clean_df['Currency'] = clean_df['Currency'].replace({'GBX': 'GBp'})
    
    # Rename columns to match the target database schema mapped in requirements
    clean_df = clean_df.rename(columns={
        'Mnemonic': 'ticker',
        'Issuer Name': 'company_name',
        'Currency': 'currency'
    })
    
    logger.info("Base transformation complete.")
    return clean_df

def enrich_and_save(df: pd.DataFrame, output_filepath: str) -> None:
    """
    Iterates through normalized tickers, fetching YF data, and writes to CSV incrementally.
    Implements checkpointing to resume from the last processed ticker if interrupted.
    Pulls correctly capitalized company names from Yahoo Finance to preserve acronyms.
    """
    logger.info("Initiating stateful Yahoo Finance enrichment phase.")
    
    path_obj = Path(output_filepath)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    
    # 1. Build the checkpoint lookup set
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

    # 2. Open file in append mode and process
    required_columns = ['ticker', 'company_name', 'sector', 'industry', 'currency', 'country', 'exchange']
    total_tickers = len(df)
    
    with open(path_obj, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=required_columns)
        
        # Write header only if we are creating a brand new file
        if not file_exists or os.path.getsize(path_obj) == 0:
            writer.writeheader()
            
        for index, row in df.iterrows():
            ticker = row['ticker']
            
            # Base LSE fallback data
            base_company_name = str(row['company_name']) if pd.notna(row['company_name']) else 'Unknown'
            currency = str(row['currency']) if pd.notna(row['currency']) else 'Unknown'
            
            # Checkpoint verification
            if ticker in processed_tickers:
                logger.info(f"Skipping [{index + 1}/{total_tickers}]: {ticker} (Already present in CSV)")
                continue
                
            try:
                # Query Yahoo Finance via central engine
                info = yahoo_engine.get_ticker_info(ticker) or {}
                
                # Extract accurately cased name from Yahoo, fallback to LSE if missing
                yf_name = info.get('shortName') or info.get('longName')
                raw_name = yf_name if yf_name else base_company_name
                
                # Run the string scrubber
                company_name = clean_company_name(raw_name)
                
                # Extract data with fallbacks
                sector = info.get('sector')
                industry = info.get('industry')
                country = info.get('country')
                
                # Null/Empty string checking
                sector = sector if sector else 'Unclassified'
                industry = industry if industry else 'Unclassified'
                country = country if country else 'Unknown'
                
                # Normalize country strings based on requirements
                if country == "United Kingdom":
                    country = "UK"
                elif country == "United States":
                    country = "US"
                
                # Log the full enriched row
                logger.info(f"Enriched [{index + 1}/{total_tickers}]: {ticker} | {company_name} | {sector} | {industry} | {country}")
                
            except Exception as e:
                logger.error(f"API failure for {ticker}: {e}")
                # Fallbacks on failure
                company_name = clean_company_name(base_company_name)
                sector = 'Unclassified'
                industry = 'Unclassified'
                country = 'Unknown'
                
            finally:
                # Atomically write the row and force disk flush
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
                
                # Strictly enforced rate limiting
                sleep_time = random.uniform(1, 3)
                time.sleep(sleep_time)

    logger.info("Yahoo Finance enrichment and export phase complete.")

def main() -> None:
    url = "https://docs.londonstockexchange.com/sites/default/files/documents/List%20of%20SETS%20securities_0.xls"
    
    # Output path relative to execution directory
    output_filepath = "data/imports/uk_universe.csv"
    
    logger.info("Starting Stateful LSE Scraper Job.")
    
    # 1. Extraction
    raw_df = fetch_lse_data(url)
    if raw_df is None or raw_df.empty:
        logger.error("Extraction failed or returned empty data. Aborting job.")
        return
        
    # 2. Transformation
    clean_df = transform_lse_data(raw_df)
    
    # 3. Enrichment & Export (Combined for Fault Tolerance)
    enrich_and_save(clean_df, output_filepath)
    
    logger.info("Stateful LSE Scraper Job finished successfully.")

if __name__ == "__main__":
    main()