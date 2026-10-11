"""Start of the installed Windows program (desktop symbol / start menu).

Runs with ``pythonw.exe`` – without a console window:

* If the program is already running, only the browser is opened (no second
  server, no port conflict).
* Otherwise the local server starts on a free port and the browser opens.
* The server ends itself when no browser page has been open for a few
  minutes (the page sends a sign of life every 30 seconds) and no job is
  running. Nothing has to be kept open and nothing has to be closed.
* Problems are shown as a German Windows message; details go to the log
  file ``%LOCALAPPDATA%\\DocAnonymizer\\logs\\programm.log``.

    pythonw -m doc_anonymizer.launcher               # start (or open browser)
    python  -m doc_anonymizer.launcher --beenden     # stop a running instance
    python  -m doc_anonymizer.launcher --kein-browser
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

APP_TITLE = "Dokumenten-Anonymisierer"
#: Ports tried one after another (the first free one is used).
PORTS = range(8765, 8785)
#: The server stops after this many seconds without a sign of life from a
#: browser page (and without a running job).
IDLE_SECONDS = int(os.environ.get("DOC_ANONYMIZER_IDLE_SECONDS", 5 * 60))
#: Before the first page has connected, the browser gets more time.
STARTUP_GRACE_SECONDS = int(os.environ.get("DOC_ANONYMIZER_STARTUP_SECONDS", 10 * 60))

log = logging.getLogger("doc_anonymizer.launcher")

#: Requests to our own server must never go through a proxy configured in Windows.
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ----------------------------------------------------------------- helpers

def _home() -> Path:
    from .config import home_dir

    return home_dir()


def state_file() -> Path:
    """Port and process of the running server."""
    return _home() / "server.json"


def log_file() -> Path:
    folder = _home() / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "programm.log"


def setup_logging() -> None:
    """Log into a file; without a console stdout/stderr do not exist."""
    handler = logging.handlers.RotatingFileHandler(log_file(), maxBytes=1_000_000,
                                                   backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # pythonw.exe: sys.stdout/sys.stderr are None – some libraries write to
    # them anyway, which would crash. Their output goes nowhere instead.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    sys.excepthook = lambda *exc: log.error("Unbehandelter Fehler:\n%s",
                                            "".join(traceback.format_exception(*exc)))
    threading.excepthook = lambda args: log.error(
        "Fehler im Hintergrund (%s):\n%s", args.thread.name if args.thread else "?",
        "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))


def show_message(text: str, error: bool = True) -> None:
    """Windows message box (German); on other systems: print."""
    log.info("Meldung an den Nutzer: %s", text)
    # Automated tests: a dialog nobody can click would block forever.
    if sys.platform.startswith("win") and not os.environ.get("DOC_ANONYMIZER_NO_DIALOG"):
        try:
            import ctypes

            flags = (0x10 if error else 0x40) | 0x10000 | 0x40000  # icon, foreground, topmost
            ctypes.windll.user32.MessageBoxW(None, text, APP_TITLE, flags)
            return
        except Exception:  # noqa: BLE001 - fall back to printing
            pass
    print(f"{APP_TITLE}: {text}", file=sys.__stderr__ or sys.stderr)


def _get(url: str, timeout: float = 2.0) -> dict | None:
    try:
        with _LOCAL.open(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None


def _post(url: str, timeout: float = 5.0) -> bool:
    request = urllib.request.Request(url, data=b"{}", method="POST",
                                     headers={"X-Anonymizer": "1",
                                              "Content-Type": "application/json"})
    try:
        with _LOCAL.open(request, timeout=timeout):
            return True
    except (OSError, urllib.error.URLError):
        return False


def running_url() -> str | None:
    """URL of an already running instance (checked, not just remembered)."""
    try:
        port = int(json.loads(state_file().read_text(encoding="utf-8"))["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    url = f"http://127.0.0.1:{port}/"
    data = _get(url + "api/ping")
    return url if data and data.get("app") == "dokumenten-anonymisierer" else None


def _free_port() -> int:
    for port in PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise PortError()


class PortError(RuntimeError):
    pass


class _InstanceLock:
    """Only one server per user: a lock on a file, held while it runs."""

    def __init__(self) -> None:
        self.handle = None

    def acquire(self) -> bool:
        path = _home() / "server.lock"
        self.handle = open(path, "a+b")  # noqa: SIM115 - kept open while running
        try:
            if sys.platform.startswith("win"):
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.handle.close()
            self.handle = None
            return False


def open_browser(url: str) -> None:
    log.info("Browser öffnen: %s", url)
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - the user can still type the address
        log.exception("Browser konnte nicht geöffnet werden")
        show_message("Der Browser konnte nicht automatisch geöffnet werden.\n\n"
                     f"Bitte im Browser diese Adresse eingeben:\n{url}", error=False)


def _wait_for(predicate, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.3)
    return False


# ----------------------------------------------------------------- server

def serve(open_page: bool = True) -> None:
    """Start the server (this process) and stop it when nobody uses it."""
    from waitress import create_server

    from . import __version__
    from .web import create_app

    port = _free_port()
    stop_event = threading.Event()
    app = create_app(managed=True, on_shutdown=stop_event.set)
    worker = app.extensions["anonymizer"]["worker"]
    server = create_server(app, host="127.0.0.1", port=port, threads=8,
                           max_request_body_size=2 * 1024 ** 3)
    url = f"http://127.0.0.1:{port}/"
    state_file().write_text(json.dumps({"port": port, "pid": os.getpid(),
                                        "version": __version__}), encoding="utf-8")
    log.info("Server gestartet: %s (Version %s, Prozess %s)", url, __version__, os.getpid())

    def watchdog() -> None:
        started = time.monotonic()
        while not stop_event.wait(10):
            life = app.extensions["anonymizer"]["life"]
            seen = life["last_seen"]
            limit = IDLE_SECONDS if life["connected"] else STARTUP_GRACE_SECONDS
            idle = time.monotonic() - (seen or started)
            if idle > limit and not (worker and worker.busy):
                log.info("Seit %d s keine geöffnete Seite – Programm wird beendet.", idle)
                stop_event.set()
        _shutdown()

    def _shutdown() -> None:
        try:
            state_file().unlink(missing_ok=True)
        except OSError:
            pass
        log.info("Programm beendet.")
        logging.shutdown()
        # Ends the process including waitress' threads; open requests have
        # been answered (the shutdown request waits 0.5 s before signalling).
        os._exit(0)

    threading.Thread(target=watchdog, name="watchdog", daemon=True).start()
    if open_page:
        threading.Thread(target=lambda: _wait_for(lambda: running_url() == url, 30)
                         and open_browser(url), name="browser", daemon=True).start()
    server.run()


def stop_running() -> bool:
    """Ask a running instance to end (used by setup/uninstall)."""
    url = running_url()
    if not url:
        return False
    _post(url + "api/shutdown")
    _wait_for(lambda: running_url() is None, 20)
    return True


# ----------------------------------------------------------------- main

def _friendly(exc: BaseException) -> str:
    log_hint = f"\n\nTechnische Details stehen in:\n{log_file()}"
    if isinstance(exc, PortError):
        return ("Das Programm konnte nicht starten, weil alle vorgesehenen Anschlüsse "
                f"(Ports {PORTS.start}–{PORTS.stop - 1}) belegt sind.\n\n"
                "Bitte den Computer neu starten und es noch einmal versuchen." + log_hint)
    if isinstance(exc, ImportError):
        return ("Das Programm ist unvollständig installiert.\n\n"
                "Bitte das Setup „Dokumenten-Anonymisierer-Setup.exe“ noch einmal "
                "ausführen." + log_hint)
    if isinstance(exc, PermissionError):
        return ("Das Programm hat keinen Zugriff auf seinen Datenordner.\n\n"
                "Bitte den Computer neu starten und es noch einmal versuchen." + log_hint)
    return ("Das Programm konnte nicht gestartet werden.\n\n"
            "Bitte den Computer neu starten und es noch einmal versuchen. Hilft das "
            "nicht, schicken Sie bitte die unten genannte Datei an Ihren Ansprechpartner."
            + log_hint)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        setup_logging()
    except Exception:  # noqa: BLE001 - logging must never block the start
        pass
    try:
        if "--beenden" in argv:
            stop_running()  # nothing running is fine as well
            return 0
        open_page = "--kein-browser" not in argv
        url = running_url()
        if url:
            log.info("Läuft bereits – nur Browser öffnen.")
            if open_page:
                open_browser(url)
            return 0
        lock = _InstanceLock()
        if not lock.acquire():
            # Another start is just in progress (double click): wait for it.
            log.info("Start läuft bereits – warte auf den Server.")
            if _wait_for(lambda: running_url() is not None, 90):
                if open_page:
                    open_browser(running_url())
                return 0
            show_message("Das Programm startet bereits. Bitte einen Moment warten und dann "
                         "noch einmal auf das Symbol klicken.", error=False)
            return 1
        serve(open_page)
        return 0
    except Exception as exc:  # noqa: BLE001 - every problem becomes a message
        log.exception("Start fehlgeschlagen")
        show_message(_friendly(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
