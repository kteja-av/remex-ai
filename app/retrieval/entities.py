import re

_ENTITY_PATTERN = re.compile(
    r"""
    (?P<quoted>["']([A-Z][A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*)["'])
    |
    (?P<titled>\b(?:Dr|Mr|Mrs|Ms|Prof)\.\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)
    |
    (?P<hyphenated>\b[A-Z][A-Za-z0-9]+(?:-[A-Za-z0-9]+)+)
    |
    (?P<multi>\b(?!(?:What|Which|Who|Where|When|Why|How|Tell|Please)\b)[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)
    |
    (?P<single>\b(?!(?:What|Which|Who|Where|When|Why|How|Tell|Please|The|User)\b)[A-Z][a-z]{3,})
    """,
    re.VERBOSE,
)

_STOPWORDS = frozenset(
    {
        "The",
        "User",
        "Assistant",
        "What",
        "Which",
        "Who",
        "Where",
        "When",
        "Why",
        "How",
        "Tell",
        "Please",
        "Fact",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    }
)


def _entity_from_match(match: re.Match[str]) -> str | None:
    if match.group("quoted"):
        return match.group(2)
    if match.group("titled"):
        return match.group("titled")
    if match.group("hyphenated"):
        return match.group("hyphenated")
    if match.group("multi"):
        return match.group("multi")
    if match.group("single"):
        return match.group("single")
    return None


def extract_entities(text: str) -> list[str]:
    """Lightweight local entity extraction for graph-link indexing."""
    found: list[str] = []
    seen: set[str] = set()
    for match in _ENTITY_PATTERN.finditer(text):
        entity = _entity_from_match(match)
        if entity is None:
            continue
        normalized = entity.strip()
        if not normalized or normalized in _STOPWORDS:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(normalized)
    return found
