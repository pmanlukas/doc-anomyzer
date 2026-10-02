"""Attack tests: after anonymisation nothing must be recoverable.

Each case hides the name "Mustermann" somewhere else in the PDF (see
``attack_docs.py``). The original file is attacked first to prove that the
hiding place really works; then the anonymised file must resist all
techniques.
"""

import pytest

from doc_anonymizer.detect.pipeline import Detector
from doc_anonymizer.extract import analyse_document
from doc_anonymizer.process import anonymise_document
from doc_anonymizer.registry import CaseRegistry

from . import attack_docs
from .conftest import needs_tesseract


@needs_tesseract
@pytest.mark.parametrize("make", attack_docs.CASES, ids=lambda f: f.__name__)
def test_nothing_recoverable(make, tmp_path, settings):
    source = make(tmp_path / f"{make.__name__}.pdf")
    assert attack_docs.recover(source), "test case does not hide the name"

    pages = analyse_document(source, settings)
    registry = CaseRegistry([pages], settings)
    registry.build([Detector(settings).detect_document(pages)])
    target = tmp_path / "ergebnis.pdf"
    result = anonymise_document(source, target, 0, pages, registry, settings)

    assert attack_docs.recover(target) == [], "recoverable"
    assert result.verification.passed, [c for c in result.verification.checks if not c.ok]
    # The legal content must survive.
    import pymupdf
    with pymupdf.open(target) as pdf:
        text = " ".join(p.get_text() for p in pdf)
    assert "§ 280 Abs. 1 BGB" in text
