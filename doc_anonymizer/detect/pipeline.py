"""Combine all detectors into one pass over the pages of a case file."""

from __future__ import annotations

import logging
from typing import Callable

from ..config import Settings
import regex as re

from ..lexicon import is_first_name, is_keep_prefix, is_stopword
from ..models import Category, Detection, PageInfo
from ..textmap import PageText
from . import ner, patterns, references
from .persons import TITLES, PersonRules, clean_person_span, split_name

log = logging.getLogger(__name__)

ProgressFn = Callable[[int, int, str], None]

_SPACY_LABELS = {"PER": Category.PERSON, "ORG": Category.ORG, "LOC": Category.LOCATION}


def _preceding_word(text: str, start: int) -> str:
    """The word directly before ``start`` (without punctuation)."""
    before = text[:start].rstrip().rsplit(None, 1)
    return before[-1].strip(".,:;()") if before else ""


_ROMAN = re.compile(r"^[IVXLCDM]+\.?$")


def _ordinary_word(word: str) -> bool:
    """Capitalised ordinary word: the model knows only its lower-case form.

    spaCy's word vectors contain "unzutreffende" but not "Unzutreffende",
    whereas real names ("Kowalski", "Schmidt") exist capitalised.
    """
    try:
        vocab = ner.load_spacy().vocab
    except RuntimeError:
        return False
    return (word[:1].isupper() and not word.isupper() and not is_first_name(word)
            and vocab.has_vector(word.lower()) and not vocab.has_vector(word))


#: Plausibility levels returned by :func:`_plausibility`.
REJECT, WEAK, OK = 0, 1, 2


def _plausible_entity(text: str, start: int, end: int, proper: set[int]) -> bool:
    return _plausibility(text, start, end, proper) == OK


def _plausibility(text: str, start: int, end: int, proper: set[int]) -> int:
    """Filter typical false positives of the statistical models.

    * single words not tagged as proper noun by spaCy are only WEAK
      ("Unzutreffend" at the start of a sentence vs. the rare name "Okonkwo"),
    * Roman numerals of headings ("III."), all-caps abbreviations ("GOÄ",
      "SEP" in an incoming-mail stamp) and stop words are dropped,
    * words hyphenated across a line break are judged in joined form.
    """
    value = text[start:end].strip()
    joined = value.replace("-\n", "")
    words = joined.split()
    if len(joined) < 3 or not any(ch.isupper() for ch in joined):
        return REJECT
    if "§" in value or sum(ch.isdigit() for ch in value) > len(value) / 2:
        return REJECT
    if all(is_stopword(w.strip(".,;:()")) or _ROMAN.match(w) for w in words):
        return REJECT
    if len(words) == 1:
        word = words[0].strip(".,;:()")
        if word.isupper() and len(word) <= 5:
            return REJECT
        if _ordinary_word(word):
            return REJECT  # "Unzutreffende" at the start of a sentence
        if start not in proper and not is_first_name(word):
            # Not tagged as proper noun: maybe an ordinary word at the start of
            # a sentence ("Unzutreffend"), maybe a rare name ("Okonkwo").
            return WEAK
    return OK


def _org_or_location_ok(text: str, start: int, end: int, proper: set[int]) -> int:
    level = _plausibility(text, start, end, proper)
    if level == REJECT:
        return REJECT
    words = text[start:end].split()
    # Courts and public authorities stay readable ("Amtsgericht Köln").
    if is_keep_prefix(words[0]) or is_keep_prefix(_preceding_word(text, start)):
        return REJECT
    return level


class Detector:
    """Runs regex rules, person rules, spaCy and (optionally) GLiNER."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.enabled = {c for c in Category if settings.enabled(c)}
        self.use_gliner = settings.use_gliner and ner.gliner_installed()
        self.warnings: list[str] = []
        if settings.use_gliner and not self.use_gliner:
            self.warnings.append("Das Zusatzmodell GLiNER ist nicht installiert – es wurden "
                                 "nur Regeln und das spaCy-Modell verwendet.")

    def detect_page(self, text: str, page: int, spacy_res: ner.SpacyResult) -> list[Detection]:
        found = patterns.detect(text, page, self.enabled)

        if Category.PERSON in self.enabled:
            rules = PersonRules(spacy_res.proper_noun_starts, spacy_res.person_starts)
            found.extend(rules.detect(text, page))

        # Exhibit designations ("Anlage K 1", "B 3") are numbering. The
        # statistical models take them for roads, companies or names – what
        # they report there is cut away (rules above are not affected).
        protected = references.spans(text)

        for start, end, label in spacy_res.entities:
            category = _SPACY_LABELS.get(label)
            if category is None or category not in self.enabled:
                continue
            kept = references.outside(text, start, end, protected)
            if kept is None:
                continue
            start, end = kept
            if category is Category.PERSON:
                cleaned = clean_person_span(text, start, end)
                if cleaned is None:
                    continue
                level = _plausibility(text, cleaned[0], cleaned[1],
                                      spacy_res.proper_noun_starts)
                if level == REJECT:
                    continue
                s, e, words = cleaned
                given, surname = split_name(words)
                found.append(Detection(page, s, e, text[s:e], category, "ki:spacy",
                                       0.7 if level == OK else 0.4,
                                       {"given": given, "surname": surname,
                                        "weak": level == WEAK}))
            else:
                level = _org_or_location_ok(text, start, end, spacy_res.proper_noun_starts)
                if level != REJECT:
                    found.append(Detection(page, start, end, text[start:end], category,
                                           "ki:spacy", 0.6 if level == OK else 0.35,
                                           {"weak": level == WEAK}))

        if Category.OTHER_NAME in self.enabled:
            found.extend(self._proper_noun_suggestions(text, page, spacy_res, found, protected))

        if self.use_gliner:
            try:
                for det in ner.gliner_detect(text, page, self.settings.gliner_threshold,
                                             self.enabled):
                    if det.category in (Category.PERSON, Category.ORG, Category.LOCATION):
                        kept = references.outside(text, det.start, det.end, protected)
                        if kept is None:
                            continue
                        det.start, det.end = kept
                        det.text = text[det.start:det.end]
                    if det.category is Category.PERSON:
                        cleaned = clean_person_span(text, det.start, det.end)
                        if cleaned is None:
                            continue
                        det.start, det.end, words = cleaned
                        det.text = text[det.start:det.end]
                        given, surname = split_name(words)
                        det.meta = {"given": given, "surname": surname}
                    elif det.category in (Category.ORG, Category.LOCATION) and \
                            _org_or_location_ok(text, det.start, det.end,
                                                spacy_res.proper_noun_starts | {det.start}
                                                ) == REJECT:
                        continue
                    found.append(det)
            except Exception as exc:  # noqa: BLE001 - model problems must not stop the run
                log.exception("GLiNER failed")
                self.use_gliner = False
                self.warnings.append(f"Zusatzmodell GLiNER konnte nicht ausgeführt werden "
                                     f"({exc.__class__.__name__}); es wurden nur Regeln und "
                                     "spaCy verwendet.")
        return found

    @staticmethod
    def _proper_noun_suggestions(text: str, page: int, spacy_res: ner.SpacyResult,
                                 found: list[Detection],
                                 protected: list[tuple[int, int]] = ()) -> list[Detection]:
        """Proper nouns that no detector claimed – proposed as *uncertain*.

        spaCy's tagger marks words like "Ivanova" or "Wolkenstein" as proper
        nouns even when the entity recogniser does not know what they are.
        They are offered to the user for a decision (default: redact).
        """
        covered = [(d.start, d.end) for d in found]
        suggestions = []
        for start, end in spacy_res.proper_noun_spans:
            if any(s < end and start < e for s, e in covered):
                continue
            kept = references.outside(text, start, end, list(protected))
            if kept is None:
                continue
            start, end = kept
            value = text[start:end].strip(".,;:()„“\"'")
            end = start + len(value)
            if len(value) < 3 or not _plausible_entity(text, start, end,
                                                        spacy_res.proper_noun_starts):
                continue
            words = value.split()
            if is_keep_prefix(words[0]) or is_keep_prefix(_preceding_word(text, start)):
                continue
            if any(is_stopword(w) or w.lower().rstrip(".") + "." in TITLES for w in words):
                continue  # "Dipl.-Ing", "Prof"
            category = Category.PERSON if len(words) == 1 and is_first_name(words[0]) \
                else Category.OTHER_NAME
            meta = {"given": words, "surname": ""} if category is Category.PERSON else {}
            suggestions.append(Detection(page, start, end, value, category,
                                         "ki:spacy-eigenname", 0.5, meta))
        return suggestions

    def detect_document(self, pages: list[PageInfo],
                        progress: ProgressFn | None = None) -> list[list[Detection]]:
        """Detections for every page of one document."""
        texts = [PageText.build(p.words).text for p in pages]
        results: list[list[Detection]] = []
        batch = 16
        for first in range(0, len(texts), batch):
            chunk = texts[first:first + batch]
            spacy_results = ner.spacy_analyse(chunk)
            for offset, (text, res) in enumerate(zip(chunk, spacy_results)):
                results.append(self.detect_page(text, first + offset, res))
                if progress:
                    progress(first + offset + 1, len(texts), "Personenbezogene Daten suchen")
        return results
