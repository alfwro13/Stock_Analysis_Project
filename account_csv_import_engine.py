import csv
import hashlib
import io
import logging
from datetime import datetime
from typing import Optional

import accounts_engine
from accounts_engine import fx_rate_on_date
from config import BASE_CURRENCY
from database import get_connection
from db_accounts import add_transaction, get_transactions
from utils import normalize_ticker
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)


def _cached_ticker_currency(ticker: str) -> Optional[str]:
    """asset_profiles is the app's own authoritative source for a ticker's trading currency (the
    GBp/GBX pence convention is built around this field everywhere else) — trusted over a broker
    export's self-reported currency, which has been observed to report GBP for LSE pence stocks."""
    if not ticker:
        return None
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT currency FROM asset_profiles WHERE ticker = ?", (ticker,))
        row = cursor.fetchone()
        return row["currency"] if row and row["currency"] else None
    except Exception as e:
        logger.error("Failed to look up cached currency for %s: %s", ticker, e)
        return None
    finally:
        if conn:
            conn.close()


_CSV_CASH_TYPE_MAP = {"TOP_UP": "Cash", "INTEREST_FROM_CASH": "Interest"}
_CSV_REQUIRED_COLUMNS = (
    "Title", "Type", "Timestamp", "Account Currency", "Total Amount in Account Currency",
    "Buy / Sell", "Ticker", "Price per Share in Account Currency", "Stamp Duty", "Quantity",
    "Instrument Currency", "Price per Share", "FX Fee Amount", "Dividend Eligible Quantity",
    "Dividend Amount Per Share", "Dividend Withheld Tax Amount", "Dividend Net Distribution Amount",
)


def _csv_float(value: Optional[str]) -> float:
    try:
        return float(value) if value not in (None, "") else 0.0
    except ValueError:
        return 0.0


def _csv_date(value: str) -> str:
    return datetime.strptime(value.strip(), "%d/%m/%Y").date().isoformat()


def _map_csv_row(row: dict) -> tuple:
    """One GIA-style CSV row -> add_transaction() kwargs, or (None, reason, ticker) to skip.
    See assets/csv_import_format.md for the column spec and the exchange-rate derivation."""
    row_type = (row.get("Type") or "").strip()
    if not row_type:
        return None, "blank_row", None
    if row_type == "INTERNAL_TRANSFER":
        return None, "ignored", None

    try:
        txn_date = _csv_date(row.get("Timestamp") or "")
    except ValueError:
        return None, "bad_date", None
    account_currency = (row.get("Account Currency") or BASE_CURRENCY).strip()

    if row_type in _CSV_CASH_TYPE_MAP:
        exchange_rate = 1.0 if account_currency == BASE_CURRENCY else fx_rate_on_date(account_currency, txn_date)
        return {
            "txn_type": _CSV_CASH_TYPE_MAP[row_type],
            "txn_date": txn_date,
            "currency": account_currency,
            "unit_price": _csv_float(row.get("Total Amount in Account Currency")),
            "exchange_rate": exchange_rate,
            "notes": row.get("Title") or None,
        }, None, None

    if row_type not in ("ORDER", "DIVIDEND"):
        return None, "unknown_type", None

    ticker = normalize_ticker(row["Ticker"]) if row.get("Ticker") else None
    if not ticker:
        return None, "no_ticker", "(no ticker)"

    currency = (row.get("Instrument Currency") or account_currency).strip()
    company_name = row.get("Title") or None
    isin = row.get("ISIN") or None

    # Brokers commonly report LSE trade prices already converted to GBP, but this app's own
    # market-data feed (Yahoo via asset_profiles/stock_signals/Parquet) always quotes these same
    # tickers in GBp pence — the only thing that matters for `price_in_pence` is which convention
    # the *market-data lookup* uses, not what currency the broker chose to display. Trust the app's
    # own cache over the file when it disagrees, same fix already applied to Ghostfolio import for
    # the identical mismatch (see `_cached_ticker_currency`'s docstring).
    pence_override = currency != "GBp" and _cached_ticker_currency(ticker) == "GBp"
    if pence_override:
        currency = "GBp"

    if row_type == "ORDER":
        buy_sell = (row.get("Buy / Sell") or "").strip().upper()
        txn_type = "Buy" if buy_sell == "BUY" else "Sell"
        price_native = _csv_float(row.get("Price per Share"))
        price_account = _csv_float(row.get("Price per Share in Account Currency"))
        exchange_rate = price_account / price_native if price_native else 1.0
        fee_account = _csv_float(row.get("Stamp Duty")) + _csv_float(row.get("FX Fee Amount"))
        fee_native = fee_account / exchange_rate if exchange_rate else fee_account
        mapped = {
            "txn_type": txn_type,
            "txn_date": txn_date,
            "ticker": ticker,
            "isin": isin,
            "company_name": company_name,
            "currency": currency,
            "quantity": _csv_float(row.get("Quantity")),
            "unit_price": price_native,
            "fee": round(fee_native, 6),
            "exchange_rate": exchange_rate,
            "price_in_pence": currency == "GBp",
        }
    else:
        net_distribution = _csv_float(row.get("Dividend Net Distribution Amount"))
        total_account = _csv_float(row.get("Total Amount in Account Currency"))
        exchange_rate = total_account / net_distribution if net_distribution and currency != account_currency else 1.0
        mapped = {
            "txn_type": "Dividend",
            "txn_date": txn_date,
            "ticker": ticker,
            "isin": isin,
            "company_name": company_name,
            "currency": currency,
            "quantity": _csv_float(row.get("Dividend Eligible Quantity")),
            "unit_price": _csv_float(row.get("Dividend Amount Per Share")),
            "fee": _csv_float(row.get("Dividend Withheld Tax Amount")),
            "exchange_rate": exchange_rate,
            "price_in_pence": currency == "GBp",
        }

    if pence_override:
        mapped["unit_price"] *= 100
        mapped["fee"] *= 100
        mapped["exchange_rate"] *= 0.01

    return mapped, None, None


def _csv_row_fingerprint(account_id: int, row: dict, occurrence: int) -> str:
    """Stable dedup key for one CSV row, reusing the `ghostfolio_ref` column as a generic import-dedup
    slot (prefixed so it can never collide with a real Ghostfolio UUID). `occurrence` disambiguates
    genuinely identical rows within the same file (e.g. three same-day same-amount Top Up rows)."""
    raw = "|".join([
        str(account_id), row.get("Type", ""), row.get("Timestamp", ""), row.get("Ticker", ""),
        row.get("Total Amount in Account Currency", ""), row.get("Quantity", ""), str(occurrence),
    ])
    return "csv:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _ticker_resolvable(ticker: str) -> bool:
    """A live Yahoo miss is retried once. `get_ticker_info` swallows every exception (including the
    HTTP 429 circuit breaker), so a single transient rate-limit hit on an otherwise-valid ticker
    looks identical to a genuinely delisted one — without a retry it gets permanently skipped from
    the import. The retry's own `get_ticker_info` call waits out any in-progress 429 cooldown via
    yahoo_engine's existing rate-limit lock before trying again, so no extra backoff is needed here."""
    if accounts_engine._ticker_known(ticker):
        return True
    return bool(yahoo_engine.get_ticker_info(ticker)) or bool(yahoo_engine.get_ticker_info(ticker))


_CSV_SKIP_REASON_LABELS = {
    "no_ticker": "no ticker in file",
    "unknown_type": "unrecognized row type",
    "bad_date": "unparseable date",
    "unresolved_ticker": "ticker not found (possibly delisted or mistyped)",
    "duplicate": "already imported",
    "db_error": "database error",
}


def import_csv_activities(account_id: int, csv_text: str) -> dict:
    """Imports a GIA/broker-export CSV (see assets/csv_import_format.md) into one built-in account.
    Rows whose ticker can't be resolved are skipped outright (not imported with a "Needs Review"
    flag) — there is no real market data to attach them to. Every skipped row (other than
    `INTERNAL_TRANSFER`/blank rows, which are expected noise) is reported back with its date and
    ticker so the operator can find the exact row in their file."""
    reader = csv.DictReader(io.StringIO(csv_text))
    header = set(reader.fieldnames or [])
    missing = [c for c in _CSV_REQUIRED_COLUMNS if c not in header]
    if missing:
        return {"error": f"CSV is missing required column(s): {', '.join(missing)}"}

    existing_refs = {t["ghostfolio_ref"] for t in get_transactions(account_id) if t["ghostfolio_ref"]}
    ticker_ok: dict[str, bool] = {}
    occurrence_counts: dict[str, int] = {}
    skipped_rows: list[dict] = []

    imported = 0
    ignored = 0

    def _skip(reason: str, date: Optional[str], ticker: Optional[str]) -> None:
        skipped_rows.append({"date": date, "ticker": ticker or None, "reason": _CSV_SKIP_REASON_LABELS[reason]})

    for row in reader:
        mapped, reason, unresolved_key = _map_csv_row(row)
        if mapped is None:
            if reason == "ignored":
                ignored += 1
            elif reason != "blank_row":
                _skip(reason, row.get("Timestamp"), unresolved_key or row.get("Ticker"))
            continue

        ticker = mapped.get("ticker")
        if ticker:
            if ticker not in ticker_ok:
                ticker_ok[ticker] = _ticker_resolvable(ticker)
            if not ticker_ok[ticker]:
                _skip("unresolved_ticker", mapped["txn_date"], ticker)
                continue

        fingerprint_base = "|".join([row.get("Type", ""), row.get("Timestamp", ""), row.get("Ticker", ""),
                                      row.get("Total Amount in Account Currency", ""), row.get("Quantity", "")])
        occurrence_counts[fingerprint_base] = occurrence_counts.get(fingerprint_base, 0) + 1
        ref = _csv_row_fingerprint(account_id, row, occurrence_counts[fingerprint_base])
        if ref in existing_refs:
            _skip("duplicate", mapped["txn_date"], ticker)
            continue

        if add_transaction(account_id=account_id, ghostfolio_ref=ref, **mapped) is None:
            _skip("db_error", mapped["txn_date"], ticker)
            continue
        imported += 1

    return {
        "imported": imported,
        "skipped": len(skipped_rows),
        "ignored": ignored,
        "skipped_rows": skipped_rows,
    }
