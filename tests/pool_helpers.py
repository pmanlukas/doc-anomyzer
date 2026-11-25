"""Functions for worker processes in the tests (must be importable there)."""

import os
import time


def work(n: int) -> int:
    """Crashes the whole process for 2, fails for 5, otherwise returns ``n * 10``."""
    if n == 2:
        os._exit(1)
    if n == 5:
        raise ValueError("kaputt")
    return n * 10


def slow(seconds: float) -> None:
    time.sleep(seconds)
