"""Detection and removal of handwritten signatures, handwriting and stamps."""

import numpy as np
import pymupdf
from PIL import Image

from doc_anonymizer import learning, signatures
from doc_anonymizer.detect.pipeline import Detector
from doc_anonymizer.extract import analyse_document
from doc_anonymizer.process import anonymise_document
from doc_anonymizer.registry import CaseRegistry

from .conftest import needs_tesseract
from .signature_docs import make_signed_scan


def _rects(page_info) -> list[pymupdf.Rect]:
    return [pymupdf.Rect(*b[:4]) for b in page_info.signature_boxes]


def _covered(truth: pymupdf.Rect, found: list[pymupdf.Rect]) -> bool:
    """At least 80 % of the signature's ink area lies inside detections."""
    return sum(abs(truth & r) for r in found) >= 0.8 * abs(truth)


def _blue_pixels(path, page_number: int, rect: pymupdf.Rect) -> int:
    """Number of clearly blue (ballpoint ink) pixels in an area of a page."""
    with pymupdf.open(path) as pdf:
        pix = pdf[page_number].get_pixmap(dpi=100, clip=rect)
    rgb = np.asarray(Image.frombytes("RGB", (pix.width, pix.height), pix.samples), dtype=int)
    return int(((rgb[..., 2] - rgb[..., 0] > 50) & (rgb[..., 2] > 90)).sum())


@needs_tesseract
def test_signatures_found_on_scans(tmp_path, settings):
    found = total = false_alarms = 0
    for seed, quality in ((100, "gut"), (101, "mittel"), (102, "schlecht"), (103, "gut")):
        path, _, truth = make_signed_scan(tmp_path / f"s{seed}.pdf", seed, quality,
                                          color=seed % 2 == 0)
        pages = analyse_document(path, settings)
        for info, rects in zip(pages, truth):
            boxes = _rects(info)
            total += len(rects)
            found += sum(_covered(r, boxes) for r in rects)
            false_alarms += sum(1 for b in boxes if not any(abs(b & r) for r in rects))
    assert found >= 0.85 * total, (found, total)
    assert false_alarms <= 1


@needs_tesseract
def test_signature_is_removed_from_output(tmp_path, settings):
    path, _, truth = make_signed_scan(tmp_path / "brief.pdf", 100, "gut", color=True,
                                      ink=(25, 45, 160))
    pages = analyse_document(path, settings)
    registry = CaseRegistry([pages], settings)
    registry.build([Detector(settings).detect_document(pages)])
    assert any(b.kind == "unterschrift" for b in registry.manual_boxes)
    # OCR "letters" read from the signature are not offered for decision.
    from doc_anonymizer.registry import _inside_any

    for entity in registry.entities.values():
        if entity.uncertain:
            mentions = [m for m in registry.mentions if m.entity_id == entity.id]
            assert any(not _inside_any(pages[m.page].words[i], pages[m.page].signature_boxes)
                       for m in mentions for i in m.word_indices), entity.original
    target = tmp_path / "ergebnis.pdf"
    result = anonymise_document(path, target, 0, pages, registry, settings)
    assert result.verification.passed
    for page_number, rects in enumerate(truth):
        for rect in rects:
            assert _blue_pixels(path, page_number, rect) > 20  # ink was there ...
            assert _blue_pixels(target, page_number, rect) <= 2  # ... and is gone


def test_unsigned_scan_has_no_signatures(tmp_path, settings):
    """Plain printed text (also bold headings, tables) is no handwriting."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(60, 60, 540, 400),
                        "Landgericht Musterstadt\n\nURTEIL\n\n" + "Im Namen des Volkes. " * 30,
                        fontsize=11, fontname="helv")
    page.insert_text((60, 450), "ÜBERSCHRIFT IN FETTSCHRIFT", fontsize=20, fontname="hebo")
    for y in range(480, 600, 20):  # table
        page.draw_line((60, y), (540, y))
    for x in (60, 300, 540):
        page.draw_line((x, 480), (x, 580))
    pix = page.get_pixmap(dpi=200)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    from doc_anonymizer import ocr

    words = ocr.ocr_words(image.convert("L"), 72 / 200) if ocr.is_available() else []
    assert signatures.detect_in_image(image, page.rect.width, page.rect.height, words) == []


def test_vector_signature_on_digital_page(tmp_path, settings):
    """Signature pads and "draw signature" tools produce curves, not images."""
    path = tmp_path / "vertrag.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Vertrag zwischen den Parteien über die Lieferung von Waren.",
                     fontsize=11)
    page.insert_text((72, 300), "Mit freundlichen Grüßen", fontsize=11)
    shape = page.new_shape()
    x = 80.0
    for i in range(14):  # a looping pen stroke
        shape.draw_bezier((x, 340), (x + 6, 310 - i), (x + 12, 360 + i), (x + 18, 330))
        x += 11
    shape.finish(color=(0.1, 0.2, 0.6), width=1.2)
    shape.commit()
    doc.save(path)

    pages = analyse_document(path, settings)
    boxes = _rects(pages[0])
    assert len(boxes) == 1 and boxes[0].contains(pymupdf.Rect(80, 320, 220, 350))

    registry = CaseRegistry([pages], settings)
    registry.build([Detector(settings).detect_document(pages)])
    target = tmp_path / "ergebnis.pdf"
    anonymise_document(path, target, 0, pages, registry, settings)
    with pymupdf.open(target) as pdf:
        curves = [d for d in pdf[0].get_drawings()
                  if any(item[0] == "c" for item in d["items"])]
        assert curves == []  # the signature paths are gone
        assert "Lieferung von Waren" in pdf[0].get_text()


def test_model_learns_from_user_clicks():
    # Users keep (0) black, cue-less blobs and confirm (1) coloured ones.
    samples = []
    for i in range(30):
        samples.append(([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.9], 1))
        samples.append(([0.0, 1.0, 0.0, i % 2, 1.0, 1.0, 0.9], 0))
    assert learning.record_signatures(samples)
    model = signatures.load_model()
    assert model.learned
    assert model.score([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.9]) > signatures.THRESHOLD
    assert model.score([0.0, 1.0, 0.0, 1.0, 1.0, 1.0, 0.9]) < signatures.THRESHOLD
    assert learning.statistics()["unterschrift_entscheidungen"] == 60
    learning.clear()
    assert not signatures.load_model().learned
