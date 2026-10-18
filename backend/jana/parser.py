"""Deterministic, source-span-preserving Kazakhstan address parser.

The runtime parser is deliberately conservative: explicit text wins, inferred
values are bounded, and the input raw_address is never normalized in the
public result. Matching normalization lives in normalization.py.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping

from .contracts import PUBLIC_FIELDS
from .normalization import normalize_for_match, normalize_with_spans
from .street_types import canonical_street_type

_PARSED_FIELDS = PUBLIC_FIELDS[2:-1]
_LETTERS = "A-Za-zÀ-ÖØ-öø-ÿĀ-ſА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі"
_NAME = rf"[{_LETTERS}][{_LETTERS}'’\- ]*?"
_NUMBER_ROLE_START = (
    r"(?:кв|пәт|подъезд|подьезд|под|кіреберіс|этаж|эт|қабат|flat|"
    r"dom|ui|uy|üi|üy|kvartira|kv|p[aäá]ter|pod[’']?yezd|podezd|kireberis|etazh|qabat|kabat)"
)
_NUMBER_PART = (
    rf"[0-9]+(?![0-9])"
    rf"(?:(?!{_NUMBER_ROLE_START})[{_LETTERS}])?"
)
_NUMBER = rf"{_NUMBER_PART}(?:\s*[/\-–—]\s*{_NUMBER_PART})*"


_CITY_ALIASES: dict[str, tuple[str, ...]] = {
    "Астана": ("астана", "astana", "нұр-сұлтан", "нур-султан", "nur-sultan"),
    "Алматы": ("алматы", "almaty", "алма-ата", "alma-ata"),
    "Шымкент": ("шымкент", "shymkent"),
    "Караганда": ("караганда", "қарағанды", "karaganda"),
    "Костанай": ("костанай", "қостанай", "kostanay"),
    "Актобе": ("актобе", "ақтөбе", "aktobe"),
    "Атырау": ("атырау", "atyrau"),
    "Актау": ("актау", "ақтау", "aktau"),
    "Павлодар": ("павлодар", "pavlodar"),
    "Семей": ("семей", "semei", "semey"),
    "Тараз": ("тараз", "taraz"),
    "Кокшетау": ("кокшетау", "көкшетау", "kokshetau"),
    "Петропавловск": ("петропавловск", "петропавл", "petropavl"),
    "Талдыкорган": ("талдыкорган", "талдықорған", "taldykorgan"),
    "Туркестан": ("туркестан", "түркістан", "turkistan"),
    "Уральск": ("уральск", "орал", "oral"),
    "Усть-Каменогорск": ("усть-каменогорск", "өскемен", "оскемен", "oskemen"),
    "Кызылорда": ("кызылорда", "қызылорда", "kyzylorda"),
    "Жезказган": ("жезказган", "жезқазған", "zhezkazgan"),
    "Конаев": ("конаев", "қонаев", "qonaev"),
    "Сарань": ("сарань",),
}

_PROVINCE_ALIASES: dict[str, tuple[str, ...]] = {
    "Акмолинская область": ("акмолинская область", "ақмола облысы", "ақмола"),
    "Актюбинская область": ("актюбинская область", "ақтөбе облысы", "ақтөбе"),
    "Алматинская область": ("алматинская область", "алматы облысы"),
    "Атырауская область": ("атырауская область", "атырау облысы"),
    "Восточно-Казахстанская область": (
        "восточно-казахстанская область",
        "шығыс қазақстан облысы",
        "шығыс қазақстан",
    ),
    "Жамбылская область": ("жамбылская область", "жамбыл облысы"),
    "Западно-Казахстанская область": ("западно-казахстанская область", "батыс қазақстан облысы"),
    "Карагандинская область": (
        "карагандинская область",
        "қарағанды облысы",
        "қарағанды",
    ),
    "Костанайская область": ("костанайская область", "қостанай облысы"),
    "Кызылординская область": ("кызылординская область", "қызылорда облысы"),
    "Мангистауская область": ("мангистауская область", "маңғыстау облысы"),
    "Павлодарская область": ("павлодарская область", "павлодар облысы"),
    "Северо-Казахстанская область": ("северо-казахстанская область", "солтүстік қазақстан облысы"),
    "Туркестанская область": ("туркестанская область", "түркістан облысы"),
    "Область Абай": ("область абай", "абай облысы"),
    "Область Жетысу": ("область жетысу", "жетісу облысы"),
    "Область Улытау": ("область улытау", "ұлытау облысы"),
}

_REPUBLIC_CITIES = {"Астана", "Алматы", "Шымкент"}

_DISTRICT_ALIASES: dict[str, tuple[str, ...]] = {
    "Алмалинский": ("алмалинский", "алмалы"),
    "Ауэзовский": ("ауэзовский", "әуезов", "аузовский"),
    "Бостандыкский": ("бостандыкский", "бостандық"),
    "Медеуский": ("медеуский", "медеу"),
    "Сарыаркинский": ("сарыаркинский", "сарыарқа", "сарыарка"),
    "Есильский": ("есильский", "есіл", "есиль"),
    "Абайский": ("абайский", "абай"),
    "Алматинский": ("алматинский", "алматы"),
    "Казыбекбийский": ("казыбекбийский", "қазыбек би", "казыбек би"),
    "Ульбинский": ("ульбинский", "үлбі", "ульба"),
}

_NUMBER_MARKERS: dict[str, tuple[str, ...]] = {
    "house": (r"дом", r"д\.?", r"үй", r"ғимарат", r"dom", r"ui", r"uy", r"üi", r"üy"),
    "apartment": (r"квартира", r"кв\.?", r"пәтер", r"пәт\.?", r"flat", r"kvartira", r"kv\.?", r"p[aäá]ter"),
    "entrance": (r"подъезд", r"подьезд", r"под\.?", r"кіреберіс", r"pod[’']?yezd", r"podezd", r"kireberis"),
    "floor": (r"этаж", r"эт\.?", r"қабат", r"etazh", r"qabat", r"kabat"),
}

_STREET_TYPES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "улица",
        (
            r"[уy]л(?:ица)?\.?", r"[кk]-сі", r"көше(?:сі)?",
            r"(?:ulitsa|ul)(?=\s|[.:-])\.?",
            r"k[oöó](?:sh|ş)e(?:si)?(?=\s|[.:-])\.?",
        ),
        (r"көшесі", r"[кk]-сі", r"k[oöó](?:sh|ş)esi"),
    ),
    (
        "проспект",
        (r"пр-т", r"пр(?:оспект)?\.?", r"д-лы", r"даңғыл(?:ы)?"),
        (r"даңғылы", r"д-лы"),
    ),
    ("переулок", (r"пер(?:еулок)?\.?",), ()),
    ("проезд", (r"проезд",), ()),
    ("шоссе", (r"шоссе",), (r"шоссесі",)),
    ("бульвар", (r"бульвар", r"б-р"), (r"бульвары",)),
    ("набережная", (r"набережная", r"наб\.?", r"жағалау"), (r"жағалауы",)),
    ("аллея", (r"аллея",), ()),
    ("тупик", (r"тупик", r"тұйық"), (r"тұйығы", r"тұйық\s+көшесі")),
    ("площадь", (r"площадь", r"пл\.?", r"алаң"), (r"алаңы",)),
)

_AREA_TYPES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "микрорайон",
        (
            r"микрорайон",
            r"(?:мkр|mkr|mkp)(?:-?[нn])?(?=\s|[.:-]|$)\.?",
            r"мкр-?н?\.?",
            r"ш-а",
            r"шағын\s+аудан",
            r"ықшам\s+аудан",
        ),
        (r"шағын\s+ауданы", r"ықшам\s+ауданы"),
    ),
    ("квартал", (r"квартал", r"кв-л", r"орам"), (r"орамы",)),
)

_FLAGS = re.IGNORECASE | re.UNICODE
_ALIASES_BY_KIND = {
    "city": _CITY_ALIASES,
    "province": _PROVINCE_ALIASES,
    "district": _DISTRICT_ALIASES,
}


def _build_alias_index(
    kind: str, aliases: Mapping[str, tuple[str, ...]]
) -> tuple[tuple[str, re.Pattern[str]], ...]:
    entries: list[tuple[str, re.Pattern[str]]] = []
    for canonical, variants in aliases.items():
        for variant in variants:
            needle = normalize_for_match(variant)
            # A bare region name is often identical to a city name. It remains
            # available for canonicalizing an explicit province component, but
            # is unsafe as an unmarked whole-address match.
            required_type_tokens = (
                ("область", "облысы")
                if kind == "province"
                else ("район", "аудан")
                if kind == "district"
                else ()
            )
            if required_type_tokens and not any(
                token in needle for token in required_type_tokens
            ):
                continue
            entries.append(
                (canonical, re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)"))
            )
    return tuple(entries)


_ALIAS_INDEX = {
    kind: _build_alias_index(kind, aliases)
    for kind, aliases in _ALIASES_BY_KIND.items()
}
_CANONICAL_SURFACES = {
    kind: {
        normalize_for_match(surface.strip(" .")): canonical
        for canonical, variants in aliases.items()
        for surface in (canonical, *variants)
    }
    for kind, aliases in _ALIASES_BY_KIND.items()
}

_ADMIN_MARKERS = {
    "city": (
        r"(?:город|г\.|қаласы|қала|қ\.|село|с\.|ауыл|а\.|(?:gorod|qalasy|qala)(?=\s|[.:-])[.:-]?)",
        r"(?:қаласы|қала|город|г\.|қ\.|село|ауылы|ауыл|gorod|qalasy|qala)",
    ),
    "province": (
        r"(?:область|обл\.|облысы|(?:oblast|oblysy)(?=\s|[.:-])[.:-]?)",
        r"(?:область|обл\.|облысы|oblast|oblysy)",
    ),
    "district": (
        r"(?:район|р-?н\.?|ауданы|аудан|ауд\.|(?:rayon|raion|audany|audan)(?=\s|[.:-])[.:-]?)",
        r"(?:район|р-?н\.?|ауданы|аудан|ауд\.|rayon|raion|audany|audan)",
    ),
}
_ADMIN_PATTERNS = {
    kind: (
        re.compile(
            rf"(?<![{_LETTERS}]){prefix}\s*(?P<value>{_NAME})(?=\s*[,;/]|\s{{2,}}|$)",
            _FLAGS,
        ),
        re.compile(
            rf"(?P<value>{_NAME})\s+{suffix}(?=\s*[,;/]|\s{{2,}}|$)",
            _FLAGS,
        ),
    )
    for kind, (prefix, suffix) in _ADMIN_MARKERS.items()
}

_ALL_NUMBER_MARKERS = "(?:" + "|".join(
    marker for markers in _NUMBER_MARKERS.values() for marker in markers
) + ")"


def _compile_number_patterns(
    field: str, markers: tuple[str, ...]
) -> tuple[re.Pattern[str], re.Pattern[str]]:
    marker = "(?:" + "|".join(markers) + ")"
    number = rf"-?{_NUMBER}" if field == "floor" else _NUMBER
    separator = r"[:№#]?" if field == "floor" else r"[:№#-]?"
    return (
        re.compile(
            rf"(?<![{_LETTERS}]){marker}\s*{separator}\s*(?P<value>{number})",
            _FLAGS,
        ),
        re.compile(
            rf"(?P<value>{number})[ \t]?[-–—]?[ \t]?{marker}(?![{_LETTERS}])",
            _FLAGS,
        ),
    )


_NUMBER_PATTERNS = {
    field: _compile_number_patterns(field, markers)
    for field, markers in _NUMBER_MARKERS.items()
}
_DETAIL_BOUNDARY_PATTERNS = tuple(
    pattern for patterns in _NUMBER_PATTERNS.values() for pattern in patterns
)
_BARE_NUMBER_COMPONENT = re.compile(rf"(?P<value>{_NUMBER})", _FLAGS)
_NUMBERED_NAME_SUFFIX = re.compile(
    rf"(?:"
    rf"(?:^|\s|[-\u2010\u2011\u2012\u2013\u2014])\s*(?:[№#]\s*)?{_NUMBER}"
    rf"|(?:^|\s)\d+\s*[-\u2010\u2011\u2012\u2013\u2014]?\s*(?:й|я|е|ші|шы)"
    rf")\s*$",
    _FLAGS,
)


def _compile_typed_patterns(
    types: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]
) -> tuple[tuple[str, re.Pattern[str], re.Pattern[str]], ...]:
    compiled = []
    for canonical, prefixes, suffixes in types:
        prefix_marker = "(?:" + "|".join(prefixes) + ")" if prefixes else r"(?!)"
        suffix_marker = "(?:" + "|".join(suffixes) + ")" if suffixes else r"(?!)"
        compiled.append(
            (
                canonical,
                re.compile(
                    rf"^\s*(?P<marker>{prefix_marker})\s*[:.-]?\s*(?P<name>.+?)\s*$",
                    _FLAGS,
                ),
                re.compile(
                    rf"^\s*(?P<name>.+?)\s+(?P<marker>{suffix_marker})\.?\s*$",
                    _FLAGS,
                ),
            )
        )
    return tuple(compiled)


_COMPILED_STREET_TYPES = _compile_typed_patterns(_STREET_TYPES)
_COMPILED_AREA_TYPES = _compile_typed_patterns(_AREA_TYPES)
_RESIDENTIAL_MARKER_SOURCE = r"(?:жк|zhk|жилой\s+комплекс|тұрғын\s+үй\s+кешені)"
_RESIDENTIAL_MARKER = re.compile(
    rf"(?<!\w){_RESIDENTIAL_MARKER_SOURCE}(?!\w)", _FLAGS
)
_COMPONENT_MARKER_SOURCE = "(?:" + "|".join(
    [
        *(marker for _, prefixes, _ in _STREET_TYPES for marker in prefixes),
        *(marker for _, prefixes, _ in _AREA_TYPES for marker in prefixes),
        *(marker for markers in _NUMBER_MARKERS.values() for marker in markers),
        *(markers[0] for markers in _ADMIN_MARKERS.values()),
        r"ориентир",
        r"бағдар(?:ы)?",
        _RESIDENTIAL_MARKER_SOURCE,
    ]
) + ")"
_COMPONENT_MARKER_AT = re.compile(_COMPONENT_MARKER_SOURCE, _FLAGS)
# A word marker cannot cut a name such as Престиж. Dotted street markers
# (ул.Тест) and numeric roles (дом17) still delimit an unquoted complex.
_RESIDENTIAL_BOUNDARY = re.compile(
    rf"[,;\n]|(?<!\w){_COMPONENT_MARKER_SOURCE}(?:(?<=\.)|(?=$|[\W\d]))", _FLAGS
)


def _compile_marked_alias_patterns() -> dict[str, re.Pattern[str]]:
    patterns: dict[str, re.Pattern[str]] = {}
    for kind, aliases in _ALIASES_BY_KIND.items():
        variants = sorted(
            {
                surface
                for canonical, names in aliases.items()
                for surface in (canonical, *names)
            },
            key=len,
            reverse=True,
        )
        prefix = _ADMIN_MARKERS[kind][0]
        alternation = "(?:" + "|".join(re.escape(value) for value in variants) + ")"
        patterns[kind] = re.compile(
            rf"(?<![{_LETTERS}]){prefix}\s*(?P<value>{alternation})", _FLAGS
        )
    return patterns


_MARKED_ALIAS_PATTERNS = _compile_marked_alias_patterns()
_LANDMARK_EXPLICIT = re.compile(
    r"(?<!\w)(?:ориентир|бағдар(?:ы)?|рядом\s+с|напротив|около|возле)\s*[:\-]?\s*(?P<value>[^,;]+)",
    _FLAGS,
)
_LANDMARK_NOUN = re.compile(
    r"(?<!\w)(?P<value>(?:магазин|школа|бизнес[- ]?центр|бц|тц|кафе|мешіт|дүкен)\s+[^,;]+)",
    _FLAGS,
)


@dataclass(frozen=True, slots=True)
class _Candidate:
    value: str
    span: tuple[int, int]
    source: str = "text"
    mask_span: tuple[int, int] | None = None


def _trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and (text[end - 1].isspace() or text[end - 1] in ".:"):
        end -= 1
    return start, end


def _mask_spans(text: str, spans: Iterable[tuple[int, int] | None]) -> str:
    """Blank protected source spans without changing Unicode offsets."""

    chars = list(text)
    for span in spans:
        if span is None:
            continue
        start, end = span
        for index in range(max(0, start), min(len(chars), end)):
            chars[index] = " "
    return "".join(chars)


def _extract_residential_complexes(
    text: str,
    excluded_spans: Iterable[tuple[int, int] | None] = (),
    *,
    empty_marker_spans: list[tuple[int, int]] | None = None,
) -> list[_Candidate]:
    """Read names from source spans, with one quote/boundary scan per address."""

    if _RESIDENTIAL_MARKER.search(text) is None:
        return []

    quote_pairs = {"«": "»", '"': '"', "“": "”"}
    quote_spans: list[tuple[int, int]] = []
    closed_quotes: dict[int, int] = {}
    opening: int | None = None
    closing = ""
    for index, char in enumerate(text):
        if opening is not None:
            if char == closing:
                quote_spans.append((opening, index + 1))
                closed_quotes[opening] = index
                opening = None
        elif char in quote_pairs:
            opening = index
            closing = quote_pairs[char]
    if opening is not None:
        # An unmatched opener supplies no name, but markers inside it are
        # still quoted text rather than independently typed complexes.
        quote_spans.append((opening, len(text)))

    outside_quotes = _mask_spans(text, quote_spans)
    landmark_spans: list[tuple[int, int]] = []
    for match in _LANDMARK_EXPLICIT.finditer(outside_quotes):
        start, end = _trim_span(outside_quotes, *match.span("value"))
        boundary = _next_detail_boundary(outside_quotes, start, end)
        if boundary is not None and boundary > start:
            _, end = _trim_span(outside_quotes, start, boundary)
        landmark_spans.append((match.start(), end))
    # Preserve the existing noun fallback without treating every noun in a
    # complex name as a separately introduced landmark.
    outside_landmark = _extract_landmark(outside_quotes) if not landmark_spans else None
    quote_starts = [start for start, _ in quote_spans]
    exclusions: set[tuple[int, int]] = set()
    for span in (
        *excluded_spans,
        *landmark_spans,
        outside_landmark.mask_span if outside_landmark else None,
    ):
        if span is None:
            continue
        start, end = max(0, span[0]), min(len(text), span[1])
        quote_index = bisect_left(quote_starts, start + 1) - 1
        if start >= end or (quote_index >= 0 and start < quote_spans[quote_index][1]):
            # A landmark-like phrase inside a quoted name cannot exclude a
            # later complex outside those quotes.
            continue
        exclusions.add((start, end))

    marker_text = _mask_spans(outside_quotes, exclusions)
    boundaries = sorted({
        *(match.start() for match in _RESIDENTIAL_BOUNDARY.finditer(outside_quotes)),
        *(start for start, _ in exclusions),
    })
    candidates: list[_Candidate] = []
    for marker in _RESIDENTIAL_MARKER.finditer(marker_text):
        start = marker.end()
        while start < len(text) and (text[start].isspace() or text[start] in ".:"):
            start += 1
        if start >= len(text):
            continue
        if text[start] in quote_pairs:
            close = closed_quotes.get(start)
            if close is None:
                continue
            mask_end = close + 1
            start, end = start + 1, close
        else:
            boundary_index = bisect_left(boundaries, start)
            end = boundaries[boundary_index] if boundary_index < len(boundaries) else len(text)
            mask_end = end
            if start == end and empty_marker_spans is not None:
                # A nameless prefix before a typed component is not a complex.
                # Blank only that prefix so the following street stays usable.
                empty_marker_spans.append((marker.start(), start))
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end and any(char.isalnum() for char in text[start:end]):
            candidates.append(_Candidate(
                text[start:end], (start, end), mask_span=(marker.start(), mask_end)
            ))
    return candidates


def _clean_number(value: str) -> str:
    return re.sub(r"\s+", "", value, flags=re.UNICODE)


def _public_district_name(value: str) -> str:
    """Remove the Russian boundary type while preserving the district name."""

    name = re.sub(r"^\s*район\b[\s,.:;-]*", "", value, flags=_FLAGS)
    name = re.sub(r"[\s,.:;-]*\bрайон\s*$", "", name, flags=_FLAGS)
    return name.strip()


def _parent_value_key(field: str, value: str) -> str:
    """Normalize parent values while ignoring district boundary markers."""

    key = normalize_for_match(value)
    if field != "district":
        return key
    key = re.sub(r"^(?:район|р\s+н|аудан|ауданы|ауд)\s+", "", key)
    key = re.sub(r"\s+(?:район|р\s+н|аудан|ауданы|ауд)$", "", key)
    return key.strip()


def _segments(text: str) -> Iterable[tuple[int, int]]:
    start = 0
    for match in re.finditer(r"[,;\n]|\s{2,}", text):
        left, right = _trim_span(text, start, match.start())
        if left < right:
            yield left, right
        start = match.end()
    left, right = _trim_span(text, start, len(text))
    if left < right:
        yield left, right


def _find_number(text: str, field: str) -> _Candidate | None:
    prefix, suffix = _NUMBER_PATTERNS[field]
    prefix_match = prefix.search(text)
    suffix_match = suffix.search(text)
    match = prefix_match
    if prefix_match is not None and suffix_match is not None:
        following = text[prefix_match.end("value") :]
        before_suffix_value = text[: suffix_match.start("value")]
        suffix_value_has_prior_role = re.search(
            rf"{_ALL_NUMBER_MARKERS}\s*[:№#-]?\s*$",
            before_suffix_value,
            _FLAGS,
        )
        # In "2 подъезд 5 этаж", the 5 belongs to этаж; prefer the suffix
        # reading for подъезд. In "дом 7 квартира 3", квартира 3 remains
        # the stronger prefix reading because 3 has no following role marker.
        if suffix_value_has_prior_role is None and re.match(
            rf"\s*[-–—]?\s*{_ALL_NUMBER_MARKERS}(?![{_LETTERS}])",
            following,
            _FLAGS,
        ):
            match = suffix_match
    if match is None:
        match = suffix_match
    if match is None:
        return None
    start, end = match.span("value")
    start, end = _trim_span(text, start, end)
    return _Candidate(_clean_number(text[start:end]), (start, end))


def _next_detail_boundary(
    text: str,
    start: int,
    end: int,
    *,
    include_landmark: bool = False,
    landmark_exclusions: Iterable[tuple[int, int]] = (),
) -> int | None:
    """Return the first explicit detail/landmark marker inside a source span."""

    fragment = text[start:end]
    offsets: list[int] = []
    for pattern in _DETAIL_BOUNDARY_PATTERNS:
        match = pattern.search(fragment)
        if match:
            offsets.append(match.start())
    if include_landmark:
        exclusions = tuple(landmark_exclusions)
        landmarks = re.finditer(
            r"(?<!\w)(?:ориентир|бағдар(?:ы)?|рядом\s+с|напротив|около|возле)\b",
            fragment,
            re.IGNORECASE | re.UNICODE,
        )
        for landmark in landmarks:
            absolute_start = start + landmark.start()
            if any(left <= absolute_start < right for left, right in exclusions):
                continue
            offsets.append(landmark.start())
            break
    return start + min(offsets) if offsets else None


def _find_alias(
    text: str,
    normalized: Any,
    kind: str,
) -> tuple[str, tuple[int, int]] | None:
    best: tuple[int, int, str] | None = None
    for canonical, pattern in _ALIAS_INDEX[kind]:
        match = pattern.search(normalized.text)
        if not match:
            continue
        source_start, source_end = normalized.source_span(match.start(), match.end())
        # Unmarked aliases are accepted only when they occupy a complete
        # component. This prevents a street named Алматы or Абай from silently
        # becoming administrative metadata.
        component_start = 0
        component_end = len(text)
        for boundary in re.finditer(r"[,;\n]|\s{2,}", text):
            if boundary.end() <= source_start:
                component_start = boundary.end()
            elif boundary.start() >= source_end:
                component_end = boundary.start()
                break
        left, right = _trim_span(text, component_start, component_end)
        occupies_component = left == source_start and right == source_end
        begins_component_before_typed_data = (
            kind == "city"
            and left == source_start
            and re.match(
                rf"\s+{_COMPONENT_MARKER_SOURCE}",
                text[source_end:component_end],
                _FLAGS,
            )
            is not None
        )
        if not occupies_component and not begins_component_before_typed_data:
            continue
        candidate = (match.start(), -(match.end() - match.start()), canonical)
        if best is None or candidate < best:
            best = candidate
    if best is None:
        return None
    start = best[0]
    length = -best[1]
    return best[2], normalized.source_span(start, start + length)


def _find_marked_alias(text: str, kind: str) -> _Candidate | None:
    """Resolve a known alias immediately following an explicit admin marker."""

    pattern = _MARKED_ALIAS_PATTERNS[kind]
    for match in pattern.finditer(text):
        end = match.end("value")
        if end < len(text) and text[end].isalnum():
            component = _COMPONENT_MARKER_AT.match(text, end)
            if component is None:
                continue
        start, end = match.span("value")
        canonical = (
            text[start:end].strip(" .")
            if kind == "district"
            else _canonical_from_surface(text[start:end], kind)
        )
        return _Candidate(canonical, (start, end))
    return None


def _canonical_from_surface(value: str, kind: str) -> str:
    normalized = normalize_for_match(value.strip(" ."))
    return _CANONICAL_SURFACES[kind].get(normalized, value.strip(" ."))


def _find_generic_admin(text: str, kind: str) -> _Candidate | None:
    marked_alias = _find_marked_alias(text, kind)
    if marked_alias is not None:
        return marked_alias
    patterns = _ADMIN_PATTERNS[kind]
    matches = [match for pattern in patterns if (match := pattern.search(text))]
    if not matches:
        return None
    match = min(matches, key=lambda item: item.start())
    start, end = _trim_span(text, *match.span("value"))
    value = text[start:end]
    canonical = value if kind == "district" else _canonical_from_surface(value, kind)
    return _Candidate(canonical, (start, end))


def _extract_admin(text: str, normalized: Any, kind: str) -> _Candidate | None:
    alias = _find_alias(text, normalized, kind)
    generic = _find_generic_admin(text, kind)
    if generic is not None and alias is not None:
        alias_start, alias_end = alias[1]
        generic_start, generic_end = generic.span
        if alias_start < generic_end and generic_start < alias_end:
            return _Candidate(alias[0], alias[1])
        return generic
    if alias is not None:
        return _Candidate(alias[0], alias[1])
    return generic


def _split_trailing_house(
    text: str,
    start: int,
    end: int,
) -> tuple[tuple[int, int], _Candidate | None, bool]:
    fragment = text[start:end]
    match = re.search(rf"\s+(?P<value>{_NUMBER})\s*$", fragment, re.IGNORECASE | re.UNICODE)
    if not match:
        return _trim_span(text, start, end), None, False
    house_start = start + match.start("value")
    house_end = start + match.end("value")
    name_span = _trim_span(text, start, start + match.start())
    if name_span[0] >= name_span[1]:
        return _trim_span(text, start, end), None, False
    ambiguous = bool(re.search(r"\d\s*$", text[name_span[0] : name_span[1]]))
    return (
        name_span,
        _Candidate(
            _clean_number(text[house_start:house_end]),
            (house_start, house_end),
            "heuristic",
        ),
        ambiguous,
    )


def _find_following_bare_house(
    text: str, component_span: tuple[int, int]
) -> tuple[_Candidate | None, bool]:
    """Read consecutive numeric components immediately after a typed component."""

    segments = list(_segments(text))
    component_index = next(
        (
            index
            for index, (start, end) in enumerate(segments)
            if start <= component_span[0] and component_span[1] <= end
        ),
        None,
    )
    if component_index is None:
        return None, False
    candidates: list[_Candidate] = []
    for start, end in segments[component_index + 1 :]:
        match = _BARE_NUMBER_COMPONENT.fullmatch(text[start:end])
        if match is None:
            break
        value_start = start + match.start("value")
        value_end = start + match.end("value")
        # Kazakhstan's legacy postal index is six digits. A standalone index
        # next to a microdistrict must not silently become a house number.
        if re.fullmatch(r"\d{6}", text[value_start:value_end].strip()):
            continue
        candidates.append(
            _Candidate(
                _clean_number(text[value_start:value_end]),
                (value_start, value_end),
                "heuristic",
            )
        )
    if len(candidates) == 1:
        return candidates[0], False
    return None, len(candidates) > 1


def _extract_unmarked_street_house(
    text: str,
    *,
    start: int,
    house_already_known: bool,
) -> tuple[_Candidate, _Candidate | None] | None:
    """Read ``street name + house`` after a confirmed city or region.

    The gazetteer must confirm the street before the parser returns it.
    """

    following_marker = _COMPONENT_MARKER_AT.search(text, start)
    end = following_marker.start() if following_marker is not None else len(text)
    start, end = _trim_span(text, start, end)
    while start < end and text[start] in ",;:/":
        start += 1
        start, end = _trim_span(text, start, end)
    if start >= end:
        return None

    if house_already_known:
        surface = text[start:end]
        if not re.fullmatch(rf"[{_LETTERS}][{_LETTERS}'’\- ]{{0,120}}", surface):
            return None
        return _Candidate(surface, (start, end)), None

    fragment = text[start:end]
    match = re.fullmatch(
        rf"(?P<name>[{_LETTERS}][{_LETTERS}'’\- ]{{0,120}}?)\s+"
        rf"(?P<house>{_NUMBER})",
        fragment,
        _FLAGS,
    )
    if match is None:
        return None
    name_start = start + match.start("name")
    name_end = start + match.end("name")
    house_start = start + match.start("house")
    house_end = start + match.end("house")
    name_start, name_end = _trim_span(text, name_start, name_end)
    return (
        _Candidate(text[name_start:name_end], (name_start, name_end)),
        _Candidate(
            _clean_number(text[house_start:house_end]),
            (house_start, house_end),
            "heuristic",
        ),
    )


def _extract_typed_segment(
    text: str,
    types: tuple[tuple[str, re.Pattern[str], re.Pattern[str]], ...],
    infer_house: bool = False,
    starts: Iterable[int] = (),
    *,
    include_landmark_boundary: bool = True,
    landmark_exclusions: Iterable[tuple[int, int]] = (),
) -> tuple[str, tuple[int, int], _Candidate, _Candidate | None, bool] | None:
    candidate_segments = list(_segments(text))
    for offset in starts:
        while offset < len(text) and (text[offset].isspace() or text[offset] in ",;/"):
            offset += 1
        if offset < len(text):
            candidate_segments.append((offset, len(text)))

    seen_segments: set[tuple[int, int]] = set()
    for segment_start, segment_end in candidate_segments:
        if (segment_start, segment_end) in seen_segments:
            continue
        seen_segments.add((segment_start, segment_end))
        segment = text[segment_start:segment_end]
        for canonical_type, prefix, suffix in types:
            match = prefix.match(segment) or suffix.match(segment)
            if not match:
                continue
            name_start = segment_start + match.start("name")
            name_end = segment_start + match.end("name")
            marker_span = (
                segment_start + match.start("marker"),
                segment_start + match.end("marker"),
            )
            name_start, name_end = _trim_span(text, name_start, name_end)
            boundary = _next_detail_boundary(
                text,
                name_start,
                name_end,
                include_landmark=include_landmark_boundary,
                landmark_exclusions=landmark_exclusions,
            )
            if boundary is not None:
                name_start, name_end = _trim_span(text, name_start, boundary)
            inferred_house = None
            ambiguous_house = False
            if infer_house:
                (
                    (name_start, name_end),
                    inferred_house,
                    ambiguous_house,
                ) = _split_trailing_house(text, name_start, name_end)
            if name_start < name_end:
                return (
                    canonical_street_type(canonical_type) or canonical_type,
                    marker_span,
                    _Candidate(text[name_start:name_end], (name_start, name_end)),
                    inferred_house,
                    ambiguous_house,
                )
    return None


def _extract_landmark(
    text: str,
    excluded_spans: Iterable[tuple[int, int] | None] = (),
) -> _Candidate | None:
    exclusions = tuple(span for span in excluded_spans if span is not None)

    def is_excluded(position: int) -> bool:
        return any(start <= position < end for start, end in exclusions)

    for explicit in _LANDMARK_EXPLICIT.finditer(text):
        if is_excluded(explicit.start()):
            continue
        start, end = _trim_span(text, *explicit.span("value"))
        boundary = _next_detail_boundary(text, start, end)
        if boundary is not None and boundary > start:
            start, end = _trim_span(text, start, boundary)
        return _Candidate(
            text[start:end],
            (start, end),
            mask_span=(explicit.start(), end),
        )

    for landmark_noun in _LANDMARK_NOUN.finditer(text):
        if is_excluded(landmark_noun.start()):
            continue
        start, end = _trim_span(text, *landmark_noun.span("value"))
        boundary = _next_detail_boundary(text, start, end)
        if boundary is not None and boundary > start:
            start, end = _trim_span(text, start, boundary)
        return _Candidate(
            text[start:end],
            (start, end),
            mask_span=(landmark_noun.start(), end),
        )
    return None


def _coerce_gazetteer_field(
    value: Any, default_source: str
) -> tuple[Any, dict[str, Any]]:
    if isinstance(value, Mapping):
        metadata = dict(value)
        field_value = metadata.pop("value", None)
        metadata.setdefault("source", default_source)
        metadata.setdefault("span", None)
        return field_value, metadata
    return value, {"source": default_source, "span": None}


def _validate_record(record: Any, index: int | None = None) -> None:
    label = "record" if index is None else f"record[{index}]"
    if not isinstance(record, Mapping):
        raise ValueError(f"{label} must be an object")
    if not isinstance(record.get("id"), str):
        raise ValueError(f"{label}.id must be a string")
    if not isinstance(record.get("raw_address"), str):
        raise ValueError(f"{label}.raw_address must be a string")


def _confidence(
    values: Mapping[str, Any],
    metadata: Mapping[str, Mapping[str, Any]],
    warnings: list[str],
) -> float:
    del metadata  # Provenance explains a value; it does not reduce completeness.
    weights = {
        "province": 0.08,
        "city": 0.16,
        "district": 0.04,
        "living_area": 0.05,
        "street_type": 0.08,
        "street_name": 0.22,
        "house": 0.19,
        "apartment": 0.06,
        "entrance": 0.03,
        "floor": 0.03,
        "landmark": 0.03,
    }
    score = 0.08
    for field, weight in weights.items():
        if values.get(field) is None:
            continue
        score += weight
    score -= 0.1 * len(warnings)
    return round(max(0.0, min(0.96, score)), 4)


def parse_record(
    record: dict[str, Any],
    gazetteer: Any = None,
    explain: bool = False,
) -> dict[str, Any]:
    """Parse one valid record, optionally returning field provenance."""

    _validate_record(record)
    raw = record["raw_address"]
    values: dict[str, Any] = {field: None for field in _PARSED_FIELDS}
    metadata: dict[str, dict[str, Any]] = {
        field: {"source": "unknown", "span": None} for field in _PARSED_FIELDS
    }
    warnings: list[str] = []
    typed_name_spans: list[tuple[int, int]] = []
    for types in (_COMPILED_STREET_TYPES, _COMPILED_AREA_TYPES):
        typed = _extract_typed_segment(
            raw,
            types,
            include_landmark_boundary=False,
        )
        if typed is not None:
            typed_name_spans.append(typed[2].span)
    landmark_probe = _extract_landmark(raw, typed_name_spans)
    empty_complex_marker_spans: list[tuple[int, int]] = []
    complexes = _extract_residential_complexes(
        raw,
        excluded_spans=[landmark_probe.mask_span] if landmark_probe else [],
        empty_marker_spans=empty_complex_marker_spans,
    )
    complex_spans = [*(candidate.mask_span for candidate in complexes), *empty_complex_marker_spans]
    parsing_text = (
        _mask_spans(raw, complex_spans) if complex_spans else raw
    )
    normalized = normalize_with_spans(parsing_text)

    def set_value(
        field: str,
        value: Any,
        source: str,
        span: tuple[int, int] | list[int] | None,
        *,
        overwrite: bool = False,
        **extra: Any,
    ) -> None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return
        if values[field] is not None and not overwrite:
            return
        if isinstance(value, str):
            value = value.strip()
            if field == "district":
                value = _public_district_name(value)
        values[field] = value
        metadata[field] = {
            "source": source,
            "span": list(span) if span is not None else None,
            **extra,
        }

    # Landmark text is masked before numeric extraction so phrases such as
    # "ориентир дом34" cannot become address components.
    landmark = (
        _extract_landmark(parsing_text, typed_name_spans)
        if complexes
        else landmark_probe
    )
    detail_text = parsing_text
    if landmark:
        set_value("landmark", landmark.value, landmark.source, landmark.span)
        if landmark.mask_span is not None:
            mask_start, mask_end = landmark.mask_span
            detail_text = (
                parsing_text[:mask_start] + (" " * (mask_end - mask_start)) + parsing_text[mask_end:]
            )

    # Explicit detail roles are peeled off before street/house inference.
    for field in _NUMBER_MARKERS:
        candidate = _find_number(detail_text, field)
        if candidate:
            set_value(field, candidate.value, candidate.source, candidate.span)

        prefix, suffix = _NUMBER_PATTERNS[field]
        prefix_values = {
            _clean_number(match.group("value")) for match in prefix.finditer(detail_text)
        }
        suffix_values = {
            _clean_number(match.group("value")) for match in suffix.finditer(detail_text)
        }
        explicit_values = prefix_values if prefix_values else suffix_values
        if len(explicit_values) > 1:
            warnings.append(f"conflicting_{field}_values")

    admin_ends: list[int] = []
    for kind in ("province", "city", "district"):
        candidate = _extract_admin(parsing_text, normalized, kind)
        if candidate:
            set_value(kind, candidate.value, candidate.source, candidate.span)
            admin_ends.append(candidate.span[1])

    street_probe = _extract_typed_segment(
        parsing_text,
        _COMPILED_STREET_TYPES,
        starts=admin_ends,
        landmark_exclusions=typed_name_spans,
        include_landmark_boundary=False,
    )
    area_probe = _extract_typed_segment(
        parsing_text,
        _COMPILED_AREA_TYPES,
        starts=admin_ends,
        landmark_exclusions=typed_name_spans,
    )
    ambiguous_bare_house = False
    numbered_area = bool(
        area_probe
        and street_probe is None
        and area_probe[0] == "микрорайон"
        and _NUMBERED_NAME_SUFFIX.search(area_probe[2].value)
    )
    if values["house"] is None and numbered_area:
        marker_span = area_probe[1]
        name_span = area_probe[2].span
        component_span = (
            min(marker_span[0], name_span[0]),
            max(marker_span[1], name_span[1]),
        )
        bare_house, ambiguous_bare_house = _find_following_bare_house(
            detail_text, component_span
        )
        if bare_house is not None:
            set_value("house", bare_house.value, bare_house.source, bare_house.span)
        elif ambiguous_bare_house:
            warnings.append("ambiguous_bare_house")

    infer_house = values["house"] is None and not numbered_area
    street = (
        street_probe
        if numbered_area
        else _extract_typed_segment(
            parsing_text,
            _COMPILED_STREET_TYPES,
            infer_house=infer_house,
            starts=admin_ends,
            landmark_exclusions=typed_name_spans,
        )
    )
    # When both an area and a street are present, the street owns bare house
    # inference. Keeping the non-inferential area probe preserves names such
    # as "Арман 2" while the street extracts its own trailing house number.
    area = (
        area_probe
        if numbered_area or street is not None
        else _extract_typed_segment(
            parsing_text,
            _COMPILED_AREA_TYPES,
            infer_house=infer_house,
            starts=admin_ends,
            landmark_exclusions=typed_name_spans,
        )
    )
    if street:
        (
            street_type,
            street_type_span,
            street_name,
            inferred_house,
            ambiguous_house,
        ) = street
        set_value("street_type", street_type, "text", street_type_span)
        set_value("street_name", street_name.value, street_name.source, street_name.span)
        if inferred_house:
            set_value("house", inferred_house.value, inferred_house.source, inferred_house.span)
        if ambiguous_house:
            warnings.append("ambiguous_house_inference")
        if area:
            _, _, area_name, _, _ = area
            set_value("living_area", area_name.value, area_name.source, area_name.span)
    elif area:
        (
            area_type,
            area_type_span,
            area_name,
            inferred_house,
            ambiguous_house,
        ) = area
        set_value("street_type", area_type, "text", area_type_span)
        set_value("street_name", area_name.value, area_name.source, area_name.span)
        if inferred_house:
            set_value("house", inferred_house.value, inferred_house.source, inferred_house.span)
        if ambiguous_house:
            warnings.append("ambiguous_house_inference")

    ambiguous_complex = len({normalize_for_match(candidate.value) for candidate in complexes}) > 1
    if ambiguous_complex:
        warnings.append("ambiguous_residential_complex")
    elif street and not area and complexes:
        candidate = complexes[0]
        set_value("living_area", candidate.value, candidate.source, candidate.span)

    # Cities of republican significance double as the public province value.
    if values["city"] in _REPUBLIC_CITIES:
        set_value(
            "province",
            values["city"],
            metadata["city"]["source"],
            metadata["city"]["span"],
        )

    if gazetteer is not None:
        protected_spans: list[tuple[int, int] | None] = [
            landmark.mask_span if landmark else None,
            *complex_spans,
        ]
        if street:
            _, marker_span, name_candidate, _, _ = street
            protected_spans.append(
                (
                    min(marker_span[0], name_candidate.span[0]),
                    max(marker_span[1], name_candidate.span[1]),
                )
            )
        if area:
            _, marker_span, name_candidate, _, _ = area
            protected_spans.append(
                (
                    min(marker_span[0], name_candidate.span[0]),
                    max(marker_span[1], name_candidate.span[1]),
                )
            )
        explicit_district_span = metadata["district"].get("span")
        if metadata["district"].get("source") == "text" and explicit_district_span:
            protected_spans.append(tuple(explicit_district_span))
        admin_text = _mask_spans(raw, protected_spans)
        admin = gazetteer.find_admin(admin_text) or {}
        for field in ("province", "city", "living_area"):
            if field == "living_area" and ambiguous_complex:
                continue
            if field in admin.get("ambiguous_fields", ()):
                warnings.append(f"gazetteer_{field}_ambiguous")
                continue
            if values[field] is not None or field not in admin:
                continue
            field_value, field_meta = _coerce_gazetteer_field(
                admin[field], "gazetteer"
            )
            set_value(
                field,
                field_value,
                field_meta.pop("source"),
                field_meta.pop("span"),
                **field_meta,
            )

        fuzzy_resolver = getattr(gazetteer, "fuzzy_lookup_street", None)
        parent_spans = [
            tuple(metadata[field]["span"])
            for field in ("province", "city", "district")
            if metadata[field].get("span") is not None
        ]
        if (
            values["street_name"] is None
            and values["city"] is not None
            and parent_spans
            and callable(fuzzy_resolver)
        ):
            unmarked = _extract_unmarked_street_house(
                detail_text,
                start=max(span[1] for span in parent_spans),
                house_already_known=values["house"] is not None,
            )
            if unmarked is not None:
                street_candidate, inferred_house = unmarked
                fuzzy_lookup = fuzzy_resolver(
                    values["city"],
                    street_candidate.value,
                    inferred_house.value if inferred_house else values["house"],
                    province=values["province"],
                    min_score=80.0,
                ) or {}
                if fuzzy_lookup.get("ambiguous"):
                    warnings.append("gazetteer_unmarked_street_ambiguous")
                elif fuzzy_lookup.get("matched") and fuzzy_lookup.get("street_name"):
                    set_value(
                        "street_name",
                        fuzzy_lookup["street_name"],
                        str(fuzzy_lookup.get("source") or "gazetteer"),
                        street_candidate.span,
                        score=fuzzy_lookup.get("score"),
                        matched_alias=fuzzy_lookup.get("matched_alias"),
                        match_kind=fuzzy_lookup.get("match_kind"),
                    )
                    if fuzzy_lookup.get("street_type"):
                        set_value(
                            "street_type",
                            fuzzy_lookup["street_type"],
                            str(fuzzy_lookup.get("source") or "gazetteer"),
                            None,
                            score=fuzzy_lookup.get("score"),
                        )
                    if inferred_house is not None:
                        set_value(
                            "house",
                            inferred_house.value,
                            inferred_house.source,
                            inferred_house.span,
                        )

        district_meta = metadata["district"]
        resolver = getattr(gazetteer, "resolve_district", None)
        if (
            values["district"] is not None
            and district_meta.get("source") == "text"
            and district_meta.get("span") is not None
            and callable(resolver)
        ):
            district_span = tuple(district_meta["span"])
            district_fragment = raw[slice(*district_span)]
            resolved = resolver(
                district_fragment,
                city=values["city"],
                province=values["province"],
            ) or {}
            if resolved.get("ambiguous"):
                warning = (
                    "gazetteer_district_parent_conflict"
                    if resolved.get("parent_conflict")
                    else "gazetteer_district_ambiguous"
                )
                warnings.append(warning)
            elif resolved.get("matched") and resolved.get("value") is not None:
                resolved_value, resolved_meta = _coerce_gazetteer_field(
                    resolved["value"], str(resolved.get("source") or "gazetteer")
                )
                resolved_meta.pop("span", None)
                shared_extra = {
                    key: resolved[key]
                    for key in (
                        "score",
                        "matched",
                        "ambiguous",
                        "parent_match",
                        "language",
                        "kato_code",
                    )
                    if key in resolved
                }
                set_value(
                    "district",
                    resolved_value,
                    resolved_meta.pop("source"),
                    district_span,
                    overwrite=True,
                    **shared_extra,
                    **resolved_meta,
                )
            else:
                warnings.append("gazetteer_district_unverified")

        area_lookup: dict[str, Any] = {}
        area_resolver = getattr(gazetteer, "lookup_area", None)
        if area and callable(area_resolver):
            area_name = values["living_area"] if street else values["street_name"]
            if area_name is not None:
                area_lookup = (
                    area_resolver(values["city"], area_name, area_type=area[0]) or {}
                )

        lookup: dict[str, Any] = {}
        if values["street_name"] is not None and (street is not None or area is None):
            lookup = (
                gazetteer.lookup_street(
                    values["city"],
                    values["street_name"],
                    values["house"],
                    street_type=values["street_type"],
                    province=values["province"],
                )
                or {}
            )

        lookup_parents = ("province", "city", "district")
        area_street_conflict = any(
            area_lookup.get(field)
            and lookup.get(field)
            and _parent_value_key(field, str(area_lookup[field]))
            != _parent_value_key(field, str(lookup[field]))
            for field in lookup_parents
        )
        if area_street_conflict:
            warnings.append("gazetteer_area_street_conflict")

        for lookup_kind, resolved_lookup, fields in (
            (
                "street",
                lookup,
                ("street_type", "province", "city", "district", "living_area"),
            ),
            ("area", area_lookup, ("province", "city", "district")),
        ):
            if not resolved_lookup:
                continue
            if resolved_lookup.get("ambiguous"):
                warnings.append(f"gazetteer_{lookup_kind}_ambiguous")
                continue
            if not resolved_lookup.get("matched", bool(resolved_lookup)):
                continue
            parent_conflict = any(
                values[field]
                and resolved_lookup.get(field)
                and _parent_value_key(field, str(values[field]))
                != _parent_value_key(field, str(resolved_lookup[field]))
                for field in lookup_parents
            )
            if parent_conflict:
                warnings.append(f"gazetteer_{lookup_kind}_parent_conflict")
                continue
            lookup_source = str(resolved_lookup.get("source") or "gazetteer")
            shared_extra = {
                key: resolved_lookup[key]
                for key in ("score", "matched", "ambiguous")
                if key in resolved_lookup
            }
            for field in fields:
                if field in lookup_parents and area_street_conflict:
                    continue
                if field == "living_area" and ambiguous_complex:
                    continue
                if values[field] is not None or resolved_lookup.get(field) is None:
                    continue
                field_value, field_meta = _coerce_gazetteer_field(
                    resolved_lookup[field], lookup_source
                )
                set_value(
                    field,
                    field_value,
                    field_meta.pop("source"),
                    field_meta.pop("span"),
                    **shared_extra,
                    **field_meta,
                )

    score = _confidence(values, metadata, warnings)
    result = {
        "id": record["id"],
        "raw_address": raw,
        **values,
        "confidence": score,
    }
    # Keep the public serializer boundary exact and metadata-free.
    result = {field: result[field] for field in PUBLIC_FIELDS}

    if not explain:
        return result

    explained_fields = {
        "id": {"source": "text", "span": None},
        "raw_address": {"source": "text", "span": [0, len(raw)]},
        **metadata,
        "confidence": {"source": "heuristic", "span": None},
    }
    return {"result": result, "fields": explained_fields, "warnings": warnings}


def parse_batch(
    records: list[dict[str, Any]],
    gazetteer: Any = None,
    explain: bool = False,
) -> list[dict[str, Any]]:
    """Parse a batch without deduplication or order changes."""

    if not isinstance(records, list):
        raise ValueError("batch must be a JSON array")
    for index, record in enumerate(records):
        _validate_record(record, index)
    return [
        parse_record(record, gazetteer=gazetteer, explain=explain)
        for record in records
    ]
