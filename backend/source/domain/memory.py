"""Stable keys for actor-scoped durable memory facts."""

from __future__ import annotations

import re


_MEMORY_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


def normalize_memory_key(value: object) -> str:
    """Normalize one generic, non-sensitive memory fact key."""
    normalized = str(value or "").strip().lower()
    if not normalized:
        raise ValueError("Memory key cannot be empty.")
    if _MEMORY_KEY_PATTERN.fullmatch(normalized) is None:
        raise ValueError(
            "Memory key must use only lowercase letters, digits, dots, underscores, "
            "or hyphens and be at most 128 characters."
        )
    return normalized
