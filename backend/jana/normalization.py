"""Reversible matching normalization for address parsing.

The parser never publishes normalized text.  ``NormalizedText`` only exists to
support matching while preserving offsets into the decoded Python string.
"""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata


_PUNCTUATION_TRANSLATION = {
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "―": "-",
    "’": "'",
    "ʻ": "'",
    "ʼ": "'",
    "`": "'",
}


@dataclass(frozen=True, slots=True)
class NormalizedText:
    """A matching string plus a code-point map back to ``original``."""

    original: str
    text: str
    source_indices: tuple[int, ...]

    def source_span(self, start: int, end: int) -> tuple[int, int]:
        """Translate a half-open normalized span to a source code-point span."""

        if start < 0 or end < start or end > len(self.text):
            raise ValueError("normalized span is out of bounds")
        if start == end:
            if start == len(self.source_indices):
                return len(self.original), len(self.original)
            point = self.source_indices[start]
            return point, point
        return self.source_indices[start], self.source_indices[end - 1] + 1


def normalize_with_spans(value: str) -> NormalizedText:
    """Case-fold and collapse spacing without losing source code-point offsets."""

    if not isinstance(value, str):
        raise TypeError("value must be a string")

    characters: list[str] = []
    source_indices: list[int] = []
    pending_space_index: int | None = None

    for source_index, source_character in enumerate(value):
        expanded = unicodedata.normalize("NFKC", source_character).casefold()
        for character in expanded:
            character = _PUNCTUATION_TRANSLATION.get(character, character)
            if character.isspace():
                if characters:
                    pending_space_index = source_index
                continue
            if pending_space_index is not None:
                characters.append(" ")
                source_indices.append(pending_space_index)
                pending_space_index = None
            characters.append(character)
            source_indices.append(source_index)

    return NormalizedText(value, "".join(characters), tuple(source_indices))


def normalize_for_match(value: str) -> str:
    """Return the matching representation when source offsets are not needed."""

    return normalize_with_spans(value).text
