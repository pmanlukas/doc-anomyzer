"""Local text recognition (OCR) with Tesseract.

Tesseract runs as a local program – no data leaves the computer. On Windows
the usual installation folders are searched automatically, so the user does
not have to touch the PATH variable.
"""

from __future__ import annotations

import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image

from .config import MODELS_DIR
from .models import Word

# Typical installation folders of the UB Mannheim Windows build.
_WINDOWS_CANDIDATES = [
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Tesseract-OCR" / "tesseract.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
]


# Several Tesseract processes run in parallel (one per page). Without this
# limit each of them starts one OpenMP thread per CPU core and they block each
# other – OCR then becomes up to 50x slower.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")


class OcrUnavailableError(RuntimeError):
    """Raised when a scanned page has to be processed but Tesseract is missing."""


@lru_cache(maxsize=1)
def tesseract_cmd() -> str | None:
    """Locate the tesseract executable (``None`` if it is not installed)."""
    env = os.environ.get("TESSERACT_CMD")
    if env and Path(env).exists():
        return env
    found = shutil.which("tesseract")
    if found:
        return found
    if sys.platform.startswith("win"):
        for candidate in _WINDOWS_CANDIDATES:
            if candidate.exists():
                return str(candidate)
    return None


def tessdata_dir() -> Path | None:
    """Language files downloaded by the setup script (preferred if present)."""
    local = MODELS_DIR / "tessdata"
    return local if (local / "deu.traineddata").exists() else None


def _config(psm: int = 3) -> str:
    # --psm 3: fully automatic page segmentation (letters, forms);
    # --psm 11: "sparse text" – finds isolated or very small text (sender
    #           lines, stamps, letterheads) that psm 3 sometimes skips.
    _use_local_tessdata()
    return f"--psm {psm}"


def _use_local_tessdata() -> None:
    """Point Tesseract to the downloaded language files.

    Done via the TESSDATA_PREFIX environment variable instead of the
    ``--tessdata-dir`` option, because paths with spaces (``C:\\Program
    Files``, user names) are not passed reliably on Windows otherwise.
    """
    tdir = tessdata_dir()
    if tdir is not None:
        os.environ["TESSDATA_PREFIX"] = str(tdir)


def is_available() -> bool:
    return tesseract_cmd() is not None


def available_languages() -> list[str]:
    """Languages Tesseract can use (empty list if Tesseract is missing)."""
    import pytesseract

    cmd = tesseract_cmd()
    if cmd is None:
        return []
    pytesseract.pytesseract.tesseract_cmd = cmd
    try:
        _use_local_tessdata()
        return list(pytesseract.get_languages(config=""))
    except Exception:  # noqa: BLE001 - any failure means "not usable"
        return []


def ocr_words(image: Image.Image, scale: float, language: str = "deu+eng",
              min_confidence: float = 0.0, psm: int = 3) -> list[Word]:
    """Run Tesseract on ``image`` and return words in PDF coordinates.

    ``scale`` converts image pixels to PDF points (72 / dpi). A low
    ``min_confidence`` is deliberate: for anonymisation it is better to look at
    a poorly recognised word than to silently ignore it.
    """
    import pytesseract

    cmd = tesseract_cmd()
    if cmd is None:
        raise OcrUnavailableError(
            "Tesseract (Texterkennung) ist nicht installiert. Eingescannte Seiten "
            "können ohne Texterkennung nicht anonymisiert werden.")
    pytesseract.pytesseract.tesseract_cmd = cmd

    data = pytesseract.image_to_data(image, lang=language, config=_config(psm),
                                     output_type=pytesseract.Output.DICT)
    words: list[Word] = []
    for i, text in enumerate(data["text"]):
        text = (text or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if 0 <= conf < min_confidence:
            continue
        # Typical OCR confusion in legal texts: "§" read as "$".
        if text.startswith("$") and (len(text) == 1 or text[1].isdigit()):
            text = "§" + text[1:]
        x, y, w, h = (data[k][i] for k in ("left", "top", "width", "height"))
        # Unique line id across blocks/paragraphs so words can be grouped later.
        line_id = (data["block_num"][i] * 10_000 + data["par_num"][i] * 100
                   + data["line_num"][i])
        words.append(Word(x * scale, y * scale, (x + w) * scale, (y + h) * scale,
                          text, "ocr", psm * 1_000_000 + line_id, max(conf, 0.0)))
    return words
