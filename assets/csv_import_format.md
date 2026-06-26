# CSV Activity Import — Required Format

`POST /api/accounts/{id}/import-csv` (UI: Accounts page → **Import from CSV**) accepts a
GIA/broker-style "activity export" CSV and loads it into one built-in account's transaction
ledger. This is the second bulk-import path alongside **Import from Ghostfolio**
(`accounts_engine.import_ghostfolio_activities`); see `import_csv_activities` /
`_map_csv_row` in `accounts_engine.py` for the implementation.

## Column matching

Columns are matched **by exact header name, not position** — the file's columns can be in
any order. Every column below must be present in the header row (even if blank on a given
row) or the import is rejected up front with a message naming the missing column(s).
Columns not listed here (e.g. `ISIN`, `FX Rate`, `Base FX Rate`, `Dividend Ex Date`) are
ignored — the importer never relies on the broker's own FX-rate column (see "Exchange rate
derivation" below).

| Column | Required for |
|---|---|
| `Title` | every row (used as `company_name`, or as a note for Cash/Interest rows) |
| `Type` | every row — one of `TOP_UP`, `INTEREST_FROM_CASH`, `ORDER`, `DIVIDEND`, `INTERNAL_TRANSFER` |
| `Timestamp` | every row — `DD/MM/YYYY` |
| `Account Currency` | every row |
| `Total Amount in Account Currency` | `TOP_UP`, `INTEREST_FROM_CASH`, `DIVIDEND` |
| `Buy / Sell` | `ORDER` — `BUY` or `SELL` |
| `Ticker` | `ORDER`, `DIVIDEND` |
| `Price per Share in Account Currency` | `ORDER` |
| `Stamp Duty` | `ORDER` |
| `Quantity` | `ORDER` |
| `Instrument Currency` | `ORDER`, `DIVIDEND` |
| `Price per Share` | `ORDER` |
| `FX Fee Amount` | `ORDER` (FX trades only — blank for same-currency trades) |
| `Dividend Eligible Quantity` | `DIVIDEND` |
| `Dividend Amount Per Share` | `DIVIDEND` |
| `Dividend Withheld Tax Amount` | `DIVIDEND` |
| `Dividend Net Distribution Amount` | `DIVIDEND` |

A row whose `Type` is missing/blank is silently skipped (e.g. trailing blank lines). A row
whose `Type` is `ORDER`/`DIVIDEND` but has no `Ticker` is skipped and reported under
`unresolved_tickers` as `"(no ticker)"`.

## Recognised row types

- **`TOP_UP`** → recorded as a `Cash` transaction (a deposit into the account).
- **`INTEREST_FROM_CASH`** → recorded as an `Interest` transaction.
- **`ORDER`** → recorded as `Buy` or `Sell` depending on `Buy / Sell`.
- **`DIVIDEND`** → recorded as `Dividend`.
- **`INTERNAL_TRANSFER`** → ignored (counted in the `ignored` total, never imported) — record
  transfers between your own accounts manually via the **Transfer** action instead.

## Exchange rate derivation

The broker's own FX-rate column is **not** used — it was found to be quoted in opposite
directions for `ORDER` rows vs `DIVIDEND` rows in a real export, which would silently double
the GBP cost basis of every FX trade if used literally. Instead the exchange rate is derived
per row from columns the CSV already gives in both currencies:

- **`ORDER`**: `exchange_rate = Price per Share in Account Currency ÷ Price per Share`. For a
  same-currency trade these two columns are identical, so this naturally yields `1.0` with no
  special-casing. `Stamp Duty` and `FX Fee Amount` are both reported in Account Currency, so
  they are summed and divided by this same `exchange_rate` before being stored as the
  transaction's `fee` — `add_transaction()`/`_cash_delta()` always expects `fee` in the
  transaction's *native* currency, since it multiplies `fee × exchange_rate` itself.
- **`DIVIDEND`**: `exchange_rate = Total Amount in Account Currency ÷ Dividend Net
  Distribution Amount`. `Dividend Withheld Tax Amount` is already in the instrument's native
  currency (same as `Dividend Amount Per Share`), so it is stored as `fee` unconverted.

### Worked examples

**GBP trade** (FirstGroup, `FGP.L`): Price per Share = Price per Share in Account Currency =
`0.731667` → `exchange_rate = 1.0`. `fee = Stamp Duty`.

**FX trade** (Virgin Galactic, `SPCE`, USD): Price per Share in Account Currency =
`764.27896`, Price per Share = `1044.60` → `exchange_rate ≈ 0.7317`. `Stamp Duty (0.00) + FX
Fee Amount (0.17) = 0.17` GBP → `fee = 0.17 ÷ 0.7317 ≈ 0.232` USD.

**Dividend** (Apple, `AAPL`, USD): Total Amount in Account Currency = `0.04`, Dividend Net
Distribution Amount = `0.05` → `exchange_rate = 0.8`. `fee = Dividend Withheld Tax Amount =
0.01` USD (unconverted).

## LSE pence (GBp) override

Some brokers report LSE trade/dividend prices already converted to GBP (e.g. `4.188` for a
Rolls-Royce buy), but this app's own market-data feed (Yahoo Finance, cached in
`asset_profiles`/`stock_signals`/historical Parquet) always quotes those same tickers in **GBp
pence** (e.g. `418.80`). Only the pence convention matters for later market-value lookups — if
a transaction were stored as GBP when the ticker actually trades in pence, the historical
value chart would multiply Yahoo's pence-quoted price by the wrong factor and inflate the
chart by ~100x (a real incident this importer was built to avoid).

To prevent this, whenever the app's own cached `asset_profiles.currency` for a ticker is
`GBp` but the CSV's `Instrument Currency` says otherwise, the importer overrides the
transaction to `GBp` — scaling `unit_price`/`fee` ×100 and `exchange_rate` ×0.01 so the GBP
cash impact (`quantity × unit_price × exchange_rate`) is numerically unchanged; only the
currency label and scale change, never the amount actually paid or received. This only fires
when the ticker is already known to the app (cache miss = trust the file as written).

## Ticker resolution

Each unique `Ticker` value across `ORDER`/`DIVIDEND` rows is resolved once (cached for the
rest of the import) against the app's own `asset_profiles` cache, falling back to a live
Yahoo Finance lookup. If neither resolves it — typically a delisted or mistyped ticker —
**every row for that ticker is skipped**, not imported with a placeholder.

## Skipped-row reporting

Every skipped row (other than `INTERNAL_TRANSFER`/blank rows, which are expected and not
worth reporting) is returned individually in the response's `skipped_rows` list, each entry
giving the row's `date`, `ticker`, and a human-readable `reason` (unresolved ticker, no
ticker in file, unparseable date, unrecognized row type, already imported, or a database
error) — enough detail to find the exact row in the source file. If any rows were skipped,
the same detail is also dispatched through `notification_engine.notify()` so it remains
visible in the in-app Notifications panel after the import modal is closed, rather than only
in the one-off response.

## Re-importing the same file

Each row gets a stable fingerprint (date, type, ticker, amount, quantity, plus an occurrence
counter so genuinely-repeated rows — e.g. several identical same-day top-ups — aren't
collapsed into one) stored in the transaction's `ghostfolio_ref` column, the same dedup slot
the Ghostfolio importer uses (prefixed `csv:` so the two can never collide). Re-uploading the
same file, or a later export that simply appends new rows to the end, only imports the new
rows. Inserting rows in the *middle* of a previously-imported file before its existing rows
will shift their positions and may cause duplicates on re-import — if that happens, delete
the affected transactions and re-import the corrected file from scratch.
