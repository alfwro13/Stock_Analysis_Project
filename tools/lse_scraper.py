import os
import time
import random
import logging
import pandas as pd
import yfinance as yf
from pathlib import Path
from typing import Optional

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

def enrich_with_yfinance(df: pd.DataFrame) -> pd.DataFrame:
    """
    Iterates through normalized tickers to fetch sector, industry, and country data.
    Enforces randomized rate limiting to prevent IP bans.
    """
    logger.info("Initiating Yahoo Finance enrichment phase. This may take a while due to rate limiting.")
    
    sectors = []
    industries = []
    countries = []
    
    total_tickers = len(df)
    
    for index, row in df.iterrows():
        ticker = row['ticker']
        company_name = row['company_name']
        
        try:
            # Query Yahoo Finance
            yf_ticker = yf.Ticker(ticker)
            info = yf_ticker.info
            
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
                
            sectors.append(sector)
            industries.append(industry)
            countries.append(country)
            
            # Log the full enriched row
            logger.info(f"Enriched [{index + 1}/{total_tickers}]: {ticker} | {company_name} | {sector} | {industry} | {country}")
            
        except Exception as e:
            logger.error(f"API failure for {ticker}: {e}")
            sectors.append('Unclassified')
            industries.append('Unclassified')
            countries.append('Unknown')
            
        finally:
            # Strictly enforced rate limiting
            sleep_time = random.uniform(1, 3)
            time.sleep(sleep_time)

    # Append enriched columns
    df['sector'] = sectors
    df['industry'] = industries
    df['country'] = countries
    
    logger.info("Yahoo Finance enrichment complete.")
    return df

def export_data(df: pd.DataFrame, output_path: str) -> None:
    """
    Subsets the required columns and writes to a standardized CSV.
    """
    required_columns = ['ticker', 'company_name', 'sector', 'industry', 'currency', 'country']
    
    # Ensure only the strictly required columns are exported, in the exact order
    try:
        final_df = df[required_columns]
    except KeyError as e:
        logger.error(f"Missing required columns before export: {e}")
        return

    # Create directories if they do not exist
    path_obj = Path(output_path)
    # Resolve relative to the project root assuming script is run from project root or inside tools
    # Using absolute path resolution relative to current working directory
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        final_df.to_csv(path_obj, index=False)
        logger.info(f"Successfully exported enriched universe to {output_path}")
    except Exception as e:
        logger.error(f"Failed to write CSV output: {e}")

def main() -> None:
    url = "https://docs.londonstockexchange.com/sites/default/files/documents/List%20of%20SETS%20securities_0.xls"
    
    # Assuming the script is executed from the project root (e.g., `python tools/lse_scraper.py`)
    output_filepath = "data/imports/uk_universe.csv"
    
    logger.info("Starting LSE Scraper Job.")
    
    # 1. Extraction
    raw_df = fetch_lse_data(url)
    if raw_df is None or raw_df.empty:
        logger.error("Extraction failed or returned empty data. Aborting job.")
        return
        
    # 2. Transformation
    clean_df = transform_lse_data(raw_df)
    
    # 3. Enrichment
    enriched_df = enrich_with_yfinance(clean_df)
    
    # 4. Export
    export_data(enriched_df, output_filepath)
    
    logger.info("LSE Scraper Job finished successfully.")

if __name__ == "__main__":
    main()