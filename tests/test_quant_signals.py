"""
tests/test_quant_signals.py

Unit tests for quant_signals.QuantEngine.run_all() business logic.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from quant_signals import QuantEngine


def test_run_all_skips_tbill_synthetic_tickers(tmp_path):
    """TBILL-{txn_id} has no Yahoo Finance listing (see AGENTS.md's Treasury Bill section) —
    a stray parquet file for one must never reach analyze_ticker()."""
    (tmp_path / "TBILL-606.parquet").touch()
    (tmp_path / "AAPL.parquet").touch()
    (tmp_path / "SP500_BASELINE.parquet").touch()

    engine = QuantEngine()
    with patch("quant_signals.HISTORICAL_DIR", tmp_path), \
         patch.object(engine, "analyze_ticker") as mock_analyze:
        engine.run_all()

    analyzed = {c.args[0] for c in mock_analyze.call_args_list}
    assert analyzed == {"AAPL"}
