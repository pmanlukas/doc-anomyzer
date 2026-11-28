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

from .config import BUNDLED_TESSERACT, MODELS_DIR
from .errors import UserError
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


class OcrUnavailableError(UserError):
    """Raised when a scanned page has to be processed but Tesseract is missing."""


#: Longest time Tesseract may need for one page (seconds). Normal pages take
#: a few seconds; a hanging run must not block the whole queue forever.
OCR_TIMEOUT = 300


@lru_cache(maxsize=1)
def tesseract_cmd() -> str | None:
    """Locate the tesseract executable (``None`` if it is not installed).

    The copy shipped with the Windows setup comes first: it matches the
    bundled language files and needs no separate installation.
    """
    if BUNDLED_TESSERACT.exists():
        return str(BUNDLED_TESSERACT)
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


#: Suffix of the second, independent set of language files ("deu_fast").
FAST_SUFFIX = "_fast"


def reading_language(language: str, fast: bool = False) -> str:
    """The Tesseract language(s) for a run.

    Two sets of language files are shipped: the accurate one ("deu") reads
    the documents and every noisy image; the fast one ("deu_fast", a smaller
    network that needs less than half the time) is used where the image is a
    crisp render of digital text and both read equally well – the check of
    finished text pages. That halves the longest step for digital documents,
    which matters on older computers. Where a fast file is missing the
    accurate one is used.
    """
    if not fast:
        return language
    tdir = tessdata_dir()
    if tdir is None:
        return language
    return "+".join(lang + FAST_SUFFIX if (tdir / f"{lang}{FAST_SUFFIX}.traineddata").exists()
                    else lang for lang in language.split("+"))


def _give_way() -> None:
    """Start Tesseract with a lower priority (Windows, once per process).

    Text recognition keeps every core busy; started with "below normal"
    priority it never holds up the program window or other programs. (Worker
    processes run with this priority anyway and pass it on; this is for
    documents the main process reads itself.)
    """
    import subprocess

    import pytesseract.pytesseract as backend

    if not sys.platform.startswith("win") or getattr(backend.subprocess_args, "_gives_way", False):
        return
    original = backend.subprocess_args

    def subprocess_args(*args, **kwargs):
        options = original(*args, **kwargs)
        options["creationflags"] = (options.get("creationflags", 0)
                                    | subprocess.BELOW_NORMAL_PRIORITY_CLASS
                                    | subprocess.CREATE_NO_WINDOW)
        return options

    subprocess_args._gives_way = True  # type: ignore[attr-defined]
    backend.subprocess_args = subprocess_args


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
              min_confidence: float = 0.0, psm: int = 3, fast: bool = False) -> list[Word]:
    """Run Tesseract on ``image`` and return words in PDF coordinates.

    ``scale`` converts image pixels to PDF points (72 / dpi). A low
    ``min_confidence`` is deliberate: for anonymisation it is better to look at
    a poorly recognised word than to silently ignore it. ``fast`` selects the
    second set of language files (see :func:`reading_language`).
    """
    import pytesseract

    cmd = tesseract_cmd()
    if cmd is None:
        raise OcrUnavailableError(
            "Tesseract (Texterkennung) ist nicht installiert. Eingescannte Seiten "
            "können ohne Texterkennung nicht anonymisiert werden.")
    pytesseract.pytesseract.tesseract_cmd = cmd
    _give_way()

    try:
        config = _config(psm)  # also points Tesseract to the language files
        data = pytesseract.image_to_data(image, lang=reading_language(language, fast),
                                         config=config,
                                         output_type=pytesseract.Output.DICT,
                                         timeout=OCR_TIMEOUT)
    except RuntimeError as exc:
        if "timeout" not in str(exc).lower():
            raise
        raise UserError("Die Texterkennung hat für eine Seite zu lange gebraucht und wurde "
                        "abgebrochen. Bitte die Seite prüfen (sehr großes oder beschädigtes "
                        "Bild?) oder das Dokument ohne diese Seite verarbeiten.") from exc
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
    _restore_section_signs(words)
    return words


#: Abbreviations of German statutes that follow a section number.
_STATUTES = {"BGB", "ZPO", "StGB", "StPO", "HGB", "GG", "SGB", "KSchG", "AO", "UStG", "EStG",
             "VwGO", "VwVfG", "InsO", "BetrVG", "ArbGG", "GKG", "RVG", "DSGVO", "BDSG", "StVG",
             "StVO", "VVG", "WEG", "AGG", "TzBfG", "GmbHG", "AktG", "UWG", "BauGB", "FamFG",
             "GVG", "OWiG", "EGBGB", "BUrlG", "MuSchG", "ArbZG", "EFZG", "GewO", "BeurkG"}


def _restore_section_signs(words: list[Word]) -> None:
    """Tesseract sometimes reads "§" as "S": "S 622 BGB" -> "§ 622 BGB".

    Only where a number and (within the next words of the line) a statute
    follow – "S 12" alone may be a page reference.
    """
    for i, word in enumerate(words):
        if word.text != "S" or i + 1 >= len(words) or not words[i + 1].text[:1].isdigit():
            continue
        for later in words[i + 2:i + 7]:
            if later.line_id != word.line_id:
                break
            if later.text.strip(".,;:()") in _STATUTES:
                word.text = "§"
                break
