from collections.abc import Sequence
from typing import Protocol


class _HasContent(Protocol):
    content: str


def estimate_tokens(text: str) -> int:
    """Cheap local token estimate — no external tokenizer calls."""
    stripped = text.strip()
    if not stripped:
        return 0
    # ~4 characters per token is a stable heuristic for English prose.
    return max(1, (len(stripped) + 3) // 4)


def pack_into_budget[T: _HasContent](items: Sequence[T], token_budget: int) -> list[T]:
    if token_budget <= 0:
        return []
    packed: list[T] = []
    used = 0
    for item in items:
        cost = estimate_tokens(item.content)
        if packed and used + cost > token_budget:
            break
        if not packed and cost > token_budget:
            break
        packed.append(item)
        used += cost
    return packed


def place_head_tail[T](items: Sequence[T]) -> list[T]:
    """Spread highest-ranked items to the head and tail (mid-context blindness)."""
    if len(items) <= 2:
        return list(items)
    result: list[T | None] = [None] * len(items)
    left = 0
    right = len(items) - 1
    for index, item in enumerate(items):
        if index % 2 == 0:
            result[left] = item
            left += 1
        else:
            result[right] = item
            right -= 1
    return [item for item in result if item is not None]


def total_token_count(items: Sequence[_HasContent]) -> int:
    return sum(estimate_tokens(item.content) for item in items)
