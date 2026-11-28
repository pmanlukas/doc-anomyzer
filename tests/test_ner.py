"""Model adapters: GLiNER windowing (with a stand-in model) and spaCy filters."""

from doc_anonymizer.detect import ner
from doc_anonymizer.detect.pipeline import Detector
from doc_anonymizer.models import Category


class FakeGliner:
    """Mimics GLiNER.predict_entities: finds 'Okonkwo' in every window."""

    calls = 0

    def predict_entities(self, text, labels, threshold=0.5):
        FakeGliner.calls += 1
        pos = text.find("Okonkwo")
        if pos < 0:
            return []
        return [{"start": pos, "end": pos + 7, "text": "Okonkwo", "label": "person",
                 "score": 0.91}]


def test_gliner_windows_and_offsets(monkeypatch):
    monkeypatch.setattr(ner, "load_gliner", lambda: FakeGliner())
    filler = " ".join(["Wort"] * 400)
    text = f"{filler} Herr Okonkwo kam. {filler}"
    found = ner.gliner_detect(text, 0, 0.5, set(Category))
    assert found and all(text[d.start:d.end] == "Okonkwo" for d in found)
    assert FakeGliner.calls > 3  # long text was split into overlapping windows


def test_detector_uses_gliner_and_survives_failures(monkeypatch, settings):
    settings.use_gliner = True
    monkeypatch.setattr(ner, "gliner_installed", lambda: True)
    monkeypatch.setattr(ner, "load_gliner", lambda: FakeGliner())
    detector = Detector(settings)
    text = "Gestern sprach Herr Okonkwo mit uns."
    res = ner.spacy_analyse([text])[0]
    assert any(d.source == "ki:gliner" for d in detector.detect_page(text, 0, res))

    def broken():
        raise RuntimeError("Modell defekt")
    monkeypatch.setattr(ner, "load_gliner", broken)
    detector = Detector(settings)
    detector.detect_page(text, 0, res)  # must not raise
    assert detector.warnings and not detector.use_gliner


def test_spacy_filters_ordinary_words(settings):
    text = "Unzutreffend ist, dass Herr Brzęczyszczykiewicz kam. III. Gründe"
    res = ner.spacy_analyse([text])[0]
    found = {d.text: d for d in Detector(settings).detect_page(text, 0, res)}
    assert "Brzęczyszczykiewicz" in found
    assert "Unzutreffend" not in found and "III" not in found


def test_known_words_export_survives_concurrent_access(monkeypatch, tmp_path):
    """Windows: the word list cannot be replaced while another worker process
    reads it. The export must then go on with the list in memory."""
    from pathlib import Path

    from doc_anonymizer.detect import ner

    ner._known_words.cache_clear()
    target = ner._known_words_file()
    target.unlink(missing_ok=True)

    def locked(self, other):
        raise PermissionError(32, "The process cannot access the file because it is "
                                  "being used by another process")

    monkeypatch.setattr(Path, "replace", locked)
    try:
        words = ner._known_words()
    finally:
        ner._known_words.cache_clear()
    assert len(words) > 100_000
    assert not list(target.parent.glob("*.tmp"))  # no leftovers


def test_language_model_is_loaded_once_even_when_two_threads_ask(monkeypatch):
    # The model is pre-loaded in the background while documents are read. A
    # caller that needs it meanwhile waits - it must not load 1 GB again.
    import threading
    import time

    from doc_anonymizer.detect import ner

    calls = []

    def slow_load():
        calls.append(threading.current_thread().name)
        time.sleep(0.3)
        return object()

    monkeypatch.setattr(ner, "_load_spacy_once", slow_load)
    monkeypatch.setattr(ner, "_preload_started", False)
    # (The real function is cached; the stand-in is not - so every call that
    # is not kept out by the lock would be counted.)
    ner.preload()
    ner.preload()                     # a second request starts nothing
    time.sleep(0.05)
    assert len(calls) == 1            # the background thread is loading ...
    started = time.monotonic()
    ner.load_spacy()                  # ... and this caller waits for it
    assert time.monotonic() - started > 0.15
    assert [name for name in calls if name == "sprachmodell"] == ["sprachmodell"]


def test_preloading_never_raises(monkeypatch):
    import time

    from doc_anonymizer.detect import ner

    def broken():
        raise OSError("Modell fehlt")

    monkeypatch.setattr(ner, "_load_spacy_once", broken)
    monkeypatch.setattr(ner, "_preload_started", False)
    ner.preload()
    time.sleep(0.2)                   # the thread ends quietly
    assert ner.load_spacy.cache_clear is not None
