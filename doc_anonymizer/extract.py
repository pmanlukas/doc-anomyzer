"""Text extraction from PDFs (digital text layer plus OCR for scans).

Every page is classified as

* ``digital`` – normal text layer, no relevant images,
* ``mixed``   – text layer plus larger images (images are OCR'd as well),
* ``scan``    – (almost) only an image; OCR is the only text source.

Pages that cannot be cleaned reliably on the vector level (scans, text outside
the visible area, documents with hidden layers) are flagged ``force_raster``
and are re-built as images in the output.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import pymupdf
from PIL import Image

from . import ocr
from .config import Settings
from .models import PageInfo, Word

log = logging.getLogger(__name__)

ProgressFn = Callable[[int, int, str], None]


class PdfOpenError(RuntimeError):
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
        raise PdfOpenError(f"Die Datei „{path.name}“ ist kein lesbares PDF.") from exc
    if doc.needs_pass:
        raise PdfOpenError(
            f"Die Datei „{path.name}“ ist passwortgeschützt. Bitte das PDF zuerst "
            "ohne Passwort speichern (z. B. „Drucken als PDF“).")
    if not doc.is_pdf:
        raise PdfOpenError(f"Die Datei „{path.name}“ ist kein PDF.")
    try:
        doc.bake(annots=True, widgets=True)
    except Exception:  # noqa: BLE001 - very old/odd files: continue without baking
        log.warning("Could not bake annotations of %s", path)
    return doc


def _image_coverage(page: pymupdf.Page) -> float:
    """Fraction of the page area covered by images (0..1, approximate)."""
    page_area = abs(page.rect) or 1.0
    covered = 0.0
    for info in page.get_image_info():
        bbox = pymupdf.Rect(info["bbox"]) & page.rect
        if not bbox.is_empty:
            covered += abs(bbox)
    return min(covered / page_area, 1.0)


def _invisible_text_ratio(page: pymupdf.Page) -> float:
    """Share of characters that are invisible (render mode 3 or fully transparent)."""
    total = hidden = 0
    try:
        for span in page.get_texttrace():
            n = len(span.get("chars", ()))
            total += n
            if span.get("type") == 3 or span.get("opacity", 1) == 0:
                hidden += n
    except Exception:  # noqa: BLE001 - texttrace not available for some pages
        return 0.0
    return hidden / total if total else 0.0


def _native_words(page: pymupdf.Page) -> list[Word]:
    """Words of the text layer, *including* text outside the visible area."""
    raw = page.get_text("words", sort=True, clip=pymupdf.INFINITE_RECT())
    return [Word(w[0], w[1], w[2], w[3], w[4], "text", int(w[5]) * 1000 + int(w[6]))
            for w in raw if w[4].strip()]


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


def render_page(page: pymupdf.Page, dpi: int, gray: bool = True) -> Image.Image:
    """Render a page to a PIL image."""
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY if gray else pymupdf.csRGB,
                          alpha=False)
    mode = "L" if gray else "RGB"
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


def analyse_document(path: Path, settings: Settings,
                     progress: ProgressFn | None = None) -> list[PageInfo]:
    """Extract words from every page of ``path`` (OCR where necessary)."""
    doc = open_pdf(path)
    pages: list[PageInfo] = []
    ocr_jobs: list[tuple[int, Image.Image]] = []
    has_hidden_layers = bool(doc.get_ocgs())

    for page in doc:
        info = PageInfo(index=page.number, width=page.rect.width, height=page.rect.height)
        words = _native_words(page)
        coverage = _image_coverage(page)
        hidden = _invisible_text_ratio(page)

        # Classification (see module docstring).
        if (len(words) < 5 and coverage > 0.3) or (hidden > 0.8 and coverage > 0.5):
            info.kind = "scan"
            info.force_raster = True
        elif coverage >= settings.image_coverage_for_ocr:
            info.kind = "mixed"
        else:
            info.kind = "digital"
        if not words and coverage == 0:
            # Vector drawings only (e.g. text converted to outlines) – the only
            # way to "read" it is OCR, so treat it like a scan.
            info.kind = "scan"
            info.force_raster = True

        # Text outside the visible page area is invisible for the reader but
        # visible for programs/LLMs – re-building the page removes it.
        if any(not pymupdf.Rect(w.x0, w.y0, w.x1, w.y1).intersects(page.rect) for w in words):
            info.force_raster = True
            info.notes.append("Unsichtbarer Text außerhalb der Seite gefunden – "
                              "Seite wird als Bild neu aufgebaut.")
        if has_hidden_layers:
            info.force_raster = True
            info.notes.append("Dokument enthält ausblendbare Ebenen – "
                              "Seite wird als Bild neu aufgebaut.")
        if hidden > 0 and info.kind != "scan":
            info.notes.append("Unsichtbarer Text gefunden – wird geprüft und entfernt.")

        info.words = words
        pages.append(info)
        if info.kind in ("scan", "mixed"):
            ocr_jobs.append((page.number, render_page(page, settings.ocr_dpi)))
        if progress:
            progress(page.number + 1, len(doc), "Seiten lesen")

        # Avoid holding too many large images in memory at once.
        if len(ocr_jobs) >= 8:
            _run_ocr(doc, pages, ocr_jobs, settings, progress)
            ocr_jobs.clear()

    _run_ocr(doc, pages, ocr_jobs, settings, progress)
    doc.close()
    return pages


def _run_ocr(doc: pymupdf.Document, pages: list[PageInfo],
             jobs: list[tuple[int, Image.Image]], settings: Settings,
             progress: ProgressFn | None) -> None:
    """OCR rendered page images in parallel (Tesseract runs as subprocess)."""
    if not jobs:
        return
    if not ocr.is_available():
        raise ocr.OcrUnavailableError(
            "Das PDF enthält eingescannte Seiten, aber die Texterkennung (Tesseract) "
            "ist nicht installiert. Bitte „Installieren.bat“ erneut ausführen.")

    def work(job: tuple[int, Image.Image]) -> tuple[int, list[Word]]:
        number, image = job
        scale = 72.0 / settings.ocr_dpi
        words = ocr.ocr_words(image, scale, settings.ocr_language)
        if settings.thorough_ocr:
            # Second pass in "sparse text" mode catches small print that the
            # layout analysis of the first pass skipped.
            extra = ocr.ocr_words(image, scale, settings.ocr_language, psm=11)
            words = _merge(words, extra)
        return number, words

    with ThreadPoolExecutor(max_workers=_ocr_workers()) as pool:
        for number, words in pool.map(work, jobs):
            page = doc[number]
            if page.rotation:
                mat = page.derotation_matrix
                for w in words:
                    r = pymupdf.Rect(w.x0, w.y0, w.x1, w.y1) * mat
                    w.x0, w.y0, w.x1, w.y1 = r.x0, r.y0, r.x1, r.y1
            info = pages[number]
            info.ocr_quality = ocr_quality(words)
            if info.kind == "scan":
                info.words = _merge(words, info.words)
            else:
                info.words = _merge(info.words, words)
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


def _ocr_workers() -> int:
    """Number of parallel Tesseract processes (leave one core for the UI)."""
    return max(1, min(4, (os.cpu_count() or 2) - 1))
