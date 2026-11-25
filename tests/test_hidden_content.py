"""What you see is what you get: nothing invisible reaches the result.

The cases in ``test_recovery.py`` hide a name the program *knows*. Here the
hidden content is something no detector can know (a made-up word) – it must
disappear all the same, because the lawyer checking the pages cannot see it:
text under a black bar, white or invisible writing, text outside the page,
camera data inside photos, cropped-away parts of images.
"""

import io

import pymupdf
import pytest
from PIL import Image

from doc_anonymizer.detect.pipeline import Detector
from doc_anonymizer.extract import analyse_document, visible_rects
from doc_anonymizer.process import anonymise_document
from doc_anonymizer.registry import CaseRegistry

from . import attack_docs
from .attack_docs import _base, _text_image
from .conftest import needs_tesseract

#: A word no name list and no language model knows.
UNKNOWN = "Quittenbaumzwerg"


def _run(source, tmp_path, settings):
    pages = analyse_document(source, settings)
    registry = CaseRegistry([pages], settings)
    registry.build([Detector(settings).detect_document(pages)])
    target = tmp_path / "ergebnis.pdf"
    result = anonymise_document(source, target, 0, pages, registry, settings)
    return pages, target, result


# ------------------------------------------------------------------ documents

def covered_by_bar(path):
    """Somebody "redacted" a line by drawing a black bar over it."""
    doc, page = _base()
    page.insert_text((60, 500), f"Vermerk: {UNKNOWN} intern klären", fontsize=11)
    page.draw_rect(pymupdf.Rect(55, 488, 400, 505), color=None, fill=(0, 0, 0), overlay=True)
    doc.save(path)
    return path


def white_writing(path):
    doc, page = _base()
    page.insert_text((60, 500), f"Notiz {UNKNOWN} bitte ignorieren", fontsize=11, color=(1, 1, 1))
    doc.save(path)
    return path


def invisible_writing(path):
    doc, page = _base()
    page.insert_text((60, 500), f"Anweisung {UNKNOWN} beachten", fontsize=11, render_mode=3)
    doc.save(path)
    return path


def outside_the_page(path):
    doc, page = _base()
    page.insert_text((700, 900), f"Ablage {UNKNOWN}", fontsize=11)
    doc.save(path)
    return path


def _photo(with_text: str = "") -> bytes:
    """JPEG "from a phone": camera data name the owner and the place."""
    image = Image.new("RGB", (900, 640), (120, 150, 190))
    if with_text:
        image.paste(Image.open(io.BytesIO(_text_image(with_text, (900, 200)))).convert("RGB"),
                    (0, 220))
    exif = Image.Exif()
    exif[0x013B] = f"Fotograf {UNKNOWN}"  # Artist
    exif[0x010F] = f"Kamera-{UNKNOWN}"  # Make
    exif[0x9286] = f"Aufgenommen bei {UNKNOWN}".encode()  # UserComment
    buf = io.BytesIO()
    image.save(buf, "JPEG", exif=exif, quality=85)
    return buf.getvalue()


def photo_with_camera_data(path):
    """Enclosure page with a large photo and a small one."""
    doc, page = _base()
    page.insert_image(pymupdf.Rect(60, 330, 510, 650), stream=_photo())
    page.insert_image(pymupdf.Rect(60, 680, 140, 737), stream=_photo())
    doc.save(path)
    return path


def cropped_photo(path):
    """Only the left third of a screenshot is on the page – the rest is cut off."""
    doc, page = _base()
    rect = pymupdf.Rect(60, 400, 60 + 3 * 480, 400 + 100)
    page.insert_image(rect, stream=_text_image(f"Chat mit Erika      {UNKNOWN}", (4800, 300)))
    doc.save(path)
    return path


CASES = [covered_by_bar, white_writing, invisible_writing, outside_the_page,
         photo_with_camera_data, cropped_photo]


# ----------------------------------------------------------------------- tests

@needs_tesseract
@pytest.mark.parametrize("make", CASES, ids=lambda f: f.__name__)
def test_hidden_content_is_not_carried_over(make, tmp_path, settings):
    source = make(tmp_path / f"{make.__name__}.pdf")
    assert attack_docs.recover(source, UNKNOWN), "test case does not hide anything"

    _pages, target, result = _run(source, tmp_path, settings)

    assert attack_docs.recover(target, UNKNOWN) == []
    assert UNKNOWN.casefold() not in "\n".join(result.redaction.texts).casefold()  # .txt export
    assert result.verification.passed, [c for c in result.verification.checks if not c.ok]
    with pymupdf.open(target) as pdf:
        text = " ".join(p.get_text() for p in pdf)
    assert "§ 280 Abs. 1 BGB" in text  # the legal content survives


def test_visibility_check_tells_hidden_from_visible():
    doc = pymupdf.open()
    page = doc.new_page()
    lines = {
        "Sichtbar normal": dict(fontsize=11),
        "Hellgrau klein": dict(fontsize=6, color=(0.65, 0.65, 0.65)),
        "Weiss versteckt": dict(fontsize=11, color=(1, 1, 1)),
        "Modus drei": dict(fontsize=11, render_mode=3),
        "Unter Balken": dict(fontsize=11),
        "Auf Farbe": dict(fontsize=11, color=(1, 1, 1)),
    }
    for n, (text, options) in enumerate(lines.items()):
        y = 100 + 40 * n
        if text == "Auf Farbe":
            page.draw_rect(pymupdf.Rect(60, y - 14, 300, y + 6), color=None, fill=(0.1, 0.2, 0.6))
        page.insert_text((72, y), text, **options)
        if text == "Unter Balken":
            page.draw_rect(pymupdf.Rect(60, y - 14, 300, y + 6), color=None, fill=(0, 0, 0))
    doc = pymupdf.open("pdf", doc.tobytes())
    page = doc[0]
    words = page.get_text("words")
    visible = dict(zip((w[4] for w in words), visible_rects(doc, page, [w[:4] for w in words])))
    assert visible == {"Sichtbar": True, "normal": True, "Hellgrau": True, "klein": True,
                       "Weiss": False, "versteckt": False, "Modus": False, "drei": False,
                       "Unter": False, "Balken": False, "Auf": True, "Farbe": True}


def test_ordinary_text_pages_stay_text_pages(tmp_path, settings):
    """The checks must not turn normal documents into images."""
    from .sample_docs import make_digital_pdf

    pages, target, result = _run(make_digital_pdf(tmp_path / "brief.pdf"), tmp_path, settings)
    assert not any(w.hidden for p in pages for w in p.words)
    assert result.redaction.raster_pages == 0
    assert result.verification.passed


@needs_tesseract
def test_vertical_text_is_removed_completely(tmp_path, settings):
    """Margin line written bottom-up: no first/last letters may survive."""
    doc, page = _base()
    page.insert_text((40, 700), "Sachbearbeiter Max Mustermann Durchwahl 0891234567",
                     fontsize=9, rotate=90)
    source = tmp_path / "rand.pdf"
    doc.save(source)
    _pages, target, result = _run(source, tmp_path, settings)
    assert result.verification.passed
    with pymupdf.open(target) as pdf:
        words = [w[4] for w in pdf[0].get_text("words")]
    assert "Sachbearbeiter" in words and "Durchwahl" in words
    # Remains of "Mustermann" would show up as short letter groups.
    assert not [w for w in words if w in ("Mu", "nn", "M", "n", "Mus", "ann", "Ma", "x")]
    assert attack_docs.recover(target) == []


@needs_tesseract
def test_hidden_text_layer_cannot_hide_a_visible_name(tmp_path, settings):
    """Text page with a picture that shows a name – and a hidden text layer
    on top of the picture claiming something harmless."""
    doc, page = _base()
    area = pymupdf.Rect(60, 400, 540, 490)
    page.insert_image(area, stream=_text_image("Zeugin Erika Sonnenberg", (1600, 300)))
    page.insert_text((70, 460), "Abbildung ohne Text hier", fontsize=30, render_mode=3)
    source = tmp_path / "ebene.pdf"
    doc.save(source)
    pages, target, result = _run(source, tmp_path, settings)
    assert attack_docs.recover(target, "Sonnenberg") == []
    assert "Abbildung ohne" not in "\n".join(result.redaction.texts)
    assert result.verification.passed
