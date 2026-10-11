"""Drive a running (installed) program through its web API like a user.

    python installer/api_smoke_test.py PORT a.pdf b.pdf ...

Creates a case, uploads the PDFs, analyses, accepts all proposals,
anonymises and checks that every document passed all verification stages.
Several documents are processed in parallel worker processes – on Windows
this tests the multiprocessing set-up of the installed (embedded) Python.
Standard library only.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(base: str, method: str, path: str, body=None, files: list[Path] | None = None):
    headers = {"X-Anonymizer": "1"}
    data = None
    if files is not None:
        boundary = uuid.uuid4().hex
        parts = []
        for f in files:
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; "
                         f"filename=\"{f.name}\"\r\nContent-Type: application/pdf\r\n\r\n"
                         .encode() + f.read_bytes() + b"\r\n")
        data = b"".join(parts) + f"--{boundary}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    with OPENER.open(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_for(base: str, case_id: str, states: set[str], timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        case = call(base, "GET", f"/api/cases/{case_id}")
        step = f"{case['status']}: {case.get('progress', {}).get('step', '')}"
        if step != last:
            print("  ", step, flush=True)
            last = step
        if case["status"] == "fehler":
            raise SystemExit(f"FEHLER in der Verarbeitung: {case.get('error')}")
        if case["status"] in states:
            return case
        time.sleep(2)
    raise SystemExit(f"Zeitüberschreitung, Status: {last}")


def main() -> int:
    port, files = int(sys.argv[1]), [Path(p) for p in sys.argv[2:]]
    base = f"http://127.0.0.1:{port}"
    started = time.monotonic()
    status = call(base, "GET", "/api/status")
    print("Status:", status)
    assert status["spacy"] and status["tesseract"] and "deu" in status["tesseract_languages"]
    case = call(base, "POST", "/api/cases", {"title": "CI-Test"})
    call(base, "POST", f"/api/cases/{case['id']}/documents", files=files)
    call(base, "POST", f"/api/cases/{case['id']}/analyse", {})
    wait_for(base, case["id"], {"pruefung"}, 900)
    call(base, "POST", f"/api/cases/{case['id']}/accept-all", {"pages": True})
    call(base, "POST", f"/api/cases/{case['id']}/redact", {})
    case = wait_for(base, case["id"], {"fertig"}, 1800)
    ok = True
    for doc in case["documents"]:
        passed = (doc.get("verification") or {}).get("passed")
        exists = bool(doc.get("output")) and Path(doc["output"]).exists()
        print(f"  {doc['name']}: {'bestanden' if passed else 'NICHT bestanden'}, "
              f"Ergebnis {'vorhanden' if exists else 'FEHLT'}")
        ok &= bool(passed) and exists
    print(f"Dauer: {time.monotonic() - started:.0f} s")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
