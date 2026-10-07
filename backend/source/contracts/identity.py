"""Versioned canonical identity functions for backend-owned boundaries.

Services share golden vectors rather than importing one another's runtime code.
"""

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
    return f"rqf_v1_{_digest(f'request:{namespace}', payload)}"


def action_fingerprint(payload: Any) -> str:
    return f"act_v1_{_digest('action:approval', payload)}"


def content_hash(namespace: str, payload: Any) -> str:
    return f"sha256:{_digest(f'content:{namespace}', payload)}"


def revision_id(prefix: str, digest: str) -> str:
    algorithm, separator, hexadecimal = digest.partition(":")
    if algorithm != "sha256" or separator != ":" or len(hexadecimal) != 64:
        raise ValueError("Revision IDs require a full sha256 content hash.")
    return f"{prefix}_v1_{hexadecimal[:24]}"


def new_runtime_id(kind: str) -> str:
    """Create a trusted, versioned identity for non-content-addressed records."""
    prefixes = {
        "approval_request": "apr_v1_",
        "authorization_lease": "azl_v1_",
        "event": "evt_v1_",
        "experiment": "exp_v1_",
        "lineage_edge": "led_v1_",
        "lineage_node": "lin_v1_",
        "interpretation_report": "itr_v1_",
        "dossier_export": "xpt_v1_",
        "publication": "pub_v1_",
        "source_record": "src_v1_",
        "workflow": "wfl_v1_",
    }
    try:
        prefix = prefixes[kind]
    except KeyError as exc:
        raise ValueError(f"Unknown runtime identity kind: {kind}.") from exc
    return f"{prefix}{uuid4().hex}"


def artifact_id(digest: str) -> str:
    """Create a content-addressed immutable artifact identity."""
    return revision_id("art", digest)
