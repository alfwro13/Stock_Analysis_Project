from config import BASE_CURRENCY
from utils import normalize_ticker, measure_request_stage
from fundamentals_helpers import compute_quality_grade
from portfolio_service import get_rate_to_base
from price_history_helpers import pct_from_anchor
import table_columns_helpers
from page_helpers import compute_badge_tags, get_pattern_tags_by_ticker


def portfolio_scope_tickers(portfolio_json, account_id, ignored_tickers):
    tickers = []
    for data in portfolio_json.values():
        if "ticker" not in data:
            continue
        if account_id == "all" or any(acc["id"] == account_id for acc in data.get("accounts", [])):
            tickers.append(data["ticker"])
    return list(dict.fromkeys(t for t in tickers if normalize_ticker(t) not in ignored_tickers))


def portfolio_account_options(config_data):
    from database import get_accounts

    active_accounts = config_data.get("GHOSTFOLIO_ACCOUNTS", {}).get("active", [])
    discovered_accounts = config_data.get("GHOSTFOLIO_ACCOUNTS", {}).get("discovered", [])
    account_options = [{"id": "all", "name": "Global (All Accounts)"}]
    for acc in discovered_accounts:
        if acc["id"] in active_accounts:
            account_options.append({"id": acc["id"], "name": acc["name"]})
    for acc in get_accounts():
        if acc["account_type"] == "Trading":
            account_options.append({"id": f"acct:{acc['id']}", "name": acc["name"]})
    return account_options


def load_signal_enrichments(tickers):
    from score_analysis import (
        compute_regime_weighted_score_batch,
        evaluate_buy_recommendation_batch,
        evaluate_pillar_confluence_batch,
    )
    from sector_relative_momentum_engine import get_column_values as get_sector_momentum_values
    from stable_shortlist_reads import get_column_values as get_shortlist_values

    confluence = evaluate_pillar_confluence_batch(tickers)
    regime_score = compute_regime_weighted_score_batch(tickers)
    return {
        "pattern_tags": get_pattern_tags_by_ticker(tickers),
        "confluence": confluence,
        "regime_score": regime_score,
        "buy_recommendation": evaluate_buy_recommendation_batch(
            tickers, confluence_by_ticker=confluence, regime_score_by_ticker=regime_score,
        ),
        "sector_momentum": get_sector_momentum_values(sorted(tickers)),
        "shortlist": get_shortlist_values(sorted(tickers)),
    }


def enrich_signal_row(row_dict, enrichments):
    from score_analysis import buy_recommendation_label, pillar_confluence_label

    ticker = row_dict['ticker']
    # Mutual funds often have no shortName from yfinance; the query already fell back through asset_profiles → market_universe
    row_dict['company_name'] = (
        row_dict.get('resolved_company_name')
        or row_dict.get('company_name')
        or ticker
    )
    row_dict['pattern_detections'] = enrichments["pattern_tags"].get(ticker, [])
    row_dict.update(compute_badge_tags(row_dict))
    row_dict['pillar_confluence_result'] = enrichments["confluence"].get(ticker)
    row_dict['pillar_confluence'] = pillar_confluence_label(row_dict['pillar_confluence_result'])
    row_dict['regime_weighted_result'] = enrichments["regime_score"].get(ticker)
    row_dict['regime_weighted_score'] = (row_dict['regime_weighted_result'] or {}).get('score')
    row_dict['buy_recommendation_result'] = enrichments["buy_recommendation"].get(ticker)
    row_dict['buy_recommendation'] = buy_recommendation_label(row_dict['buy_recommendation_result'])
    row_dict.update(enrichments["sector_momentum"].get(ticker, {}))
    row_dict.update(enrichments["shortlist"].get(ticker, {}))


def apply_change_period(row_dict, pulse_row, anchors, change_period):
    row_dict['period_anchors'] = anchors
    if change_period == "1d":
        row_dict['change_pct'] = pulse_row['change_pct'] if pulse_row else None
        row_dict['change_is_positive'] = pulse_row['is_positive'] if pulse_row else None
    else:
        display_price = pulse_row['price'] if pulse_row and pulse_row.get('price') is not None else row_dict['current_price']
        pct = pct_from_anchor(display_price, anchors.get(change_period))
        row_dict['change_pct'] = pct
        row_dict['change_is_positive'] = (pct >= 0) if pct is not None else None


def build_portfolio_rows(db_rows, portfolio_tickers):
    enrichments = load_signal_enrichments(portfolio_tickers)
    ticker_set = set(portfolio_tickers)
    rows = []
    for row in db_rows:
        row_dict = dict(row)
        if row_dict['ticker'] in ticker_set:
            enrich_signal_row(row_dict, enrichments)
            row_dict['heat_index'] = (row_dict.get('heat_index_tier') or '').capitalize() or None
            rows.append(row_dict)
    rows.sort(key=lambda x: x['ticker'])
    return rows


def _holding_targets(asset, ticker, account_id, all_holding_limits):
    acct_ids = []
    if asset:
        for acc in asset.get('accounts', []):
            raw_id = acc.get('id', '')
            if isinstance(raw_id, str) and raw_id.startswith('acct:') and (account_id == "all" or raw_id == account_id):
                try:
                    acct_ids.append(int(raw_id[len('acct:'):]))
                except ValueError:
                    pass
    lows = {all_holding_limits.get((aid, ticker), {}).get('low_limit') for aid in acct_ids}
    lows.discard(None)
    highs = {all_holding_limits.get((aid, ticker), {}).get('high_limit') for aid in acct_ids}
    highs.discard(None)
    return (next(iter(lows)) if len(lows) == 1 else None,
            next(iter(highs)) if len(highs) == 1 else None)


def _value_position(row_dict, asset, account_id, current_price, position_sizing_context, totals):
    shares = 0
    buy_price_base = 0
    if account_id == "all":
        shares = asset.get('global_shares', 0)
        buy_price_base = asset.get('global_buy_price', 0)
    else:
        for acc in asset.get('accounts', []):
            if acc['id'] == account_id:
                shares = acc.get('shares', 0)
                buy_price_base = acc.get('buy_price', 0)
                break

    cost_in_base = shares * buy_price_base
    with measure_request_stage("fx_rate"):
        exchange_rate = position_sizing_context["fx_rates"].get(row_dict['currency'])
        if exchange_rate is None:
            exchange_rate = get_rate_to_base(row_dict['currency'], cache_only=True)
    row_dict["live_shares"] = shares
    row_dict["live_cost_base"] = cost_in_base
    totals["cost"] += cost_in_base
    if exchange_rate is None:
        totals["missing_fx"] = True
        return

    val_in_base = (shares * current_price) * exchange_rate
    row_dict['market_value_base'] = round(val_in_base, 2)
    row_dict['global_market_value'] = round(val_in_base, 2)
    row_dict['live_fx_rate'] = exchange_rate
    totals["value"] += val_in_base

    pnl_in_base = val_in_base - cost_in_base
    row_dict['global_unrealized_pnl'] = round(pnl_in_base, 2)
    row_dict['global_unrealized_pnl_pct'] = round((pnl_in_base / cost_in_base) * 100, 2) if cost_in_base else None


def finalize_portfolio_rows(rows, *, portfolio_json, account_id, price_map, live_pulse, anchor_closes,
                            change_period, position_sizing_context, all_holding_limits):
    totals = {"value": 0.0, "cost": 0.0, "missing_fx": False}
    asset_by_ticker = {}
    for data in portfolio_json.values():
        if data.get("ticker"):
            asset_by_ticker.setdefault(data["ticker"], data)

    for row_dict in rows:
        ticker = row_dict['ticker']
        for key in ('market_value_base', 'global_market_value', 'global_unrealized_pnl',
                    'global_unrealized_pnl_pct', 'live_shares', 'live_cost_base', 'live_fx_rate'):
            row_dict[key] = None
        asset = asset_by_ticker.get(ticker)
        priced = price_map.get(ticker)
        current_price = priced[0] if priced and priced[0] else row_dict['current_price']

        apply_change_period(row_dict, live_pulse.get(ticker), anchor_closes.get(ticker, {}), change_period)

        if asset and current_price:
            _value_position(row_dict, asset, account_id, current_price, position_sizing_context, totals)

        row_dict['quality_grade'] = compute_quality_grade(row_dict)
        row_dict['low_target'], row_dict['high_target'] = _holding_targets(asset, ticker, account_id, all_holding_limits)
        row_dict['optional_cols'] = table_columns_helpers.build_optional_column_cells(row_dict, "portfolio")
    return totals


def format_portfolio_summary(totals):
    if totals["missing_fx"]:
        return {
            "value": "Unavailable — missing FX",
            "cost": f"{totals['cost']:,.2f} {BASE_CURRENCY}",
            "pnl": "Unavailable",
            "pnl_pct": "—",
            "is_positive": False,
        }
    if totals["cost"] > 0:
        pnl = totals["value"] - totals["cost"]
        return {
            "value": f"{totals['value']:,.2f} {BASE_CURRENCY}",
            "cost": f"{totals['cost']:,.2f} {BASE_CURRENCY}",
            "pnl": f"{'+' if pnl > 0 else ''}{pnl:,.2f} {BASE_CURRENCY}",
            "pnl_pct": f"{(pnl / totals['cost']) * 100:.2f}",
            "is_positive": pnl > 0,
        }
    return None


def portfolio_present_tags(rows):
    present_tags = set()
    present_pattern_tags = set()
    for row_dict in rows:
        if row_dict.get('trap_phase_label'):
            present_tags.add(row_dict['trap_phase_label'])
        if row_dict.get('bubble_flag_label'):
            present_tags.add(row_dict['bubble_flag_label'])
        for tag in row_dict.get('pattern_tags', []):
            present_tags.add(tag['label'])
            present_pattern_tags.add(tag['label'])
    return present_tags, sorted(present_pattern_tags)


def build_watchlist_rows(db_rows, watchlist_tickers, *, watchlist_account_id, all_holding_limits,
                         cached_pulse, anchor_closes, change_period):
    enrichments = load_signal_enrichments(watchlist_tickers)
    ticker_set = set(watchlist_tickers)
    rows = []
    for row in db_rows:
        row_dict = dict(row)
        ticker = row_dict['ticker']
        if ticker not in ticker_set:
            continue
        enrich_signal_row(row_dict, enrichments)

        limits = all_holding_limits.get((watchlist_account_id, ticker), {})
        row_dict['low_target'] = limits.get('low_limit')
        row_dict['high_target'] = limits.get('high_limit')
        row_dict['optional_cols'] = table_columns_helpers.build_optional_column_cells(row_dict, "watchlist")

        apply_change_period(row_dict, cached_pulse.get(ticker), anchor_closes.get(ticker, {}), change_period)
        rows.append(row_dict)
    rows.sort(key=lambda x: x['ticker'])
    return rows


def watchlist_present_tags(rows):
    present_tags = set()
    present_pattern_tags = set()
    for row in rows:
        for tag in row.get('setup_tags_list') or []:
            present_tags.add(tag['name'])
        for tag in row.get('report_tags') or []:
            present_tags.add(tag['name'])
        if row.get('trap_phase_label'):
            present_tags.add(row['trap_phase_label'])
        if row.get('bubble_flag_label'):
            present_tags.add(row['bubble_flag_label'])
        for tag in row.get('pattern_tags', []):
            present_tags.add(tag['label'])
            present_pattern_tags.add(tag['label'])
        if row.get('quality_grade'):
            present_tags.add('Grade ' + row['quality_grade'])
    return present_tags, sorted(present_pattern_tags)


def watchlist_score_buckets(rows):
    buckets = set()
    for row in rows:
        score = row.get('composite_score')
        if score is None:
            continue
        if score >= 75:
            buckets.add('75')
        elif score >= 60:
            buckets.add('60')
        elif score >= 40:
            buckets.add('40')
        else:
            buckets.add('0')
    return buckets
