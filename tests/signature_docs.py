"""Scanned test letters with synthetic handwritten signatures.

A signature is simulated by smooth random pen strokes (loops, waves, an
optional underline) in blue or black ink. It is placed where signatures
belong: below closing formulas ("Mit freundlichen Grüßen", "gez.") and on
signature lines – partly overlapping the typed name, as in real letters.
The positions are returned as ground truth.
"""

from __future__ import annotations

import io
import math
import random
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFilter

from .corpus import QUALITY, TEMPLATES, Cast, _degrade

INKS = [(25, 45, 160), (20, 30, 110), (30, 30, 30), (60, 20, 120), (0, 0, 0)]
ANCHORS = ["Mit freundlichen Grüßen", "Mit kollegialen Grüßen", "Viele Grüße", "i. V.",
           "______________________"]


def handwriting(rng: random.Random, width: int, height: int, ink, scale: float) -> Image.Image:
    """RGBA image with one random 'signature'."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pen = max(2, int(rng.uniform(1.6, 3.2) * scale))
    x = rng.uniform(0.02, 0.1) * width
    mid = height * rng.uniform(0.4, 0.6)
    words = rng.randint(1, 3)
    for _ in range(words):
        length = rng.uniform(0.25, 0.5) * width
        points = []
        loops = rng.randint(3, 8)
        steps = 120
        amplitude = height * 0.3
        phase = rng.random()
        for i in range(steps):
            t = i / steps
            if i % 30 == 0:  # letter heights vary along the stroke
                amplitude = height * rng.uniform(0.15, 0.4)
            px = x + t * length + math.sin(t * loops * 2 * math.pi) * width * 0.02
            py = mid + math.sin(t * loops * 2 * math.pi + phase) * amplitude
            points.append((px, py))
        draw.line(points, fill=(*ink, 255), width=pen, joint="curve")
        x += length + rng.uniform(0.03, 0.08) * width
        if x > width * 0.9:
            break
    if rng.random() < 0.5:  # flourish / underline
        y = height * rng.uniform(0.75, 0.9)
        draw.line([(width * 0.05, y), (width * 0.5, y + height * 0.05),
                   (width * rng.uniform(0.7, 0.95), y - height * 0.1)],
                  fill=(*ink, 255), width=pen, joint="curve")
    return img.filter(ImageFilter.GaussianBlur(0.6))


def make_signed_scan(path: Path, seed: int, quality: str = "gut", color: bool = True,
                     ink: tuple[int, int, int] | None = None):
    """Render a template as scan with signatures; returns ground-truth rects (pt).

    ``ink`` fixes the pen colour (default: random blue/black pen).
    """
    rng = random.Random(seed)
    sample = TEMPLATES[seed % len(TEMPLATES)](Cast(seed))
    doc = pymupdf.open()
    for text in sample.pages:
        page = doc.new_page()
        for fs in (11, 10, 9, 8):
            if page.insert_textbox(pymupdf.Rect(60, 60, 540, 800), text, fontsize=fs,
                                   fontname="helv") >= 0:
                break
    q = QUALITY[quality]
    out = pymupdf.open()
    truth: list[list[pymupdf.Rect]] = []
    for page in doc:
        dpi = rng.randint(*q["dpi"])
        zoom = dpi / 72
        pix = page.get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        rects = []
        for anchor in ANCHORS:
            for hit in page.search_for(anchor):
                w_pt, h_pt = rng.uniform(90, 190), rng.uniform(28, 55)
                # Below the closing formula, often overlapping the typed name.
                x0 = hit.x0 + rng.uniform(-5, 40)
                y0 = hit.y1 + rng.uniform(-4, 12)
                sig = handwriting(rng, int(w_pt * zoom), int(h_pt * zoom),
                                  ink or (rng.choice(INKS) if color else (20, 20, 20)),
                                  zoom / 4)
                img.paste(sig, (int(x0 * zoom), int(y0 * zoom)), sig)
                # Ground truth = the area actually covered by ink.
                bx0, by0, bx1, by1 = sig.getbbox() or (0, 0, 1, 1)
                rects.append(pymupdf.Rect(x0 + bx0 / zoom, y0 + by0 / zoom,
                                          x0 + bx1 / zoom, y0 + by1 / zoom))
        truth.append(rects)
        gray_or_color = img if color else img.convert("L")
        if gray_or_color.mode == "L":
            degraded = _degrade(gray_or_color, rng, q)
        else:
            # Same random sequence for every channel: identical skew/blur.
            seed2 = rng.random()
            channels = [_degrade(ch, random.Random(seed2), q) for ch in gray_or_color.split()]
            degraded = Image.merge("RGB", channels)
        buf = io.BytesIO()
        degraded.save(buf, format="JPEG", quality=rng.randint(*q["jpeg"]))
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=buf.getvalue())
    out.save(path)
    return path, [r for page_rects in truth for r in page_rects], truth
