#!/usr/bin/env python3
"""
tools/diagnose_garp_gaps.py

READ-ONLY diagnostic script auditing data availability required by the
planned GARP "Peter Lynch Tenbaggers" market report across the tracked
universe (FTSE 100, S&P 500, and any other is_index=1 tickers).

Issues NO writes. Performs SELECT queries only.

Outputs:
  1. Live terminal report with ANSI colour coding
  2. Persistent markdown report in ./diagnostic_reports/

Run from project root:
    python -m tools.diagnose_garp_gaps
    OR
    python tools/diagnose_garp_gaps.py
"""

import logging
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Allow direct execution from project root or as a module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import get_connection

# ─────────────────────────────────────────────────────────────────────────────
# Module-level logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - DIAGNOSE_GARP - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Terminal formatting helpers
# ─────────────────────────────────────────────────────────────────────────────
class Color:
    CYAN   = '\033[96m'
    GREEN  = '\033[92m'
    YELLOW = '\033[93m'
    RED    = '\033[91m'
    BOLD   = '\033[1m'
    DIM    = '\033[2m'
    END    = '\033[0m'


def hr(title: str) -> str:
    """Render a banner header line."""
    line = '═' * 78
    return f"\n{Color.CYAN}{Color.BOLD}{line}\n  {title}\n{line}{Color.END}\n"


def pct(num: int, denom: int) -> str:
    """Return a traffic-light coloured percentage string."""
    if denom == 0:
        return f"{Color.DIM}   n/a{Color.END}"
    p = (num / denom) * 100.0
    if p >= 90.0:
        c = Color.GREEN
    elif p >= 50.0:
        c = Color.YELLOW
    else:
        c = Color.RED
    return f"{c}{p:6.2f}%{Color.END}"


# ─────────────────────────────────────────────────────────────────────────────
# Dual-sink output: terminal + markdown buffer
# ─────────────────────────────────────────────────────────────────────────────
_md_lines: List[str] = []
_ANSI_RE = re.compile(r'\033\[[0-9;]*m')


def emit(text: str) -> None:
    """Print to terminal AND append a clean (de-ANSI'd) copy to the markdown buffer."""
    print(text)
    _md_lines.append(_ANSI_RE.sub('', text))


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — Universe inventory
# ─────────────────────────────────────────────────────────────────────────────
def section_universe_inventory(cursor: sqlite3.Cursor) -> int:
    """Count and break down tickers in market_universe."""
    emit(hr("1. UNIVERSE INVENTORY"))

    cursor.execute("SELECT COUNT(*) AS c FROM market_universe")
    total: int = cursor.fetchone()['c']
    emit(f"  Total tickers in market_universe        : {Color.BOLD}{total:>6,}{Color.END}")

    cursor.execute("SELECT COUNT(*) AS c FROM market_universe WHERE is_index = 1")
    idx_total: int = cursor.fetchone()['c']
    emit(f"  Tickers with is_index = 1 (universe)    : {Color.BOLD}{idx_total:>6,}{Color.END}")

    cursor.execute("SELECT COUNT(*) AS c FROM market_universe WHERE is_freetrade = 1")
    ft_total: int = cursor.fetchone()['c']
    emit(f"  Tickers with is_freetrade = 1           : {Color.BOLD}{ft_total:>6,}{Color.END}")

    cursor.execute("""
        SELECT COALESCE(index_membership, '(none)') AS membership, COUNT(*) AS c
        FROM market_universe
        WHERE is_index = 1
        GROUP BY membership
        ORDER BY c DESC
    """)
    emit("\n  Breakdown by index_membership (is_index = 1 only):")
    for row in cursor.fetchall():
        emit(f"    {row['membership']:<30} : {row['c']:>5,}")

    cursor.execute("""
        SELECT COALESCE(country, '(unknown)') AS country, COUNT(*) AS c
        FROM market_universe
        WHERE is_index = 1
        GROUP BY country
        ORDER BY c DESC
        LIMIT 10
    """)
    emit("\n  Breakdown by country (top 10):")
    for row in cursor.fetchall():
        emit(f"    {row['country']:<30} : {row['c']:>5,}")

    return idx_total


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 — Table coverage for universe tickers
# ─────────────────────────────────────────────────────────────────────────────
def section_table_coverage(cursor: sqlite3.Cursor, universe_total: int) -> None:
    """For tickers with is_index=1, count presence in each downstream table."""
    emit(hr("2. TABLE COVERAGE FOR UNIVERSE STOCKS (is_index = 1)"))

    cursor.execute("""
        SELECT COUNT(*) AS c
        FROM market_universe m
        INNER JOIN stock_signals s ON m.ticker = s.ticker
        WHERE m.is_index = 1
    """)
    ss_count: int = cursor.fetchone()['c']
    emit(f"  In stock_signals         : {ss_count:>5,} / {universe_total:>5,}  ({pct(ss_count, universe_total)})")

    cursor.execute("""
        SELECT COUNT(DISTINCT m.ticker) AS c
        FROM market_universe m
        INNER JOIN quant_signals q ON m.ticker = q.ticker
        WHERE m.is_index = 1
    """)
    qs_any: int = cursor.fetchone()['c']
    emit(f"  In quant_signals (any)   : {qs_any:>5,} / {universe_total:>5,}  ({pct(qs_any, universe_total)})")

    cursor.execute("""
        SELECT COUNT(DISTINCT m.ticker) AS c
        FROM market_universe m
        INNER JOIN quant_signals q ON m.ticker = q.ticker
        WHERE m.is_index = 1
          AND q.date >= date('now', '-7 days')
    """)
    qs_fresh: int = cursor.fetchone()['c']
    emit(f"  quant_signals ≤ 7 days   : {qs_fresh:>5,} / {universe_total:>5,}  ({pct(qs_fresh, universe_total)})")

    try:
        cursor.execute("""
            SELECT COUNT(*) AS c
            FROM market_universe m
            INNER JOIN ticker_metadata tm ON m.ticker = tm.ticker
            WHERE m.is_index = 1
        """)
        tm_count: int = cursor.fetchone()['c']
        emit(f"  In ticker_metadata       : {tm_count:>5,} / {universe_total:>5,}  ({pct(tm_count, universe_total)})")
    except sqlite3.OperationalError:
        emit(f"  {Color.RED}ticker_metadata table missing — run ai_prediction_engine.run_historical_backfill() first.{Color.END}")

    cursor.execute("""
        SELECT COUNT(*) AS c
        FROM market_universe m
        INNER JOIN asset_profiles p ON m.ticker = p.ticker
        WHERE m.is_index = 1
    """)
    ap_count: int = cursor.fetchone()['c']
    emit(f"  In asset_profiles        : {ap_count:>5,} / {universe_total:>5,}  ({pct(ap_count, universe_total)})")


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — Field coverage in stock_signals
# ─────────────────────────────────────────────────────────────────────────────
def section_field_coverage_stock_signals(cursor: sqlite3.Cursor) -> int:
    """For universe-resident rows in stock_signals, count non-NULL per GARP field."""
    emit(hr("3. FIELD COVERAGE IN stock_signals (universe-resident rows)"))

    cursor.execute("""
        SELECT COUNT(*) AS c
        FROM stock_signals s
        INNER JOIN market_universe m ON s.ticker = m.ticker
        WHERE m.is_index = 1
    """)
    base: int = cursor.fetchone()['c']
    emit(f"  Universe rows in stock_signals: {Color.BOLD}{base:,}{Color.END}\n")

    if base == 0:
        emit(f"  {Color.RED}No universe rows — nothing to check.{Color.END}")
        return 0

    cursor.execute("""
        SELECT COALESCE(s.score_method, '(NULL)') AS method, COUNT(*) AS c
        FROM stock_signals s
        INNER JOIN market_universe m ON s.ticker = m.ticker
        WHERE m.is_index = 1
        GROUP BY method
        ORDER BY c DESC
    """)
    emit("  score_method breakdown:")
    for row in cursor.fetchall():
        emit(f"    {row['method']:<30} : {row['c']:>5,}  ({pct(row['c'], base)})")

    fields_to_check = [
        ('peter_lynch_peg',  'Peter Lynch PEG  (GARP GATE)'),
        ('forward_pe',       'Forward PE'),
        ('trailing_pe',      'Trailing PE'),
        ('revenue_growth',   'Revenue Growth YoY'),
        ('roe',              'Return on Equity'),
        ('profit_margin',    'Profit Margin'),
        ('debt_to_equity',   'Debt/Equity'),
        ('dividend_yield',   'Dividend Yield'),
        ('current_price',    'Current Price'),
    ]
    emit("\n  Non-NULL field coverage (% of universe-resident rows):")
    for field, label in fields_to_check:
        cursor.execute(f"""
            SELECT COUNT(*) AS c
            FROM stock_signals s
            INNER JOIN market_universe m ON s.ticker = m.ticker
            WHERE m.is_index = 1
              AND s.{field} IS NOT NULL
        """)
        c: int = cursor.fetchone()['c']
        marker = ''
        if field == 'peter_lynch_peg' and c < base:
            marker = f' {Color.RED}⚠️ CRITICAL FOR GARP{Color.END}'
        emit(f"    {label:<35} : {c:>5,} / {base:>5,}  ({pct(c, base)}){marker}")

    return base


# ─────────────────────────────────────────────────────────────────────────────
# Section 4 — Field coverage in quant_signals (latest row per ticker)
# ─────────────────────────────────────────────────────────────────────────────
def section_field_coverage_quant_signals(cursor: sqlite3.Cursor) -> None:
    """For universe rows in latest quant_signals snapshot, check field NULL coverage."""
    emit(hr("4. FIELD COVERAGE IN quant_signals (latest row per universe ticker)"))

    latest_cte = """
        WITH latest AS (
            SELECT q.*
            FROM quant_signals q
            INNER JOIN (
                SELECT ticker, MAX(date) AS max_date
                FROM quant_signals
                GROUP BY ticker
            ) l ON q.ticker = l.ticker AND q.date = l.max_date
        )
    """

    cursor.execute(latest_cte + """
        SELECT COUNT(*) AS c
        FROM latest lq
        INNER JOIN market_universe m ON lq.ticker = m.ticker
        WHERE m.is_index = 1
    """)
    base: int = cursor.fetchone()['c']
    emit(f"  Universe tickers with a latest quant_signals row: {Color.BOLD}{base:,}{Color.END}\n")

    if base == 0:
        emit(f"  {Color.RED}No universe rows in quant_signals — run quant_engine or backfill.{Color.END}")
        return

    qs_fields = [
        ('ml_confidence_score', 'ML Confidence Score  (GARP CORROBORATION)'),
        ('close_price',         'Close Price'),
        ('rsi_14',              'RSI (14)'),
        ('macd',                'MACD'),
        ('sma_50',              'SMA 50'),
        ('sma_200',             'SMA 200'),
        ('var_95',              'VaR 95'),
        ('sentiment_score',     'Sentiment Score'),
    ]
    emit("  Non-NULL field coverage on latest row:")
    for field, label in qs_fields:
        cursor.execute(latest_cte + f"""
            SELECT COUNT(*) AS c
            FROM latest lq
            INNER JOIN market_universe m ON lq.ticker = m.ticker
            WHERE m.is_index = 1
              AND lq.{field} IS NOT NULL
        """)
        c: int = cursor.fetchone()['c']
        marker = ''
        if field == 'ml_confidence_score' and c < base:
            marker = f' {Color.RED}⚠️ CRITICAL FOR GARP{Color.END}'
        emit(f"    {label:<45} : {c:>5,} / {base:>5,}  ({pct(c, base)}){marker}")

    cursor.execute("""
        WITH latest AS (
            SELECT ticker, MAX(date) AS max_date
            FROM quant_signals
            GROUP BY ticker
        )
        SELECT max_date, COUNT(*) AS c
        FROM latest lq
        INNER JOIN market_universe m ON lq.ticker = m.ticker
        WHERE m.is_index = 1
        GROUP BY max_date
        ORDER BY max_date DESC
        LIMIT 5
    """)
    emit("\n  Top 5 most recent latest-dates among universe tickers:")
    for row in cursor.fetchall():
        emit(f"    {row['max_date']} : {row['c']:>5,} tickers")


# ─────────────────────────────────────────────────────────────────────────────
# Section 5 — Market cap coverage
# ─────────────────────────────────────────────────────────────────────────────
def section_market_cap_coverage(cursor: sqlite3.Cursor) -> None:
    """Audit ticker_metadata.market_cap availability + distribution."""
    emit(hr("5. ticker_metadata.market_cap COVERAGE & DISTRIBUTION"))

    try:
        cursor.execute("""
            SELECT COUNT(*) AS c
            FROM ticker_metadata tm
            INNER JOIN market_universe m ON tm.ticker = m.ticker
            WHERE m.is_index = 1
        """)
        base: int = cursor.fetchone()['c']
        emit(f"  Universe tickers in ticker_metadata: {Color.BOLD}{base:,}{Color.END}\n")

        if base == 0:
            emit(f"  {Color.YELLOW}ticker_metadata empty for universe — run ML historical backfill.{Color.END}")
            return

        cursor.execute("""
            SELECT COUNT(*) AS c
            FROM ticker_metadata tm
            INNER JOIN market_universe m ON tm.ticker = m.ticker
            WHERE m.is_index = 1
              AND tm.market_cap > 0
        """)
        nonzero: int = cursor.fetchone()['c']
        emit(f"  With market_cap > 0           : {nonzero:>5,} / {base:>5,}  ({pct(nonzero, base)})")

        cursor.execute("""
            SELECT COUNT(*) AS c
            FROM ticker_metadata tm
            INNER JOIN market_universe m ON tm.ticker = m.ticker
            WHERE m.is_index = 1
              AND tm.market_cap > 500000000
        """)
        gate: int = cursor.fetchone()['c']
        emit(f"  With market_cap > $500M       : {gate:>5,} / {base:>5,}  ({pct(gate, base)})")

        cursor.execute("""
            SELECT
                MIN(market_cap) AS mn,
                MAX(market_cap) AS mx,
                AVG(market_cap) AS av
            FROM ticker_metadata tm
            INNER JOIN market_universe m ON tm.ticker = m.ticker
            WHERE m.is_index = 1
              AND tm.market_cap > 0
        """)
        stats = cursor.fetchone()
        if stats and stats['mn'] is not None:
            emit(f"\n  Distribution (where market_cap > 0):")
            emit(f"    Min  : ${stats['mn']:>20,.0f}")
            emit(f"    Avg  : ${stats['av']:>20,.0f}")
            emit(f"    Max  : ${stats['mx']:>20,.0f}")
    except sqlite3.OperationalError as e:
        emit(f"  {Color.RED}Error querying ticker_metadata: {e}{Color.END}")


# ─────────────────────────────────────────────────────────────────────────────
# Section 6 — GARP filter funnel simulation
# ─────────────────────────────────────────────────────────────────────────────
def section_garp_filter_funnel(cursor: sqlite3.Cursor) -> None:
    """Apply each planned GARP filter cumulatively and report how many survive."""
    emit(hr("6. GARP FILTER SIMULATION — WHERE DO WE LOSE STOCKS?"))

    base_query_template = """
        WITH latest_quant AS (
            SELECT q.*
            FROM quant_signals q
            INNER JOIN (
                SELECT ticker, MAX(date) AS max_date
                FROM quant_signals
                GROUP BY ticker
            ) l ON q.ticker = l.ticker AND q.date = l.max_date
        )
        SELECT COUNT(DISTINCT s.ticker) AS c
        FROM stock_signals s
        INNER JOIN market_universe m ON s.ticker = m.ticker
        LEFT JOIN latest_quant lq ON s.ticker = lq.ticker
        LEFT JOIN ticker_metadata tm ON s.ticker = tm.ticker
        WHERE m.is_index = 1
        {predicate}
    """

    filters = [
        ("Base: universe + in stock_signals",         ""),
        ("AND quote_type = 'EQUITY'",                 "AND COALESCE(s.quote_type, m.quote_type, 'EQUITY') = 'EQUITY'"),
        ("AND peter_lynch_peg BETWEEN 0 AND 1.0",     "AND s.peter_lynch_peg > 0 AND s.peter_lynch_peg <= 1.0"),
        ("AND revenue_growth > 0.15",                 "AND s.revenue_growth > 0.15"),
        ("AND roe > 0.10",                            "AND s.roe > 0.10"),
        ("AND forward_pe BETWEEN 10 AND 40",          "AND s.forward_pe BETWEEN 10 AND 40"),
        ("AND market_cap > $500M",                    "AND COALESCE(tm.market_cap, 0) > 500000000"),
        ("AND ml_confidence_score >= 45",             "AND lq.ml_confidence_score >= 45"),
    ]

    emit("  Cumulative funnel (each row adds its filter on top of all previous):\n")
    cumulative_predicates: List[str] = []
    for label, predicate in filters:
        if predicate:
            cumulative_predicates.append(predicate)
        full_predicate = "\n          ".join(cumulative_predicates)
        query = base_query_template.format(predicate=full_predicate)
        cursor.execute(query)
        c: int = cursor.fetchone()['c']
        emit(f"    {label:<48} : {Color.BOLD}{c:>5,}{Color.END} remain")

    emit(f"\n  {Color.CYAN}Independent filter pass rates (each filter alone, applied to base universe):{Color.END}\n")
    for label, predicate in filters[1:]:
        query = base_query_template.format(predicate=predicate)
        cursor.execute(query)
        c = cursor.fetchone()['c']
        emit(f"    {label:<48} : {c:>5,} pass")


# ─────────────────────────────────────────────────────────────────────────────
# Section 7 — Sample universe rows
# ─────────────────────────────────────────────────────────────────────────────
def section_sample_universe_rows(cursor: sqlite3.Cursor) -> None:
    """Render 10 random universe rows so we can eyeball the data."""
    emit(hr("7. SAMPLE UNIVERSE ROWS (random 10 from is_index = 1)"))

    cursor.execute("""
        WITH latest_quant AS (
            SELECT q.ticker, q.ml_confidence_score, q.close_price, q.date
            FROM quant_signals q
            INNER JOIN (
                SELECT ticker, MAX(date) AS max_date
                FROM quant_signals
                GROUP BY ticker
            ) l ON q.ticker = l.ticker AND q.date = l.max_date
        )
        SELECT
            s.ticker,
            COALESCE(s.score_method, '(NULL)')   AS score_method,
            s.forward_pe,
            s.peter_lynch_peg,
            s.revenue_growth,
            s.roe,
            lq.ml_confidence_score               AS ml_score,
            tm.market_cap
        FROM stock_signals s
        INNER JOIN market_universe m ON s.ticker = m.ticker
        LEFT JOIN latest_quant lq    ON s.ticker = lq.ticker
        LEFT JOIN ticker_metadata tm ON s.ticker = tm.ticker
        WHERE m.is_index = 1
        ORDER BY RANDOM()
        LIMIT 10
    """)

    rows = cursor.fetchall()
    if not rows:
        emit(f"  {Color.RED}No rows found.{Color.END}")
        return

    header = (
        f"  {'TICKER':<10} {'SCORE_METHOD':<24} {'FWD_PE':>8} "
        f"{'LYNCH_PEG':>10} {'REV_GR%':>9} {'ROE%':>8} {'ML':>6} {'MCAP_B':>9}"
    )
    emit(header)
    emit(f"  {'-'*10} {'-'*24} {'-'*8} {'-'*10} {'-'*9} {'-'*8} {'-'*6} {'-'*9}")

    def fmt_f(v: Optional[float], precision: int = 2) -> str:
        return f"{v:.{precision}f}" if v is not None else '—'

    def fmt_pct(v: Optional[float]) -> str:
        return f"{v * 100.0:.1f}" if v is not None else '—'

    def fmt_mcap(v: Optional[float]) -> str:
        return f"{v / 1e9:.2f}" if v and v > 0 else '—'

    for row in rows:
        emit(
            f"  {row['ticker']:<10} {str(row['score_method']):<24} "
            f"{fmt_f(row['forward_pe']):>8} {fmt_f(row['peter_lynch_peg']):>10} "
            f"{fmt_pct(row['revenue_growth']):>9} {fmt_pct(row['roe']):>8} "
            f"{fmt_f(row['ml_score'], 1):>6} {fmt_mcap(row['market_cap']):>9}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Section 8 — ML pipeline coverage diagnosis
# ─────────────────────────────────────────────────────────────────────────────
def section_ml_diagnosis(cursor: sqlite3.Cursor) -> None:
    """Directly answer: does ML scoring run on the universe?"""
    emit(hr("8. ML PIPELINE COVERAGE DIAGNOSIS"))

    eligible_cte = """
        WITH latest_quant AS (
            SELECT q.ticker, q.ml_confidence_score
            FROM quant_signals q
            INNER JOIN (
                SELECT ticker, MAX(date) AS max_date
                FROM quant_signals
                GROUP BY ticker
            ) l ON q.ticker = l.ticker AND q.date = l.max_date
        )
    """

    cursor.execute(eligible_cte + """
        SELECT COUNT(*) AS c
        FROM market_universe m
        INNER JOIN stock_signals s   ON m.ticker = s.ticker
        INNER JOIN latest_quant lq   ON m.ticker = lq.ticker
        WHERE m.is_index = 1
    """)
    eligible: int = cursor.fetchone()['c']

    cursor.execute(eligible_cte + """
        SELECT COUNT(*) AS c
        FROM market_universe m
        INNER JOIN stock_signals s   ON m.ticker = s.ticker
        INNER JOIN latest_quant lq   ON m.ticker = lq.ticker
        WHERE m.is_index = 1
          AND lq.ml_confidence_score IS NOT NULL
    """)
    scored: int = cursor.fetchone()['c']

    emit(f"  Universe tickers ELIGIBLE for ML scoring (stock_signals + latest quant_signals present): {Color.BOLD}{eligible:>5,}{Color.END}")
    emit(f"  Universe tickers ACTUALLY scored (ml_confidence_score IS NOT NULL):                     {Color.BOLD}{scored:>5,}{Color.END}")

    if eligible == 0:
        emit(f"\n  {Color.RED}DIAGNOSIS: No eligible universe tickers — populate stock_signals + quant_signals first.{Color.END}")
    elif scored == 0:
        emit(f"\n  {Color.RED}DIAGNOSIS: ML scoring is NOT running on the universe.{Color.END}")
        emit(f"  {Color.YELLOW}  Likely cause : update_daily_ml_predictions() is called with portfolio/watchlist only.{Color.END}")
        emit(f"  {Color.YELLOW}  Fix          : extend the scheduler caller to also pass get_universe_tickers().{Color.END}")
    elif scored == eligible:
        emit(f"\n  {Color.GREEN}DIAGNOSIS: ML scoring covers the full eligible universe ({pct(scored, eligible)}). ✓{Color.END}")
    else:
        emit(f"\n  {Color.YELLOW}DIAGNOSIS: Partial coverage — {pct(scored, eligible)} of eligible universe tickers are scored.{Color.END}")
        emit(f"  {Color.YELLOW}  Likely cause : the ML pass runs on a subset only (e.g. portfolio + watchlist + Freetrade firewall).{Color.END}")


# ─────────────────────────────────────────────────────────────────────────────
# Section 9 — Recommendations summary
# ─────────────────────────────────────────────────────────────────────────────
def section_recommendations() -> None:
    """Closing 'what to do next' panel — interpretive guidance."""
    emit(hr("9. NEXT STEPS"))
    emit("  Review sections 3, 4, and 8 above. Decision tree for the GARP report:")
    emit("")
    emit("    • If peter_lynch_peg coverage in section 3 is low (< 80%):")
    emit("        → Patch universe_fundamentals_engine.py to compute & store peter_lynch_peg,")
    emit("          AND run a one-shot SQL backfill from existing forward_pe + revenue_growth.")
    emit("")
    emit("    • If ml_confidence_score coverage in section 4 is low:")
    emit("        → Confirm update_daily_ml_predictions() is being invoked with the universe.")
    emit("          Inspect scheduler_engine.py for the active caller.")
    emit("")
    emit("    • If market_cap coverage in section 5 is low:")
    emit("        → Run ai_prediction_engine.sync_ticker_metadata(get_universe_tickers()).")
    emit("")
    emit("  Once gaps are closed (or at least quantified), we can implement the GARP report")
    emit("  with confidence that the universe is fairly represented.")


# ─────────────────────────────────────────────────────────────────────────────
# Markdown report sink
# ─────────────────────────────────────────────────────────────────────────────
def save_markdown_report() -> Path:
    """Persist the captured output as a portable markdown file."""
    out_dir = Path('./diagnostic_reports')
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"garp_gaps_{timestamp}.md"

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write("# GARP Gap Diagnostic Report\n\n")
        f.write(f"_Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_\n\n")
        f.write("```text\n")
        f.write("\n".join(_md_lines))
        f.write("\n```\n")

    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    logger.info("Starting GARP gap diagnostic (read-only)...")

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        universe_total = section_universe_inventory(cursor)
        section_table_coverage(cursor, universe_total)
        section_field_coverage_stock_signals(cursor)
        section_field_coverage_quant_signals(cursor)
        section_market_cap_coverage(cursor)
        section_garp_filter_funnel(cursor)
        section_sample_universe_rows(cursor)
        section_ml_diagnosis(cursor)
        section_recommendations()

        out_path = save_markdown_report()
        emit(f"\n{Color.GREEN}✓ Markdown report saved to: {out_path}{Color.END}")
        logger.info(f"Diagnostic complete. Report: {out_path}")

    except Exception as e:
        logger.error(f"Diagnostic failed: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    main()