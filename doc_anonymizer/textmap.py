"""Mapping between a page's word list and a flat text string.

Detectors (regular expressions, spaCy, GLiNER) work on plain text. Redaction
works on word boxes. :class:`PageText` builds the text from the words and
translates character offsets back to word indices.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

from .models import Word


@dataclass
class PageText:
    """Plain text of one page plus the offset of every word inside it."""

    text: str
    starts: list[int] = field(default_factory=list)
    ends: list[int] = field(default_factory=list)
    #: Word indices in reading order (position i in ``starts`` -> word index)
    order: list[int] = field(default_factory=list)

    @classmethod
    def build(cls, words: list[Word]) -> "PageText":
        """Join words with spaces and lines with newlines (reading order)."""
        # Group words by line while keeping the order of first appearance.
        lines: dict[int, list[int]] = {}
        for idx, word in enumerate(words):
            lines.setdefault(word.line_id, []).append(idx)

        parts: list[str] = []
        starts: list[int] = []
        ends: list[int] = []
        order: list[int] = []
        pos = 0
        for line_no, indices in enumerate(lines.values()):
            if line_no:
                parts.append("\n")
                pos += 1
            for n, idx in enumerate(indices):
                if n:
                    parts.append(" ")
                    pos += 1
                text = words[idx].text
                starts.append(pos)
                parts.append(text)
                pos += len(text)
                ends.append(pos)
                order.append(idx)
        return cls("".join(parts), starts, ends, order)

    def words_for_span(self, start: int, end: int) -> list[int]:
        """Indices of all words overlapping the character span ``[start, end)``."""
        if end <= start:
            return []
        result = []
        pos = max(bisect_right(self.starts, start) - 1, 0)
        while pos < len(self.starts) and self.starts[pos] < end:
            if self.ends[pos] > start:
                result.append(self.order[pos])
            pos += 1
        return result

    def word_span(self, word_index: int) -> tuple[int, int]:
        """Character span of a word (inverse of :meth:`words_for_span`)."""
        pos = self.order.index(word_index)
        return self.starts[pos], self.ends[pos]

    def position_map(self) -> dict[int, int]:
        """Word index -> position in reading order (for fast lookups)."""
        return {idx: pos for pos, idx in enumerate(self.order)}
