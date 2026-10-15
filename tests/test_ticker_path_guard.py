"""Every file name built from a ticker goes through utils.safe_ticker_filename() (SEC 24/27 S4-2, S3-7):
a ticker can come from a transaction, the Watchlist or the Markets registry, not only the trusted universe."""
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

_UNGUARDED_PATH = re.compile(
    r"""(?:/\s*f["']\{(?:ticker|t|symbol)\}|f["']\{(?:ticker|t|symbol)\}(?:_intraday)?\.(?:parquet|json|joblib)["'])"""
)
_SKIPPED_DIRS = {"tests", "debug_scripts", "venv", ".git", "data", "node_modules", "static", "templates"}


def _source_files():
    for path in ROOT.rglob("*.py"):
        if not _SKIPPED_DIRS.intersection(path.relative_to(ROOT).parts):
            yield path


@pytest.mark.config
def test_no_file_path_is_built_from_a_raw_ticker_variable():
    offenders = []
    for path in _source_files():
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if _UNGUARDED_PATH.search(line) and not line.lstrip().startswith(("#", '"', "'")):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not offenders, "build per-ticker paths from safe_ticker_filename(ticker):\n" + "\n".join(offenders)


@pytest.mark.config
def test_universe_fundamentals_fetch_info_refuses_an_unsafe_ticker_without_a_lookup():
    import universe_fundamentals_engine as engine

    with patch.object(engine.yahoo_engine, "get_ticker_info") as lookup:
        assert engine._fetch_info("../X") == {}
    lookup.assert_not_called()


@pytest.mark.config
def test_price_repair_history_path_uses_the_shared_ticker_pattern():
    from price_repair_engine import PriceRepairError, _history_path

    for bad in ("../X", "/X/Y", "A B", "A" * 65):
        with pytest.raises(PriceRepairError):
            _history_path(bad)
