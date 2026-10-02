"""Learning from the user's decisions (stored locally only).

Every time the lawyer confirms ("schwärzen") or rejects ("nicht schwärzen")
an uncertain finding, or adds something the program missed, the decision is
appended to ``feedback.jsonl`` in the private application folder together
with a short text context.

The decisions are used in two ways:

1. **Immediately (rule learning)** – a term the user wanted redacted is
   redacted automatically in every future case; a term the user rejected
   twice is no longer proposed when only the statistical model found it.
2. **Model training (optional)** – ``python -m doc_anonymizer trainieren``
   fine-tunes the local spaCy model on the collected examples
   (see :mod:`doc_anonymizer.train`).

The file contains personal data from the user's documents (the contexts),
so it never leaves the computer and can be deleted in the settings.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .config import home_dir

_LOCK = threading.Lock()

#: Sources whose findings may be suppressed by learned "keep" decisions. Rule
#: based findings with checksums or strong context (IBAN, "Herr X") never are.
WEAK_SOURCES = {"ki:spacy", "ki:spacy-eigenname", "ki:gliner", "regel:street_name"}


@dataclass
class Decision:
    text: str
    category: str
    decision: str  # "schwaerzen" | "behalten"
    origin: str  # "unsicher" | "hinzugefuegt" | "nachkontrolle" | "liste"
    sources: list[str] = field(default_factory=list)
    #: Short text windows with the term, used for model training.
    contexts: list[str] = field(default_factory=list)
    ts: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


def feedback_path() -> Path:
    return home_dir() / "feedback.jsonl"


def record(decision: Decision) -> None:
    """Append one decision (thread-safe)."""
    with _LOCK, feedback_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(decision), ensure_ascii=False) + "\n")


def load() -> list[Decision]:
    path = feedback_path()
    if not path.exists():
        return []
    decisions = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            decisions.append(Decision(**json.loads(line)))
        except (ValueError, TypeError):
            continue
    return decisions


def clear() -> None:
    with _LOCK:
        feedback_path().unlink(missing_ok=True)


@dataclass
class Learned:
    """What the program has learned from all past decisions."""

    #: normalised text -> category; always redact
    redact: dict[str, str] = field(default_factory=dict)
    #: normalised texts that are not personal data (only for weak sources)
    keep: set[str] = field(default_factory=set)
    decisions: int = 0


def learned(min_keep_votes: int = 2) -> Learned:
    """Aggregate the decisions. The latest decision for a term wins."""
    from .registry import normalise

    result = Learned()
    keep_votes: Counter[str] = Counter()
    last: dict[str, Decision] = {}
    for d in load():
        key = normalise(d.text)
        if not key:
            continue
        result.decisions += 1
        last[key] = d
        if d.decision == "behalten":
            keep_votes[key] += 1
    for key, d in last.items():
        if d.decision == "schwaerzen":
            result.redact[key] = d.category
        elif keep_votes[key] >= min_keep_votes:
            result.keep.add(key)
    return result


def statistics() -> dict:
    """Numbers for the settings dialog."""
    data = load()
    info = learned()
    return {"entscheidungen": len(data), "immer_schwaerzen": len(info.redact),
            "nicht_mehr_vorschlagen": len(info.keep),
            "trainingsbeispiele": sum(len(d.contexts) for d in data)}
