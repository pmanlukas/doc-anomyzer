"""Measure the anonymiser on the synthetic corpus.

    python -m tests.evaluate --anzahl 24 --scan
"""

from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

import pymupdf

from doc_anonymizer.config import Settings
from doc_anonymizer.detect.pipeline import Detector
from doc_anonymizer.extract import analyse_document
from doc_anonymizer.process import anonymise_document
from doc_anonymizer.registry import CaseRegistry

from .corpus import evaluate, generate


def run(out_dir: Path, count: int, seed: int = 1, scan_every: int = 3,
        settings: Settings | None = None, verbose: bool = True, quality: str = "gut") -> dict:
    """Anonymise every generated document and compare with the ground truth."""
    settings = settings or Settings(use_gliner=False)
    detector = Detector(settings)
    totals = {"docs": 0, "secrets": 0, "leaks": [], "missing": [], "failed_checks": [],
              "seconds": 0.0}
    for sample, path in generate(out_dir / "in", count, seed, scan_every, quality):
        start = time.time()
        pages = analyse_document(path, settings)
        registry = CaseRegistry([pages], settings)
        registry.build([detector.detect_document(pages)])
        target = out_dir / "out" / path.name
        result = anonymise_document(path, target, 0, pages, registry, settings)
        totals["seconds"] += time.time() - start
        with pymupdf.open(target) as pdf:
            text = "\n".join(page.get_text() for page in pdf)
        text += "\n" + "\n".join(result.redaction.texts)
        leaks, missing = evaluate(sample, text)
        totals["docs"] += 1
        totals["secrets"] += len(sample.secrets)
        totals["leaks"] += [(path.name, *leak) for leak in leaks]
        totals["missing"] += [(path.name, m) for m in missing]
        if not result.verification.passed:
            totals["failed_checks"].append(path.name)
        if verbose:
            state = "OK " if not leaks else "LECK"
            print(f"{state} {path.name:38} {len(sample.secrets):3} Angaben, "
                  f"{len(leaks)} Lecks, {len(missing)} fehlende Fachbegriffe, "
                  f"{result.repairs} Nachbesserungen")
            for leak in leaks:
                print("      Leck:", leak)
            for m in missing:
                print("      fehlt:", m)
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anzahl", type=int, default=12)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--nur-scans", action="store_true")
    parser.add_argument("--qualitaet", default="gut",
                        choices=["gut", "mittel", "schlecht", "sehr_schlecht"])
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    out = args.out or Path(tempfile.mkdtemp(prefix="anon_eval_"))
    scan_every = 1 if args.nur_scans else (3 if args.scan else 0)
    totals = run(out, args.anzahl, args.seed, scan_every, quality=args.qualitaet)
    found = totals["secrets"] - len(totals["leaks"])
    print("-" * 70)
    print(f"{totals['docs']} Dokumente, {totals['secrets']} eingefügte Angaben, "
          f"{len(totals['leaks'])} Lecks ({100 * found / max(totals['secrets'], 1):.1f} % entfernt), "
          f"{len(totals['missing'])} fehlende Fachbegriffe, "
          f"{totals['seconds']:.0f} s gesamt")


if __name__ == "__main__":
    main()
