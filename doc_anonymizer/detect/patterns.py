"""Rule based detection of structured personal data (German conventions).

Each rule is a regular expression plus an optional validator (e.g. the IBAN
check digit). Rules are deliberately generous – it is better to redact a
number too many than to leak one. Legal citations (``§ 823 BGB``, case
numbers of *published* decisions) are not touched because they contain no
personal data and are important for the lawyer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterator

import regex as re

from ..lexicon import is_stopword
from ..models import Category, Detection

# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

#: Upper-case letter in any (Latin) alphabet – names like "Şahin", "Łukasz".
UP = r"\p{Lu}"
#: Letters (any alphabet), used inside names.
LET = r"\p{L}"
MONTHS = (r"(?:Januar|Jänner|Februar|März|April|Mai|Juni|Juli|August|September|"
          r"Oktober|November|Dezember|Jan\.|Feb\.|Mär\.|Apr\.|Jun\.|Jul\.|Aug\.|"
          r"Sep\.|Sept\.|Okt\.|Nov\.|Dez\.)")
DATE = (rf"(?:\d{{1,2}}\. ?\d{{1,2}}\. ?(?:\d{{4}}|\d{{2}})|\d{{1,2}}\. ?{MONTHS} ?\d{{4}}"
        r"|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})")

#: Dates as OCR sometimes reads them: "14.01 2002", "14,01.2002", "14. 01. 2002".
SLOPPY_DATE = r"\d{1,2}[.,] ?\d{1,2}[.,]? ?(?:19|20)\d\d"
STREET_SUFFIX = (r"(?:straße|strasse|str\.|weg|allee|platz|gasse|ring|damm|ufer|chaussee|"
                 r"steig|pfad|markt|graben|kamp|wall|stieg|twiete|zeile|anger|promenade|"
                 r"bogen|winkel|redder|kai|hof|berg|feld|garten|höhe|tal|grund|siedlung|park)")
STREET_PREPOSITIONS = (r"(?:Am|An der|An den|Im|In der|In den|Auf der|Auf dem|Auf den|Zum|Zur|"
                       r"Unter den|Unter der|Hinter der|Hinter dem|Vor dem|Vor der|Bei der|"
                       r"Beim|Alte|Alter|Altes|Neue|Neuer|Neues|Platz der|Straße der|Straße des)")
HOUSE_NUMBER = (r"\d{1,4}(?: ?[a-zA-Z](?!\p{L}))?(?: ?[-/] ?\d{1,3}(?: ?[a-zA-Z](?!\p{L}))?)?"
                r"(?![\d.,]\d|\p{L})")

# Public legal information sites are no personal data and stay readable.
PUBLIC_DOMAINS = (
    "gesetze-im-internet.de", "bundesgerichtshof.de", "bundesverfassungsgericht.de",
    "dejure.org", "juris.de", "beck-online.de", "openjur.de", "eur-lex.europa.eu",
    "curia.europa.eu", "bundesarbeitsgericht.de", "bundesfinanzhof.de",
    "bundessozialgericht.de", "bverwg.de", "rechtsprechung-im-internet.de",
    "justiz.de", "bmj.de", "bundesanzeiger.de", "handelsregister.de",
)


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def iban_valid(text: str) -> bool:
    """ISO 13616 check (mod 97 == 1)."""
    compact = re.sub(r"\s", "", text).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", compact):
        return False
    rearranged = compact[4:] + compact[:4]
    number = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(number) % 97 == 1


def luhn_valid(text: str) -> bool:
    digits = _digits(text)
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def tax_id_valid(text: str) -> bool:
    """German tax identification number (ISO 7064 MOD 11,10 check digit)."""
    digits = _digits(text)
    if len(digits) != 11 or digits[0] == "0":
        return False
    product = 10
    for ch in digits[:10]:
        total = (int(ch) + product) % 10 or 10
        product = (total * 2) % 11
    check = (11 - product) % 10
    return check == int(digits[10])


def phone_valid(text: str) -> bool:
    digits = _digits(text)
    return 6 <= len(digits) <= 15


# ---------------------------------------------------------------------------
# Rule table
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    name: str
    category: Category
    pattern: re.Pattern
    group: int = 0
    validator: Callable[[str], bool] | None = None
    priority: int = 90


def _r(pattern: str, flags: int = 0) -> re.Pattern:
    return re.compile(pattern, flags | re.VERSION1)


RULES: list[Rule] = [
    Rule("email", Category.EMAIL,
         _r(r"[\w.%+\-]+@[\w\-]+(?:\.[\w\-]+)*\.\p{L}{2,}")),
    # E-mail addresses torn apart by OCR: "bekir.weihmann@ hotmail.de",
    # "konstantinos. meister @ web.de".
    Rule("email_ocr", Category.EMAIL,
         _r(r"[\w%+\-]+(?:\.\s?[\w%+\-]+)*\s?@\s?[\w\-]+(?:\s?\.\s?[\w\-]+)*"
            r"(?:\s?\.\s?\p{L}{2,})?"),
         priority=91),
    Rule("url", Category.URL,
         _r(r"(?:https?://|www\.)[^\s<>\"“”]+[^\s<>\"“”.,;:)\]]")),
    Rule("domain", Category.URL,
         _r(r"(?<![@\w.\-/])[a-z0-9][a-z0-9\-]{1,62}\.(?:de|com|net|org|eu|info|biz|at|ch|io)\b"
            r"(?:/[^\s]*[^\s.,;:)])?", re.IGNORECASE)),
    Rule("iban", Category.IBAN,
         _r(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{1,4}){3,8}\b"), validator=iban_valid, priority=100),
    Rule("bic", Category.BIC,
         _r(r"(?:BIC|SWIFT)(?:-Code)?:? ?([A-Z]{4} ?[A-Z]{2} ?[A-Z0-9]{2}(?: ?[A-Z0-9]{3})?)\b"),
         group=1, priority=95),
    Rule("account", Category.IBAN,
         _r(r"(?:Konto(?:-?(?:nummer|nr\.?))?|Kto\.?(?:-?Nr\.?)?|BLZ|Bankleitzahl)[: ]*"
            r"((?:\d[ ]?){5,12}\d)", re.IGNORECASE), group=1),
    Rule("credit_card", Category.CREDIT_CARD,
         _r(r"\b(?:\d[ -]?){12,18}\d\b"), validator=luhn_valid, priority=95),
    Rule("phone_int", Category.PHONE,
         _r(r"(?<![\w/])(?:\+|00)\d{1,3}[ \-/.]*(?:\(0\)[ \-/.]*)?\d(?:[ \-/.]?\d){5,14}"),
         validator=phone_valid),
    Rule("phone_nat", Category.PHONE,
         _r(r"(?<![\w/.,§])(?<!\d )\(?0\d{2,5}\)?\.?[ ]?(?:[-/][ ]?)?\d(?:[ \-]?\d){3,12}(?![\d.,]\d)"),
         validator=phone_valid),
    Rule("phone_ctx", Category.PHONE,
         _r(r"(?:Tel(?:efon)?|Fax|Telefax|Mobil|Handy|Mobilfunk|Ruf)(?:-?(?:nummer|nr\.?))?\.?[: ]*"
            r"((?:\+|\()?\d(?:[ \-/.()]{0,3}\d){5,16})", re.IGNORECASE),
         group=1, validator=phone_valid),
    Rule("tax_id_ctx", Category.TAX_ID,
         # [IıIl1|]: OCR reads the "I" of "ID" in many ways ("Steuer-ıD", "Steuer-lD").
         _r(r"(?:Steuer[- ]?[IıIl1|][Dd]|Steuer-?Identifikationsnummer|Identifikationsnummer|"
            r"IdNr\.?)[^\d\n]{0,40}?(\d{2}[ \-]?\d{3}[ \-]?\d{3}[ \-]?\d{3})(?!\d)", re.IGNORECASE), group=1, priority=94),
    Rule("tax_id", Category.TAX_ID,
         _r(r"(?<![\d,.])\d{2}[ \-]?\d{3}[ \-]?\d{3}[ \-]?\d{3}(?!\d|[,.]\d)"),
         validator=tax_id_valid,
         priority=93),
    Rule("tax_number", Category.TAX_ID,
         _r(r"(?:Steuernummer|Steuer-Nr\.?|St\.-?Nr\.?|StNr\.?)[: ]*(\d{2,3}/\d{3,4}/\d{4,5}|"
            r"\d{10,13})", re.IGNORECASE), group=1),
    Rule("vat_id", Category.REGISTER_NUMBER,
         _r(r"\b(?:DE ?\d{9}|ATU ?\d{8}|CHE-?\d{3}\.\d{3}\.\d{3})\b")),
    Rule("trade_register", Category.REGISTER_NUMBER,
         _r(r"\b(?:HRB|HRA|GnR|PR|VR|GsR)[ ]?\d{1,7}(?: ?[A-Z]{1,2}\b)?")),
    Rule("social_security", Category.SOCIAL_SECURITY,
         _r(r"\b\d{2}[ .]?\d{6}[ .]?[A-Za-z][ .]?\d{3}\b")),
    Rule("health_insurance", Category.SOCIAL_SECURITY,
         _r(r"\b[A-Z]\d{9}\b")),
    Rule("id_document", Category.ID_DOCUMENT,
         _r(r"(?:Personalausweis|Ausweis|Reisepass|Pass|Aufenthaltstitel|Führerschein|Dokument)"
            r"(?:-?(?:nummer|nr\.?))?[: ]*(?:Nr\.?:? ?)?((?=[A-Z0-9]*\d)[A-Z0-9]{6,12})\b",
            re.IGNORECASE), group=1),
    Rule("license_plate_ctx", Category.LICENSE_PLATE,
         _r(r"(?:Kennzeichen|Kfz-Kennzeichen|amtl\. Kennzeichen)[: ]*"
            r"(\p{Lu}{1,3}[- ]\p{Lu}{1,2} ?\d{1,4}[EH]?)\b"), group=1),
    Rule("license_plate", Category.LICENSE_PLATE,
         _r(r"\b\p{Lu}{1,3}-\p{Lu}{1,2} \d{1,4}[EH]?\b")),
    Rule("birth_date", Category.BIRTH_DATE,
         # "gob"/"gcb": typical OCR misreadings of "geb."
         # "Geburt\w*" also covers OCR variants such as "Geburtadatum".
         _r(rf"(?:g[eoc]b[.,]?|geboren|Geburt\w{{0,6}}|Geb\.-Datum|\*)"
            rf"(?: (?:am|arn|ani|an|om))?:?[ ]?"
            rf"({DATE}|{SLOPPY_DATE})", re.IGNORECASE), group=1, priority=96),
    Rule("birth_place", Category.LOCATION,
         _r(rf"(?:geboren|geb\.)(?:(?: am)? [^\n]{{0,25}}?)? in ({UP}{LET}+(?:[- ]{UP}{LET}+)?)"
            rf"|Geburtsort:? ({UP}{LET}+(?:[- ]{UP}{LET}+)?)"), group=-1),
    Rule("ip_address", Category.IP_ADDRESS,
         _r(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")),
    Rule("street", Category.ADDRESS,
         _r(rf"(?:{UP}[\p{{L}}.\-]*[ -]){{0,3}}?(?:{UP}[\p{{L}}\-]*?(?:-\n\p{{Ll}}*?)?)?"
            rf"(?i:{STREET_SUFFIX}) "
            rf"{HOUSE_NUMBER}"), priority=92),
    Rule("street_prep", Category.ADDRESS,
         _r(rf"\b{STREET_PREPOSITIONS} {UP}[\p{{L}}\-]+(?: {UP}[\p{{L}}\-]+)? {HOUSE_NUMBER}"),
         priority=92),
    Rule("postcode_city", Category.ADDRESS,
         _r(rf"(?<![\d./§])(?:D-)?\d{{5}} {UP}[\p{{L}}\-]+(?:(?: (?:am|an der|im|in der|ob der|"
            rf"bei|vor der)) {UP}[\p{{L}}]+| \({UP}[\p{{L}} .]+\)| {UP}[\p{{L}}\-]+(?=\n))?"),
         priority=92),
    Rule("street_name", Category.ADDRESS,
         # Street name without (readable) house number: "im Hause Tschentschergasse".
         _r(rf"\b{UP}[\p{{L}}\-]{{2,}}(?:straße|strasse|gasse|allee|chaussee|promenade)\b"),
         priority=80),
    Rule("po_box", Category.ADDRESS,
         _r(r"Postfach[: ]+\d(?:[ ]?\d){2,9}"), priority=92),
]

# Organisations recognisable by their legal form.
LEGAL_FORMS = (r"(?:e\. ?K\.?|e\. ?Kfm\.|e\. ?Kfr\.|Inh\.|GmbH & Co\.? KG|GmbH & Co\.? KGaA|gGmbH|GmbH|mbH|AG & Co\.? KG|AG|KGaA|KG|OHG|"
               r"UG \(haftungsbeschränkt\)|UG|GbR|PartG mbB|PartGmbB|PartG|e\. ?V\.|eG|SE|"
               r"Ltd\.?|Limited|Inc\.?|LLC|S\.A\.|S\.r\.l\.|B\.V\.|N\.V\.|"
               r"Stiftung|Partnerschaft(?:sgesellschaft)?(?: mbB)?|& Partner(?: mbB)?|& Kollegen)")
ORG_RULES: list[Rule] = [
    Rule("org_legal_form", Category.ORG,
         _r(rf"(?:(?:{UP}[\p{{L}}&.\-']*|&|\d+)[ ]){{1,4}}{LEGAL_FORMS}(?![\p{{L}}])"),
         priority=85),
    Rule("org_prefix", Category.ORG,
         _r(rf"\b(?:Kanzlei|Rechtsanwaltskanzlei|Firma|Fa\.|Praxis|Arztpraxis|Hotel|Restaurant|"
            rf"Autohaus|Bäckerei|Pflegedienst|Hausverwaltung|Immobilien|Steuerkanzlei|"
            rf"Ingenieurbüro|Architekturbüro|Spedition|Gaststätte|Apotheke) "
            rf"{UP}[\p{{L}}&\-']+(?: (?:{UP}[\p{{L}}&\-']*|&|und)){{0,3}}"), priority=84),
]

# Keywords that introduce file numbers or other reference numbers.
CASE_KEYWORDS = _r(
    r"(?<!\p{L})(?:Az\.?|Aktenzeichen|Akt\.-?Z\.?|Geschäftszeichen|Gz\.?|GZ|Unser Zeichen|Ihr Zeichen|"
    r"Unsere Zeichen|Ihre Zeichen|Zeichen|Tgb\.-?Nr\.?|Tagebuchnummer|Vorgangsnummer|"
    r"Vorgangs-Nr\.?)(?: ?:| |\n)", re.IGNORECASE)
REFERENCE_KEYWORDS = _r(
    r"\b(?!(?:Rand|Haus|Seiten|Anlagen|Telefon|Fax|Steuer|Ausweis|Konto|Rn)(?:nummer|-?nr))"
    r"[\p{L}]{2,}(?:-?[Nn]u[mn]{1,2}i?e[rn]|-Nr\.?|nr\.)(?: ?[:;]| )|\b(?:FIN|Fahrgestell-?Nr\.?|"
    r"Kassenzeichen|Ref\.?|Referenz|Policen?-?Nr\.?)(?: ?:| )")
_REF_TOKEN = _r(r"[\p{L}0-9./\-()]{1,20}")


#: Abbreviations before "-Nummer" that denote personal numbers (unlike
#: "GOÄ-Nr." of a fee schedule): social/pension/health insurance, tax, car.
_PERSONAL_NUMBER_PREFIXES = {"sv", "rv", "kv", "ust", "st", "kfz", "bg", "vn", "pk", "ik"}


def _reference_after(text: str, start: int) -> tuple[int, int] | None:
    """Collect the reference number that follows a keyword at ``start``.

    Takes up to eight space separated tokens that look like parts of a file
    number (``12 O 345/21``, ``VI ZR 12/20``, ``K 42 / 2025 / ab``) and
    requires at least one digit overall. A token ending in punctuation ends
    the number. The number may start on the line after the keyword.
    """
    pos = start
    while pos < len(text) and text[pos] == " ":
        pos += 1
    if pos < len(text) and text[pos] == "\n":
        pos += 1
    first = end = None
    has_digit = False
    tokens = 0
    while tokens < 8 and pos < len(text):
        while pos < len(text) and text[pos] == " ":
            pos += 1
        match = _REF_TOKEN.match(text, pos)
        if not match:
            break
        raw = match.group(0)
        token = raw.rstrip(".,;:)")
        if not token:
            break
        digit = any(ch.isdigit() for ch in token)
        # Short letter codes inside file numbers: "O", "C", "Js", "ZR", "BvR",
        # initials of the clerk ("ks"); separators "/" and "-".
        short_code = (len(token) <= 3 and token.isalpha() and not is_stopword(token)) \
            or token in ("/", "-")
        if not (digit or short_code):
            break
        has_digit |= digit
        if first is None:
            first = pos
        end = pos + len(token)
        pos = match.end()
        tokens += 1
        if token != raw:  # trailing punctuation closes the reference
            break
    if end is None or not has_digit:
        return None
    # Drop trailing letter codes/separators that are not part of the number,
    # unless they follow a separator ("2026 / ks" keeps "ks").
    while " " in text[first:end]:
        head, tail = text[first:end].rsplit(" ", 1)
        if any(ch.isdigit() for ch in tail) or (tail.isalpha() and head.endswith("/")):
            break
        end -= len(tail) + 1
    return first, end


def _street_ok(text: str) -> bool:
    """Reject street matches that are ordinary phrases ("im Jahr 2020")."""
    words = text.split()
    if len(words) < 2:
        return False
    numbers = [w for w in words if any(ch.isdigit() for ch in w)]
    if not numbers or re.fullmatch(r"(?:19|20)\d\d", numbers[0]):
        return False
    name_words = [w for w in words if w[:1].isupper()]
    if not name_words:
        return False
    # A bare suffix word ("Weg 5") is not an address.
    if len(words) == 2 and re.fullmatch(rf"(?i:{STREET_SUFFIX})", words[0]):
        return False
    return not all(is_stopword(w) for w in name_words)


_NOT_A_CITY = {"euro", "eur", "stück", "stk", "jahre", "jahren", "mitglieder", "einwohner",
               "personen", "seiten", "km", "kg", "m²", "qm", "tage", "tagen"}


def _postcode_ok(text: str) -> bool:
    city = text.split()[1] if len(text.split()) > 1 else ""
    return city.casefold().rstrip(".,") not in _NOT_A_CITY and not is_stopword(city)


def _url_ok(text: str) -> bool:
    lowered = text.casefold()
    return not any(domain in lowered for domain in PUBLIC_DOMAINS)


def _org_ok(text: str) -> bool:
    words = [w for w in text.split() if w not in ("&", "und")]
    # Strip leading stop words ("die Beklagte GmbH" -> nothing left => reject)
    while words and is_stopword(words[0]):
        words.pop(0)
    return len(words) >= 2


_GENERIC_STREETS = {"bundesstraße", "landstraße", "kreisstraße", "staatsstraße",
                    "seitenstraße", "nebenstraße", "hauptverkehrsstraße", "einbahnstraße",
                    "spielstraße", "fahrradstraße", "zufahrtsstraße",
                    "durchgangsstraße", "ortsstraße", "baumallee"}


def _street_name_ok(text: str) -> bool:
    return text.lower() not in _GENERIC_STREETS  # lower(): casefold() turns "ß" into "ss"


_EXTRA_VALIDATORS: dict[str, Callable[[str], bool]] = {
    "street_name": _street_name_ok,
    "street": _street_ok,
    "street_prep": _street_ok,
    "postcode_city": _postcode_ok,
    "url": _url_ok,
    "domain": _url_ok,
    "org_legal_form": _org_ok,
}


def _trim_org(text: str, start: int) -> tuple[int, str]:
    """Remove leading stop words like "Die" or "Beklagte" from an org match."""
    while True:
        first, _, rest = text.partition(" ")
        if rest and (is_stopword(first) or first.islower()):
            start += len(first) + 1
            text = rest
        else:
            return start, text


def find_iban(text: str) -> Iterator[tuple[int, int]]:
    """IBANs, trimming trailing tokens that were swallowed by the pattern."""
    rule = next(r for r in RULES if r.name == "iban")
    for match in rule.pattern.finditer(text):
        candidate = match.group(0)
        # Try shorter candidates (drop trailing groups) until the checksum fits.
        # Groups may be separated by a space or a line break.
        ends = [m.end() for m in re.finditer(r"\S+", candidate)]
        for end in reversed(ends):
            sub = candidate[:end]
            if len(re.sub(r"\s", "", sub)) >= 15 and iban_valid(sub):
                yield match.start(), match.start() + end
                break


def detect(text: str, page: int, enabled: set[Category]) -> list[Detection]:
    """Run all rules on ``text`` (the plain text of one page)."""
    found: list[Detection] = []

    def add(start: int, end: int, category: Category, source: str, priority: int) -> None:
        if category in enabled and end > start:
            found.append(Detection(page, start, end, text[start:end], category,
                                   f"regel:{source}", score=priority / 100))

    for start, end in find_iban(text):
        add(start, end, Category.IBAN, "iban", 100)

    for rule in RULES + ORG_RULES:
        if rule.name == "iban" or rule.category not in enabled:
            continue
        for match in rule.pattern.finditer(text):
            if rule.group == -1:  # first non-empty group
                group = next((g for g in range(1, len(match.groups()) + 1)
                              if match.group(g)), 0)
            else:
                group = rule.group
            start, end = match.span(group)
            value = text[start:end]
            if rule.validator and not rule.validator(value):
                continue
            extra = _EXTRA_VALIDATORS.get(rule.name)
            if extra and not extra(value):
                continue
            if rule.category is Category.ORG:
                start, value = _trim_org(value, start)
                end = start + len(value)
            add(start, end, rule.category, rule.name, rule.priority)

    for keywords, category in ((CASE_KEYWORDS, Category.CASE_NUMBER),
                               (REFERENCE_KEYWORDS, Category.REFERENCE_NUMBER)):
        if category not in enabled:
            continue
        for match in keywords.finditer(text):
            prefix = match.group(0).split("-")[0]
            if category is Category.REFERENCE_NUMBER and prefix.isupper() \
                    and prefix.casefold() not in _PERSONAL_NUMBER_PREFIXES:
                continue  # "GOÄ-Nr. 50", "BGB-Nr." – numbers of legal fee schedules
            span = _reference_after(text, match.end())
            if span:
                add(span[0], span[1], category, category.value.lower(), 88)
    return found
