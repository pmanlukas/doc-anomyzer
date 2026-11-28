"""Start of the installed Windows program (desktop symbol / start menu).

Runs with ``pythonw.exe`` – without a console window:

* If the program is already running, only its window is opened again (no
  second server, no port conflict).
* Otherwise the local server starts on a free port and the window opens:
  a window of its own (Microsoft Edge in "app" mode, without address bar
  and tabs) or, if Edge is not available, a tab of the default browser.
* Access is protected by a random token created at every start, stored
  for a second start in ``server.json`` inside the user's private
  application folder. The window is opened with a one-time ticket – the
  token itself never appears in an address or a command line.
* The Edge window uses a profile of its own inside the application folder:
  no browser extensions, no synchronisation, and a cookie store that no
  other web page ever sees.
* The server ends itself shortly after the window has been closed, or when
  no page has given a sign of life for a while – never while a job is
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
import secrets
import socket
import subprocess
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
#: page (and without a running job). Browsers put background tabs to sleep,
#: so this is generous; a closed window is noticed much earlier (below).
IDLE_SECONDS = int(os.environ.get("DOC_ANONYMIZER_IDLE_SECONDS", 15 * 60))
#: ... and this many seconds after the last page said it is being closed.
#: (Longer than the sign-of-life interval of a second, still open window.)
CLOSE_GRACE_SECONDS = int(os.environ.get("DOC_ANONYMIZER_CLOSE_SECONDS", 90))
#: Before the first page has connected, the browser gets more time.
STARTUP_GRACE_SECONDS = int(os.environ.get("DOC_ANONYMIZER_STARTUP_SECONDS", 10 * 60))
#: How long a start may take before it counts as failed (older computers
#: with a hard disk need a while after switching on).
START_WAIT_SECONDS = 180

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


def _post(url: str, token: str, body: dict | None = None, timeout: float = 5.0) -> bool:
    request = urllib.request.Request(url, data=json.dumps(body or {}).encode(), method="POST",
                                     headers={"X-Anonymizer": "1",
                                              "X-Anonymizer-Token": token,
                                              "Content-Type": "application/json"})
    try:
        with _LOCAL.open(request, timeout=timeout):
            return True
    except (OSError, urllib.error.URLError):
        return False


def read_state() -> dict | None:
    """Port and token of the server this user started (unverified)."""
    try:
        state = json.loads(state_file().read_text(encoding="utf-8"))
        return {"port": int(state["port"]), "token": str(state["token"]),
                "pid": int(state.get("pid", 0))}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_state(port: int, token: str, version: str) -> None:
    """Store port and token – readable by this user only."""
    path = state_file()
    data = json.dumps({"port": port, "pid": os.getpid(), "version": version, "token": token})
    # 0600 where the system supports it; on Windows the profile folder's
    # access rights already exclude other users. Written next to the file
    # and moved into place: a second start reading at this moment sees the
    # old or the new state, never half of it.
    tmp = path.with_name(f"server.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as fh:
        fh.write(data)
    for attempt in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: somebody is reading the file right now
            if attempt == 39:
                raise
            time.sleep(0.05)


def request_ticket(base_url: str, token: str) -> str | None:
    """Ask the running program for a one-time ticket for a further window."""
    request = urllib.request.Request(
        base_url + "api/ticket", data=b"{}", method="POST",
        headers={"X-Anonymizer": "1", "X-Anonymizer-Token": token,
                 "Content-Type": "application/json"})
    try:
        with _LOCAL.open(request, timeout=5) as resp:
            return str(json.loads(resp.read().decode("utf-8"))["ticket"])
    except (OSError, ValueError, KeyError, urllib.error.URLError):
        return None


def running() -> tuple[str, str] | None:
    """(base URL, token) of an already running instance – verified.

    The answer must prove knowledge of the token (challenge/response), so a
    foreign program that happens to listen on the remembered port is never
    mistaken for this one – and never receives the token.
    """
    from .web import ping_proof

    state = read_state()
    if state is None:
        return None
    url = f"http://127.0.0.1:{state['port']}/"
    challenge = secrets.token_hex(16)
    data = _get(f"{url}api/ping?c={challenge}")
    if not data or data.get("app") != "dokumenten-anonymisierer":
        return None
    if not secrets.compare_digest(str(data.get("proof", "")),
                                  ping_proof(state["token"], challenge)):
        return None
    return url, state["token"]


def running_url() -> str | None:
    """Base URL of the running instance (``None`` if there is none)."""
    found = running()
    return found[0] if found else None


def _listen_socket() -> tuple[socket.socket, int]:
    """Bind the first free port – exclusively.

    On Windows a second program could otherwise bind the same port
    (``SO_REUSEADDR`` semantics) and intercept requests.
    """
    # The usual ports first; if all are taken (many users on one terminal
    # server), any free port the system offers.
    for port in [*PORTS, 0]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if sys.platform.startswith("win"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(("127.0.0.1", port))
            return sock, sock.getsockname()[1]
        except OSError:
            sock.close()
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


def _find_edge() -> str | None:
    """Path of Microsoft Edge (part of Windows 10/11)."""
    if not sys.platform.startswith("win"):
        return None
    candidates = [Path(os.environ.get(var, "")) / "Microsoft" / "Edge" / "Application" /
                  "msedge.exe" for var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")
                  if os.environ.get(var)]
    try:
        import winreg

        key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(root, key) as handle:
                    candidates.insert(0, Path(winreg.QueryValue(handle, None)))
            except OSError:
                continue
    except ImportError:
        pass
    return next((str(path) for path in candidates if path.is_file()), None)


def window_profile() -> Path:
    """Browser profile used only by the program window."""
    return _home() / "fenster"


def open_window(base_url: str, ticket: str) -> None:
    """Show the program: own window if possible, otherwise a browser tab.

    ``ticket``: one-time code that lets the window in (see :mod:`.web`).
    """
    url = f"{base_url}?ticket={ticket}"
    log.info("Fenster öffnen: %s", base_url)  # never log the ticket
    app_window = True
    try:
        from .config import Settings

        app_window = Settings.load().app_window
    except Exception:  # noqa: BLE001 - settings must never block the start
        pass
    edge = _find_edge() if app_window else None
    if edge:
        try:
            # A profile of its own: the access cookie lives in a store no
            # other page of the user's browser can reach, and neither
            # extensions (translators, "AI assistants" …) nor the
            # synchronisation of the everyday profile see the documents.
            subprocess.Popen([edge, f"--app={url}",  # noqa: S603
                              f"--user-data-dir={window_profile()}",
                              "--no-first-run", "--no-default-browser-check",
                              "--disable-extensions", "--disable-sync",
                              "--disable-features=Translate",
                              "--disk-cache-size=1048576", "--window-size=1500,950"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True)
            return
        except OSError:
            log.exception("Edge konnte nicht gestartet werden – Standardbrowser wird benutzt")
    try:
        if not webbrowser.open(url):
            raise OSError("kein Browser gefunden")
    except Exception:  # noqa: BLE001 - the user can still type the address
        log.exception("Browser konnte nicht geöffnet werden")
        show_message("Das Programmfenster konnte nicht automatisch geöffnet werden.\n\n"
                     "Bitte einen Browser (z. B. Microsoft Edge) installieren bzw. als "
                     "Standardbrowser festlegen und das Programm erneut starten.", error=True)


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
    from .config import private_temp_dir
    from .parallel import kill_workers
    from .web import create_app

    sock, port = _listen_socket()
    token = secrets.token_urlsafe(32)
    stop_event = threading.Event()
    # Page images for the text recognition and uploads in transit: in the
    # private folder, emptied at every start and end.
    private_temp_dir(clean=True)
    app = create_app(managed=True, on_shutdown=stop_event.set, token=token)
    worker = app.extensions["anonymizer"]["worker"]
    server = create_server(app, sockets=[sock], threads=8,
                           max_request_body_size=2 * 1024 ** 3)
    url = f"http://127.0.0.1:{port}/"
    write_state(port, token, __version__)
    log.info("Server gestartet: %s (Version %s, Prozess %s)", url, __version__, os.getpid())
    try:
        from .parallel import describe_resources

        log.info("Rechner: %s", describe_resources())
    except Exception:  # noqa: BLE001 - information only
        pass

    def watchdog() -> None:
        started = time.monotonic()
        while not stop_event.wait(5):
            life = app.extensions["anonymizer"]["life"]
            if worker and worker.busy:
                continue  # never end while documents are being processed
            now = time.monotonic()
            closed = life["closed"]
            if closed is not None and now - closed > CLOSE_GRACE_SECONDS:
                log.info("Fenster wurde geschlossen – Programm wird beendet.")
                stop_event.set()
                continue
            limit = IDLE_SECONDS if life["connected"] else STARTUP_GRACE_SECONDS
            idle = now - (life["last_seen"] or started)
            if idle > limit:
                log.info("Seit %d s kein Lebenszeichen einer Seite – Programm wird beendet.",
                         idle)
                stop_event.set()
        _shutdown()

    def _shutdown() -> None:
        try:
            state_file().unlink(missing_ok=True)
        except OSError:
            pass
        # Worker processes must not go on after the program has ended.
        kill_workers()
        try:
            private_temp_dir(clean=True)
        except OSError:
            pass
        log.info("Programm beendet.")
        logging.shutdown()
        # Ends the process including waitress' threads; open requests have
        # been answered (the shutdown request waits 0.5 s before signalling).
        os._exit(0)

    def first_window() -> None:
        if _wait_for(lambda: running_url() == url, START_WAIT_SECONDS):
            open_window(url, app.extensions["anonymizer"]["new_ticket"]())
        else:
            log.error("Der eigene Server antwortet nicht – kein Fenster geöffnet.")
            show_message("Das Programm ist gestartet, antwortet aber nicht.\n\n"
                         "Bitte den Computer neu starten und es noch einmal versuchen."
                         f"\n\nTechnische Details stehen in:\n{log_file()}")
            stop_event.set()

    threading.Thread(target=watchdog, name="watchdog", daemon=True).start()
    if open_page:
        threading.Thread(target=first_window, name="window", daemon=True).start()
    server.run()


def stop_running() -> bool:
    """Ask a running instance to end (used by setup/uninstall)."""
    found = running()
    if not found:
        return False
    url, token = found
    # Used by setup and uninstall: ends the program even while it is working
    # (its files are about to be replaced or removed).
    _post(url + "api/shutdown", token, {"trotzdem": True})
    _wait_for(lambda: running() is None, 20)
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


def _open_further_window(found: tuple[str, str]) -> None:
    """The program runs already: show it (again) in a window."""
    url, token = found
    ticket = request_ticket(url, token)
    if ticket is None:
        show_message("Das Programm läuft bereits, das Fenster konnte aber nicht geöffnet "
                     "werden.\n\nBitte einen Moment warten und noch einmal auf das "
                     "Symbol klicken.")
        return
    open_window(url, ticket)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        setup_logging()
    except Exception:  # noqa: BLE001 - logging must never block the start
        pass
    try:
        from .config import quiet_subprocesses

        quiet_subprocesses()
    except Exception:  # noqa: BLE001 - cosmetic only
        pass
    try:
        if "--beenden" in argv:
            stop_running()  # nothing running is fine as well
            return 0
        open_page = "--kein-browser" not in argv
        found = running()
        if found:
            log.info("Läuft bereits – nur das Fenster öffnen.")
            if open_page:
                _open_further_window(found)
            return 0
        lock = _InstanceLock()
        if not lock.acquire():
            # Another start is just in progress (an impatient second double
            # click – the first start can take a while on an older computer).
            # That start opens the window itself; a second one would only
            # confuse. Just make sure it really comes up.
            log.info("Start läuft bereits – warte auf den Server.")
            if _wait_for(lambda: running() is not None, START_WAIT_SECONDS):
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
