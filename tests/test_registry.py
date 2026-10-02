"""Grouping, propagation and replacement labels."""

from doc_anonymizer.models import Category, Detection, PageInfo, Word
from doc_anonymizer.registry import CaseRegistry, FuzzyIndex, initial
from doc_anonymizer.textmap import PageText


def page(text: str, source: str = "text") -> PageInfo:
    words, x = [], 0.0
    for line_no, line in enumerate(text.split("\n")):
        for token in line.split():
            words.append(Word(x, line_no * 12, x + 5 * len(token), line_no * 12 + 10, token,
                              source, line_no))
            x += 5 * len(token) + 3
    return PageInfo(0, 600, 800, words)


def person(text: str, full: str, given: list[str], surname: str) -> Detection:
    start = text.index(full)
    return Detection(0, start, start + len(full), full, Category.PERSON, "regel:test", 0.9,
                     {"given": given, "surname": surname})


def build(text: str, detections, settings, source="text"):
    reg = CaseRegistry([[page(text, source)]], settings)
    reg.build([[detections]])
    return reg


def rendered(reg: CaseRegistry) -> str:
    """Page text with every mention replaced (like the plain-text export)."""
    p = reg.docs[0][0]
    out, skip = [], set()
    first = {m.word_indices[0]: m for m in reg.mentions}
    for idx in PageText.build(p.words).order:
        if idx in first:
            out.append(reg.replacement_for(first[idx]))
            skip.update(first[idx].word_indices)
        elif idx not in skip:
            out.append(p.words[idx].text)
    return " ".join(out)


def test_initials_and_propagation(settings):
    text = ("Herr Max Mustermann klagt. MUSTERMANN zahlte nicht. Mustermanns Anwalt "
            "schrieb an Max.")
    reg = build(text, [person(text, "Max Mustermann", ["Max"], "Mustermann")], settings)
    out = rendered(reg)
    assert "Mustermann" not in out and "MUSTERMANN" not in out and "Max" not in out
    assert "Herr M. M. klagt." in out
    assert "M. zahlte" in out


def test_collisions_get_numbers(settings):
    text = "Max Mustermann und Moritz Müller sowie Herr Müller."
    reg = build(text, [person(text, "Max Mustermann", ["Max"], "Mustermann"),
                       person(text, "Moritz Müller", ["Moritz"], "Müller")], settings)
    labels = sorted(e.replacement for e in reg.entities.values())
    assert labels == ["M. M.", "M. M. (2)"]
    assert "Herr M. (2)." in rendered(reg)


def test_ocr_typo_is_found(settings):
    text = "Max Mustermann wohnt hier. Später schrieb Musterrnann erneut."
    reg = build(text, [person(text, "Max Mustermann", ["Max"], "Mustermann")], settings,
                source="ocr")
    assert "Musterrnann" not in rendered(reg)


def test_fuzzy_index_and_initial():
    index = FuzzyIndex(["mustermann", "kowalski"])
    assert index.match("musterrnann") == "mustermann"
    assert index.match("meister") is None
    assert initial("von der Leyen") == "L"


def test_allow_list(settings):
    settings.allow_terms = ["Mustermann"]
    text = "Herr Max Mustermann klagt."
    reg = build(text, [person(text, "Max Mustermann", ["Max"], "Mustermann")], settings)
    assert not reg.mentions
