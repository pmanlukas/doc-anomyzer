"""Synthetic test corpus: realistic, entirely fictitious German legal documents.

Every generated document comes with its *ground truth*: the list of personal
data that was inserted (and must disappear) and a list of legal content that
must stay readable. This allows measuring the anonymiser objectively:

* **leaks**   – inserted personal data still present in the output (must be 0)
* **kept**    – legal terms/citations that survived (should be all)

Usage (stand-alone, for larger runs)::

    python -m tests.corpus --out /tmp/korpus --anzahl 20 --scan
"""

from __future__ import annotations

import argparse
import io
import random
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from faker import Faker
from PIL import Image, ImageFilter

# Mixed origins: German names dominate, but foreign names are common in practice.
LOCALES = ["de_DE"] * 6 + ["de_AT", "tr_TR", "pl_PL", "it_IT", "hr_HR"]
FONTS = ["helv", "tiro", "cour"]


@dataclass
class Person:
    first: str
    last: str
    title: str = ""
    salutation: str = "Herr"

    @property
    def full(self) -> str:
        return f"{self.first} {self.last}"

    @property
    def with_title(self) -> str:
        return f"{self.title} {self.full}".strip()


@dataclass
class Sample:
    """One generated document plus its ground truth."""

    name: str
    pages: list[str]
    secrets: list[tuple[str, str]] = field(default_factory=list)  # (category, text)
    keep: list[str] = field(default_factory=list)

    def secret(self, category: str, text: str) -> str:
        self.secrets.append((category, text))
        return text


class Cast:
    """Random but consistent set of persons, companies and numbers."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        Faker.seed(seed)
        self.fakers = {loc: Faker(loc) for loc in set(LOCALES)}
        self.de = self.fakers["de_DE"]

    def person(self, title_chance: float = 0.2) -> Person:
        fk = self.fakers[self.rng.choice(LOCALES)]
        female = self.rng.random() < 0.5
        first = fk.first_name_female() if female else fk.first_name_male()
        last = fk.last_name()
        # The PDF base fonts only cover Latin-1: characters such as "Ż" would
        # be printed as "?" – use a German name instead in that case.
        if not all(ord(c) < 256 for c in first + last):
            first = self.de.first_name_female() if female else self.de.first_name_male()
            last = self.de.last_name()
        title = self.rng.choice(["Dr.", "Prof. Dr.", "Dipl.-Ing."]) \
            if self.rng.random() < title_chance else ""
        return Person(first, last, title, "Frau" if female else "Herr")

    def street(self) -> str:
        return self.de.street_address()

    def city_line(self) -> str:
        return f"{self.de.postcode()} {self.de.city()}"

    def phone(self) -> str:
        area = self.rng.choice(["030", "089", "040", "0221", "069", "0711", "07131", "0351"])
        number = "".join(self.rng.choice("0123456789") for _ in range(self.rng.randint(5, 8)))
        style = self.rng.choice(["{a} {n}", "{a}/{n}", "{a} / {n}", "+49 {b} {n}", "({a}) {n}",
                                 "{a}-{n}", "{a}. {n}"])
        return style.format(a=area, b=area[1:], n=number)

    def mobile(self) -> str:
        return f"01{self.rng.choice('567')}{self.rng.randint(0, 9)} " + \
            "".join(self.rng.choice("0123456789") for _ in range(7))

    def iban(self) -> str:
        raw = self.de.iban()
        return " ".join(raw[i:i + 4] for i in range(0, len(raw), 4)) \
            if self.rng.random() < 0.7 else raw

    def email(self, p: Person) -> str:
        import unicodedata

        def ascii_(s: str) -> str:
            plain = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
            return plain.replace(" ", "")
        return f"{ascii_(p.first)}.{ascii_(p.last)}@{self.de.free_email_domain()}"

    def company(self) -> str:
        return f"{self.de.last_name()} {self.rng.choice(['Bau', 'Immobilien', 'Logistik', 'Handel', 'Service'])} " \
               f"{self.rng.choice(['GmbH', 'GmbH & Co. KG', 'AG', 'UG (haftungsbeschränkt)', 'e.K.'])}"

    def date(self) -> str:
        return self.de.date_of_birth(minimum_age=18, maximum_age=90).strftime("%d.%m.%Y")

    def tax_id(self) -> str:
        from doc_anonymizer.detect.patterns import tax_id_valid
        while True:
            digits = str(self.rng.randint(10**10, 10**11 - 1))
            if tax_id_valid(digits):
                return f"{digits[:2]} {digits[2:5]} {digits[5:8]} {digits[8:]}"

    def plate(self) -> str:
        return f"{self.rng.choice(['M', 'B', 'HH', 'K', 'S', 'FS', 'LA'])}-" \
               f"{self.rng.choice('ABCDEFGHKLMNPRSTVXZ')}{self.rng.choice('ABCDEFGHKLMNPRSTVXZ')} " \
               f"{self.rng.randint(1, 9999)}"

    def az(self) -> str:
        return f"{self.rng.randint(1, 45)} {self.rng.choice(['O', 'C', 'S', 'Ca', 'T'])} " \
               f"{self.rng.randint(10, 9999)}/{self.rng.randint(20, 26)}"


# ---------------------------------------------------------------------------
# Document templates
# ---------------------------------------------------------------------------

KEEP_LEGAL = ["§ 823 Abs. 1 BGB", "§ 280 Abs. 1 BGB", "Landgericht", "Amtsgericht",
              "Kläger", "Beklagte", "Rechtsstreit", "VI ZR 123/19"]


def klageschrift(c: Cast) -> Sample:
    s = Sample("klageschrift", [])
    kl, bk, ra, zeuge = c.person(), c.person(), c.person(0.4), c.person()
    firm = f"Kanzlei {ra.last} & Partner mbB"
    s.secret("ORG", firm)
    for p in (kl, bk, ra, zeuge):
        s.secret("PERSON", p.first)
        s.secret("PERSON", p.last)
    st1, ct1, st2, ct2, st3, ct3 = (c.street(), c.city_line(), c.street(), c.city_line(),
                                   c.street(), c.city_line())
    for x in (st1, ct1, st2, ct2, st3, ct3):
        s.secret("ADDRESS", x)
    phone, mail, iban, dob, az = c.phone(), c.email(ra), c.iban(), c.date(), c.az()
    for cat, x in (("PHONE", phone), ("EMAIL", mail), ("IBAN", iban), ("BIRTH_DATE", dob),
                   ("CASE_NUMBER", az)):
        s.secret(cat, x)
    s.keep += ["Landgericht", "Klage", "§ 823 Abs. 1 BGB", "Kläger", "Beklagte", "Schmerzensgeld"]
    s.pages.append(f"""{firm} · {st3} · {ct3}
Tel.: {phone} · E-Mail: {mail}

Landgericht Musterstadt
Gerichtsstraße 1
12345 Musterstadt

Az.: {az}

Klage

{'des Herrn' if kl.salutation == 'Herr' else 'der Frau'} {kl.with_title}, geb. am {dob},
wohnhaft {st1}, {ct1},
- Kläger -
Prozessbevollmächtigte: Rechtsanwältin {ra.with_title}, {firm}

gegen

{bk.salutation} {bk.full}, {st2}, {ct2},
- Beklagte(r) -

wegen Schadensersatz und Schmerzensgeld.

Namens und in Vollmacht des Klägers erheben wir Klage und werden beantragen,
die beklagte Partei zu verurteilen, an den Kläger 4.500,00 EUR nebst Zinsen
in Höhe von fünf Prozentpunkten über dem Basiszinssatz zu zahlen.""")
    s.pages.append(f"""Begründung

Der Kläger {kl.last} befuhr am 14.05.2025 gegen 17:30 Uhr die Hauptkreuzung. Die beklagte
Partei, {bk.salutation} {bk.last}, missachtete die Vorfahrt. Zeuge des Unfalls war
{zeuge.salutation} {zeuge.full}, der den Hergang bestätigen kann.
Beweis: Zeugnis {zeuge.salutation} {zeuge.last}, zu laden über die Klägerseite.

{kl.first} {kl.last.upper()} erlitt eine Fraktur des linken Handgelenks. Herrn bzw. Frau
{kl.last}s Ansprüche ergeben sich aus § 823 Abs. 1 BGB sowie § 280 Abs. 1 BGB
(vgl. BGH, Urteil vom 12.03.2020 - VI ZR 123/19).

Zahlungen werden erbeten auf das Konto IBAN {iban}.

Mit freundlichen Grüßen

{ra.with_title}
Rechtsanwältin""")
    return s


def arztbrief(c: Cast) -> Sample:
    s = Sample("arztbrief", [])
    pat, doc = c.person(0), c.person(1.0)
    for p in (pat, doc):
        s.secret("PERSON", p.first)
        s.secret("PERSON", p.last)
    dob, st, ct = c.date(), c.street(), c.city_line()
    kv = f"{c.rng.choice('ABCDEFGHKLMN')}{c.rng.randint(100000000, 999999999)}"
    phone = c.phone()
    for cat, x in (("BIRTH_DATE", dob), ("ADDRESS", st), ("ADDRESS", ct),
                   ("SOCIAL_SECURITY", kv), ("PHONE", phone)):
        s.secret(cat, x)
    s.keep += ["Diagnose", "Befund", "Therapieempfehlung"]
    s.pages.append(f"""Praxis {doc.with_title} · Facharzt für Orthopädie
Telefon {phone}

Arztbrief

Patient/in: {pat.last}, {pat.first}
Geburtsdatum: {dob}
Anschrift: {st}, {ct}
Versichertennummer: {kv}

Sehr geehrte Kollegin, sehr geehrter Kollege,

wir berichten über {pat.salutation} {pat.full}, geb. {dob}, die/der sich am 03.02.2026
in unserer Sprechstunde vorstellte.

Diagnose: Distorsion des rechten Sprunggelenks.
Befund: Schwellung lateral, Druckschmerz über dem Ligamentum fibulotalare anterius.
Therapieempfehlung: Orthese für sechs Wochen, Physiotherapie.

{pat.salutation} {pat.last} ist bis einschließlich 28.02.2026 arbeitsunfähig.

Mit kollegialen Grüßen
{doc.with_title}""")
    return s


def mietvertrag(c: Cast) -> Sample:
    s = Sample("mietvertrag", [])
    vm, mt1, mt2 = c.person(), c.person(), c.person()
    for p in (vm, mt1, mt2):
        s.secret("PERSON", p.first)
        s.secret("PERSON", p.last)
    obj, ct, vst, vct = c.street(), c.city_line(), c.street(), c.city_line()
    iban, mail, mob = c.iban(), c.email(mt1), c.mobile()
    for cat, x in (("ADDRESS", obj), ("ADDRESS", ct), ("ADDRESS", vst), ("ADDRESS", vct),
                   ("IBAN", iban), ("EMAIL", mail), ("PHONE", mob)):
        s.secret(cat, x)
    s.keep += ["Mietvertrag", "Kaution", "§ 551 BGB", "Schönheitsreparaturen"]
    s.pages.append(f"""Mietvertrag über Wohnraum

Zwischen
{vm.full}, {vst}, {vct}
- nachfolgend „Vermieter“ genannt -
und
{mt1.full} und {mt2.full}
- nachfolgend „Mieter“ genannt -

§ 1 Mietsache
Vermietet wird die Wohnung im 2. OG links im Hause {obj}, {ct}.

§ 2 Miete und Kaution
Die Miete beträgt monatlich 950,00 EUR. Die Kaution gemäß § 551 BGB beträgt drei
Monatsmieten und ist auf das Konto des Vermieters, IBAN {iban}, zu zahlen.

§ 3 Schönheitsreparaturen
Die Mieter {mt1.last} und {mt2.last} sind zur Durchführung von Schönheitsreparaturen
nicht verpflichtet.

Kontakt Mieter: {mail}, mobil {mob}

______________________                ______________________
{vm.full}                              {mt1.full}""")
    return s


def unfallbericht(c: Cast) -> Sample:
    s = Sample("unfallbericht", [])
    f1, f2, pol = c.person(0), c.person(0), c.person(0)
    for p in (f1, f2, pol):
        s.secret("PERSON", p.first)
        s.secret("PERSON", p.last)
    plate1, plate2, dob1 = c.plate(), c.plate(), c.date()
    fs = f"B{c.rng.randint(10, 99)}{c.rng.choice('ABCDEFGHKL')}{c.rng.randint(100000, 999999)}"
    city = c.de.city()
    for cat, x in (("LICENSE_PLATE", plate1), ("LICENSE_PLATE", plate2), ("BIRTH_DATE", dob1),
                   ("ID_DOCUMENT", fs), ("LOCATION", city)):
        s.secret(cat, x)
    s.keep += ["Verkehrsunfallanzeige", "Unfallhergang", "Sachschaden"]
    s.pages.append(f"""Polizeiinspektion Musterstadt
Verkehrsunfallanzeige

Beteiligter 1: {f1.last}, {f1.first}, geb. {dob1} in {city}
Kfz-Kennzeichen: {plate1}
Führerschein-Nr.: {fs}

Beteiligter 2: {f2.full}
amtl. Kennzeichen: {plate2}

Unfallhergang:
Beteiligter 1 ({f1.last}) befuhr die Bundesstraße in Richtung Norden und übersah beim
Abbiegen das entgegenkommende Fahrzeug von {f2.salutation} {f2.last}. Es entstand
Sachschaden in Höhe von ca. 8.000 EUR. Personen wurden nicht verletzt.

Aufgenommen durch: PHK {pol.full}""")
    return s


def email_verlauf(c: Cast) -> Sample:
    s = Sample("email", [])
    a, b = c.person(0), c.person(0)
    for p in (a, b):
        s.secret("PERSON", p.first)
        s.secret("PERSON", p.last)
    ma, mb, ph = c.email(a), c.email(b), c.phone()
    comp = c.company()
    url = f"www.{a.last.lower().replace(' ', '')}-beratung.de"
    for cat, x in (("EMAIL", ma), ("EMAIL", mb), ("PHONE", ph), ("ORG", comp)):
        s.secret(cat, x)
    s.keep += ["Kündigung", "Arbeitsvertrag", "Kündigungsschutzklage"]
    s.pages.append(f"""Von: {a.full} <{ma}>
An: {mb}
Gesendet: Montag, 2. März 2026 09:14
Betreff: AW: Kündigung Arbeitsvertrag

Hallo {b.first},

anbei wie besprochen die Kündigung der {comp}. Bitte prüfe, ob wir noch
fristgerecht Kündigungsschutzklage erheben können. Die Frist von drei Wochen
läuft am 20.03.2026 ab.

Viele Grüße
{a.first}

--
{a.full} | Personalabteilung | {comp}
Tel. {ph} | {url}""")
    s.secrets.append(("URL", url))
    return s


def kuendigung(c: Cast) -> Sample:
    s = Sample("kuendigung", [])
    an, gf = c.person(0), c.person(0)
    for p in (an, gf):
        s.secret("PERSON", p.first)
        s.secret("PERSON", p.last)
    comp, hrb = c.company(), f"HRB {c.rng.randint(1000, 99999)}"
    pnr, sv, tax = f"{c.rng.randint(10000, 99999)}", \
        f"{c.rng.randint(10, 99)} {c.rng.randint(100000, 311299):06d} " \
        f"{an.last[0].upper()} {c.rng.randint(100, 999)}", c.tax_id()
    st, ct = c.street(), c.city_line()
    for cat, x in (("ORG", comp), ("REGISTER_NUMBER", hrb), ("REFERENCE_NUMBER", pnr),
                   ("SOCIAL_SECURITY", sv), ("TAX_ID", tax), ("ADDRESS", st), ("ADDRESS", ct)):
        s.secret(cat, x)
    s.keep += ["ordentlich", "Kündigungsfrist", "§ 622 BGB", "Betriebsrat"]
    s.pages.append(f"""{comp} · Amtsgericht Musterstadt {hrb}

{an.salutation}
{an.full}
{st}
{ct}

Personalnummer: {pnr}
SV-Nummer: {sv}
Steuer-ID: {tax}

Kündigung Ihres Arbeitsverhältnisses

Sehr geehrte{'r' if an.salutation == 'Herr' else ''} {an.salutation} {an.last},

hiermit kündigen wir das mit Ihnen bestehende Arbeitsverhältnis ordentlich unter
Einhaltung der Kündigungsfrist gemäß § 622 BGB zum 30.06.2026. Der Betriebsrat
wurde ordnungsgemäß angehört.

Mit freundlichen Grüßen
i. V. {gf.full}
Geschäftsführer""")
    return s


TEMPLATES = [klageschrift, arztbrief, mietvertrag, unfallbericht, email_verlauf, kuendigung]


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------

#: Simulated scanner quality: dpi, max. skew (degrees), blur radius, noise
#: share, JPEG quality, contrast range (faded print / grey paper).
QUALITY = {
    "gut": dict(dpi=(250, 300), skew=0.5, blur=(0.2, 0.5), noise=0.03, jpeg=(80, 90),
                contrast=(0, 255)),
    "mittel": dict(dpi=(200, 220), skew=1.2, blur=(0.5, 0.8), noise=0.06, jpeg=(60, 75),
                   contrast=(25, 235)),
    "schlecht": dict(dpi=(150, 170), skew=2.0, blur=(0.8, 1.1), noise=0.10, jpeg=(35, 50),
                     contrast=(60, 215)),
    "sehr_schlecht": dict(dpi=(120, 130), skew=3.0, blur=(1.0, 1.3), noise=0.14,
                          jpeg=(25, 35), contrast=(80, 200)),
}


def _degrade(img: Image.Image, rng: random.Random, q: dict) -> Image.Image:
    """Make a clean page image look like a (bad) scan or fax."""
    low, high = q["contrast"]
    img = img.point(lambda v: low + v * (high - low) // 255)  # faded toner, grey paper
    img = img.rotate(rng.uniform(-q["skew"], q["skew"]), fillcolor=high,
                     resample=Image.BICUBIC, expand=False)
    img = img.filter(ImageFilter.GaussianBlur(rng.uniform(*q["blur"])))
    noise = Image.effect_noise(img.size, 40).convert("L")
    img = Image.blend(img, noise, q["noise"])
    # Dust and speckles.
    px = img.load()
    for _ in range(int(img.size[0] * img.size[1] * q["noise"] / 400)):
        x, y = rng.randrange(img.size[0]), rng.randrange(img.size[1])
        px[x, y] = rng.choice((0, 40, 90))
    return img


def render_pdf(sample: Sample, path: Path, rng: random.Random, scan: bool = False,
               quality: str = "gut") -> Path:
    """Write the sample as digital PDF (or as simulated scan of given quality)."""
    doc = pymupdf.open()
    font = rng.choice(FONTS)
    size = rng.choice([9.5, 10, 10.5, 11, 12])
    for text in sample.pages:
        page = doc.new_page()
        rect = pymupdf.Rect(60, 60, 540, 800)
        # Fall back to a smaller font if the text does not fit (insert_textbox
        # writes nothing and returns a negative value in that case).
        for fs in (size, 9, 8, 7):
            if page.insert_textbox(rect, text, fontsize=fs, fontname=font) >= 0:
                break
    doc.set_metadata({"author": "Test", "title": sample.name})
    if not scan:
        doc.save(path)
        return path

    q = QUALITY[quality]
    out = pymupdf.open()
    for page in doc:
        pix = page.get_pixmap(dpi=rng.randint(*q["dpi"]), colorspace=pymupdf.csGRAY)
        img = _degrade(Image.frombytes("L", (pix.width, pix.height), pix.samples), rng, q)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=rng.randint(*q["jpeg"]))
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=buf.getvalue())
    out.save(path)
    return path


def generate(out_dir: Path, count: int, seed: int = 1, scan_every: int = 3,
             quality: str = "gut") -> list[tuple[Sample, Path]]:
    """Generate ``count`` documents; every ``scan_every``-th one is a scan."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    result = []
    for n in range(count):
        cast = Cast(seed * 1000 + n)
        sample = TEMPLATES[n % len(TEMPLATES)](cast)
        scan = scan_every > 0 and n % scan_every == scan_every - 1
        suffix = f"_scan_{quality}" if scan else ""
        path = out_dir / f"{n:03d}_{sample.name}{suffix}.pdf"
        render_pdf(sample, path, rng, scan=scan, quality=quality)
        result.append((sample, path))
    return result


def generate_long(path: Path, pages: int, seed: int = 7, scan: bool = False,
                  quality: str = "gut") -> Sample:
    """One long case file (e.g. 300 pages) made of many different documents."""
    rng = random.Random(seed)
    combined = Sample("lange_akte", [])
    n = 0
    while len(combined.pages) < pages:
        sample = TEMPLATES[n % len(TEMPLATES)](Cast(seed * 7919 + n))
        combined.pages += sample.pages
        combined.secrets += sample.secrets
        combined.keep += sample.keep
        n += 1
    combined.pages = combined.pages[:pages]
    render_pdf(combined, path, rng, scan=scan, quality=quality)
    return combined


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _compact(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def evaluate(sample: Sample, output_text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Return (leaked secrets, missing keep-terms) for one anonymised text."""
    import regex as re

    norm, comp = _norm(output_text), _compact(output_text)
    leaks = []
    for category, secret in sample.secrets:
        if category in ("PERSON", "LOCATION", "ORG", "URL", "EMAIL"):
            pattern = (r"(?<![\p{L}\p{N}])(?<!\p{L}-)" + re.escape(_norm(secret))
                       + r"(?![\p{L}\p{N}])(?!-\p{L})")
            if len(secret) >= 3 and re.search(pattern, norm):
                leaks.append((category, secret))
        elif category == "ADDRESS":
            # The street name (without number) must be gone as well.
            for part in (secret, re.sub(r"\s*\d.*$", "", secret)):
                if len(part) >= 4 and _norm(part) in norm:
                    leaks.append((category, part))
                    break
        else:
            if len(_compact(secret)) >= 5 and _compact(secret) in comp:
                leaks.append((category, secret))
    missing = [k for k in sample.keep if _norm(k) not in norm]
    return leaks, missing


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--anzahl", type=int, default=12)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--scan", action="store_true", help="jedes dritte Dokument als Scan")
    args = parser.parse_args()
    for sample, path in generate(args.out, args.anzahl, args.seed, 3 if args.scan else 0):
        print(path, len(sample.secrets), "geheime Angaben")


if __name__ == "__main__":
    main()
