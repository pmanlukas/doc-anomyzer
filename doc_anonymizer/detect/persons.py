"""Rule based recognition of person names in German legal documents.

Statistical models (spaCy/GLiNER) miss many names in legal letters, e.g.
"Richterin am Landgericht Dr. Weber" or "Mustermann, Erika". These rules use
the strong contextual signals typical for German legal texts:

* salutations   – "Herr", "Frau", "Herrn"
* titles        – "Dr.", "Prof.", "Dipl.-Ing."
* roles         – "Rechtsanwältin", "Zeuge", "Richter am Amtsgericht"
* signatures    – "gez.", "i. A.", "i. V."
* maiden names  – "geb. Schmidt"
* known first names followed by a capitalised word – "Erika Mustermann"
* inverted form – "Mustermann, Erika"
"""

from __future__ import annotations

from dataclasses import dataclass

import regex as re

from ..lexicon import is_first_name, is_keep_prefix, is_stopword
from ..models import Category, Detection

SALUTATIONS = {"herr", "herrn", "frau", "hr.", "fr.", "hrn.", "fräulein", "mr.", "mrs.", "ms.",
               # frequent OCR misreadings of scans
               "herm", "hern", "herrm", "henn", "herr.", "frau.", "frou", "fraun"}
TITLES = {
    "dr.", "prof.", "dres.", "dr.-ing.", "dipl.-ing.", "dipl.-kfm.", "dipl.-kffr.",
    "dipl.-psych.", "dipl.-jur.", "dipl.-betriebsw.", "dipl.-volksw.", "dipl.-päd.",
    "med.", "jur.", "rer.", "nat.", "pol.", "phil.", "oec.", "dent.", "vet.", "h.c.",
    "h.", "c.", "mag.", "ll.m.", "m.a.", "b.a.", "msc.", "univ.-prof.", "priv.-doz.", "pd",
    "apl.", "habil.", "ing.", "rer.nat.", "med.dent.",
}
#: Roles typically followed by a name (but not always – checked more strictly).
ROLES = {
    "rechtsanwalt", "rechtsanwältin", "ra", "rain", "rae", "anwalt", "anwältin", "notar",
    "notarin", "notariatsverwalter", "richter", "richterin", "vorsitzende", "vorsitzender",
    "vorsitzenden", "staatsanwalt", "staatsanwältin", "oberstaatsanwalt",
    "oberstaatsanwältin", "rechtspfleger", "rechtspflegerin", "zeuge", "zeugin", "zeugen",
    "sachverständige", "sachverständiger", "sachverständigen", "gutachter", "gutachterin",
    "dolmetscher", "dolmetscherin", "geschäftsführer", "geschäftsführerin", "sachbearbeiter",
    "sachbearbeiterin", "betreuer", "betreuerin", "verfahrenspfleger", "verfahrenspflegerin",
    "verfahrensbeistand", "insolvenzverwalter", "insolvenzverwalterin", "gerichtsvollzieher",
    "gerichtsvollzieherin", "steuerberater", "steuerberaterin", "wirtschaftsprüfer",
    "wirtschaftsprüferin", "arzt", "ärztin", "oberarzt", "oberärztin", "chefarzt",
    "chefärztin", "kollege", "kollegin", "kollegen", "polizeibeamter", "polizeibeamtin",
    "pok", "phk", "khk", "kok", "kk", "pk", "pm", "pom", "pkin", "pokin", "phkin", "khkin",
    "kriminalkommissar", "kriminalhauptkommissar", "polizeikommissar",
    "polizeihauptkommissar", "polizeioberkommissar", "polizeiobermeister", "vorstand",
    "prokurist", "prokuristin", "inhaber", "inhaberin", "kläger", "klägerin", "beklagte",
    "beklagter", "beklagten", "antragsteller", "antragstellerin", "antragsgegner",
    "antragsgegnerin", "angeklagte", "angeklagter", "angeklagten", "beschuldigte",
    "beschuldigter", "geschädigte", "geschädigter", "erblasser", "erblasserin", "mieter",
    "mieterin", "vermieter", "vermieterin", "ehemann", "ehefrau", "sohn", "tochter",
    "vater", "mutter", "bruder", "schwester", "lebensgefährte", "lebensgefährtin",
    "verteidiger", "verteidigerin", "nebenkläger", "nebenklägerin", "mandant", "mandantin",
    "schuldner", "schuldnerin", "gläubiger", "gläubigerin", "patient", "patientin",
    "arbeitnehmer", "arbeitnehmerin", "mitarbeiter", "mitarbeiterin", "versicherungsnehmer",
    "versicherungsnehmerin", "beteiligter", "beteiligte", "unfallbeteiligter",
    "fahrer", "fahrerin", "halter", "halterin", "eigentümer", "eigentümerin", "urkundsbeamter", "urkundsbeamtin", "justizangestellte",
    "justizbeschäftigte", "direktor", "direktorin", "präsident", "präsidentin",
}
#: Role words that are surnames in their own right: "Richter" is among the
#: most frequent German surnames ("Prof. Dr. Karl Richter", "Herrn Richter").
SURNAME_ROLES = {"richter"}
#: Family words are a surname after "Herr"/"Frau" ("Frau Vater sagte aus") –
#: nobody is addressed that way, except in the polite "Ihr Herr Vater".
FAMILY_WORDS = {"vater", "mutter", "sohn", "tochter", "bruder", "schwester", "ehemann"}
#: Before "Herr Richter" / "Herr Vater" these words show a form of address
#: or a description, not a surname ("Sehr geehrter Herr Richter", "Ihr Herr Vater").
_ADDRESS_WORDS = {
    "geehrter", "geehrte", "geehrten", "verehrter", "verehrte", "lieber", "liebe", "werter",
    "werte", "hallo", "ihr", "ihre", "ihrem", "ihren", "ihrer", "ihres", "sein", "seine",
    "seinem", "seinen", "seiner", "seines", "mein", "meine", "meinem", "meinen", "meiner",
    "meines", "dein", "deine", "deinem", "deinen", "deiner", "unser", "unsere", "unserem",
    "unseren", "unserer", "dessen", "deren", "der", "die", "des", "dem", "den", "ein", "eine",
    "einem", "einen", "einer", "als", "zum", "vom", "beim",
}
#: Words that may stand between a role and the name ("Richter am Landgericht X").
CONNECTORS = {"am", "beim", "des", "der", "a.d.", "i.r.", "a.", "d.", "i.", "r.",
              "vom", "von", "bei", "dem", "den", "zum", "zur"}
SIGNATURE_MARKS = {"gez.", "i.a.", "i.v.", "ppa.", "pp.", "gezeichnet"}
#: Greetings and closing formulas are followed by a name ("Hallo Lisa,",
#: "Viele Grüße\nTilo").
GREETINGS = {"hallo", "hi", "hey", "liebe", "lieber", "servus", "moin", "grüße", "gruß",
             "grüßen", "grüssen", "grüsse", "gruss", "dear"}
MAIDEN_MARKS = {"geb.", "geborene", "geborener", "geborenen", "verw.", "verwitwete", "gesch.",
                "geschiedene", "vormals"}
#: Lower-case name particles ("Ursula von der Leyen", "Rafael van der Vaart").
PARTICLES = {"von", "van", "de", "der", "den", "zu", "zur", "vom", "del", "della", "da", "di",
             "du", "ten", "ter", "le", "la", "al", "el", "bin", "ibn", "y"}

_FORM_PARTS = {"nr", "id", "nummer", "str", "tel", "fax", "mail", "datum", "kennzeichen"}
_NAME_RE = re.compile(r"^\p{Lu}[\p{L}'’]*(?:-\p{Lu}?[\p{L}'’]+)*$")
_STRIP_LEFT = "(„“\"'‚‘[<"
_STRIP_RIGHT = ")“”\"'’,.;:!?]>"


@dataclass
class Token:
    start: int
    end: int
    raw: str

    @property
    def clean(self) -> str:
        """Token without surrounding quotes/brackets/punctuation."""
        return self.raw.lstrip(_STRIP_LEFT).rstrip(_STRIP_RIGHT)

    @property
    def lower(self) -> str:
        return self.raw.lstrip(_STRIP_LEFT).casefold()

    @property
    def ends_clause(self) -> bool:
        """True if punctuation after the token ends a name (comma, period ...)."""
        stripped = self.raw.rstrip(")“”\"'’]>")
        return bool(stripped) and stripped[-1] in ",;:!?" or (
            stripped.endswith(".") and len(self.clean) > 1 and not self.clean.isupper())


def _ordinary(word: str) -> bool:
    """Known ordinary German word (noun, adjective …) – not a name part."""
    from .ner import is_known_word

    return not is_first_name(word) and is_known_word(word) and not word.isupper() \
        and is_known_word(word.lower())


def tokenize(text: str) -> list[Token]:
    return [Token(m.start(), m.end(), m.group(0)) for m in re.finditer(r"\S+", text)]


def is_name_token(token: Token) -> bool:
    """Capitalised word that could be (part of) a name."""
    word = token.clean
    if len(word) < 2 or not _NAME_RE.match(word):
        return False
    if len(word) < 3 and word.isupper():
        return False  # "AA", "OK" – abbreviations or OCR noise
    parts = word.split("-")
    if len(parts) > 1 and any(p.casefold() in _FORM_PARTS or is_stopword(p) for p in parts):
        return False  # form labels: "Führerschein-Nr", "Steuer-ID", "Kunden-Nummer"
    if is_stopword(word) or word.casefold() in SALUTATIONS or word.casefold() in ROLES:
        return False
    if is_keep_prefix(word):
        return False
    # Abbreviations such as "Abs." or "Nr." are not names.
    return not (token.raw.endswith(".") and len(word) <= 3 and not word.isupper())


def _clean_span(tokens: list[Token]) -> tuple[int, int]:
    """Character span of the name tokens without surrounding punctuation."""
    first, last = tokens[0], tokens[-1]
    start = first.start + (len(first.raw) - len(first.raw.lstrip(_STRIP_LEFT)))
    end = last.start + len(last.raw.rstrip(_STRIP_RIGHT))
    return start, end


def split_name(words: list[str], surname_only: bool = False) -> tuple[list[str], str]:
    """Split name words into (given names, surname).

    "Max Peter Mustermann" -> (["Max", "Peter"], "Mustermann")
    "Ursula von der Leyen" -> (["Ursula"], "von der Leyen")
    A single word is a surname unless it is a known first name.
    """
    words = [w for w in words if w]
    if not words:
        return [], ""
    if surname_only:
        return [], " ".join(words)
    if len(words) == 1:
        return ([words[0]], "") if is_first_name(words[0]) else ([], words[0])
    # Surname = last capitalised word plus preceding particles.
    idx = len(words) - 1
    while idx - 1 > 0 and words[idx - 1].casefold() in PARTICLES:
        idx -= 1
    return words[:idx], " ".join(words[idx:])


class PersonRules:
    """Apply the person heuristics to the plain text of one page."""

    def __init__(self, proper_noun_starts: set[int] | None = None,
                 person_starts: set[int] | None = None) -> None:
        # Offsets of tokens that spaCy tagged as proper nouns / persons. Used
        # to confirm weak signals ("Zeuge Glauben" is not a name).
        self.proper = proper_noun_starts or set()
        self.persons = person_starts or set()

    def _confirmed(self, token: Token) -> bool:
        return (is_first_name(token.clean) or token.start in self.proper
                or token.start in self.persons or token.clean.isupper())

    def _collect(self, tokens: list[Token], j: int, text: str,
                 strong: bool = False, led: bool = False) -> list[Token]:
        """Collect the name starting at token ``j``.

        A name is: optional first names, then one surname (which may carry
        particles such as "von der"). After a word that is neither a known
        first name nor confirmed by spaCy, collection stops – this prevents
        "Dr. Ute Beispiel Sparkasse Musterland" from becoming one name.
        Names never continue across a line break.
        """
        names: list[Token] = []
        while j < len(tokens) and len(names) < 4:
            tok = tokens[j]
            if names and "\n" in text[names[-1].end:tok.start] and not all(
                    is_first_name(n.clean) for n in names if n.lower not in PARTICLES):
                # A line break ends the name once the surname is complete
                # (address blocks, signatures). "Herr Max\nMustermann" in
                # running text still continues because only a first name was seen.
                break
            if names and "\n" in text[names[-1].end:tok.start] and j + 1 < len(tokens) \
                    and tokens[j + 1].clean[:1].isdigit() \
                    and "\n" not in text[tok.end:tokens[j + 1].start]:
                # Next line is "Word 12": a street with house number, not a name
                # ("Grit Herrmann" / "Pärtzeltring 517" in an address block).
                break
            if is_name_token(tok):
                if names and not self._continues(names, tok, strong):
                    break
                names.append(tok)
                if tok.ends_clause:
                    break
                j += 1
            elif tok.lower in PARTICLES and j + 2 < len(tokens) and (
                    is_name_token(tokens[j + 1]) or (tokens[j + 1].lower in PARTICLES
                                                     and is_name_token(tokens[j + 2]))):
                names.append(tok)  # "von" / "van der" in "Frau van der Dussen"
                j += 1
            elif self._role_after_first_name(names, tok, led):
                names.append(tok)  # "Karl Richter", "Frau Anna Vater": the surname
                break
            else:
                break
        # Never end on a particle.
        while names and names[-1].lower in PARTICLES:
            names.pop()
        return names

    @staticmethod
    def _role_after_first_name(names: list[Token], tok: Token, led: bool) -> bool:
        """A role word directly after a first name is the surname.

        "Richter" always ("Karl Richter"); other role words only where a
        salutation, title or role leads in ("Frau Anna Vater", "die Zeugin
        Anna Vater"). Only words the propagation never spreads on their own
        (stop words) qualify: the single mention is removed, "der Vater" and
        "der Richter am Landgericht" elsewhere in the file stay readable.
        """
        word = tok.clean.casefold()
        if word not in ROLES or not is_stopword(tok.clean) \
                or not tok.clean[:1].isupper() or tok.clean.isupper():
            return False
        given = [n for n in names if n.lower not in PARTICLES]
        if not given or not all(is_first_name(n.clean) for n in given):
            return False
        return word in SURNAME_ROLES or led

    def _role_is_surname(self, tokens: list[Token], lead: int, j: int, text: str,
                         after_title: bool) -> bool:
        """``tokens[j]`` (a role or family word) is used as a surname.

        Checked for the word directly after a salutation or title:
        "Herrn Richter angehört", "Dr. Richter", "Frau Vater sagte aus" – but
        not "Herr Richter Dr. Weber", "Richter am Landgericht" (a name or a
        court follows: the word is the role), "Sehr geehrter Herr Richter"
        (form of address) or "Ihr Herr Vater".
        """
        tok = tokens[j]
        word = tok.clean.casefold()
        if word not in SURNAME_ROLES | FAMILY_WORDS or not tok.clean[:1].isupper():
            return False
        if word in FAMILY_WORDS and tokens[lead].lower not in SALUTATIONS:
            return False
        nxt = tokens[j + 1] if j + 1 < len(tokens) else None
        if nxt is not None and not tok.ends_clause and "\n" not in text[tok.end:nxt.start]:
            following = nxt.clean.casefold()
            # Any capitalised word that could be a name: "Herr Richter Weber".
            # (Many surnames are ordinary words – "Weber", "Koch" – so the
            # dictionary cannot decide here; the role reading loses nothing.)
            if (nxt.lower in TITLES or following in SALUTATIONS or following in CONNECTORS
                    or is_keep_prefix(nxt.clean) or is_name_token(nxt)):
                return False
        if after_title:
            return True  # "Dr. Richter", "Frau Prof. Dr. Richter"
        before = tokens[lead - 1].clean.casefold() if lead else ""
        return before not in _ADDRESS_WORDS

    def _continues(self, names: list[Token], tok: Token, strong: bool = False) -> bool:
        """May ``tok`` be appended to the name collected so far?"""
        previous = [n for n in names if n.lower not in PARTICLES]
        if not previous or names[-1].lower in PARTICLES:
            return True  # first word or after "von"
        if strong and len(previous) == 1 and (
                tok.start in self.proper or tok.start in self.persons
                or not _ordinary(tok.clean)):
            # "Frau Cäcilie Meyer": after a salutation/title two capitalised
            # words are first name + surname, even if the first name is unknown
            # – but not "Frau Kambs Ansprüche" (ordinary noun).
            return True
        if all(is_first_name(n.clean) for n in previous):
            return True  # "Max" -> "Max Mustermann", "Max Peter" -> "... Mustermann"
        if len(previous) == 1 and tok.start in self.persons \
                and previous[-1].start in self.persons:
            return True  # unknown first name, spaCy sees one person ("Svetlana Ivanova")
        return tok.clean.isupper() and previous[-1].clean.isupper()  # "ERIKA MUSTER"

    def detect(self, text: str, page: int) -> list[Detection]:
        tokens = tokenize(text)
        found: list[Detection] = []
        used: set[int] = set()  # token indices already part of a name

        def emit(names: list[Token], source: str, surname_only: bool = False,
                 inverted: bool = False) -> None:
            if not names:
                return
            idx = [tokens.index(n) for n in names]
            if any(i in used for i in idx):
                return
            used.update(idx)
            words = [n.clean for n in names]
            if inverted:
                given, surname = words[1:], words[0]
            else:
                given, surname = split_name(words, surname_only)
            start, end = _clean_span(names)
            found.append(Detection(page, start, end, text[start:end], Category.PERSON,
                                   f"regel:{source}", 0.9,
                                   {"given": given, "surname": surname}))

        for i, tok in enumerate(tokens):
            low = tok.lower
            if low in ("v.", "a.") and i > 0 and tokens[i - 1].lower == "i.":
                low = "i." + low  # "i. V. Name" -> signature
            # --- salutation / title / signature / maiden name -----------------
            if low.rstrip(",") in GREETINGS and i + 1 < len(tokens):
                names = self._collect(tokens, i + 1, text)
                # Only a short name directly after the greeting ("Hallo Lisa,").
                if names and len(names) <= 3 and (is_first_name(names[0].clean)
                                                  or self._confirmed(names[0])):
                    emit(names, "gruss")
                continue
            if low in SALUTATIONS or low in TITLES or low in SIGNATURE_MARKS \
                    or low in MAIDEN_MARKS or low in ROLES:
                j = i + 1
                skipped = 0
                strong = low not in ROLES
                # Only titles between the lead-in and the word ("Herrn Dr. Richter").
                direct = low in SALUTATIONS or low in TITLES
                surname_role = False
                while j < len(tokens) and skipped < 6:
                    nxt = tokens[j].lower
                    if direct and self._role_is_surname(
                            tokens, i, j, text, low in TITLES or skipped > 0):
                        surname_role = True
                        break
                    direct = direct and nxt in TITLES
                    if nxt in PARTICLES and j + 1 < len(tokens) and (
                            is_name_token(tokens[j + 1]) or tokens[j + 1].lower in PARTICLES):
                        break  # "Frau von der Leyen": particles belong to the name
                    if re.fullmatch(r"\d{1,2}[:.)]?|[:\-–]", nxt):
                        j += 1  # "Beteiligter 2: Name", "Zeuge - Name"
                        skipped += 1
                        continue
                    if (nxt in TITLES or nxt in ROLES or nxt in SALUTATIONS
                            or nxt in CONNECTORS or is_keep_prefix(nxt)):
                        strong |= nxt in TITLES or nxt in SALUTATIONS
                        j += 1
                        skipped += 1
                        # "Richter am Landgericht Köln Dr. Weber": skip the place.
                        if is_keep_prefix(nxt) and j < len(tokens) \
                                and is_name_token(tokens[j]) and tokens[j + 1:j + 2] \
                                and tokens[j + 1].lower in TITLES | SALUTATIONS:
                            j += 1
                        continue
                    break
                if surname_role:
                    emit([tokens[j]], "anrede" if low in SALUTATIONS else "titel",
                         surname_only=True)
                    continue
                if low in MAIDEN_MARKS and j < len(tokens) and tokens[j].clean[:1].isdigit():
                    continue  # "geb. 01.02.1970" is a birth date, handled elsewhere
                names = self._collect(tokens, j, text,
                                      strong=strong and low not in MAIDEN_MARKS,
                                      led=low not in MAIDEN_MARKS)
                if not names:
                    continue
                # "Beteiligter 1: Kowara, Liwia" – inverted after a role/title.
                k = tokens.index(names[-1]) + 1
                if (len(names) == 1 and names[0].raw.endswith(",") and k < len(tokens)
                        and is_name_token(tokens[k]) and low not in MAIDEN_MARKS
                        and "\n" not in text[names[0].end:tokens[k].start]
                        and (is_first_name(tokens[k].clean) or self._confirmed(tokens[k]))):
                    emit([names[0], tokens[k]], "umgekehrt", inverted=True)
                    continue
                if not strong and not self._confirmed(names[0]):
                    continue
                only_surname = (low in SALUTATIONS or low in MAIDEN_MARKS) and len(names) == 1
                emit(names, "anrede" if low in SALUTATIONS else
                     "titel" if low in TITLES else
                     "geburtsname" if low in MAIDEN_MARKS else
                     "unterschrift" if low in SIGNATURE_MARKS else "rolle",
                     surname_only=only_surname)
                continue

            # --- inverted "Mustermann, Erika" ---------------------------------
            if (i + 1 < len(tokens) and tok.raw.endswith(",") and is_name_token(tok)
                    and is_name_token(tokens[i + 1]) and not is_first_name(tok.clean)
                    and (is_first_name(tokens[i + 1].clean)
                         or (tokens[i + 1].start in self.persons and tok.start in self.proper))
                    and "\n" not in text[tok.end:tokens[i + 1].start]):
                names = [tok, tokens[i + 1]]
                if i + 2 < len(tokens) and is_first_name(tokens[i + 2].clean) \
                        and not tokens[i + 1].ends_clause:
                    names.append(tokens[i + 2])
                emit(names, "umgekehrt", inverted=True)
                continue

            # --- known first name followed by a capitalised word --------------
            if is_name_token(tok) and is_first_name(tok.clean) and not tok.ends_clause:
                names = self._collect(tokens, i, text)
                if len(names) >= 2:
                    emit(names, "vorname")
                elif tok.start in self.persons:
                    emit(names, "vorname")
        return found


def clean_person_span(text: str, start: int, end: int) -> tuple[int, int, list[str]] | None:
    """Strip salutations/titles/roles from an NER person span.

    Returns the new span and the remaining name words, or ``None`` if nothing
    name-like remains (e.g. spaCy tagged "Beklagte" as a person).
    """
    tokens = tokenize(text[start:end])
    while tokens and (tokens[0].lower in SALUTATIONS | TITLES | ROLES | CONNECTORS
                      or is_stopword(tokens[0].clean)):
        tokens.pop(0)
    while tokens and (tokens[-1].lower in TITLES or is_stopword(tokens[-1].clean)):
        tokens.pop()
    if not tokens:
        return None
    words = [t.clean for t in tokens]
    if any(any(ch.isdigit() for ch in w) for w in words):
        return None
    if not all(is_name_token(t) or t.lower in PARTICLES for t in tokens):
        return None
    s, e = _clean_span(tokens)
    return start + s, start + e, words
