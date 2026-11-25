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


def test_compound_split_by_ocr_is_not_a_surname():
    """Image check: "Steuer-" + "ID" (cut by the OCR) is the label "Steuer-ID",
    not the surname "Steuer" – but a name followed by a real dash still counts."""
    from doc_anonymizer.models import Category, Word
    from doc_anonymizer.verify import Haystack, _contains, _ocr_text

    def line(*parts):  # (text, x0, x1)
        return [Word(x0, 100, x1, 112, text, "ocr", 7) for text, x0, x1 in parts]

    split = line(("Steuer-", 100, 142), ("ID:", 143, 160), ("19", 170, 182))
    assert _ocr_text(split) == ["Steuer-ID:", "19"]
    assert not _contains(Haystack(" ".join(_ocr_text(split))), "Steuer", False, Category.PERSON)
    other = line(("Steuer", 100, 136), ("-ID:", 137, 160))
    assert _ocr_text(other) == ["Steuer-ID:"]

    dash = line(("Herr", 60, 84), ("Steuer", 90, 126), ("-", 132, 136), ("Kläger", 142, 178))
    assert _ocr_text(dash) == ["Herr", "Steuer", "-", "Kläger"]
    assert _contains(Haystack(" ".join(_ocr_text(dash))), "Steuer", False, Category.PERSON)
    spaced = line(("Steuer-", 100, 142), ("Kläger", 150, 186))  # real gap: two words
    assert _ocr_text(spaced) == ["Steuer-", "Kläger"]


def test_name_inside_a_hyphenated_compound(tmp_path, settings):
    """"das Mustermann-Gutachten" contains the client's name; "Steuer-ID" and
    "Max-Planck-Institut" do not."""
    import pymupdf

    from doc_anonymizer.detect.pipeline import Detector
    from doc_anonymizer.extract import analyse_document
    from doc_anonymizer.models import Category
    from doc_anonymizer.process import anonymise_document
    from doc_anonymizer.registry import CaseRegistry
    from doc_anonymizer.verify import Haystack, _contains

    doc = pymupdf.open()
    doc.new_page().insert_textbox(
        pymupdf.Rect(60, 60, 540, 400),
        "In dem Rechtsstreit erklärt Herr Max Mustermann, wohnhaft Lindenstraße 12, "
        "80331 München, dass die Forderung unbegründet ist.\n\n"
        "Das Mustermann-Gutachten vom 3. Mai 2024 liegt bei, ebenso die Stellungnahme des "
        "Max-Planck-Instituts. Die Steuer-ID ist unbekannt.", fontsize=11)
    source = tmp_path / "k.pdf"
    doc.save(source)
    pages = analyse_document(source, settings)
    registry = CaseRegistry([pages], settings)
    registry.build([Detector(settings).detect_document(pages)])
    result = anonymise_document(source, tmp_path / "o.pdf", 0, pages, registry, settings)
    with pymupdf.open(tmp_path / "o.pdf") as pdf:
        text = " ".join(pdf[0].get_text().split())
    assert "Mustermann" not in text and result.verification.passed
    assert result.repairs == 0  # found by the detection, not only by the safety net
    assert "Max-Planck-Instituts" in text and "Steuer-ID" in text and "vom 3. Mai 2024" in text

    # The verification on its own counts the compound as a leak, too.
    hay = Haystack("Das Mustermann-Gutachten und die Steuer-ID sowie das Max-Planck-Institut")
    assert _contains(hay, "Mustermann", False, Category.PERSON)
    assert not _contains(hay, "Steuer", False, Category.PERSON)
    assert not _contains(hay, "Max", False, Category.PERSON)


def test_known_name_inside_a_finding_the_user_kept(tmp_path, settings):
    """A street named after somebody with the client's surname was left
    readable (uncertain finding, not confirmed). The verification must not
    just report the surname – the automatic repair has to remove it."""
    import pymupdf

    from doc_anonymizer.detect.pipeline import Detector
    from doc_anonymizer.extract import analyse_document
    from doc_anonymizer.models import Category
    from doc_anonymizer.process import anonymise_document
    from doc_anonymizer.registry import CaseRegistry

    doc = pymupdf.open()
    doc.new_page().insert_textbox(
        pymupdf.Rect(60, 60, 540, 400),
        "In dem Rechtsstreit erklärt Herr Amir Sauer, dass die Forderung unbegründet ist.\n\n"
        "Die Beklagte ist wohnhaft Brit-Sauer-Platz 2075 und bestreitet den Anspruch aus "
        "§ 280 Abs. 1 BGB.", fontsize=11)
    source = tmp_path / "k.pdf"
    doc.save(source)
    pages = analyse_document(source, settings)
    registry = CaseRegistry([pages], settings)
    registry.build([Detector(settings).detect_document(pages)])
    # The state found in a large test run: the word belongs to a finding
    # that is switched off.
    index = next(i for i, w in enumerate(pages[0].words) if "Sauer-Platz" in w.text)
    registry.mentions = [m for m in registry.mentions if index not in m.word_indices]
    registry._recompute_covered()
    street = registry.add_term("Brit-Sauer-Platz", Category.LOCATION)
    street.enabled = False
    assert any(index in m.word_indices and m.entity_id == street.id for m in registry.mentions)

    result = anonymise_document(source, tmp_path / "o.pdf", 0, pages, registry, settings)
    with pymupdf.open(tmp_path / "o.pdf") as pdf:
        text = " ".join(pdf[0].get_text().split())
    assert "Sauer" not in text
    assert result.verification.passed and result.repairs >= 1
    assert "§ 280 Abs. 1 BGB" in text


def test_font_name_is_not_a_recovered_first_name(tmp_path, settings):
    """A client called Roman and a PDF set in "Times-Roman": the font name in
    the file is not a leak – and no reason to re-build every page as an image."""
    import pymupdf

    from doc_anonymizer.detect.pipeline import Detector
    from doc_anonymizer.extract import analyse_document
    from doc_anonymizer.process import anonymise_document
    from doc_anonymizer.registry import CaseRegistry

    doc = pymupdf.open()
    doc.new_page().insert_textbox(
        pymupdf.Rect(60, 60, 540, 400),
        "In dem Rechtsstreit erklärt Herr Roman Mustermann, wohnhaft Lindenstraße 12, "
        "80331 München, dass die Forderung aus § 280 Abs. 1 BGB unbegründet ist.",
        fontsize=11, fontname="tiro")
    source = tmp_path / "k.pdf"
    doc.save(source)
    assert b"Times-Roman" in source.read_bytes()
    pages = analyse_document(source, settings)
    registry = CaseRegistry([pages], settings)
    registry.build([Detector(settings).detect_document(pages)])
    assert any(e.given == ["Roman"] for e in registry.entities.values())
    result = anonymise_document(source, tmp_path / "o.pdf", 0, pages, registry, settings)
    assert result.verification.passed
    assert result.repairs == 0 and result.redaction.raster_pages == 0
    with pymupdf.open(tmp_path / "o.pdf") as pdf:
        assert "Roman" not in pdf[0].get_text()


def test_first_name_torn_off_an_email_address_by_ocr():
    """Poor scan: "samuel.lindner@…" is read as "samuel" + ".lindner@…". The
    lower-case first name in front of the address must go as well – and the
    verification must notice if it did not."""
    from doc_anonymizer.config import Settings
    from doc_anonymizer.detect.pipeline import Detector
    from doc_anonymizer.models import Category, PageInfo, Word
    from doc_anonymizer.registry import CaseRegistry
    from doc_anonymizer.verify import Haystack, _contains

    lines = ["Mietvertrag zwischen Herrn Samuel Lindner und der Vermieterin.",
             "Kontakt Mieter: samuel .lindner@gmx-beispiel.de, mobil 0151 23456789",
             "Der Mieter wohnt seit lange im Haus."]
    words = [Word(40 + 70 * n, 100 + 20 * i, 105 + 70 * n, 112 + 20 * i, text, "ocr", i, 90)
             for i, line in enumerate(lines) for n, text in enumerate(line.split())]
    pages = [PageInfo(0, 595, 842, words, kind="scan")]
    settings = Settings(use_gliner=False)
    registry = CaseRegistry([pages], settings)
    registry.build([Detector(settings).detect_document(pages)])
    redacted = {words[i].text for m in registry.mentions for i in m.word_indices}
    assert "samuel" in redacted and "Samuel" in redacted

    assert _contains(Haystack("Kontakt Mieter: samuel [E-Mail 10], mobil"), "Samuel", False,
                     Category.PERSON)
    assert _contains(Haystack("An: [E-Mail 4] heidrun"), "Heidrun", False, Category.PERSON)
    # An ordinary lower-case word elsewhere is still no name.
    assert not _contains(Haystack("seit lange im Haus, siehe [E-Mail 1]"), "Lange", False,
                         Category.PERSON)


def test_health_insurance_number_read_with_a_gap():
    from doc_anonymizer.detect.patterns import detect
    from doc_anonymizer.models import Category

    found = {d.text for d in detect("Versichertennumimer: B788 104556 :", 0, set(Category))}
    assert "B788 104556" in found
    assert not detect("Abschnitt B788 104556 der Liste", 0, {Category.SOCIAL_SECURITY})
