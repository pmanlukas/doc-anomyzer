"""Exhibit designations ("Anlage K 1", "B 3") are numbering and stay readable."""

from __future__ import annotations

import re

import pymupdf
import pytest

from doc_anonymizer.config import Settings
from doc_anonymizer.detect import references
from doc_anonymizer.detect.pipeline import Detector
from doc_anonymizer.extract import analyse_document
from doc_anonymizer.process import anonymise_document
from doc_anonymizer.registry import CaseRegistry

from .exhibit_sentences import PLATE_SENTENCE, SENTENCES


def _found(text: str) -> list[str]:
    return [text[start:end] for start, end in references.spans(text)]


@pytest.mark.parametrize("text, expected", [
    # the usual forms: Kläger / Beklagte
    ("Beweis: Mietvertrag, Anlage K 1.", ["Anlage K 1"]),
    ("als Anlage K1 vorgelegt", ["Anlage K1"]),
    ("(Anlage B 1, Bl. 152 f. d. A.)", ["Anlage B 1"]),
    ("Anl. K 5, S. 3", ["Anl. K 5"]),
    ("ANLAGE K 3", ["ANLAGE K 3"]),
    # numbers: leading zeros, letters, sub-numbers
    ("Anlage K01, Anlage K001 und Anlage K116", ["Anlage K01", "Anlage K001", "Anlage K116"]),
    ("Anlagen K 1a, K 1b und K 1c", ["Anlagen K 1a", "K 1b", "K 1c"]),
    ("(Anlage K 1.1), (Anlage B2-1), (Anlagen HL 5/5a)",
     ["Anlage K 1.1", "Anlage B2-1", "Anlagen HL 5/5a"]),
    # lists and ranges without the word "Anlage"
    ("Anlagen K 14 - K 24 und K 25 bis K 27", ["Anlagen K 14", "K 24", "K 25", "K 27"]),
    ("wie sich aus K 1 ergibt, entgegen B 3", ["K 1", "B 3"]),
    ("Anlagenkonvolut K 3", ["Anlagenkonvolut K 3"]),
    # other roles
    ("Anlage ASt 2, ASt 4 bis ASt 8, Anlage Ast. 1, Anlage AST 1",
     ["Anlage ASt 2", "ASt 4", "ASt 8", "Anlage Ast. 1", "Anlage AST 1"]),
    ("Anlage AG 01, Anlage AGG 1, Anlage A 1", ["Anlage AG 01", "Anlage AGG 1", "Anlage A 1"]),
    ("Anlage BK 1, Anlage BB 1, Anlage Bf 1, Anlage BG 1",
     ["Anlage BK 1", "Anlage BB 1", "Anlage Bf 1", "Anlage BG 1"]),
    ("Anlage WK 1, Anlage Vk 1, Anlage SH 1, Anlagen SV1 und SV2, Anlagenkonvolut NI 11",
     ["Anlage WK 1", "Anlage Vk 1", "Anlage SH 1", "Anlagen SV1", "SV2", "Anlagenkonvolut NI 11"]),
    # patent cases, initials of law firms
    ("Anlage NK 1, NK 2 und NK 11; D1, D2 und D1.1; NB1",
     ["Anlage NK 1", "NK 2", "NK 11", "D1", "D2", "D1.1", "NB1"]),
    ("Anlage HL 1, Anlagen rop 10a und rop 10b, Anlage rop WK 1",
     ["Anlage HL 1", "Anlagen rop 10a", "rop 10b", "Anlage rop WK 1"]),
    ("Anlage B&B 1 und Anlage B & B 10", ["Anlage B&B 1", "Anlage B & B 10"]),
    ("Anlage FBD 1, Anlage TW 1, Anlage WKS1, Anlage KAP 21",
     ["Anlage FBD 1", "Anlage TW 1", "Anlage WKS1", "Anlage KAP 21"]),
    # without prefix, Roman numerals, file names of electronic filing
    ("Anlage 3 und Anlage II zum Protokoll", ["Anlage 3", "Anlage II"]),
    ("03_Anl_K1.pdf und 01_20220121_AnlageK1_Kuendigung.pdf", ["K1", "AnlageK1"]),
    # arbitration, Austria, Switzerland
    ("Exhibit C-12, CL-3, R-1 und RL-2", ["Exhibit C-12", "CL-3", "R-1", "RL-2"]),
    ("(Beilage ./A) und (Beilage ./1)", ["Beilage ./A", "Beilage ./1"]),
    ("(act. 1), Klagebeilage 5 (act. 3/5), KB 37-60",
     ["act. 1", "Klagebeilage 5", "act. 3/5", "KB 37-60"]),
    # torn apart by a line break in justified text
    ("vorgelegt als Anlage K\n1 und als Anlage\nB 2", ["Anlage K\n1", "Anlage\nB 2"]),
])
def test_designations_are_recognised(text, expected):
    assert _found(text) == expected


@pytest.mark.parametrize("text", [
    "Anlage Max 3",                    # a capitalised word after "Anlage" may be a name
    "Anlage Müller 12",
    "Anl. Li 3",
    "Anlage von Uwe 2019",
    "Anlage zum Vertrag 2019",
    "Anna 3 Jahre alt",
    "Frau Li 2019",
    "Herr K. wurde 1990 geboren",      # an abbreviated name is not a code
    "Dr. A. Meier",
    "Kanzlei B. & Partner",
    "wohnhaft in Ulm 5 km entfernt",
    "Haus 5, Zimmer 12",
    "Max Mustermann, geb. 1.1.1980",
])
def test_names_are_never_taken_for_designations(text):
    assert _found(text) == []


def test_whole_value_and_remainder():
    assert references.is_reference("K 1") and references.is_reference("(Anlage B 3)")
    assert not references.is_reference("Anlage K 1 Mustermann")
    text = "siehe Anlage K 5 Mustermann GmbH"
    protected = references.spans(text)
    # What is left beside the designation is still examined.
    start, end = references.outside(text, 6, len(text), protected)
    assert text[start:end] == "Mustermann GmbH"
    assert references.outside(text, 6, 16, protected) is None            # only the designation
    assert references.outside(text, 17, len(text), protected) == (17, len(text))  # untouched


def _anonymise(tmp_path, paragraphs: list[str]):
    """Write the paragraphs as a pleading, anonymise it, return (text, entities)."""
    source = tmp_path / "schriftsatz.pdf"
    doc = pymupdf.open()
    for first in range(0, len(paragraphs), 10):
        page = doc.new_page()
        left = page.insert_textbox(pymupdf.Rect(60, 60, 535, 800),
                                   "\n\n".join(paragraphs[first:first + 10]),
                                   fontsize=11, fontname="helv")
        assert left >= 0, "Text passt nicht auf die Seite"
    doc.save(source)
    doc.close()
    settings = Settings(use_gliner=False)
    pages = analyse_document(source, settings)
    registry = CaseRegistry([pages], settings)
    registry.build([Detector(settings).detect_document(pages)])
    result = anonymise_document(source, tmp_path / "out.pdf", 0, pages, registry, settings)
    assert result.verification.passed
    with pymupdf.open(tmp_path / "out.pdf") as pdf:
        text = re.sub(r"\s+", " ", " ".join(page.get_text() for page in pdf))
    return text, registry


def test_pleading_keeps_its_exhibit_numbers_and_loses_the_names(tmp_path):
    text, registry = _anonymise(tmp_path, [s for s, _, _ in SENTENCES] + [PLATE_SENTENCE])
    problems = []
    for sentence, keep, remove in SENTENCES:
        problems += [f"fehlt: {k!r} in: {sentence}" for k in keep if k not in text]
        problems += [f"nicht entfernt: {r!r} in: {sentence}" for r in remove if r in text]
    assert not problems, "\n".join(problems)
    # Personal data in the same sentence as a designation is still removed.
    assert "K-AB 1234" not in text and "Anlage K 4" in text
    # No replacement was invented for a designation.
    for entity in registry.entities.values():
        assert not any(references.is_reference(v) for v in entity.variants), entity.variants


def test_language_model_guesses_on_designations_are_dropped(tmp_path):
    # Before: "K 4", "B 1" and "B 3" became places (roads), "BK 1" a company.
    text, registry = _anonymise(tmp_path, [
        "Hierzu verweisen wir auf die Anlagen K 4 bis K 6 sowie das Anlagenkonvolut K 7.",
        "Die Beklagte hat mit Anlage B 1 und B2 erwidert, vgl. auch B 3, S. 2.",
        "Im Berufungsverfahren wurden BK 1 und BB 2 eingereicht (Bl. 45 d. A., GA 112).",
        "Wie sich aus K1 ergibt, schuldet die Beklagte Erika Hoffmann 2.450,00 EUR.",
    ])
    for code in ("K 4 bis K 6", "Anlagenkonvolut K 7", "Anlage B 1 und B2", "B 3, S. 2",
                 "BK 1 und BB 2", "aus K1 ergibt"):
        assert code in text
    assert "Hoffmann" not in text
    assert [e.category.value for e in registry.entities.values()] == ["PERSON"]
