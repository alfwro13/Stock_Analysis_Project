"""Importing trafilatura leaves urllib3's InsecureRequestWarning on.

Importing trafilatura (its downloads module) calls urllib3.disable_warnings(InsecureRequestWarning), which silences the
warning for the whole process. No module under news/ imports it at module level (see
tests/accounts/test_ghostfolio_sync.py::TestTlsVerificationStaysOn), checked on the source because any earlier import
in the test session would already have added the warning filter; both functions that do import it wrap that first import
in preserved_warning_filters, checked in a fresh interpreter for the same reason."""
import ast
import subprocess
import sys
import textwrap
import warnings
from pathlib import Path

import pytest

from outbound_fetch_helpers import preserved_warning_filters

ROOT = Path(__file__).resolve().parents[3]
NEWS = ROOT / "news"

_CHILD = """
import socket, sys, warnings
from pathlib import Path

def _no_network(*args, **kwargs):
    raise AssertionError("network reached")
socket.getaddrinfo = socket.socket.connect = _no_network

import urllib3
import config
config.DB_PATH = Path(sys.argv[1]) / "child.db"

def silenced():
    return any(f[0] == "ignore" and f[2] is urllib3.exceptions.InsecureRequestWarning for f in warnings.filters)

PAGE = '<html lang="en"><body><article><p>' + "Shares rose after strong results. " * 30 + "</p></article></body></html>"
assert "trafilatura" not in sys.modules and not silenced()
%s
assert "trafilatura" in sys.modules
print("silenced" if silenced() else "on")
"""

_SITES = {
    "plain-import": "import trafilatura",
    "helper": """
        from outbound_fetch_helpers import preserved_warning_filters
        with preserved_warning_filters("trafilatura"):
            import trafilatura
    """,
    "news-feed": """
        import news.news_feed_engine as engine
        assert "trafilatura" not in sys.modules
        engine.fetch_body = lambda *a, **k: PAGE
        assert "Shares rose" in engine._fetch_full_text("https://publisher.example/a")
    """,
    "google-news-fetcher": """
        from news.gfetcher import article_text
        assert "trafilatura" not in sys.modules
        article_text.fetch_body = lambda *a, **k: PAGE
        assert "Shares rose" in article_text.fetch_article("https://publisher.example/a").text
    """,
}


@pytest.mark.parametrize("site", _SITES)
def test_the_first_trafilatura_import_keeps_the_tls_warning_on(site, tmp_path):
    script = _CHILD % textwrap.dedent(_SITES[site])
    done = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    # The unwrapped import is the control: it shows the check can tell a silenced warning apart.
    assert done.stdout.split()[-1] == ("silenced" if site == "plain-import" else "on")


@pytest.mark.parametrize("module,kept", [("zz_not_imported_yet", False), ("sys", True)])
def test_filters_are_restored_only_around_a_first_import(module, kept):
    class Probe(Warning):
        pass

    with warnings.catch_warnings():
        with preserved_warning_filters(module):
            warnings.simplefilter("ignore", Probe)
        assert any(f[2] is Probe for f in warnings.filters) is kept


def test_a_failing_first_import_still_restores_the_filters():
    before = list(warnings.filters)
    with pytest.raises(ImportError):
        with preserved_warning_filters("zz_not_installed"):
            warnings.simplefilter("ignore")
            import zz_not_installed  # noqa: F401
    assert warnings.filters == before
    with preserved_warning_filters("zz_not_installed"):
        pass


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
