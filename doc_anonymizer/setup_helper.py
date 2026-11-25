"""One-time downloads used by the Windows installer.

Only *downloads* happen here (language files, models). No document data is
ever sent anywhere – after installation the program works offline.

    python -m doc_anonymizer.setup_helper tessdata
    python -m doc_anonymizer.setup_helper gliner
"""

from __future__ import annotations

import hashlib
import os
import sys
import urllib.request
from pathlib import Path

from .config import MODELS_DIR

#: Fixed release of the language files (not a moving branch). Direct address
#: first (no redirect – works behind more firewalls), then the GitHub web
#: address as a fallback.
TESSDATA_URLS = (
    "https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/4.1.0/{lang}.traineddata",
    "https://github.com/tesseract-ocr/tessdata_best/raw/4.1.0/{lang}.traineddata",
)
#: SHA-256 of the files: a download that was tampered with (or cut off) is
#: never used. (The same values are checked when the Windows setup is built.)
TESSDATA_SHA256 = {
    "deu": "8407331d6aa0229dc927685c01a7938fc5a641d1a9524f74838cdac599f0d06e",
    "eng": "8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba",
    "osd": "9cf5d576fcc47564f11265841e5ca839001e7e6f38ff7f7aacf46d15a96b00ff",
}


class ChecksumError(OSError):
    """A downloaded file is not the expected one."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, target: Path, expected: str | None = None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")

    def hook(blocks: int, block_size: int, total: int) -> None:
        if total > 0:
            done = min(100, blocks * block_size * 100 // total)
            print(f"\r  {target.name}: {done:3d} %", end="", flush=True)

    urllib.request.urlretrieve(url, tmp, hook)  # noqa: S310 - fixed https URL
    print()
    if expected is not None and sha256(tmp) != expected:
        tmp.unlink(missing_ok=True)
        raise ChecksumError(f"{target.name}: Prüfsumme stimmt nicht – Datei verworfen")
    tmp.replace(target)


def tessdata(languages: tuple[str, ...] = ("deu", "eng", "osd")) -> None:
    """German/English language files for Tesseract (best accuracy models)."""
    target_dir = MODELS_DIR / "tessdata"
    for lang in languages:
        target = target_dir / f"{lang}.traineddata"
        expected = TESSDATA_SHA256.get(lang)
        if target.exists() and (expected is None or sha256(target) == expected):
            print(f"  {target.name} bereits vorhanden")
            continue
        for n, url in enumerate(TESSDATA_URLS, 1):
            try:
                _download(url.format(lang=lang), target, expected)
                break
            except OSError as exc:  # HTTPError/URLError are OSErrors
                print(f"\n  {target.name}: Download fehlgeschlagen ({exc})")
                if n == len(TESSDATA_URLS):
                    raise


def gliner() -> None:
    """Download the optional GLiNER model into the local model folder."""
    from .detect.ner import GLINER_MODEL, HF_CACHE

    os.environ["HF_HOME"] = str(HF_CACHE)
    os.environ.pop("HF_HUB_OFFLINE", None)
    from gliner import GLiNER

    print(f"  Lade {GLINER_MODEL} (einmalig, ca. 1 GB) …")
    model = GLiNER.from_pretrained(GLINER_MODEL)
    model.predict_entities("Herr Max Mustermann wohnt in Berlin.", ["person"])
    print("  Zusatzmodell bereit.")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "tessdata":
        tessdata()
    elif command == "gliner":
        gliner()
    else:
        print(__doc__)
        sys.exit(2)
