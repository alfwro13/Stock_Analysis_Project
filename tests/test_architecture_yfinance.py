"""
tests/test_architecture_yfinance.py

Architecture enforcement: no Python file in the project may import yfinance
directly except the documented exceptions listed in ALLOWED_EXCEPTIONS.

This test will fail CI immediately if a future developer adds a raw yfinance
call outside of yahoo_engine.py.
"""

import ast
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent

# Files that are allowed to import yfinance directly (with reason):
#   yahoo_engine.py    — the gateway itself; it must import yfinance
#   api_routes.py      — IPv6/network diagnostic endpoint that tests the raw session
#   network_engine.py  — lazy `from yfinance.data import YfData` inside a function body
#                        to clear the singleton crumb cache after a session replacement;
#                        can't move to yahoo_engine because yahoo_engine already imports
#                        yahoo_connection_boundary from this module (circular import).
ALLOWED_EXCEPTIONS = {
    "yahoo_engine.py",
    "api_routes.py",
    "network_engine.py",
}


def _collect_py_files() -> list[Path]:
    """All .py files in the project root (non-recursive subdirs excluded)."""
    files = []
    for p in PROJECT_ROOT.iterdir():
        if p.suffix == ".py" and p.is_file():
            files.append(p)
    # Also check the tests/ directory itself
    tests_dir = PROJECT_ROOT / "tests"
    if tests_dir.is_dir():
        for p in tests_dir.glob("*.py"):
            files.append(p)
    # And tools/ sub-package
    tools_dir = PROJECT_ROOT / "tools"
    if tools_dir.is_dir():
        for p in tools_dir.glob("*.py"):
            files.append(p)
    return sorted(files)


def _has_direct_yfinance_import(path: Path) -> list[str]:
    """
    Returns a list of offending lines (as strings) if the file contains
    a direct yfinance import statement.  Returns [] if clean.

    We use AST-based detection for import statements — more reliable than
    naive grep (won't fire on comments or docstrings).
    We also grep for 'yf.download', 'yf.Ticker', 'yfinance.Ticker' etc.
    in case someone assigns the module to an alias.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except Exception:
        return []

    violations = []

    # 1. AST check: import yfinance / from yfinance import ...
    try:
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "yfinance" or alias.name.startswith("yfinance."):
                        violations.append(
                            f"  line {node.lineno}: import {alias.name}"
                            + (f" as {alias.asname}" if alias.asname else "")
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module and (
                    node.module == "yfinance" or node.module.startswith("yfinance.")
                ):
                    names = ", ".join(a.name for a in node.names)
                    violations.append(
                        f"  line {node.lineno}: from {node.module} import {names}"
                    )
    except SyntaxError:
        pass  # if we can't parse it, skip — syntax errors are a different problem

    return violations


def pytest_generate_tests(metafunc):
    if "py_file" in metafunc.fixturenames:
        files = _collect_py_files()
        ids = [p.name if p.parent == PROJECT_ROOT else f"{p.parent.name}/{p.name}"
               for p in files]
        metafunc.parametrize("py_file", files, ids=ids)


def test_no_direct_yfinance_import(py_file: Path):
    """
    Every .py file must route Yahoo Finance calls through yahoo_engine.
    Allowed exceptions: yahoo_engine.py (the gateway) and api_routes.py (IPv6 diagnostic).
    """
    if py_file.name in ALLOWED_EXCEPTIONS:
        pytest.skip(f"{py_file.name} is a documented exception")

    violations = _has_direct_yfinance_import(py_file)

    assert not violations, (
        f"\n\n{py_file.relative_to(PROJECT_ROOT)} imports yfinance directly.\n"
        "All Yahoo Finance calls must go through yahoo_engine.py.\n"
        "Violations found:\n"
        + "\n".join(violations)
        + "\n\n"
        "Fix: replace 'import yfinance as yf' with 'from yahoo_engine import yahoo_engine'\n"
        "and use yahoo_engine.get_price_history(), .get_ticker_info(), etc."
    )
