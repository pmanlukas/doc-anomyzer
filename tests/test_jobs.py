"""Robust batch processing: one bad document must not spoil the others."""

import json
import os
import time
import zipfile
from io import BytesIO

import pymupdf
import pytest

from doc_anonymizer import jobs, parallel
from doc_anonymizer.config import Settings
from doc_anonymizer.errors import WorkerCrashed
from doc_anonymizer.errors import Cancelled
from doc_anonymizer.jobs import (CONFIDENTIAL_FOLDER, FAILED_FOLDER, CaseStore, NameAnonymiser,
                                 Worker)
from doc_anonymizer.verify import name_leaks, search_terms
from doc_anonymizer.web import create_app

from .pool_helpers import work
from .sample_docs import make_digital_pdf

H = {"X-Anonymizer": "1"}


def _worker(store: CaseStore) -> Worker:
    worker = Worker.__new__(Worker)  # run jobs synchronously in the test
    worker.store = store
    return worker


def _encrypted_pdf(path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Geheim")
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="geheim", owner_pw="geheim")
    return path


@pytest.fixture
def case_with_bad_documents(tmp_path):
    store = CaseStore(tmp_path / "store")
    case = store.create("Gemischt")
    store.add_document(case, "Klage.pdf", make_digital_pdf(tmp_path / "ok.pdf").read_bytes())
    store.add_document(case, "Kaputt.pdf", b"%PDF-1.4\nkein echtes PDF\n")
    store.add_document(case, "Passwort.pdf", _encrypted_pdf(tmp_path / "pw.pdf").read_bytes())
    return store, case


@pytest.mark.parametrize("in_process", [True, False])
def test_unreadable_documents_are_skipped(case_with_bad_documents, monkeypatch, in_process):
    store, case = case_with_bad_documents
    if in_process:
        monkeypatch.setattr(jobs, "run_in_pool", lambda *a, **k: False)
    else:
        monkeypatch.setattr(parallel, "worker_count", lambda n: min(2, n))
    worker = _worker(store)
    worker.analyse(case.id)
    case = store.load(case.id)
    assert case.status == "pruefung"
    good, broken, locked = case.documents
    assert not good.error and good.pages == 2
    assert "beschädigt" in broken.error and "dok_" not in broken.error
    assert "passwortgeschützt" in locked.error
    worker.redact(case.id)
    case = store.load(case.id)
    assert case.status == "fertig"
    assert case.documents[0].verification["passed"] and case.documents[0].output
    assert not case.documents[1].output and "beschädigt" in case.documents[1].error

    client = create_app(store, start_worker=False).test_client()
    summary = client.get(f"/api/cases/{case.id}").get_json()["summary"]
    assert summary["passed"] is True and summary["unreadable"] == 2 and summary["not_passed"] == 0
    names = zipfile.ZipFile(BytesIO(client.get(f"/api/cases/{case.id}/zip").data)).namelist()
    assert len(names) == 3 and all("Klage" in n for n in names)


def test_all_documents_unreadable_is_an_error(tmp_path):
    store = CaseStore(tmp_path / "store")
    case = store.create("Nur kaputt")
    store.add_document(case, "Kaputt.pdf", b"%PDF-1.4\nnix\n")
    _worker(store).analyse(case.id)
    case = store.load(case.id)
    assert case.status == "fehler" and "beschädigt" in case.error
    assert "Traceback" not in case.error and str(tmp_path) not in case.error


def test_failed_verification_is_quarantined(tmp_path, monkeypatch):
    """A result that failed the check must not look like a finished file."""
    from doc_anonymizer import process
    from doc_anonymizer.verify import Leak

    original = process.verify_document

    def leaky(*args, **kwargs):
        result = original(*args, **kwargs)
        result.leaks.append(Leak(1, "PERSON", "unbekannt", "Restname", "Textebene"))
        return result

    monkeypatch.setattr(process, "verify_document", leaky)
    monkeypatch.setattr(jobs, "run_in_pool", lambda *a, **k: False)
    store = CaseStore(tmp_path / "store")
    case = store.create("Leck")
    store.add_document(case, "Klage.pdf", make_digital_pdf(tmp_path / "ok.pdf").read_bytes())
    worker = _worker(store)
    worker.analyse(case.id)
    worker.redact(case.id)
    case = store.load(case.id)
    doc = case.documents[0]
    assert case.status == "fertig" and doc.verification["passed"] is False
    assert FAILED_FOLDER in doc.output and FAILED_FOLDER in doc.text_output
    assert CONFIDENTIAL_FOLDER in doc.output and os.path.exists(doc.output)
    # No result folder "to pass on" exists – nothing passed.
    assert case.output_dir == ""
    root = Settings.load().output_dir
    assert os.listdir(root) == [CONFIDENTIAL_FOLDER]
    assert not (store.folder(case.id) / "ausgabe").exists()  # working copies are gone
    client = create_app(store, start_worker=False).test_client()
    summary = client.get(f"/api/cases/{case.id}").get_json()["summary"]
    assert summary["passed"] is False and summary["not_passed"] == 1
    archive = zipfile.ZipFile(BytesIO(client.get(f"/api/cases/{case.id}/zip").data))
    assert archive.namelist() == []  # failed documents are never part of the ZIP


def test_interrupted_cases_are_recovered(tmp_path):
    store = CaseStore(tmp_path / "store")
    reading = store.create("wurde gelesen")
    reading.status = "analyse"
    store.save(reading)
    redacting = store.create("wurde anonymisiert")
    redacting.status = "schwaerzen"
    store.save(redacting)
    (store.folder(redacting.id) / "register.json").write_text("{}")
    done = store.create("fertig")
    done.status = "fertig"
    store.save(done)
    Worker(store)  # program start
    assert store.load(reading.id).status == "fehler"
    assert "unterbrochen" in store.load(reading.id).error
    assert store.load(redacting.id).status == "pruefung"
    assert any("unterbrochen" in w for w in store.load(redacting.id).warnings)
    assert store.load(done.id).status == "fertig"


def test_old_cases_are_purged(tmp_path):
    store = CaseStore(tmp_path / "store")
    old, running, fresh = store.create("alt"), store.create("läuft"), store.create("neu")
    running.status = "analyse"
    store.save(running)
    long_ago = time.time() - 40 * 86400
    for case in (old, running):
        os.utime(store.folder(case.id) / "case.json", (long_ago, long_ago))
    assert store.purge(0) == 0  # 0 = never
    assert store.purge(30) == 1
    remaining = {c.title for c in store.list()}
    assert remaining == {"läuft", "neu"} and fresh.id
    settings = Settings.load()
    assert settings.keep_days == 30


def test_delete_all_cases(tmp_path):
    store = CaseStore(tmp_path / "store")
    store.create("a")
    busy = store.create("b")
    busy.status = "schwaerzen"
    store.save(busy)
    client = create_app(store, start_worker=False).test_client()
    assert client.delete("/api/cases").status_code == 403  # header missing
    assert client.delete("/api/cases", headers=H).get_json()["deleted"] == 1
    assert [c.title for c in store.list()] == ["b"]


def test_crashing_worker_only_fails_its_own_document(monkeypatch):
    """A worker process that dies must not take the other documents with it."""
    monkeypatch.setattr(parallel, "worker_count", lambda n: 2)
    results, errors = {}, {}
    used = parallel.run_in_pool([(n, work, (n,)) for n in range(1, 7)],
                                lambda i, r: results.__setitem__(i, r),
                                lambda i, e: errors.__setitem__(i, e))
    assert used is True
    assert results == {1: 10, 3: 30, 4: 40, 6: 60}
    assert isinstance(errors[2], WorkerCrashed) and isinstance(errors[5], ValueError)
    assert "Grenzen" in str(errors[2])


def test_case_files_from_newer_versions_still_load(tmp_path):
    store = CaseStore(tmp_path / "store")
    case = store.create("x")
    path = store.folder(case.id) / "case.json"
    data = json.loads(path.read_text())
    data["neues_feld"] = 1
    data["documents"] = [{"name": "a.pdf", "stored": "dok_0001.pdf", "zukunft": True}]
    path.write_text(json.dumps(data))
    assert store.load(case.id).documents[0].name == "a.pdf"


# ------------------------------------------------------------ result folders

def _finished_case(tmp_path, monkeypatch, title="Sache Mustermann", name="Klage.pdf"):
    monkeypatch.setattr(jobs, "run_in_pool", lambda *a, **k: False)
    store = CaseStore(tmp_path / "store")
    case = store.create(title)
    store.add_document(case, name, make_digital_pdf(tmp_path / "ok.pdf").read_bytes())
    worker = _worker(store)
    worker.analyse(case.id)
    worker.redact(case.id)
    return store, worker, store.load(case.id)


def test_result_folder_contains_only_what_may_be_passed_on(tmp_path, monkeypatch):
    store, _worker_, case = _finished_case(tmp_path, monkeypatch)
    root = Settings.load().output_dir
    assert case.status == "fertig" and os.path.dirname(case.output_dir) == root
    names = sorted(os.listdir(case.output_dir))
    assert names == ["Klage_Pruefbericht.html", "Klage_anonymisiert.pdf",
                     "Klage_anonymisiert.txt"]
    # The mapping table lies outside – zipping the result folder never includes it.
    assert CONFIDENTIAL_FOLDER in case.mapping and os.path.exists(case.mapping)
    assert not case.mapping.startswith(case.output_dir)
    assert os.path.basename(os.path.dirname(case.mapping)) == os.path.basename(case.output_dir)
    # The client's name in the case title does not end up in the folder name.
    assert "Mustermann" not in case.output_dir


def _at(monkeypatch, hour: int, minute: int) -> None:
    """Let the next run believe it starts at this time (folder names carry it)."""
    import datetime as real

    class Clock:
        @staticmethod
        def now():
            return real.datetime(2031, 5, 4, hour, minute)

    monkeypatch.setattr(jobs, "datetime", Clock)


def test_new_run_replaces_the_old_results(tmp_path, monkeypatch):
    """After a correction no outdated (flawed) result may stay behind."""
    store, worker, case = _finished_case(tmp_path, monkeypatch)
    first = case.output_dir
    old_files = [p for p in case.written if os.path.isfile(p)]
    assert len(old_files) == 4  # PDF, text, report, mapping table
    _at(monkeypatch, 9, 30)
    worker.redact(case.id)
    case = store.load(case.id)
    assert case.output_dir != first and not os.path.exists(first)
    assert not any(os.path.exists(p) for p in old_files)
    assert os.path.exists(case.documents[0].output) and os.path.exists(case.mapping)
    root = Settings.load().output_dir
    assert sorted(os.listdir(root)) == sorted([os.path.basename(case.output_dir),
                                               CONFIDENTIAL_FOLDER])
    assert os.listdir(os.path.join(root, CONFIDENTIAL_FOLDER)) == [
        os.path.basename(case.output_dir)]

    # A folder the user put own files into is kept – only our files go.
    second = case.output_dir
    with open(os.path.join(second, "eigene Notiz.txt"), "w") as fh:
        fh.write("bleibt")
    _at(monkeypatch, 9, 45)
    worker.redact(case.id)
    assert os.listdir(second) == ["eigene Notiz.txt"]


def test_cancel_puts_the_case_back(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "run_in_pool", lambda *a, **k: False)
    store = CaseStore(tmp_path / "store")
    case = store.create("Abbruch")
    for n in range(3):
        store.add_document(case, f"Brief {n}.pdf",
                           make_digital_pdf(tmp_path / "ok.pdf").read_bytes())
    worker = _worker(store)
    worker.analyse(case.id)

    # Cancel while the second document is being anonymised.
    original = jobs.anonymise_document
    seen = []

    def slow(source, target, index, *args, **kwargs):
        seen.append(index)
        if index == 1:
            worker.cancel(case.id)
        return original(source, target, index, *args, **kwargs)

    monkeypatch.setattr(jobs, "anonymise_document", slow)
    with pytest.raises(Cancelled):
        worker.redact(case.id)
    worker._after_cancel(case.id, "redact")
    case = store.load(case.id)
    assert case.status == "pruefung" and seen == [0, 1]
    assert not case.output_dir and not any(d.output for d in case.documents)
    root = Settings.load().output_dir
    assert not os.path.exists(root) or os.listdir(root) == []  # nothing half-done stays
    assert not (store.folder(case.id) / "ausgabe").exists()
    # ... and the review can go on: nothing of the user's work is lost.
    assert (store.folder(case.id) / "register.json").exists()


def test_cancel_in_worker_processes(monkeypatch):
    from .pool_helpers import slow

    monkeypatch.setattr(parallel, "worker_count", lambda n: 2)
    started = time.monotonic()
    results = []
    with pytest.raises(Cancelled):
        parallel.run_in_pool([(n, slow, (30,)) for n in range(4)],
                             lambda i, r: results.append(i), lambda i, e: None,
                             cancelled=lambda: time.monotonic() - started > 2)
    assert time.monotonic() - started < 20 and results == []


def test_single_document_can_run_isolated(monkeypatch):
    """After a crash of the whole program the next attempt uses a worker process."""
    monkeypatch.setattr(parallel, "worker_count", lambda n: 1)
    results, errors = {}, {}
    assert parallel.run_in_pool([(0, work, (1,))], results.__setitem__, errors.__setitem__) is False
    assert parallel.run_in_pool([(0, work, (2,))], results.__setitem__, errors.__setitem__,
                                isolate=True) is True
    assert isinstance(errors[0], WorkerCrashed) and not results


# ----------------------------------------------------------------- file names

def _registry_with(*people):
    from doc_anonymizer.models import Category, Entity
    from doc_anonymizer.registry import CaseRegistry

    registry = CaseRegistry([[]], Settings.load())
    for n, (given, surname) in enumerate(people):
        full = f"{given} {surname}"
        label = f"{given[0]}. {surname[0]}."
        registry.entities[f"e{n}"] = Entity(
            f"e{n}", Category.PERSON, full, label, given=[given], surname=surname,
            variants=[full, surname], forms={"surname": f"{surname[0]}."})
    return registry


@pytest.mark.parametrize("stem, expected", [
    ("Klage Mustermann", "Klage M"),
    ("KlageMustermann", "Klage M"),             # camel case
    ("Mustermann2024_Klage", "M. 2024 Klage"),  # digits right after the name
    ("Schriftsatz_Mueller", "Schriftsatz M"),   # umlaut written out
    ("Schriftsatz_Mu\u0308ller", "Schriftsatz M"),  # decomposed (macOS)
    ("Vollmacht Meissner", "Vollmacht M"),      # ß written as ss
    ("YILMAZ Bescheid", "Y. Bescheid"),         # dotless i
    ("klagemustermann", "Dokument_007"),        # not separable: neutral name
    ("Anlage K1", "Anlage K 1"),
])
def test_file_names_never_keep_a_name(stem, expected):
    registry = _registry_with(("Max", "Mustermann"), ("Jörg", "Müller"), ("Ute", "Meißner"),
                              ("Ali", "Yılmaz"))
    terms = search_terms(registry)
    name = NameAnonymiser(registry).clean(stem, terms, "Dokument_007")
    assert name == expected
    assert name_leaks(name, terms) == []


def test_file_name_check_is_strict():
    terms = search_terms(_registry_with(("Max", "Mustermann"), ("Jörg", "Müller")))
    for stem in ("Klage_Mustermann", "klagemustermann2024", "MUSTERMANN", "Brief Mueller",
                 "brief_muller_final"):
        assert name_leaks(stem, terms), stem
    assert name_leaks("Klageschrift_anonymisiert", terms) == []
