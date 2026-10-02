"""Command line entry point.

    python -m doc_anonymizer               # start the browser interface
    python -m doc_anonymizer check         # check installation
    python -m doc_anonymizer trainieren    # fine-tune the local model
    python -m doc_anonymizer anonymisieren a.pdf b.pdf --ausgabe Ordner
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser
from pathlib import Path


def _serve(port: int, open_browser: bool) -> None:
    from .web import create_app

    app = create_app()
    url = f"http://127.0.0.1:{port}/"
    print("=" * 64)
    print(" Dokumenten-Anonymisierer läuft – nur auf diesem Computer.")
    print(f" Im Browser öffnen: {url}")
    print(" Zum Beenden dieses Fenster schließen (oder Strg+C).")
    print("=" * 64)
    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    try:
        from waitress import serve

        # Only the loopback interface: unreachable from other computers.
        serve(app, host="127.0.0.1", port=port, threads=8, max_request_body_size=2 * 1024 ** 3)
    except ImportError:
        app.run(host="127.0.0.1", port=port, threaded=True)


def _check() -> int:
    """Print the state of all components (used by the installer)."""
    from . import ocr
    from .detect import ner

    ok = True
    try:
        nlp = ner.load_spacy()
        print(f"[OK]   Sprachmodell spaCy: {nlp.meta.get('name')} {nlp.meta.get('version')}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[FEHLT] Sprachmodell spaCy: {exc}")
    if ocr.is_available():
        langs = ocr.available_languages()
        has_deu = "deu" in langs
        ok &= has_deu
        print(f"[{'OK' if has_deu else 'FEHLT'}]   Texterkennung Tesseract: "
              f"{ocr.tesseract_cmd()} (Sprachen: {', '.join(langs) or '-'})")
    else:
        ok = False
        print("[FEHLT] Texterkennung Tesseract (für eingescannte PDFs)")
    print(f"[{'OK' if ner.gliner_installed() else 'optional'}] KI-Zusatzmodell GLiNER "
          f"{'installiert' if ner.gliner_installed() else 'nicht installiert'}")
    return 0 if ok else 1


def _anonymise(files: list[Path], output: Path | None, auto: bool) -> int:
    """Batch mode without review (for tests and power users)."""
    from .config import Settings
    from .detect.pipeline import Detector
    from .extract import analyse_document
    from .process import anonymise_document
    from .registry import CaseRegistry

    settings = Settings.load()
    if output:
        settings.output_dir = str(output)
    out_dir = Path(settings.output_dir)
    detector = Detector(settings)

    def progress(done: int, total: int, step: str) -> None:
        print(f"\r  {step}: {done}/{total}   ", end="", flush=True)

    docs, detections = [], []
    for path in files:
        print(f"Analysiere {path.name}")
        pages = analyse_document(path, settings, progress)
        docs.append(pages)
        detections.append(detector.detect_document(pages, progress))
        print()
    registry = CaseRegistry(docs, settings)
    registry.build(detections)
    print(f"{len(registry.entities)} Einträge gefunden.")
    if not auto:
        for e in registry.entities.values():
            print(f"  {e.category.label_de:30} {e.replacement:18} ({e.count}x)")
    failed = False
    for i, path in enumerate(files):
        target = out_dir / f"{path.stem}_anonymisiert.pdf"
        processed = anonymise_document(path, target, i, docs[i], registry, settings, progress)
        verification = processed.verification
        print(f"\n{target}: {'Prüfung bestanden' if verification.passed else 'PRÜFUNG FEHLGESCHLAGEN'}")
        failed |= not verification.passed
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="doc_anonymizer", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("start", help="Browser-Oberfläche starten (Standard)")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--kein-browser", action="store_true")
    sub.add_parser("check", help="Installation prüfen")
    sub.add_parser("trainieren", help="Sprachmodell mit den eigenen Entscheidungen nachtrainieren")
    batch = sub.add_parser("anonymisieren", help="PDFs ohne Oberfläche anonymisieren")
    batch.add_argument("dateien", nargs="+", type=Path)
    batch.add_argument("--ausgabe", type=Path)
    batch.add_argument("--still", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if args.command == "check":
        return _check()
    if args.command == "trainieren":
        from .train import train

        result = train()
        print(result["message"])
        return 0 if result["ok"] else 1
    if args.command == "anonymisieren":
        return _anonymise(args.dateien, args.ausgabe, args.still)
    _serve(getattr(args, "port", 8765), not getattr(args, "kein_browser", False))
    return 0


if __name__ == "__main__":
    # Needed for the worker processes when packaged as Windows .exe.
    import multiprocessing

    multiprocessing.freeze_support()
    sys.exit(main())
