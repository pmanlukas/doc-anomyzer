"""Irreversible redaction and re-building of the output PDF.

Why the result cannot be restored:

* **True redaction** – PyMuPDF/MuPDF *removes* the characters from the page's
  content stream and overwrites image pixels below the redaction area. Simply
  drawing a black box (which can be removed again) is never done.
* **Fresh document** – the output is a *new* PDF into which only the cleaned
  pages are copied. Metadata, XMP, bookmarks, attachments, JavaScript, form
  fields, comments, links and old revisions of the source file are therefore
  not carried over.
* **Raster pages** – scanned pages (and, in "maximum security" mode, all
  pages) are rendered to a new image after redaction. Their text layer is
  re-created exclusively from the already anonymised word list.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pymupdf

from .config import Settings
from .extract import open_pdf
from .models import ManualBox, PageInfo, Word
from .registry import CaseRegistry
from .textmap import PageText

log = logging.getLogger(__name__)

ProgressFn = Callable[[int, int, str], None]

FILL = (0.86, 0.86, 0.86)  # light grey: clearly visible, still printable
TEXT_COLOR = (0, 0, 0)
FONT = "helv"
#: Automatically detected handwriting areas (see :mod:`doc_anonymizer.signatures`).
SIGNATURE_KINDS = {"unterschrift", "handschrift", "stempel"}


@dataclass
class Box:
    """One rectangle to redact plus the text printed into it."""

    rect: pymupdf.Rect
    text: str = ""
    from_ocr: bool = False


@dataclass
class Segment:
    """A piece of the anonymised text in reading order (word or replacement)."""

    rect: pymupdf.Rect
    text: str
    line_id: int


@dataclass
class PagePlan:
    boxes: list[Box] = field(default_factory=list)
    #: Anonymised text of the page in reading order. Used for the text layer
    #: of raster pages and for the plain-text export.
    segments: list[Segment] = field(default_factory=list)

    def plain_text(self) -> str:
        """Anonymised page text, one line per visual line."""
        lines: list[str] = []
        current: int | None = None
        for seg in self.segments:
            if seg.line_id != current:
                lines.append(seg.text)
                current = seg.line_id
            else:
                lines[-1] += " " + seg.text
        return "\n".join(lines)


@dataclass
class RedactionResult:
    pages: int = 0
    raster_pages: int = 0
    boxes: int = 0
    raster_indices: list[int] = field(default_factory=list)
    #: Anonymised plain text per page (for the text export)
    texts: list[str] = field(default_factory=list)


def _union(words: list[Word]) -> pymupdf.Rect:
    rect = pymupdf.Rect(words[0].x0, words[0].y0, words[0].x1, words[0].y1)
    for w in words[1:]:
        rect |= pymupdf.Rect(w.x0, w.y0, w.x1, w.y1)
    return rect


def plan_page(registry: CaseRegistry, doc_index: int, page: PageInfo,
              manual: list[ManualBox]) -> PagePlan:
    """Work out which areas of a page are redacted and what is printed there."""
    plan = PagePlan()
    #: word index -> replacement text (only for the first word of a mention)
    replacement_at: dict[int, tuple[pymupdf.Rect, str]] = {}
    redacted: set[int] = set()
    for mention in registry.mentions:
        if mention.doc != doc_index or mention.page != page.index:
            continue
        if not registry.entities[mention.entity_id].enabled:
            continue
        words = [page.words[i] for i in mention.word_indices]
        redacted.update(mention.word_indices)
        # One box per visual line; the replacement goes into the first one.
        lines: dict[int, list[Word]] = {}
        for word in words:
            lines.setdefault(word.line_id, []).append(word)
        text = registry.replacement_for(mention)
        for n, line_words in enumerate(lines.values()):
            rect = _union(line_words)
            plan.boxes.append(Box(rect, text if n == 0 else "",
                                  any(w.source == "ocr" for w in line_words)))
            if n == 0:
                replacement_at[mention.word_indices[0]] = (rect, text)
    for box in manual:
        mrect = pymupdf.Rect(box.x0, box.y0, box.x1, box.y1)
        plan.boxes.append(Box(mrect, box.label, True))
        # Words under a manual box are removed from the text layer as well.
        covered = [i for i, word in enumerate(page.words)
                   if pymupdf.Rect(word.x0, word.y0, word.x1, word.y1).intersects(mrect)]
        redacted.update(covered)
        if covered:
            replacement_at.setdefault(covered[0], (mrect, box.label))

    # Anonymised text in reading order.
    ptext = PageText.build(page.words)
    for idx in ptext.order:
        word = page.words[idx]
        if idx in replacement_at:
            rect, text = replacement_at[idx]
            plan.segments.append(Segment(rect, text, word.line_id))
        elif idx not in redacted:
            plan.segments.append(Segment(pymupdf.Rect(word.x0, word.y0, word.x1, word.y1),
                                         word.text, word.line_id))
    return plan


def _removal_rect(box: Box) -> pymupdf.Rect:
    """Area handed to MuPDF for removing text and image pixels.

    For text-layer words the box is shrunk vertically: character boxes of the
    neighbouring lines often overlap the word box, and MuPDF removes every
    character that touches the redaction area. OCR boxes (image areas) are
    slightly *enlarged* instead so no pixel of the letters survives.
    """
    r = pymupdf.Rect(box.rect)
    if box.from_ocr:
        return r + (-1.5, -1.5, 1.5, 1.5)
    h = r.height
    return pymupdf.Rect(r.x0 + 0.3, r.y0 + 0.28 * h, r.x1 - 0.3, r.y1 - 0.28 * h)


def _fit_fontsize(text: str, rect: pymupdf.Rect, max_size: float) -> float:
    width = pymupdf.get_text_length(text, fontname=FONT, fontsize=1) or 1
    return max(2.5, min(max_size, (rect.width - 1) / width))


def _draw_box(page: pymupdf.Page, box: Box) -> None:
    """Grey box with the replacement text (e.g. "M. M." or "[Adresse 1]")."""
    if box.from_ocr:
        # OCR boxes hug the glyphs tightly – draw the (slightly larger) area
        # that was actually cleared and size the text to the glyph height.
        rect = _removal_rect(box)
        # Capped: on poor scans OCR boxes can be much taller than the text.
        max_size = min(box.rect.height * 1.15, 12.0)
        baseline_y = box.rect.y1 - box.rect.height * 0.12
    else:
        # Font size and baseline imitate the original line so that text
        # extraction tools keep the replacement inside its line.
        rect = box.rect
        max_size, baseline_y = rect.height * 0.72, rect.y0 + rect.height * 0.78
    page.draw_rect(rect, color=None, fill=FILL, overlay=True)
    if not box.text:
        return
    size = _fit_fontsize(box.text, rect, max_size)
    baseline = pymupdf.Point(rect.x0 + 0.5, baseline_y)
    page.insert_text(baseline * page.rotation_matrix if page.rotation else baseline,
                     box.text, fontsize=size, fontname=FONT, color=TEXT_COLOR,
                     rotate=page.rotation)


#: Letters outside the PDF base fonts' character set (Windows-1252) and their
#: closest Latin equivalent for the invisible text layer of image pages.
_TRANSLIT = str.maketrans({"ı": "i", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ħ": "h",
                           "ŋ": "n", "ß": "ß", "ə": "e", "ʼ": "'", "‐": "-", "−": "-"})


def encodable(text: str) -> str:
    """Make ``text`` representable in the base font of the text layer.

    Otherwise e.g. "Steuer-ıD" (OCR) would be written as "Steuer-·D" and the
    text layer would differ from what the detection saw.
    """
    out = []
    for ch in text.translate(_TRANSLIT):
        try:
            ch.encode("cp1252")
            out.append(ch)
        except UnicodeEncodeError:
            base = "".join(c for c in unicodedata.normalize("NFKD", ch)
                           if not unicodedata.combining(c))
            try:
                base.encode("cp1252")
                out.append(base)
            except UnicodeEncodeError:
                out.append("?")
    return "".join(out)


def _insert_text_layer(page: pymupdf.Page, segments: list[Segment],
                       matrix: pymupdf.Matrix | None) -> None:
    """Invisible, searchable text (render mode 3) on top of a page image.

    Each visual line is written as *one* text run (stretched to the width of
    the line), so programs that extract the text get whole lines and natural
    sentences instead of single words.
    """
    lines: dict[int, list[Segment]] = {}
    for seg in segments:
        lines.setdefault(seg.line_id, []).append(seg)
    for line in lines.values():
        rect = pymupdf.Rect(line[0].rect)
        for seg in line[1:]:
            rect |= seg.rect
        if matrix is not None:
            rect = rect * matrix
        text = encodable(" ".join(seg.text for seg in line).strip())
        if not text or rect.is_empty or rect.height <= 0:
            continue
        size = max(1.0, min(rect.height * 0.8, 40))
        natural = pymupdf.get_text_length(text, fontname=FONT, fontsize=size) or 1
        point = pymupdf.Point(rect.x0, rect.y0 + rect.height * 0.8)
        # Horizontal stretch so the invisible line covers the visible one.
        stretch = pymupdf.Matrix(max(rect.width / natural, 0.05), 1)
        try:
            page.insert_text(point, text, fontsize=size, fontname=FONT, render_mode=3,
                             morph=(point, stretch))
        except Exception:  # noqa: BLE001 - one odd glyph must not abort the page
            log.debug("could not insert text layer line %r", text)


def redact_document(source: Path, target: Path, doc_index: int, pages: list[PageInfo],
                    registry: CaseRegistry, settings: Settings,
                    progress: ProgressFn | None = None) -> RedactionResult:
    """Write the anonymised copy of ``source`` to ``target``."""
    result = RedactionResult(pages=len(pages))
    src = open_pdf(source)
    plans: list[PagePlan] = []

    # 1) Remove text and image pixels under every box (true redaction).
    for info in pages:
        page = src[info.index]
        manual = [b for b in registry.manual_boxes
                  if b.doc == doc_index and b.page == info.index]
        plan = plan_page(registry, doc_index, info, manual)
        plans.append(plan)
        # Signatures drawn as vector paths: MuPDF only counts a stroke as
        # "covered" with a generous margin (line width x miter limit), so a
        # separate pass removes line art only – text and images untouched.
        vector_areas = [b for b in manual if b.kind in SIGNATURE_KINDS]
        if vector_areas:
            for b in vector_areas:
                page.add_redact_annot(pymupdf.Rect(b.x0, b.y0, b.x1, b.y1) + (-15, -15, 15, 15),
                                      fill=False)
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                                  graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                                  text=pymupdf.PDF_REDACT_TEXT_NONE)
        for box in plan.boxes:
            page.add_redact_annot(_removal_rect(box), fill=False)
        if plan.boxes:
            # Vector drawings lying completely inside a box are removed
            # (signatures from signature pads, text converted to outlines);
            # table lines crossing a box stay.
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                                  graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                                  text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        result.boxes += len(plan.boxes)
        if progress:
            progress(info.index + 1, len(pages), "Daten entfernen")

    # 2) Remove hidden text, metadata, scripts, attachments, links ...
    src.scrub(attached_files=True, clean_pages=True, embedded_files=True, hidden_text=True,
              javascript=True, metadata=True, redactions=True, remove_links=True,
              reset_fields=True, reset_responses=True, thumbnails=True, xml_metadata=True)

    # 2b) Tagged PDFs (e.g. from Word) repeat text in marked-content
    #     properties such as /ActualText or /Alt – invisible, but extractable.
    for info in pages:
        _strip_marked_content_properties(src, src[info.index])

    # 3) Draw the visible replacement boxes.
    for info, plan in zip(pages, plans):
        page = src[info.index]
        for box in plan.boxes:
            _draw_box(page, box)

    # 4) Build a brand-new PDF from the cleaned pages.
    out = pymupdf.open()
    for info, plan in zip(pages, plans):
        page = src[info.index]
        raster = info.force_raster or settings.security_mode == "maximum"
        if not raster:
            out.insert_pdf(src, from_page=info.index, to_page=info.index,
                           links=False, annots=False, widgets=False)
        else:
            result.raster_pages += 1
            result.raster_indices.append(info.index)
            _rebuild_as_image(out, page, plan, registry, doc_index, info, settings)
        if progress:
            progress(info.index + 1, len(pages), "Neues PDF erzeugen")

    result.texts = [plan.plain_text() for plan in plans]
    # Re-subset embedded fonts: they would otherwise still contain the glyphs
    # of removed characters (revealing e.g. that an "ł" occurred somewhere).
    try:
        out.subset_fonts()
    except Exception:  # noqa: BLE001 - unusual fonts: keep them as they are
        log.info("font subsetting failed", exc_info=True)
    out.set_metadata({})
    out.del_xml_metadata()
    target.parent.mkdir(parents=True, exist_ok=True)
    out.save(target, garbage=4, deflate=True, clean=True)
    out.close()
    src.close()
    return result


_BDC_WITH_PROPERTIES = re.compile(rb"/([A-Za-z0-9_.\-]+)\s*<<(?:(?!>>).)*?>>\s*BDC", re.S)


def _strip_marked_content_properties(doc: pymupdf.Document, page: pymupdf.Page) -> None:
    """Replace "/Span <</ActualText (…)>> BDC" by "/Span BMC" in the page content.

    The marked-content structure stays valid, only the property dictionaries
    (which may carry alternative text) are dropped.
    """
    for xref in page.get_contents():
        stream = doc.xref_stream(xref)
        if not stream or b"BDC" not in stream:
            continue
        cleaned = _BDC_WITH_PROPERTIES.sub(rb"/\1 BMC", stream)
        if cleaned != stream:
            doc.update_stream(xref, cleaned)


def _rebuild_as_image(out: pymupdf.Document, page: pymupdf.Page, plan: PagePlan,
                      registry: CaseRegistry, doc_index: int, info: PageInfo,
                      settings: Settings) -> None:
    """Replace a page by an image of itself plus a clean, invisible text layer."""
    pix = page.get_pixmap(dpi=settings.raster_dpi, alpha=False)
    new = out.new_page(width=page.rect.width, height=page.rect.height)
    # JPEG keeps hundreds of pages small; quality 80 is easily readable.
    new.insert_image(new.rect, stream=pix.tobytes("jpeg", jpg_quality=80))

    # Words are stored in unrotated coordinates, the image shows the rotated
    # view -> convert.
    _insert_text_layer(new, plan.segments, page.rotation_matrix if page.rotation else None)
