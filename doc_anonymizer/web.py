"""Local web interface (runs only on this computer, 127.0.0.1).

The browser is used purely as a display. Protection, in layers:

* The server listens on the loopback interface only – no other computer in
  the network can reach it.
* **Access token:** every request needs a random secret that only the
  program started by this Windows user knows (cookie set when the program
  opens its window). Other accounts on a shared computer (terminal server)
  and other programs cannot read or change anything. The secret itself is
  never part of an address: the window is opened with a *ticket* that is
  valid once and for a short time (addresses end up in the browser history
  and in the list of running programs).
* Requests must come from the program's own page: the ``Host`` header is
  checked (DNS rebinding), cross-site requests are rejected by the browser's
  ``Sec-Fetch-Site``/``Origin`` headers, mutating requests need a custom
  header, and the pages may not be embedded in frames (clickjacking).
* A strict Content-Security-Policy: no inline scripts, no external content.
"""

from __future__ import annotations

import getpass
import hashlib
import hmac
import io
import logging
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import zipfile
from pathlib import Path

import pymupdf
from flask import Flask, abort, jsonify, redirect, request, send_file, send_from_directory

from . import __version__, learning, ocr
from .config import Settings, default_output_dir, is_cloud_synced
from .detect import ner
from .errors import UserError, friendly_message
from .jobs import CaseStore, Worker, _safe_filename
from .models import Category, ManualBox
from .registry import AUTO_BOX_LABELS, CaseRegistry

log = logging.getLogger(__name__)

#: Areas the program marked by itself (shown to the user for a quick look).
AUTO_KINDS = set(AUTO_BOX_LABELS)

STATIC_DIR = Path(__file__).resolve().parent / "static"
CSRF_HEADER = "X-Anonymizer"
TOKEN_HEADER = "X-Anonymizer-Token"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
#: Largest preview image served to the page (pixels).
MAX_PREVIEW_PIXELS = 16_000_000

_CSP_APP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'")
#: Generated reports are static HTML with an inline style sheet – no scripts.
_CSP_DOCUMENT = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"


class RegistryCache:
    """Keeps the (large) registry of the open case in memory."""

    def __init__(self, store: CaseStore) -> None:
        self.store = store
        self.lock = threading.RLock()
        self._cache: dict[str, tuple[float, CaseRegistry]] = {}

    def get(self, case_id: str) -> CaseRegistry:
        with self.lock:
            case = self.store.load(case_id)
            path = self.store.folder(case_id) / "register.json"
            if not path.exists():
                abort(409, "Die Akte wurde noch nicht analysiert.")
            mtime = path.stat().st_mtime
            cached = self._cache.get(case_id)
            if cached and cached[0] == mtime:
                cached[1].settings = Settings.load()
                return cached[1]
            registry = self.store.load_registry(case, Settings.load())
            self._cache = {case_id: (mtime, registry)}  # only the open case
            return registry

    def save(self, case_id: str, registry: CaseRegistry) -> None:
        with self.lock:
            self.store.save_registry(case_id, registry)
            mtime = (self.store.folder(case_id) / "register.json").stat().st_mtime
            self._cache = {case_id: (mtime, registry)}

    def forget(self) -> None:
        with self.lock:
            self._cache = {}


def ping_proof(token: str, challenge: str) -> str:
    """Answer to the launcher's challenge: proves the server knows the token."""
    return hmac.new(token.encode(), challenge.encode(), hashlib.sha256).hexdigest()


def create_app(store: CaseStore | None = None, start_worker: bool = True,
               managed: bool = False, on_shutdown=None, token: str | None = None) -> Flask:
    """The local web application.

    ``managed``: started by the Windows program (:mod:`.launcher`) – the page
    then sends signs of life and offers a "Beenden" button; ``on_shutdown``
    is called when the user ends the program.

    ``token``: the access secret. Every real start passes one (launcher and
    ``python -m doc_anonymizer start``); ``None`` switches the check off and
    is meant for automated tests only.
    """
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 ** 3  # 2 GB uploads (large case files)
    store = store or CaseStore()
    worker = Worker(store) if start_worker else None
    registries = RegistryCache(store)
    #: last sign of life of a browser page (time.monotonic()); "closed": the
    #: last page said goodbye at that time (window closed).
    #: "page": a browser page that was let in with a ticket has reported
    #: (in contrast to a program using the token directly).
    life = {"last_seen": None, "connected": False, "closed": None, "page": False}
    #: One-time tickets that open the page: code -> end of validity
    #: (time.monotonic(); None = until used).
    tickets: dict[str, float | None] = {}
    tickets_lock = threading.Lock()

    def new_ticket(ttl: float | None = 120.0) -> str:
        """A code that lets a browser in exactly once (see module docstring)."""
        code = secrets.token_urlsafe(24)
        with tickets_lock:
            now = time.monotonic()
            for old, until in list(tickets.items()):
                if until is not None and until < now:
                    del tickets[old]
            tickets[code] = None if ttl is None else now + ttl
        return code

    def redeem(code: str) -> bool:
        with tickets_lock:
            for known in list(tickets):
                if hmac.compare_digest(known.encode(), code.encode("utf-8", "replace")):
                    until = tickets.pop(known)
                    return until is None or until >= time.monotonic()
        return False

    app.extensions["anonymizer"] = {"store": store, "worker": worker, "life": life,
                                    "new_ticket": new_ticket}

    if start_worker:
        # Load the language model in the background: the first analysis then
        # starts without the 5-10 seconds the model needs to load.
        threading.Thread(target=_warm_up, name="warm-up", daemon=True).start()

    # ------------------------------------------------------------ security

    def _cookie_name() -> str:
        # Cookies are shared between all ports of 127.0.0.1 – one name per port.
        port = (request.host or "").rsplit(":", 1)[-1]
        return f"anonymisierer_{port if port.isdigit() else '0'}"

    def _authorised() -> bool:
        supplied = request.cookies.get(_cookie_name()) or request.headers.get(TOKEN_HEADER, "")
        return bool(supplied) and hmac.compare_digest(supplied.encode("utf-8", "replace"),
                                                      token.encode())

    @app.before_request
    def _guard():
        host = (request.host or "").rsplit(":", 1)[0].strip("[]")
        if host not in ALLOWED_HOSTS:
            abort(403)
        path = request.path
        is_api = path.startswith("/api/")
        # Requests sent by other web sites: the browser marks them.
        site = request.headers.get("Sec-Fetch-Site")
        if is_api and site not in (None, "same-origin", "none"):
            abort(403)
        origin = request.headers.get("Origin")
        if origin and origin not in (f"http://{request.host}",):
            abort(403)
        if request.method not in ("GET", "HEAD", "OPTIONS") and \
                request.headers.get(CSRF_HEADER) != "1":
            abort(403)
        if token is not None:
            if path == "/" and request.args.get("ticket"):
                # The program opens its window with a one-time ticket; it is
                # exchanged for the cookie and removed from the address.
                resp = redirect("/", code=303)
                if redeem(request.args["ticket"]):
                    resp.set_cookie(_cookie_name(), token, httponly=True, samesite="Strict",
                                    path="/")
                elif not _authorised():  # (a repeated request of the same window is fine)
                    return _locked()
                return resp
            public = path in ("/api/ping", "/favicon.ico") or path.startswith("/static/")
            if not public and not _authorised():
                if is_api:
                    return jsonify(error="Bitte das Programm über das Symbol "
                                         "„Dokumenten-Anonymisierer“ neu öffnen.",
                                   locked=True), 401
                return _locked()
        if is_api and path not in ("/api/ping", "/api/bye"):
            # Any request of the page counts as a sign of life (not the
            # launcher's "is it running?" ping).
            life["last_seen"] = time.monotonic()
            life["connected"] = True
            life["closed"] = None
            if token is not None and request.cookies.get(_cookie_name()):
                life["page"] = True
        return None

    def _locked():
        resp = send_from_directory(STATIC_DIR, "gesperrt.html")
        resp.status_code = 401
        return resp

    @app.after_request
    def _headers(resp):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        resp.headers.setdefault("Content-Security-Policy", _CSP_APP)
        return resp

    @app.errorhandler(Exception)
    def _error(exc):
        from werkzeug.exceptions import HTTPException

        if isinstance(exc, HTTPException):
            return jsonify(error=exc.description or exc.name), exc.code
        if isinstance(exc, KeyError):
            return jsonify(error="Nicht gefunden"), 404
        if isinstance(exc, UserError):
            return jsonify(error=str(exc)), 400
        if isinstance(exc, (ValueError, TypeError)):
            # Where it happened, but not the message (may quote what was typed).
            log.warning("bad request (%s):\n%s", exc.__class__.__name__,
                        "".join(traceback.format_tb(exc.__traceback__)))
            return jsonify(error="Ungültige Anfrage."), 400
        log.exception("request failed")
        return jsonify(error=friendly_message(exc)), 500

    def _submit(case_id: str, action: str) -> None:
        if worker is not None:
            worker.submit(case_id, action)

    def _json() -> dict:
        payload = request.get_json(force=True, silent=True)
        return payload if isinstance(payload, dict) else {}

    def _editable(case_id: str) -> CaseRegistry:
        """The register of a case for a change by the user.

        Not while the case is being processed: the change would come too
        late for the running job and be overwritten when it ends. Callers
        hold ``registries.lock`` from here until the change is saved, so a
        job never starts in the middle of a change.
        """
        if store.load(case_id).status in ("analyse", "schwaerzen"):
            abort(409, "Die Akte wird gerade verarbeitet – Änderungen sind erst danach "
                       "wieder möglich.")
        return registries.get(case_id)

    def _document(path: str | Path, **kwargs):
        """Send a generated file (report, PDF …) with the strict document CSP."""
        resp = send_file(path, **kwargs)
        resp.headers["Content-Security-Policy"] = _CSP_DOCUMENT
        return resp

    # -------------------------------------------------------------- static

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/favicon.ico")
    def favicon():
        return send_from_directory(STATIC_DIR, "icon.svg", mimetype="image/svg+xml")

    @app.get("/static/<path:name>")
    def static_files(name: str):
        return send_from_directory(STATIC_DIR, name)

    # -------------------------------------------------------- status/settings

    @app.get("/api/ping")
    def ping():
        """Cheap check for the launcher: is *this program* listening here?

        With ``?c=<random>`` the answer contains a proof that the server
        knows the access token – another program listening on the port
        cannot pretend to be this one.
        """
        data = {"app": "dokumenten-anonymisierer", "version": __version__}
        challenge = request.args.get("c", "")
        if token is not None and 8 <= len(challenge) <= 128:
            data["proof"] = ping_proof(token, challenge)
        return jsonify(data)

    @app.post("/api/ticket")
    def ticket():
        """A one-time ticket for a further window (asked for by the launcher)."""
        if token is None:
            abort(404)
        return jsonify(ticket=new_ticket())

    @app.post("/api/heartbeat")
    def heartbeat():
        busy = bool(worker and worker.busy)
        return jsonify(ok=True, busy=busy)

    @app.post("/api/bye")
    def bye():
        """The page is being closed (window closed or reloaded)."""
        life["closed"] = time.monotonic()
        return jsonify(ok=True)

    @app.post("/api/shutdown")
    def shutdown():
        """End the program (only when started by the Windows program)."""
        if not managed or on_shutdown is None:
            abort(404)
        if worker is not None and worker.busy and not _json().get("trotzdem"):
            return jsonify(ok=False, busy=True,
                           error="Es läuft gerade noch eine Verarbeitung."), 409
        # Answer first, then end.
        threading.Timer(0.5, on_shutdown).start()
        return jsonify(ok=True)

    @app.get("/api/status")
    def status():
        settings = Settings.load()
        return jsonify(version=__version__, spacy=ner.spacy_installed(),
                       tesseract=ocr.is_available(),
                       tesseract_languages=ocr.available_languages(),
                       gliner=ner.gliner_installed(), platform=sys.platform, managed=managed,
                       output_dir=settings.output_dir, keep_days=settings.keep_days,
                       cloud=is_cloud_synced(settings.output_dir), user=_user_name(),
                       page_connected=life["page"])

    @app.get("/api/settings")
    def get_settings():
        settings = Settings.load()
        data = settings.__dict__.copy()
        data["category_labels"] = {c.value: c.label_de for c in Category
                                   if c is not Category.MANUAL}
        data["default_output_dir"] = str(default_output_dir())
        return jsonify(data)

    @app.post("/api/settings")
    def set_settings():
        payload = _json()
        settings = Settings.load()
        for key in ("use_gliner", "deep_verification", "write_mapping", "use_learning",
                    "thorough_ocr", "remove_small_images", "forensic_check", "auto_train",
                    "detect_signatures", "app_window"):
            if key in payload:
                setattr(settings, key, bool(payload[key]))
        if "categories" in payload:
            valid = {c.value for c in Category if c is not Category.MANUAL}
            settings.categories = [c for c in payload["categories"] or [] if c in valid]
        if payload.get("security_mode") in ("standard", "maximum"):
            settings.security_mode = payload["security_mode"]
        if "keep_days" in payload:
            settings.keep_days = max(0, min(3650, int(payload["keep_days"] or 0)))
        if "allow_terms" in payload:
            settings.allow_terms = [t.strip()[:200] for t in payload["allow_terms"] or []
                                    if isinstance(t, str) and t.strip()][:5000]
        if "deny_terms" in payload:
            valid_all = {c.value for c in Category}
            terms = []
            for item in payload["deny_terms"] or []:
                if isinstance(item, dict) and str(item.get("text", "")).strip():
                    category = item.get("category") if item.get("category") in valid_all \
                        else Category.MANUAL.value
                    terms.append({"text": str(item["text"]).strip()[:200], "category": category})
            settings.deny_terms = terms[:5000]
        if "output_dir" in payload:
            settings.output_dir = str(_usable_folder(str(payload["output_dir"] or "")))
        settings.save()
        warning = ""
        if is_cloud_synced(settings.output_dir):
            warning = ("Achtung: Der Speicherort scheint mit einem Cloud-Dienst synchronisiert "
                       "zu werden (z. B. OneDrive). Die Ergebnisse und die vertrauliche "
                       "Zuordnungstabelle würden dann dorthin hochgeladen.")
        return jsonify(ok=True, warning=warning, output_dir=settings.output_dir)

    # --------------------------------------------------------- folder picker

    @app.get("/api/folders")
    def folders():
        """Sub-folders of a folder – for choosing where results are saved."""
        return jsonify(_folder_listing(request.args.get("path", "")))

    @app.post("/api/folders")
    def create_folder():
        payload = _json()
        parent = Path(str(payload.get("parent", ""))).expanduser()
        name = _safe_filename(str(payload.get("name", "")).strip(), limit=60)
        if not parent.is_absolute() or not parent.is_dir():
            abort(400, "Der übergeordnete Ordner wurde nicht gefunden.")
        if not str(payload.get("name", "")).strip():
            abort(400, "Bitte einen Namen für den neuen Ordner eingeben.")
        target = parent / name
        try:
            target.mkdir(exist_ok=True)
        except OSError:
            abort(400, "Der Ordner konnte hier nicht angelegt werden (keine Berechtigung?).")
        return jsonify(_folder_listing(str(target)))

    # ---------------------------------------------------------------- cases

    @app.get("/api/cases")
    def list_cases():
        return jsonify([_case_summary(c) for c in store.list()])

    @app.post("/api/cases")
    def create_case():
        title = str(_json().get("title", ""))
        title = "".join(ch for ch in title if ch.isprintable()).strip()[:150]
        case = store.create(title)
        return jsonify(_case_summary(case))

    @app.delete("/api/cases")
    def delete_all_cases():
        """Delete the working data of all cases that are not being processed."""
        deleted = 0
        for case in store.list():
            if case.status not in ("analyse", "schwaerzen"):
                store.delete(case.id)
                deleted += 1
        registries.forget()
        return jsonify(ok=True, deleted=deleted)

    @app.get("/api/cases/<case_id>")
    def get_case(case_id: str):
        data = store.load(case_id).to_dict()
        data["summary"] = _case_summary(store.load(case_id))
        return jsonify(data)

    @app.delete("/api/cases/<case_id>")
    def delete_case(case_id: str):
        case = store.load(case_id)
        if case.status in ("analyse", "schwaerzen"):
            abort(409, "Die Akte wird gerade bearbeitet. Bitte zuerst abbrechen.")
        with registries.lock:
            store.delete(case_id)
            registries.forget()
        return jsonify(ok=True)

    @app.post("/api/cases/<case_id>/cancel")
    def cancel(case_id: str):
        """Stop the running analysis/anonymisation of a case."""
        case = store.load(case_id)
        if case.status in ("analyse", "schwaerzen") and worker is not None:
            worker.cancel(case_id)
        return jsonify(ok=True)

    @app.post("/api/cases/<case_id>/documents")
    def upload(case_id: str):
        case = store.load(case_id)
        if case.status not in ("neu", "fehler"):
            abort(409, "Dokumente können nur vor der Analyse hinzugefügt werden.")
        files = request.files.getlist("files")
        added, rejected = [], []
        for file in files:
            head = file.stream.read(1024)
            file.stream.seek(0)
            # The PDF header may be preceded by a few bytes of other data.
            if b"%PDF-" not in head:
                rejected.append(file.filename or "?")
                continue
            added.append(store.add_document(case, file.filename or "Dokument.pdf",
                                            file.stream).name)
        return jsonify(added=added, rejected=rejected)

    @app.post("/api/cases/<case_id>/analyse")
    def analyse(case_id: str):
        case = store.load(case_id)
        if not case.documents:
            abort(400, "Bitte zuerst PDF-Dateien hinzufügen.")
        if case.status in ("analyse", "schwaerzen"):
            abort(409, "Die Akte wird gerade bearbeitet.")
        with registries.lock:
            case.status = "analyse"
            case.error = ""
            case.progress = {"done": 0, "total": 1, "step": "Wartet …"}
            store.save(case)
        _submit(case_id, "analyse")
        return jsonify(ok=True)

    @app.post("/api/cases/<case_id>/redact")
    def redact(case_id: str):
        case = store.load(case_id)
        if case.status not in ("pruefung", "fertig", "fehler") or \
                not (store.folder(case_id) / "register.json").exists():
            abort(409, "Die Akte ist noch nicht bereit.")
        # From here on the review is closed (see _editable): what the user
        # sees when clicking is exactly what gets anonymised.
        with registries.lock:
            case.status = "schwaerzen"
            case.error = ""
            case.progress = {"done": 0, "total": 1, "step": "Wartet …"}
            store.save(case)
        _submit(case_id, "redact")
        return jsonify(ok=True)

    @app.post("/api/cases/<case_id>/review")
    def back_to_review(case_id: str):
        case = store.load(case_id)
        if case.status in ("fertig", "fehler") and \
                (store.folder(case_id) / "register.json").exists():
            case.status = "pruefung"
            store.save(case)
        return jsonify(ok=True)

    # ------------------------------------------------------------- entities

    @app.get("/api/cases/<case_id>/entities")
    def entities(case_id: str):
        registry = registries.get(case_id)
        pages: dict[str, set] = {}
        for m in registry.mentions:
            pages.setdefault(m.entity_id, set()).add((m.doc, m.page))
        result = []
        for e in registry.entities.values():
            if e.count == 0:
                continue
            first = min(pages.get(e.id, {(0, 0)}))
            item = {
                "id": e.id, "category": e.category.value, "category_label": e.category.label_de,
                "original": e.original, "variants": e.variants, "replacement": e.replacement,
                "short": e.forms.get("surname", ""), "enabled": e.enabled, "count": e.count,
                "sources": e.sources, "first": {"doc": first[0], "page": first[1]},
                "uncertain": e.uncertain, "decided": e.decided, "score": round(e.score, 2),
            }
            if e.uncertain and not e.decided:
                # The sentence around the finding: usually enough to decide
                # without looking for the place in the document.
                contexts = registry.contexts(e, limit=1, width=70)
                item["context"] = contexts[0] if contexts else ""
            result.append(item)
        return jsonify(result)

    @app.patch("/api/cases/<case_id>/entities/<entity_id>")
    def update_entity(case_id: str, entity_id: str):
        payload = _json()
        with registries.lock:
            registry = _editable(case_id)
            entity = registry.entities[entity_id]
            if "enabled" in payload:
                changed = entity.enabled != bool(payload["enabled"])
                entity.enabled = bool(payload["enabled"])
                if changed or payload.get("decided"):
                    _learn(registry, entity, "unsicher" if entity.uncertain else "korrektur")
            if payload.get("decided"):
                entity.decided = True
            if "replacement" in payload and str(payload["replacement"]).strip():
                entity.replacement = _label(payload["replacement"], 80)
                entity.forms["edited"] = "1"
                if entity.category is Category.PERSON:
                    # Short forms ("Herr M.") follow the edited full label.
                    short = _label(payload.get("short") or entity.replacement, 80)
                    entity.forms.update(surname=short, given=short,
                                        inverted=entity.replacement)
            registries.save(case_id, registry)
        return jsonify(ok=True)

    @app.post("/api/cases/<case_id>/entities")
    def add_entity(case_id: str):
        payload = _json()
        text = " ".join(str(payload.get("text", "")).split())[:300]
        try:
            category = Category(payload.get("category", "MANUAL"))
        except ValueError:
            abort(400, "Unbekannte Kategorie")
        if len(text) < 2:
            abort(400, "Bitte mindestens zwei Zeichen eingeben.")
        with registries.lock:
            registry = _editable(case_id)
            entity = registry.add_term(text, category)
            if entity is not None:
                _learn(registry, entity, "hinzugefuegt")
            if payload.get("always"):
                settings = Settings.load()
                if not any(t.get("text") == text for t in settings.deny_terms):
                    settings.deny_terms.append({"text": text, "category": category.value})
                    settings.save()
            registries.save(case_id, registry)
        return jsonify(id=entity.id if entity else None,
                       count=entity.count if entity else 0)

    @app.post("/api/cases/<case_id>/entities/<entity_id>/never")
    def never_redact(case_id: str, entity_id: str):
        with registries.lock:
            registry = _editable(case_id)
            entity = registry.entities[entity_id]
            entity.enabled = False
            entity.decided = True
            _learn(registry, entity, "liste")
            settings = Settings.load()
            if entity.original not in settings.allow_terms:
                settings.allow_terms.append(entity.original)
                settings.save()
            registries.save(case_id, registry)
        return jsonify(ok=True)

    @app.get("/api/cases/<case_id>/attention")
    def attention(case_id: str):
        """Open decisions: uncertain findings and poorly readable pages."""
        registry = registries.get(case_id)
        open_items = [e.id for e in registry.entities.values()
                      if e.uncertain and not e.decided and e.count]
        pages = [{"doc": d, "page": p.index, "quality": round(p.ocr_quality or 0),
                  "reviewed": f"{d}:{p.index}" in registry.reviewed_pages}
                 for d, doc_pages in enumerate(registry.docs) for p in doc_pages
                 if p.poorly_readable]
        # Automatically found signatures, handwriting, stamps and images: the
        # user should glance at them (one click keeps an area visible).
        areas = [{"index": idx, "doc": b.doc, "page": b.page, "kind": b.kind,
                  "label": b.label}
                 for idx, b in enumerate(registry.manual_boxes) if b.kind in AUTO_KINDS]
        return jsonify(uncertain_open=len(open_items), pages=pages, areas=areas)

    @app.post("/api/cases/<case_id>/pages/reviewed")
    def page_reviewed(case_id: str):
        payload = _json()
        key = f"{int(payload['doc'])}:{int(payload['page'])}"
        with registries.lock:
            registry = _editable(case_id)
            if key not in registry.reviewed_pages:
                registry.reviewed_pages.append(key)
            registries.save(case_id, registry)
        return jsonify(ok=True)

    @app.post("/api/cases/<case_id>/suspects")
    def decide_suspect(case_id: str):
        """Decision about a finding of the residual scan (after anonymising)."""
        payload = _json()
        text = " ".join(str(payload.get("text", "")).split())[:300]
        try:
            category = Category(payload.get("category", "OTHER_NAME"))
        except ValueError:
            category = Category.OTHER_NAME
        if not text:
            abort(400, "Kein Text angegeben.")
        if payload.get("redact"):
            with registries.lock:
                registry = _editable(case_id)
                entity = registry.add_term(text, category)
                if entity is not None:
                    _learn(registry, entity, "nachkontrolle")
                registries.save(case_id, registry)
        elif Settings.load().use_learning:
            learning.record(learning.Decision(text, category.value, "behalten", "nachkontrolle"))
        return jsonify(ok=True)

    @app.get("/api/learning")
    def learning_stats():
        from .train import custom_model_path

        data = learning.statistics()
        training = worker.training if worker is not None else {"running": False, "message": ""}
        data.update(training=training, custom_model=custom_model_path() is not None)
        return jsonify(data)

    @app.post("/api/learning/train")
    def learning_train():
        """Fine-tune the local language model (queued behind running cases)."""
        if worker is None:
            abort(503)
        if worker.training.get("running"):
            abort(409, "Das Training läuft bereits.")
        worker.training = {"running": True, "message": "Training ist eingeplant …"}
        worker.submit("", "train")
        return jsonify(ok=True)

    @app.post("/api/learning/clear")
    def learning_clear():
        """Forget everything learned: the decisions *and* what was derived
        from them (trained language model, signature model)."""
        from .train import reset

        if worker is not None and worker.training.get("running"):
            abort(409, "Das Programm lernt gerade – bitte danach noch einmal versuchen.")
        learning.clear()
        reset()
        return jsonify(ok=True)

    # ---------------------------------------------------------------- pages

    def _doc_info(case_id: str, doc: int):
        case = store.load(case_id)
        if not 0 <= doc < len(case.documents):
            abort(404)
        return case.documents[doc]

    def _doc_path(case_id: str, doc: int, output: bool = False) -> Path:
        info = _doc_info(case_id, doc)
        if output:
            if not info.output or not Path(info.output).exists():
                abort(404, "Noch kein Ergebnis vorhanden.")
            return Path(info.output)
        return store.folder(case_id) / info.stored

    def _render(path: Path, page_no: int):
        try:
            zoom = min(max(float(request.args.get("zoom", 1.5)), 0.3), 4.0)
        except ValueError:
            zoom = 1.5
        with pymupdf.open(path) as pdf:
            if not 0 <= page_no < pdf.page_count:
                abort(404)
            page = pdf[page_no]
            # Very large pages are shown smaller instead of using up the memory.
            pixels = page.rect.width * page.rect.height * zoom * zoom
            if pixels > MAX_PREVIEW_PIXELS:
                zoom *= (MAX_PREVIEW_PIXELS / pixels) ** 0.5
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            return send_file(io.BytesIO(pix.tobytes("png")), mimetype="image/png")

    @app.get("/api/cases/<case_id>/docs/<int:doc>/pages/<int:page>.png")
    def page_image(case_id: str, doc: int, page: int):
        return _render(_doc_path(case_id, doc), page)

    @app.get("/api/cases/<case_id>/docs/<int:doc>/output/pages/<int:page>.png")
    def output_image(case_id: str, doc: int, page: int):
        return _render(_doc_path(case_id, doc, output=True), page)

    @app.get("/api/cases/<case_id>/docs/<int:doc>/pages/<int:page>/boxes")
    def page_boxes(case_id: str, doc: int, page: int):
        """Highlight rectangles for the review screen (in view coordinates)."""
        registry = registries.get(case_id)
        if not 0 <= doc < len(registry.docs) or not 0 <= page < len(registry.docs[doc]):
            abort(404)
        info = registry.docs[doc][page]
        with pymupdf.open(_doc_path(case_id, doc)) as pdf:
            pg = pdf[page]
            matrix = pg.rotation_matrix
            width, height = pg.rect.width, pg.rect.height
        boxes = []
        for m in registry.mentions:
            if m.doc != doc or m.page != page:
                continue
            entity = registry.entities[m.entity_id]
            rect = None
            for i in m.word_indices:
                w = info.words[i]
                r = pymupdf.Rect(w.x0, w.y0, w.x1, w.y1) * matrix
                rect = r if rect is None else rect | r
            if rect is not None:
                boxes.append({"entity": entity.id, "category": entity.category.value,
                              "enabled": entity.enabled,
                              "label": registry.replacement_for(m),
                              "rect": [rect.x0, rect.y0, rect.x1, rect.y1]})
        manual = []
        for idx, b in enumerate(registry.manual_boxes):
            if b.doc == doc and b.page == page:
                r = pymupdf.Rect(b.x0, b.y0, b.x1, b.y1) * matrix
                manual.append({"index": idx, "label": b.label, "kind": b.kind,
                               "rect": [r.x0, r.y0, r.x1, r.y1]})
        return jsonify(width=width, height=height, kind=info.kind, boxes=boxes,
                       manual=manual, pages=len(registry.docs[doc]),
                       quality=round(info.ocr_quality) if info.ocr_quality is not None else None,
                       poorly_readable=info.poorly_readable)

    @app.post("/api/cases/<case_id>/manual")
    def add_manual(case_id: str):
        payload = _json()
        doc, page = int(payload["doc"]), int(payload["page"])
        rect = [float(v) for v in payload["rect"]]
        if len(rect) != 4:
            abort(400, "Ungültiger Bereich.")
        label = _label(payload.get("label") or "[geschwärzt]", 40)
        with registries.lock:
            registry = _editable(case_id)
            if not 0 <= doc < len(registry.docs) or not 0 <= page < len(registry.docs[doc]):
                abort(404)
            with pymupdf.open(_doc_path(case_id, doc)) as pdf:
                pg = pdf[page]
                derot = pg.derotation_matrix
                r = pymupdf.Rect(*rect) & pymupdf.Rect(0, 0, pg.rect.width, pg.rect.height)
            r = r * derot
            r.normalize()
            if r.is_empty or r.width < 2 or r.height < 2:
                abort(400, "Der Bereich ist zu klein.")
            registry.manual_boxes.append(ManualBox(doc, page, r.x0, r.y0, r.x1, r.y1, label))
            registries.save(case_id, registry)
        return jsonify(ok=True)

    @app.delete("/api/cases/<case_id>/manual/<int:index>")
    def delete_manual(case_id: str, index: int):
        with registries.lock:
            registry = _editable(case_id)
            if not 0 <= index < len(registry.manual_boxes):
                abort(404)
            box = registry.manual_boxes.pop(index)
            if box.features and Settings.load().use_learning:
                # "Not a signature": a negative example for the signature model.
                learning.record_signatures([(box.features, 0)])
            registries.save(case_id, registry)
        return jsonify(ok=True)

    # --------------------------------------------------------------- results

    def _result_file(case_id: str, doc: int, kind: str) -> Path:
        info = _doc_info(case_id, doc)
        path = {"pdf": info.output, "txt": info.text_output, "report": info.report}.get(kind)
        if not path or not Path(path).exists():
            abort(404, "Datei nicht vorhanden.")
        return Path(path)

    @app.get("/api/cases/<case_id>/docs/<int:doc>/download/<kind>")
    def download(case_id: str, doc: int, kind: str):
        path = _result_file(case_id, doc, kind)
        inline = request.args.get("inline") == "1"
        return _document(path, as_attachment=not inline, download_name=path.name)

    @app.post("/api/cases/<case_id>/docs/<int:doc>/open/<kind>")
    def open_result(case_id: str, doc: int, kind: str):
        """Open a finished file with the program Windows uses for it."""
        _open_in_file_manager(_result_file(case_id, doc, kind))
        return jsonify(ok=True)

    @app.get("/api/cases/<case_id>/zip")
    def download_zip(case_id: str):
        """All anonymised files of a case in one ZIP.

        Never included: the confidential mapping table and documents that
        failed the verification.
        """
        case = store.load(case_id)
        # Built in a temporary file: hundreds of documents do not fit in memory.
        buffer = tempfile.SpooledTemporaryFile(max_size=64 * 1024 ** 2)  # noqa: SIM115
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for doc in case.documents:
                if not (doc.verification or {}).get("passed"):
                    continue
                for path in (doc.output, doc.text_output, doc.report):
                    if path and Path(path).exists():
                        archive.write(path, Path(path).name)
        buffer.seek(0)
        name = Path(case.output_dir).name if case.output_dir else "Anonymisiert"
        return send_file(buffer, mimetype="application/zip", as_attachment=True,
                         download_name=f"{name}.zip")

    @app.post("/api/cases/<case_id>/accept-all")
    def accept_all(case_id: str):
        """Take over all open proposals as shown (and mark scan pages as viewed)."""
        with registries.lock:
            registry = _editable(case_id)
            for entity in registry.entities.values():
                if entity.uncertain and not entity.decided:
                    entity.decided = True  # not recorded as learning signal (not looked at)
            if _json().get("pages"):
                for d, doc_pages in enumerate(registry.docs):
                    for page in doc_pages:
                        key = f"{d}:{page.index}"
                        if page.poorly_readable and key not in registry.reviewed_pages:
                            registry.reviewed_pages.append(key)
            registries.save(case_id, registry)
        return jsonify(ok=True)

    @app.get("/api/cases/<case_id>/mapping")
    def mapping(case_id: str):
        case = store.load(case_id)
        if not case.mapping or not Path(case.mapping).exists():
            abort(404)
        return _document(case.mapping)

    @app.post("/api/cases/<case_id>/open-folder")
    def open_folder(case_id: str):
        case = store.load(case_id)
        folder = case.output_dir
        if not folder or not Path(folder).is_dir():
            abort(404, "Der Ordner ist nicht mehr vorhanden.")
        _open_in_file_manager(Path(folder))
        return jsonify(ok=True, folder=folder)

    return app


# ------------------------------------------------------------------ helpers

def _warm_up() -> None:
    try:
        ner.load_spacy()
        ner.prepare_known_words()
    except Exception as exc:  # noqa: BLE001 - reported where the model is needed
        log.info("warm-up skipped: %s", exc.__class__.__name__)


def _label(value, limit: int) -> str:
    """A replacement/label typed by the user: one line, printable, bounded."""
    text = "".join(ch for ch in str(value) if ch.isprintable())
    return " ".join(text.split())[:limit]


def _learn(registry: CaseRegistry, entity, origin: str) -> None:
    """Store the user's decision about ``entity`` for future cases."""
    if not registry.settings.use_learning:
        return
    learning.record(learning.Decision(
        text=entity.original, category=entity.category.value,
        decision="schwaerzen" if entity.enabled else "behalten", origin=origin,
        sources=list(entity.sources), contexts=registry.contexts(entity)))


def _user_name() -> str:
    """Windows account the program runs for (shown in the interface, so
    nobody works in a colleague's program by mistake on a shared computer)."""
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - not important enough to fail
        return ""


def _case_summary(case) -> dict:
    processed = [d for d in case.documents if not d.error]
    failed_docs = sum(1 for d in case.documents if d.error)
    not_passed = sum(1 for d in processed if not (d.verification or {}).get("passed"))
    # Verification stages that could not be carried out (not the same as passed).
    unchecked = sum(1 for d in processed if (d.verification or {}).get("passed")
                    and d.verification.get("complete") is False)
    done = case.status == "fertig"
    return {"id": case.id, "title": case.title, "created": case.created,
            "status": case.status, "documents": len(case.documents),
            "pages": sum(d.pages for d in case.documents), "progress": case.progress,
            "error": case.error, "unreadable": failed_docs,
            "not_passed": not_passed if done else None,
            "unchecked": unchecked if done else None,
            "has_folder": bool(case.output_dir) and Path(case.output_dir).is_dir(),
            "reviewable": case.status == "fehler" and any(d.pages for d in case.documents),
            "passed": (not_passed == 0 and bool(processed)) if done else None}


def _usable_folder(text: str) -> Path:
    """The folder for results chosen by the user – must exist or be creatable."""
    text = text.strip().strip('"')
    if not text:
        raise UserError("Bitte einen Speicherort angeben.")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise UserError("Bitte einen vollständigen Pfad angeben, z. B. C:\\Akten\\Anonymisiert.")
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=path):
            pass
    except OSError as exc:
        raise UserError("In diesen Ordner kann nicht gespeichert werden (nicht vorhanden "
                        "oder keine Schreibberechtigung). Bitte einen anderen wählen.") from exc
    return path


def _hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith((".", "$")):
        return True
    try:
        attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attributes & 0x2) or bool(attributes & 0x4)  # hidden / system


def _folder_listing(text: str) -> dict:
    """Sub-folders of ``text`` plus shortcuts to the usual places."""
    home = Path.home()
    path = Path(text.strip().strip('"')).expanduser() if text.strip() else None
    if path is None or not path.is_absolute():
        path = Path(Settings.load().output_dir).expanduser()
    # Walk up to the nearest existing folder (the default may not exist yet).
    while not path.is_dir() and path.parent != path:
        path = path.parent
    if not path.is_dir():
        path = home
    names: list[str] = []
    readable = True
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    if entry.is_dir() and not _hidden(entry):
                        names.append(entry.name)
                except OSError:
                    continue
    except OSError:
        readable = False
    names.sort(key=str.casefold)
    shortcuts = [{"label": "Anonymisiert (Standard)", "path": str(default_output_dir())},
                 {"label": "Eigener Benutzerordner", "path": str(home)}]
    for label, name in (("Desktop", "Desktop"), ("Dokumente", "Documents"),
                        ("Downloads", "Downloads")):
        if (home / name).is_dir():
            shortcuts.append({"label": label, "path": str(home / name)})
    for drive in _drives():
        shortcuts.append({"label": f"Laufwerk {drive[:2]}", "path": drive})
    try:
        writable = os.access(path, os.W_OK)
    except OSError:
        writable = False
    return {"path": str(path), "parent": str(path.parent) if path.parent != path else None,
            "folders": [{"name": n, "path": str(path / n)} for n in names[:1000]],
            "truncated": len(names) > 1000, "readable": readable, "writable": writable,
            "cloud": is_cloud_synced(path), "shortcuts": shortcuts}


def _drives() -> list[str]:
    """Drive letters of this computer ("C:\\", …; empty on other systems).

    Asked from Windows in one call – probing every letter would block for
    seconds on disconnected network drives.
    """
    if not sys.platform.startswith("win"):
        return []
    try:
        import ctypes

        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:  # noqa: BLE001
        return ["C:\\"]
    return [f"{chr(65 + i)}:\\" for i in range(26) if mask >> i & 1]


#: Results the program writes – only these are opened with their default program.
_OPENABLE = {".pdf", ".txt", ".html"}


def _open_in_file_manager(path: Path) -> None:
    """Open a folder/file with Explorer or the default program (local desktop)."""
    if not path.is_dir() and path.suffix.lower() not in _OPENABLE:
        raise UserError("Dieser Dateityp wird nicht geöffnet.")
    try:
        if sys.platform.startswith("win") and path.is_dir():
            # Explorer itself – never "run" something that only looks like a folder.
            explorer = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "explorer.exe"
            subprocess.Popen([str(explorer), str(path)])  # noqa: S603
        elif sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])  # noqa: S603, S607
        else:
            subprocess.Popen(["xdg-open", str(path)],  # noqa: S603, S607
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise UserError("Das konnte nicht geöffnet werden. Sie finden die Datei im "
                        f"Ordner „{path.parent}“.") from exc
