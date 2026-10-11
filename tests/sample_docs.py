"""Generate realistic (fictitious) German legal PDFs for the tests.

All persons, addresses and numbers are invented. The IBAN, tax id and
credit card numbers are well-known *test* numbers with valid check digits.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

LETTER_PAGES = [
    """Kanzlei Schmidt & Partner mbB · Lindenstraße 12 · 80331 München
Tel.: 089 / 123 456-78 · E-Mail: m.schmidt@kanzlei-schmidt.de

Landgericht München I
Prielmayerstraße 7
80335 München

Az.: 12 O 345/21

Klage

des Herrn Maximilian Schröder, geb. am 03.04.1978,
wohnhaft Berliner Straße 5a, 10115 Berlin,
- Kläger -
Prozessbevollmächtigte: Rechtsanwältin Petra Kowalski,

gegen

die Müller Bau GmbH, vertreten durch den Geschäftsführer Klaus Jansen,
Industrieweg 3, 50667 Köln,
- Beklagte -

wegen Schadensersatz.""",
    """Sachverhalt

Der Kläger beauftragte die Beklagte am 12.03.2020 mit Arbeiten an seinem Haus.
Herr Schröder überwies den Betrag von seinem Konto IBAN DE89 3704 0044 0532 0130 00.
Die Ehefrau des Klägers, Frau Erika Schröder, geb. Hoffmann, war bei der Abnahme anwesend.
Zeuge: Ahmet Yilmaz, zu laden über die Klägerseite.
Mustermann, Erika, Mitarbeiterin der Beklagten, bestätigte den Mangel per E-Mail.
Die Steuer-ID des Klägers lautet 86 095 742 719, Kundennummer: 4711-0815.

Rechtlich ergibt sich der Anspruch aus § 280 Abs. 1 BGB (vgl. BGH, Urteil vom
12.03.2020 - VI ZR 123/19). Schröders Schaden beträgt 12.345 Euro.

Mit freundlichen Grüßen

gez. Petra Kowalski
Rechtsanwältin""",
]


def make_digital_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    for text in LETTER_PAGES:
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(60, 60, 540, 800), text, fontsize=10.5, fontname="helv")
    doc.set_metadata({"author": "Petra Kowalski", "title": "Klage Schröder ./. Müller Bau"})
    doc.set_toc([[1, "Klage Schröder", 1]])
    # A comment with personal data (must disappear).
    doc[0].add_text_annot((500, 40), "Rückruf Herr Schröder 0171 2345678")
    doc.save(path)
    return path


def make_scanned_pdf(path: Path, dpi: int = 200) -> Path:
    """A 'scan': the letter rendered to an image, no text layer."""
    digital = pymupdf.open(make_digital_pdf(path.with_suffix(".tmp.pdf")))
    out = pymupdf.open()
    for page in digital:
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, pixmap=pix)
    out.save(path)
    path.with_suffix(".tmp.pdf").unlink()
    return path


#: Letter from the Windows field test (fictitious data): a scanned IBAN that
#: was only half redacted, an e-mail at the end of a sentence before the
#: closing formula and a "Personal-Nr." field.
BANK_LETTER = ["Bäckerei Lindner GmbH", "Personalabteilung", "",
               "Sehr geehrte Frau Kretschmer,",
               "bitte überweisen Sie den Betrag auf folgendes Konto:",
               "IBAN DE02 1203 0000 0000 2020 51.", "Personal-Nr.: 4471", "",
               "Bei Fragen erreichen Sie uns unter s.kretschmer@baeckerei-lindner-test.de.",
               "Mit freundlichen Grüßen", "Sabine Kretschmer"]


def _arial_like(size: int):
    """Arial or a metrically identical font (Liberation Sans), else a fallback."""
    from PIL import ImageFont

    for name in ("arial.ttf", "LiberationSans-Regular.ttf",
                 "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                 "DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def make_bank_letter_scan(path: Path, dpi: int = 200, px: int = 36, angle: float = 0.6,
                          blur: float = 0.8) -> Path:
    """Image-only scan like in the field test: 200 dpi, Arial ~36 px, 0.6° skew, blurred."""
    import io

    from PIL import Image, ImageDraw, ImageFilter

    width, height = int(8.27 * dpi), int(11.69 * dpi)
    img = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(img)
    font = _arial_like(px)
    y = int(1.3 * dpi)
    for line in BANK_LETTER:
        draw.text((int(0.9 * dpi), y), line, font=font, fill=20)
        y += int(px * 1.5)
    img = img.rotate(angle, resample=Image.BICUBIC, fillcolor=255)
    img = img.filter(ImageFilter.GaussianBlur(blur))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(path)
    return path
