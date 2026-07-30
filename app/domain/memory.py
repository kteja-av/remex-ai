from enum import StrEnum


class MemoryType(StrEnum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class AuditEvent(StrEnum):
    ADMIT = "admit"
    REJECT = "reject"
    UPDATE = "update"
    SUPERSEDE = "supersede"
    DECAY = "decay"
    REFLECT = "reflect"
    ARCHIVE = "archive"
    DELETE = "delete"


def validate_weight(name: str, value: float) -> float:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be within [0.0, 1.0], got {value}")
    return value
