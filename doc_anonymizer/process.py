"""Redaction with built-in verification and automatic repair.

The verification is not only a report: if it still finds original data, the
affected places are redacted *additionally* and the document is produced and
checked again (at most ``MAX_ROUNDS`` times). Only if data remain after that
is the result marked as failed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pymupdf

from .config import Settings
from .models import ManualBox, PageInfo
from .redact import RedactionResult, redact_document
from .registry import CaseRegistry
from .verify import VerificationResult, verify_document

MAX_ROUNDS = 3
ProgressFn = Callable[[int, int, str], None]


@dataclass
class ProcessResult:
    redaction: RedactionResult
    verification: VerificationResult
    repairs: int = 0  # number of automatically added redactions


def _repair(source: Path, doc_index: int, registry: CaseRegistry,
            verification: VerificationResult) -> tuple[int, set[int]]:
    """Add redactions for every leak found.

    Returns the number of fixes and the (0-based) pages that changed.
    """
    fixes = 0
    changed: set[int] = set()
    before = {id(m) for m in registry.mentions}
    with pymupdf.open(source) as pdf:
        for leak in verification.leaks:
            entity = registry.entities.get(leak.entity_id)
            if entity is None or leak.page < 1:
                continue
            if leak.rects:
                # Text visible in the page image: cover exactly these words.
                derot = pdf[leak.page - 1].derotation_matrix
                for x0, y0, x1, y1 in leak.rects:
                    r = pymupdf.Rect(x0, y0, x1, y1) * derot
                    r.normalize()
                    box = ManualBox(doc_index, leak.page - 1, r.x0, r.y0, r.x1, r.y1,
                                    entity.replacement)
                    if box not in registry.manual_boxes:
                        registry.manual_boxes.append(box)
                        changed.add(leak.page - 1)
                        fixes += 1
            else:
                # Text layer: the term occurs inside a longer word or in an
                # unexpected spelling – redact every word containing it.
                count = len(registry.mentions)
                registry.redact_occurrences(entity, leak.term)
                fixes += len(registry.mentions) - count
    changed |= {m.page for m in registry.mentions
                if id(m) not in before and m.doc == doc_index}
    if fixes:
        registry.assign_labels()
    return fixes, changed


def anonymise_document(source: Path, target: Path, doc_index: int, pages: list[PageInfo],
                       registry: CaseRegistry, settings: Settings,
                       progress: ProgressFn | None = None) -> ProcessResult:
    """Redact, verify and – if necessary – repair and redo."""
    repairs = 0
    image_pages: set[int] | None = None  # first round: all pages
    for _ in range(MAX_ROUNDS):
        redaction = redact_document(source, target, doc_index, pages, registry, settings,
                                    progress)
        verification = verify_document(target, doc_index, len(pages), registry, settings,
                                       progress, image_pages=image_pages)
        if not verification.leaks:
            break
        fixed, changed = _repair(source, doc_index, registry, verification)
        repairs += fixed
        if not fixed:
            break  # nothing more we can do automatically
        # Re-check images only where something changed or was found; the text
        # check (stage 1) always covers all pages.
        image_pages = (image_pages or set()) | changed | {
            leak.page - 1 for leak in verification.leaks if leak.page > 0}
    return ProcessResult(redaction, verification, repairs)
