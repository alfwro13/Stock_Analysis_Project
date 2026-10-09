import pandas as pd

from config import load_config, BASE_CURRENCY, ACCOUNT_CURRENCIES
from page_helpers import get_unread_count, get_portfolio_heat_row


def trading_account_context(acc, chart_period_cookie):
    from accounts_engine import (
        account_summary, cash_history, closed_positions, filter_value_history_by_period,
        holdings_with_market_value, is_unresolved_ticker, refresh_performance_cache,
        stale_pricing_warning, transaction_total_base, VALUE_CHART_PERIODS,
    )
    from database import (
        get_performance_cache, get_transactions, get_unresolved_pending_topups, get_value_history,
    )
    from treasury_bill_engine import bills_pending_ytm_confirmation, list_treasury_bills

    account_id = acc["id"]
    activities = get_transactions(account_id)
    for a in activities:
        a["total_base"] = transaction_total_base(a)
        a["needs_review"] = is_unresolved_ticker(a.get("ticker"))

    chart_period = chart_period_cookie if chart_period_cookie in VALUE_CHART_PERIODS else "max"
    chart_initial = filter_value_history_by_period(get_value_history(account_id), chart_period)

    holdings = holdings_with_market_value(account_id)
    pricing_warning = stale_pricing_warning(holdings)

    performance = get_performance_cache(account_id)
    if performance is None:
        refresh_performance_cache(account_id)
        performance = get_performance_cache(account_id)

    return {
        "account": acc,
        "summary": account_summary(account_id),
        "holdings": holdings,
        "pricing_warning": pricing_warning,
        "closed_positions": closed_positions(account_id),
        "activities": activities,
        "cash_history": cash_history(account_id),
        "chart_initial": chart_initial,
        "chart_period": chart_period,
        "base_currency": BASE_CURRENCY,
        "account_currencies": ACCOUNT_CURRENCIES,
        "unread_count": get_unread_count(),
        "pending_topups": get_unresolved_pending_topups(account_id),
        "performance": performance,
        "treasury_bills": list_treasury_bills(account_id),
        "treasury_bills_pending_ytm": bills_pending_ytm_confirmation(account_id),
        "config": load_config(),
        "portfolio_heat": get_portfolio_heat_row(f"acct:{account_id}"),
    }


def pension_account_context(acc):
    from accounts_engine import (
        account_summary, pension_activities, pension_benchmark_overlay, pension_display_label,
        scraped_price_performance,
    )
    from database import get_price_history, get_value_history
    from visuals import create_pension_unit_price_chart, create_pension_value_chart

    account_id = acc["id"]
    price_history = get_price_history(account_id)
    if price_history:
        price_df = pd.DataFrame(price_history).set_index("price_date")
        price_df.index = pd.to_datetime(price_df.index)
        price_chart_html = create_pension_unit_price_chart(price_df)
    else:
        price_chart_html = "<p class='text-muted'>No unit price history yet — scrape or import one to see this chart.</p>"

    value_history = get_value_history(account_id)
    if value_history:
        value_df = pd.DataFrame(value_history).set_index("snapshot_date")
        value_df.index = pd.to_datetime(value_df.index)
        benchmark_series = pension_benchmark_overlay(account_id, value_df)
        value_chart_html = create_pension_value_chart(value_df, benchmark_series)
    else:
        value_chart_html = "<p class='text-muted'>No value history yet — check back after the next nightly snapshot.</p>"

    return {
        "account": acc,
        "ticker_label": pension_display_label(acc),
        "summary": account_summary(account_id),
        "performance": scraped_price_performance(account_id),
        "activities": pension_activities(account_id),
        "price_chart_html": price_chart_html,
        "value_chart_html": value_chart_html,
        "base_currency": BASE_CURRENCY,
        "unread_count": get_unread_count(),
    }


def house_account_context(acc):
    from database import get_price_history

    price_history = get_price_history(acc["id"])
    if price_history:
        from visuals import create_house_value_chart
        price_df = pd.DataFrame(price_history).set_index("price_date")
        price_df.index = pd.to_datetime(price_df.index)
        chart_html = create_house_value_chart(price_df)
    else:
        chart_html = "<p class='text-muted'>No value history yet — scrape or import one to see this chart.</p>"

    return {
        "account": acc,
        "chart_html": chart_html,
        "base_currency": BASE_CURRENCY,
        "unread_count": get_unread_count(),
    }
