"""One-time downloads used by the Windows installer.

Only *downloads* happen here (language files, models). No document data is
ever sent anywhere – after installation the program works offline.

    python -m doc_anonymizer.setup_helper tessdata
    python -m doc_anonymizer.setup_helper gliner
"""

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

from .config import MODELS_DIR

#: Direct address first (no redirect – works behind more firewalls), then
#: the GitHub web address as a fallback.
TESSDATA_URLS = (
    "https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main/{lang}.traineddata",
    "https://github.com/tesseract-ocr/tessdata_best/raw/main/{lang}.traineddata",
)


def _download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")

    def hook(blocks: int, block_size: int, total: int) -> None:
        if total > 0:
            done = min(100, blocks * block_size * 100 // total)
            print(f"\r  {target.name}: {done:3d} %", end="", flush=True)

    urllib.request.urlretrieve(url, tmp, hook)  # noqa: S310 - fixed https URL
    tmp.replace(target)
    print()


def tessdata(languages: tuple[str, ...] = ("deu", "eng", "osd")) -> None:
    """German/English language files for Tesseract (best accuracy models)."""
    target_dir = MODELS_DIR / "tessdata"
    for lang in languages:
        target = target_dir / f"{lang}.traineddata"
        if target.exists() and target.stat().st_size > 1_000_000:
            print(f"  {target.name} bereits vorhanden")
            continue
        for n, url in enumerate(TESSDATA_URLS, 1):
            try:
                _download(url.format(lang=lang), target)
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
