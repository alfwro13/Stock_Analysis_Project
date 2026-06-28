from config import HISTORICAL_DIR
from database import get_all_account_tickers, get_mutual_fund_tickers

tickers = sorted(get_all_account_tickers())
funds = get_mutual_fund_tickers(tickers)

missing = [t for t in tickers if t not in funds and not (HISTORICAL_DIR / f"{t}.parquet").exists()]

print(f"{len(tickers)} tickers held across accounts, {len(funds)} are mutual funds.")
if missing:
    print(f"{len(missing)} missing Parquet history (check if delisted):")
    for t in missing:
        print(f"  {t}")
else:
    print("All non-fund tickers have Parquet history.")
