"""Core data structures shared by extraction, detection, redaction and verification.

All coordinates are PDF user-space points (1/72 inch) in PyMuPDF's
coordinate system (origin top-left, y grows downwards).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Category(str, Enum):
    """Categories of personal data the tool knows about.

    The string value is stable and used in JSON files; the German label is
    what the lawyer sees in the user interface and inside placeholders.
    """

    PERSON = "PERSON"
    ORG = "ORG"
    LOCATION = "LOCATION"
    ADDRESS = "ADDRESS"
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    URL = "URL"
    IBAN = "IBAN"
    BIC = "BIC"
    CREDIT_CARD = "CREDIT_CARD"
    TAX_ID = "TAX_ID"
    SOCIAL_SECURITY = "SOCIAL_SECURITY"
    ID_DOCUMENT = "ID_DOCUMENT"
    LICENSE_PLATE = "LICENSE_PLATE"
    BIRTH_DATE = "BIRTH_DATE"
    CASE_NUMBER = "CASE_NUMBER"
    REFERENCE_NUMBER = "REFERENCE_NUMBER"
    REGISTER_NUMBER = "REGISTER_NUMBER"
    IP_ADDRESS = "IP_ADDRESS"
    OTHER_NAME = "OTHER_NAME"
    MANUAL = "MANUAL"

    @property
    def label_de(self) -> str:
        """Human readable German name (used in UI and reports)."""
        return CATEGORY_LABELS_DE[self]

    @property
    def placeholder_de(self) -> str:
        """Word used inside placeholders, e.g. ``[Adresse 1]``."""
        return CATEGORY_PLACEHOLDERS_DE[self]


CATEGORY_LABELS_DE: dict[Category, str] = {
    Category.PERSON: "Personennamen",
    Category.ORG: "Firmen / Organisationen",
    Category.LOCATION: "Orte",
    Category.ADDRESS: "Adressen",
    Category.PHONE: "Telefon / Fax",
    Category.EMAIL: "E-Mail-Adressen",
    Category.URL: "Webadressen",
    Category.IBAN: "IBAN / Kontonummern",
    Category.BIC: "BIC",
    Category.CREDIT_CARD: "Kreditkartennummern",
    Category.TAX_ID: "Steuer-ID / Steuernummer",
    Category.SOCIAL_SECURITY: "Sozialversicherungsnummer",
    Category.ID_DOCUMENT: "Ausweis- / Passnummer",
    Category.LICENSE_PLATE: "Kfz-Kennzeichen",
    Category.BIRTH_DATE: "Geburtsdaten",
    Category.CASE_NUMBER: "Aktenzeichen (eigene)",
    Category.REFERENCE_NUMBER: "Kunden-/Vertrags-/sonstige Nummern",
    Category.REGISTER_NUMBER: "Handelsregister / USt-IdNr.",
    Category.IP_ADDRESS: "IP-Adressen",
    Category.OTHER_NAME: "Sonstige Eigennamen",
    Category.MANUAL: "Manuell markiert",
}

CATEGORY_PLACEHOLDERS_DE: dict[Category, str] = {
    Category.PERSON: "Person",
    Category.ORG: "Firma",
    Category.LOCATION: "Ort",
    Category.ADDRESS: "Adresse",
    Category.PHONE: "Telefon",
    Category.EMAIL: "E-Mail",
    Category.URL: "Webadresse",
    Category.IBAN: "IBAN",
    Category.BIC: "BIC",
    Category.CREDIT_CARD: "Kartennummer",
    Category.TAX_ID: "Steuernummer",
    Category.SOCIAL_SECURITY: "SV-Nummer",
    Category.ID_DOCUMENT: "Ausweisnummer",
    Category.LICENSE_PLATE: "Kennzeichen",
    Category.BIRTH_DATE: "Geburtsdatum",
    Category.CASE_NUMBER: "Aktenzeichen",
    Category.REFERENCE_NUMBER: "Nummer",
    Category.REGISTER_NUMBER: "Registernummer",
    Category.IP_ADDRESS: "IP-Adresse",
    Category.OTHER_NAME: "Name",
    Category.MANUAL: "geschwärzt",
}


@dataclass(slots=True)
class Word:
    """A single word on a page together with its bounding box.

    ``source`` is ``"text"`` for words taken from the PDF's own text layer and
    ``"ocr"`` for words recognised by Tesseract on a rendered page image.
    """

    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    source: str = "text"
    line_id: int = 0  # words sharing a line_id are on the same visual line
    #: OCR confidence 0..100 (always 100 for the PDF's own text layer)
    conf: float = 100.0

    def to_list(self) -> list[Any]:
        """Compact JSON representation (keeps cache files small)."""
        return [round(self.x0, 2), round(self.y0, 2), round(self.x1, 2), round(self.y1, 2),
                self.text, self.source, self.line_id, round(self.conf)]

    @classmethod
    def from_list(cls, data: list[Any]) -> "Word":
        return cls(*data)


@dataclass
class PageInfo:
    """Extraction result for a single page."""

    index: int  # zero based page number
    width: float
    height: float
    words: list[Word] = field(default_factory=list)
    #: "digital" (text layer), "scan" (image only, OCR) or "mixed"
    kind: str = "digital"
    #: True if the page must be re-built as an image in the output, e.g.
    #: because it is a scan or because hidden/off-page text was found.
    force_raster: bool = False
    notes: list[str] = field(default_factory=list)
    #: Readability of OCR'd pages (mean word confidence 0..100, None = digital)
    ocr_quality: float | None = None
    #: Small images (logos, signatures, photos) to remove: [x0, y0, x1, y1]
    image_boxes: list[list[float]] = field(default_factory=list)
    #: Detected signatures/handwriting/stamps: [x0, y0, x1, y1, score, kind, features]
    signature_boxes: list[list] = field(default_factory=list)

    @property
    def poorly_readable(self) -> bool:
        """OCR quality too low to trust the automatic detection alone."""
        return self.ocr_quality is not None and self.ocr_quality < 80

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "width": self.width,
            "height": self.height,
            "kind": self.kind,
            "force_raster": self.force_raster,
            "notes": self.notes,
            "ocr_quality": self.ocr_quality,
            "image_boxes": self.image_boxes,
            "signature_boxes": self.signature_boxes,
            "words": [w.to_list() for w in self.words],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PageInfo":
        return cls(
            index=data["index"],
            width=data["width"],
            height=data["height"],
            kind=data["kind"],
            force_raster=data["force_raster"],
            notes=list(data.get("notes", [])),
            ocr_quality=data.get("ocr_quality"),
            image_boxes=[list(b) for b in data.get("image_boxes", [])],
            signature_boxes=[list(b) for b in data.get("signature_boxes", [])],
            words=[Word.from_list(w) for w in data["words"]],
        )


@dataclass
class Detection:
    """A raw finding produced by a detector, expressed in page-text offsets."""

    page: int
    start: int
    end: int
    text: str
    category: Category
    source: str  # e.g. "regex:iban", "spacy", "gliner", "rule:salutation"
    score: float = 1.0
    #: Optional structured hints for person names (given / surname split).
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Mention:
    """One occurrence of an entity, resolved to word indices on a page."""

    doc: int  # index of the document inside the case file ("Akte")
    page: int
    word_indices: list[int]
    entity_id: str
    #: How the mention is rendered (e.g. "Herr M." uses the surname code only)
    form: str = "full"  # "full" | "surname" | "given"
    #: Text that is kept before/after the replacement (punctuation such as
    #: commas or brackets that belonged to the redacted word but not to the PII).
    prefix: str = ""
    suffix: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Entity:
    """A distinct piece of personal data, e.g. one person or one IBAN."""

    id: str
    category: Category
    original: str  # canonical original text (CONFIDENTIAL)
    replacement: str  # what is printed instead
    enabled: bool = True
    sources: list[str] = field(default_factory=list)
    #: For persons: given names and surname, used for initials and matching.
    given: list[str] = field(default_factory=list)
    surname: str = ""
    #: All textual variants seen in the documents (CONFIDENTIAL).
    variants: list[str] = field(default_factory=list)
    #: Alternative renderings for partial mentions (e.g. surname only).
    forms: dict[str, str] = field(default_factory=dict)
    count: int = 0
    #: Highest detector confidence (0..1)
    score: float = 1.0
    #: Why the detection is uncertain ("" = certain). Uncertain entries are
    #: presented to the user for a decision.
    uncertain: str = ""
    #: True once the user confirmed or rejected the entry.
    decided: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Entity":
        data = dict(data)
        data["category"] = Category(data["category"])
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class ManualBox:
    """A rectangle drawn by the user in the review screen."""

    doc: int
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    label: str = "[geschwärzt]"
    #: "manuell" (drawn by the user), "bild" (image removed automatically),
    #: "unterschrift" / "handschrift" / "stempel" (detected automatically)
    kind: str = "manuell"
    #: Detection features (for learning from the user's keep/remove clicks)
    features: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
