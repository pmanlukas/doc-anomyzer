"""Word lists bundled with the application (first names, stop words ...)."""

from __future__ import annotations

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


def is_stopword(token: str) -> bool:
    return bool(_base_forms(token) & stopwords())


def is_keep_prefix(token: str) -> bool:
    """Courts/authorities ("Amtsgericht", also inflected "Bundesgerichtshofs")."""
    return bool(_base_forms(token.strip(".,:;()")) & keep_prefixes())
