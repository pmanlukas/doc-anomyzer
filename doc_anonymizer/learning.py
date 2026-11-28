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


#: Origin of decisions the program made itself (automatic repair after a
#: verification finding). They are used as training examples only.
SELF_CORRECTION = "selbstkorrektur"


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
    record_many([decision])


def _changed() -> None:
    """Make freshly learned first names effective immediately."""
    from .lexicon import first_names

    first_names.cache_clear()


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
        signature_feedback_path().unlink(missing_ok=True)
        (home_dir() / "unterschriften_modell.json").unlink(missing_ok=True)
        _state_path().unlink(missing_ok=True)
    from .signatures import load_model

    load_model.cache_clear()
    _changed()


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
        if d.origin == SELF_CORRECTION:
            continue  # training material only – never a global "always redact"
        last[key] = d
        if d.decision == "behalten":
            keep_votes[key] += 1
    from .detect.references import is_reference

    for key, d in last.items():
        if d.decision == "schwaerzen":
            # Exhibit designations ("B 1", "Anlage K 3") were offered as
            # places or companies by earlier versions. A confirmation given
            # then must not make them disappear from every future file.
            if not is_reference(d.text):
                result.redact[key] = d.category
        elif keep_votes[key] >= min_keep_votes:
            result.keep.add(key)
    return result


def learned_first_names() -> set[str]:
    """First names learned from confirmed or added persons ("Liwia Kowara")."""
    from .detect.ner import is_known_word

    names = set()
    for d in load():
        if d.category != "PERSON" or d.decision != "schwaerzen" or d.origin == SELF_CORRECTION:
            continue
        parts = d.text.split()
        if len(parts) >= 2 and parts[0][:1].isupper() and len(parts[0]) >= 3 \
                and parts[0].isalpha() and not is_known_word(parts[0].lower()):
            names.add(parts[0].casefold())
    return names


def record_many(decisions: list[Decision]) -> None:
    """Append several decisions at once (one file access)."""
    if not decisions:
        return
    with _LOCK, feedback_path().open("a", encoding="utf-8") as fh:
        for decision in decisions:
            fh.write(json.dumps(asdict(decision), ensure_ascii=False) + "\n")
    _changed()


# ----------------------------------------------------------- signatures

def signature_feedback_path() -> Path:
    """Feature vectors of detected signature areas with the user's verdict.

    Contains only numbers (no text, no image data).
    """
    return home_dir() / "unterschriften_feedback.jsonl"


def record_signatures(samples: list[tuple[list[float], int]]) -> bool:
    """Store keep (0) / remove (1) verdicts and refit the signature model.

    Returns True when a new model was fitted.
    """
    samples = [(list(map(float, f)), int(label)) for f, label in samples if f]
    if not samples:
        return False
    with _LOCK, signature_feedback_path().open("a", encoding="utf-8") as fh:
        for features, label in samples:
            fh.write(json.dumps({"features": features, "label": label}) + "\n")
    from .signatures import learn_from_decisions

    return learn_from_decisions(signature_samples())


def signature_samples() -> list[tuple[list[float], int]]:
    path = signature_feedback_path()
    if not path.exists():
        return []
    samples = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
            samples.append((list(map(float, item["features"])), int(item["label"])))
        except (ValueError, KeyError, TypeError):
            continue
    return samples


# ----------------------------------------------------------- training state

def _state_path() -> Path:
    return home_dir() / "lernstand.json"


def training_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_training_state(**values) -> None:
    state = training_state()
    state.update(values)
    _state_path().write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def training_examples() -> int:
    return sum(len(d.contexts) for d in load())


def training_due(min_new: int = 40) -> bool:
    """Enough new examples since the last training run?"""
    return training_examples() - int(training_state().get("examples", 0)) >= min_new


def statistics() -> dict:
    """Numbers for the settings dialog and the start page."""
    data = load()
    info = learned()
    state = training_state()
    return {"entscheidungen": sum(1 for d in data if d.origin != SELF_CORRECTION),
            "selbstkorrekturen": sum(1 for d in data if d.origin == SELF_CORRECTION),
            "immer_schwaerzen": len(info.redact),
            "nicht_mehr_vorschlagen": len(info.keep),
            "gelernte_vornamen": len(learned_first_names()),
            "trainingsbeispiele": sum(len(d.contexts) for d in data),
            "unterschrift_entscheidungen": len(signature_samples()),
            "letztes_training": state.get("date", ""),
            "trainingsergebnis": state.get("message", "")}
