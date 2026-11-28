"""Complete pipeline on generated PDFs: extraction -> redaction -> verification."""

import pymupdf
import pytest

from doc_anonymizer.detect.pipeline import Detector
from doc_anonymizer.extract import analyse_document
from doc_anonymizer.process import anonymise_document
from doc_anonymizer.registry import CaseRegistry

from .conftest import needs_tesseract
from .sample_docs import make_digital_pdf, make_scanned_pdf

ORIGINALS = ["Schröder", "Maximilian", "Kowalski", "Jansen", "Hoffmann", "Yilmaz",
             "Mustermann", "Lindenstraße", "DE89 3704", "m.schmidt@", "12 O 345/21",
             "03.04.1978", "86 095 742 719", "4711-0815"]


def run(path, settings, tmp_path):
    pages = analyse_document(path, settings)
    registry = CaseRegistry([pages], settings)
    registry.build([Detector(settings).detect_document(pages)])
    target = tmp_path / "ergebnis.pdf"
    result = anonymise_document(path, target, 0, pages, registry, settings)
    return target, result


def full_text(path) -> str:
    with pymupdf.open(path) as pdf:
        return "\n".join(p.get_text(clip=pymupdf.INFINITE_RECT()) for p in pdf)


def test_digital_pdf(tmp_path, settings):
    target, result = run(make_digital_pdf(tmp_path / "klage.pdf"), settings, tmp_path)
    text = full_text(target)
    for original in ORIGINALS:
        assert original not in text, original
    assert "M. S." in text and "[IBAN 1]" in text
    # legal content stays readable
    assert "§ 280 Abs. 1 BGB" in text and "VI ZR 123/19" in text
    assert "Landgericht München I" in text
    assert result.verification.passed, [c for c in result.verification.checks if not c.ok]
    with pymupdf.open(target) as pdf:
        assert not any(pdf.metadata.get(k) for k in ("author", "title"))
        assert not pdf.get_toc()
        assert all(not list(p.annots()) for p in pdf)
    # natural reading order in the plain-text export
    assert "des Herrn M. S., geb. am [Geburtsdatum 1]," in result.redaction.texts[0]


@needs_tesseract
def test_scanned_pdf(tmp_path, settings):
    target, result = run(make_scanned_pdf(tmp_path / "scan.pdf"), settings, tmp_path)
    assert result.redaction.raster_pages == 2
    text = full_text(target)
    for original in ORIGINALS:
        assert original not in text, original
    assert result.verification.passed
    assert result.verification.ocr_pages_checked == 2


def test_maximum_security_mode(tmp_path, settings):
    settings.security_mode = "maximum"
    settings.deep_verification = False
    target, result = run(make_digital_pdf(tmp_path / "klage.pdf"), settings, tmp_path)
    assert result.redaction.raster_pages == 2
    with pymupdf.open(target) as pdf:
        assert all(p.get_images() for p in pdf)  # pages are images now
    assert "Schröder" not in full_text(target)


def test_password_protected_pdf_is_rejected(tmp_path, settings):
    from doc_anonymizer.extract import PdfOpenError

    path = make_digital_pdf(tmp_path / "klage.pdf")
    with pymupdf.open(path) as pdf:
        pdf.save(tmp_path / "geschuetzt.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256,
                 user_pw="geheim", owner_pw="geheim")
    with pytest.raises(PdfOpenError):
        analyse_document(tmp_path / "geschuetzt.pdf", settings)
