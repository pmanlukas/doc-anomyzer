"""Self-learning: decisions, self-corrections, learned first names."""

from doc_anonymizer import learning
from doc_anonymizer.detect.persons import PersonRules
from doc_anonymizer.lexicon import is_first_name


def test_decisions_become_rules():
    learning.record(learning.Decision("Kanzlei Musterhaus", "ORG", "schwaerzen", "hinzugefuegt"))
    for _ in range(2):
        learning.record(learning.Decision("Arzthonorar", "ORG", "behalten", "unsicher"))
    info = learning.learned()
    assert "kanzlei musterhaus" in info.redact
    assert "arzthonorar" in info.keep


def test_self_corrections_are_training_only():
    learning.record_many([learning.Decision("Meister", "PERSON", "schwaerzen",
                                            learning.SELF_CORRECTION, [], ["Herr Meister kam"])])
    assert "meister" not in learning.learned().redact  # no global "always redact"
    assert learning.statistics()["selbstkorrekturen"] == 1
    assert learning.training_examples() == 1


def test_confirmed_person_teaches_first_name():
    assert not is_first_name("Zubeyda")
    learning.record(learning.Decision("Zubeyda Kaczmarek", "PERSON", "schwaerzen", "unsicher"))
    assert is_first_name("Zubeyda")
    found = {d.text for d in PersonRules().detect("Gestern rief Zubeyda Wrobel an.", 0)}
    assert "Zubeyda Wrobel" in found


def test_training_due():
    assert not learning.training_due(min_new=5)
    learning.record_many([learning.Decision(f"Name{i}", "PERSON", "schwaerzen", "unsicher", [],
                                            [f"Herr Name{i} kam"]) for i in range(5)])
    assert learning.training_due(min_new=5)
    learning.save_training_state(examples=learning.training_examples())
    assert not learning.training_due(min_new=5)


def test_exhibit_designations_are_never_learned_as_always_redact():
    # Earlier versions offered "B 1" as a place (road). A confirmation given
    # then must not remove the exhibit number from every future file.
    learning.record(learning.Decision("B 1", "LOCATION", "schwaerzen", "unsicher"))
    learning.record(learning.Decision("Anlage K 3", "OTHER_NAME", "schwaerzen", "unsicher"))
    learning.record(learning.Decision("Kanzlei Musterhaus", "ORG", "schwaerzen", "hinzugefuegt"))
    assert set(learning.learned().redact) == {"kanzlei musterhaus"}
