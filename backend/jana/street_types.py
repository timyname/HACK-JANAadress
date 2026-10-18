"""Shared canonical street-type vocabulary for build and runtime lookup."""

from __future__ import annotations

import re

from .normalization import normalize_for_match


_TRAILING_PUNCTUATION = re.compile(r"[.]+$")

_ALIASES: dict[str, tuple[str, ...]] = {
    "улица": ("улица", "ул", "көше", "көшесі"),
    "проспект": (
        "проспект",
        "просп",
        "пр",
        "пр-т",
        "даңғыл",
        "даңғылы",
        "данғыл",
        "данғылы",
    ),
    "переулок": ("переулок", "пер"),
    "проезд": ("проезд",),
    "шоссе": ("шоссе", "шоссесі", "тас жолы"),
    "набережная": ("набережная", "наб", "жағалау", "жағалауы"),
    "бульвар": ("бульвар", "б-р", "бульвары"),
    "аллея": ("аллея",),
    "тупик": ("тупик", "тұйық", "тұйығы", "тұйық көшесі"),
    "площадь": ("площадь", "пл", "алаң", "алаңы"),
    "микрорайон": (
        "микрорайон",
        "мкр",
        "мкр-н",
        "шағын аудан",
        "шағын ауданы",
        "ықшам аудан",
        "ықшам ауданы",
    ),
    "квартал": ("квартал", "кв-л", "орам", "орамы"),
}


def _key(value: str) -> str:
    return _TRAILING_PUNCTUATION.sub("", normalize_for_match(value).strip())


_CANONICAL_BY_ALIAS = {
    _key(alias): canonical
    for canonical, aliases in _ALIASES.items()
    for alias in aliases
}


def canonical_street_type(value: str | None) -> str | None:
    """Return a shared canonical role, preserving unknown non-empty values."""

    if value is None:
        return None
    normalized = _key(value)
    if not normalized:
        return None
    return _CANONICAL_BY_ALIAS.get(normalized, normalized)
