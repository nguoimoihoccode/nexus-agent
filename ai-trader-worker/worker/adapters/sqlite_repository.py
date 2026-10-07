"""SQLite repository for Nexus signals and cached market snapshots."""

import json
import sqlite3
from pathlib import Path
from typing import Any

from worker.domain.market import dump_json, load_json_list, timestamp_from_iso, utc_now_iso


class PublicationConflict(ValueError):
    """An idempotency key was reused for a different approved action."""


class SQLiteAiTraderRepository:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._init_database()

    def health(self) -> dict[str, str | bool]:
        return {"status": "ok", "worker": "ai-trader-worker", "database": "ok"}

    def publish_signal(
        self,
        *,
        message_type: str,
        market: str,
        title: str,
        content: str,
        symbol: str | None,
        symbols: list[str],
        tags: list[str],
        actor_key: str | None,
        idempotency_key: str | None,
        action_digest: str | None,
    ) -> dict[str, Any]:
        created_at = utc_now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                existing = conn.execute(
                    """
                    SELECT id, message_type, market, created_at, action_digest
                    FROM nexus_signals WHERE idempotency_key = ?
                    """,
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    if existing["action_digest"] != action_digest:
                        raise PublicationConflict(
                            "idempotency_conflict: publication arguments changed"
                        )
                    return {
                        "success": True,
                        "signal_id": int(existing["id"]),
                        "message_type": existing["message_type"],
                        "market": existing["market"],
                        "created_at": existing["created_at"],
                        "reused": True,
                    }
            cursor = conn.execute(
                """
                INSERT INTO nexus_signals
                    (message_type, market, symbol, symbols_json, title, content,
                     tags_json, created_at, agent_name, actor_key,
                     idempotency_key, action_digest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_type,
                    market,
                    symbol,
                    dump_json(symbols),
                    title,
                    content,
                    dump_json(tags),
                    created_at,
                    "nexus-agent",
                    actor_key,
                    idempotency_key,
                    action_digest,
                ),
            )
            signal_id = int(cursor.lastrowid)
            conn.commit()
        return {
            "success": True,
            "signal_id": signal_id,
            "message_type": message_type,
            "market": market,
            "created_at": created_at,
        }

    def signal_feed(
        self,
        *,
        message_type: str | None = None,
        market: str | None = None,
        keyword: str | None = None,
        limit: int = 50,
        offset: int = 0,
        sort: str = "new",
    ) -> dict[str, Any]:
        del sort
        safe_limit = max(1, min(int(limit), 100))
        safe_offset = max(0, int(offset))
        normalized_type = (message_type or "").strip().lower()
        type_filter = normalized_type if normalized_type and normalized_type != "all" else None
        market_filter = (market or "").strip().lower() or None
        normalized_keyword = (keyword or "").strip()
        keyword_pattern = f"%{normalized_keyword}%" if normalized_keyword else None
        with self._connect() as conn:
            total_row = conn.execute(
                """
                SELECT COUNT(*) AS total FROM nexus_signals
                WHERE (? IS NULL OR message_type = ?)
                  AND (? IS NULL OR market = ?)
                  AND (? IS NULL OR title LIKE ? OR content LIKE ?)
                """,
                [
                    type_filter,
                    type_filter,
                    market_filter,
                    market_filter,
                    keyword_pattern,
                    keyword_pattern,
                    keyword_pattern,
                ],
            ).fetchone()
            rows = conn.execute(
                """
                SELECT * FROM nexus_signals
                WHERE (? IS NULL OR message_type = ?)
                  AND (? IS NULL OR market = ?)
                  AND (? IS NULL OR title LIKE ? OR content LIKE ?)
                ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?
                """,
                [
                    type_filter,
                    type_filter,
                    market_filter,
                    market_filter,
                    keyword_pattern,
                    keyword_pattern,
                    keyword_pattern,
                    safe_limit,
                    safe_offset,
                ],
            ).fetchall()
        signals = [self._signal_from_row(row) for row in rows]
        total = int(total_row["total"] if total_row else 0)
        return {
            "signals": signals,
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "has_more": safe_offset + len(signals) < total,
        }

    def load_snapshot(self, snapshot_type: str, snapshot_key: str) -> Any:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json FROM nexus_market_snapshots
                WHERE snapshot_type = ? AND snapshot_key = ?
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (snapshot_type, snapshot_key),
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["payload_json"])
        except (TypeError, ValueError):
            return None

    def upsert_snapshot(
        self,
        snapshot_type: str,
        snapshot_key: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO nexus_market_snapshots
                    (snapshot_type, snapshot_key, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(snapshot_type, snapshot_key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    created_at = excluded.created_at
                """,
                (
                    snapshot_type,
                    snapshot_key,
                    json.dumps(payload, ensure_ascii=True),
                    created_at,
                ),
            )
            conn.commit()

    @staticmethod
    def _signal_from_row(row: sqlite3.Row) -> dict[str, Any]:
        created_at = str(row["created_at"])
        message_type = str(row["message_type"])
        return {
            "signal_id": int(row["id"]),
            "agent_id": "nexus-agent",
            "agent_name": row["agent_name"] or "nexus-agent",
            "agent_identity_status": "nexus",
            "agent_is_verified": True,
            "message_type": message_type,
            "signal_type": message_type,
            "market": row["market"],
            "symbol": row["symbol"],
            "symbols": load_json_list(row["symbols_json"]),
            "title": row["title"],
            "content": row["content"],
            "tags": load_json_list(row["tags_json"]),
            "timestamp": timestamp_from_iso(created_at),
            "created_at": created_at,
            "reply_count": 0,
            "participant_count": 1,
            "accepted_reply_count": 0,
            "is_following_author": False,
        }

    def _init_database(self) -> None:
        if self.db_path != Path(":memory:"):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS nexus_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_type TEXT NOT NULL,
                    market TEXT NOT NULL,
                    symbol TEXT,
                    symbols_json TEXT NOT NULL DEFAULT '[]',
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    agent_name TEXT NOT NULL DEFAULT 'nexus-agent'
                );
                CREATE INDEX IF NOT EXISTS idx_nexus_signals_feed
                    ON nexus_signals (message_type, market, created_at);
                CREATE TABLE IF NOT EXISTS nexus_market_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_type TEXT NOT NULL,
                    snapshot_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(snapshot_type, snapshot_key)
                );
                """
            )
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(nexus_signals)")
            }
            for name in ("actor_key", "idempotency_key", "action_digest"):
                if name not in columns:
                    conn.execute(f"ALTER TABLE nexus_signals ADD COLUMN {name} TEXT")
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_nexus_signals_idempotency
                ON nexus_signals (idempotency_key)
                WHERE idempotency_key IS NOT NULL
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
