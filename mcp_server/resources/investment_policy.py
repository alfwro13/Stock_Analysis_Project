import logging
from typing import Optional

from config import BASE_DIR, DATA_DIR
from constants import INVESTMENT_POLICY_MAX_CHARS

from ..server import server

logger = logging.getLogger(__name__)

POLICY_URI = "policy://investment-policy"
POLICY_PATH = DATA_DIR / "investment_policy.md"
POLICY_TEMPLATE_PATH = BASE_DIR / "assets" / "investment_policy_template.md"
_POLICY_RULE = (
    "Tell the user the investment policy is unavailable. Do not invent goals, limits or rules, do not assume defaults, "
    "and do not present any limit as the operator's own."
)


def _policy_unavailable(reason: str) -> str:
    return f"POLICY NOT LOADED: {reason}\n\n{_POLICY_RULE}"


def _policy_missing() -> str:
    template = policy_template()
    return (
        "POLICY NOT CONFIGURED: data/investment_policy.md is missing or empty.\n\n"
        f"{_POLICY_RULE} The operator writes this file; these are the sections it should cover:\n\n{template}"
    )


def load_policy_file() -> tuple[Optional[str], Optional[str]]:
    """(text, None) for a servable file, (None, None) when missing or blank, (None, reason) when it exists but cannot be served."""
    try:
        with POLICY_PATH.open(encoding="utf-8-sig") as policy_file:
            text = policy_file.read(INVESTMENT_POLICY_MAX_CHARS + 1)
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError) as e:
        logger.error("Investment policy file could not be read: %s", e)
        return None, "data/investment_policy.md exists but could not be read as UTF-8 text."
    if not text.strip():
        return None, None
    if len(text) > INVESTMENT_POLICY_MAX_CHARS:
        return None, f"data/investment_policy.md is longer than {INVESTMENT_POLICY_MAX_CHARS} characters."
    return text, None


def policy_template() -> str:
    return POLICY_TEMPLATE_PATH.read_text(encoding="utf-8")


@server.resource(
    POLICY_URI, name="investment_policy", title="Investment policy", mime_type="text/markdown",
    description=(
        "The operator's investment policy: goals and horizon per account, risk tolerance, maximum position, sector, "
        "country and currency weights, tax wrappers and constraints, never-do list, profit-taking style and preferred "
        "response format. Read it BEFORE any analysis or recommendation and apply it. If it says the policy is not "
        "configured, say so instead of inventing one. Read-only."
    ),
)
def investment_policy() -> str:
    text, reason = load_policy_file()
    if reason:
        return _policy_unavailable(reason)
    if text is None:
        return _policy_missing()
    return text
