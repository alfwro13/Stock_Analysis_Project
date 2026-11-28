"""The Macro AI pipeline routes' background bodies: Initialize and Run Now both retrain the stacked models from the stored calendar."""
from unittest.mock import MagicMock, patch

from api import routes_triggers


def _run(body, init_calls):
    engine = MagicMock()
    for name in ("train_regime_clustering", "train_consensus_miss_probability", "train_volatility_magnitude", "run_macro_inference"):
        getattr(engine, name).side_effect = lambda *_, n=name: init_calls.append(n)
    patches = [
        patch.object(routes_triggers, "MacroAIEngine", return_value=engine),
        patch.object(routes_triggers, "update_macro_calendar", side_effect=lambda: init_calls.append("calendar")),
        patch.object(routes_triggers, "update_macro_indicators", side_effect=lambda: init_calls.append("indicators")),
        patch.object(routes_triggers, "update_config_atomic"),
        patch.object(routes_triggers, "log_notification"),
        patch("regime_engine.calculate_systemic_macro_threat"),
        patch("regime_engine.calculate_market_regime"),
    ]
    for p in patches:
        p.start()
    try:
        body()
    finally:
        for p in patches:
            p.stop()
    return engine


def test_run_now_retrains_all_three_models_before_scoring():
    calls = []
    engine = _run(routes_triggers.bg_run_macro_pipeline, calls)
    assert calls == ["calendar", "indicators", "train_regime_clustering", "train_consensus_miss_probability", "train_volatility_magnitude", "run_macro_inference"]
    engine.close.assert_called_once()


def test_initialize_trains_all_three_models_from_the_feed_without_a_seed_step():
    calls = []
    _run(routes_triggers.bg_init_macro_pipeline, calls)
    assert calls == ["calendar", "indicators", "train_regime_clustering", "train_consensus_miss_probability", "train_volatility_magnitude", "run_macro_inference"]
