"""Documentation describes the present: no ticket IDs or dated change records (AGENTS.md, "Current-state documentation")."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted((ROOT / "assets").glob("*.md")) + [ROOT / "README.md", ROOT / "AGENTS.md"]

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
_VERB = r"(?:added|fixed|found|decided|accepted|confirmed|changed|introduced|removed|retired|revised|tightened|widened|shipped|moved|renamed)"
FORBIDDEN = [
    ("ticket or audit ID", re.compile(r"\bSEC \d+/\d+|\b(?:LR|PERF|BUG)-\d+\b|\bMCP-\d+[a-z]?\b|\bS\d-\d+\b")),
    ("dated attribution", re.compile(rf"\b{_VERB}\b[^.|\n]{{0,40}}?\b(?:20\d\d-\d\d-\d\d|{_MONTH} 20\d\d)", re.I)),
    ("dated decision", re.compile(r"\b(?:operator decision|decided|accepted),? (?:on )?20\d\d-\d\d-\d\d", re.I)),
    ("dated 'as of'", re.compile(rf"\bas of (?:20\d\d-\d\d-\d\d|{_MONTH} 20\d\d)", re.I)),
    ("last-updated line", re.compile(r"^\W*(?:Last updated|Generated)\b", re.I | re.M)),
]


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_doc_has_no_change_records(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for label, pattern in FORBIDDEN:
        for m in pattern.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            hits.append(f"{path.name}:{line} {label}: {m.group(0)!r}")
    assert not hits, "change history belongs in the commit/PR, not the docs:\n" + "\n".join(hits)
