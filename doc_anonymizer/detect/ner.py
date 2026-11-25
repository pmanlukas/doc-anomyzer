"""Local machine-learning models for named entity recognition.

Two open-source models are supported; both run completely offline:

* **spaCy** ``de_core_news_lg`` (MIT licence) – fast German model for
  persons, organisations and places. Required.
* **GLiNER** ``urchade/gliner_multi_pii-v1`` (Apache 2.0) – multilingual
  transformer trained specifically on personal data (PII). Optional; it
  improves recall noticeably but needs more disk space and CPU time.

The models are downloaded once by the setup script into the ``models``
folder. At runtime Hugging Face is forced into offline mode so that no
network connection is ever opened.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..config import MODELS_DIR
from ..models import Category, Detection

log = logging.getLogger(__name__)

SPACY_MODELS = ("de_core_news_lg", "de_core_news_md", "de_core_news_sm")
GLINER_MODEL = "urchade/gliner_multi_pii-v1"
HF_CACHE = MODELS_DIR / "hf_cache"


@dataclass
class SpacyResult:
    """What the rule engine needs from spaCy for one page."""

    entities: list[tuple[int, int, str]] = field(default_factory=list)
    proper_noun_starts: set[int] = field(default_factory=set)
    person_starts: set[int] = field(default_factory=set)
    #: Character spans of runs of proper nouns ("NE" tags), e.g. "Ivanova"
    proper_noun_spans: list[tuple[int, int]] = field(default_factory=list)


def spacy_installed() -> bool:
    """Is a German language model installed? (Cheap – does not load it.)"""
    import importlib.util

    from ..config import custom_model_dir

    if (custom_model_dir() / "meta.json").exists():
        return True
    return any(importlib.util.find_spec(name) is not None for name in SPACY_MODELS)


@lru_cache(maxsize=1)
def load_spacy():
    """Load the best installed German spaCy model (cached)."""
    import spacy

    from ..config import custom_model_dir

    # The fine-tuned model lives in the user folder; older versions stored it
    # next to the program.
    custom = [str(p) for p in (custom_model_dir(), MODELS_DIR / "spacy_angepasst")
              if (p / "meta.json").exists()][:1]
    candidates = custom + list(SPACY_MODELS)
    for name in candidates:
        try:
            # Only the components we need: tokenizer, tagger (proper nouns), NER.
            nlp = spacy.load(name, exclude=["parser", "lemmatizer", "senter"])
            log.info("spaCy model %s loaded", name)
            return nlp
        except OSError:
            continue
    raise RuntimeError(
        "Kein deutsches Sprachmodell gefunden. Bitte „Installieren.bat“ ausführen "
        "(benötigt einmalig Internet).")


def _known_words_file() -> Path:
    from ..config import home_dir

    return home_dir() / "bekannte_woerter.txt"


@lru_cache(maxsize=1)
def _known_words() -> frozenset[str]:
    """Vocabulary of the language model's word vectors (500 000 words).

    Exported once to a small text file so that worker processes do not have
    to load the whole model (about 1 GB) just to look up words.
    """
    path = _known_words_file()
    if not path.exists():
        nlp = load_spacy()
        strings = nlp.vocab.strings
        words = []
        for key in nlp.vocab.vectors.keys():
            try:
                words.append(strings[key])
            except KeyError:
                continue
        # Several worker processes may get here at the same time: each writes
        # its own temporary file. On Windows the file cannot be replaced while
        # another process reads it – then that process' copy is used and this
        # one is simply dropped (the content is identical).
        tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp")
        try:
            tmp.write_text("\n".join(words), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            log.info("Wortliste nicht gespeichert (%s) – wird im Speicher verwendet", exc)
        finally:
            tmp.unlink(missing_ok=True)
        return frozenset(words)
    return frozenset(path.read_text(encoding="utf-8").split("\n"))


def prepare_known_words() -> None:
    """Create the word list once before worker processes start (no race)."""
    try:
        _known_words()
    except RuntimeError:  # no language model – handled where it is needed
        pass


@lru_cache(maxsize=200_000)
def is_known_word(word: str) -> bool:
    """True if the language model knows ``word`` as an ordinary word.

    Used to stop fuzzy name matching from turning ordinary words into names
    ("links" is not a misread "Linke", "unter" not a misread "Gunter").
    """
    try:
        words = _known_words()
    except RuntimeError:
        return False
    return word in words or word.lower() in words


def spacy_analyse(texts: list[str]) -> list[SpacyResult]:
    """Run spaCy on a batch of page texts."""
    nlp = load_spacy()
    results = []
    for doc in nlp.pipe(texts, batch_size=16):
        res = SpacyResult()
        for ent in doc.ents:
            res.entities.append((ent.start_char, ent.end_char, ent.label_))
            if ent.label_ == "PER":
                res.person_starts.update(tok.idx for tok in ent)
        run: list = []
        for tok in list(doc) + [None]:
            # STTS tag "NE" = proper noun; "FM" = foreign material (often names).
            if tok is not None and tok.tag_ in ("NE", "FM") and "\n" not in tok.text:
                res.proper_noun_starts.add(tok.idx)
                run.append(tok)
                continue
            if run:
                res.proper_noun_spans.append((run[0].idx, run[-1].idx + len(run[-1].text)))
                run = []
        results.append(res)
    return results


# ---------------------------------------------------------------------------
# GLiNER (optional)
# ---------------------------------------------------------------------------

#: GLiNER label -> our category. Labels are English because the model was
#: trained with English label names; it nevertheless works on German text.
GLINER_LABELS: dict[str, Category] = {
    "person": Category.PERSON,
    "organization": Category.ORG,
    "address": Category.ADDRESS,
    "street address": Category.ADDRESS,
    "city": Category.LOCATION,
    "phone number": Category.PHONE,
    "email": Category.EMAIL,
    "iban": Category.IBAN,
    "bank account number": Category.IBAN,
    "credit card number": Category.CREDIT_CARD,
    "date of birth": Category.BIRTH_DATE,
    "tax identification number": Category.TAX_ID,
    "social security number": Category.SOCIAL_SECURITY,
    "identity card number": Category.ID_DOCUMENT,
    "passport number": Category.ID_DOCUMENT,
    "license plate number": Category.LICENSE_PLATE,
    "ip address": Category.IP_ADDRESS,
}


def _prepare_offline_env() -> None:
    """Point Hugging Face to the local cache and forbid any network access."""
    os.environ.setdefault("HF_HOME", str(HF_CACHE))
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"


def gliner_installed() -> bool:
    """True if the package *and* the downloaded model are available."""
    try:
        import importlib.util

        if importlib.util.find_spec("gliner") is None:
            return False
    except (ImportError, ValueError):
        return False
    marker = HF_CACHE / "hub" / ("models--" + GLINER_MODEL.replace("/", "--"))
    return marker.exists()


@lru_cache(maxsize=1)
def load_gliner():
    _prepare_offline_env()
    from gliner import GLiNER  # imported lazily: heavy (PyTorch)

    model = GLiNER.from_pretrained(GLINER_MODEL, local_files_only=True)
    if hasattr(model, "eval"):
        model.eval()  # inference mode (no dropout)
    return model


def gliner_detect(text: str, page: int, threshold: float,
                  enabled: set[Category], chunk_words: int = 180,
                  overlap: int = 30) -> list[Detection]:
    """Run GLiNER over ``text`` in overlapping word windows.

    The model only sees ~384 tokens at once, so long pages are split into
    windows of ``chunk_words`` words that overlap to not cut names in half.
    """
    import regex as re

    model = load_gliner()
    labels = [lbl for lbl, cat in GLINER_LABELS.items() if cat in enabled]
    if not labels:
        return []
    words = [m.span() for m in re.finditer(r"\S+", text)]
    found: list[Detection] = []
    step = max(chunk_words - overlap, 1)
    for first in range(0, max(len(words), 1), step):
        window = words[first:first + chunk_words]
        if not window:
            break
        offset, end = window[0][0], window[-1][1]
        chunk = text[offset:end]
        for ent in model.predict_entities(chunk, labels, threshold=threshold):
            category = GLINER_LABELS.get(ent["label"])
            if category is None:
                continue
            start, stop = offset + ent["start"], offset + ent["end"]
            found.append(Detection(page, start, stop, text[start:stop], category,
                                   "ki:gliner", float(ent.get("score", 0.5))))
        if first + chunk_words >= len(words):
            break
    return found
