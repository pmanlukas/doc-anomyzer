"""Word lists bundled with the application (first names, stop words ...)."""

from __future__ import annotations

import re
from functools import lru_cache

from .config import DATA_DIR


def _read_words(name: str) -> list[str]:
    """Read a whitespace separated word list, ignoring ``#`` comment lines."""
    words: list[str] = []
    for line in (DATA_DIR / name).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            words.extend(line.split())
    return words


@lru_cache(maxsize=1)
def first_names() -> frozenset[str]:
    """Common first names, case-folded."""
    names = set()
    for name in _read_words("first_names.txt"):
        names.add(name.casefold())
        # "Karl-Heinz" should also make "Karl" and "Heinz" known.
        names.update(part.casefold() for part in name.split("-"))
    try:
        from .learning import learned_first_names

        names |= learned_first_names()  # learned from the user's decisions
    except Exception:  # noqa: BLE001 - learning data must never break detection
        pass
    return frozenset(names)


@lru_cache(maxsize=1)
def stopwords() -> frozenset[str]:
    """Words that are never names (roles, legal terms ...), case-folded."""
    return frozenset(w.casefold().rstrip(".") for w in _read_words("stopwords.txt"))


@lru_cache(maxsize=1)
def keep_prefixes() -> frozenset[str]:
    """First words of courts/authorities whose names stay readable."""
    return frozenset(w.casefold() for w in _read_words("keep_prefixes.txt"))


def is_first_name(token: str) -> bool:
    return token.casefold() in first_names()


def _base_forms(token: str) -> set[str]:
    """Token plus simple German inflections ("Bundesgerichtshofs" -> "...hof")."""
    word = token.casefold().rstrip(".").replace("-\n", "")
    forms = {word}
    for ending in ("es", "s", "n", "en", "e"):
        # Only for longer words: "Inge" must not become "ing" (as in "Dipl.-Ing.").
        if word.endswith(ending) and len(word) - len(ending) >= 4:
            forms.add(word[: -len(ending)])
    return forms


#: Labels of number fields in forms and letterheads: "Führerschein-Nr.",
#: "Kunden-Nr", "Vertragsnummer", "Kfz-Kennzeichen" – never a name.
_FIELD_LABEL = re.compile(r".{2,}(?:-nr|nr|-nummer|nummer|-kennzeichen|kennzeichen|-id|-idnr)", re.I)


def is_field_label(token: str) -> bool:
    return bool(_FIELD_LABEL.fullmatch(token.strip().rstrip(".:")))


def is_stopword(token: str) -> bool:
    return bool(_base_forms(token) & stopwords()) or is_field_label(token)


def compound_around(text: str, start: int, end: int) -> str:
    """The hyphenated word a match is part of ("Steuer" in "Steuer-ID"), else ""."""
    before = start >= 2 and text[start - 1] == "-" and text[start - 2].isalpha()
    after = end + 1 < len(text) and text[end] == "-" and text[end + 1].isalnum()
    if not before and not after:
        return ""
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    while end < len(text) and not text[end].isspace():
        end += 1
    return text[start:end].strip(".,;:()„“\"'")


def is_harmless_compound(compound: str, term: str, person: bool) -> bool:
    """A name found inside a hyphenated word that is *not* this name.

    "Steuer-ID" is the label of a form field, not the surname "Steuer"
    (digits ignored: the OCR reads it as "Steuer-1ID"); "Max-Planck-Institut"
    is not the client Max. Everything else counts: "das Mustermann-Gutachten".
    The detection, the verification and the automatic repair all use this
    one rule – what one of them calls a leak, the others can fix.
    """
    return (is_field_label(re.sub(r"\d", "", compound)) or is_stopword(compound)
            or (person and is_first_name(term)))


def is_keep_prefix(token: str) -> bool:
    """Courts/authorities ("Amtsgericht", also inflected "Bundesgerichtshofs")."""
    return bool(_base_forms(token.strip(".,:;()")) & keep_prefixes())
