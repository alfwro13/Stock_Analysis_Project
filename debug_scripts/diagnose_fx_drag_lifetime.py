import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import BASE_CURRENCY
import database  # noqa: F401 — must import before accounts_engine to avoid a circular import
from db_accounts import get_accounts, get_transactions
from accounts_engine import get_combined_holdings
from fx_drag_engine import _get_usd_tickers_from_db, _lifetime_buy_stats

print(f"BASE_CURRENCY = {BASE_CURRENCY!r} (Lifetime mode requires this to be 'GBP')")

portfolio = get_combined_holdings()
all_tickers = [v["ticker"] for v in portfolio.values() if v.get("ticker")]
usd_tickers = _get_usd_tickers_from_db(all_tickers)
print(f"\n{len(all_tickers)} tickers in combined holdings; {len(usd_tickers)} flagged USD in stock_signals:")
for t in sorted(usd_tickers):
    print(f"  {t}")

print("\nRaw Buy-transaction rows per USD ticker (across all accounts):")
for ticker in sorted(usd_tickers):
    rows = []
    for acc in get_accounts():
        for txn in get_transactions(acc["id"]):
            if txn["ticker"] == ticker:
                rows.append((acc["name"], txn["txn_type"], txn["currency"], txn["quantity"],
                             txn["unit_price"], txn["exchange_rate"], txn["txn_date"]))
    print(f"\n  {ticker}: {len(rows)} transaction row(s)")
    for acc_name, txn_type, currency, qty, unit_price, fx, txn_date in rows:
        print(f"    [{acc_name}] type={txn_type!r} currency={currency!r} qty={qty} "
              f"unit_price={unit_price} exchange_rate={fx} date={txn_date}")
    stats = _lifetime_buy_stats(ticker)
    print(f"    -> _lifetime_buy_stats result: {stats}")
