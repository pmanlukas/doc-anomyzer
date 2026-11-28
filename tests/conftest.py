"""Shared fixtures: every test runs with its own private application folder."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None,
                                     reason="Tesseract nicht installiert")


@pytest.fixture(autouse=True)
def private_home(tmp_path, monkeypatch):
    """Isolated settings/feedback/case storage per test."""
    home = tmp_path / "home"
    monkeypatch.setenv("DOC_ANONYMIZER_HOME", str(home))
    from doc_anonymizer.config import Settings

    settings = Settings(use_gliner=False, output_dir=str(tmp_path / "ausgabe"))
    home.mkdir()
    settings.save()
    return home


@pytest.fixture
def settings(tmp_path):
    from doc_anonymizer.config import Settings

    return Settings.load()
