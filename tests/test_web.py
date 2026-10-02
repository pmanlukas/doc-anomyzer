"""Local web interface: security guard and complete workflow."""

import pytest

from doc_anonymizer.jobs import CaseStore, Worker
from doc_anonymizer.web import create_app

from .sample_docs import make_digital_pdf

H = {"X-Anonymizer": "1"}


@pytest.fixture
def client(tmp_path):
    store = CaseStore(tmp_path / "store")
    app = create_app(store, start_worker=False)
    app.config["TESTING"] = True
    return app.test_client(), store


def test_requests_from_other_hosts_are_rejected(client):
    c, _ = client
    assert c.get("/api/cases", base_url="http://evil.example").status_code == 403
    assert c.post("/api/cases", json={"title": "x"}).status_code == 403  # header missing
    assert c.post("/api/cases", json={"title": "x"}, headers=H).status_code == 200


def test_complete_workflow(client, tmp_path):
    c, store = client
    case = c.post("/api/cases", json={"title": "Test"}, headers=H).get_json()
    pdf = make_digital_pdf(tmp_path / "klage.pdf")
    with open(pdf, "rb") as fh:
        resp = c.post(f"/api/cases/{case['id']}/documents", headers=H,
                      data={"files": (fh, "Klage Schröder.pdf")})
    assert resp.status_code == 200
    worker = Worker.__new__(Worker)  # run jobs synchronously
    worker.store = store
    worker.analyse(case["id"])

    entities = c.get(f"/api/cases/{case['id']}/entities").get_json()
    by_original = {e["original"]: e for e in entities}
    assert "Maximilian Schröder" in by_original
    attention = c.get(f"/api/cases/{case['id']}/attention").get_json()
    assert "uncertain_open" in attention

    # user decisions: edit a label, add a term, draw a box
    eid = by_original["Maximilian Schröder"]["id"]
    assert c.patch(f"/api/cases/{case['id']}/entities/{eid}", headers=H,
                   json={"replacement": "Kläger K."}).status_code == 200
    assert c.post(f"/api/cases/{case['id']}/entities", headers=H,
                  json={"text": "Abnahme", "category": "MANUAL"}).get_json()["count"] == 1
    assert c.post(f"/api/cases/{case['id']}/manual", headers=H,
                  json={"doc": 0, "page": 0, "rect": [400, 700, 500, 740]}).status_code == 200
    boxes = c.get(f"/api/cases/{case['id']}/docs/0/pages/0/boxes").get_json()
    assert boxes["boxes"] and boxes["manual"]
    assert c.get(f"/api/cases/{case['id']}/docs/0/pages/0.png").mimetype == "image/png"

    worker.redact(case["id"])
    result = c.get(f"/api/cases/{case['id']}").get_json()
    assert result["status"] == "fertig"
    doc = result["documents"][0]
    assert doc["verification"]["passed"]
    assert "Schröder" not in doc["output"]  # file name anonymised as well
    pdf_bytes = c.get(f"/api/cases/{case['id']}/docs/0/download/pdf").data
    assert pdf_bytes.startswith(b"%PDF")
    txt = c.get(f"/api/cases/{case['id']}/docs/0/download/txt").data.decode()
    assert "Kläger K." in txt and "Schröder" not in txt and "Abnahme" not in txt

    # learning: the added term is remembered for the next case
    from doc_anonymizer import learning
    assert "abnahme" in learning.learned().redact
