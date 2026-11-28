"""PDFs that hide a name in tricky places – used to test that nothing can be
recovered after anonymisation.

Every document contains the visible sentence "Herr Max Mustermann ..." (so
the detector knows the person) plus the name hidden in one particular way.
The recovery helper :func:`recover` deliberately does *not* use the
application's own verification code: it combines several independent
extraction techniques the way an attacker would.
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

SECRET = "Mustermann"
VISIBLE = ("Az. 12 O 345/21\n\nIn dem Rechtsstreit erklärt Herr Max Mustermann, wohnhaft "
           "Lindenstraße 12, 80331 München, dass die Forderung unbegründet ist.\n"
           "Der Anspruch ergibt sich aus § 280 Abs. 1 BGB.")


def _base(text: str = VISIBLE) -> tuple[pymupdf.Document, pymupdf.Page]:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(60, 60, 540, 300), text, fontsize=11, fontname="helv")
    return doc, page


def _text_image(text: str, size: tuple[int, int] = (1600, 300)) -> bytes:
    """PNG with large black text (for image based cases)."""
    image = Image.new("L", size, 255)
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 90)
    except OSError:
        font = ImageFont.load_default(size=90)
    draw.text((30, 90), text, fill=0, font=font)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------- cases

def actual_text(path: Path) -> Path:
    """Tagged PDF: /ActualText of a marked-content sequence repeats the name."""
    doc, page = _base()
    xref = doc.get_new_xref()
    stream = (b"/Span <</ActualText (Herr Max Mustermann)>> BDC BT /helv 9 Tf 60 700 Td "
              b"(Unterschrift siehe Anlage) Tj ET EMC")
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, stream)
    refs = " ".join(f"{x} 0 R" for x in [*page.get_contents(), xref])
    doc.xref_set_key(page.xref, "Contents", f"[{refs}]")
    doc.save(path)
    return path


def clipped_image(path: Path) -> Path:
    """Large image whose right half (with the name) lies outside the page."""
    doc, page = _base()
    rect = pymupdf.Rect(60, 400, 60 + 2 * 480, 400 + 180)  # twice as wide as visible
    page.insert_image(rect, stream=_text_image(f"Kontoinhaber {SECRET}", (3200, 600)))
    doc.save(path)
    return path


def white_text(path: Path) -> Path:
    doc, page = _base()
    page.insert_text((60, 500), f"Notiz: {SECRET} anrufen", fontsize=11, color=(1, 1, 1))
    doc.save(path)
    return path


def tiny_text(path: Path) -> Path:
    doc, page = _base()
    page.insert_text((60, 500), f"Herr {SECRET}", fontsize=0.6)
    doc.save(path)
    return path


def text_under_image(path: Path) -> Path:
    doc, page = _base()
    page.insert_text((80, 520), f"Herr Max {SECRET} Lindenstraße 12", fontsize=12)
    page.insert_image(pymupdf.Rect(60, 450, 560, 650), stream=_text_image("Stempel", (1000, 400)))
    doc.save(path)
    return path


def invisible_text(path: Path) -> Path:
    """Render mode 3 (invisible) – like an OCR layer."""
    doc, page = _base()
    page.insert_text((60, 500), f"Herr Max {SECRET}", fontsize=11, render_mode=3)
    doc.save(path)
    return path


def outside_page(path: Path) -> Path:
    doc, page = _base()
    page.insert_text((700, 900), f"Herr Max {SECRET}", fontsize=11)  # beyond the page
    doc.save(path)
    return path


def soft_hyphen(path: Path) -> Path:
    """Name split by a soft hyphen: 'Muster\\u00admann'."""
    doc, page = _base()
    page.insert_text((60, 500), "Zeuge: Herr Muster­mann", fontsize=11)
    doc.save(path)
    return path


def form_xobject(path: Path) -> Path:
    """Text inside a Form XObject (embedded page)."""
    doc, page = _base()
    other = pymupdf.open()
    opage = other.new_page(width=300, height=100)
    opage.insert_text((10, 50), f"Herr Max {SECRET}", fontsize=14)
    page.show_pdf_page(pymupdf.Rect(60, 450, 360, 550), other, 0)
    doc.save(path)
    return path


def annotations_and_forms(path: Path) -> Path:
    doc, page = _base()
    page.add_text_annot((500, 50), f"Rückruf {SECRET}")
    page.add_freetext_annot(pymupdf.Rect(60, 600, 300, 640), f"Notiz {SECRET}")
    widget = pymupdf.Widget()
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.field_name = "Name"
    widget.field_value = f"Max {SECRET}"
    widget.rect = pymupdf.Rect(60, 680, 300, 700)
    page.add_widget(widget)
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(60, 720, 200, 735),
                      "uri": f"mailto:max.{SECRET.lower()}@example.org"})
    doc.save(path)
    return path


def document_level(path: Path) -> Path:
    """Metadata, bookmarks, attachment, JavaScript, page labels, hidden layer."""
    doc, page = _base()
    doc.set_metadata({"author": f"Max {SECRET}", "title": f"Akte {SECRET}",
                      "subject": SECRET, "keywords": SECRET})
    doc.set_xml_metadata(f"<x:xmpmeta xmlns:x='adobe:ns:meta/'>{SECRET}</x:xmpmeta>")
    doc.set_toc([[1, f"Schreiben an {SECRET}", 1]])
    doc.embfile_add("notiz.txt", f"Telefonnotiz {SECRET}".encode(), filename="notiz.txt")
    doc.set_page_labels([{"startpage": 0, "prefix": f"{SECRET}-", "style": "D"}])
    xref = doc.add_ocg(f"Ebene {SECRET}", on=False)
    page.insert_text((60, 500), f"Verborgen: Herr {SECRET}", fontsize=11, oc=xref)
    doc.save(path)
    doc.close()
    # JavaScript open action (added via low-level API).
    doc = pymupdf.open(path)
    js = doc.get_new_xref()
    doc.update_object(js, f"<</S/JavaScript/JS(app.alert('{SECRET}'))>>")
    doc.xref_set_key(doc.pdf_catalog(), "OpenAction", f"{js} 0 R")
    doc.save(path, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    return path


def incremental_update(path: Path) -> Path:
    """Old file version (with the name) is still inside the PDF."""
    doc, page = _base()
    page.insert_text((60, 500), f"Entwurf für Herrn {SECRET}", fontsize=11)
    doc.save(path)
    doc.close()
    doc = pymupdf.open(path)
    page = doc[0]
    page.add_redact_annot(page.search_for("Entwurf für Herrn")[0])
    page.apply_redactions()
    doc.save(path, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    return path


CASES = [actual_text, clipped_image, white_text, tiny_text, text_under_image, invisible_text,
         outside_page, soft_hyphen, form_xobject, annotations_and_forms, document_level,
         incremental_update]


# -------------------------------------------------------------------- attacker

def recover(path: Path, term: str = SECRET) -> list[str]:
    """Try every technique an attacker could use; return those that succeed."""
    from doc_anonymizer import ocr

    needle = term.casefold()
    found: list[str] = []
    raw = path.read_bytes()
    variants = [term.encode(), term.encode("utf-16-be"), term.encode().hex().encode(),
                term.encode("utf-16-be").hex().encode()]
    if any(v.lower() in raw.lower() for v in variants):
        found.append("Rohbytes der Datei")
    doc = pymupdf.open(path)
    for xref in range(1, doc.xref_length()):
        try:
            data = doc.xref_object(xref, compressed=False).encode("latin-1", "replace")
            if doc.xref_is_stream(xref):
                data += doc.xref_stream(xref) or b""
        except Exception:  # noqa: BLE001
            continue
        if any(v.lower() in data.lower() for v in variants):
            found.append(f"PDF-Objekt {xref} (entpackt)")
            break
    flags = pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES
    mupdf = " ".join(p.get_text(clip=pymupdf.INFINITE_RECT(), flags=flags) for p in doc)
    if needle in mupdf.replace("­", "").casefold():
        found.append("MuPDF-Text")
    try:
        from pypdf import PdfReader

        if needle in " ".join(p.extract_text() or "" for p in PdfReader(str(path)).pages).casefold():
            found.append("pypdf")
    except Exception:  # noqa: BLE001
        pass
    try:
        from pdfminer.high_level import extract_text

        if needle in extract_text(str(path)).replace("­", "").casefold():
            found.append("pdfminer")
    except Exception:  # noqa: BLE001
        pass
    if shutil.which("pdftotext"):
        out = subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True).stdout
        if needle in out.casefold():
            found.append("pdftotext")
    if ocr.is_available():
        for page in doc:
            pix = page.get_pixmap(dpi=200, colorspace=pymupdf.csGRAY)
            img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
            if needle in " ".join(w.text for w in ocr.ocr_words(img, 1.0)).casefold():
                found.append(f"OCR Seite {page.number + 1}")
            for info in page.get_images(full=True):
                pix = pymupdf.Pixmap(doc, info[0])
                if pix.n - pix.alpha != 1:
                    pix = pymupdf.Pixmap(pymupdf.csGRAY, pix)
                img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
                if needle in " ".join(w.text for w in ocr.ocr_words(img, 1.0)).casefold():
                    found.append(f"OCR eingebettetes Bild {info[0]}")
    doc.close()
    return sorted(set(re.sub(r"\d+", "#", f) for f in found))
