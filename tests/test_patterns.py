"""Rule based detection of structured data."""

import pytest

from doc_anonymizer.detect.patterns import detect, iban_valid, luhn_valid, tax_id_valid
from doc_anonymizer.models import Category

ALL = set(Category)


def found(text: str) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for d in detect(text, 0, ALL):
        result.setdefault(d.category.value, set()).add(d.text)
    return result


def test_validators():
    assert iban_valid("DE89 3704 0044 0532 0130 00")
    assert not iban_valid("DE89 3704 0044 0532 0130 01")
    assert luhn_valid("4111 1111 1111 1111")
    assert tax_id_valid("86095742719")
    assert not tax_id_valid("86095742718")


def test_bank_and_contact():
    f = found("IBAN DE89 3704 0044 0532 0130 00 BIC: MUST DE FF\n"
              "Tel.: 07131. 555 123 4, Fax +49 (0)89 1234 5679, mobil 0171 2345678\n"
              "E-Mail: m.schmidt@kanzlei-schmidt.de, www.kanzlei-schmidt.de")
    assert "DE89 3704 0044 0532 0130 00" in f["IBAN"]
    assert "MUST DE FF" in f["BIC"]
    assert {"07131. 555 123 4", "+49 (0)89 1234 5679", "0171 2345678"} <= f["PHONE"]
    assert "m.schmidt@kanzlei-schmidt.de" in f["EMAIL"]
    assert "www.kanzlei-schmidt.de" in f["URL"]


def test_iban_across_line_break():
    f = found("auf das Konto IBAN DE89 3704 0044\n0532 0130 00, zu zahlen")
    assert "DE89 3704 0044\n0532 0130 00" in f["IBAN"]


def test_addresses():
    f = found("wohnhaft Berliner Straße 5a, 10115 Berlin; Am Lindenbaum 3; Hauptstr. 3-5;\n"
              "im Hause Tschentschergasse vt; Neue Linden-\nstraße 12")
    assert {"Berliner Straße 5a", "10115 Berlin", "Am Lindenbaum 3", "Hauptstr. 3-5",
            "Tschentschergasse", "Neue Linden-\nstraße 12"} <= f["ADDRESS"]


def test_numbers_and_dates():
    f = found("Steuer-ID des Klägers lautet 86 095 742 719. Az.: 12 O 345/21\n"
              "AKTENZEICHEN\nK 42 / 2025 / ab\nKundennummer: 4711-0815, geb. am 03.04.1978,"
              " gob am 14.01 2002, SV-Nummer 12 150378 M 123, HRB 12345 B, DE 123456789")
    assert "86 095 742 719" in f["TAX_ID"]
    assert {"12 O 345/21", "K 42 / 2025 / ab"} <= f["CASE_NUMBER"]
    assert "4711-0815" in f["REFERENCE_NUMBER"]
    assert {"03.04.1978", "14.01 2002"} <= f["BIRTH_DATE"]
    assert "12 150378 M 123" in f["SOCIAL_SECURITY"]
    assert {"HRB 12345 B", "DE 123456789"} <= f["REGISTER_NUMBER"]


def test_legal_content_stays():
    text = ("Der Anspruch folgt aus § 823 Abs. 1 BGB (vgl. BGH, Urteil vom 12.03.2020 – "
            "VI ZR 123/19, Rn. 15). Im Jahr 2020 zahlte er 12345 Euro. GOÄ-Nr. 50. "
            "Die Beklagte GmbH zahlte. Er befuhr die Bundesstraße.")
    assert found(text) == {}


def test_ocr_variants():
    f = found("Steuer-ıD: 23-539 572 762\nTel.: 089- -358675\nGeburtadatum: 11.04.1950\n"
              "SV-Nummer: 36 266759 s 152\nE-Mail: bekir.weihmann@ hotmail.de")
    assert "23-539 572 762" in f["TAX_ID"]
    assert "089- -358675" in f["PHONE"]
    assert "11.04.1950" in f["BIRTH_DATE"]
    assert "36 266759 s 152" in f["SOCIAL_SECURITY"]
    assert "bekir.weihmann@ hotmail.de" in f["EMAIL"]


def test_postcode_and_place_separated_by_a_line_break():
    """"… Lindenweg 3, 06098" / "Wurzen." – the address wraps at the line end."""
    text = "Vermietet wird die Wohnung im Hause Lindenweg 3, 06098\nWurzen.\n§ 2 Miete"
    assert "06098\nWurzen" in found(text)["ADDRESS"]
    # A number at the end of a line is not a postcode.
    text = "Ihre Rechnungsnummer lautet 12345\nMit freundlichen Grüßen"
    assert not any("12345" in t for t in found(text).get("ADDRESS", ()))
    text = "Kosten laut Anlage, 12345\nEuro insgesamt"
    assert not any("Euro" in t for t in found(text).get("ADDRESS", ()))


def test_section_sign_misread_by_ocr_is_restored():
    from doc_anonymizer.models import Word
    from doc_anonymizer.ocr import _restore_section_signs

    def line(text, line_id=1):
        return [Word(10 * n, 0, 10 * n + 8, 10, t, "ocr", line_id) for n, t in enumerate(text.split())]

    words = line("Kündigungsfrist gemäß S 622 Abs. 2 BGB zum Monatsende")
    _restore_section_signs(words)
    assert " ".join(w.text for w in words) == "Kündigungsfrist gemäß § 622 Abs. 2 BGB zum Monatsende"
    words = line("siehe S 12 der Akte")  # a page, not a section
    _restore_section_signs(words)
    assert words[1].text == "S"


# --- poor scans: specks in numbers, e-mail addresses without their "@" ------

def test_tax_id_with_scan_specks_between_the_groups():
    # Read from a poor scan: "ID" became "1D", a speck became an apostrophe.
    found = [d for d in detect("Steuer-1D: 75'124 758 579", 0, set(Category))
             if d.category is Category.TAX_ID]
    assert found and all(d.text == "75'124 758 579" for d in found)
    # Without a label the check digit still decides - an amount is not a tax id.
    assert not [d for d in detect("Betrag 12.345.678.901 EUR", 0, set(Category))
                if d.category is Category.TAX_ID]


@pytest.mark.parametrize("text, address", [
    # The "@" was read as "e", blanks appeared after the dots.
    ("Von: Walli Jäntsch <wallı. . jantscheyahoo. de>", "wallı. . jantscheyahoo. de"),
    ("schrieb karl.kaisergmail.com gestern", "karl.kaisergmail.com"),
    ("An: Max <max.mustermann@web.de>", "max.mustermann@web.de"),
])
def test_damaged_email_addresses_are_found(text, address):
    found = {d.text for d in detect(text, 0, set(Category))
             if d.category is Category.EMAIL}
    assert address in found


def test_markup_and_provider_names_are_no_addresses():
    for text in ("Text <b>fett</b> und <i>kursiv</i>", "Konto bei yahoo.de oder gmx.de",
                 "siehe Seite <3> und Anlage <K 1>"):
        assert not [d for d in detect(text, 0, set(Category))
                    if d.category is Category.EMAIL], text
