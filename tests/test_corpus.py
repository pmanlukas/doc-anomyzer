"""Measured quality on fictitious case files (ground truth known)."""

from .conftest import needs_tesseract
from .evaluate import run


@needs_tesseract
def test_no_leaks_on_corpus(tmp_path, settings):
    totals = run(tmp_path, count=6, seed=21, scan_every=3, settings=settings, verbose=False)
    assert totals["leaks"] == []
    assert totals["failed_checks"] == []
