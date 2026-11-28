"""The program adapts to the computer: older processors, little memory."""

from __future__ import annotations

import subprocess
import sys

import pytest

from doc_anonymizer import ocr, parallel
from doc_anonymizer.config import Settings, background_priority


def _computer(monkeypatch, cores: int, available: int | None, total: int | None) -> None:
    """Pretend to run on a computer with these resources (memory in MB)."""
    monkeypatch.setattr(parallel.os, "cpu_count", lambda: cores)
    monkeypatch.setattr(parallel, "_memory_mb", lambda: (available, total))
    monkeypatch.delenv("DOC_ANONYMIZER_OCR_THREADS", raising=False)


def test_memory_is_known_on_this_platform():
    # Without this the program cannot know that a computer is small – on
    # Windows it used to assume "unlimited" (no extra package installed).
    available, total = parallel._memory_mb()
    assert available is not None and total is not None
    assert 0 < available <= total


def test_old_dual_core_with_8_gb_uses_three_workers(monkeypatch):
    # Core i3 of the 4000 series: 2 cores / 4 threads; one stays free.
    _computer(monkeypatch, cores=4, available=4600, total=8100)
    assert not parallel.low_memory()
    assert parallel.worker_count(50) == 3
    assert parallel.ocr_threads() == 3


def test_workers_are_limited_by_the_free_memory(monkeypatch):
    # 8 GB installed but most of it in use: fewer processes instead of swapping.
    _computer(monkeypatch, cores=4, available=2800, total=8100)
    assert parallel.worker_count(50) == 2
    _computer(monkeypatch, cores=8, available=2000, total=8100)
    assert parallel.worker_count(50) == 1


def test_computer_with_4_gb_works_in_one_process(monkeypatch):
    _computer(monkeypatch, cores=4, available=1700, total=3950)
    assert parallel.low_memory()
    assert parallel.worker_count(50) == 1
    assert parallel.make_pool(50) is None  # everything in the main process
    assert parallel.ocr_threads() == 2
    # ... even right after switching on, when a lot of memory is still free.
    _computer(monkeypatch, cores=4, available=3000, total=3950)
    assert parallel.worker_count(50) == 1


def test_unknown_memory_falls_back_to_the_processor(monkeypatch):
    _computer(monkeypatch, cores=4, available=None, total=None)
    assert not parallel.low_memory()
    assert parallel.worker_count(50) == 3


def test_single_core_and_many_cores(monkeypatch):
    _computer(monkeypatch, cores=1, available=6000, total=8000)
    assert parallel.worker_count(50) == 1 and parallel.ocr_threads() == 1
    _computer(monkeypatch, cores=32, available=60000, total=64000)
    assert parallel.worker_count(500) == 8 and parallel.ocr_threads() == 4
    assert parallel.worker_count(2) == 2  # never more processes than documents


def test_resources_are_described_for_the_log(monkeypatch):
    _computer(monkeypatch, cores=4, available=4600, total=8100)
    text = parallel.describe_resources()
    assert "4 logische" in text and "8100 MB" in text and "3 Arbeitsprozess" in text


def test_check_reads_with_the_second_set_of_language_files(tmp_path, monkeypatch):
    for name in ("deu", "eng", "deu_fast"):
        (tmp_path / f"{name}.traineddata").write_bytes(b"x")
    monkeypatch.setattr(ocr, "tessdata_dir", lambda: tmp_path)
    assert ocr.reading_language("deu+eng") == "deu+eng"
    # The fast file is used where it exists, the accurate one otherwise.
    assert ocr.reading_language("deu+eng", fast=True) == "deu_fast+eng"
    (tmp_path / "eng_fast.traineddata").write_bytes(b"x")
    assert ocr.reading_language("deu+eng", fast=True) == "deu_fast+eng_fast"
    monkeypatch.setattr(ocr, "tessdata_dir", lambda: None)
    assert ocr.reading_language("deu+eng", fast=True) == "deu+eng"
    assert Settings().fast_verification_ocr is True


_PRIORITY_PROBE = """
import ctypes, sys
from doc_anonymizer.config import background_priority
kernel = ctypes.windll.kernel32
kernel.GetCurrentProcess.restype = ctypes.c_void_p
kernel.GetPriorityClass.argtypes = [ctypes.c_void_p]
kernel.GetCurrentThread.restype = ctypes.c_void_p
kernel.GetThreadPriority.argtypes = [ctypes.c_void_p]
before = kernel.GetPriorityClass(kernel.GetCurrentProcess())
background_priority(process=False)
thread = kernel.GetThreadPriority(kernel.GetCurrentThread())
same = kernel.GetPriorityClass(kernel.GetCurrentProcess())
background_priority()
print(before, thread, same, kernel.GetPriorityClass(kernel.GetCurrentProcess()))
"""


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows priority classes")
def test_background_work_gives_way_on_windows():
    out = subprocess.run([sys.executable, "-c", _PRIORITY_PROBE], capture_output=True,
                         text=True, check=True).stdout.split()
    before, thread, same, after = (int(v) for v in out)
    assert thread == -1          # job thread: below normal ...
    assert same == before        # ... the process itself (web server) unchanged
    assert after == 0x4000       # worker process: below normal


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX niceness")
def test_background_work_gives_way_on_posix():
    code = ("import os; from doc_anonymizer.config import background_priority; "
            "a = os.nice(0); background_priority(process=False); b = os.nice(0); "
            "background_priority(); print(a, b, os.nice(0))")
    a, b, c = (int(v) for v in subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.split())
    assert b == a and c > a


def test_priority_helper_never_raises():
    background_priority(process=False)  # must be harmless in the test process
