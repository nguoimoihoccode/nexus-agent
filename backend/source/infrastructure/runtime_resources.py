"""Process-local handles for already-owned shared infrastructure resources."""

from typing import Any

_domain_pool: Any | None = None


def set_domain_pool(pool: Any) -> None:
    global _domain_pool
    _domain_pool = pool


def require_domain_pool() -> Any:
    if _domain_pool is None:
        raise RuntimeError("Backend domain pool has not been prepared.")
    return _domain_pool


def optional_domain_pool() -> Any | None:
    return _domain_pool
