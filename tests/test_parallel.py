"""Parallel processing of many documents in one case file."""

import pymupdf

from doc_anonymizer.jobs import CaseStore, Worker

from .conftest import needs_tesseract
from .sample_docs import make_digital_pdf, make_scanned_pdf


@needs_tesseract
def test_parallel_case_consistent_labels(tmp_path, monkeypatch):
    # Force the process pool even on small machines.
    monkeypatch.setattr("doc_anonymizer.parallel.worker_count", lambda n: min(3, n))
    store = CaseStore(tmp_path / "store")
    case = store.create("Parallel")
    paths = [make_digital_pdf(tmp_path / "a.pdf"), make_digital_pdf(tmp_path / "b.pdf"),
             make_scanned_pdf(tmp_path / "c.pdf")]
    for name, path in zip(["Klage Schröder.pdf", "Kopie.pdf", "Scan.pdf"], paths):
        store.add_document(case, name, path.read_bytes())
    worker = Worker.__new__(Worker)
    worker.store = store
    worker.analyse(case.id)
    worker.redact(case.id)

    case = store.load(case.id)
    assert case.status == "fertig"
    assert all(d.verification["passed"] for d in case.documents)
    texts = []
    for doc in case.documents:
        with pymupdf.open(doc.output) as pdf:
            texts.append(" ".join(p.get_text() for p in pdf))
    # The same person has the same initials in every document.
    assert all("M. S." in t for t in texts[:2])
    assert all("Schröder" not in t for t in texts)
    # Output names are unique and anonymised.
    names = [d.output for d in case.documents]
    assert len(set(names)) == 3 and not any("Schröder" in n for n in names)
