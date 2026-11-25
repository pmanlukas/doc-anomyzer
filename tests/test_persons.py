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


def test_name_in_address_block_does_not_run_into_the_street():
    """"Frau / Grit Herrmann / Pärtzeltring 517": the street is no surname –
    and the first name must not be lost because of it."""
    from doc_anonymizer.config import Settings
    from doc_anonymizer.detect.pipeline import Detector
    from doc_anonymizer.models import PageInfo, Word
    from doc_anonymizer.registry import CaseRegistry

    lines = ["Frau", "Grit Herrmann", "Pärtzeltring 517", "91531 Ribnitz-Damgarten", "",
             "Sehr geehrte Frau Herrmann,", "hiermit kündigen wir das Arbeitsverhältnis."]
    words = [Word(40 + 60 * n, 100 + 20 * i, 95 + 60 * n, 112 + 20 * i, text, "text", i)
             for i, line in enumerate(lines) for n, text in enumerate(line.split())]
    pages = [PageInfo(0, 595, 842, words)]
    settings = Settings(use_gliner=False)
    registry = CaseRegistry([pages], settings)
    registry.build([Detector(settings).detect_document(pages)])
    redacted = {words[i].text.strip(",") for m in registry.mentions for i in m.word_indices
                if registry.entities[m.entity_id].enabled}
    assert {"Grit", "Herrmann", "Pärtzeltring"} <= redacted
    person = next(e for e in registry.entities.values() if e.surname == "Herrmann")
    assert person.given == ["Grit"] and person.replacement == "G. H."
