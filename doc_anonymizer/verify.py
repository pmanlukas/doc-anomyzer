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
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import pymupdf
import regex as re

from . import ocr
from .config import Settings
from .detect.pipeline import Detector
from .extract import clean_word, image_scale, render_page
from .forensics import forensic_check
from .parallel import ocr_threads
from .detect.patterns import NUMERIC_IBAN_COUNTRIES
from .lexicon import compound_around, is_harmless_compound, is_keep_prefix, is_stopword
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
    #: The check could not be carried out (switched off, tool missing,
    #: technical problem). It is then neither passed nor failed – the report
    #: and the interface say so instead of showing a green tick.
    skipped: bool = False


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
    forensic_methods: list[str] = field(default_factory=list)
    #: Output text per page (input for stage 4, not stored in reports)
    scan_pages: list[PageInfo] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.leaks and all(c.ok for c in self.checks)

    @property
    def complete(self) -> bool:
        """Were all verification stages really carried out?"""
        return not any(c.skipped for c in self.checks)

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("scan_pages", None)
        data["passed"] = self.passed
        data["complete"] = self.complete
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
    """Numbers without spacing; OCR letter/digit confusions mapped ("DEO2" = "DE02")."""
    return "".join(ch for ch in text.casefold() if ch.isalnum()).translate(_OCR_DIGIT_KEY)


_OCR_DIGIT_KEY = str.maketrans({"o": "0", "i": "1", "l": "1"})

#: Beginning of an IBAN: country code, check digits, digit groups (OCR-tolerant).
_IBAN_FRAGMENT = re.compile(
    r"(?<![\p{L}\p{N}])(?:" + "|".join(sorted(NUMERIC_IBAN_COUNTRIES | {
        "CH", "LI", "LU", "NL", "FR", "IT", "GB", "IE", "MC"}))
    + r") ?[\dOoIl]{2}(?:[ ]?[\dOoIl]{2,4}){1,8}(?![\p{L}\p{N}])")


def iban_fragments(text: str) -> list[str]:
    """Visible remains of an IBAN in anonymised text (CONFIDENTIAL).

    Reported are IBAN beginnings ("DE02 1203") that
    * stand next to a placeholder ("DE02 1203 [Telefon 3]"), i.e. only part
      of the number was redacted,
    * follow the word "IBAN", or
    * still show at least three digit groups.
    """
    found = []
    for match in _IBAN_FRAGMENT.finditer(text):
        value = match.group(0)
        if text.rfind("[", 0, match.start()) > text.rfind("]", 0, match.start()):
            continue  # inside a placeholder
        groups = len(re.findall(r"[\dOoIl]{2,4}", value[4:]))
        after = text[match.end():match.end() + 3].lstrip(" .,:;")
        before = text[max(0, match.start() - 12):match.start()]
        if groups >= 3 or after.startswith("[") or before.rstrip(" .,:;").endswith("]") \
                or re.search(r"IBAN\W*$", before, re.IGNORECASE):
            found.append(value)
    return found


#: Placeholder of an e-mail/web address directly after / before a word.
_BESIDE_ADDRESS_AFTER = re.compile(r"[\s.,;:<(]{0,3}\[(?:E-Mail|Webadresse) \d+\]")
_BESIDE_ADDRESS_BEFORE = re.compile(r"\[(?:E-Mail|Webadresse) \d+\][\s.,;:>)]{0,3}$")

#: Categories whose terms are proper names: in German always capitalised.
_NAME_CATEGORIES = {Category.PERSON, Category.LOCATION, Category.ORG, Category.OTHER_NAME}
#: Names of courts and authorities stay readable ("Amtsgericht Köln") – the
#: same categories the detection leaves alone there. A *person's* name after
#: such a word ("Richterin am Landgericht Mustermann") is a leak.
_KEPT_AFTER_COURT = {Category.LOCATION, Category.ORG, Category.OTHER_NAME, None}


@dataclass
class Haystack:
    """A text prepared for fast term searches."""

    raw: str
    norm: str = ""
    compact: str = ""

    def __post_init__(self) -> None:
        self.raw = fold(" ".join(clean_word(self.raw).split()))
        self.norm = normalise(self.raw)
        self.compact = _compact(self.raw)


def fold(text: str) -> str:
    """Remove diacritics/transliterate ("Yılmaz" -> "Yilmaz", "Dvořák" -> "Dvorak").

    Both the searched text and the terms are folded, so a different spelling
    of special characters (OCR, font substitution) cannot hide a name.
    """
    text = text.translate(_FOLD)
    decomposed = unicodedata.normalize("NFKD", text)
    return unicodedata.normalize("NFC", "".join(c for c in decomposed
                                               if not unicodedata.combining(c)))


_FOLD = str.maketrans({"ı": "i", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ø": "o", "Ø": "O",
                       "æ": "ae", "Æ": "Ae", "œ": "oe", "Œ": "Oe", "ħ": "h"})


def _contains(hay: Haystack, term: str, compact: bool,
              category: Category | None = None) -> bool:
    """Does ``term`` occur in ``hay``?

    * numbers are compared without spaces/punctuation (``DE89 3704…``),
    * names must occur as capitalised words (the surname "Lange" is not
      found in the adverb "lange"), also in the genitive ("Langes"),
    * a name inside a hyphenated compound counts ("das Mustermann-Gutachten")
      – unless the compound is the label of a form field: the surname
      "Steuer" does not occur in "Steuer-ID",
    * occurrences right after a court/authority word are allowed
      ("Landgericht Musterstadt").
    """
    if compact:
        return _compact(term) in hay.compact
    term = fold(term)
    key = normalise(term)
    if key not in hay.norm:
        return False  # fast path: most terms do not occur at all
    words = [re.escape(w) for w in term.split()]
    pattern = (r"(?<![\p{L}\p{N}])" + r"\s+".join(words)
               + r"(?:s|['’]s)?(?![\p{L}\p{N}])")
    for match in re.finditer(pattern, hay.raw, re.IGNORECASE):
        compound = compound_around(hay.raw, match.start(), match.end())
        if compound and (category not in _NAME_CATEGORIES or is_harmless_compound(
                compound, term, category is Category.PERSON)):
            continue  # part of a longer expression, not this term
        if category in _NAME_CATEGORIES and not match.group(0)[:1].isupper():
            # Lower case only counts inside e-mail/web addresses
            # ("bekir.weihmann@…") – not the adverb "lange" for "Lange" – and
            # right beside one that was replaced ("samuel [E-Mail 3]": the
            # OCR tore the address apart, the first name stayed).
            near = hay.raw[max(0, match.start() - 1):match.end() + 2]
            if not re.search(r"[@_]|\.\S|\S\.", near.replace(match.group(0), " ", 1)) \
                    and not _BESIDE_ADDRESS_AFTER.match(hay.raw, match.end()) \
                    and not _BESIDE_ADDRESS_BEFORE.search(hay.raw[:match.start()]):
                continue
        before = hay.raw[:match.start()].rstrip().rsplit(None, 1)
        if before and category in _KEPT_AFTER_COURT and is_keep_prefix(before[-1]):
            continue  # "Landgericht Musterstadt" stays readable by design
        return True
    return False


_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                           "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"})
#: "KlageMustermann2024" -> "Klage Mustermann 2024"
_WORD_JOINTS = re.compile(r"(?<=\p{Ll})(?=\p{Lu})|(?<=\p{L})(?=\p{N})|(?<=\p{N})(?=\p{L})")


def split_name_words(text: str) -> str:
    """Separate the words of a file name ("_", camel case, digits)."""
    text = unicodedata.normalize("NFC", text).replace("_", " ")
    return _WORD_JOINTS.sub(" ", text)


def _name_keys(text: str) -> set[str]:
    """Spellings of ``text`` as they occur in file names: letters and digits
    only, without diacritics, umlauts also written out ("Mueller")."""
    text = unicodedata.normalize("NFC", text)
    return {"".join(ch for ch in fold(variant).casefold() if ch.isalnum())
            for variant in (text, text.translate(_UMLAUTS))}


def name_leaks(stem: str, terms) -> list[tuple[str, str, Category]]:
    """Original terms that can be read in a file or folder name.

    Stricter than the text check – file names have no grammar: a name also
    counts in lower case, inside a longer word ("klagemustermann") and in
    another spelling ("Mueller" for "Müller").
    """
    hay = Haystack(split_name_words(stem))
    hay_keys = _name_keys(stem)
    found = []
    for term, entity_id, category, compact in terms:
        hit = _contains(hay, term, compact, category)
        if not hit and not compact:
            hit = any(len(key) >= 5 and key in h for key in _name_keys(term) for h in hay_keys)
        if hit:
            found.append((term, entity_id, category))
    return found


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


def _ocr_text(words: list[Word]) -> list[str]:
    """OCR words as text, with split compounds joined again.

    Tesseract sometimes cuts "Steuer-ID" into "Steuer-" and "ID". For the
    search this must stay *one* word – otherwise the label would count as the
    surname "Steuer". Joined only where the two parts touch (no real space in
    between): "Mustermann - Kläger" stays three words.
    """
    out: list[str] = []
    previous: Word | None = None
    for word in words:
        text = word.text
        if previous is not None and out and word.line_id == previous.line_id and len(text) > 1 \
                and len(previous.text) > 1 and (previous.text.endswith("-") or text.startswith("-")):
            char_width = (previous.x1 - previous.x0) / max(len(previous.text), 1)
            if word.x0 - previous.x1 < 0.6 * char_width:
                out[-1] += text
                previous = word
                continue
        out.append(text)
        previous = word
    return out


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
                    image_pages: set[int] | None = None,
                    source: Path | None = None,
                    raster_pages: set[int] | None = None) -> VerificationResult:
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
    for term, entity_id, category in name_leaks(output.stem, terms):
        result.leaks.append(Leak(0, category.value, entity_id, term, "Dateiname"))

    pages_for_scan: list[PageInfo] = []
    for page in doc:
        result.pages_checked += 1
        # Text of the page *including* text outside the visible area.
        words = page.get_text("words", clip=pymupdf.INFINITE_RECT(),
                              flags=pymupdf.TEXTFLAGS_WORDS & ~pymupdf.TEXT_PRESERVE_LIGATURES)
        words = [(*w[:4], clean_word(w[4]), *w[5:]) for w in words]
        hay = Haystack(" ".join(w[4] for w in words))
        for term, entity_id, category, compact in terms:
            if _contains(hay, term, compact, category):
                result.leaks.append(Leak(page.number + 1, category.value, entity_id, term,
                                         "Textebene"))
        for fragment in iban_fragments(" ".join(w[4] for w in words)):
            # Part of an IBAN survived (e.g. the rest was taken for a phone number).
            result.leaks.append(Leak(page.number + 1, Category.IBAN.value, "", fragment,
                                     "Textebene (IBAN-Rest)"))
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
        _image_check(doc, terms, settings, result, progress, image_pages,
                     raster_pages or set())
        result.checks.append(Check(
            "Prüfstufe 2 – Bildprüfung: keine Originaldaten auf den Seitenbildern",
            len(result.leaks) == text_leaks,
            f"{result.ocr_pages_checked} Seiten per Texterkennung (OCR) gelesen und geprüft"
            + (" (Wiederholungsprüfung der nachgebesserten Seiten)" if image_pages is not None
               else "")))
    else:
        result.checks.append(Check(
            "Prüfstufe 2 – Bildprüfung", True,
            "nicht ausgeführt (" + ("in den Einstellungen abgeschaltet"
                                    if not settings.deep_verification
                                    else "Texterkennung nicht installiert") + ")",
            skipped=True))
    doc.close()

    # ---- Stage 3: recovery attempts (forensics) -------------------------------
    if settings.forensic_check:
        if progress:
            progress(1, 1, "Prüfstufe 3: Wiederherstellungsversuche")
        forensic = forensic_check(output, source, terms, settings, raster_pages or set())
        for f in forensic.findings:
            entity = registry.entities.get(f.entity_id)
            result.leaks.append(Leak(f.page, entity.category.value if entity else "",
                                     f.entity_id, f.term, f"Forensik: {f.method}"))
        result.forensic_methods = forensic.methods
        result.checks.append(Check(
            "Prüfstufe 3 – Wiederherstellungsversuche: nichts wiederherstellbar",
            not forensic.findings and not forensic.structure_problems,
            "; ".join(forensic.methods) + (
                " | Probleme: " + ", ".join(forensic.structure_problems)
                if forensic.structure_problems else "")))
    else:
        result.checks.append(Check(
            "Prüfstufe 3 – Wiederherstellungsversuche", True,
            "nicht ausgeführt (in den Einstellungen abgeschaltet)", skipped=True))

    result.checks.extend(structure)

    # ---- Stage 4: residual scan ---------------------------------------------
    result.scan_pages = pages_for_scan
    if run_residual_scan:
        if progress:
            progress(1, 1, "Prüfstufe 4: Nachkontrolle")
        finish_verification(result, registry, settings)
    return result


def finish_verification(result: VerificationResult, registry: CaseRegistry,
                        settings: Settings) -> None:
    """Stage 4 (needs the language model, therefore run in the main process)."""
    suspects = residual_scan(result.scan_pages, registry, settings)
    result.scan_pages = []
    if suspects is None:
        result.checks.append(Check("Prüfstufe 4 – Nachkontrolle mit KI/Regeln", True,
                                   "nicht ausgeführt (technisches Problem)", skipped=True))
        return
    result.suspects = suspects
    result.checks.append(Check(
        "Prüfstufe 4 – Nachkontrolle mit KI/Regeln", True,
        f"{len(result.suspects)} Hinweis(e) zur Sichtprüfung" if result.suspects
        else "keine Auffälligkeiten"))


def _image_check(doc: pymupdf.Document, terms, settings: Settings,
                 result: VerificationResult, progress: ProgressFn | None,
                 only: set[int] | None = None, raster: set[int] | None = None) -> None:
    """Stage 2: render every page, OCR it and search all original terms.

    Pages are processed in small blocks (memory) and OCR'd in parallel.
    """
    raster = raster or set()

    def read(job) -> list[Word]:
        image, raster, scale = job
        if not raster:
            # Text pages: one "sparse text" pass finds everything visible; small
            # print of the text layer is covered completely by stage 1.
            return ocr.ocr_words(image, scale, settings.ocr_language, psm=11)
        boxes = ocr.ocr_words(image, scale, settings.ocr_language)
        if settings.thorough_ocr:
            boxes += ocr.ocr_words(image, scale, settings.ocr_language, psm=11)
        return boxes

    fuzzy = FuzzyIndex({normalise(t) for t, _, cat, _ in terms
                        if cat is Category.PERSON and " " not in t.strip()})
    workers = ocr_threads()
    block = workers * 2
    with ThreadPoolExecutor(max_workers=workers) as pool:
        todo = [n for n in range(doc.page_count) if only is None or n in only]
        for first in range(0, len(todo), block):
            numbers = todo[first:first + block]
            images = [render_page(doc[n], settings.ocr_dpi) for n in numbers]
            jobs = [(image, n in raster, image_scale(doc[n], image))
                    for n, image in zip(numbers, images)]
            for number, boxes in zip(numbers, pool.map(read, jobs)):
                result.ocr_pages_checked += 1
                words = _ocr_text(boxes)
                hay = Haystack(" ".join(words))
                fuzzy_found = _fuzzy_hits(words, fuzzy)
                for term, entity_id, category, compact in terms:
                    hit = _contains(hay, term, compact, category) or (
                        category is Category.PERSON and normalise(term) in fuzzy_found)
                    if hit:
                        result.leaks.append(Leak(number + 1, category.value, entity_id, term,
                                                 "Bild (OCR)", _leak_boxes(boxes, term)))
                for fragment in iban_fragments(" ".join(words)):
                    result.leaks.append(Leak(number + 1, Category.IBAN.value, "", fragment,
                                             "Bild (OCR, IBAN-Rest)", _leak_boxes(boxes, fragment)))
                if progress:
                    progress(number + 1, doc.page_count, "Prüfstufe 2: Bildprüfung")


_LABEL_RE = re.compile(r"\[[^\]]{1,40}\]|\b\p{Lu}\.(?: \(\d+\))?")


def residual_scan(pages: list[PageInfo], registry: CaseRegistry,
                  settings: Settings) -> list[Suspect] | None:
    """Run the detectors again on the output text and list what they still find.

    ``None``: the scan could not be carried out.
    """
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
        return None
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
