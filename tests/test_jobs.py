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
from doc_anonymizer.jobs import FAILED_FOLDER, CaseStore, Worker
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
    # nothing that looks finished lies in the main folder
    top = [p.name for p in os.scandir(case.output_dir) if p.is_file()]
    assert not [n for n in top if n.endswith(("_anonymisiert.pdf", "_anonymisiert.txt"))]
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
