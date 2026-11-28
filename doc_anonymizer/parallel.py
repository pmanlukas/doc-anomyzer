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
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import Settings
from .errors import Cancelled, WorkerCrashed

log = logging.getLogger(__name__)

#: Memory one worker process needs at its peak (PyMuPDF, page images, word
#: list: about 500 MB measured) including the Tesseract process it starts.
WORKER_MEMORY_MB = 600
#: Kept free for the main process: the language model alone needs 1 GB.
MAIN_MEMORY_MB = 1500
#: Computers with less installed memory than this (in practice: 4 GB) do
#: everything in one process and read at most two pages at a time.
LOW_MEMORY_TOTAL_MB = 5000


def _windows_memory_mb() -> tuple[int, int] | None:
    """(available, installed) physical memory on Windows, without extra packages."""
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    try:
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
    except (AttributeError, OSError):
        return None
    return int(status.ullAvailPhys / 2**20), int(status.ullTotalPhys / 2**20)


def _memory_mb() -> tuple[int | None, int | None]:
    """(available, installed) physical memory in MB; ``None`` where unknown."""
    if sys.platform.startswith("win"):
        return _windows_memory_mb() or (None, None)
    values: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                name, _, rest = line.partition(":")
                if name in ("MemAvailable", "MemTotal"):
                    values[name] = int(rest.split()[0]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return values.get("MemAvailable"), values.get("MemTotal")


def _available_memory_mb() -> int | None:
    """Free physical memory (None if unknown)."""
    return _memory_mb()[0]


def _total_memory_mb() -> int | None:
    """Installed physical memory (None if unknown)."""
    return _memory_mb()[1]


def low_memory() -> bool:
    """A computer with little memory (4 GB): work one page after the other."""
    total = _total_memory_mb()
    return total is not None and total < LOW_MEMORY_TOTAL_MB


def worker_count(documents: int) -> int:
    """How many worker processes to use for ``documents`` documents.

    Limited by the processor (one logical core stays free for the window) and
    by the memory: a process that does not fit makes the computer swap, which
    is far slower than doing the documents one after the other.
    """
    if low_memory():
        return 1
    cores = os.cpu_count() or 2
    workers = max(1, cores - 1)
    memory = _available_memory_mb()
    if memory is not None:
        workers = min(workers, max(1, (memory - MAIN_MEMORY_MB) // WORKER_MEMORY_MB))
    workers = min(workers, 8, documents)
    return max(1, workers)


def ocr_threads() -> int:
    """Parallel Tesseract processes *within* one process."""
    env = os.environ.get("DOC_ANONYMIZER_OCR_THREADS")
    if env and env.isdigit():
        return max(1, int(env))
    threads = max(1, min(4, (os.cpu_count() or 2) - 1))
    return min(threads, 2) if low_memory() else threads


def describe_resources() -> str:
    """One line for the log: what the program found and how it will work."""
    available, total = _memory_mb()
    return (f"{os.cpu_count() or '?'} logische Prozessorkerne, Arbeitsspeicher "
            f"{total if total is not None else '?'} MB (frei: "
            f"{available if available is not None else '?'} MB) -> bis zu "
            f"{worker_count(8)} Arbeitsprozess(e), {ocr_threads()} Texterkennung(en) gleichzeitig")


def _init_worker(ocr_threads_per_worker: int, home: str) -> None:
    """Runs once in every worker process."""
    os.environ["DOC_ANONYMIZER_OCR_THREADS"] = str(ocr_threads_per_worker)
    os.environ["DOC_ANONYMIZER_HOME"] = home
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    from .config import background_priority, private_temp_dir, quiet_subprocesses

    quiet_subprocesses()
    # The workers (and the Tesseract processes they start) give way to the
    # window and to whatever else the user is doing.
    background_priority()
    private_temp_dir()  # page images for Tesseract: not in the shared temp folder
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


def kill_workers(pool: ProcessPoolExecutor | None = None) -> None:
    """End worker processes at once (cancel, program end).

    Without this they would finish their current document after the program
    itself has ended.
    """
    if pool is None:
        processes = multiprocessing.active_children()  # every pool of this program
    else:
        processes = list((getattr(pool, "_processes", None) or {}).values())
    for process in processes:
        try:
            process.kill()
        except Exception:  # noqa: BLE001 - already gone
            pass


def _collect(pool: ProcessPoolExecutor, futures: dict[Future, int],
             on_done: Callable[[int, Future], None],
             cancelled: Callable[[], bool] | None) -> None:
    """Hand every finished future to ``on_done``; stop at once when cancelled."""
    remaining = set(futures)
    try:
        while remaining:
            done, remaining = wait(remaining, timeout=0.5, return_when=FIRST_COMPLETED)
            if cancelled is not None and cancelled():
                raise Cancelled()
            for future in done:
                on_done(futures[future], future)
    except BaseException:
        # Cancelled or an error in a callback: do not wait for documents
        # that are still being processed.
        kill_workers(pool)
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)


def run_in_pool(jobs: list[tuple[int, Callable, tuple]],
                on_result: Callable[[int, object], None],
                on_error: Callable[[int, BaseException], None],
                cancelled: Callable[[], bool] | None = None,
                isolate: bool = False) -> bool:
    """Run ``jobs`` (index, function, arguments) in worker processes.

    A document must never take the whole batch down:

    * an exception in one job is reported for that job only,
    * if a worker process *dies* (memory exhausted, crash inside a native
      library on a damaged PDF), the pool is unusable – the jobs that were
      not finished are then repeated one by one, each in a fresh process, so
      only the document that really causes the crash fails.

    ``cancelled`` is asked regularly; if it answers ``True`` the workers are
    ended and :class:`Cancelled` is raised. ``isolate`` uses a worker process
    even where it does not pay off (single document) – for cases whose last
    attempt ended the whole program.

    Returns ``False`` if no pool is used (the caller processes in-process).
    """
    pool = make_pool(len(jobs))
    if pool is None and isolate:
        pool = make_pool(1, workers=1)
    if pool is None:
        return False
    pending = {index: (fn, args) for index, fn, args in jobs}
    broken = []

    def on_done(index: int, future: Future) -> None:
        try:
            result = future.result()
        except BrokenProcessPool:
            broken.append(index)  # every unfinished future fails the same way
            return
        except Exception as exc:  # noqa: BLE001 - reported per document
            pending.pop(index)
            on_error(index, exc)
            return
        pending.pop(index)
        on_result(index, result)

    _collect(pool, {pool.submit(fn, *args): index for index, (fn, args) in pending.items()},
             on_done, cancelled)
    if broken:
        log.warning("worker process died – repeating %d document(s) one by one", len(pending))
        for index, (fn, args) in sorted(pending.items()):
            def on_single(i: int, future: Future) -> None:
                try:
                    result = future.result()
                except BrokenProcessPool:
                    on_error(i, WorkerCrashed())
                except Exception as exc:  # noqa: BLE001
                    on_error(i, exc)
                else:
                    on_result(i, result)

            single = make_pool(1, workers=1)
            _collect(single, {single.submit(fn, *args): index}, on_single, cancelled)
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
