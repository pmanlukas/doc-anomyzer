"""Person name rules for German legal texts."""

import pytest

from doc_anonymizer.detect.persons import PersonRules


def names(text: str) -> set[str]:
    return {d.text for d in PersonRules().detect(text, 0)}


@pytest.mark.parametrize("text, expected", [
    ("Der Kläger, Herr Dr. Maximilian Schröder, wohnt hier.", "Maximilian Schröder"),
    ("vertreten durch Rechtsanwältin Petra Kowalski,", "Petra Kowalski"),
    ("Richterin am Landgericht Dr. Weber.", "Weber"),
    ("Mustermann, Erika, Kontoinhaberin", "Mustermann, Erika"),
    ("Beteiligter 1: Kowara, Liwia, geb. 12.01.1970", "Kowara, Liwia"),
    ("gez. Hans-Peter Wagner", "Hans-Peter Wagner"),
    ("i. V. Margrit Margraf\nGeschäftsführer", "Margrit Margraf"),
    ("Frau Cäcilie Mehringer teilte mit", "Cäcilie Mehringer"),
    ("Hallo Lisa,\nanbei", "Lisa"),
    ("Frau van der Dussen erklärte", "van der Dussen"),
    ("Herr Max\nMustermann erschien", "Max\nMustermann"),
    ("Erika MUSTERMANN und Max Mustermann", "Max Mustermann"),
])
def test_detects(text, expected):
    assert expected in names(text)


def test_address_block_stops_at_line_break():
    found = names("Dr. Jan Beispielmann\nHans-Muster-Straße 7")
    assert "Jan Beispielmann" in found
    assert not any("Hans" in n for n in found)


def test_no_false_positives():
    found = names("Sehr geehrte Frau Kollegin, dem Zeugen Glauben schenken. "
                  "Die Klägerin Anspruch. Mit freundlichen Grüßen")
    assert found == set()
