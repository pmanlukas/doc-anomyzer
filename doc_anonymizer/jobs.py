"""Case files ("Akten") and the background worker that processes them.

A case file groups one or more PDFs that belong together. All documents of a
case share one entity list, so "Max Mustermann" becomes "M. M." in *every*
document of the case.

Lifecycle::

    neu -> analyse -> pruefung (review by the lawyer) -> schwaerzen -> fertig
                 \\-> fehler                          \\-> fehler

All data of a case (including the confidential originals) lives in the
private application folder and can be deleted with one click.
"""

from __future__ import annotations

import html
import json
import logging
import queue
import re
import shutil
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from . import report
from .config import Settings, home_dir, is_cloud_synced
from .detect.pipeline import Detector
from .extract import analyse_document
from .models import PageInfo
from .process import anonymise_document
from .registry import CaseRegistry

log = logging.getLogger(__name__)


@dataclass
class DocumentInfo:
    name: str  # original file name (CONFIDENTIAL – may contain names)
    stored: str  # file name inside the case folder
    pages: int = 0
    kinds: dict[str, int] = field(default_factory=dict)
    output: str = ""  # path of the anonymised PDF
    text_output: str = ""  # path of the anonymised plain text
    report: str = ""  # path of the verification report
    verification: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class Case:
    id: str
    title: str
    created: str
    status: str = "neu"
    progress: dict = field(default_factory=lambda: {"done": 0, "total": 0, "step": ""})
    documents: list[DocumentInfo] = field(default_factory=list)
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    output_dir: str = ""
    mapping: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Case":
        data = dict(data)
        data["documents"] = [DocumentInfo(**d) for d in data.get("documents", [])]
        return cls(**data)


def _safe_filename(name: str) -> str:
    name = re.sub(r"[^\w\-. ]+", "_", name, flags=re.UNICODE).strip(" .")
    return name[:120] or "Dokument"


class CaseStore:
    """File based storage of all case files."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or home_dir()) / "akten"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # ------------------------------------------------------------ basic I/O

    def folder(self, case_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", case_id):
            raise KeyError(case_id)
        return self.root / case_id

    def create(self, title: str) -> Case:
        case = Case(id=uuid.uuid4().hex, title=title.strip() or "Neue Akte",
                    created=datetime.now().isoformat(timespec="seconds"))
        self.folder(case.id).mkdir(parents=True)
        self.save(case)
        return case

    def save(self, case: Case) -> None:
        with self._lock:
            path = self.folder(case.id) / "case.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(case.to_dict(), ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(path)

    def load(self, case_id: str) -> Case:
        with self._lock:
            path = self.folder(case_id) / "case.json"
            if not path.exists():
                raise KeyError(case_id)
            return Case.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list(self) -> list[Case]:
        cases = []
        for path in self.root.glob("*/case.json"):
            try:
                cases.append(self.load(path.parent.name))
            except (KeyError, ValueError):
                continue
        return sorted(cases, key=lambda c: c.created, reverse=True)

    def delete(self, case_id: str) -> None:
        with self._lock:
            shutil.rmtree(self.folder(case_id), ignore_errors=True)

    def add_document(self, case: Case, filename: str, data: bytes) -> DocumentInfo:
        stored = f"dok_{len(case.documents) + 1:03d}.pdf"
        (self.folder(case.id) / stored).write_bytes(data)
        doc = DocumentInfo(name=filename, stored=stored)
        case.documents.append(doc)
        self.save(case)
        return doc

    # ------------------------------------------------- pages and registry

    def save_pages(self, case_id: str, index: int, pages: list[PageInfo]) -> None:
        path = self.folder(case_id) / f"seiten_{index:03d}.json"
        path.write_text(json.dumps([p.to_dict() for p in pages]), encoding="utf-8")

    def load_pages(self, case_id: str, index: int) -> list[PageInfo]:
        path = self.folder(case_id) / f"seiten_{index:03d}.json"
        return [PageInfo.from_dict(p) for p in json.loads(path.read_text(encoding="utf-8"))]

    def load_registry(self, case: Case, settings: Settings) -> CaseRegistry:
        docs = [self.load_pages(case.id, i) for i in range(len(case.documents))]
        path = self.folder(case.id) / "register.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return CaseRegistry.from_dict(data, docs, settings)

    def save_registry(self, case_id: str, registry: CaseRegistry) -> None:
        path = self.folder(case_id) / "register.json"
        path.write_text(json.dumps(registry.to_dict(), ensure_ascii=False), encoding="utf-8")


class Worker:
    """Processes analysis and redaction jobs one after another in a thread."""

    def __init__(self, store: CaseStore) -> None:
        self.store = store
        self.jobs: queue.Queue[tuple[str, str]] = queue.Queue()
        self.thread = threading.Thread(target=self._run, name="anonymizer-worker", daemon=True)
        self.thread.start()

    def submit(self, case_id: str, action: str) -> None:
        self.jobs.put((case_id, action))

    def _run(self) -> None:
        while True:
            case_id, action = self.jobs.get()
            try:
                if action == "analyse":
                    self.analyse(case_id)
                elif action == "redact":
                    self.redact(case_id)
            except Exception as exc:  # noqa: BLE001 - report every problem to the UI
                log.error("job failed: %s", traceback.format_exc())
                try:
                    case = self.store.load(case_id)
                    case.status = "fehler"
                    case.error = str(exc) or exc.__class__.__name__
                    self.store.save(case)
                except KeyError:
                    pass

    # ------------------------------------------------------------- progress

    def _progress_fn(self, case: Case, prefix: str = ""):
        last = [0.0]

        def progress(done: int, total: int, step: str) -> None:
            case.progress = {"done": done, "total": total, "step": f"{prefix}{step}"}
            now = time.monotonic()
            if now - last[0] > 0.5 or done == total:  # do not write the file too often
                last[0] = now
                self.store.save(case)
        return progress

    # -------------------------------------------------------------- analyse

    def analyse(self, case_id: str) -> None:
        settings = Settings.load()
        case = self.store.load(case_id)
        case.status, case.error, case.warnings = "analyse", "", []
        self.store.save(case)

        detector = Detector(settings)
        docs: list[list[PageInfo]] = []
        detections = []
        for i, doc in enumerate(case.documents):
            prefix = f"Dokument {i + 1}/{len(case.documents)}: "
            path = self.store.folder(case.id) / doc.stored
            pages = analyse_document(path, settings, self._progress_fn(case, prefix))
            doc.pages = len(pages)
            doc.kinds = {k: sum(1 for p in pages if p.kind == k) for k in
                         ("digital", "mixed", "scan")}
            doc.notes = [f"{doc.name}: {n}" for n in dict.fromkeys(n for p in pages for n in p.notes)]
            self.store.save_pages(case.id, i, pages)
            detections.append(detector.detect_document(pages, self._progress_fn(case, prefix)))
            docs.append(pages)

        registry = CaseRegistry(docs, settings)
        registry.build(detections)
        self.store.save_registry(case.id, registry)
        case.warnings = detector.warnings
        case.status = "pruefung"
        case.progress = {"done": 1, "total": 1, "step": "Bereit zur Prüfung"}
        self.store.save(case)

    # --------------------------------------------------------------- redact

    def redact(self, case_id: str) -> None:
        settings = Settings.load()
        case = self.store.load(case_id)
        case.status, case.error = "schwaerzen", ""
        case.warnings = [w for w in case.warnings if "Nachkontrolle" not in w]
        self.store.save(case)
        registry = self.store.load_registry(case, settings)

        out_dir = Path(settings.output_dir) / _safe_filename(
            f"{datetime.now():%Y-%m-%d_%H%M} {anonymise_text(case.title, registry)}")
        out_dir.mkdir(parents=True, exist_ok=True)
        case.output_dir = str(out_dir)
        if is_cloud_synced(out_dir):
            case.warnings.append("Der Ergebnisordner wird offenbar mit einem Cloud-Dienst "
                                 "synchronisiert – bitte in den Einstellungen ändern.")

        for i, doc in enumerate(case.documents):
            prefix = f"Dokument {i + 1}/{len(case.documents)}: "
            pages = registry.docs[i]
            base = _safe_filename(anonymise_text(Path(doc.name).stem, registry))
            target = out_dir / f"{base}_anonymisiert.pdf"
            source = self.store.folder(case.id) / doc.stored
            processed = anonymise_document(source, target, i, pages, registry, settings,
                                           self._progress_fn(case, prefix))
            result, verification = processed.redaction, processed.verification
            if processed.repairs:
                case.warnings.append(
                    f"{doc.name}: Die Nachkontrolle hat {processed.repairs} zusätzliche "
                    "Stelle(n) gefunden und automatisch geschwärzt.")
            text_target = out_dir / f"{base}_anonymisiert.txt"
            text_target.write_text(_text_export(result.texts), encoding="utf-8")
            _check_text_export(text_target, registry, verification)
            doc.output, doc.text_output = str(target), str(text_target)
            doc.verification = verification.to_dict()
            report_path = out_dir / f"{base}_Pruefbericht.html"
            report_path.write_text(report.verification_report(
                case, doc, verification, result, registry), encoding="utf-8")
            doc.report = str(report_path)
            self.store.save(case)

        # Automatic repairs may have added redactions – keep them for the review.
        self.store.save_registry(case.id, registry)
        if settings.write_mapping:
            confidential = out_dir / "VERTRAULICH_nicht_weitergeben"
            confidential.mkdir(exist_ok=True)
            mapping = confidential / "Zuordnungstabelle.html"
            mapping.write_text(report.mapping_report(case, registry), encoding="utf-8")
            case.mapping = str(mapping)
        case.status = "fertig"
        case.progress = {"done": 1, "total": 1, "step": "Fertig"}
        self.store.save(case)


def anonymise_text(text: str, registry: CaseRegistry) -> str:
    """Replace known personal data inside a short text (e.g. a file name)."""
    result = text.replace("_", " ")
    terms: list[tuple[str, str]] = []
    for entity in registry.entities.values():
        if not entity.enabled:
            continue
        variants = set(entity.variants) | {entity.original}
        if entity.surname:
            variants |= {w for w in entity.surname.split() if len(w) > 2}
        variants |= {g for g in entity.given if len(g) > 2}
        for variant in variants:
            terms.append((variant, entity.forms.get("surname") if variant == entity.surname
                          else entity.replacement))
    # Longest first so "Max Mustermann" is replaced before "Mustermann".
    for variant, label in sorted(terms, key=lambda t: -len(t[0])):
        pattern = r"(?<!\w)" + re.escape(variant) + r"(?!\w)"
        result = re.sub(pattern, (label or "X").replace("\\", ""), result, flags=re.IGNORECASE)
    return result


def _text_export(texts: list[str]) -> str:
    parts = []
    for n, text in enumerate(texts, 1):
        parts.append(f"===== Seite {n} =====\n{text}\n")
    return "\n".join(parts)


def _check_text_export(path: Path, registry: CaseRegistry, verification) -> None:
    """The plain-text export is verified like the PDF itself."""
    from .verify import Check, Haystack, Leak, _contains, search_terms

    hay = Haystack(path.read_text(encoding="utf-8"))
    leaks = [Leak(0, cat.value, eid, term, "Textdatei")
             for term, eid, cat, compact in search_terms(registry)
             if _contains(hay, term, compact, cat)]
    verification.leaks.extend(leaks)
    verification.checks.append(Check("Textfassung (.txt) frei von Originaldaten", not leaks))


def escape(text: str) -> str:
    return html.escape(text, quote=True)
