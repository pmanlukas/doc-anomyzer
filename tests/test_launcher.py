"""Start/stop behaviour and access protection of the installed program."""

import http.server
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _env(home: Path, **extra) -> dict:
    return dict(os.environ, DOC_ANONYMIZER_HOME=str(home), DOC_ANONYMIZER_NO_DIALOG="1",
                **{k: str(v) for k, v in extra.items()})


def _start(home: Path, idle: int = 60, grace: int = 60, close: int = 60) -> subprocess.Popen:
    env = _env(home, DOC_ANONYMIZER_IDLE_SECONDS=idle, DOC_ANONYMIZER_STARTUP_SECONDS=grace,
               DOC_ANONYMIZER_CLOSE_SECONDS=close)
    return subprocess.Popen([sys.executable, "-m", "doc_anonymizer.launcher", "--kein-browser"],
                            cwd=ROOT, env=env)


def _state(home: Path, timeout: float = 60) -> dict:
    """Port and token of the started server (waits until it answers)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            state = json.loads((home / "server.json").read_text())
            with _LOCAL.open(f"http://127.0.0.1:{state['port']}/api/ping", timeout=2) as r:
                if json.loads(r.read())["app"] == "dokumenten-anonymisierer":
                    return state
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.3)
    raise AssertionError("Server ist nicht gestartet")


def _request(state: dict, method: str, path: str, body: dict | None = None,
             token: str | None = "") -> int:
    headers = {"X-Anonymizer": "1", "Content-Type": "application/json"}
    token = state["token"] if token == "" else token
    if token:
        headers["X-Anonymizer-Token"] = token
    req = urllib.request.Request(f"http://127.0.0.1:{state['port']}{path}", method=method,
                                 data=json.dumps(body or {}).encode() if method != "GET" else None,
                                 headers=headers)
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
        state = _state(home)
        # A second start finds the running program and ends at once.
        second = _start(home)
        assert second.wait(timeout=30) == 0
        assert server.poll() is None
        assert json.loads((home / "server.json").read_text())["pid"] == server.pid
        # "--beenden" (used by setup/uninstall) ends the program.
        subprocess.run([sys.executable, "-m", "doc_anonymizer.launcher", "--beenden"],
                       cwd=ROOT, env=_env(home), timeout=60, check=True)
        assert server.wait(timeout=30) == 0
        assert not (home / "server.json").exists()
        assert state["port"]
    finally:
        server.kill()


def test_access_needs_the_token(home):
    """Other users/programs on the computer cannot use the running program."""
    server = _start(home)
    try:
        state = _state(home)
        assert len(state["token"]) >= 32
        assert _request(state, "GET", "/api/cases") == 200
        assert _request(state, "GET", "/api/cases", token=None) == 401
        assert _request(state, "GET", "/api/cases", token="falsch") == 401
        assert _request(state, "POST", "/api/shutdown", token=None) == 401
        assert _request(state, "GET", "/api/settings", token=None) == 401
        assert server.poll() is None
        if os.name == "posix":  # the token file is private to the user
            assert (home / "server.json").stat().st_mode & 0o077 == 0
            assert home.stat().st_mode & 0o077 == 0
        # The log never contains the token.
        log = (home / "logs" / "programm.log").read_text(encoding="utf-8")
        assert state["token"] not in log
    finally:
        server.kill()


def test_further_window_gets_a_one_time_ticket(home):
    """A second start asks the running program for a ticket – the address of
    the window never contains the access token."""
    from doc_anonymizer import launcher

    server = _start(home)
    try:
        state = _state(home)
        base = f"http://127.0.0.1:{state['port']}/"
        assert launcher.request_ticket(base, "falsch") is None
        ticket = launcher.request_ticket(base, state["token"])
        assert ticket and ticket != state["token"]

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        browser = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
        with pytest.raises(urllib.error.HTTPError) as first:
            browser.open(f"{base}?ticket={ticket}", timeout=5)
        assert first.value.code == 303 and "anonymisierer_" in first.value.headers["Set-Cookie"]
        with pytest.raises(urllib.error.HTTPError) as again:  # used up
            browser.open(f"{base}?ticket={ticket}", timeout=5)
        assert again.value.code == 401
        # Temporary files live in the private folder, not in the system temp folder.
        assert (home / "tmp").is_dir()
    finally:
        server.kill()


def test_window_uses_its_own_browser_profile(home, monkeypatch):
    """Edge is started with a profile of its own (separate cookies, no extensions)."""
    from doc_anonymizer import launcher

    started = []
    monkeypatch.setattr(launcher, "_find_edge", lambda: "C:/Edge/msedge.exe")
    monkeypatch.setattr(launcher.subprocess, "Popen",
                        lambda args, **kwargs: started.append(args))
    launcher.open_window("http://127.0.0.1:8765/", "einmal-ticket")
    (args,) = started
    assert args[0] == "C:/Edge/msedge.exe"
    assert "--app=http://127.0.0.1:8765/?ticket=einmal-ticket" in args
    assert f"--user-data-dir={home / 'fenster'}" in args
    assert "--disable-extensions" in args and "--no-first-run" in args

    # Without Edge (or if the user prefers a browser tab): the default browser.
    opened = []
    monkeypatch.setattr(launcher, "_find_edge", lambda: None)
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url) or True)
    launcher.open_window("http://127.0.0.1:8765/", "zweites-ticket")
    assert opened == ["http://127.0.0.1:8765/?ticket=zweites-ticket"]


def test_foreign_program_on_the_port_is_not_trusted(home):
    """A program that imitates the ping answer does not get the token."""
    from doc_anonymizer import launcher

    received = []

    class Fake(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            received.append((self.path, dict(self.headers)))
            body = json.dumps({"app": "dokumenten-anonymisierer", "version": "9"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    fake = http.server.HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    try:
        # (DOC_ANONYMIZER_HOME points to ``home`` – see conftest.private_home)
        home.mkdir(parents=True, exist_ok=True)
        (home / "server.json").write_text(json.dumps(
            {"port": fake.server_address[1], "pid": 1, "token": "geheimes-token-1234567890"}))
        assert launcher.running() is None
        assert received, "the fake server was asked"
        assert all("geheimes-token" not in path and "geheimes-token" not in str(headers)
                   for path, headers in received)
    finally:
        fake.shutdown()


def test_ends_itself_without_open_page(home):
    server = _start(home, idle=4, grace=4)
    try:
        state = _state(home)
        # Signs of life keep it running ...
        for _ in range(4):
            assert _request(state, "POST", "/api/heartbeat") == 200
            time.sleep(2)
        assert server.poll() is None
        # ... without them it ends by itself.
        assert server.wait(timeout=40) == 0
    finally:
        server.kill()


def test_ends_soon_after_the_window_was_closed(home):
    server = _start(home, close=3)
    try:
        state = _state(home)
        assert _request(state, "POST", "/api/heartbeat") == 200
        assert _request(state, "POST", "/api/bye") == 200
        # A reload says goodbye as well – the new page's requests cancel it.
        time.sleep(1.5)
        assert _request(state, "POST", "/api/heartbeat") == 200
        time.sleep(4)
        assert server.poll() is None
        assert _request(state, "POST", "/api/bye") == 200
        assert server.wait(timeout=30) == 0
    finally:
        server.kill()


def test_quit_button_endpoint(home):
    server = _start(home)
    try:
        state = _state(home)
        assert _request(state, "POST", "/api/shutdown", {}) == 200
        assert server.wait(timeout=30) == 0
    finally:
        server.kill()


def test_shutdown_not_offered_in_plain_server():
    """Started with "python -m doc_anonymizer start" there is no remote stop."""
    from doc_anonymizer.web import create_app

    client = create_app(start_worker=False).test_client()
    assert client.post("/api/shutdown", headers={"X-Anonymizer": "1"}).status_code == 404
    assert client.get("/api/status").get_json()["managed"] is False
