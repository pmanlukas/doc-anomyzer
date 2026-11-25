"""Process documents of a case file in parallel.

Case files often consist of hundreds of short documents. Text recognition
(Tesseract) and PDF processing dominate the run time, and a single short
document cannot keep all CPU cores busy. Therefore documents are distributed
over several worker *processes* (PyMuPDF is not thread-safe, processes are
also robust against a crash caused by a damaged file).

What runs where:

* worker processes – reading/OCR of a document; redaction and verification
  stages 1-3 of a document (no language model needed there),
* main process     – detection with the language model, grouping of all
  entities of the case (needs all documents), verification stage 4.

Replacement labels are frozen before parallel redaction starts, so every
document gets exactly the same "M. M." / "[Adresse 3]" regardless of the
order in which the workers finish.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import Settings
from .errors import WorkerCrashed

log = logging.getLogger(__name__)

#: Approximate memory need of one worker process (PyMuPDF, images, word list).
WORKER_MEMORY_MB = 450


def _available_memory_mb() -> int | None:
    """Free physical memory (None if unknown)."""
    try:
        import psutil  # optional

        return int(psutil.virtual_memory().available / 2**20)
    except ImportError:
        pass
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def worker_count(documents: int) -> int:
    """How many worker processes to use for ``documents`` documents."""
    cores = os.cpu_count() or 2
    workers = max(1, cores - 1)  # one core stays free for the user interface
    memory = _available_memory_mb()
    if memory is not None:
        workers = min(workers, max(1, (memory - 1500) // WORKER_MEMORY_MB))
    workers = min(workers, 8, documents)
    return max(1, workers)


def ocr_threads() -> int:
    """Parallel Tesseract processes *within* one process."""
    env = os.environ.get("DOC_ANONYMIZER_OCR_THREADS")
    if env and env.isdigit():
        return max(1, int(env))
    return max(1, min(4, (os.cpu_count() or 2) - 1))


def _init_worker(ocr_threads_per_worker: int, home: str) -> None:
    """Runs once in every worker process."""
    os.environ["DOC_ANONYMIZER_OCR_THREADS"] = str(ocr_threads_per_worker)
    os.environ["DOC_ANONYMIZER_HOME"] = home
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    # Started from pythonw.exe (installed program): there is no console, so
    # stdout/stderr are None – libraries writing to them must not crash.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    logging.basicConfig(level=logging.WARNING)


def make_pool(documents: int, workers: int | None = None) -> ProcessPoolExecutor | None:
    """A process pool – or ``None`` if parallel processing is not worthwhile."""
    if workers is None:
        workers = worker_count(documents)
        if workers < 2:
            return None
    from .config import home_dir
    from .detect.ner import prepare_known_words

    # Shared files the workers would otherwise all create at the same time
    # (Windows does not allow replacing a file another process is reading).
    prepare_known_words()
    threads = max(1, (os.cpu_count() or 2) // workers)
    # "spawn" on every platform: identical behaviour on Windows and Linux and
    # no inherited threads/locks of the web server.
    context = multiprocessing.get_context("spawn")
    return ProcessPoolExecutor(max_workers=workers, mp_context=context,
                               initializer=_init_worker,
                               initargs=(threads, str(home_dir())))


def run_in_pool(jobs: list[tuple[int, Callable, tuple]],
                on_result: Callable[[int, object], None],
                on_error: Callable[[int, BaseException], None]) -> bool:
    """Run ``jobs`` (index, function, arguments) in worker processes.

    A document must never take the whole batch down:

    * an exception in one job is reported for that job only,
    * if a worker process *dies* (memory exhausted, crash inside a native
      library on a damaged PDF), the pool is unusable – the jobs that were
      not finished are then repeated one by one, each in a fresh process, so
      only the document that really causes the crash fails.

    Returns ``False`` if no pool could be used (caller processes in-process).
    """
    pool = make_pool(len(jobs))
    if pool is None:
        return False
    pending = {index: (fn, args) for index, fn, args in jobs}
    broken = False
    with pool:
        futures = {pool.submit(fn, *args): index for index, (fn, args) in pending.items()}
        for future in as_completed(futures):
            index = futures[future]
            try:
                result = future.result()
            except BrokenProcessPool:
                broken = True  # every unfinished future fails the same way
                continue
            except Exception as exc:  # noqa: BLE001 - reported per document
                pending.pop(index)
                on_error(index, exc)
                continue
            pending.pop(index)
            on_result(index, result)
    if broken:
        log.warning("worker process died – repeating %d document(s) one by one", len(pending))
        for index, (fn, args) in sorted(pending.items()):
            single = make_pool(1, workers=1)
            try:
                with single:
                    result = single.submit(fn, *args).result()
            except BrokenProcessPool:
                on_error(index, WorkerCrashed())
            except Exception as exc:  # noqa: BLE001
                on_error(index, exc)
            else:
                on_result(index, result)
    return True


# ----------------------------------------------------------------- tasks

def extract_task(path: str, settings: Settings):
    """Worker: read one document (text layer + OCR)."""
    from .extract import analyse_document

    return analyse_document(Path(path), settings)


@dataclass
class AnonymiseJob:
    """Everything a worker needs to anonymise one document."""

    source: str
    target: str
    doc_index: int
    doc_count: int
    pages: list
    registry_state: dict  # entities + this document's mentions/boxes
    settings: Settings


def anonymise_task(job: AnonymiseJob):
    """Worker: redact + verification stages 1-3 (+ automatic repair)."""
    from .process import anonymise_document
    from .registry import CaseRegistry

    docs = [[] for _ in range(job.doc_count)]
    docs[job.doc_index] = job.pages
    registry = CaseRegistry.from_dict(job.registry_state, docs, job.settings)
    registry.labels_frozen = True
    result = anonymise_document(Path(job.source), Path(job.target), job.doc_index, job.pages,
                                registry, job.settings, run_residual_scan=False)
    mentions = [m for m in registry.mentions if m.doc == job.doc_index]
    boxes = [b for b in registry.manual_boxes if b.doc == job.doc_index]
    return result, mentions, boxes, job.pages
