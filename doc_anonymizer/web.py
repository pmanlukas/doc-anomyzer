"""Local web interface (runs only on this computer, 127.0.0.1).

The browser is used purely as a display; the server listens exclusively on
the loopback interface, so no other computer in the network can reach it.
Mutating requests must carry a custom header, which browsers do not allow
foreign web sites to send – this protects against cross-site requests.
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

import pymupdf
from flask import Flask, abort, jsonify, request, send_file, send_from_directory

from . import __version__, learning, ocr
from .config import Settings, is_cloud_synced
from .detect import ner
from .jobs import CaseStore, Worker
from .models import Category, ManualBox
from .registry import CaseRegistry

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
CSRF_HEADER = "X-Anonymizer"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


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


def create_app(store: CaseStore | None = None, start_worker: bool = True) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 ** 3  # 2 GB uploads (large case files)
    store = store or CaseStore()
    worker = Worker(store) if start_worker else None
    registries = RegistryCache(store)
    app.extensions["anonymizer"] = {"store": store, "worker": worker}

    # ------------------------------------------------------------ security

    @app.before_request
    def _guard():
        host = (request.host or "").rsplit(":", 1)[0].strip("[]")
        if host not in ALLOWED_HOSTS:
            abort(403)
        if request.method not in ("GET", "HEAD", "OPTIONS") and \
                request.headers.get(CSRF_HEADER) != "1":
            abort(403)

    @app.after_request
    def _headers(resp):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            "frame-src 'self'; connect-src 'self'")
        return resp

    @app.errorhandler(Exception)
    def _error(exc):
        from werkzeug.exceptions import HTTPException

        if isinstance(exc, HTTPException):
            return jsonify(error=exc.description or exc.name), exc.code
        if isinstance(exc, KeyError):
            return jsonify(error="Nicht gefunden"), 404
        log.exception("request failed")
        return jsonify(error=str(exc)), 500

    def _submit(case_id: str, action: str) -> None:
        if worker is not None:
            worker.submit(case_id, action)

    # -------------------------------------------------------------- static

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/favicon.ico")
    def favicon():
        return "", 204

    @app.get("/static/<path:name>")
    def static_files(name: str):
        return send_from_directory(STATIC_DIR, name)

    # -------------------------------------------------------- status/settings

    @app.get("/api/status")
    def status():
        try:
            ner.load_spacy()
            spacy_ok = True
        except Exception:  # noqa: BLE001
            spacy_ok = False
        return jsonify(version=__version__, spacy=spacy_ok, tesseract=ocr.is_available(),
                       tesseract_languages=ocr.available_languages(),
                       gliner=ner.gliner_installed(), platform=sys.platform)

    @app.get("/api/settings")
    def get_settings():
        settings = Settings.load()
        data = settings.__dict__.copy()
        data["category_labels"] = {c.value: c.label_de for c in Category
                                   if c is not Category.MANUAL}
        return jsonify(data)

    @app.post("/api/settings")
    def set_settings():
        payload = request.get_json(force=True) or {}
        settings = Settings.load()
        for key in ("categories", "security_mode", "use_gliner", "deep_verification",
                    "write_mapping", "output_dir", "allow_terms", "deny_terms",
                    "use_learning", "thorough_ocr", "remove_small_images", "forensic_check",
                    "auto_train"):
            if key in payload:
                setattr(settings, key, payload[key])
        if settings.security_mode not in ("standard", "maximum"):
            settings.security_mode = "standard"
        settings.allow_terms = [t.strip() for t in settings.allow_terms if str(t).strip()]
        settings.save()
        warning = ""
        if is_cloud_synced(settings.output_dir):
            warning = ("Achtung: Der Ergebnisordner scheint mit einem Cloud-Dienst synchronisiert "
                       "zu werden. Die vertrauliche Zuordnungstabelle würde dann hochgeladen.")
        return jsonify(ok=True, warning=warning)

    # ---------------------------------------------------------------- cases

    @app.get("/api/cases")
    def list_cases():
        return jsonify([_case_summary(c) for c in store.list()])

    @app.post("/api/cases")
    def create_case():
        payload = request.get_json(force=True) or {}
        case = store.create(payload.get("title", ""))
        return jsonify(_case_summary(case))

    @app.get("/api/cases/<case_id>")
    def get_case(case_id: str):
        return jsonify(store.load(case_id).to_dict())

    @app.delete("/api/cases/<case_id>")
    def delete_case(case_id: str):
        case = store.load(case_id)
        if case.status in ("analyse", "schwaerzen"):
            abort(409, "Die Akte wird gerade bearbeitet.")
        store.delete(case_id)
        return jsonify(ok=True)

    @app.post("/api/cases/<case_id>/documents")
    def upload(case_id: str):
        case = store.load(case_id)
        if case.status not in ("neu", "fehler"):
            abort(409, "Dokumente können nur vor der Analyse hinzugefügt werden.")
        files = request.files.getlist("files")
        added = []
        for file in files:
            data = file.read()
            if not data.startswith(b"%PDF"):
                abort(400, f"„{file.filename}“ ist keine PDF-Datei.")
            added.append(store.add_document(case, file.filename or "Dokument.pdf", data).name)
        return jsonify(added=added)

    @app.post("/api/cases/<case_id>/analyse")
    def analyse(case_id: str):
        case = store.load(case_id)
        if not case.documents:
            abort(400, "Bitte zuerst PDF-Dateien hinzufügen.")
        case.status = "analyse"
        case.progress = {"done": 0, "total": 1, "step": "Wartet …"}
        store.save(case)
        _submit(case_id, "analyse")
        return jsonify(ok=True)

    @app.post("/api/cases/<case_id>/redact")
    def redact(case_id: str):
        case = store.load(case_id)
        if case.status not in ("pruefung", "fertig", "fehler"):
            abort(409, "Die Akte ist noch nicht bereit.")
        case.status = "schwaerzen"
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
            result.append({
                "id": e.id, "category": e.category.value, "category_label": e.category.label_de,
                "original": e.original, "variants": e.variants, "replacement": e.replacement,
                "short": e.forms.get("surname", ""), "enabled": e.enabled, "count": e.count,
                "sources": e.sources, "first": {"doc": first[0], "page": first[1]},
                "uncertain": e.uncertain, "decided": e.decided, "score": round(e.score, 2),
            })
        return jsonify(result)

    @app.patch("/api/cases/<case_id>/entities/<entity_id>")
    def update_entity(case_id: str, entity_id: str):
        payload = request.get_json(force=True) or {}
        registry = registries.get(case_id)
        entity = registry.entities[entity_id]
        if "enabled" in payload:
            changed = entity.enabled != bool(payload["enabled"])
            entity.enabled = bool(payload["enabled"])
            if changed or payload.get("decided"):
                _learn(registry, entity, "unsicher" if entity.uncertain else "korrektur")
        if payload.get("decided"):
            entity.decided = True
        if "replacement" in payload and str(payload["replacement"]).strip():
            entity.replacement = str(payload["replacement"]).strip()[:80]
            entity.forms["edited"] = "1"
            if entity.category is Category.PERSON:
                # Short forms ("Herr M.") follow the edited full label.
                short = str(payload.get("short") or entity.replacement).strip()[:80]
                entity.forms.update(surname=short, given=short, inverted=entity.replacement)
        registries.save(case_id, registry)
        return jsonify(ok=True)

    @app.post("/api/cases/<case_id>/entities")
    def add_entity(case_id: str):
        payload = request.get_json(force=True) or {}
        text = str(payload.get("text", "")).strip()
        try:
            category = Category(payload.get("category", "MANUAL"))
        except ValueError:
            abort(400, "Unbekannte Kategorie")
        if len(text) < 2:
            abort(400, "Bitte mindestens zwei Zeichen eingeben.")
        registry = registries.get(case_id)
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
        registry = registries.get(case_id)
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
        return jsonify(uncertain_open=len(open_items), pages=pages)

    @app.post("/api/cases/<case_id>/pages/reviewed")
    def page_reviewed(case_id: str):
        payload = request.get_json(force=True) or {}
        registry = registries.get(case_id)
        key = f"{int(payload['doc'])}:{int(payload['page'])}"
        if key not in registry.reviewed_pages:
            registry.reviewed_pages.append(key)
        registries.save(case_id, registry)
        return jsonify(ok=True)

    @app.post("/api/cases/<case_id>/suspects")
    def decide_suspect(case_id: str):
        """Decision about a finding of the residual scan (after anonymising)."""
        payload = request.get_json(force=True) or {}
        text = str(payload.get("text", "")).strip()
        try:
            category = Category(payload.get("category", "OTHER_NAME"))
        except ValueError:
            category = Category.OTHER_NAME
        if not text:
            abort(400, "Kein Text angegeben.")
        if payload.get("redact"):
            registry = registries.get(case_id)
            entity = registry.add_term(text, category)
            if entity is not None:
                _learn(registry, entity, "nachkontrolle")
            registries.save(case_id, registry)
        else:
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
        learning.clear()
        return jsonify(ok=True)

    # ---------------------------------------------------------------- pages

    def _doc_path(case_id: str, doc: int, output: bool = False) -> Path:
        case = store.load(case_id)
        if not 0 <= doc < len(case.documents):
            abort(404)
        info = case.documents[doc]
        if output:
            if not info.output or not Path(info.output).exists():
                abort(404, "Noch kein Ergebnis vorhanden.")
            return Path(info.output)
        return store.folder(case_id) / info.stored

    def _render(path: Path, page_no: int, zoom: float):
        with pymupdf.open(path) as pdf:
            if not 0 <= page_no < pdf.page_count:
                abort(404)
            pix = pdf[page_no].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            return send_file(io.BytesIO(pix.tobytes("png")), mimetype="image/png")

    @app.get("/api/cases/<case_id>/docs/<int:doc>/pages/<int:page>.png")
    def page_image(case_id: str, doc: int, page: int):
        zoom = min(max(float(request.args.get("zoom", 1.5)), 0.3), 4.0)
        return _render(_doc_path(case_id, doc), page, zoom)

    @app.get("/api/cases/<case_id>/docs/<int:doc>/output/pages/<int:page>.png")
    def output_image(case_id: str, doc: int, page: int):
        zoom = min(max(float(request.args.get("zoom", 1.5)), 0.3), 4.0)
        return _render(_doc_path(case_id, doc, output=True), page, zoom)

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
                manual.append({"index": idx, "label": b.label,
                               "rect": [r.x0, r.y0, r.x1, r.y1]})
        return jsonify(width=width, height=height, kind=info.kind, boxes=boxes,
                       manual=manual, pages=len(registry.docs[doc]))

    @app.post("/api/cases/<case_id>/manual")
    def add_manual(case_id: str):
        payload = request.get_json(force=True) or {}
        registry = registries.get(case_id)
        doc, page = int(payload["doc"]), int(payload["page"])
        with pymupdf.open(_doc_path(case_id, doc)) as pdf:
            derot = pdf[page].derotation_matrix
        r = pymupdf.Rect(*map(float, payload["rect"])) * derot
        r.normalize()
        if r.width < 2 or r.height < 2:
            abort(400, "Der Bereich ist zu klein.")
        label = str(payload.get("label") or "[geschwärzt]")[:40]
        registry.manual_boxes.append(ManualBox(doc, page, r.x0, r.y0, r.x1, r.y1, label))
        registries.save(case_id, registry)
        return jsonify(ok=True)

    @app.delete("/api/cases/<case_id>/manual/<int:index>")
    def delete_manual(case_id: str, index: int):
        registry = registries.get(case_id)
        if not 0 <= index < len(registry.manual_boxes):
            abort(404)
        registry.manual_boxes.pop(index)
        registries.save(case_id, registry)
        return jsonify(ok=True)

    # --------------------------------------------------------------- results

    @app.get("/api/cases/<case_id>/docs/<int:doc>/download/<kind>")
    def download(case_id: str, doc: int, kind: str):
        case = store.load(case_id)
        if not 0 <= doc < len(case.documents):
            abort(404)
        info = case.documents[doc]
        path = {"pdf": info.output, "txt": info.text_output, "report": info.report}.get(kind)
        if not path or not Path(path).exists():
            abort(404, "Datei nicht vorhanden.")
        inline = request.args.get("inline") == "1"
        return send_file(path, as_attachment=not inline, download_name=Path(path).name)

    @app.get("/api/cases/<case_id>/zip")
    def download_zip(case_id: str):
        """All anonymised files of a case in one ZIP (never the mapping table)."""
        import zipfile

        case = store.load(case_id)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for doc in case.documents:
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
        registry = registries.get(case_id)
        for entity in registry.entities.values():
            if entity.uncertain and not entity.decided:
                entity.decided = True  # not recorded as learning signal (not looked at)
        payload = request.get_json(silent=True) or {}
        if payload.get("pages"):
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
        return send_file(case.mapping)

    @app.post("/api/cases/<case_id>/open-folder")
    def open_folder(case_id: str):
        case = store.load(case_id)
        folder = case.output_dir
        if not folder or not Path(folder).exists():
            abort(404, "Ordner nicht vorhanden.")
        _open_in_file_manager(Path(folder))
        return jsonify(ok=True, folder=folder)

    return app


def _learn(registry: CaseRegistry, entity, origin: str) -> None:
    """Store the user's decision about ``entity`` for future cases."""
    if not registry.settings.use_learning:
        return
    learning.record(learning.Decision(
        text=entity.original, category=entity.category.value,
        decision="schwaerzen" if entity.enabled else "behalten", origin=origin,
        sources=list(entity.sources), contexts=registry.contexts(entity)))


def _case_summary(case) -> dict:
    return {"id": case.id, "title": case.title, "created": case.created,
            "status": case.status, "documents": len(case.documents),
            "pages": sum(d.pages for d in case.documents), "progress": case.progress,
            "error": case.error,
            "passed": all(d.verification.get("passed") for d in case.documents)
            if case.status == "fertig" else None}


def _open_in_file_manager(path: Path) -> None:
    """Open a folder in Explorer/Finder (only works on the local desktop)."""
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])  # noqa: S603, S607
    else:
        subprocess.Popen(["xdg-open", str(path)])  # noqa: S603, S607
