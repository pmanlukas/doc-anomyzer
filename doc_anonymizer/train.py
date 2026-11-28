"""Fine-tune the local spaCy model with the user's decisions.

    python -m doc_anonymizer trainieren

Training data are the text windows stored with every decision
(:mod:`doc_anonymizer.learning`):

* "schwärzen"  → the term is a person / organisation / place (positive),
* "behalten"   → the term is *not* an entity (negative example).

To avoid "catastrophic forgetting" every example also contains what the
original model already recognises elsewhere in the same window (pseudo
labels), and tokens without information are marked as *unknown* rather than
"no entity". 20 % of the examples are held back; the new model is only saved
if it is better on them than the current one. Everything runs locally.
"""

from __future__ import annotations

import logging
import random
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import learning
from .config import custom_model_dir

log = logging.getLogger(__name__)

MIN_EXAMPLES = 20
LEARN_RATE = 0.001
LABELS = {"PERSON": "PER", "ORG": "ORG", "LOCATION": "LOC", "ADDRESS": "LOC",
          "OTHER_NAME": "MISC"}


@dataclass
class Sample:
    text: str
    positives: list[tuple[int, int, str]]  # spans that must be entities
    negatives: list[tuple[int, int]]  # spans that must not be entities


def _find_all(text: str, term: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    lowered, needle = text.casefold(), term.casefold()
    if len(lowered) != len(text):  # "ß".casefold() changes the length
        lowered, needle = text.lower(), term.lower()
    while needle and (pos := lowered.find(needle, start)) >= 0:
        spans.append((pos, pos + len(term)))
        start = pos + len(term)
    return spans


def build_samples() -> list[Sample]:
    """Turn stored decisions into training samples (one per text window)."""
    samples: dict[str, Sample] = {}
    for d in learning.load():
        label = LABELS.get(d.category)
        for context in d.contexts:
            sample = samples.setdefault(context, Sample(context, [], []))
            for span in _find_all(context, d.text):
                if d.decision == "schwaerzen" and label:
                    sample.positives.append((*span, label))
                elif d.decision == "behalten":
                    sample.negatives.append(span)
    return [s for s in samples.values() if s.positives or s.negatives]


def _make_example(nlp, base, sample: Sample):
    """spaCy Example: corrections + base-model predictions, rest unknown."""
    from spacy.training import Example, offsets_to_biluo_tags

    doc = nlp.make_doc(sample.text)
    predicted = base(sample.text)
    ents = list(sample.positives)
    claimed = [(s, e) for s, e, _ in ents] + sample.negatives
    for ent in predicted.ents:
        if not any(ent.start_char < e and s < ent.end_char for s, e in claimed):
            ents.append((ent.start_char, ent.end_char, ent.label_))
    ents.sort()
    # Remove overlaps (keep the earlier/longer span).
    clean: list[tuple[int, int, str]] = []
    for ent in ents:
        if not clean or ent[0] >= clean[-1][1]:
            clean.append(ent)
    # Tokens of rejected terms become "O" (no entity), the rest keeps the
    # base model's prediction; spans not aligned to tokens become "-" (unknown).
    tags = offsets_to_biluo_tags(doc, clean)
    return Example.from_dict(doc, {"entities": tags})


def _score(nlp, samples: list[Sample]) -> tuple[float, float]:
    """(recall on positives, error rate on negatives)."""
    found = total = wrong = negs = 0
    for sample in samples:
        doc = nlp(sample.text)
        ents = [(e.start_char, e.end_char) for e in doc.ents]
        for s, e, _ in sample.positives:
            total += 1
            found += any(a <= s and e <= b for a, b in ents)
        for s, e in sample.negatives:
            negs += 1
            wrong += any(a < e and s < b for a, b in ents)
    return (found / total if total else 1.0), (wrong / negs if negs else 0.0)


def train(epochs: int = 12, seed: int = 0, progress=print) -> dict:
    """Fine-tune and save the model if it improves on held-out data."""
    import spacy
    from spacy.util import minibatch

    from .detect.ner import SPACY_MODELS, load_spacy

    samples = build_samples()
    if len(samples) < MIN_EXAMPLES:
        return {"ok": False, "message": f"Zu wenige Beispiele ({len(samples)}). Es werden "
                f"mindestens {MIN_EXAMPLES} Entscheidungen mit Textstellen benötigt."}
    random.Random(seed).shuffle(samples)
    cut = max(1, len(samples) // 5)
    test, train_set = samples[:cut], samples[cut:]

    base = load_spacy()
    base_name = custom_model_dir() if custom_model_dir().exists() else next(
        n for n in SPACY_MODELS if spacy.util.is_package(n))
    nlp = spacy.load(base_name)
    before = _score(nlp, test)
    progress(f"Vorher: Trefferquote {before[0]:.0%}, Fehlalarme {before[1]:.0%}")

    examples = [_make_example(nlp, base, s) for s in train_set]
    other = [p for p in nlp.pipe_names if p != "ner"]
    rng = random.Random(seed)
    with nlp.select_pipes(disable=other):
        optimizer = nlp.resume_training()
        optimizer.learn_rate = LEARN_RATE  # small steps: adapt, do not overwrite
        for epoch in range(epochs):
            rng.shuffle(examples)
            losses: dict = {}
            for batch in minibatch(examples, size=8):
                nlp.update(batch, sgd=optimizer, drop=0.2, losses=losses)
            progress(f"Runde {epoch + 1}/{epochs}: Verlust {losses.get('ner', 0):.2f}")

    after = _score(nlp, test)
    progress(f"Nachher: Trefferquote {after[0]:.0%}, Fehlalarme {after[1]:.0%}")
    better = after[0] > before[0] or (after[0] == before[0] and after[1] < before[1])
    if better:
        tmp = custom_model_dir().with_name(custom_model_dir().name + "_neu")
        shutil.rmtree(tmp, ignore_errors=True)
        nlp.to_disk(tmp)
        shutil.rmtree(custom_model_dir(), ignore_errors=True)
        tmp.rename(custom_model_dir())
        load_spacy.cache_clear()
    return {"ok": better, "before": before, "after": after, "examples": len(samples),
            "message": "Angepasstes Modell gespeichert." if better else
            "Das neue Modell war nicht besser – das bisherige Modell bleibt aktiv."}


def reset() -> None:
    """Delete the fine-tuned model (back to the original spaCy model)."""
    from .detect.ner import load_spacy

    shutil.rmtree(custom_model_dir(), ignore_errors=True)
    load_spacy.cache_clear()


def custom_model_path() -> Path | None:
    return custom_model_dir() if (custom_model_dir() / "meta.json").exists() else None
