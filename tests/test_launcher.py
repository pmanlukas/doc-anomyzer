"""Start/stop behaviour of the installed program (doc_anonymizer.launcher)."""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _start(home: Path, idle: int = 60, grace: int = 60) -> subprocess.Popen:
    env = dict(os.environ, DOC_ANONYMIZER_HOME=str(home),
               DOC_ANONYMIZER_IDLE_SECONDS=str(idle), DOC_ANONYMIZER_STARTUP_SECONDS=str(grace))
    return subprocess.Popen([sys.executable, "-m", "doc_anonymizer.launcher", "--kein-browser"],
                            cwd=ROOT, env=env)


def _port(home: Path, timeout: float = 60) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            port = json.loads((home / "server.json").read_text())["port"]
            with _LOCAL.open(f"http://127.0.0.1:{port}/api/ping", timeout=2) as r:
                if json.loads(r.read())["app"] == "dokumenten-anonymisierer":
                    return port
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.3)
    raise AssertionError("Server ist nicht gestartet")


def _post(port: int, path: str, body: dict | None = None) -> int:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST",
                                 data=json.dumps(body or {}).encode(),
                                 headers={"X-Anonymizer": "1",
                                          "Content-Type": "application/json"})
    try:
        with _LOCAL.open(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


def test_single_instance_and_stop(home):
    server = _start(home)
    try:
        port = _port(home)
        # A second start finds the running program and ends at once.
        second = _start(home)
        assert second.wait(timeout=30) == 0
        assert server.poll() is None
        assert json.loads((home / "server.json").read_text())["pid"] == server.pid
        # "--beenden" (used by setup/uninstall) ends the program.
        env = dict(os.environ, DOC_ANONYMIZER_HOME=str(home))
        subprocess.run([sys.executable, "-m", "doc_anonymizer.launcher", "--beenden"],
                       cwd=ROOT, env=env, timeout=60, check=True)
        assert server.wait(timeout=30) == 0
        assert not (home / "server.json").exists()
        assert port
    finally:
        server.kill()


def test_ends_itself_without_open_page(home):
    server = _start(home, idle=4, grace=4)
    try:
        port = _port(home)
        # Signs of life keep it running ...
        for _ in range(4):
            assert _post(port, "/api/heartbeat") == 200
            time.sleep(2)
        assert server.poll() is None
        # ... without them it ends by itself (watchdog checks every 10 s).
        assert server.wait(timeout=40) == 0
    finally:
        server.kill()


def test_quit_button_endpoint(home):
    server = _start(home)
    try:
        port = _port(home)
        assert _post(port, "/api/shutdown", {}) == 200
        assert server.wait(timeout=30) == 0
    finally:
        server.kill()


def test_shutdown_not_offered_in_plain_server():
    """Started with "python -m doc_anonymizer start" there is no remote stop."""
    from doc_anonymizer.web import create_app

    client = create_app(start_worker=False).test_client()
    assert client.post("/api/shutdown", headers={"X-Anonymizer": "1"}).status_code == 404
    assert client.get("/api/status").get_json()["managed"] is False
