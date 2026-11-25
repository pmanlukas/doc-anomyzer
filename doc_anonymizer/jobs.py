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
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta
from pathlib import Path

from . import learning, report
from .config import Settings, home_dir, is_cloud_synced
from .detect.pipeline import Detector
from .errors import UserError, friendly_message
from .extract import analyse_document
from .models import PageInfo
from .parallel import AnonymiseJob, anonymise_task, extract_task, run_in_pool
from .process import ProcessResult, anonymise_document
from .registry import CaseRegistry
from .verify import finish_verification

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
    #: Why this document could not be processed (text for the user); the
    #: other documents of the case are processed normally.
    error: str = ""


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
        # Unknown keys (written by a newer version) are ignored.
        doc_keys = {f.name for f in fields(DocumentInfo)}
        case_keys = {f.name for f in fields(cls)}
        data = {k: v for k, v in data.items() if k in case_keys}
        data["documents"] = [DocumentInfo(**{k: v for k, v in d.items() if k in doc_keys})
                             for d in data.get("documents", [])]
        return cls(**data)


#: Device names Windows does not accept as file names (with any extension).
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}
#: Folder for results that failed the verification – the name is the warning.
FAILED_FOLDER = "NICHT BESTANDEN - nicht weitergeben"


def _safe_filename(name: str, limit: int = 80) -> str:
    """A file/folder name that is valid on Windows.

    Only letters, digits, space, dot, hyphen and underscore survive; the
    length is limited so the complete path stays below the 260 characters
    many Windows programs still require.
    """
    name = re.sub(r"[^\w\-. ]+", "_", name, flags=re.UNICODE)
    name = re.sub(r"\s+", " ", name)[:limit].strip(" ._")
    if name.split(".")[0].casefold() in _RESERVED:
        name = "_" + name
    return name or "Dokument"


def display_name(filename: str) -> str:
    """Upload file name as shown to the user (no control characters, bounded)."""
    name = "".join(ch for ch in (filename or "").replace("\\", "/") if ch.isprintable())
    name = "/".join(part for part in name.split("/") if part not in ("", ".", ".."))
    return name[-200:] or "Dokument.pdf"


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

    def purge(self, days: int) -> int:
        """Delete the working data of cases untouched for ``days`` days.

        The working data contains copies of the *original* documents; the
        finished files in the result folder are not touched. Running cases
        are never deleted. Returns the number of deleted cases.
        """
        if days <= 0:
            return 0
        limit = time.time() - timedelta(days=days).total_seconds()
        deleted = 0
        for path in list(self.root.glob("*/case.json")):
            try:
                if path.stat().st_mtime >= limit:
                    continue
                if self.load(path.parent.name).status in ("analyse", "schwaerzen"):
                    continue
            except (OSError, KeyError, ValueError, TypeError):
                continue
            self.delete(path.parent.name)
            deleted += 1
        return deleted

    def add_document(self, case: Case, filename: str, source) -> DocumentInfo:
        """Store an uploaded PDF (``source``: bytes or a readable stream)."""
        stored = f"dok_{len(case.documents) + 1:04d}.pdf"
        target = self.folder(case.id) / stored
        if isinstance(source, (bytes, bytearray)):
            target.write_bytes(source)
        else:
            with target.open("wb") as out:
                shutil.copyfileobj(source, out, 1024 * 1024)
        doc = DocumentInfo(name=display_name(filename), stored=stored)
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
        self.training = {"running": False, "message": ""}
        self._active = 0  # jobs currently being processed
        self._recover_interrupted()
        self._purge()
        self.thread = threading.Thread(target=self._run, name="anonymizer-worker", daemon=True)
        self.thread.start()

    def _recover_interrupted(self) -> None:
        """Cases that were being processed when the program ended.

        Without this they would show "wird verarbeitet …" forever.
        """
        for case in self.store.list():
            if case.status == "schwaerzen" and \
                    (self.store.folder(case.id) / "register.json").exists():
                case.status = "pruefung"
                case.warnings.append("Die Anonymisierung wurde unterbrochen, weil das Programm "
                                     "beendet wurde. Bitte noch einmal starten.")
            elif case.status in ("analyse", "schwaerzen"):
                case.status = "fehler"
                case.error = ("Die Verarbeitung wurde unterbrochen, weil das Programm beendet "
                              "wurde. Bitte noch einmal starten.")
            else:
                continue
            case.progress = {"done": 0, "total": 0, "step": ""}
            self.store.save(case)

    def _purge(self) -> None:
        try:
            deleted = self.store.purge(Settings.load().keep_days)
            if deleted:
                log.info("%d alte Akte(n) automatisch gelöscht", deleted)
        except Exception:  # noqa: BLE001 - housekeeping must never break the program
            log.exception("purge failed")

    def submit(self, case_id: str, action: str) -> None:
        self.jobs.put((case_id, action))

    @property
    def busy(self) -> bool:
        """Is a job running or waiting? (The program must not end then.)"""
        return bool(self._active) or not self.jobs.empty() or self.training["running"]

    def _run(self) -> None:
        while True:
            case_id, action = self.jobs.get()
            self._active += 1
            try:
                if action == "analyse":
                    self.analyse(case_id)
                elif action == "redact":
                    self.redact(case_id)
                    self._maybe_train()
                elif action == "train":
                    self.train()
            except Exception as exc:  # noqa: BLE001 - report every problem to the UI
                log.error("job failed: %s", traceback.format_exc())
                try:
                    case = self.store.load(case_id)
                    case.status = "fehler"
                    case.error = friendly_message(exc)
                    self.store.save(case)
                except KeyError:
                    pass
            finally:
                self._active -= 1
                if action != "train":
                    self._purge()

    # ------------------------------------------------------------- learning

    def _maybe_train(self) -> None:
        """Queue an automatic training run when enough new examples exist."""
        settings = Settings.load()
        if settings.use_learning and settings.auto_train and learning.training_due():
            self.submit("", "train")

    def train(self) -> None:
        """Fine-tune the language model (runs between cases, never during one)."""
        from .train import train

        if not self.jobs.empty():
            self.submit("", "train")  # a case is waiting – train afterwards
            return
        self.training = {"running": True, "message": "Das Sprachmodell lernt gerade dazu …"}
        try:
            result = train(progress=lambda msg: self.training.update(message=msg))
            message = result["message"]
        except Exception as exc:  # noqa: BLE001 - learning must never break the app
            log.exception("training failed")
            message = f"Training nicht möglich: {exc}"
        learning.save_training_state(examples=learning.training_examples(),
                                     date=datetime.now().strftime("%d.%m.%Y %H:%M"),
                                     message=message)
        self.training = {"running": False, "message": message}

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
        """Read all documents (in parallel) and detect personal data."""
        settings = Settings.load()
        case = self.store.load(case_id)
        case.status, case.error, case.warnings = "analyse", "", []
        self.store.save(case)
        total = len(case.documents)
        progress = self._progress_fn(case)
        detector = Detector(settings)
        docs: list[list[PageInfo] | None] = [None] * total
        detections: list = [None] * total
        paths = [self.store.folder(case.id) / d.stored for d in case.documents]

        def finish(i: int, pages: list[PageInfo]) -> None:
            # Detection with the language model runs here (main process)
            # while the workers already read the next documents.
            doc = case.documents[i]
            doc.pages = len(pages)
            doc.kinds = {k: sum(1 for p in pages if p.kind == k) for k in
                         ("digital", "mixed", "scan")}
            doc.notes = [f"{doc.name}: {n}" for n in dict.fromkeys(n for p in pages for n in p.notes)]
            self.store.save_pages(case.id, i, pages)
            detections[i] = detector.detect_document(pages)
            docs[i] = pages

        def fail(i: int, exc: BaseException) -> None:
            # One unreadable document must not stop the others.
            doc = case.documents[i]
            doc.error = friendly_message(exc)
            doc.pages, doc.kinds, doc.notes = 0, {}, []
            docs[i], detections[i] = [], []
            self.store.save_pages(case.id, i, [])
            if not isinstance(exc, UserError):
                log.error("Dokument %d konnte nicht gelesen werden:\n%s", i + 1,
                          "".join(traceback.format_exception(exc)))

        for doc in case.documents:
            doc.error = ""
        started = time.monotonic()
        done = [0]

        def step() -> None:
            done[0] += 1
            progress(done[0], total, _eta("Dokumente lesen", started, done[0], total))

        def on_result(i: int, pages) -> None:
            try:
                finish(i, pages)
            except Exception as exc:  # noqa: BLE001
                fail(i, exc)
            step()

        def on_error(i: int, exc: BaseException) -> None:
            fail(i, exc)
            step()

        jobs = [(i, extract_task, (str(path), settings)) for i, path in enumerate(paths)]
        if not run_in_pool(jobs, on_result, on_error):
            for i, path in enumerate(paths):
                prefix = f"Dokument {i + 1}/{total}: " if total > 1 else ""
                try:
                    finish(i, analyse_document(path, settings, self._progress_fn(case, prefix)))
                except Exception as exc:  # noqa: BLE001
                    fail(i, exc)
                step()

        failed = [d for d in case.documents if d.error]
        if len(failed) == total:
            case.status = "fehler"
            case.error = failed[0].error if total == 1 else (
                f"Keines der {total} Dokumente konnte gelesen werden. Erstes Problem: "
                f"{failed[0].error}")
            case.progress = {"done": 0, "total": 0, "step": ""}
            self.store.save(case)
            return

        registry = CaseRegistry(docs, settings)  # type: ignore[arg-type]
        registry.build(detections)
        self.store.save_registry(case.id, registry)
        case.warnings = list(detector.warnings)
        case.status = "pruefung"
        case.progress = {"done": 1, "total": 1, "step": "Bereit zur Prüfung"}
        self.store.save(case)

    # --------------------------------------------------------------- redact

    def redact(self, case_id: str) -> None:
        """Anonymise all documents (in parallel) and verify them."""
        settings = Settings.load()
        case = self.store.load(case_id)
        case.status, case.error = "schwaerzen", ""
        case.warnings = [w for w in case.warnings if "Nachkontrolle" not in w]
        self.store.save(case)
        registry = self.store.load_registry(case, settings)
        registry.labels_frozen = True  # identical labels in every document
        names = NameAnonymiser(registry)

        out_dir = Path(settings.output_dir).expanduser() / _safe_filename(
            f"{datetime.now():%Y-%m-%d_%H%M} {names(case.title)}", limit=70)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise UserError(
                f"Der Ergebnisordner „{settings.output_dir}“ kann nicht angelegt werden. "
                "Bitte in den Einstellungen einen anderen Speicherort wählen.") from exc
        case.output_dir = str(out_dir)
        if is_cloud_synced(out_dir):
            case.warnings.append("Der Ergebnisordner wird offenbar mit einem Cloud-Dienst "
                                 "synchronisiert – bitte in den Einstellungen ändern.")

        total = len(case.documents)
        targets, used = [], set()
        for doc in case.documents:
            base = _safe_filename(names(Path(doc.name).stem))
            while base.casefold() in used:  # identical names after anonymisation
                base += "_2"
            used.add(base.casefold())
            targets.append(out_dir / f"{base}_anonymisiert.pdf")
        progress = self._progress_fn(case)
        started = time.monotonic()

        self_corrections: list = []

        def finish(i: int, processed: ProcessResult) -> None:
            doc = case.documents[i]
            self_corrections.extend(processed.learned)
            result, verification = processed.redaction, processed.verification
            if verification.scan_pages:
                finish_verification(verification, registry, settings)  # stage 4
            if processed.repairs:
                case.warnings.append(
                    f"{doc.name}: Die Nachkontrolle hat {processed.repairs} zusätzliche "
                    "Stelle(n) gefunden und automatisch geschwärzt.")
            target = targets[i]
            text_target = target.with_suffix(".txt")
            text_target.write_text(_text_export(result.texts), encoding="utf-8")
            _check_text_export(text_target, registry, verification)
            if not verification.passed:
                # A result that failed the verification must not look like a
                # finished file: it is moved to a folder whose name says so.
                quarantine = out_dir / FAILED_FOLDER
                quarantine.mkdir(exist_ok=True)
                target = _move(target, quarantine / target.name)
                text_target = _move(text_target, quarantine / text_target.name)
            doc.output, doc.text_output = str(target), str(text_target)
            doc.verification = verification.to_dict()
            report_path = target.with_name(target.stem.replace("_anonymisiert", "")
                                           + "_Pruefbericht.html")
            report_path.write_text(report.verification_report(
                case, doc, verification, result, registry), encoding="utf-8")
            doc.report = str(report_path)

        def fail(i: int, exc: BaseException) -> None:
            # One document that cannot be anonymised must not stop the others;
            # a half-written result must not stay behind.
            doc = case.documents[i]
            doc.error = "Anonymisierung nicht möglich: " + friendly_message(exc)
            doc.output = doc.text_output = doc.report = ""
            doc.verification = {}
            for leftover in (targets[i], targets[i].with_suffix(".txt")):
                try:
                    leftover.unlink(missing_ok=True)
                except OSError:
                    pass
            if not isinstance(exc, UserError):
                log.error("Dokument %d konnte nicht anonymisiert werden:\n%s", i + 1,
                          "".join(traceback.format_exception(exc)))

        # Documents that could not be read during the analysis are skipped.
        todo = [i for i in range(total) if registry.docs[i]]
        for i, doc in enumerate(case.documents):
            if i in todo:
                doc.error = ""
            doc.output = doc.text_output = doc.report = ""
            doc.verification = {}
        done = [0]

        def step() -> None:
            done[0] += 1
            progress(done[0], len(todo),
                     _eta("Anonymisieren und prüfen", started, done[0], len(todo)))

        def on_result(i: int, payload) -> None:
            try:
                processed, mentions, boxes, pages = payload
                registry.replace_document_results(i, mentions, boxes)
                registry.docs[i] = pages
                finish(i, processed)
            except Exception as exc:  # noqa: BLE001
                fail(i, exc)
            step()

        def on_error(i: int, exc: BaseException) -> None:
            fail(i, exc)
            step()

        jobs = [(i, anonymise_task, (AnonymiseJob(
            str(self.store.folder(case.id) / case.documents[i].stored), str(targets[i]), i,
            total, registry.docs[i], registry.state_for_document(i), settings),))
            for i in todo]
        if not run_in_pool(jobs, on_result, on_error):
            for i in todo:
                prefix = f"Dokument {i + 1}/{total}: " if total > 1 else ""
                source = self.store.folder(case.id) / case.documents[i].stored
                try:
                    finish(i, anonymise_document(source, targets[i], i, registry.docs[i],
                                                 registry, settings,
                                                 self._progress_fn(case, prefix),
                                                 run_residual_scan=False))
                except Exception as exc:  # noqa: BLE001
                    fail(i, exc)
                step()

        # Detection gaps found by the verification become training examples.
        if settings.use_learning:
            learning.record_many(self_corrections)
            # Detected signature areas the user left in place were correct.
            kept = [b for b in registry.manual_boxes if b.features]
            learning.record_signatures([(b.features, 1) for b in kept])
            for b in kept:
                b.features = []  # count every area only once

        # Automatic repairs may have added redactions – keep them for the review.
        registry.labels_frozen = False
        self.store.save_registry(case.id, registry)
        for i in range(total):
            self.store.save_pages(case.id, i, registry.docs[i])
        produced = [d for d in case.documents if d.output]
        if not produced:
            case.status = "fehler"
            errors = [d.error for d in case.documents if d.error]
            case.error = errors[0] if errors else "Es konnte kein Dokument anonymisiert werden."
            case.progress = {"done": 0, "total": 0, "step": ""}
            self.store.save(case)
            try:
                out_dir.rmdir()  # nothing was written
            except OSError:
                pass
            return
        case.mapping = ""
        if settings.write_mapping:
            confidential = out_dir / "VERTRAULICH_nicht_weitergeben"
            confidential.mkdir(exist_ok=True)
            mapping = confidential / "Zuordnungstabelle.html"
            mapping.write_text(report.mapping_report(case, registry), encoding="utf-8")
            case.mapping = str(mapping)
        case.status = "fertig"
        case.progress = {"done": 1, "total": 1, "step": "Fertig"}
        self.store.save(case)


def _move(source: Path, target: Path) -> Path:
    """Move a file, replacing an existing target."""
    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass
    return Path(shutil.move(str(source), str(target)))


def _eta(step: str, started: float, done: int, total: int) -> str:
    """Progress text with estimated remaining time ("noch ca. 3 Minuten")."""
    if done >= total or done < 2:
        return step
    remaining = (time.monotonic() - started) / done * (total - done)
    if remaining < 90:
        return f"{step} – noch ca. {max(10, round(remaining, -1)):.0f} Sekunden"
    return f"{step} – noch ca. {remaining / 60:.0f} Minuten"


class NameAnonymiser:
    """Replace known personal data inside short texts such as file names.

    All variants are compiled into one regular expression (fast for
    hundreds of file names).
    """

    def __init__(self, registry: CaseRegistry) -> None:
        mapping: dict[str, str] = {}
        for entity in registry.entities.values():
            if not entity.enabled:
                continue
            variants = set(entity.variants) | {entity.original}
            if entity.surname:
                variants |= {w for w in entity.surname.split() if len(w) > 2}
            variants |= {g for g in entity.given if len(g) > 2}
            for variant in variants:
                label = entity.forms.get("surname") if variant == entity.surname \
                    else entity.replacement
                if len(variant) >= 2:
                    mapping.setdefault(variant.casefold(), label or "X")
        self.mapping = mapping
        # Longest first so "Max Mustermann" is replaced before "Mustermann".
        alternatives = sorted(mapping, key=len, reverse=True)
        self.pattern = re.compile(
            r"(?<!\w)(" + "|".join(re.escape(a) for a in alternatives) + r")(?!\w)",
            re.IGNORECASE) if alternatives else None

    def __call__(self, text: str) -> str:
        text = text.replace("_", " ")
        if self.pattern is None:
            return text
        return self.pattern.sub(lambda m: self.mapping[m.group(1).casefold()], text)


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
