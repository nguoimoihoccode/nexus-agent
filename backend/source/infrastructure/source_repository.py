"""Safe, actor-scoped external research source evidence."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from psycopg.types.json import Jsonb

from source.contracts.identity import content_hash, new_runtime_id

_SECRET_TEXT = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9._~-]+|sk-[A-Za-z0-9_-]{12,}|"
    r"(?:password|token|secret)\s*[:=]\s*\S+)"
)
_SENSITIVE_QUERY_PARAM = re.compile(
    r"(?i)(?:access[_-]?token|api[_-]?key|authorization|credential|password|secret)"
)


class PostgresResearchSourceRepository:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def record_search_results(
        self,
        *,
        actor_key: str,
        thread_id: str,
        run_id: str,
        query: str,
        results: list[dict[str, Any]],
        subagent_name: str | None = None,
        tool_call_id: str | None = None,
    ) -> list[dict[str, Any]]:
        bounded_query = _redact(query.strip())[:500]
        recorded: list[dict[str, Any]] = []
        async with self.pool.connection() as connection:
            async with connection.transaction():
                await self._run_node(connection, actor_key, run_id, thread_id)
                for item in results[:5]:
                    locator = normalize_source_locator(str(item.get("url") or ""))
                    if locator is None:
                        continue
                    title = _redact(str(item.get("title") or "Untitled"))[:300]
                    excerpt = _redact(str(item.get("content") or "").strip())[:800]
                    published_at = str(
                        item.get("published_date") or item.get("published_at") or ""
                    )[:64] or None
                    digest = content_hash(
                        "research_source",
                        {
                            "locator": locator,
                            "title": title,
                            "excerpt": excerpt,
                            "published_at": published_at,
                        },
                    )
                    source_id = new_runtime_id("source_record")
                    metadata = {
                        "title": title,
                        "publisher": urlsplit(locator).hostname,
                        "publication_date": published_at,
                        "excerpt": excerpt,
                        "focused_query": bounded_query,
                        "tool_name": "web_search",
                        "subagent_name": _safe_identifier(subagent_name, "researcher"),
                        "tool_call_id": _safe_identifier(tool_call_id, "unavailable"),
                        "mapping_origin": "retrieval",
                    }
                    cursor = await connection.execute(
                        """
                        INSERT INTO nexus_domain.research_sources
                            (actor_key, source_record_id, thread_id, run_id,
                             normalized_locator, content_hash, source_metadata,
                             retrieval_status, retrieved_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 'available', %s)
                        ON CONFLICT (actor_key, run_id, normalized_locator, content_hash)
                        DO NOTHING
                        RETURNING source_record_id
                        """,
                        (
                            actor_key,
                            source_id,
                            thread_id,
                            run_id,
                            locator,
                            digest,
                            Jsonb(metadata),
                            datetime.now(UTC),
                        ),
                    )
                    inserted = await cursor.fetchone()
                    if inserted is None:
                        cursor = await connection.execute(
                            """
                            SELECT source_record_id
                            FROM nexus_domain.research_sources
                            WHERE actor_key = %s AND run_id = %s
                              AND normalized_locator = %s AND content_hash = %s
                            """,
                            (actor_key, run_id, locator, digest),
                        )
                        inserted = await cursor.fetchone()
                    if inserted is None:
                        continue
                    source_id = inserted[0]
                    await self._source_lineage(
                        connection,
                        actor_key,
                        run_id,
                        source_id,
                        locator,
                        digest,
                        metadata,
                        "available",
                    )
                    recorded.append(
                        {
                            "source_record_id": source_id,
                            "url": locator,
                            **metadata,
                            "content_hash": digest,
                            "retrieval_status": "available",
                        }
                    )
        return recorded

    async def record_gap(
        self,
        *,
        actor_key: str,
        thread_id: str,
        run_id: str,
        query: str,
        reason: str,
        subagent_name: str | None = None,
        tool_call_id: str | None = None,
    ) -> dict[str, Any]:
        bounded_query = _redact(query.strip())[:500]
        safe_reason = re.sub(r"[^a-z0-9_.-]+", "_", reason.lower())[:80]
        locator = f"evidence-gap:web-search:{safe_reason}"
        digest = content_hash(
            "research_evidence_gap",
            {"query": bounded_query, "reason": safe_reason},
        )
        source_id = new_runtime_id("source_record")
        metadata = {
            "title": "Live web search evidence gap",
            "focused_query": bounded_query,
            "evidence_gap_reason": safe_reason,
            "tool_name": "web_search",
            "subagent_name": _safe_identifier(subagent_name, "researcher"),
            "tool_call_id": _safe_identifier(tool_call_id, "unavailable"),
        }
        async with self.pool.connection() as connection:
            async with connection.transaction():
                await self._run_node(connection, actor_key, run_id, thread_id)
                cursor = await connection.execute(
                    """
                    INSERT INTO nexus_domain.research_sources
                        (actor_key, source_record_id, thread_id, run_id,
                         normalized_locator, content_hash, source_metadata,
                         retrieval_status, retrieved_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'evidence_gap', %s)
                    ON CONFLICT (actor_key, run_id, normalized_locator, content_hash)
                    DO NOTHING
                    RETURNING source_record_id
                    """,
                    (
                        actor_key,
                        source_id,
                        thread_id,
                        run_id,
                        locator,
                        digest,
                        Jsonb(metadata),
                        datetime.now(UTC),
                    ),
                )
                row = await cursor.fetchone()
                if row is None:
                    cursor = await connection.execute(
                        """
                        SELECT source_record_id FROM nexus_domain.research_sources
                        WHERE actor_key = %s AND run_id = %s
                          AND normalized_locator = %s AND content_hash = %s
                        """,
                        (actor_key, run_id, locator, digest),
                    )
                    row = await cursor.fetchone()
                if row is None:
                    raise RuntimeError("Research evidence gap disappeared.")
                source_id = row[0]
                await self._source_lineage(
                    connection,
                    actor_key,
                    run_id,
                    source_id,
                    locator,
                    digest,
                    metadata,
                    "evidence_gap",
                )
        return {"source_record_id": source_id, **metadata}

    @staticmethod
    async def _run_node(connection, actor: str, run_id: str, thread_id: str) -> None:
        await connection.execute(
            """
            INSERT INTO nexus_domain.lineage_nodes
                (actor_key, node_id, node_type, schema_version, properties)
            VALUES (%s, %s, 'research_run', '1', %s)
            ON CONFLICT (actor_key, node_id) DO NOTHING
            """,
            (actor, run_id, Jsonb({"run_id": run_id, "thread_id": thread_id})),
        )

    @staticmethod
    async def _source_lineage(
        connection,
        actor: str,
        run_id: str,
        source_id: str,
        locator: str,
        digest: str,
        metadata: dict[str, Any],
        status: str,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO nexus_domain.lineage_nodes
                (actor_key, node_id, node_type, schema_version, properties)
            VALUES (%s, %s, 'research_source', '1', %s)
            ON CONFLICT (actor_key, node_id) DO NOTHING
            """,
            (
                actor,
                source_id,
                Jsonb(
                    {
                        "source_record_id": source_id,
                        "locator": locator,
                        "content_hash": digest,
                        "retrieval_status": status,
                        **metadata,
                    }
                ),
            ),
        )
        edge_digest = hashlib.sha256(
            f"{actor}:cites_source:{run_id}:{source_id}".encode()
        ).hexdigest()
        await connection.execute(
            """
            INSERT INTO nexus_domain.lineage_edges
                (actor_key, edge_id, edge_type, source_node_id,
                 target_node_id, properties)
            VALUES (%s, %s, 'cites_source', %s, %s, '{}'::jsonb)
            ON CONFLICT (actor_key, edge_type, source_node_id, target_node_id)
            DO NOTHING
            """,
            (actor, f"led_v1_{edge_digest[:32]}", run_id, source_id),
        )


def normalize_source_locator(value: str) -> str | None:
    if len(value) > 4096:
        return None
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return None
    host = parts.hostname.lower()
    try:
        port = parts.port
    except ValueError:
        return None
    netloc = host if port is None else f"{host}:{port}"
    path = parts.path or "/"
    try:
        query = urlencode(
            [
                (key, item)
                for key, item in parse_qsl(parts.query, keep_blank_values=True)
                if not _SENSITIVE_QUERY_PARAM.search(key)
            ],
            doseq=True,
        )
    except ValueError:
        return None
    normalized = urlunsplit((parts.scheme.lower(), netloc, path, query, ""))
    return normalized if len(normalized) <= 2048 else None


def _redact(value: str) -> str:
    return _SECRET_TEXT.sub("[REDACTED]", value)


def _safe_identifier(value: str | None, default: str) -> str:
    candidate = str(value or default)
    return candidate[:200] if re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", candidate) else default
