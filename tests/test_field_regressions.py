"""Regressions from the field test on a real Windows PC (fictitious data)."""

import pymupdf
import pytest

from doc_anonymizer.detect import patterns
from doc_anonymizer.detect.pipeline import Detector
from doc_anonymizer.extract import analyse_document
from doc_anonymizer.models import Category
from doc_anonymizer.process import anonymise_document
from doc_anonymizer.registry import CaseRegistry
from doc_anonymizer.verify import iban_fragments

from .conftest import needs_tesseract
from .sample_docs import make_bank_letter_scan

IBAN_PARTS = ["DE02", "DEO2", "1203", "2020"]


def _run(tmp_path, settings):
    path = make_bank_letter_scan(tmp_path / "brief.pdf")
    pages = analyse_document(path, settings)
    registry = CaseRegistry([pages], settings)
    registry.build([Detector(settings).detect_document(pages)])
    target = tmp_path / "ergebnis.pdf"
    result = anonymise_document(path, target, 0, pages, registry, settings)
    with pymupdf.open(target) as pdf:
        text = pdf[0].get_text()
    return registry, result, text


@needs_tesseract
def test_scanned_iban_completely_redacted(tmp_path, settings):
    registry, result, text = _run(tmp_path, settings)
    ibans = [e for e in registry.entities.values() if e.category is Category.IBAN and e.count]
    assert len(ibans) == 1 and patterns.iban_valid(ibans[0].original)
    for part in IBAN_PARTS:
        assert part not in text, part
    assert "[IBAN 1]" in text and "Telefon" not in text
    assert result.verification.passed
    # b) the e-mail ends before the sentence period, "Mit" stays readable
    emails = [e.original for e in registry.entities.values() if e.category is Category.EMAIL]
    assert emails == ["s.kretschmer@baeckerei-lindner-test.de"]
    assert "Mit freundlichen Grüßen" in text
    # c) "Personal-Nr." is a field label, not a name
    assert "Personal-Nr" in text
    assert not any("Personal" in e.original for e in registry.entities.values()
                   if e.category in (Category.OTHER_NAME, Category.ORG, Category.PERSON))


@needs_tesseract
def test_verification_catches_half_redacted_iban(tmp_path, settings, monkeypatch):
    """Safety net: even if detection misses the IBAN (only the phone rule
    fires, as in the field test), the check must not pass with a visible rest."""
    monkeypatch.setattr(patterns, "find_iban", lambda text: iter(()))
    monkeypatch.setattr(patterns, "RULES",
                        [r for r in patterns.RULES if not r.name.startswith("iban")])
    registry, result, text = _run(tmp_path, settings)
    assert result.repairs >= 1  # the fragment was found and covered
    for part in ("DE02", "DEO2", "1203"):
        assert part not in text, part
    assert not iban_fragments(" ".join(text.split()))


@pytest.mark.parametrize("text, expected", [
    ("IBAN DEO2 1203 0000 0000 2020 51.", "DEO2 1203 0000 0000 2020 51"),   # OCR: O for 0
    ("IBAN DE02 12O3 0000 0000 2020 51", "DE02 12O3 0000 0000 2020 51"),
    ("IBAN DE03 1203 0000 0000 2020 51 x", "DE03 1203 0000 0000 2020 51"),  # misread digit
])
def test_iban_wins_over_phone(text, expected):
    found = patterns.detect(text, 0, set(Category))
    ibans = {d.text for d in found if d.category is Category.IBAN}
    assert expected in ibans
    # the IBAN detection covers every phone/card candidate inside it
    best = max(d.score for d in found if d.category is Category.IBAN)
    assert all(d.score < best for d in found if d.category is not Category.IBAN)


@pytest.mark.parametrize("text, expected", [
    ("unter s.kretschmer@baeckerei-lindner-test.de.\nMit freundlichen Grüßen",
     "s.kretschmer@baeckerei-lindner-test.de"),
    ("an max@web.de. und dann", "max@web.de"),
    ("Ende. max.muster @ web. de schreiben", "max.muster @ web. de"),
    ("bekir.weihmann@ hotmail.de", "bekir.weihmann@ hotmail.de"),
])
def test_email_stops_at_sentence_end(text, expected):
    emails = {d.text for d in patterns.detect(text, 0, {Category.EMAIL})}
    assert expected in emails and all(e in expected for e in emails)


@pytest.mark.parametrize("text, fragments", [
    ("IBAN DEO2 1203 [Telefon 3].", ["DEO2 1203"]),
    ("Konto DE02 1203 0000 0000 2020 51 bei", ["DE02 1203 0000 0000 2020 51"]),
    ("IBAN [IBAN 1].", []),
    ("Az. DE 12 vom 1. 2. 2020", []),
    ("Die Firma DE12 3456 ist", []),
])
def test_iban_fragment_check(text, fragments):
    assert iban_fragments(text) == fragments
