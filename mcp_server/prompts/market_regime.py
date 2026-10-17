from ai_regime_engine import AIRegimePromptEngine

from ..server import server
from .shared import dashboard_analysis_message

REGIME_MODES = (
    ("regime_plain_english_briefing", "Plain English Briefing", "Explains the current Market Regime HMM state in three short plain-English paragraphs."),
    ("regime_what_happens_next", "What Happens Next?", "Uses the regime transition matrix to explain the most likely next shift and what to watch."),
    ("regime_how_should_i_position", "How Should I Position?", "Gives three concrete plain-English portfolio actions for the current regime."),
    ("regime_red_flags_check", "Red Flags Check", "Lists two or three early-warning signs the regime may shift and rates overall risk."),
)


def _register(name: str, mode: str, summary: str) -> None:
    @server.prompt(
        name=name, title=f"Market Regime: {mode}",
        description=f"{summary} The text is the one the Market Regime page's AI prompt button copies, with the investment policy read first. Read-only analysis.",
    )
    def render() -> str:
        return dashboard_analysis_message(AIRegimePromptEngine().generate_prompt(mode))


for _name, _mode, _summary in REGIME_MODES:
    _register(_name, _mode, _summary)
