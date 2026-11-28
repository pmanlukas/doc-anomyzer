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
    import secrets
    import socket

    from waitress import create_server

    from .config import private_temp_dir
    from .web import create_app

    # Only the loopback interface (unreachable from other computers), and
    # exclusively: no second program can listen on the same port.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform.startswith("win"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        print(f"Der Anschluss (Port) {port} ist belegt – läuft das Programm schon?\n"
              "Sonst mit einem anderen starten:  python -m doc_anonymizer start --port 8770")
        raise SystemExit(1) from None
    private_temp_dir(clean=True)
    # Access token: only a browser that was let in with a ticket may use the
    # program (protects against other users and programs on this computer).
    app = create_app(token=secrets.token_urlsafe(32))
    new_ticket = app.extensions["anonymizer"]["new_ticket"]
    base = f"http://127.0.0.1:{port}/"
    print("=" * 72)
    print(" Dokumenten-Anonymisierer läuft – nur auf diesem Computer.")
    print(" Im Browser öffnen (die Adresse gilt nur ein einziges Mal):")
    print(f"   {base}?ticket={new_ticket(ttl=None)}")
    print(" Zum Beenden dieses Fenster schließen (oder Strg+C).")
    print("=" * 72)
    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"{base}?ticket={new_ticket()}")).start()
    create_server(app, sockets=[sock], threads=8, max_request_body_size=2 * 1024 ** 3).run()


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
    from .parallel import describe_resources, low_memory

    # What the program found on this computer and how it will work with it.
    print(f"[OK]   Rechner: {describe_resources()}")
    if low_memory():
        print("[HINWEIS] Wenig Arbeitsspeicher: Dokumente werden nacheinander verarbeitet "
              "(langsamer, aber ohne Auslagern). Empfohlen sind 8 GB.")
    print(f"[{'OK' if ner.gliner_installed() else 'optional'}] KI-Zusatzmodell GLiNER "
          f"{'installiert' if ner.gliner_installed() else 'nicht installiert'}")
    return 0 if ok else 1


def _anonymise(files: list[Path], output: Path | None, auto: bool) -> int:
    """Batch mode without review (for tests and power users)."""
    from .config import Settings
    from .detect.pipeline import Detector
    from .errors import friendly_message
    from .extract import analyse_document
    from .jobs import FAILED_FOLDER, NameAnonymiser
    from .process import anonymise_document
    from .registry import CaseRegistry
    from .verify import search_terms

    settings = Settings.load()
    if output:
        settings.output_dir = str(output)
    out_dir = Path(settings.output_dir)
    detector = Detector(settings)

    def progress(done: int, total: int, step: str) -> None:
        print(f"\r  {step}: {done}/{total}   ", end="", flush=True)

    failed = False
    docs, detections = [], []
    for path in files:
        print(f"Analysiere {path.name}")
        try:
            pages = analyse_document(path, settings, progress)
            found = detector.detect_document(pages, progress)
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the batch
            print(f"\n  NICHT LESBAR: {friendly_message(exc)}")
            pages, found, failed = [], [], True
        docs.append(pages)
        detections.append(found)
        print()
    registry = CaseRegistry(docs, settings)
    registry.build(detections)
    print(f"{len(registry.entities)} Einträge gefunden.")
    if not auto:
        for e in registry.entities.values():
            print(f"  {e.category.label_de:30} {e.replacement:18} ({e.count}x)")
    names, terms = NameAnonymiser(registry), search_terms(registry)
    for i, path in enumerate(files):
        if not docs[i]:
            continue
        # File names often contain the client's name – never taken over as it is.
        stem = names.clean(path.stem, terms, f"Dokument_{i + 1:03d}")
        target = out_dir / f"{stem}_anonymisiert.pdf"
        try:
            processed = anonymise_document(path, target, i, docs[i], registry, settings,
                                           progress)
        except Exception as exc:  # noqa: BLE001
            target.unlink(missing_ok=True)
            print(f"\n{path.name}: FEHLER – {friendly_message(exc)}")
            failed = True
            continue
        passed = processed.verification.passed
        if not passed:
            # Must not look like a finished file.
            (out_dir / FAILED_FOLDER).mkdir(parents=True, exist_ok=True)
            target = target.replace(out_dir / FAILED_FOLDER / target.name)
        print(f"\n{target}: {'Prüfung bestanden' if passed else 'PRÜFUNG FEHLGESCHLAGEN'}")
        failed |= not passed
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
    for stream in (sys.stdout, sys.stderr):
        # A Windows console cannot show every character – never crash on output.
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

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
