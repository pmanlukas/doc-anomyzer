"""Build the Windows ZIP package for lawyers.

    python tools/build_zip.py [ZIELORDNER]

The archive contains the program, the start/installation scripts and the
short guide – no tests, no models, no caches and of course no documents.
Everything else (Python packages, language model, Tesseract) is downloaded
by ``Installieren.bat`` on the target computer.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from doc_anonymizer import __version__  # noqa: E402

#: Top-level files shipped to the user.
FILES = ["ANLEITUNG.txt", "Installieren.bat", "Start.bat", "Modell_trainieren.bat",
         "Zusatzmodell_installieren.bat", "README.md", "LICENSE", "requirements.txt",
         "requirements-ki.txt"]
#: Never shipped, whatever lies in the working copy.
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", "models", ".venv"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".pdf", ".log"}


def package_files() -> list[Path]:
    files = [ROOT / name for name in FILES]
    for path in sorted((ROOT / "doc_anonymizer").rglob("*")):
        if path.is_file() and not EXCLUDED_PARTS & set(path.parts) \
                and path.suffix not in EXCLUDED_SUFFIXES:
            files.append(path)
    return files


def build(target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"Dokumenten-Anonymisierer_{__version__}.zip"
    folder = "Dokumenten-Anonymisierer"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in package_files():
            data = path.read_bytes()
            if path.suffix == ".bat" and b"\r\n" not in data:
                data = data.replace(b"\n", b"\r\n")  # cmd.exe needs CRLF
            zf.writestr(f"{folder}/{path.relative_to(ROOT).as_posix()}", data)
    return target


if __name__ == "__main__":
    out = build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist")
    print(f"{out} ({out.stat().st_size / 1e6:.1f} MB)")
