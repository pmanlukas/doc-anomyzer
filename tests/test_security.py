"""Protection of the local web interface and handling of hostile input."""

import json
import re
from pathlib import Path

import pymupdf
import pytest

from doc_anonymizer import web
from doc_anonymizer.config import Settings
from doc_anonymizer.jobs import CaseStore, _safe_filename, display_name
from doc_anonymizer.web import create_app, ping_proof

from .sample_docs import make_digital_pdf

H = {"X-Anonymizer": "1"}
TOKEN = "t" * 43
STATIC = Path(web.__file__).parent / "static"


@pytest.fixture
def locked(tmp_path):
    """App with access token – as started by the launcher."""
    app = create_app(CaseStore(tmp_path / "store"), start_worker=False, token=TOKEN)
    return app.test_client()


@pytest.fixture
def client(tmp_path):
    app = create_app(CaseStore(tmp_path / "store"), start_worker=False)
    return app.test_client()


# ------------------------------------------------------------- access token

def test_nothing_works_without_the_token(locked):
    for path in ("/api/cases", "/api/settings", "/api/status", "/api/learning",
                 "/api/folders", "/api/cases/" + "0" * 32):
        resp = locked.get(path)
        assert resp.status_code == 401, path
        assert resp.get_json()["locked"] is True
    assert locked.post("/api/cases", json={"title": "x"}, headers=H).status_code == 401
    assert locked.post("/api/shutdown", headers=H).status_code == 401
    page = locked.get("/")
    assert page.status_code == 401 and "Programmsymbol" in page.get_data(as_text=True)
    # Public: the ping (without secrets) and the static files of the locked page.
    assert locked.get("/api/ping").get_json() == {"app": "dokumenten-anonymisierer",
                                                   "version": web.__version__}
    assert locked.get("/static/app.css").status_code == 200


def test_token_in_address_becomes_a_cookie(locked):
    assert locked.get("/?token=falsch").status_code == 401
    resp = locked.get(f"/?token={TOKEN}")
    assert resp.status_code == 303 and resp.headers["Location"] == "/"  # token leaves the URL
    cookie = resp.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert locked.get("/api/cases").status_code == 200  # the test client keeps the cookie
    assert locked.get("/").status_code == 200


def test_token_header_for_programs(locked):
    assert locked.get("/api/cases", headers={"X-Anonymizer-Token": TOKEN}).status_code == 200
    assert locked.get("/api/cases", headers={"X-Anonymizer-Token": TOKEN + "x"}).status_code == 401
    assert locked.get("/api/cases", headers={"X-Anonymizer-Token": "ä"}).status_code == 401


def test_ping_proves_knowledge_of_the_token(locked):
    challenge = "abcdef0123456789"
    proof = locked.get(f"/api/ping?c={challenge}").get_json()["proof"]
    assert proof == ping_proof(TOKEN, challenge)
    assert TOKEN not in proof
    assert "proof" not in locked.get("/api/ping?c=kurz").get_json()


# ----------------------------------------------------- cross-site requests

def test_requests_from_other_sites_are_rejected(client):
    assert client.get("/api/cases", base_url="http://evil.example").status_code == 403
    assert client.get("/api/cases", base_url="http://127.0.0.1.evil.example").status_code == 403
    assert client.get("/api/cases", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/api/cases", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    assert client.get("/api/cases", headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.get("/api/cases", headers={"Origin": "null"}).status_code == 403
    assert client.post("/api/cases", json={"title": "x"}).status_code == 403  # header missing
    ok = {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost", **H}
    assert client.post("/api/cases", json={"title": "x"}, headers=ok).status_code == 200


def test_security_headers(client):
    for path in ("/", "/api/cases", "/static/app.js"):
        headers = client.get(path).headers
        csp = headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
        assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Cache-Control"] == "no-store"
        assert headers["Cross-Origin-Resource-Policy"] == "same-origin"
        assert headers["Referrer-Policy"] == "no-referrer"


def test_page_works_without_inline_code():
    """The strict CSP only holds if the page never needs inline script/style."""
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"\son\w+\s*=", index) and "style=" not in index
    assert "<script>" not in index and "javascript:" not in index
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in script, forbidden
    locked_page = (STATIC / "gesperrt.html").read_text(encoding="utf-8")
    assert "<script" not in locked_page and "style=" not in locked_page


# ------------------------------------------------------------ input checks

def test_file_names_are_made_safe():
    assert _safe_filename("../../Windows/System32") == "Windows_System32"
    assert _safe_filename("a:b*c?d<e>f|g\"h") == "a_b_c_d_e_f_g_h"
    assert _safe_filename("CON") == "_CON" and _safe_filename("nul.txt") == "_nul.txt"
    assert _safe_filename("   ...  ") == "Dokument"
    assert len(_safe_filename("x" * 500)) == 80
    assert not _safe_filename("Brief. ").endswith((".", " "))
    assert display_name("..\\..\\böse/../Akte Müller.pdf") == "böse/Akte Müller.pdf"
    assert display_name("a\x00b\nc.pdf") == "abc.pdf"


def test_upload_stores_under_neutral_name(client, tmp_path):
    case = client.post("/api/cases", json={"title": "T"}, headers=H).get_json()
    pdf = make_digital_pdf(tmp_path / "x.pdf")
    with open(pdf, "rb") as fh, open(__file__, "rb") as not_pdf:
        resp = client.post(f"/api/cases/{case['id']}/documents", headers=H, data={
            "files": [(fh, "../../../evil/Akte Müller.pdf"), (not_pdf, "virus.pdf")]})
    data = resp.get_json()
    assert data["added"] == ["evil/Akte Müller.pdf"] and data["rejected"] == ["virus.pdf"]
    folder = next((tmp_path / "store" / "akten").iterdir())
    assert sorted(p.name for p in folder.iterdir()) == ["case.json", "dok_0001.pdf"]
    assert not (tmp_path / "evil").exists()


def test_invalid_requests_do_not_crash(client, tmp_path):
    case = client.post("/api/cases", json={"title": "\x00<b>T</b>\n" + "x" * 500},
                       headers=H).get_json()
    assert len(case["title"]) <= 150 and "\n" not in case["title"]
    cid = case["id"]
    assert client.get("/api/cases/nicht-vorhanden").status_code == 404
    assert client.get("/api/cases/" + "f" * 32).status_code == 404
    assert client.get("/api/cases/../../etc/passwd").status_code in (403, 404)
    assert client.post(f"/api/cases/{cid}/analyse", headers=H).status_code == 400  # no files
    assert client.post(f"/api/cases/{cid}/redact", headers=H).status_code == 409
    assert client.get(f"/api/cases/{cid}/entities").status_code == 409
    assert client.get(f"/api/cases/{cid}/docs/0/pages/0.png").status_code == 404
    assert client.get(f"/api/cases/{cid}/docs/0/download/pdf").status_code == 404
    assert client.get(f"/api/cases/{cid}/zip").status_code == 200  # empty archive
    assert client.post("/api/cases", data="kein json", headers=H).status_code == 200
    assert client.post("/api/settings", json=["liste"], headers=H).status_code == 200
    assert client.post("/api/settings", json={"keep_days": "viele"}, headers=H).status_code == 400
    assert client.get("/static/../web.py").status_code == 404


def test_settings_are_validated(client, tmp_path):
    target = tmp_path / "Ergebnisse"
    resp = client.post("/api/settings", headers=H, json={
        "categories": ["PERSON", "GIBT_ES_NICHT"], "security_mode": "kaputt",
        "keep_days": 99999, "output_dir": str(target), "use_learning": "ja",
        "deny_terms": [{"text": " Mustermann ", "category": "FALSCH"}, "kein dict", {"text": ""}],
        "allow_terms": [" Kanzlei X ", "", None]})
    assert resp.status_code == 200
    s = Settings.load()
    assert s.categories == ["PERSON"] and s.security_mode == "standard"
    assert s.keep_days == 3650 and s.use_learning is True
    assert s.deny_terms == [{"text": "Mustermann", "category": "MANUAL"}]
    assert s.allow_terms == ["Kanzlei X"]
    assert target.is_dir() and s.output_dir == str(target)
    # a relative path or an unusable folder is refused with an explanation
    for bad in ("Ergebnisse", "", str(tmp_path / "datei.txt" / "unterordner")):
        (tmp_path / "datei.txt").write_text("x")
        resp = client.post("/api/settings", headers=H, json={"output_dir": bad})
        assert resp.status_code == 400 and resp.get_json()["error"], bad
    assert Settings.load().output_dir == str(target)


def test_folder_picker(client, tmp_path):
    (tmp_path / "Akten" / "Müller").mkdir(parents=True)
    (tmp_path / "Akten" / ".versteckt").mkdir()
    (tmp_path / "Akten" / "datei.txt").write_text("x")
    data = client.get(f"/api/folders?path={tmp_path / 'Akten'}").get_json()
    assert data["path"] == str(tmp_path / "Akten") and data["parent"] == str(tmp_path)
    assert [f["name"] for f in data["folders"]] == ["Müller"]  # no files, no hidden folders
    assert data["writable"] and data["shortcuts"]
    # a folder that does not exist: the nearest existing parent is shown
    data = client.get(f"/api/folders?path={tmp_path / 'Akten' / 'neu' / 'tiefer'}").get_json()
    assert data["path"] == str(tmp_path / "Akten")
    # new folder: the name cannot leave the parent
    made = client.post("/api/folders", headers=H,
                       json={"parent": str(tmp_path / "Akten"), "name": "../../raus"}).get_json()
    assert made["path"] == str(tmp_path / "Akten" / "raus")
    assert not (tmp_path.parent / "raus").exists()
    assert client.post("/api/folders", headers=H,
                       json={"parent": "relativ", "name": "x"}).status_code == 400
    assert client.post("/api/folders", headers=H,
                       json={"parent": str(tmp_path), "name": "  "}).status_code == 400


# -------------------------------------------------------------- hostile PDFs

def test_huge_pages_are_rendered_smaller(tmp_path, settings):
    """A page far larger than A0 must not exhaust the memory."""
    from doc_anonymizer import extract

    doc = pymupdf.open()
    page = doc.new_page(width=14400, height=14400)  # 5 x 5 metres
    page.insert_text((2000, 3000), "Herr Maximilian Schröder wohnt in Musterstadt.", fontsize=300)
    assert extract.effective_dpi(page, 300) < 40
    image = extract.render_page(page, 300)
    assert image.width * image.height <= extract.MAX_RENDER_PIXELS * 1.01
    assert abs(extract.image_scale(page, image) * image.width - 14400) < 1
    path = tmp_path / "riesig.pdf"
    doc.save(path)
    pages = extract.analyse_document(path, settings)
    assert any(w.text == "Schröder" for w in pages[0].words)


def test_too_many_pages_are_refused(tmp_path, settings, monkeypatch):
    from doc_anonymizer import extract

    monkeypatch.setattr(extract, "MAX_PAGES", 3)
    doc = pymupdf.open()
    for _ in range(4):
        doc.new_page()
    path = tmp_path / "viele.pdf"
    doc.save(path)
    with pytest.raises(extract.PdfOpenError, match="Seiten"):
        extract.analyse_document(path, settings)


def test_preview_of_huge_page_is_limited(client, tmp_path):
    case = client.post("/api/cases", json={"title": "T"}, headers=H).get_json()
    doc = pymupdf.open()
    doc.new_page(width=14400, height=14400)
    data = doc.tobytes()
    import io

    client.post(f"/api/cases/{case['id']}/documents", headers=H,
                data={"files": (io.BytesIO(data), "riesig.pdf")})
    resp = client.get(f"/api/cases/{case['id']}/docs/0/pages/0.png?zoom=4")
    assert resp.status_code == 200
    pix = pymupdf.Pixmap(resp.data)
    assert pix.width * pix.height <= web.MAX_PREVIEW_PIXELS * 1.01
    assert client.get(f"/api/cases/{case['id']}/docs/0/pages/0.png?zoom=abc").status_code == 200


def test_reports_carry_their_own_csp(client, tmp_path):
    from doc_anonymizer.report import _page

    html = _page("Titel <script>alert(1)</script>", "<p>x</p>")
    assert "default-src 'none'" in html and "<script>alert" not in html
    assert json.dumps(html)  # plain text
