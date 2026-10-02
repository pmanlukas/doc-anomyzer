"""Detection of handwritten signatures and handwriting.

Scanned pages
-------------
1. The page is rendered in colour and split into ink and paper.
2. Ink that belongs to well-recognised printed text (OCR words with good
   confidence) and long straight lines (tables, underlines) is removed.
3. The remaining strokes are merged into "ink islands".
4. Every island is scored with simple, explainable features:

   * coloured ink (blue/violet ballpoint pens are typical for signatures),
   * no readable text inside (handwriting produces OCR garbage or nothing),
   * a closing formula, "gez.", "Unterschrift" or a role ("Rechtsanwalt")
     directly above/next to it,
   * position in the lower part of the page, typical size and stroke
     density.

Islands above the threshold become redaction boxes ("[Unterschrift]" or
"[Handschrift]"). The user sees them in the review and can keep any of
them with one click; these decisions are stored and – once there are enough
– used to fit a small logistic regression model that replaces the fixed
weights (self-learning, see :class:`SignatureModel`).

Digital pages
-------------
Signatures drawn as vector graphics (signature pads, "draw signature"
features of PDF tools) are found as compact clusters of curved path
segments without text.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from PIL import Image

from .models import Word

log = logging.getLogger(__name__)

#: Words after which a signature typically follows.
SIGNATURE_CUES = {
    "grüßen", "grüssen", "gruß", "grüße", "hochachtungsvoll", "gez", "gez.", "gezeichnet",
    "unterschrift", "unterschriften", "unterzeichnet", "i.a.", "i.v.", "ppa.", "ppa", "pp.",
    "rechtsanwalt", "rechtsanwältin", "notar", "notarin", "geschäftsführer",
    "geschäftsführerin", "vorsitzender", "vorsitzende", "richter", "richterin",
    "sachbearbeiter", "sachbearbeiterin", "steuerberater", "arzt", "ärztin", "facharzt",
    "fachärztin", "vermieter", "mieter", "arbeitgeber", "arbeitnehmer", "auftraggeber",
    "kunde", "unterschrieben", "datum,", "(unterschrift)",
}
FEATURES = ["color", "unreadable", "cue", "lower", "size", "density", "complexity"]
#: Hand-tuned weights (used until enough user decisions exist).
DEFAULT_WEIGHTS = {"color": 0.30, "unreadable": 0.15, "cue": 0.30, "lower": 0.05,
                   "size": 0.10, "density": 0.05, "complexity": 0.15}
THRESHOLD = 0.55
#: Without a closing formula/role nearby, more evidence is needed (logos,
#: coloured page decorations). Only used with the hand-tuned weights.
THRESHOLD_NO_CUE = 0.65
WORK_DPI = 100  # analysis resolution (fast, sufficient for strokes)


@dataclass
class Candidate:
    """A detected signature/handwriting area in PDF points (unrotated)."""

    x0: float
    y0: float
    x1: float
    y1: float
    score: float
    kind: str  # "unterschrift" | "handschrift" | "stempel"
    features: list[float] = field(default_factory=list)

    def to_list(self) -> list:
        return [round(self.x0, 2), round(self.y0, 2), round(self.x1, 2), round(self.y1, 2),
                round(self.score, 3), self.kind, [round(f, 3) for f in self.features]]


# --------------------------------------------------------------- learning

class SignatureModel:
    """Logistic regression over :data:`FEATURES`, fitted on user decisions."""

    def __init__(self, weights: dict[str, float] | None = None, bias: float = 0.0,
                 learned: bool = False) -> None:
        self.weights = weights or dict(DEFAULT_WEIGHTS)
        self.bias = bias
        self.learned = learned

    def score(self, features: list[float]) -> float:
        z = sum(self.weights[name] * value for name, value in zip(FEATURES, features))
        if not self.learned:
            return z  # weighted sum in 0..1
        return 1 / (1 + math.exp(-(z + self.bias)))

    @classmethod
    def fit(cls, samples: list[tuple[list[float], int]], epochs: int = 600,
            rate: float = 0.5) -> "SignatureModel":
        x = np.array([s for s, _ in samples], dtype=float)
        y = np.array([label for _, label in samples], dtype=float)
        w = np.array([DEFAULT_WEIGHTS[n] for n in FEATURES]) * 4
        b = -2.0
        for _ in range(epochs):
            p = 1 / (1 + np.exp(-(x @ w + b)))
            grad = p - y
            w -= rate * (x.T @ grad / len(y) + 0.01 * w)
            b -= rate * grad.mean()
        return cls(dict(zip(FEATURES, map(float, w))), float(b), learned=True)


def _model_path():
    from .config import home_dir

    return home_dir() / "unterschriften_modell.json"


@lru_cache(maxsize=1)
def load_model() -> SignatureModel:
    """The learned model if one exists, otherwise the default weights."""
    try:
        data = json.loads(_model_path().read_text(encoding="utf-8"))
        return SignatureModel(data["weights"], data["bias"], learned=True)
    except (OSError, ValueError, KeyError):
        return SignatureModel()


def learn_from_decisions(samples: list[tuple[list[float], int]], min_samples: int = 20) -> bool:
    """Fit and store a new model when enough (and both kinds of) samples exist."""
    if len(samples) < min_samples or len({label for _, label in samples}) < 2:
        return False
    model = SignatureModel.fit(samples)
    _model_path().write_text(json.dumps({"weights": model.weights, "bias": model.bias,
                                         "samples": len(samples)}), encoding="utf-8")
    load_model.cache_clear()
    return True


# --------------------------------------------------------------- scanned pages

def _cue_rects(words: list[Word], scale: float) -> list[tuple[float, float, float, float]]:
    """Pixel boxes of words that typically precede a signature."""
    rects = []
    for w in words:
        # lower(), not casefold(): casefold turns "Grüße" into "grüsse".
        token = w.text.lower().strip("„“\"()")
        if token in SIGNATURE_CUES or token.rstrip(",.:") in SIGNATURE_CUES \
                or set(token) <= {"_", "."} and len(token) >= 8:
            rects.append((w.x0 / scale, w.y0 / scale, w.x1 / scale, w.y1 / scale))
    return rects


def detect_in_image(image: Image.Image, page_width: float, page_height: float,
                    words: list[Word], model: SignatureModel | None = None) -> list[Candidate]:
    """Find signature/handwriting islands in a rendered page image.

    ``words`` are the OCR words of the page in PDF points (unrotated view is
    assumed to match the image).
    """
    import cv2

    model = model or load_model()
    factor = WORK_DPI * page_width / 72 / image.width  # resize to WORK_DPI
    small = image.convert("RGB").resize((max(1, int(image.width * factor)),
                                         max(1, int(image.height * factor))), Image.BILINEAR)
    rgb = np.asarray(small)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    otsu, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # A little more tolerant than Otsu: thin ballpoint strokes become light
    # grey when the page is reduced to WORK_DPI. Speckles are removed below.
    _, ink = cv2.threshold(gray, min(225.0, otsu + 25), 255, cv2.THRESH_BINARY_INV)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    colored = ((hsv[..., 1] > 70) & (hsv[..., 2] < 235)).astype(np.uint8) * 255
    mm = WORK_DPI / 25.4  # pixels per millimetre
    scale = 72 / WORK_DPI  # pixel -> point

    # 1) Remove confidently recognised printed text.
    text_mask = np.zeros_like(ink)
    unreadable_mask = np.zeros_like(ink)
    for w in words:
        x0, y0, x1, y1 = (int(w.x0 / scale), int(w.y0 / scale), int(math.ceil(w.x1 / scale)),
                          int(math.ceil(w.y1 / scale)))
        alnum = sum(ch.isalnum() for ch in w.text)
        readable = w.conf >= 60 and alnum >= 2 and alnum >= 0.6 * len(w.text)
        target = text_mask if readable else unreadable_mask
        cv2.rectangle(target, (x0 - 1, y0 - 1), (x1 + 1, y1 + 1), 255, -1)
    # Coloured strokes over printed text (signature across the typed name)
    # are kept: only black text is removed.
    strokes = cv2.bitwise_and(ink, cv2.bitwise_not(cv2.bitwise_and(
        text_mask, cv2.bitwise_not(colored))))

    # 2) Remove long straight lines (tables, underlines, frames).
    # 25 mm: long enough that steep strokes of a signature survive.
    with_lines = strokes.copy()  # for the flourish step at the end
    for size in ((int(25 * mm), 1), (1, int(25 * mm))):
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, size)
        lines = cv2.morphologyEx(strokes, cv2.MORPH_OPEN, kernel)
        strokes = cv2.subtract(strokes, cv2.dilate(lines, np.ones((3, 3), np.uint8)))
    # Speckles (scan noise) away. No morphological opening: a thin ballpoint
    # stroke is only ~1 px wide at WORK_DPI and would vanish. Tiny isolated
    # components are dropped instead; strokes are long and connected.
    n, specks, sstats, _ = cv2.connectedComponentsWithStats(strokes, connectivity=8)
    small_ids = np.nonzero(sstats[:, cv2.CC_STAT_AREA] < 5)[0]
    small_ids = small_ids[small_ids > 0]
    if len(small_ids):
        strokes[np.isin(specks, small_ids)] = 0

    # 3) Merge strokes into islands.
    merged = cv2.dilate(strokes, cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (max(3, int(4 * mm)), max(3, int(2.5 * mm)))))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(merged, connectivity=8)
    cues = _cue_rects(words, scale)
    height_px = ink.shape[0]

    candidates: list[Candidate] = []
    for label in range(1, count):
        x, y, w, h, _area = stats[label]
        if not (4 * mm <= w <= 160 * mm and 3 * mm <= h <= 70 * mm):
            continue
        region = strokes[y:y + h, x:x + w] > 0
        island = region & (labels[y:y + h, x:x + w] == label)
        ink_px = int(island.sum())
        if ink_px < 0.8 * mm * mm * 6:  # too little ink
            continue
        ys, xs = np.nonzero(island)
        ink_h = (ys.max() - ys.min() + 1) / mm
        ink_w = (xs.max() - xs.min() + 1) / mm
        color_share = float((colored[y:y + h, x:x + w][island] > 0).mean())
        # A printed line is about 3-4 mm high; handwriting/signatures are
        # taller (unless written in coloured ink, which print rarely is).
        if ink_h < 5.5 and color_share < 0.3:
            continue
        # Tallest single stroke: printed letters are at most ~3.5 mm high
        # (even blurred), a pen stroke of a signature usually spans more.
        _n, _l, part_stats, _ = cv2.connectedComponentsWithStats(island.astype(np.uint8),
                                                                connectivity=8)
        tallest = float(part_stats[1:, cv2.CC_STAT_HEIGHT].max()) / mm if _n > 1 else 0.0
        if tallest < 5.5 and color_share < 0.3:
            continue  # blurred printed text lines (measured: <= 4.6 mm vs. >= 5.6 mm)
        density = ink_px / float(w * h)
        if density > 0.45:  # solid blocks: photos, logos, black bars
            continue
        readable_cover = float((text_mask[y:y + h, x:x + w][island] > 0).mean())
        unreadable = 1.0 - readable_cover
        if unreadable < 0.25:  # mostly well-read print (e.g. a coloured letterhead)
            continue
        # Stroke complexity: contour length relative to ink amount.
        contours, _ = cv2.findContours(island.astype(np.uint8), cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_NONE)
        perimeter = sum(len(c) for c in contours)
        complexity = min(1.0, perimeter / (ink_px + 1) / 0.9)
        cue = 0.0
        for cx0, cy0, cx1, cy1 in cues:
            above = cy1 <= y + h * 0.6 and y - cy1 <= 45 * mm and cx0 <= x + w + 30 * mm \
                and cx1 >= x - 60 * mm
            beside = abs((cy0 + cy1) / 2 - (y + h / 2)) <= h and abs(cx0 - (x + w)) <= 40 * mm
            below = cy0 >= y + h * 0.4 and cy0 - (y + h) <= 15 * mm and \
                cx0 <= x + w and cx1 >= x
            if above or beside or below:
                cue = 1.0
                break
        size_ok = 1.0 if 25 * mm <= w <= 110 * mm and 6 * mm <= h <= 40 * mm else 0.4
        dens_ok = 1.0 if 0.02 <= density <= 0.25 else 0.3
        lower = 1.0 if y > height_px * 0.45 else 0.0
        features = [min(1.0, color_share * 1.5), unreadable, cue, lower, size_ok, dens_ok,
                    complexity]
        score = model.score(features)
        if score < ABSORB_THRESHOLD:
            continue
        roundish = 0.7 <= ink_w / max(ink_h, 0.1) <= 1.4 and ink_h >= 15
        kind = "stempel" if roundish else \
            "unterschrift" if cue or color_share > 0.3 else "handschrift"
        pad = 2.5 * mm
        candidates.append(Candidate(
            max(0.0, (x - pad) * scale), max(0.0, (y - pad) * scale),
            min(page_width, (x + w + pad) * scale), min(page_height, (y + h + pad) * scale),
            score, kind, features))
    if not model.learned:
        for c in candidates:
            if not c.features[2] and THRESHOLD <= c.score < THRESHOLD_NO_CUE:
                c.score = min(c.score, ABSORB_THRESHOLD)  # only as a piece of a signature
    result = _grow(candidates, gap=8 * mm * scale)
    _absorb_flourishes(result, with_lines, mm, scale, page_width, page_height)
    return result


def _absorb_flourishes(result: list[Candidate], ink: np.ndarray, mm: float, scale: float,
                       page_width: float, page_height: float) -> None:
    """Extend signatures by connected strokes the line filter removed.

    The line filter also removes the long, almost straight underline many
    people add to their signature. Every ink component (printed text already
    removed, lines kept) that touches a detected signature and is no bigger
    than a signature with its underline is added to the box. A blank
    signature line under it may be covered too – that does no harm. Whole
    table frames are too big and stay untouched.
    """
    import cv2

    if not result or not ink.any():
        return
    count, _labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    pieces = []
    for label in range(1, count):
        x, y, w, h, _area = stats[label]
        if w <= 120 * mm and h <= 25 * mm and max(w, h) >= 3 * mm:
            pieces.append(((x - 1) * scale, (y - 1) * scale, (x + w + 1) * scale,
                           (y + h + 1) * scale))
    pad = 2.5 * mm * scale
    for c in result:
        for x0, y0, x1, y1 in pieces:
            if x0 < c.x1 and c.x0 < x1 and y0 < c.y1 and c.y0 < y1:
                c.x0, c.y0 = max(0.0, min(c.x0, x0 - pad)), max(0.0, min(c.y0, y0 - pad))
                c.x1 = min(page_width, max(c.x1, x1 + pad))
                c.y1 = min(page_height, max(c.y1, y1 + pad))


#: Islands this strong are kept when they touch a confident detection: a
#: signature crossing printed text falls apart into several pieces.
ABSORB_THRESHOLD = 0.3


def _grow(cands: list[Candidate], gap: float) -> list[Candidate]:
    """Start from confident detections and absorb nearby weaker pieces."""
    accepted = [c for c in sorted(cands, key=lambda c: -c.score) if c.score >= THRESHOLD]
    pieces = [c for c in cands if c.score < THRESHOLD]
    result: list[Candidate] = []
    for c in accepted:
        for m in result:  # overlapping confident detections
            if c.x0 < m.x1 + gap and m.x0 < c.x1 + gap and c.y0 < m.y1 + gap / 2 \
                    and m.y0 < c.y1 + gap / 2:
                m.x0, m.y0, m.x1, m.y1 = (min(m.x0, c.x0), min(m.y0, c.y0),
                                          max(m.x1, c.x1), max(m.y1, c.y1))
                break
        else:
            result.append(c)
    changed = True
    while changed:
        changed = False
        for m in result:
            for piece in list(pieces):
                if piece.x0 < m.x1 + gap and m.x0 < piece.x1 + gap and \
                        piece.y0 < m.y1 + gap / 2 and m.y0 < piece.y1 + gap / 2:
                    m.x0, m.y0 = min(m.x0, piece.x0), min(m.y0, piece.y0)
                    m.x1, m.y1 = max(m.x1, piece.x1), max(m.y1, piece.y1)
                    pieces.remove(piece)
                    changed = True
    return result


# --------------------------------------------------------------- digital pages

def detect_vector_signatures(page, words: list[Word]) -> list[Candidate]:
    """Signatures drawn as vector paths (many curve segments, no text)."""
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001 - exotic content
        return []
    curvy = []
    for d in drawings:
        curves = sum(1 for item in d.get("items", ()) if item[0] == "c")
        rect = d.get("rect")
        if rect is not None and curves >= 3 and rect.width < 300 and rect.height < 150:
            curvy.append((rect, curves))
    if not curvy:
        return []
    # Cluster nearby curvy paths.
    clusters: list[list] = []
    for rect, curves in curvy:
        for cluster in clusters:
            box = cluster[0]
            if rect.x0 < box.x1 + 15 and box.x0 < rect.x1 + 15 and \
                    rect.y0 < box.y1 + 15 and box.y0 < rect.y1 + 15:
                cluster[0] = box | rect
                cluster[1] += curves
                break
        else:
            clusters.append([rect, curves])
    result = []
    for box, curves in clusters:
        if curves < 12 or not (28 <= box.width <= 450 and 10 <= box.height <= 200):
            continue
        text_inside = sum(1 for w in words if box.x0 <= (w.x0 + w.x1) / 2 <= box.x1
                          and box.y0 <= (w.y0 + w.y1) / 2 <= box.y1)
        if text_inside > 2:
            continue  # vector text (fonts as outlines) or a chart with labels
        result.append(Candidate(box.x0 - 3, box.y0 - 3, box.x1 + 3, box.y1 + 3, 0.8,
                                "unterschrift", []))
    return result
