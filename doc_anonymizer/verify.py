"""Independent verification of the anonymised PDF.

The checks open the *finished* file like any other program (or an LLM upload
pipeline) would and answer three questions:

1. **Are the original data gone?** Every original term (all variants and
   every part of every name) is searched in the complete text of the output,
   including invisible text and text outside the page area.
2. **Is the file clean?** No metadata, XMP, attachments, scripts, comments,
   form fields, hidden layers, bookmarks or links.
3. **Is anything suspicious left?** The detectors are run *again* on the
   output; anything that still looks like personal data is listed for a
   manual look (it may be a false alarm).

For raster pages an optional deep check reads the page *image* with OCR, so
also text that is only visible in pixels is covered.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import pymupdf
import regex as re

from . import ocr
from .config import Settings
from .detect.pipeline import Detector
from .extract import render_page
from .lexicon import is_keep_prefix, is_stopword
from .models import Category, PageInfo, Word
from .registry import (CaseRegistry, FuzzyIndex, levenshtein_at_most, misread_candidate,
                       normalise)

log = logging.getLogger(__name__)

ProgressFn = Callable[[int, int, str], None]

_NUMERIC = {Category.IBAN, Category.PHONE, Category.CREDIT_CARD, Category.TAX_ID,
            Category.SOCIAL_SECURITY, Category.ID_DOCUMENT, Category.REGISTER_NUMBER,
            Category.BIC, Category.IP_ADDRESS}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Leak:
    """An original term that is still present (CONFIDENTIAL – contains the term)."""

    page: int
    category: str
    entity_id: str
    term: str
    where: str  # "Textebene" | "Bild (OCR)" | "Dateiname" | "Textdatei"
    #: For image leaks: boxes of the offending words (output page coordinates)
    rects: list[list[float]] = field(default_factory=list)


@dataclass
class Suspect:
    """Something that still looks like personal data (CONFIDENTIAL)."""

    page: int
    category: str
    text: str
    source: str


@dataclass
class VerificationResult:
    checks: list[Check] = field(default_factory=list)
    leaks: list[Leak] = field(default_factory=list)
    suspects: list[Suspect] = field(default_factory=list)
    terms_checked: int = 0
    pages_checked: int = 0
    ocr_pages_checked: int = 0

    @property
    def passed(self) -> bool:
        return not self.leaks and all(c.ok for c in self.checks)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["passed"] = self.passed
        return data


def search_terms(registry: CaseRegistry, doc_index: int | None = None
                 ) -> list[tuple[str, str, Category, bool]]:
    """All original strings that must not appear: (term, entity_id, category, compact)."""
    terms: dict[tuple[str, str], tuple[str, str, Category, bool]] = {}
    for entity in registry.entities.values():
        if not entity.enabled:
            continue
        compact = entity.category in _NUMERIC
        candidates = set(entity.variants) | {entity.original}
        if entity.category is Category.PERSON:
            candidates |= set(entity.given)
            candidates |= {w for w in entity.surname.split() if w[:1].isupper()}
        for term in candidates:
            term = " ".join(term.split())
            key_text = _compact(term) if compact else normalise(term)
            if len(key_text) < 3 or (not compact and is_stopword(key_text)):
                continue
            terms[(entity.id, key_text)] = (term, entity.id, entity.category, compact)
    return list(terms.values())


def _compact(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


#: Categories whose terms are proper names: in German always capitalised.
_NAME_CATEGORIES = {Category.PERSON, Category.LOCATION, Category.ORG, Category.OTHER_NAME}


@dataclass
class Haystack:
    """A text prepared for fast term searches."""

    raw: str
    norm: str = ""
    compact: str = ""

    def __post_init__(self) -> None:
        self.raw = " ".join(self.raw.split())
        self.norm = normalise(self.raw)
        self.compact = _compact(self.raw)


def _contains(hay: Haystack, term: str, compact: bool,
              category: Category | None = None) -> bool:
    """Does ``term`` occur in ``hay``?

    * numbers are compared without spaces/punctuation (``DE89 3704…``),
    * names must occur as capitalised words (the surname "Lange" is not
      found in the adverb "lange"), also in the genitive ("Langes"),
    * occurrences right after a court/authority word are allowed
      ("Landgericht Musterstadt").
    """
    if compact:
        return _compact(term) in hay.compact
    key = normalise(term)
    if key not in hay.norm:
        return False  # fast path: most terms do not occur at all
    words = [re.escape(w) for w in term.split()]
    # A hyphen followed by a letter continues the word: the surname "Steuer"
    # does not occur in "Steuer-ID".
    pattern = (r"(?<![\p{L}\p{N}])(?<!\p{L}-)" + r"\s+".join(words)
               + r"(?:s|['’]s)?(?![\p{L}\p{N}])(?!-\p{L})")
    for match in re.finditer(pattern, hay.raw, re.IGNORECASE):
        if category in _NAME_CATEGORIES and not match.group(0)[:1].isupper():
            # Lower case only counts inside e-mail/web addresses
            # ("bekir.weihmann@…") – not the adverb "lange" for "Lange".
            near = hay.raw[max(0, match.start() - 1):match.end() + 2]
            if not re.search(r"[@_]|\.\S|\S\.", near.replace(match.group(0), " ", 1)):
                continue
        before = hay.raw[:match.start()].rstrip().rsplit(None, 1)
        if before and is_keep_prefix(before[-1]):
            continue
        return True
    return False


def _fuzzy_hits(words: list[str], index: FuzzyIndex) -> set[str]:
    """OCR text: names that occur with one/two misread characters."""
    hits = set()
    for word in words:
        if not misread_candidate(word):
            continue
        match = index.match(normalise(word.strip(".,;:()„“\"'")))
        if match:
            hits.add(match)
    return hits


def _leak_boxes(words: list[Word], term: str) -> list[list[float]]:
    """Boxes of OCR words that belong to a leaked term (for automatic repair)."""
    parts = {normalise(p.strip(".,;:()")) for p in term.split()}
    compact_term = _compact(term)
    rects = []
    for w in words:
        key = normalise(w.text.strip(".,;:()„“\"'"))
        if not key:
            continue
        fuzzy = misread_candidate(w.text) and any(levenshtein_at_most(key, p, 1) for p in parts)
        if key in parts or fuzzy or (len(_compact(key)) >= 3 and _compact(key) in compact_term):
            rects.append([w.x0, w.y0, w.x1, w.y1])
    return rects


def _structure_checks(doc: pymupdf.Document, expected_pages: int) -> list[Check]:
    checks = [Check("Seitenzahl unverändert", doc.page_count == expected_pages,
                    f"{doc.page_count} von {expected_pages} Seiten")]
    meta = {k: v for k, v in (doc.metadata or {}).items() if v and k not in ("format",)}
    # PyMuPDF always reports the PDF version ("format") and may report the
    # encryption state – neither is personal data.
    meta.pop("encryption", None)
    checks.append(Check("Keine Metadaten (Autor, Titel, Programm …)", not meta,
                        ", ".join(meta) if meta else ""))
    checks.append(Check("Keine XMP-Metadaten", not (doc.get_xml_metadata() or "").strip()))
    checks.append(Check("Keine Dateianhänge", doc.embfile_count() == 0))
    checks.append(Check("Keine Lesezeichen/Gliederung", not doc.get_toc()))
    checks.append(Check("Keine ausblendbaren Ebenen", not doc.get_ocgs()))
    annots = sum(1 for page in doc for _ in page.annots())
    links = sum(len(page.get_links()) for page in doc)
    checks.append(Check("Keine Kommentare/Anmerkungen", annots == 0, f"{annots} gefunden"))
    checks.append(Check("Keine Links", links == 0, f"{links} gefunden"))
    checks.append(Check("Keine Formularfelder", not doc.is_form_pdf))
    catalog = doc.pdf_catalog()
    has_js = doc.xref_get_key(catalog, "OpenAction")[0] != "null" or \
        "JavaScript" in doc.xref_object(catalog)
    checks.append(Check("Kein JavaScript / keine Autostart-Aktion", not has_js))
    return checks


def _revision_check(path: Path) -> Check:
    """A PDF saved incrementally keeps old versions (with old text) inside."""
    eof_markers = path.read_bytes().count(b"%%EOF")
    return Check("Keine früheren Dateiversionen enthalten", eof_markers <= 1,
                 f"{eof_markers} Versionen" if eof_markers > 1 else "")


def verify_document(output: Path, doc_index: int, expected_pages: int,
                    registry: CaseRegistry, settings: Settings,
                    progress: ProgressFn | None = None,
                    run_residual_scan: bool = True,
                    image_pages: set[int] | None = None) -> VerificationResult:
    """Run all verification stages on a finished PDF.

    Stage 1 – text check: every original term is searched in the complete text
              of the file (incl. invisible text), the file name and the file
              structure (metadata, attachments ...) are checked.
    Stage 2 – image check: every page is rendered and read with OCR, i.e. the
              check sees what a human or an AI model *sees* – independent of
              the text layer. (Can be switched off in the settings.)
    Stage 3 – residual scan: the detectors run again on the result; anything
              that still looks like personal data is listed for the user.
    """
    result = VerificationResult()
    terms = search_terms(registry)
    result.terms_checked = len(terms)
    doc = pymupdf.open(output)
    structure = _structure_checks(doc, expected_pages) + [_revision_check(output)]

    # ---- Stage 1: text layer and file name ---------------------------------
    name_hay = Haystack(output.stem.replace("_", " "))
    for term, entity_id, category, compact in terms:
        if _contains(name_hay, term, compact, category):
            result.leaks.append(Leak(0, category.value, entity_id, term, "Dateiname"))

    pages_for_scan: list[PageInfo] = []
    for page in doc:
        result.pages_checked += 1
        # Text of the page *including* text outside the visible area.
        words = page.get_text("words", clip=pymupdf.INFINITE_RECT())
        hay = Haystack(" ".join(w[4] for w in words))
        for term, entity_id, category, compact in terms:
            if _contains(hay, term, compact, category):
                result.leaks.append(Leak(page.number + 1, category.value, entity_id, term,
                                         "Textebene"))
        pages_for_scan.append(PageInfo(page.number, page.rect.width, page.rect.height,
                                       [Word(*w[:5], "text", int(w[5]) * 1000 + int(w[6]))
                                        for w in words]))
        if progress:
            progress(page.number + 1, doc.page_count, "Prüfstufe 1: Textprüfung")
    text_leaks = len(result.leaks)
    result.checks.append(Check(
        "Prüfstufe 1 – Textprüfung: keine Originaldaten im Text der Datei", not text_leaks,
        f"{len(terms)} Suchbegriffe auf {result.pages_checked} Seiten geprüft "
        "(inkl. unsichtbarem Text und Dateiname)"))

    # ---- Stage 2: what is visible on the page images -----------------------
    if settings.deep_verification and ocr.is_available():
        _image_check(doc, terms, settings, result, progress, image_pages)
        result.checks.append(Check(
            "Prüfstufe 2 – Bildprüfung: keine Originaldaten auf den Seitenbildern",
            len(result.leaks) == text_leaks,
            f"{result.ocr_pages_checked} Seiten per Texterkennung (OCR) gelesen und geprüft"
            + (" (Wiederholungsprüfung der nachgebesserten Seiten)" if image_pages is not None
               else "")))
    else:
        result.checks.append(Check(
            "Prüfstufe 2 – Bildprüfung", True,
            "übersprungen (abgeschaltet oder Texterkennung nicht installiert)"))
    doc.close()

    result.checks.extend(structure)

    # ---- Stage 3: residual scan ---------------------------------------------
    if run_residual_scan:
        if progress:
            progress(1, 1, "Prüfstufe 3: Nachkontrolle")
        result.suspects = residual_scan(pages_for_scan, registry, settings)
        result.checks.append(Check(
            "Prüfstufe 3 – Nachkontrolle mit KI/Regeln", True,
            f"{len(result.suspects)} Hinweis(e) zur Sichtprüfung" if result.suspects
            else "keine Auffälligkeiten"))
    return result


def _image_check(doc: pymupdf.Document, terms, settings: Settings,
                 result: VerificationResult, progress: ProgressFn | None,
                 only: set[int] | None = None) -> None:
    """Stage 2: render every page, OCR it and search all original terms.

    Pages are processed in small blocks (memory) and OCR'd in parallel.
    """
    scale = 72 / settings.ocr_dpi

    def read(image) -> list[Word]:
        boxes = ocr.ocr_words(image, scale, settings.ocr_language)
        if settings.thorough_ocr:
            boxes += ocr.ocr_words(image, scale, settings.ocr_language, psm=11)
        return boxes

    fuzzy = FuzzyIndex({normalise(t) for t, _, cat, _ in terms
                        if cat is Category.PERSON and " " not in t.strip()})
    workers = max(1, min(4, (os.cpu_count() or 2) - 1))
    block = workers * 2
    with ThreadPoolExecutor(max_workers=workers) as pool:
        todo = [n for n in range(doc.page_count) if only is None or n in only]
        for first in range(0, len(todo), block):
            numbers = todo[first:first + block]
            images = [render_page(doc[n], settings.ocr_dpi) for n in numbers]
            for number, boxes in zip(numbers, pool.map(read, images)):
                result.ocr_pages_checked += 1
                words = [w.text for w in boxes]
                hay = Haystack(" ".join(words))
                fuzzy_found = _fuzzy_hits(words, fuzzy)
                for term, entity_id, category, compact in terms:
                    hit = _contains(hay, term, compact, category) or (
                        category is Category.PERSON and normalise(term) in fuzzy_found)
                    if hit:
                        result.leaks.append(Leak(number + 1, category.value, entity_id, term,
                                                 "Bild (OCR)", _leak_boxes(boxes, term)))
                if progress:
                    progress(number + 1, doc.page_count, "Prüfstufe 2: Bildprüfung")


_LABEL_RE = re.compile(r"\[[^\]]{1,40}\]|\b\p{Lu}\.(?: \(\d+\))?")


def residual_scan(pages: list[PageInfo], registry: CaseRegistry,
                  settings: Settings) -> list[Suspect]:
    """Run the detectors again on the output text and list what they still find."""
    detector = Detector(settings)
    replacements = {normalise(e.replacement) for e in registry.entities.values()}
    disabled = {normalise(v) for e in registry.entities.values() if not e.enabled
                for v in e.variants}
    suspects: list[Suspect] = []
    seen: set[tuple[int, str]] = set()
    try:
        detections = detector.detect_document(pages)
    except Exception:  # noqa: BLE001 - the scan is an extra, never fatal
        log.exception("residual scan failed")
        return []
    for page_dets in detections:
        for det in page_dets:
            if det.meta.get("weak"):
                continue  # too unreliable for a post-check hint
            text = det.text.strip()
            cleaned = [t for t in _LABEL_RE.sub("", text).split()
                       if len(t.strip(" ,.;:-()/")) >= 3]
            if not cleaned or normalise(text) in replacements:
                continue
            if normalise(text) in disabled:
                continue  # the user deliberately kept this
            key = (det.page, normalise(text))
            if key in seen:
                continue
            seen.add(key)
            suspects.append(Suspect(det.page + 1, det.category.value, text, det.source))
    return suspects
