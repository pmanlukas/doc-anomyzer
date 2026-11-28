"""Text extraction from PDFs (digital text layer plus OCR for scans).

Every page is classified as

* ``digital`` – normal text layer, no relevant images,
* ``mixed``   – text layer plus larger images (images are OCR'd as well),
* ``scan``    – (almost) only an image; OCR is the only text source.

Pages that cannot be cleaned reliably on the vector level (scans, documents
with hidden layers, pages with text that is in the file but not visible) are
flagged ``force_raster`` and are re-built as images in the output.

**What you see is what you get:** every word of the text layer is checked for
being *visible* (see :func:`visible_rects`). Words under a black bar, in white
or invisible writing, clipped away or outside the page are flagged
:data:`~doc_anonymizer.models.HIDDEN`. They take part in the detection – but
they are never carried over into the result.
"""

from __future__ import annotations

import logging
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import numpy as np
import pymupdf
from PIL import Image

from . import ocr
from .config import Settings
from .models import HIDDEN, ON_IMAGE, SLANTED, VERTICAL, PageInfo, Word
from .errors import UserError
from .parallel import ocr_threads
from .signatures import detect_in_image, detect_vector_signatures

log = logging.getLogger(__name__)

ProgressFn = Callable[[int, int, str], None]


class PdfOpenError(UserError):
    """The PDF cannot be opened (damaged or password protected)."""


def open_pdf(path: Path) -> pymupdf.Document:
    """Open a PDF and normalise annotations and form fields into page content.

    ``bake()`` turns visible annotations and filled-in form fields into
    ordinary page content, so their text is detected and redacted like any
    other text. Remaining (non-visible) annotations are removed later.
    """
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # noqa: BLE001 - PyMuPDF raises various types
        raise PdfOpenError("Die Datei ist beschädigt oder kein lesbares PDF.") from exc
    # (Messages without the file name: inside the program the document is
    #  stored under a neutral name; the interface shows the real one.)
    if doc.needs_pass:
        raise PdfOpenError(
            "Das PDF ist passwortgeschützt. Bitte zuerst ohne Passwort speichern "
            "(z. B. im PDF-Programm „Drucken“ → „Als PDF speichern“).")
    if not doc.is_pdf:
        raise PdfOpenError("Die Datei ist kein PDF.")
    if doc.page_count == 0:
        raise PdfOpenError("Das PDF enthält keine Seiten.")
    if doc.page_count > MAX_PAGES:
        raise PdfOpenError(
            f"Das PDF hat {doc.page_count} Seiten – mehr als das Programm verarbeitet "
            f"({MAX_PAGES}). Bitte in mehrere Teile aufteilen.")
    try:
        doc.bake(annots=True, widgets=True)
    except Exception:  # noqa: BLE001 - very old/odd files: continue without baking
        log.warning("Could not bake annotations of %s", path)
    return doc


#: Images below this area (pt²) are decoration (lines, bullets) and ignored.
MIN_IMAGE_AREA = 100.0


def _image_boxes(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Visible image areas of a page (clipped to the page)."""
    boxes = []
    for info in page.get_image_info():
        bbox = pymupdf.Rect(info["bbox"]) & page.rect
        if not bbox.is_empty and abs(bbox) >= MIN_IMAGE_AREA:
            boxes.append(bbox)
    return boxes


def _coverage(boxes: list[pymupdf.Rect], page: pymupdf.Page) -> float:
    """Fraction of the page area covered by ``boxes`` (0..1, approximate)."""
    return min(sum(abs(b) for b in boxes) / (abs(page.rect) or 1.0), 1.0)


def _trace(page: pymupdf.Page) -> tuple[float, list[tuple[pymupdf.Rect, int]]]:
    """Share of invisible characters and the areas of non-horizontal text.

    Invisible: render mode 3 or fully transparent (the text layer of scans).
    Non-horizontal text is returned as (area, :data:`VERTICAL` | :data:`SLANTED`).
    """
    total = hidden = 0
    rotated: list[tuple[pymupdf.Rect, int]] = []
    try:
        for span in page.get_texttrace():
            n = len(span.get("chars", ()))
            total += n
            if span.get("type") == 3 or span.get("opacity", 1) == 0:
                hidden += n
            dx, dy = span.get("dir", (1.0, 0.0))
            if abs(dy) > 0.1:
                rotated.append((pymupdf.Rect(span["bbox"]),
                                VERTICAL if abs(dx) < 0.1 else SLANTED))
    except Exception:  # noqa: BLE001 - texttrace not available for some pages
        return 0.0, []
    return (hidden / total if total else 0.0), rotated


#: Resolution of the two renderings compared to find invisible words.
VISIBILITY_DPI = 96
#: Smallest difference of grey values (0..255) that counts as visible.
VISIBILITY_THRESHOLD = 12


def visible_rects(doc: pymupdf.Document, page: pymupdf.Page,
                  rects: list[tuple[float, float, float, float]]) -> list[bool]:
    """Which of the word boxes ``rects`` show visible text?

    The page is rendered twice: as it is, and with *all* text removed. Where
    removing the text changes no pixel inside a word's box, the word was not
    visible – whatever the trick: white on white, covered by a bar or an
    image, invisible render mode, zero opacity, clipped away, outside the
    page. This needs no knowledge about the many ways to hide text.

    ``rects`` are in the unrotated page coordinates used throughout the
    program.
    """
    blank = pymupdf.open()
    try:
        blank.insert_pdf(doc, from_page=page.number, to_page=page.number,
                         links=False, annots=False, widgets=False)
        bpage = blank[0]
        bpage.add_redact_annot(pymupdf.Rect(-1e5, -1e5, 1e5, 1e5), fill=False)
        bpage.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                               graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                               text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        zoom = effective_dpi(page, VISIBILITY_DPI, 8_000_000) / 72
        matrix = pymupdf.Matrix(zoom, zoom)
        shots = []
        for candidate in (page, bpage):
            pix = candidate.get_pixmap(matrix=matrix, colorspace=pymupdf.csGRAY, alpha=False)
            shots.append(np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)
                         [:, :pix.width].astype(np.int16))
    finally:
        blank.close()
    if shots[0].shape != shots[1].shape:
        raise ValueError("page images differ in size")
    changed = np.abs(shots[0] - shots[1]) >= VISIBILITY_THRESHOLD
    to_pixels = page.rotation_matrix * matrix
    height, width = changed.shape
    result = []
    for rect in rects:
        r = pymupdf.Rect(rect) * to_pixels
        r.normalize()
        x0, y0 = max(int(r.x0), 0), max(int(r.y0), 0)
        x1, y1 = min(int(r.x1) + 1, width), min(int(r.y1) + 1, height)
        result.append(x1 > x0 and y1 > y0 and bool(changed[y0:y1, x0:x1].any()))
    return result


def _flag_words(doc: pymupdf.Document, page: pymupdf.Page, words: list[Word]) -> bool:
    """Set the HIDDEN/VERTICAL/SLANTED/ON_IMAGE flags of the text-layer words.

    Returns ``False`` if the visibility could not be determined (the page is
    then re-built as an image to be on the safe side).
    """
    if not words:
        return True
    invisible_share, rotated = _trace(page)
    for area, kind in rotated:
        for word in words:
            if area.contains(pymupdf.Point((word.x0 + word.x1) / 2, (word.y0 + word.y1) / 2)):
                word.flags |= kind
    checked = True
    if invisible_share >= 0.999:
        visible = [False] * len(words)  # text layer of a scan – no rendering needed
        hide_all = True
    else:
        hide_all = False
        try:
            visible = visible_rects(doc, page, [(w.x0, w.y0, w.x1, w.y1) for w in words])
        except Exception:  # noqa: BLE001 - unusual page: handled by the caller
            log.warning("visibility of the text on page %d could not be checked",
                        page.number + 1, exc_info=True)
            return False
    images = [pymupdf.Rect(info["bbox"]) for info in page.get_image_info()]
    by_line: dict[int, list[Word]] = {}
    for word, seen in zip(words, visible):
        by_line.setdefault(word.line_id, []).append(word)
        # Single punctuation marks are too small to judge on their own.
        if not seen and (hide_all or any(ch.isalnum() for ch in word.text)):
            word.flags |= HIDDEN
    # Punctuation shares the fate of its line ("Herr Mustermann , geb .").
    for line in by_line.values():
        judged = [w for w in line if any(ch.isalnum() for ch in w.text)]
        if judged and all(w.hidden for w in judged):
            for word in line:
                word.flags |= HIDDEN
    for word in words:
        if word.hidden:
            centre = pymupdf.Point((word.x0 + word.x1) / 2, (word.y0 + word.y1) / 2)
            if any(image.contains(centre) for image in images):
                word.flags |= ON_IMAGE
    return checked


#: Invisible characters that can be used to hide a name from a text search
#: ("Mus\u200btermann") – removed before detection.
_INVISIBLE_CHARS = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad\u180e"))


def clean_word(text: str) -> str:
    """Unicode-normalise a word: ligatures ("ﬁ" -> "fi"), full-width letters,
    zero-width characters and soft hyphens."""
    return unicodedata.normalize("NFKC", text).translate(_INVISIBLE_CHARS)


def _native_words(page: pymupdf.Page) -> list[Word]:
    """Words of the text layer, *including* text outside the visible area."""
    flags = pymupdf.TEXTFLAGS_WORDS & ~pymupdf.TEXT_PRESERVE_LIGATURES
    raw = page.get_text("words", sort=True, clip=pymupdf.INFINITE_RECT(), flags=flags)
    words = []
    for w in raw:
        text = clean_word(w[4])
        if text.strip():
            words.append(Word(w[0], w[1], w[2], w[3], text, "text", int(w[5]) * 1000 + int(w[6])))
    return words


def _overlap(a: Word, b: Word) -> float:
    """Intersection area divided by the smaller box area."""
    ix = max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0))
    iy = max(0.0, min(a.y1, b.y1) - max(a.y0, b.y0))
    smaller = min((a.x1 - a.x0) * (a.y1 - a.y0), (b.x1 - b.x0) * (b.y1 - b.y0)) or 1.0
    return ix * iy / smaller


def _merge(primary: list[Word], secondary: list[Word]) -> list[Word]:
    """Add words from ``secondary`` that are not already covered by ``primary``."""
    merged = list(primary)
    for word in secondary:
        if not any(_overlap(word, p) > 0.5 for p in primary):
            merged.append(word)
    return merged


#: Upper limit for a rendered page image. Very large pages (construction
#: plans in A0, but also deliberately oversized "bomb" pages) are rendered at
#: a lower resolution instead of exhausting the memory. A4 at 300 dpi has
#: 8.7 million pixels, A0 at 300 dpi 139 million.
MAX_RENDER_PIXELS = 60_000_000
#: Documents with more pages are refused (protects against hostile files).
MAX_PAGES = 5000
#: Rendered page images waiting for OCR (bytes) – eight colour A4 pages.
MAX_PENDING_OCR_BYTES = 220_000_000


def effective_dpi(page: pymupdf.Page, dpi: float, limit: int = MAX_RENDER_PIXELS) -> float:
    """``dpi``, reduced if the rendered page would exceed ``limit`` pixels."""
    pixels = (page.rect.width / 72 * dpi) * (page.rect.height / 72 * dpi)
    if pixels <= limit:
        return dpi
    return max(20.0, dpi * (limit / pixels) ** 0.5)


def render_page(page: pymupdf.Page, dpi: float, gray: bool = True) -> Image.Image:
    """Render a page to a PIL image (resolution capped, see :func:`effective_dpi`).

    Callers that map image coordinates back to the page must use
    :func:`image_scale` – the image may be smaller than ``dpi`` suggests.
    """
    zoom = effective_dpi(page, dpi) / 72
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom),
                          colorspace=pymupdf.csGRAY if gray else pymupdf.csRGB, alpha=False)
    mode = "L" if gray else "RGB"
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


def image_scale(page: pymupdf.Page, image: Image.Image) -> float:
    """PDF points per pixel of an image made by :func:`render_page`."""
    return page.rect.width / image.width


def analyse_document(path: Path, settings: Settings,
                     progress: ProgressFn | None = None) -> list[PageInfo]:
    """Extract words from every page of ``path`` (OCR where necessary)."""
    doc = open_pdf(path)
    pages: list[PageInfo] = []
    ocr_jobs: list[tuple[int, Image.Image, float]] = []
    ocr_block = min(8, 2 * ocr_threads())  # page images waiting for the text recognition
    has_hidden_layers = bool(doc.get_ocgs())

    pending_pixels = 0
    for page in doc:
        info = PageInfo(index=page.number, width=page.rect.width, height=page.rect.height)
        words = _native_words(page)
        images = _image_boxes(page)
        coverage = _coverage(images, page)
        visibility_known = _flag_words(doc, page, words)
        visible_count = sum(1 for w in words if not w.hidden)
        hidden = 1 - visible_count / len(words) if words else 0.0
        is_scan = (visible_count < 5 and coverage > 0.3) or (hidden > 0.8 and coverage > 0.5)
        if not is_scan and settings.remove_small_images:
            # Logos, signatures, stamps, photos on text pages: removed as a
            # whole (OCR cannot read stylised names in logos reliably).
            limit = settings.small_image_max_coverage * abs(page.rect)
            small = [b for b in images if abs(b) < limit]
            info.image_boxes = [[b.x0, b.y0, b.x1, b.y1] for b in small]
            images = [b for b in images if abs(b) >= limit]
            coverage = _coverage(images, page)

        # Classification (see module docstring).
        if is_scan:
            info.kind = "scan"
            info.force_raster = True
        elif coverage >= settings.image_coverage_for_ocr or images:
            info.kind = "mixed"  # larger images (enclosures) are read with OCR
        else:
            info.kind = "digital"
        if not visible_count and not images and not info.image_boxes:
            # Vector drawings only (e.g. text converted to outlines) – the only
            # way to "read" it is OCR, so treat it like a scan.
            info.kind = "scan"
            info.force_raster = True

        # Text that is in the file but cannot be seen (under a black bar, in
        # white, outside the page …) is invisible for the reader but visible
        # for programs/LLMs. It is not carried over; re-building the page as
        # an image removes it from the page itself. (The invisible text layer
        # of a scan is normal and needs no hint.)
        if visible_count < len(words) and not is_scan:
            info.force_raster = True
            info.notes.append("Verdeckter oder unsichtbarer Text gefunden (z. B. unter einem "
                              "Balken oder in weißer Schrift) – er wird nicht übernommen, "
                              "die Seite wird als Bild neu aufgebaut.")
        if not visibility_known:
            info.force_raster = True
        if has_hidden_layers:
            info.force_raster = True
            info.notes.append("Dokument enthält ausblendbare Ebenen – "
                              "Seite wird als Bild neu aufgebaut.")

        info.words = words
        pages.append(info)
        if info.kind in ("scan", "mixed"):
            # Colour render: ink colour helps to find signatures.
            gray = not settings.detect_signatures
            image = render_page(page, settings.ocr_dpi, gray=gray)
            ocr_jobs.append((page.number, image, image_scale(page, image)))
            pending_pixels += image.width * image.height * len(image.getbands())
        elif settings.detect_signatures:
            info.signature_boxes = [c.to_list() for c in detect_vector_signatures(page, words)]
        if progress:
            progress(page.number + 1, len(doc), "Seiten lesen")

        # Avoid holding too many large images in memory at once: two pages
        # per parallel reader (at most eight A4 pages – or fewer, if the
        # pages are very large or the computer has little memory).
        if len(ocr_jobs) >= ocr_block or pending_pixels >= MAX_PENDING_OCR_BYTES:
            _run_ocr(doc, pages, ocr_jobs, settings, progress)
            ocr_jobs.clear()
            pending_pixels = 0

    _run_ocr(doc, pages, ocr_jobs, settings, progress)
    doc.close()
    return pages


def _run_ocr(doc: pymupdf.Document, pages: list[PageInfo],
             jobs: list[tuple[int, Image.Image, float]], settings: Settings,
             progress: ProgressFn | None) -> None:
    """OCR rendered page images in parallel (Tesseract runs as subprocess)."""
    if not jobs:
        return
    if not ocr.is_available():
        raise ocr.OcrUnavailableError(
            "Das PDF enthält eingescannte Seiten, aber die Texterkennung ist nicht "
            "installiert. Bitte das Setup des Programms noch einmal ausführen.")

    def work(job: tuple[int, Image.Image, float]):
        number, image, scale = job
        gray = image.convert("L") if image.mode != "L" else image
        words = ocr.ocr_words(gray, scale, settings.ocr_language)
        if settings.thorough_ocr:
            # Second pass in "sparse text" mode catches small print that the
            # layout analysis of the first pass skipped.
            extra = ocr.ocr_words(gray, scale, settings.ocr_language, psm=11)
            words = _merge(words, extra)
        signatures = []
        if settings.detect_signatures:
            width, height = image.width * scale, image.height * scale
            try:
                signatures = detect_in_image(image, width, height, words)
            except Exception:  # noqa: BLE001 - detection must never stop the analysis
                log.exception("signature detection failed")
        return number, words, signatures

    with ThreadPoolExecutor(max_workers=ocr_threads()) as pool:
        for number, words, signatures in pool.map(work, jobs):
            page = doc[number]
            if page.rotation:
                mat = page.derotation_matrix
                for item in [*words, *signatures]:
                    r = pymupdf.Rect(item.x0, item.y0, item.x1, item.y1) * mat
                    item.x0, item.y0, item.x1, item.y1 = r.x0, r.y0, r.x1, r.y1
            info = pages[number]
            info.signature_boxes = [c.to_list() for c in signatures]
            info.ocr_quality = ocr_quality(words)
            # What is *seen* wins: a hidden text layer never suppresses the
            # words read from the page image (it could claim "nothing here"
            # where the image shows a name). Hidden words are kept only where
            # the OCR found nothing – as hints for the detection.
            native = [w for w in info.words if not w.hidden]
            concealed = [w for w in info.words if w.hidden]
            if info.kind == "scan":
                merged = _merge(words, native)
            else:
                merged = _merge(native, words)
            info.words = _merge(merged, concealed)
            if info.poorly_readable:
                info.notes.append(
                    f"Seite {number + 1} ist schlecht lesbar (Lesbarkeit {info.ocr_quality:.0f} %) – "
                    "bitte diese Seite selbst ansehen und Namen/Daten mit der Maus markieren.")
            if progress:
                progress(number + 1, len(doc), "Texterkennung (OCR)")


def ocr_quality(words: list[Word]) -> float:
    """Mean OCR confidence weighted by word length (0 if nothing was read)."""
    letters = [(len(w.text), w.conf) for w in words if any(ch.isalnum() for ch in w.text)]
    total = sum(n for n, _ in letters)
    return sum(n * c for n, c in letters) / total if total else 0.0
