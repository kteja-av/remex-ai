"""Pre-send PII gate — runs before any candidate text reaches an external provider."""

import re
from uuid import uuid4

from app.domain.policy import PiiStatus, PiiVerdict

_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    (
        "phone",
        re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    ),
    (
        "ssn",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    ),
    (
        "credit_card",
        re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
    ),
)


def scan_text(content: str) -> PiiVerdict:
    matched = tuple(
        label for label, pattern in _PII_PATTERNS if pattern.search(content)
    )
    if matched:
        return PiiVerdict(
            verdict_id=uuid4(),
            status=PiiStatus.BLOCKED,
            matched_patterns=matched,
        )
    return PiiVerdict(verdict_id=uuid4(), status=PiiStatus.CLEAN)
