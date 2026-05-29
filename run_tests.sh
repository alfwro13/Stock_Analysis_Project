#!/usr/bin/env bash
# =============================================================================
#  run_tests.sh  —  Quantamental Dashboard Regression Test Runner
#  Run this after EVERY code change to check nothing is broken.
#
#  Usage:
#    ./run_tests.sh              # full suite
#    ./run_tests.sh --fast       # skip slow page-render tests
#    ./run_tests.sh --db-only    # database schema tests only
#    ./run_tests.sh --api-only   # API endpoint tests only
# =============================================================================

set -euo pipefail

# ── Colour helpers ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
TICK="${GREEN}✓${RESET}"; CROSS="${RED}✗${RESET}"; INFO="${CYAN}ℹ${RESET}"

# ── Move to project root regardless of where the script is called from ────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Parse flags ───────────────────────────────────────────────────────────────
FAST=0; DB_ONLY=0; API_ONLY=0
for arg in "$@"; do
  case $arg in
    --fast)    FAST=1 ;;
    --db-only) DB_ONLY=1 ;;
    --api-only) API_ONLY=1 ;;
  esac
done

# ── Header ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}════════════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}   QUANTAMENTAL DASHBOARD — REGRESSION TEST SUITE${RESET}"
echo -e "${BOLD}   $(date '+%Y-%m-%d %H:%M:%S')${RESET}"
echo -e "${BOLD}════════════════════════════════════════════════════════════${RESET}"
echo ""

# ── Check Python / pytest ─────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  echo -e "${CROSS} python3 not found. Install it and try again."
  exit 1
fi

PYTEST_CMD="python3 -m pytest"
if ! $PYTEST_CMD --version &>/dev/null 2>&1; then
  echo -e "${CROSS} pytest is not installed. Run: pip install pytest"
  exit 1
fi

# ── Check for pytest-html (optional but recommended) ─────────────────────────
REPORT_FLAG=""
if python3 -c "import pytest_html" 2>/dev/null; then
  REPORT_FLAG="--html=test_report.html --self-contained-html"
  echo -e "${INFO}  HTML report will be saved to: ${BOLD}test_report.html${RESET}"
else
  echo -e "${YELLOW}⚠${RESET}  pytest-html not installed (optional). Run: pip install pytest-html"
  echo -e "    Text output only.  HTML report skipped."
fi
echo ""

# ── Build pytest arguments ────────────────────────────────────────────────────
PYTEST_ARGS=(
  -v
  --tb=short
  --no-header
  -p no:warnings
  $REPORT_FLAG
)

if [[ $DB_ONLY -eq 1 ]]; then
  PYTEST_ARGS+=(-m db)
  echo -e "${CYAN}Running DATABASE SCHEMA tests only${RESET}"
elif [[ $API_ONLY -eq 1 ]]; then
  PYTEST_ARGS+=(-m api)
  echo -e "${CYAN}Running API ENDPOINT tests only${RESET}"
elif [[ $FAST -eq 1 ]]; then
  PYTEST_ARGS+=(-m "db or api or config")
  echo -e "${CYAN}Running FAST tests (skipping page render tests)${RESET}"
else
  echo -e "${CYAN}Running FULL test suite${RESET}"
fi
echo ""

# ── Category headers printed per test file ────────────────────────────────────
echo -e "${BOLD}── Test Categories ────────────────────────────────────────${RESET}"
echo -e "  ${CYAN}[DB]${RESET}     Database schema & integrity"
echo -e "  ${CYAN}[API]${RESET}    API endpoints (GET & POST)"
echo -e "  ${CYAN}[PAGES]${RESET}  HTML page routes"
echo -e "  ${CYAN}[MATH]${RESET}   Computations (indicators, position sizing, risk)"
echo -e "  ${CYAN}[CFG]${RESET}    Configuration & utilities"
echo ""
echo -e "${BOLD}── Running… ────────────────────────────────────────────────${RESET}"
echo ""

# ── Run pytest and capture exit code ─────────────────────────────────────────
RESULT_FILE="$(mktemp /tmp/pytest_output.XXXXXX)"
set +e
$PYTEST_CMD "${PYTEST_ARGS[@]}" tests/ 2>&1 | tee "$RESULT_FILE"
PYTEST_EXIT=$?
set -e

# ── Parse summary from pytest output ──────────────────────────────────────────
SUMMARY_LINE=$(grep -E "passed|failed|error" "$RESULT_FILE" | tail -1 || true)
PASSED=$(echo "$SUMMARY_LINE" | grep -oE "[0-9]+ passed" | grep -oE "[0-9]+" || echo "0")
FAILED=$(echo "$SUMMARY_LINE" | grep -oE "[0-9]+ failed" | grep -oE "[0-9]+" || echo "0")
ERRORS=$(echo "$SUMMARY_LINE" | grep -oE "[0-9]+ error" | grep -oE "[0-9]+" || echo "0")
WARNINGS=$(echo "$SUMMARY_LINE" | grep -oE "[0-9]+ warning" | grep -oE "[0-9]+" || echo "0")
TOTAL=$((PASSED + FAILED + ERRORS))

rm -f "$RESULT_FILE"

# ── Final summary banner ──────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}════════════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}   RESULTS SUMMARY${RESET}"
echo -e "${BOLD}════════════════════════════════════════════════════════════${RESET}"
echo ""

if [[ $PYTEST_EXIT -eq 0 ]]; then
  echo -e "  ${TICK}  ${GREEN}${BOLD}ALL TESTS PASSED${RESET}   (${PASSED}/${TOTAL})"
  echo ""
  echo -e "  Your recent code changes have not broken anything."
  echo -e "  Safe to commit / deploy."
else
  echo -e "  ${CROSS}  ${RED}${BOLD}TESTS FAILED${RESET}   (${PASSED} passed, ${FAILED} failed${ERRORS:+, $ERRORS errors})"
  echo ""
  echo -e "  ${RED}Please fix the failures listed above before committing.${RESET}"
  echo ""
  echo -e "${BOLD}── How to read the failures ────────────────────────────────${RESET}"
  echo -e "  Each failure shows:"
  echo -e "  • Which test failed  (plain English name)"
  echo -e "  • What it was checking  (the assertion message)"
  echo -e "  • The actual vs expected values"
  echo ""
  echo -e "  Common causes after a code change:"
  echo -e "  ${CYAN}[DB]${RESET}     A table or column was renamed / removed"
  echo -e "  ${CYAN}[API]${RESET}    An endpoint now returns 500 (crashed) or wrong JSON structure"
  echo -e "  ${CYAN}[PAGES]${RESET}  A page template or DB query threw an exception"
  echo -e "  ${CYAN}[MATH]${RESET}   A calculation formula was accidentally changed"
  echo -e "  ${CYAN}[CFG]${RESET}    A config key was renamed or removed"
fi

if [[ -n "$REPORT_FLAG" ]]; then
  echo ""
  echo -e "  ${INFO}  Full HTML report: ${BOLD}${SCRIPT_DIR}/test_report.html${RESET}"
  echo -e "      Open it in a browser for a detailed, colour-coded breakdown."
fi

echo ""
echo -e "${BOLD}════════════════════════════════════════════════════════════${RESET}"
echo ""

exit $PYTEST_EXIT
