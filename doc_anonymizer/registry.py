"""Turn raw detections into consistent entities and replacement labels.

Responsibilities:

1. **Conflict resolution** – overlapping detections are reduced to one
   (the most reliable detector wins).
2. **Grouping** – all mentions of the same person/IBAN/company are grouped
   into one :class:`Entity`, so they get the *same* replacement everywhere –
   across all documents of a case file.
3. **Propagation** – once a name is known, *every* further occurrence is
   found, also where the models missed it (e.g. "MUSTERMANN" in a header,
   the genitive "Mustermanns", or an OCR-misread "Musterrnann").
4. **Labels** – persons become initials ("M. M."), everything else a
   numbered placeholder ("[Adresse 1]").
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import regex as re

from . import learning
from .config import Settings
from .detect.persons import PARTICLES, split_name
from .lexicon import is_keep_prefix, is_stopword
from .models import Category, Detection, Entity, ManualBox, Mention, PageInfo
from .textmap import PageText

#: Labels of automatically detected areas.
AUTO_BOX_LABELS = {"unterschrift": "[Unterschrift]", "handschrift": "[Handschrift]",
                   "stempel": "[Stempel]", "bild": "[Bild]"}

#: Categories whose further occurrences are searched as word sequences.
SEQUENCE_CATEGORIES = {Category.ORG, Category.LOCATION, Category.ADDRESS, Category.MANUAL,
                       Category.CASE_NUMBER, Category.REFERENCE_NUMBER}
_PUNCT = "(„“\"'‚‘[<)”’,.;:!?]>"
_GENITIVE = re.compile(r"(?:['’]s|['’]|s)$")


def normalise(text: str) -> str:
    """Comparison key: case-insensitive, unified whitespace and dashes."""
    text = text.replace("­", "").replace("–", "-").replace("‐", "-")
    return " ".join(text.casefold().split())


def _rare_lowercase_name(word: str, tokens) -> bool:
    """Lower-case known name that is no ordinary word ("flantz", not "lange").

    Occurs in e-mail addresses torn apart by OCR and in user names.
    """
    from .detect.ner import is_known_word

    return len(word) >= 4 and normalise(word) in tokens and not is_known_word(word)


def _is_address_fragment(word: str) -> bool:
    """Part of an e-mail/web address ("max.mustermann@", "mustermann.de")."""
    return "@" in word or bool(re.fullmatch(r"[\w\-]+(?:[._][\w\-]+)+[.@]?", word)) and \
        word.lower() == word


def normalise_word(word: str) -> str:
    return normalise(word.strip(_PUNCT))


#: Letters OCR reads instead of digits ("DEO2" for "DE02").
_OCR_DIGIT_KEY = str.maketrans({"o": "0", "i": "1", "l": "1"})


def _compact(text: str) -> str:
    """Key for numbers: letters and digits only (spacing is irrelevant).

    Letters that OCR confuses with digits are mapped to the digit, so the
    same number read once as "DE02" and once as "DEO2" is one entity.
    """
    return "".join(ch for ch in text.casefold() if ch.isalnum()).translate(_OCR_DIGIT_KEY)


def initial(name: str) -> str:
    """First letter of the relevant part of a name ("von der Leyen" -> "L")."""
    parts = [p for p in re.split(r"[\s]+", name) if p and p.casefold() not in PARTICLES]
    word = parts[0] if parts else name
    for ch in word:
        if ch.isalpha():
            return ch.upper()
    return "X"


def levenshtein_at_most(a: str, b: str, limit: int) -> bool:
    """True if the edit distance between ``a`` and ``b`` is ``<= limit``."""
    if abs(len(a) - len(b)) > limit:
        return False
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        if min(current) > limit:
            return False
        previous = current
    return previous[-1] <= limit


def misread_candidate(word: str) -> bool:
    """Could ``word`` be an OCR misreading of a name?

    Only capitalised words the language model does not know as ordinary
    words qualify.
    """
    from .detect.ner import is_known_word

    clean = word.strip(_PUNCT)
    return len(clean) >= 5 and clean[:1].isupper() and not is_known_word(clean)


class FuzzyIndex:
    """Find words within a small edit distance of known names – fast.

    Uses the "symmetric delete" idea (SymSpell): two words have an edit
    distance <= k only if they share a variant obtained by deleting <= k
    characters from each. Lookup cost is independent of the number of names.
    Names shorter than 5 characters are matched exactly only (too many false
    hits otherwise); up to 8 characters one error, longer names two errors.
    """

    def __init__(self, keys) -> None:
        self._variants: dict[str, set[str]] = defaultdict(set)
        self._exact = set(keys)
        for key in keys:
            if len(key) >= 5:
                for variant in self._deletions(key, self.limit(key)):
                    self._variants[variant].add(key)

    @staticmethod
    def limit(word: str) -> int:
        return 1 if len(word) < 9 else 2

    @staticmethod
    def _deletions(word: str, depth: int) -> set[str]:
        result = {word}
        frontier = {word}
        for _ in range(depth):
            frontier = {w[:i] + w[i + 1:] for w in frontier for i in range(len(w))}
            result |= frontier
        return result

    def match(self, word: str) -> str | None:
        """Best matching known name for ``word`` (or ``None``)."""
        if len(word) < 5 or not self._variants:
            return None
        if word in self._exact:
            return word
        limit = self.limit(word)
        candidates: set[str] = set()
        for variant in self._deletions(word, limit):
            candidates |= self._variants.get(variant, set())
        for key in sorted(candidates, key=lambda k: abs(len(k) - len(word))):
            if levenshtein_at_most(word, key, min(limit, self.limit(key))):
                return key
        return None


@dataclass
class _PersonKey:
    given: str  # case-folded first given name ("" if unknown)
    surname: str  # case-folded surname ("" if unknown)


@dataclass
class CaseRegistry:
    """Entities and mentions of a whole case file (one or more PDFs)."""

    docs: list[list[PageInfo]]
    settings: Settings
    entities: dict[str, Entity] = field(default_factory=dict)
    mentions: list[Mention] = field(default_factory=list)
    manual_boxes: list[ManualBox] = field(default_factory=list)
    _texts: dict[tuple[int, int], PageText] = field(default_factory=dict, repr=False)
    _covered: dict[tuple[int, int], set[int]] = field(default_factory=dict, repr=False)
    _next_id: int = field(default=1, repr=False)
    #: Pages the user has looked at (for poorly readable scans): "doc:page"
    reviewed_pages: list[str] = field(default_factory=list)
    #: Once redaction starts, labels must not change any more (documents are
    #: processed in parallel and must use identical replacements).
    labels_frozen: bool = False
    _learned: learning.Learned | None = field(default=None, repr=False)

    # ------------------------------------------------------------------ helpers

    def page_text(self, doc: int, page: int) -> PageText:
        key = (doc, page)
        if key not in self._texts:
            self._texts[key] = PageText.build(self.docs[doc][page].words)
        return self._texts[key]

    def _covered_set(self, doc: int, page: int) -> set[int]:
        return self._covered.setdefault((doc, page), set())

    def _new_entity(self, category: Category, original: str, source: str, **kw) -> Entity:
        entity = Entity(id=f"e{self._next_id}", category=category, original=original,
                        replacement="", sources=[source], variants=[original], **kw)
        self._next_id += 1
        self.entities[entity.id] = entity
        return entity

    def _allowed(self, text: str) -> bool:
        """True if the user marked this text as "never redact"."""
        allow = {normalise(t) for t in self.settings.allow_terms}
        return normalise(text) in allow

    def _add_mention(self, doc: int, page: int, words: list[int], entity: Entity,
                     form: str = "full", prefix: str = "", suffix: str = "") -> None:
        self.mentions.append(Mention(doc, page, sorted(words), entity.id, form, prefix, suffix))
        self._covered_set(doc, page).update(words)

    def _affixes(self, doc: int, page: int, words: list[int], start: int, end: int,
                 category: Category) -> tuple[str, str]:
        """Text of the first/last word that lies outside the detected span."""
        ptext = self.page_text(doc, page)
        first_start, _ = ptext.word_span(words[0])
        _, last_end = ptext.word_span(words[-1])
        prefix = ptext.text[first_start:start] if start > first_start else ""
        suffix = ptext.text[end:last_end] if end < last_end else ""
        # Only punctuation of a partially detected word is kept ("(" and "),")
        # – letters/digits glued to the data ("4,42718") could be data too.
        keep = lambda text: "".join(ch for ch in text if not ch.isalnum())  # noqa: E731
        return keep(prefix), keep(suffix)

    # --------------------------------------------------------------- building

    def build(self, detections: list[list[list[Detection]]]) -> None:
        """Create entities and mentions from ``detections[doc][page]``."""
        self._learned = learning.learned() if self.settings.use_learning else learning.Learned()
        accepted = self._resolve_conflicts(detections)
        persons = [(d, p, det, w) for d, p, det, w in accepted
                   if det.category is Category.PERSON]
        others = [(d, p, det, w) for d, p, det, w in accepted
                  if det.category is not Category.PERSON]
        self._build_persons(persons)
        # Unclassified proper nouns that are parts of known names ("Beispiel"
        # in "Dr. Muster ./. Beispiel") are left to the person propagation.
        tokens = self._person_tokens()
        others = [x for x in others if not (
            x[2].category is Category.OTHER_NAME
            and all(normalise_word(w) in tokens for w in x[2].text.split()))]
        self._build_others(others)
        # Terms the user always wants redacted (settings list + learned ones).
        terms = [(t.get("text", ""), t.get("category", "MANUAL"), "liste:immer")
                 for t in self.settings.deny_terms]
        terms += [(text, cat, "gelernt") for text, cat in self._learned.redact.items()]
        known = {normalise(v) for e in self.entities.values() for v in e.variants}
        for text, cat, source in terms:
            try:
                category = Category(cat)
            except ValueError:
                category = Category.MANUAL
            if text and normalise(text) not in known:
                self._term_entity(text, category, source)
                known.add(normalise(text))
        self.propagate()
        self._weak_guesses_to_persons()
        self.assign_labels()
        self.mark_uncertain()
        # Small images (logos, signatures, photos) are removed unless the user
        # keeps them in the review, where they appear as removable boxes.
        for d, doc_pages in enumerate(self.docs):
            for page in doc_pages:
                for x0, y0, x1, y1 in page.image_boxes:
                    self.manual_boxes.append(
                        ManualBox(d, page.index, x0, y0, x1, y1, "[Bild]", "bild"))
                for x0, y0, x1, y1, _score, kind, features in page.signature_boxes:
                    self.manual_boxes.append(ManualBox(
                        d, page.index, x0, y0, x1, y1, AUTO_BOX_LABELS.get(kind, "[Handschrift]"),
                        kind, list(features)))

    def _resolve_conflicts(self, detections):
        """Keep the most reliable detection where detections overlap."""
        accepted = []
        for d, doc_dets in enumerate(detections):
            for p, page_dets in enumerate(doc_dets):
                ptext = self.page_text(d, p)
                ranked = sorted(page_dets, key=lambda x: (-x.score, -(x.end - x.start)))
                taken: set[int] = set()
                keep = self._learned.keep if self._learned else set()
                for det in ranked:
                    if self._allowed(det.text) or not det.text.strip():
                        continue
                    if det.source in learning.WEAK_SOURCES and normalise(det.text) in keep:
                        continue  # the user said twice: this is no personal data
                    words = ptext.words_for_span(det.start, det.end)
                    if words and taken.intersection(words):
                        det, words = self._trim_person(ptext, det, words, taken)
                    if not words:
                        continue
                    taken.update(words)
                    accepted.append((d, p, det, words))
        # Stable order of first appearance (document, page, position).
        accepted.sort(key=lambda x: (x[0], x[1], x[2].start))
        return accepted

    @staticmethod
    def _trim_person(ptext: PageText, det: Detection, words: list[int],
                     taken: set[int]) -> tuple[Detection, list[int]]:
        """A name that overlaps a more reliable finding keeps its free part.

        In an address block the name rule may run on into the next line
        ("Grit Herrmann" + "Pärtzeltring", a street the address rule claims).
        Dropping the whole finding would leave the first name readable –
        instead the name is cut back to the words nobody else claimed.
        Returns ``(det, [])`` if nothing usable remains.
        """
        free = [w for w in words if w not in taken]
        if det.category is not Category.PERSON or not free:
            return det, []
        if words[:len(free)] != free and words[-len(free):] != free:
            return det, []  # overlap in the middle: no clean remainder
        start, end = ptext.word_span(free[0])[0], ptext.word_span(free[-1])[1]
        names = [w.strip(_PUNCT) for w in ptext.text[start:end].split()]
        names = [w for w in names if w]
        if not names or not all(w[:1].isupper() or w.casefold() in PARTICLES for w in names) \
                or all(is_stopword(w) for w in names):
            return det, []
        given, surname = split_name(names)
        trimmed = Detection(det.page, start, end, ptext.text[start:end], det.category,
                            det.source, det.score, {**det.meta, "given": given,
                                                    "surname": surname})
        return trimmed, free

    def _build_persons(self, persons) -> None:
        full: dict[tuple[str, str], Entity] = {}
        partial: list[tuple[int, int, Detection, list[int]]] = []
        for d, p, det, words in persons:
            given = list(det.meta.get("given") or [])
            surname = det.meta.get("surname") or ""
            if not given and not surname:
                given, surname = split_name(det.text.split())
            if any(self._allowed(x) for x in [surname, *given] if x):
                continue
            if given and surname:
                key = (normalise(given[0]), normalise(surname))
                entity = full.get(key)
                if entity is None:
                    entity = self._new_entity(Category.PERSON, f"{' '.join(given)} {surname}",
                                              det.source, given=given, surname=surname)
                    full[key] = entity
                form = "inverted" if det.source.endswith("umgekehrt") else "full"
                self._register_variant(entity, det)
                prefix, suffix = self._affixes(d, p, words, det.start, det.end, Category.PERSON)
                self._add_mention(d, p, words, entity, form, prefix, suffix)
            else:
                partial.append((d, p, det, words))

        # Surname-only / given-name-only mentions: link to a known full person
        # when unambiguous, otherwise create an entity of their own.
        by_surname: dict[str, list[Entity]] = defaultdict(list)
        by_given: dict[str, list[Entity]] = defaultdict(list)
        for entity in full.values():
            by_surname[normalise(entity.surname)].append(entity)
            by_given[normalise(entity.given[0])].append(entity)
        loose: dict[tuple[str, str], Entity] = {}
        for d, p, det, words in partial:
            given = list(det.meta.get("given") or [])
            surname = det.meta.get("surname") or ""
            if surname:
                form, key, pool = "surname", normalise(surname), by_surname
            else:
                form, key, pool = "given", normalise(given[0]), by_given
            if key not in pool and form == "surname":
                # Genitive ("Schröders") of a known surname -> same person.
                stripped = _GENITIVE.sub("", key)
                if stripped in pool or any(normalise(e.surname) == stripped
                                           for e in loose.values()):
                    key, surname = stripped, surname[:len(stripped)]
            candidates = pool.get(key, [])
            if len(candidates) == 1:
                entity = candidates[0]
            else:
                entity = loose.get((form, key))
                if entity is None:
                    entity = self._new_entity(Category.PERSON, surname or given[0], det.source,
                                              given=[] if surname else given,
                                              surname=surname)
                    loose[(form, key)] = entity
            self._register_variant(entity, det)
            prefix, suffix = self._affixes(d, p, words, det.start, det.end, Category.PERSON)
            self._add_mention(d, p, words, entity, form, prefix, suffix)

    def _build_others(self, others) -> None:
        index: dict[tuple[Category, str], Entity] = {}
        numeric = {Category.IBAN, Category.PHONE, Category.CREDIT_CARD, Category.TAX_ID,
                   Category.SOCIAL_SECURITY, Category.ID_DOCUMENT, Category.LICENSE_PLATE,
                   Category.REGISTER_NUMBER, Category.CASE_NUMBER, Category.REFERENCE_NUMBER,
                   Category.BIRTH_DATE, Category.BIC}
        for d, p, det, words in others:
            key_text = _compact(det.text) if det.category in numeric else normalise(det.text)
            key = (det.category, key_text)
            entity = index.get(key)
            if entity is None:
                entity = self._new_entity(det.category, det.text.strip(), det.source)
                index[key] = entity
            self._register_variant(entity, det)
            prefix, suffix = self._affixes(d, p, words, det.start, det.end, det.category)
            self._add_mention(d, p, words, entity, "full", prefix, suffix)

    @staticmethod
    def _register_variant(entity: Entity, det: Detection) -> None:
        if len(entity.sources) == 1 and len(entity.variants) == 1 and \
                entity.variants[0] == det.text and entity.sources[0] == det.source:
            entity.score = det.score  # first detection of a new entity
        else:
            entity.score = max(entity.score, det.score)
        if det.text not in entity.variants:
            entity.variants.append(det.text)
        if det.source not in entity.sources:
            entity.sources.append(det.source)

    # ------------------------------------------------------------ propagation

    def _person_tokens(self) -> dict[str, list[tuple[Entity, str]]]:
        """Name token -> [(entity, form)] for every known person."""
        tokens: dict[str, list[tuple[Entity, str]]] = defaultdict(list)
        for entity in self.entities.values():
            if entity.category is not Category.PERSON or not entity.enabled:
                continue
            parts: list[tuple[str, str]] = []
            if entity.surname:
                for word in entity.surname.split():
                    if word.casefold() not in PARTICLES:
                        parts.append((word, "surname"))
            for given in entity.given:
                parts.append((given, "given"))
            for word, form in parts:
                key = normalise_word(word)
                if len(key) < 2 or is_stopword(key) or self._allowed(word):
                    continue
                if all(e is not entity for e, _ in tokens[key]):
                    tokens[key].append((entity, form))
        return tokens

    def propagate(self) -> None:
        """Find every further occurrence of known entities in all documents."""
        self._propagate_sequences()
        self._propagate_person_tokens()

    def _propagate_person_tokens(self) -> None:
        tokens = self._person_tokens()
        if not tokens:
            return
        # Long tokens of OCR'd text may be misread – allow small typos there.
        fuzzy = FuzzyIndex(tokens)
        family: dict[tuple[str, str], Entity] = {}

        for d, pages in enumerate(self.docs):
            for p, page in enumerate(pages):
                covered = self._covered_set(d, p)
                for idx, word in enumerate(page.words):
                    if idx in covered:
                        continue
                    raw = word.text.strip(_PUNCT)
                    if _is_address_fragment(word.text):
                        # "romina.scalera@" – names inside e-mail addresses are
                        # lower case; redact the whole fragment.
                        parts = [normalise(x) for x in re.split(r"[.@_\-]+", word.text) if x]
                        hit = next((tokens[x] for x in parts if x in tokens), None)
                        if hit:
                            self._add_mention(d, p, [idx], hit[0][0], "full")
                        continue
                    if not raw[:1].isupper() and not _rare_lowercase_name(raw, tokens):
                        continue  # names are capitalised (also "MUSTERMANN")
                    key = normalise(raw)
                    hits = tokens.get(key)
                    if hits is None and len(key) > 3:
                        hits = tokens.get(_GENITIVE.sub("", key))
                    if hits is None and word.source == "ocr" and misread_candidate(raw):
                        match = fuzzy.match(key)
                        hits = tokens.get(match) if match else None
                    if not hits:
                        continue
                    entity, form = hits[0]
                    if len(hits) > 1:
                        # Shared surname (family) or shared first name: use a
                        # group entity so the rendering stays neutral ("M.").
                        token = next(k for k, v in tokens.items() if v is hits)
                        entity = family.get((form, token)) or self._family_entity(
                            family, (form, token), hits, form)
                    lead = word.text[:len(word.text) - len(word.text.lstrip(_PUNCT))]
                    trail = word.text[len(word.text.rstrip(_PUNCT)):]
                    self._add_mention(d, p, [idx], entity, form, lead, trail)
                    if entity.category is Category.PERSON and "weitergabe" not in entity.sources:
                        entity.sources.append("weitergabe")
        self._merge_adjacent_person_mentions()

    def _weak_guesses_to_persons(self) -> None:
        """Model guesses like "Kambs" = organisation that are known names.

        If the statistical model alone classified a word as organisation or
        place, but the word is part of a known person's name, the mentions
        belong to that person (and must not stay unredacted when the guess
        is switched off).
        """
        tokens = self._person_tokens()
        for entity in list(self.entities.values()):
            if entity.category not in (Category.ORG, Category.LOCATION, Category.OTHER_NAME):
                continue
            if any(src not in learning.WEAK_SOURCES for src in entity.sources):
                continue
            words = [normalise_word(w) for w in entity.original.split()]
            if len(words) != 1 or words[0] not in tokens:
                continue
            person, form = tokens[words[0]][0]
            for m in self.mentions:
                if m.entity_id == entity.id:
                    m.entity_id, m.form = person.id, form
            person.variants.append(entity.original)
            del self.entities[entity.id]

    def _family_entity(self, family, group_key, hits, form) -> Entity:
        """Entity for a surname (or first name) shared by several persons."""
        name = (hits[0][0].surname if form == "surname" else hits[0][0].given[0])
        existing = next((e for e in self.entities.values()
                         if e.category is Category.PERSON
                         and not (e.given if form == "surname" else e.surname)
                         and normalise(e.surname if form == "surname" else e.given[0])
                         == group_key[1]), None)
        entity = existing or self._new_entity(
            Category.PERSON, name, "weitergabe",
            surname=name if form == "surname" else "",
            given=[] if form == "surname" else [name])
        family[group_key] = entity
        return entity

    def _merge_adjacent_person_mentions(self) -> None:
        """"Max" + "Mustermann" found separately -> one full mention "M. M."."""
        by_page: dict[tuple[int, int], list[Mention]] = defaultdict(list)
        for m in self.mentions:
            by_page[(m.doc, m.page)].append(m)
        merged: list[Mention] = []
        for (d, p), mentions in by_page.items():
            ptext = self.page_text(d, p)
            pos = ptext.position_map()
            mentions.sort(key=lambda m: pos.get(m.word_indices[0], 0))
            out: list[Mention] = []
            for m in mentions:
                prev = out[-1] if out else None
                if (prev and prev.entity_id == m.entity_id and prev.form == "given"
                        and m.form == "surname" and not prev.suffix
                        and pos.get(m.word_indices[0]) == pos.get(prev.word_indices[-1], -9) + 1
                        and self.docs[d][p].words[m.word_indices[0]].line_id
                        == self.docs[d][p].words[prev.word_indices[-1]].line_id):
                    prev.word_indices = prev.word_indices + m.word_indices
                    prev.form = "full"
                    prev.suffix = m.suffix
                    continue
                out.append(m)
            merged.extend(out)
        self.mentions = merged

    def _propagate_sequences(self, only: list[Entity] | None = None) -> None:
        """Search all variants of ORG/LOCATION/ADDRESS/... entities as word sequences."""
        targets = only if only is not None else [
            e for e in self.entities.values()
            if e.category in SEQUENCE_CATEGORIES or e.category is Category.OTHER_NAME
            or any(src in ("liste:immer", "gelernt", "manuell") for src in e.sources)]
        patterns: list[tuple[list[str], Entity]] = []
        for entity in targets:
            for variant in entity.variants:
                seq = [normalise_word(w) for w in variant.split()]
                seq = [w for w in seq if w]
                if seq and not (len(seq) == 1 and (len(seq[0]) < 3 or is_stopword(seq[0]))):
                    patterns.append((seq, entity))
        if not patterns:
            return
        # Longest patterns first so "Müller Bau GmbH" wins over "Müller Bau".
        patterns.sort(key=lambda x: -len(x[0]))
        first_words: dict[str, list[tuple[list[str], Entity]]] = defaultdict(list)
        for seq, entity in patterns:
            first_words[seq[0]].append((seq, entity))

        for d, pages in enumerate(self.docs):
            for p, page in enumerate(pages):
                ptext = self.page_text(d, p)
                order = ptext.order
                norm = [normalise_word(page.words[i].text) for i in order]
                covered = self._covered_set(d, p)
                for pos_, key in enumerate(norm):
                    for seq, entity in first_words.get(key, ()):
                        end = pos_ + len(seq)
                        if norm[pos_:end] != seq:
                            continue
                        indices = order[pos_:end]
                        if covered.intersection(indices):
                            continue
                        if entity.category in (Category.LOCATION, Category.ORG,
                                               Category.OTHER_NAME) and pos_ > 0 and \
                                is_keep_prefix(norm[pos_ - 1]):
                            continue  # "Landgericht München" stays readable
                        first = page.words[indices[0]].text
                        last = page.words[indices[-1]].text
                        lead = first[:len(first) - len(first.lstrip(_PUNCT))]
                        trail = last[len(last.rstrip(_PUNCT)):]
                        self._add_mention(d, p, indices, entity, "full", lead, trail)
                        break

    # ------------------------------------------------------------------ labels

    def assign_labels(self) -> None:
        """(Re-)compute replacement texts. Manually edited labels are kept."""
        if self.labels_frozen:
            self._count_mentions()
            return
        order = self._first_appearance()
        persons = [e for e in order if e.category is Category.PERSON]
        self._label_persons(persons)
        counters: dict[Category, int] = defaultdict(int)
        for entity in order:
            if entity.category is Category.PERSON or entity.forms.get("edited"):
                continue
            counters[entity.category] += 1
            entity.replacement = f"[{entity.category.placeholder_de} {counters[entity.category]}]"
        self._count_mentions()

    def _count_mentions(self) -> None:
        for entity in self.entities.values():
            entity.count = 0
        for m in self.mentions:
            if m.entity_id in self.entities:
                self.entities[m.entity_id].count += 1

    def _first_appearance(self) -> list[Entity]:
        seen: dict[str, tuple[int, int, int]] = {}
        for m in self.mentions:
            key = (m.doc, m.page, min(m.word_indices))
            if m.entity_id not in seen or key < seen[m.entity_id]:
                seen[m.entity_id] = key
        return sorted(self.entities.values(),
                      key=lambda e: seen.get(e.id, (10**9, 0, 0)))

    @staticmethod
    def _label_persons(persons: list[Entity]) -> None:
        surname_codes: dict[str, str] = {}
        per_initial: dict[str, int] = defaultdict(int)

        def code_for(name: str, table: dict[str, str]) -> str:
            key = normalise(name)
            if key not in table:
                letter = initial(name)
                per_initial[letter] += 1
                n = per_initial[letter]
                table[key] = f"{letter}." if n == 1 else f"{letter}. ({n})"
            return table[key]

        given_codes: dict[str, str] = {}
        used_full: set[str] = set()
        for entity in persons:
            if entity.forms.get("edited"):
                used_full.add(entity.replacement)
                continue
            surname_code = code_for(entity.surname, surname_codes) if entity.surname else ""
            given_initial = f"{initial(entity.given[0])}." if entity.given else ""
            if entity.surname and entity.given:
                label = f"{given_initial} {surname_code}"
                if label in used_full:
                    # Same initials *and* same surname: use two letters of the
                    # first name ("Ma. M." / "Mo. M."), then a number.
                    two = entity.given[0][:2].capitalize() + "."
                    label = f"{two} {surname_code}"
                    n = 2
                    while label in used_full:
                        label = f"{given_initial} {surname_code} [{n}]"
                        n += 1
            elif entity.surname:
                label = surname_code
            else:
                label = CaseRegistry._given_code(entity.given[0], given_codes)
            used_full.add(label)
            entity.replacement = label
            entity.forms.update({
                "surname": surname_code or label,
                "given": given_initial or label,
                "inverted": f"{surname_code}, {given_initial}" if surname_code and given_initial
                else label,
            })

    @staticmethod
    def _given_code(given: str, table: dict[str, str]) -> str:
        key = normalise(given)
        if key not in table:
            letter = initial(given)
            n = sum(1 for v in table.values() if v.startswith(letter + ".")) + 1
            table[key] = f"{letter}." if n == 1 else f"{letter}. ({n})"
        return table[key]

    # -------------------------------------------------------------- user edits

    def _term_entity(self, text: str, category: Category, source: str) -> Entity:
        """Entity for a user supplied term (occurrences are found by propagation)."""
        if category is Category.PERSON:
            given, surname = split_name(text.split())
            entity = self._new_entity(Category.PERSON, text, source, given=given,
                                      surname=surname)
        else:
            entity = self._new_entity(category, text, source)
        entity.decided = True
        return entity

    def add_term(self, text: str, category: Category, source: str = "manuell") -> Entity | None:
        """Add a term the user wants redacted and find all its occurrences."""
        text = " ".join(text.split())
        if not text:
            return None
        entity = self._term_entity(text, category, source)
        if category is Category.PERSON:
            self._propagate_person_tokens()
        else:
            self._propagate_sequences([entity])
        self.assign_labels()
        if entity.count == 0 and category is not Category.PERSON:
            # Not found as complete words – also try inside words (e.g. "Müller"
            # in "Müller-Bau"): redact the containing words.
            self._propagate_substring(entity)
            self.assign_labels()
        return entity

    def redact_occurrences(self, entity: Entity, term: str) -> None:
        """Redact further occurrences of ``term`` (used by the auto repair).

        Whole words only (plus genitive); names only where capitalised – the
        same rules the verification uses to report a leak.
        """
        self._propagate_substring(entity, term, whole_words=True)

    def _propagate_substring(self, entity: Entity, term: str | None = None,
                             whole_words: bool = False) -> None:
        # Case-insensitive search directly in the page text (offsets must stay
        # aligned, so the text itself is not normalised); whitespace in the
        # term may match any whitespace incl. line breaks.
        parts = (term or entity.original).split()
        if not parts:
            return
        body = r"\s+".join(re.escape(p) for p in parts)
        if whole_words:
            body = (r"(?<![\p{L}\p{N}])(?<!\p{L}-)" + body
                    + r"(?:s|['’]s)?(?![\p{L}\p{N}])(?!-\p{L})")
        pattern = re.compile(body, re.IGNORECASE)
        names = entity.category in (Category.PERSON, Category.LOCATION, Category.ORG,
                                    Category.OTHER_NAME)
        for d, pages in enumerate(self.docs):
            for p, _page in enumerate(pages):
                ptext = self.page_text(d, p)
                covered = self._covered_set(d, p)
                for match in pattern.finditer(ptext.text):
                    if whole_words and names and not match.group(0)[:1].isupper():
                        continue
                    words = [w for w in ptext.words_for_span(match.start(), match.end())
                             if w not in covered]
                    if words:
                        self._add_mention(d, p, words, entity)

    def mark_uncertain(self) -> None:
        """Flag findings the user should decide on.

        * found *only* by the statistical model (no rule, no user list),
        * proper nouns no detector could classify,
        * occurrences in poorly readable (OCR) text.

        Weak organisation/place guesses ("Unzutreffend" at the start of a
        sentence) are proposed *unchecked*; everything else stays checked –
        when in doubt the program redacts.
        """
        strong = lambda src: src not in learning.WEAK_SOURCES and src != "weitergabe"  # noqa: E731
        low_conf: set[str] = set()
        #: Entities with at least one mention outside detected signature areas.
        outside_signatures: set[str] = set()
        for m in self.mentions:
            page = self.docs[m.doc][m.page]
            words = [page.words[i] for i in m.word_indices]
            if words and all(w.source == "ocr" and w.conf < 50 for w in words):
                low_conf.add(m.entity_id)
            if not all(_inside_any(w, page.signature_boxes) for w in words):
                outside_signatures.add(m.entity_id)
        for entity in self.entities.values():
            if entity.decided or entity.forms.get("edited"):
                continue
            reason = ""
            if entity.id not in outside_signatures:
                # OCR "letters" read from a signature: the whole area is
                # blacked out anyway – nothing to decide.
                pass
            elif entity.category is Category.OTHER_NAME:
                reason = "Möglicher Eigenname – bitte entscheiden"
            elif not any(strong(src) for src in entity.sources):
                reason = "Nur vom Sprachmodell erkannt – bitte bestätigen"
                if entity.score < 0.5 and entity.category in (Category.ORG,
                                                              Category.LOCATION):
                    entity.enabled = False  # likely an ordinary word
            elif entity.id in low_conf:
                reason = "Schlecht lesbare Stelle (Scan) – bitte prüfen"
            entity.uncertain = reason

    def contexts(self, entity: Entity, limit: int = 3, width: int = 90) -> list[str]:
        """Short text windows around the first mentions (for learning)."""
        result = []
        for m in self.mentions:
            if m.entity_id != entity.id:
                continue
            ptext = self.page_text(m.doc, m.page)
            start, _ = ptext.word_span(m.word_indices[0])
            _, end = ptext.word_span(m.word_indices[-1])
            window = ptext.text[max(0, start - width):end + width].replace("\n", " ")
            if window not in result:
                result.append(window)
            if len(result) >= limit:
                break
        return result

    def remove_entity(self, entity_id: str) -> None:
        self.mentions = [m for m in self.mentions if m.entity_id != entity_id]
        self.entities.pop(entity_id, None)
        self._recompute_covered()

    def _recompute_covered(self) -> None:
        self._covered.clear()
        for m in self.mentions:
            self._covered_set(m.doc, m.page).update(m.word_indices)

    # ------------------------------------------------------------- rendering

    def replacement_for(self, mention: Mention) -> str:
        entity = self.entities[mention.entity_id]
        if entity.category is Category.PERSON and mention.form != "full" \
                and not entity.forms.get("edited"):
            text = entity.forms.get(mention.form) or entity.replacement
        else:
            text = entity.replacement
        suffix = mention.suffix
        if text.endswith(".") and suffix.startswith("."):
            suffix = suffix[1:]  # "Frau D." at the end of a sentence, not "Frau D.."
        return f"{mention.prefix}{text}{suffix}"

    # ----------------------------------------------------------- persistence

    def state_for_document(self, doc: int) -> dict:
        """Entities plus only this document's mentions/boxes (for a worker)."""
        data = self.to_dict()
        data["mentions"] = [m for m in data["mentions"] if m["doc"] == doc]
        data["manual_boxes"] = [b for b in data["manual_boxes"] if b["doc"] == doc]
        return data

    def replace_document_results(self, doc: int, mentions, boxes) -> None:
        """Take over the (possibly repaired) mentions/boxes from a worker."""
        self.mentions = [m for m in self.mentions if m.doc != doc] + list(mentions)
        self.manual_boxes = [b for b in self.manual_boxes if b.doc != doc] + list(boxes)
        self._recompute_covered()
        self._count_mentions()

    def to_dict(self) -> dict:
        return {
            "entities": [e.to_dict() for e in self.entities.values()],
            "mentions": [m.to_dict() for m in self.mentions],
            "manual_boxes": [b.to_dict() for b in self.manual_boxes],
            "next_id": self._next_id,
            "reviewed_pages": self.reviewed_pages,
        }

    @classmethod
    def from_dict(cls, data: dict, docs: list[list[PageInfo]],
                  settings: Settings) -> "CaseRegistry":
        reg = cls(docs=docs, settings=settings)
        for raw in data.get("entities", []):
            entity = Entity.from_dict(raw)
            reg.entities[entity.id] = entity
        reg.mentions = [Mention(**m) for m in data.get("mentions", [])]
        reg.manual_boxes = [ManualBox(**b) for b in data.get("manual_boxes", [])]
        reg._next_id = int(data.get("next_id", len(reg.entities) + 1))
        reg.reviewed_pages = list(data.get("reviewed_pages", []))
        reg._recompute_covered()
        return reg


def _inside_any(word, boxes: list[list]) -> bool:
    """Is the centre of the word inside one of the [x0, y0, x1, y1, ...] boxes?"""
    cx, cy = (word.x0 + word.x1) / 2, (word.y0 + word.y1) / 2
    return any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes)
