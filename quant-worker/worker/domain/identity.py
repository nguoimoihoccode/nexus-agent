"""Canonical identity verification implemented from the shared v1 contract."""

import hashlib
import json
import math
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

CANONICAL_SCHEMA_VERSION = "1"


def canonicalize(value: Any) -> Any:
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
    return json.dumps(
        canonicalize(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def content_hash(namespace: str, payload: Any) -> str:
    canonical = canonical_json_bytes(
        {
            "namespace": f"content:{namespace}",
            "payload": payload,
            "schema_version": CANONICAL_SCHEMA_VERSION,
        }
    )
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def revision_id(prefix: str, digest: str) -> str:
    algorithm, separator, hexadecimal = digest.partition(":")
    if algorithm != "sha256" or separator != ":" or len(hexadecimal) != 64:
        raise ValueError("Revision IDs require a full sha256 content hash.")
    return f"{prefix}_v1_{hexadecimal[:24]}"


def new_experiment_id() -> str:
    return f"exp_v1_{uuid4().hex}"
