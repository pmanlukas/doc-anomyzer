"""Functions run in worker processes by the tests (must be importable)."""

import os


def work(n: int) -> int:
    if n == 2:
        os._exit(1)  # the worker process dies (like a crash in a native library)
    if n == 5:
        raise ValueError("kaputt")
    return n * 10
