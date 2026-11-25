"""Verification stage 3: try to *recover* original data from the result.

The previous stages look at the PDF the way a reader does. This stage acts
like an attacker who wants to undo the anonymisation and uses several
independent techniques:

1. **Raw objects** – every PDF object and every decompressed data stream is
   searched for the original terms (plain, UTF-16 and hex encoded). This finds
   leftovers in structure data such as ``/ActualText`` or ``/Alt`` texts of
   tagged PDFs, private application data (``/PieceInfo``), metadata streams,
   bookmarks or form data.
2. **Independent parsers** – the text is extracted with *other* PDF libraries
   (pypdf, pdfminer.six and – if installed – poppler's ``pdftotext``). Each
   parser interprets fonts and content streams differently.
3. **Embedded images** – every image is extracted at its native resolution
   and read with OCR. This finds image parts that are hidden by clipping or
   lie outside the page (and therefore invisible in stage 2).
4. **Linking data** – the document ID must differ from the original so the
   file cannot be matched to the source; no incremental updates, no
   thumbnails, no embedded files.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from PIL import Image

from . import ocr
from .config import Settings

log = logging.getLogger(__name__)

#: Embedded images above this size are not decoded for the image check.
MAX_IMAGE_PIXELS = 120_000_000

#: Keys that must not appear in an anonymised file (hidden text/data carriers).
SUSPICIOUS_KEYS = ("/ActualText", "/Alt ", "/Alt(", "/Alt<", "/StructTreeRoot", "/PieceInfo",
                   "/EmbeddedFile", "/JavaScript", "/AcroForm", "/Thumb", "/OpenAction",
                   "/Outlines", "/Annots")


@dataclass
class Finding:
    """One successful recovery (CONFIDENTIAL – contains the term)."""

    method: str  # e.g. "Rohobjekt", "pypdf", "Bild-OCR"
    term: str
    entity_id: str
    page: int = 0  # 1-based, 0 = unknown / whole file


@dataclass
class ForensicResult:
    findings: list[Finding] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)  # methods that were run
    structure_problems: list[str] = field(default_factory=list)


#: Marked-content properties inside page content streams that carry text
#: ("/Span <</ActualText (Max Mustermann)>> BDC").
_MARKED_TEXT = re.compile(r"/(?:ActualText|Alt|E)\s*(\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f\s]+>)")


def _content_and_font_xrefs(doc: pymupdf.Document) -> tuple[set[int], set[int]]:
    """Xrefs of page content streams and of embedded font programs."""
    contents, fonts = set(), set()
    for page in doc:
        contents.update(page.get_contents())
    for xref in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(xref, compressed=False)
        except Exception:  # noqa: BLE001
            continue
        for key in ("/FontFile", "/FontFile2", "/FontFile3"):
            match = re.search(re.escape(key) + r"\s+(\d+)\s+0\s+R", obj)
            if match:
                fonts.add(int(match.group(1)))
    return contents, fonts


_TOKEN = re.compile(r"[^\W_]{2,}")
_HEX_STRING = re.compile(r"<([0-9A-Fa-f\s]{8,})>")


def _decoded_views(data: bytes) -> str:
    """All plausible text readings of raw object data.

    Plain (Latin-1), UTF-16 (big and little endian, e.g. /ActualText in
    Unicode) and the contents of hexadecimal PDF strings.
    """
    views = [data.decode("latin-1")]
    if data.count(b"\x00") > len(data) // 8:
        views.append(data.decode("utf-16-be", "ignore"))
        views.append(data.decode("utf-16-le", "ignore"))
    for match in _HEX_STRING.finditer(views[0]):
        digits = re.sub(r"\s", "", match.group(1))
        raw = bytes.fromhex(digits[: len(digits) // 2 * 2])
        views.append(raw.decode("latin-1"))
        if raw.startswith(b"\xfe\xff") or raw[:1] == b"\x00":
            views.append(raw.decode("utf-16-be", "ignore"))
    return " ".join(views)


def _raw_object_scan(doc: pymupdf.Document, terms, result: ForensicResult) -> None:
    """Search all objects except the visible page text (checked by stage 1).

    Page content streams are only searched inside marked-content text
    properties (/ActualText, /Alt, /E); their visible text is subject to the
    context rules of stage 1 (e.g. "Landgericht Landshut" stays readable).
    Font programs and image data are binary and skipped (images are read
    with OCR instead).

    For speed every object is indexed once (set of words); only terms whose
    words all occur are then checked as complete phrases.
    """
    patterns = [(" ".join(t.casefold().split()), t.casefold().split(), t, eid)
                for t, eid, _cat, compact in terms if not compact and len(t) >= 4]
    contents, fonts = _content_and_font_xrefs(doc)
    for xref in range(1, doc.xref_length()):
        if xref in fonts:
            continue
        try:
            obj = doc.xref_object(xref, compressed=False)
            data = obj.encode("latin-1", "replace")
            if doc.xref_is_stream(xref) and "/Image" not in obj:
                stream = doc.xref_stream(xref) or b""
                if xref in contents:
                    stream = " ".join(_MARKED_TEXT.findall(stream.decode("latin-1"))).encode(
                        "latin-1")
                data += b" " + stream
        except Exception:  # noqa: BLE001 - broken objects are skipped
            continue
        for key in SUSPICIOUS_KEYS:
            if key in obj and f"{key.strip()} null" not in obj:
                problem = f"Objekt enthält {key.strip()}"
                if problem not in result.structure_problems:
                    result.structure_problems.append(problem)
        text = _decoded_views(data).casefold()
        words = set(_TOKEN.findall(text))
        flat = None
        for phrase, parts, term, eid in patterns:
            if all(p in words for p in parts):
                flat = flat or " ".join(_TOKEN.findall(text))
                if " ".join(_TOKEN.findall(phrase)) in flat:
                    result.findings.append(Finding("Rohobjekt", term, eid))


def _extract_with_other_parsers(path: Path) -> dict[str, list[str]]:
    """Page texts as seen by independent PDF libraries (if installed)."""
    texts: dict[str, list[str]] = {}
    try:
        from pypdf import PdfReader

        texts["pypdf"] = [page.extract_text() or "" for page in PdfReader(str(path)).pages]
    except Exception as exc:  # noqa: BLE001 - optional tool
        log.info("pypdf not usable: %s", exc)
    try:
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTTextContainer

        pages = []
        for layout in extract_pages(str(path)):
            pages.append(" ".join(el.get_text() for el in layout
                                  if isinstance(el, LTTextContainer)))
        texts["pdfminer"] = pages
    except Exception as exc:  # noqa: BLE001 - optional tool
        log.info("pdfminer not usable: %s", exc)
    tool = shutil.which("pdftotext")
    if tool:
        try:
            out = subprocess.run([tool, "-layout", str(path), "-"], capture_output=True,  # noqa: S603
                                 text=True, timeout=300, check=False,
                                 # no console window flashing up (pythonw)
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            texts["pdftotext"] = out.split("\f")
        except Exception as exc:  # noqa: BLE001 - optional tool
            log.info("pdftotext not usable: %s", exc)
    return texts


def _image_ocr(doc: pymupdf.Document, terms, settings: Settings, skip_pages: set[int],
               result: ForensicResult, contains) -> None:
    """OCR every embedded image at native resolution."""
    from .verify import Haystack

    seen: set[int] = set()
    for page in doc:
        if page.number in skip_pages:
            continue
        for img in page.get_images(full=True):
            xref = img[0]
            if xref in seen:
                continue
            seen.add(xref)
            if img[2] * img[3] > MAX_IMAGE_PIXELS:
                # Too large to decode safely (plans, "bomb" images): cannot be
                # checked, so the page is re-built as a plain image instead.
                result.findings.append(Finding(
                    "Bild-OCR (Bild zu groß für die Prüfung – Seite wird neu aufgebaut)",
                    "", "", page.number + 1))
                continue
            try:
                pix = pymupdf.Pixmap(doc, xref)
                if pix.width < 60 or pix.height < 20:
                    continue
                if pix.alpha:
                    pix = pymupdf.Pixmap(pix, 0)
                if pix.n - pix.alpha != 1:
                    pix = pymupdf.Pixmap(pymupdf.csGRAY, pix)
                image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
            except Exception:  # noqa: BLE001 - exotic image formats
                continue
            # Small images are enlarged so Tesseract can read them.
            if image.width < 1200:
                factor = min(4.0, 1200 / image.width)
                image = image.resize((int(image.width * factor), int(image.height * factor)))
            words = ocr.ocr_words(image, 1.0, settings.ocr_language)
            hay = Haystack(" ".join(w.text for w in words))
            for term, eid, category, compact in terms:
                if contains(hay, term, compact, category):
                    result.findings.append(Finding("Bild-OCR (eingebettetes Bild)", term, eid,
                                                   page.number + 1))


def forensic_check(output: Path, source: Path | None, terms, settings: Settings,
                   raster_pages: set[int]) -> ForensicResult:
    """Run all recovery attempts on ``output``."""
    from .verify import Haystack, _contains

    result = ForensicResult()
    doc = pymupdf.open(output)

    result.methods.append("Rohobjekte und entpackte Datenströme")
    _raw_object_scan(doc, terms, result)

    for name, pages in _extract_with_other_parsers(output).items():
        result.methods.append(f"unabhängiger Parser: {name}")
        for number, text in enumerate(pages, 1):
            hay = Haystack(text)
            for term, eid, category, compact in terms:
                if _contains(hay, term, compact, category):
                    result.findings.append(Finding(name, term, eid, number))

    if ocr.is_available():
        result.methods.append("Texterkennung aller eingebetteten Bilder in Originalauflösung")
        # Raster pages consist of one freshly rendered image identical to the
        # visible page, which stage 2 already read.
        _image_ocr(doc, terms, settings, raster_pages, result, _contains)

    if source is not None:
        result.methods.append("Abgleich der Dokumentkennung mit dem Original")
        try:
            with pymupdf.open(source) as original:
                src_id = re.search(r"/ID\s*\[\s*<([0-9A-Fa-f]+)>", original.pdf_trailer())
            out_id = re.search(r"/ID\s*\[\s*<([0-9A-Fa-f]+)>", doc.pdf_trailer())
            if src_id and out_id and src_id.group(1).lower() == out_id.group(1).lower():
                result.structure_problems.append(
                    "Dokumentkennung (/ID) identisch mit dem Original")
        except Exception:  # noqa: BLE001 - source may be unreadable
            pass
    doc.close()
    return result
