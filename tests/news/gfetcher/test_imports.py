"""No module under news/ imports trafilatura at module level.

Importing trafilatura (its downloads module) calls urllib3.disable_warnings(InsecureRequestWarning), which silences the
warning for the whole process; the app imports it only inside the function that extracts text (see
tests/accounts/test_ghostfolio_sync.py::TestTlsVerificationStaysOn). Checked on the source, because any earlier import
in the test session would already have added the warning filter."""
import ast
from pathlib import Path

import pytest

NEWS = Path(__file__).resolve().parents[3] / "news"


def _top_level_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


@pytest.mark.parametrize("path", sorted(NEWS.rglob("*.py")), ids=lambda p: p.relative_to(NEWS.parent).as_posix())
def test_trafilatura_is_never_imported_at_module_level(path):
    assert not any(name.split(".")[0] == "trafilatura" for name in _top_level_imports(path))
