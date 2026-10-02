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
MODELS_DIR = Path(os.environ.get("DOC_ANONYMIZER_MODELS", APP_ROOT / "models"))


def home_dir() -> Path:
    """Return (and create) the private working directory of the application."""
    override = os.environ.get("DOC_ANONYMIZER_HOME")
    if override:
        base = Path(override)
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DocAnonymizer"
    else:
        base = Path.home() / ".doc_anonymizer"
    base.mkdir(parents=True, exist_ok=True)
    return base


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
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        settings = cls(**known)
        # Guard against unknown category names from older versions.
        valid = {c.value for c in Category}
        settings.categories = [c for c in settings.categories if c in valid]
        return settings

    def save(self) -> None:
        self.path().write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False),
                               encoding="utf-8")

    def enabled(self, category: Category) -> bool:
        return category is Category.MANUAL or category.value in self.categories
