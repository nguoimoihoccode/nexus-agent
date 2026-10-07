"""Versioned canonical identities for immutable market-data revisions."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

CANONICAL_SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class RevisionIdentity:
    """Trusted identities emitted when one provider response is persisted."""

    schema_version: str
    revision_id: str
    request_fingerprint: str
    content_hash: str
    retrieved_at: str


@dataclass(frozen=True)
class DatasetBuildIdentity:
    """Trusted identity and manifest details for one ready Qlib revision."""

    schema_version: str
    dataset_revision_id: str
    dataset_alias: str
    source_staging_revision_id: str
    manifest_hash: str
    dates: list[date]
    symbols: list[str]


def utc_now() -> str:
    """Return one canonical UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def canonicalize(value: Any) -> Any:
    """Convert supported values into a stable JSON-compatible representation."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, datetime):
        normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Canonical values cannot contain NaN or infinity.")
        return float(format(value, ".17g"))
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): canonicalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    raise TypeError(f"Unsupported canonical value type: {type(value).__name__}.")


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize one value with deterministic keys, separators, and UTF-8."""
    return json.dumps(
        canonicalize(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(namespace: str, payload: Any) -> str:
    canonical = canonical_json_bytes(
        {
            "namespace": namespace,
            "payload": payload,
            "schema_version": CANONICAL_SCHEMA_VERSION,
        }
    )
    return hashlib.sha256(canonical).hexdigest()


def request_fingerprint(namespace: str, payload: Any) -> str:
    """Hash normalized request parameters without volatile retrieval metadata."""
    return f"rqf_v1_{_digest(f'request:{namespace}', payload)}"


def action_fingerprint(payload: Any) -> str:
    """Hash one normalized actor/tool/argument/target approval action."""
    return f"act_v1_{_digest('action:approval', payload)}"


def content_hash(namespace: str, payload: Any) -> str:
    """Hash normalized provider content independently from request parameters."""
    return f"sha256:{_digest(f'content:{namespace}', payload)}"


def revision_id(prefix: str, digest: str) -> str:
    """Generate a bounded canonical revision ID from a full content hash."""
    algorithm, separator, hexadecimal = digest.partition(":")
    if algorithm != "sha256" or separator != ":" or len(hexadecimal) != 64:
        raise ValueError("Revision IDs require a full sha256 content hash.")
    return f"{prefix}_v1_{hexadecimal[:24]}"
