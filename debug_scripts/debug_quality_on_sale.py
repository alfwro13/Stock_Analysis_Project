"""
Debug script: verify data availability for "Quality on Sale — 52-Week Low Bargains" report.

Checks:
  1. Column availability in stock_signals and quant_signals
  2. Non-null coverage for each required field
  3. The actual filter query (relaxed progressively to show where drop-off occurs)
  4. Sample rows of candidates that would appear in the report
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_connection


def separator(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def run():
    conn = get_connection()
    cursor = conn.cursor()

    # ------------------------------------------------------------------
    # 1. Column coverage in stock_signals
    # ------------------------------------------------------------------
    separator("1. Non-null coverage — stock_signals required fields")
    coverage_query = """
    SELECT
        COUNT(*) as total_rows,
        SUM(CASE WHEN fifty_two_week_low IS NOT NULL AND fifty_two_week_low > 0 THEN 1 ELSE 0 END) as has_52w_low,
        SUM(CASE WHEN roe IS NOT NULL THEN 1 ELSE 0 END) as has_roe,
        SUM(CASE WHEN debt_to_equity IS NOT NULL THEN 1 ELSE 0 END) as has_debt_equity,
        SUM(CASE WHEN profit_margin IS NOT NULL THEN 1 ELSE 0 END) as has_profit_margin,
        SUM(CASE WHEN trailing_pe IS NOT NULL THEN 1 ELSE 0 END) as has_trailing_pe,
        SUM(CASE WHEN composite_score IS NOT NULL THEN 1 ELSE 0 END) as has_composite_score,
        SUM(CASE WHEN quote_type = 'EQUITY' THEN 1 ELSE 0 END) as is_equity
    FROM stock_signals
    """
    cursor.execute(coverage_query)
    row = cursor.fetchone()
    labels = [
        "Total rows", "52w low", "ROE", "Debt/Equity",
        "Profit margin", "Trailing PE", "Composite score", "EQUITY type"
    ]
    for label, val in zip(labels, row):
        print(f"  {label:<20}: {val}")

    # ------------------------------------------------------------------
    # 2. quant_signals close_price coverage
    # ------------------------------------------------------------------
    separator("2. quant_signals — close_price (latest snapshot) coverage")
    cursor.execute("""
        WITH latest AS (
            SELECT ticker, MAX(date) AS max_date FROM quant_signals GROUP BY ticker
        )
        SELECT COUNT(*) as total, SUM(CASE WHEN q.close_price > 0 THEN 1 ELSE 0 END) as has_price
        FROM quant_signals q
        INNER JOIN latest l ON q.ticker = l.ticker AND q.date = l.max_date
    """)
    row = cursor.fetchone()
    print(f"  Latest quant rows  : {row[0]}")
    print(f"  Has close_price > 0: {row[1]}")

    # ------------------------------------------------------------------
    # 3. Progressive filter funnel
    # ------------------------------------------------------------------
    separator("3. Filter funnel — how many stocks pass each gate")

    gates = [
        ("EQUITY + has 52w low + has close_price",
         """
         SELECT COUNT(DISTINCT s.ticker)
         FROM stock_signals s
         INNER JOIN (
             SELECT q.ticker, q.close_price
             FROM quant_signals q
             INNER JOIN (SELECT ticker, MAX(date) AS max_date FROM quant_signals GROUP BY ticker) l
                 ON q.ticker = l.ticker AND q.date = l.max_date
         ) lp ON s.ticker = lp.ticker
         WHERE s.quote_type = 'EQUITY'
           AND s.fifty_two_week_low IS NOT NULL AND s.fifty_two_week_low > 0
           AND lp.close_price > 0
         """),
        ("+ close_price <= 52w_low * 1.15",
         """
         SELECT COUNT(DISTINCT s.ticker)
         FROM stock_signals s
         INNER JOIN (
             SELECT q.ticker, q.close_price
             FROM quant_signals q
             INNER JOIN (SELECT ticker, MAX(date) AS max_date FROM quant_signals GROUP BY ticker) l
                 ON q.ticker = l.ticker AND q.date = l.max_date
         ) lp ON s.ticker = lp.ticker
         WHERE s.quote_type = 'EQUITY'
           AND s.fifty_two_week_low IS NOT NULL AND s.fifty_two_week_low > 0
           AND lp.close_price > 0
           AND lp.close_price <= s.fifty_two_week_low * 1.15
         """),
        ("+ ROE > 10%",
         """
         SELECT COUNT(DISTINCT s.ticker)
         FROM stock_signals s
         INNER JOIN (
             SELECT q.ticker, q.close_price
             FROM quant_signals q
             INNER JOIN (SELECT ticker, MAX(date) AS max_date FROM quant_signals GROUP BY ticker) l
                 ON q.ticker = l.ticker AND q.date = l.max_date
         ) lp ON s.ticker = lp.ticker
         WHERE s.quote_type = 'EQUITY'
           AND s.fifty_two_week_low IS NOT NULL AND s.fifty_two_week_low > 0
           AND lp.close_price > 0
           AND lp.close_price <= s.fifty_two_week_low * 1.15
           AND s.roe > 0.10
         """),
        ("+ Debt/Equity < 150 (= 1.5x ratio, yfinance % scale)",
         """
         SELECT COUNT(DISTINCT s.ticker)
         FROM stock_signals s
         INNER JOIN (
             SELECT q.ticker, q.close_price
             FROM quant_signals q
             INNER JOIN (SELECT ticker, MAX(date) AS max_date FROM quant_signals GROUP BY ticker) l
                 ON q.ticker = l.ticker AND q.date = l.max_date
         ) lp ON s.ticker = lp.ticker
         WHERE s.quote_type = 'EQUITY'
           AND s.fifty_two_week_low IS NOT NULL AND s.fifty_two_week_low > 0
           AND lp.close_price > 0
           AND lp.close_price <= s.fifty_two_week_low * 1.15
           AND s.roe > 0.10
           AND (s.debt_to_equity IS NULL OR s.debt_to_equity < 150)
         """),
        ("+ Profit margin > 5%",
         """
         SELECT COUNT(DISTINCT s.ticker)
         FROM stock_signals s
         INNER JOIN (
             SELECT q.ticker, q.close_price
             FROM quant_signals q
             INNER JOIN (SELECT ticker, MAX(date) AS max_date FROM quant_signals GROUP BY ticker) l
                 ON q.ticker = l.ticker AND q.date = l.max_date
         ) lp ON s.ticker = lp.ticker
         WHERE s.quote_type = 'EQUITY'
           AND s.fifty_two_week_low IS NOT NULL AND s.fifty_two_week_low > 0
           AND lp.close_price > 0
           AND lp.close_price <= s.fifty_two_week_low * 1.15
           AND s.roe > 0.10
           AND s.debt_to_equity < 1.5
           AND s.profit_margin > 0.05
         """),
        ("+ Trailing PE < 25",
         """
         SELECT COUNT(DISTINCT s.ticker)
         FROM stock_signals s
         INNER JOIN (
             SELECT q.ticker, q.close_price
             FROM quant_signals q
             INNER JOIN (SELECT ticker, MAX(date) AS max_date FROM quant_signals GROUP BY ticker) l
                 ON q.ticker = l.ticker AND q.date = l.max_date
         ) lp ON s.ticker = lp.ticker
         WHERE s.quote_type = 'EQUITY'
           AND s.fifty_two_week_low IS NOT NULL AND s.fifty_two_week_low > 0
           AND lp.close_price > 0
           AND lp.close_price <= s.fifty_two_week_low * 1.15
           AND s.roe > 0.10
           AND s.debt_to_equity < 1.5
           AND s.profit_margin > 0.05
           AND s.trailing_pe > 0 AND s.trailing_pe < 25
         """),
        ("+ composite_score >= 50  [FINAL]",
         """
         SELECT COUNT(DISTINCT s.ticker)
         FROM stock_signals s
         INNER JOIN (
             SELECT q.ticker, q.close_price
             FROM quant_signals q
             INNER JOIN (SELECT ticker, MAX(date) AS max_date FROM quant_signals GROUP BY ticker) l
                 ON q.ticker = l.ticker AND q.date = l.max_date
         ) lp ON s.ticker = lp.ticker
         WHERE s.quote_type = 'EQUITY'
           AND s.fifty_two_week_low IS NOT NULL AND s.fifty_two_week_low > 0
           AND lp.close_price > 0
           AND lp.close_price <= s.fifty_two_week_low * 1.15
           AND s.roe > 0.10
           AND (s.debt_to_equity IS NULL OR s.debt_to_equity < 150)
           AND s.profit_margin > 0.05
           AND s.trailing_pe > 0 AND s.trailing_pe < 25
           AND s.composite_score >= 50
         """),
    ]

    for label, sql in gates:
        cursor.execute(sql)
        count = cursor.fetchone()[0]
        print(f"  {label:<45}: {count}")

    # ------------------------------------------------------------------
    # 4. Sample candidate rows
    # ------------------------------------------------------------------
    separator("4. Sample candidates (up to 10 rows)")
    sample_query = """
    WITH latest_price AS (
        SELECT q.ticker, q.close_price
        FROM quant_signals q
        INNER JOIN (SELECT ticker, MAX(date) AS max_date FROM quant_signals GROUP BY ticker) l
            ON q.ticker = l.ticker AND q.date = l.max_date
    )
    SELECT
        s.ticker,
        COALESCE(s.company_name, s.ticker) as company_name,
        ROUND(lp.close_price, 2) as close_price,
        ROUND(s.fifty_two_week_low, 2) as fifty_two_week_low,
        ROUND((lp.close_price / s.fifty_two_week_low - 1.0) * 100.0, 1) as pct_above_52w_low,
        ROUND(s.roe * 100.0, 1) as roe_pct,
        ROUND(s.debt_to_equity, 2) as debt_to_equity,
        ROUND(s.profit_margin * 100.0, 1) as margin_pct,
        ROUND(s.trailing_pe, 1) as trailing_pe,
        s.composite_score
    FROM stock_signals s
    INNER JOIN latest_price lp ON s.ticker = lp.ticker
    WHERE s.quote_type = 'EQUITY'
      AND s.fifty_two_week_low IS NOT NULL AND s.fifty_two_week_low > 0
      AND lp.close_price > 0
      AND lp.close_price <= s.fifty_two_week_low * 1.15
      AND s.roe > 0.10
      AND (s.debt_to_equity IS NULL OR s.debt_to_equity < 150)
      AND s.profit_margin > 0.05
      AND s.trailing_pe > 0 AND s.trailing_pe < 25
      AND s.composite_score >= 50
    ORDER BY s.composite_score DESC, pct_above_52w_low ASC
    LIMIT 10
    """
    cursor.execute(sample_query)
    rows = cursor.fetchall()

    if not rows:
        print("  *** No candidates found with current filters ***")
        print("  Consider relaxing thresholds (e.g. composite_score >= 40, trailing_pe < 30)")
    else:
        header = f"  {'Ticker':<10} {'Company':<30} {'Price':>8} {'52wLow':>8} {'%Above':>7} {'ROE%':>6} {'D/E':>6} {'Margin%':>8} {'PE':>6} {'Score':>6}"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for r in rows:
            print(f"  {r[0]:<10} {str(r[1])[:29]:<30} {r[2]:>8.2f} {r[3]:>8.2f} {r[4]:>6.1f}% {r[5]:>5.1f}% {r[6]:>6.2f} {r[7]:>7.1f}% {r[8]:>6.1f} {r[9]:>6}")

    conn.close()
    print()


if __name__ == "__main__":
    run()
