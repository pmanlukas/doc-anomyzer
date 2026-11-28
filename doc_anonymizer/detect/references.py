"""Exhibit designations and similar codes – numbering, not personal data.

Pleadings number their exhibits: "Anlage K 1" (Kläger), "B 3" (Beklagte),
"ASt 2" / "AG 01" (Antragsteller / Antragsgegner), "BK 1" / "BB 1" (appeal),
"NK 11" (patent nullity), "HL 9a" or "B&B 2" (initials of the law firm),
"Beilage ./A" (Austria), "act. 3/5" (Switzerland), "C-12" (arbitration).
A statistical language model sees a capital letter and a number and guesses
a road ("B 1" = Bundesstraße), a company or a name. Anonymising these codes
makes a pleading unusable – its evidence can no longer be found.

This module finds the codes so the *statistical* detectors leave them alone.
Rules with a strict format of their own (licence plates, IBAN, case numbers
…) are not affected: they never rely on the model's guess.

Two forms are recognised:

* **anchored** – after "Anlage", "Anl.", "Anlagenkonvolut", "Beilage",
  "Exhibit", "act." …: any short prefix counts, because law firms invent
  their own ("Anlage rop 1", "Anlage KAP 21");
* **bare** – without such a word ("wie sich aus K 1 ergibt", "(B 3)",
  "K 4 bis K 6"): one to four capital letters, or one of the mixed-case
  prefixes in common use, directly followed by the number.

A bare capital-letter code is never a person: names are not written that way.
What it shares its form with – roads ("B 1"), pay grades ("E 13"), paper
sizes, vitamins, ICD codes – is not personal data either.
"""

from __future__ import annotations

import regex as re

#: Number of an exhibit: "1", "01", "116", "1a", "1.1", "2-1", "5/5a".
_NUMBER = r"\d{1,4}[a-z]?(?:[./\-]\d{1,3}[a-z]?)?"
#: Firm initials with an ampersand: "B&B", "B & B", "K&K".
_AMPERSAND = r"(?:[ \u00a0]?&[ \u00a0]?\p{Lu}{1,3})?"
#: Mixed-case prefixes in common use (role of the party, a few law firms).
_KNOWN = r"ASt|Ast\.?|AGg|Bf|Bg|Vk|Vb|NiK|rop|StrH|StH"
#: Between prefix and number: nothing, a blank, a hyphen or an underscore –
#: or the end of a line ("Anlage K" / "1" in justified text).
_GAP = r"[ \u00a0\n]?[-_]?[ \u00a0\n]?"

_BARE = re.compile(
    rf"(?<![\p{{L}}\p{{N}}&])(?:{_KNOWN}|\p{{Lu}}{{1,4}}{_AMPERSAND})(?!\p{{L}}){_GAP}{_NUMBER}"
    rf"(?![\p{{L}}\p{{N}}])")

#: Words that announce an exhibit (in any case: "ANLAGE K 1").
_ANCHOR = (r"(?i:Anlagenkonvolut(?:e[sn]?|s)?|Anlagen?|Anl\.|Anlg\.|"
           r"(?:Klage(?:antwort)?|Replik|Duplik|Beschwerde)?beilagen?|"
           r"Exhibits?|act\.)")
#: Small words that may follow such a word but are no prefix ("Anlage zum …").
_NO_PREFIX = (r"(?!(?:zum|zur|zu|von|vom|van|de|der|die|das|des|dem|den|mit|bis|und|ab|"
              r"nr|im|in|an|am|als|auf|aus|bei|je|pro|vor)\b)")
#: Prefix after an anchor: capitals, a known mixed-case form, or a short
#: lower-case token ("rop") – never a capitalised word, which could be a
#: name ("Anlage Max 3" is not an exhibit designation).
_ANCHORED_PREFIX = (rf"(?:{_KNOWN}|\p{{Lu}}{{1,4}}{_AMPERSAND}|{_NO_PREFIX}\p{{Ll}}{{2,4}})"
                    r"(?!\p{L})\.?")  # a whole token: "M" + "ax" is not two prefixes
_ANCHORED = re.compile(
    rf"(?<![\p{{L}}\p{{N}}]){_ANCHOR}(?:[ \u00a0_]+|\n|(?=[\p{{Lu}}\d]))"
    rf"(?:(?:{_ANCHORED_PREFIX}{_GAP}){{0,2}}{_NUMBER}"     # Anlage K 1, Anlage rop WK 1, Anlage 3
    r"|\./[ \u00a0]?[\p{Lu}\d]{1,3}"                        # Beilage ./A, Beilage ./1
    r"|[IVX]{1,4})"                                           # Anlage II
    r"(?![\p{L}\p{N}])")


def spans(text: str) -> list[tuple[int, int]]:
    """Character ranges of all exhibit designations and codes in ``text``."""
    return _merge([m.span() for pattern in (_BARE, _ANCHORED) for m in pattern.finditer(text)])


def _merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def is_reference(value: str) -> bool:
    """``value`` as a whole is an exhibit designation or code ("K 1", "Anlage B 3")."""
    value = value.strip(" \u00a0\t\n.,;:()[]„“\"'")
    return bool(value) and spans(value) == [(0, len(value))]


def outside(text: str, start: int, end: int,
            protected: list[tuple[int, int]]) -> tuple[int, int] | None:
    """The part of ``text[start:end]`` that is not an exhibit designation.

    Returns the range unchanged if it touches none, ``None`` if nothing with
    at least three letters is left, otherwise the longest remaining piece
    ("Anlage K 5 Mustermann" -> "Mustermann").
    """
    pieces, position, touched = [], start, False
    for p_start, p_end in protected:
        if p_end <= start or p_start >= end:
            continue
        touched = True
        if p_start > position:
            pieces.append((position, p_start))
        position = max(position, p_end)
    if not touched:
        return start, end
    if position < end:
        pieces.append((position, end))
    best: tuple[int, int] | None = None
    for p_start, p_end in pieces:
        piece = text[p_start:p_end]
        stripped = piece.strip(" \u00a0\t\n.,;:()[]–-/„“\"'")
        if sum(ch.isalpha() for ch in stripped) < 3:
            continue
        s = p_start + piece.index(stripped)
        candidate = (s, s + len(stripped))
        if best is None or candidate[1] - candidate[0] > best[1] - best[0]:
            best = candidate
    return best
