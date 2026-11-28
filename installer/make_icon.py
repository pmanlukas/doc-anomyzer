"""Draw the program icon (document with blacked-out lines) as .ico.

    python installer/make_icon.py build/app/app.ico
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw


def draw(size: int = 256) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 256
    # Page with folded corner.
    page = [(44 * s, 16 * s), (172 * s, 16 * s), (212 * s, 56 * s), (212 * s, 240 * s),
            (44 * s, 240 * s)]
    d.polygon(page, fill=(255, 255, 255, 255), outline=(16, 42, 84, 255))
    d.line(page + [page[0]], fill=(16, 42, 84, 255), width=max(1, int(10 * s)))
    d.polygon([(172 * s, 16 * s), (172 * s, 56 * s), (212 * s, 56 * s)], fill=(200, 210, 228, 255))
    # Text lines, two of them blacked out (the redaction).
    for i, (x1, black) in enumerate([(180, False), (190, True), (150, False), (186, True),
                                     (170, False)]):
        y = (80 + i * 30) * s
        colour = (20, 20, 20, 255) if black else (140, 160, 190, 255)
        height = 18 * s if black else 8 * s
        d.rounded_rectangle([70 * s, y, x1 * s, y + height], radius=max(1, int(3 * s)),
                            fill=colour)
    return img


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "app.ico")
    target.parent.mkdir(parents=True, exist_ok=True)
    draw().save(target, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128),
                               (256, 256)])
    print(target)
