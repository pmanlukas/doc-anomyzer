"""Application settings and local storage locations.

Everything is stored on the local machine only:

* Windows: ``%LOCALAPPDATA%\\DocAnonymizer``
* other systems: ``~/.doc_anonymizer``

The location can be overridden with the environment variable
``DOC_ANONYMIZER_HOME`` (used by the tests).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .models import Category

#: Root of the source checkout (contains ``models/`` with downloaded models).
APP_ROOT = Path(__file__).resolve().parent.parent
#: Bundled word lists (first names, stop words ...).
DATA_DIR = Path(__file__).resolve().parent / "data"
#: Locally stored AI models / OCR language files (downloaded once by setup).
#: Part of the program – read only; updates replace it.
MODELS_DIR = Path(os.environ.get("DOC_ANONYMIZER_MODELS", APP_ROOT / "models"))
#: Tesseract shipped with the Windows setup (preferred over an installed one).
BUNDLED_TESSERACT = APP_ROOT / "tesseract" / (
    "tesseract.exe" if sys.platform.startswith("win") else "tesseract")


def home_dir() -> Path:
    """Return (and create) the private working directory of the application."""
    override = os.environ.get("DOC_ANONYMIZER_HOME")
    if override:
        base = Path(override)
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DocAnonymizer"
    else:
        base = Path.home() / ".doc_anonymizer"
    # Private to the user (other accounts on a shared computer must not read
    # case files, settings or the access token). On Windows the profile
    # folder already is; elsewhere the mode is set explicitly.
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        # A folder from an older version (or created by someone else) may be
        # readable for other accounts – tighten it once.
        try:
            if base.stat().st_mode & 0o077:
                base.chmod(0o700)
        except OSError:
            pass
    return base


def user_models_dir() -> Path:
    """Models the program creates itself (fine-tuned language model).

    They belong to the user's learning progress and therefore live in the
    user folder – an update or uninstall of the program never deletes them.
    """
    path = home_dir() / "modelle"
    path.mkdir(parents=True, exist_ok=True)
    return path


def custom_model_dir() -> Path:
    """Location of the fine-tuned spaCy model."""
    return user_models_dir() / "spacy_angepasst"


def default_output_dir() -> Path:
    """Folder where finished documents are written (visible to the user).

    Deliberately *not* inside "Documents"/"Desktop": on many Windows PCs these
    folders are synchronised to OneDrive, i.e. to a cloud service.
    """
    return Path.home() / "Anonymisiert"


CLOUD_FOLDER_HINTS = ("onedrive", "dropbox", "google drive", "googledrive", "icloud",
                      "nextcloud", "owncloud", "box sync", "magentacloud")


def is_cloud_synced(path: str | Path) -> bool:
    """Heuristic: does the folder belong to a cloud synchronisation client?"""
    lowered = str(path).casefold()
    return any(hint in lowered for hint in CLOUD_FOLDER_HINTS)


# Categories that are switched on by default. Everything is on – the user
# asked for removal of *all* personal data in the sense of the GDPR/BDSG.
DEFAULT_CATEGORIES = [c.value for c in Category if c is not Category.MANUAL]


@dataclass
class Settings:
    """User adjustable settings, persisted as JSON in :func:`home_dir`."""

    #: Enabled detection categories (values of :class:`Category`).
    categories: list[str] = field(default_factory=lambda: list(DEFAULT_CATEGORIES))
    #: "standard": keep the digital text layer (best readability, small files);
    #: "maximum": every page is re-built as an image with a fresh text layer.
    security_mode: str = "standard"
    #: Use the optional GLiNER transformer model in addition to spaCy.
    use_gliner: bool = True
    #: Minimum confidence for GLiNER findings.
    gliner_threshold: float = 0.45
    #: Resolution used for OCR and rasterised output pages.
    ocr_dpi: int = 300
    raster_dpi: int = 200
    #: Second OCR pass for small/isolated print (slower, more thorough).
    thorough_ocr: bool = True
    #: Tesseract language(s).
    ocr_language: str = "deu+eng"
    #: Fraction of the page covered by images above which the page is OCR'd.
    image_coverage_for_ocr: float = 0.15
    #: Remove small images on text pages completely (logos, signatures,
    #: stamps, photos) – they often contain names that OCR cannot read reliably.
    remove_small_images: bool = True
    #: An image counts as "small" below this share of the page area.
    small_image_max_coverage: float = 0.25
    #: Detect handwritten signatures, handwriting and stamps on scans.
    detect_signatures: bool = True
    #: Stage 2: read every finished page with OCR after anonymisation.
    deep_verification: bool = True
    #: Stage 3: recovery attempts with independent tools (forensics).
    forensic_check: bool = True
    #: Learn from the user's decisions (stored locally in feedback.jsonl).
    use_learning: bool = True
    #: Re-train the local language model automatically when enough new
    #: examples were collected (only kept if it measurably improves).
    auto_train: bool = True
    #: Write the confidential mapping table (original -> replacement).
    write_mapping: bool = True
    #: Folder for finished files.
    output_dir: str = field(default_factory=lambda: str(default_output_dir()))
    #: Working data of a case (copies of the *original* documents, the list
    #: of found names) is deleted automatically after this many days without
    #: activity; 0 = keep until deleted by hand. Finished files are not touched.
    keep_days: int = 30
    #: Open the program in its own window (Microsoft Edge "app" window)
    #: instead of a tab of the default browser.
    app_window: bool = True
    #: Terms that must never be redacted (one per entry, case-insensitive).
    allow_terms: list[str] = field(default_factory=list)
    #: Terms that must always be redacted: list of {"text": ..., "category": ...}.
    deny_terms: list[dict] = field(default_factory=list)

    # -- persistence -------------------------------------------------------

    @staticmethod
    def path() -> Path:
        return home_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        """Load settings from disk, falling back to defaults on any problem."""
        try:
            raw = json.loads(cls.path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        try:
            settings = cls(**known)
        except TypeError:
            return cls()
        # Guard against unknown category names from older versions.
        valid = {c.value for c in Category}
        settings.categories = [c for c in settings.categories if c in valid]
        return settings

    def save(self) -> None:
        # Written to a temporary file first: a crash while writing must not
        # leave a broken settings file behind.
        path = self.path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(path)

    def enabled(self, category: Category) -> bool:
        return category is Category.MANUAL or category.value in self.categories
