"""The durable registry store (aiosqlite).

Async end-to-end (never blocks the loop). An injectable clock makes time-stamped
rows deterministic in tests. Holds ``lanes``, ``triggers`` (populated in Phase 3),
and the ``actions_log`` audit of every send/action.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from outfitter.dispatch.contracts.errors import NotFoundError

from .models import (
    ActionAdapter,
    ActionRecord,
    Guard,
    Lane,
    LaneModelSettings,
    LaneSource,
    LaneStatus,
    LaneSync,
    ModelCatalogEntry,
    QueuedMessage,
    ServiceTierEntry,
    Trigger,
    WhenAdapter,
)
from .refs import BASE58BTC_ALPHABET, CODEX_REF_SOURCE, codex_ref_payload, make_ref

Clock = Callable[[], datetime]
SCHEMA_VERSION = 6

_QUEUED_MESSAGES_SCHEMA = """
CREATE TABLE IF NOT EXISTS queued_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lane TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error TEXT,
    FOREIGN KEY(lane) REFERENCES lanes(id) ON DELETE CASCADE
);
"""


def _utcnow() -> datetime:
    return datetime.now(UTC)


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS lanes (
    id TEXT PRIMARY KEY,
    ref TEXT NOT NULL UNIQUE,
    ref_source TEXT NOT NULL,
    ref_payload TEXT NOT NULL,
    ref_mixer TEXT NOT NULL,
    handle TEXT NOT NULL,
    role TEXT,
    cwd TEXT,
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unknown',
    pinned INTEGER NOT NULL DEFAULT 0,
    active_turn_id TEXT,
    latest_turn_id TEXT,
    latest_turn_status TEXT,
    latest_error TEXT,
    latest_error_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_event_at TEXT
);
CREATE TABLE IF NOT EXISTS triggers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    lane_selector TEXT NOT NULL,
    when_spec TEXT NOT NULL,
    action_spec TEXT NOT NULL,
    guard_spec TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT,
    last_fired_at TEXT
);
CREATE TABLE IF NOT EXISTS actions_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    op TEXT NOT NULL,
    lane TEXT,
    trigger_id TEXT,
    detail TEXT,
    outcome TEXT NOT NULL DEFAULT 'ok'
);
{_QUEUED_MESSAGES_SCHEMA}
CREATE TABLE IF NOT EXISTS lane_sync_sources (
    lane TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    source_path TEXT,
    source_device INTEGER,
    source_inode INTEGER,
    source_size INTEGER,
    source_mtime_ns INTEGER,
    line_count INTEGER,
    first_offset INTEGER,
    tail_offset INTEGER,
    last_synced_at TEXT,
    error TEXT,
    FOREIGN KEY(lane) REFERENCES lanes(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS lane_snapshots (
    lane TEXT PRIMARY KEY,
    display_name TEXT,
    preview TEXT,
    cwd TEXT,
    source TEXT,
    thread_source TEXT,
    model_provider TEXT,
    model TEXT,
    reasoning_effort TEXT,
    session_id TEXT,
    latest_event_at TEXT,
    latest_turn_id TEXT,
    transcript_partial INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(lane) REFERENCES lanes(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS model_catalog (
    id TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'openai',
    display_name TEXT,
    description TEXT,
    is_default INTEGER,
    hidden INTEGER,
    default_reasoning_effort TEXT,
    supported_reasoning_efforts TEXT NOT NULL DEFAULT '[]',
    default_service_tier TEXT,
    service_tiers TEXT NOT NULL DEFAULT '[]',
    additional_speed_tiers TEXT NOT NULL DEFAULT '[]',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'app-server',
    PRIMARY KEY(provider, id)
);
CREATE TABLE IF NOT EXISTS lane_model_settings (
    lane TEXT PRIMARY KEY,
    model_provider TEXT,
    model TEXT,
    reasoning_effort TEXT,
    requested_service_tier TEXT,
    resolved_service_tier TEXT,
    service_tier_name TEXT,
    service_tier_source TEXT NOT NULL DEFAULT 'unknown',
    updated_at TEXT NOT NULL,
    FOREIGN KEY(lane) REFERENCES lanes(id) ON DELETE CASCADE
);
"""


class Registry:
    """The lane/trigger/audit store."""

    def __init__(self, conn: aiosqlite.Connection, now: Clock) -> None:
        self._conn = conn
        self._now = now

    @classmethod
    async def open(cls, path: str | Path = ":memory:", now: Clock = _utcnow) -> Registry:
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        store = cls(conn, now)
        async with store._conn.execute("PRAGMA user_version") as cur:
            row = await cur.fetchone()
        user_version = int(row[0]) if row is not None else 0
        if user_version > SCHEMA_VERSION:
            await store._conn.close()
            raise RuntimeError(
                f"registry schema version {user_version} is newer than supported "
                f"version {SCHEMA_VERSION}"
            )
        await store._conn.executescript(_SCHEMA)
        if user_version < SCHEMA_VERSION:
            await store._migrate(user_version)
            await store._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        await store._conn.commit()
        return store

    async def close(self) -> None:
        await self._conn.close()

    def now_iso(self) -> str:
        return self._now().isoformat()

    async def _migrate(self, user_version: int) -> None:
        if user_version < 3:
            await self._ensure_ref_columns()
            async with self._conn.execute(
                "SELECT id FROM lanes WHERE ref IS NULL OR ref = '' ORDER BY created_at, id"
            ) as cur:
                rows = await cur.fetchall()
            for row in rows:
                thread_id = str(row["id"])
                ref, source, payload, mixer = await self._allocate_ref_parts(thread_id)
                await self._conn.execute(
                    "UPDATE lanes SET ref = ?, ref_source = ?, ref_payload = ?, ref_mixer = ? "
                    "WHERE id = ?",
                    (ref, source, payload, mixer, thread_id),
                )
            await self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_lanes_ref ON lanes(ref)"
            )
        if user_version < 4:
            await self._ensure_lane_runtime_columns()
        if user_version < 5:
            await self._ensure_model_registry_tables()
        if user_version < 6:
            await self._prune_orphan_lane_children()
            await self._ensure_queued_messages_foreign_key()

    async def _ensure_ref_columns(self) -> None:
        async with self._conn.execute("PRAGMA table_info(lanes)") as cur:
            rows = await cur.fetchall()
        columns = {str(row["name"]) for row in rows}
        for name in ("ref", "ref_source", "ref_payload", "ref_mixer"):
            if name not in columns:
                await self._conn.execute(f"ALTER TABLE lanes ADD COLUMN {name} TEXT")

    async def _ensure_lane_runtime_columns(self) -> None:
        async with self._conn.execute("PRAGMA table_info(lanes)") as cur:
            rows = await cur.fetchall()
        columns = {str(row["name"]) for row in rows}
        column_defs = {
            "latest_turn_id": "TEXT",
            "latest_turn_status": "TEXT",
            "latest_error": "TEXT",
            "latest_error_at": "TEXT",
        }
        for name, definition in column_defs.items():
            if name not in columns:
                await self._conn.execute(f"ALTER TABLE lanes ADD COLUMN {name} {definition}")

    async def _ensure_model_registry_tables(self) -> None:
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS model_catalog (
                id TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'openai',
                display_name TEXT,
                description TEXT,
                is_default INTEGER,
                hidden INTEGER,
                default_reasoning_effort TEXT,
                supported_reasoning_efforts TEXT NOT NULL DEFAULT '[]',
                default_service_tier TEXT,
                service_tiers TEXT NOT NULL DEFAULT '[]',
                additional_speed_tiers TEXT NOT NULL DEFAULT '[]',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'app-server',
                PRIMARY KEY(provider, id)
            );
            CREATE TABLE IF NOT EXISTS lane_model_settings (
                lane TEXT PRIMARY KEY,
                model_provider TEXT,
                model TEXT,
                reasoning_effort TEXT,
                requested_service_tier TEXT,
                resolved_service_tier TEXT,
                service_tier_name TEXT,
                service_tier_source TEXT NOT NULL DEFAULT 'unknown',
                updated_at TEXT NOT NULL,
                FOREIGN KEY(lane) REFERENCES lanes(id) ON DELETE CASCADE
            );
            """
        )

    async def _prune_orphan_lane_children(self) -> None:
        for table in (
            "lane_sync_sources",
            "lane_snapshots",
            "lane_model_settings",
            "queued_messages",
        ):
            await self._conn.execute(
                f"""
                DELETE FROM {table}
                WHERE NOT EXISTS (
                    SELECT 1 FROM lanes WHERE lanes.id = {table}.lane
                )
                """
            )

    async def _ensure_queued_messages_foreign_key(self) -> None:
        async with self._conn.execute("PRAGMA foreign_key_list(queued_messages)") as cur:
            rows = await cur.fetchall()
        if any(str(row["table"]) == "lanes" and str(row["from"]) == "lane" for row in rows):
            return
        await self._conn.executescript(
            """
            ALTER TABLE queued_messages RENAME TO queued_messages_old;
            CREATE TABLE queued_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lane TEXT NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error TEXT,
                FOREIGN KEY(lane) REFERENCES lanes(id) ON DELETE CASCADE
            );
            INSERT INTO queued_messages (id, lane, text, status, created_at, updated_at, error)
            SELECT
                old.id,
                old.lane,
                old.text,
                old.status,
                old.created_at,
                old.updated_at,
                old.error
            FROM queued_messages_old old
            INNER JOIN lanes ON lanes.id = old.lane;
            DROP TABLE queued_messages_old;
            """
        )

    # --- lanes ----------------------------------------------------------------

    async def add_lane(
        self,
        *,
        id: str,
        handle: str,
        source: LaneSource,
        role: str | None = None,
        cwd: str | None = None,
        status: LaneStatus = "unknown",
        pinned: bool = False,
    ) -> Lane:
        now = self._now()
        ref, ref_source, ref_payload, ref_mixer = await self._allocate_ref_parts(id)
        lane = Lane(
            id=id,
            ref=ref,
            ref_source=ref_source,
            ref_payload=ref_payload,
            ref_mixer=ref_mixer,
            handle=handle,
            role=role,
            cwd=cwd,
            source=source,
            status=status,
            pinned=pinned,
            created_at=now,
            updated_at=now,
            last_event_at=None,
        )
        await self._insert_lane(lane)
        await self._conn.commit()
        return lane

    async def add_lane_with_sync(
        self,
        *,
        id: str,
        handle: str,
        source: LaneSource,
        sync: LaneSync,
        role: str | None = None,
        cwd: str | None = None,
        status: LaneStatus = "unknown",
        pinned: bool = False,
        audit_op: str | None = None,
        audit_detail: str | None = None,
    ) -> tuple[Lane, LaneSync]:
        if sync.lane != id:
            raise ValueError(f"sync lane {sync.lane!r} does not match lane id {id!r}")
        now = self._now()
        ref, ref_source, ref_payload, ref_mixer = await self._allocate_ref_parts(id)
        lane = Lane(
            id=id,
            ref=ref,
            ref_source=ref_source,
            ref_payload=ref_payload,
            ref_mixer=ref_mixer,
            handle=handle,
            role=role,
            cwd=cwd,
            source=source,
            status=status,
            pinned=pinned,
            created_at=now,
            updated_at=now,
            last_event_at=None,
        )
        synced_at = sync.last_synced_at or now.isoformat()
        await self._conn.execute("BEGIN")
        try:
            await self._insert_lane(lane)
            await self._upsert_lane_sync_rows(sync, synced_at)
            if audit_op is not None:
                await self._insert_action_log(audit_op, lane=lane.id, detail=audit_detail)
        except Exception:
            await self._conn.rollback()
            raise
        await self._conn.commit()
        saved_sync = await self.get_lane_sync(lane.id)
        if saved_sync is None:
            raise RuntimeError("lane sync insert did not return a row")
        return lane, saved_sync

    async def _insert_lane(self, lane: Lane) -> None:
        await self._conn.execute(
            "INSERT INTO lanes (id, ref, ref_source, ref_payload, ref_mixer, handle, role, cwd, "
            "source, status, pinned, active_turn_id, latest_turn_id, latest_turn_status, "
            "latest_error, latest_error_at, created_at, updated_at, last_event_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                lane.id,
                lane.ref,
                lane.ref_source,
                lane.ref_payload,
                lane.ref_mixer,
                lane.handle,
                lane.role,
                lane.cwd,
                lane.source,
                lane.status,
                int(lane.pinned),
                lane.active_turn_id,
                lane.latest_turn_id,
                lane.latest_turn_status,
                lane.latest_error,
                lane.latest_error_at.isoformat() if lane.latest_error_at else None,
                lane.created_at.isoformat(),
                lane.updated_at.isoformat(),
                lane.last_event_at.isoformat() if lane.last_event_at else None,
            ),
        )

    async def _allocate_ref_parts(self, thread_id: str) -> tuple[str, str, str, str]:
        source = CODEX_REF_SOURCE
        payload = codex_ref_payload(thread_id)
        for mixer in BASE58BTC_ALPHABET:
            candidate = make_ref(source=source, payload=payload, mixer=mixer)
            existing = await self.find_lane_by_ref(candidate)
            if existing is None or existing.id == thread_id:
                return candidate, source, payload, mixer
        raise RuntimeError(
            f"ref mixer alphabet exhausted for Codex thread hash payload {payload!r}; "
            "use the full Codex thread id"
        )

    async def find_lane(self, lane_id: str) -> Lane | None:
        async with self._conn.execute("SELECT * FROM lanes WHERE id = ?", (lane_id,)) as cur:
            row = await cur.fetchone()
        return _row_to_lane(row) if row is not None else None

    async def find_lane_by_ref(self, ref: str) -> Lane | None:
        async with self._conn.execute("SELECT * FROM lanes WHERE ref = ?", (ref,)) as cur:
            row = await cur.fetchone()
        return _row_to_lane(row) if row is not None else None

    async def find_lane_by_handle(self, handle: str) -> Lane | None:
        async with self._conn.execute("SELECT * FROM lanes WHERE handle = ?", (handle,)) as cur:
            row = await cur.fetchone()
        return _row_to_lane(row) if row is not None else None

    async def find_lanes_by_handle(self, handle: str) -> list[Lane]:
        async with self._conn.execute("SELECT * FROM lanes WHERE handle = ?", (handle,)) as cur:
            rows = await cur.fetchall()
        return [_row_to_lane(row) for row in rows]

    async def find_lanes_by_title(self, title: str) -> list[Lane]:
        async with self._conn.execute(
            """
            SELECT lanes.* FROM lanes
            LEFT JOIN lane_snapshots snap ON snap.lane = lanes.id
            WHERE snap.display_name = ? OR lanes.handle = ? OR ltrim(lanes.handle, '@') = ?
            ORDER BY lanes.created_at, lanes.id
            """,
            (title, title, title),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_lane(row) for row in rows]

    async def fuzzy_find_lanes_by_title(self, title: str) -> list[Lane]:
        pattern = f"%{title}%"
        async with self._conn.execute(
            """
            SELECT lanes.* FROM lanes
            LEFT JOIN lane_snapshots snap ON snap.lane = lanes.id
            WHERE snap.display_name LIKE ? OR lanes.handle LIKE ? OR ltrim(lanes.handle, '@') LIKE ?
            ORDER BY lanes.created_at, lanes.id
            """,
            (pattern, pattern, pattern),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_lane(row) for row in rows]

    async def get_lane(self, lane_id: str) -> Lane:
        lane = await self.find_lane(lane_id)
        if lane is None:
            raise NotFoundError(f"no lane {lane_id!r}")
        return lane

    async def list_lanes(self, *, include_archived: bool = False) -> list[Lane]:
        sql = "SELECT * FROM lanes"
        if not include_archived:
            sql += " WHERE status != 'archived'"
        sql += " ORDER BY created_at"
        async with self._conn.execute(sql) as cur:
            rows = await cur.fetchall()
        return [_row_to_lane(row) for row in rows]

    async def update_lane_status(self, lane_id: str, status: LaneStatus) -> None:
        await self._conn.execute(
            "UPDATE lanes SET status = ?, updated_at = ? WHERE id = ?",
            (status, self._now().isoformat(), lane_id),
        )
        await self._conn.commit()

    async def update_lane_handle(self, lane_id: str, handle: str) -> None:
        await self._conn.execute(
            "UPDATE lanes SET handle = ?, updated_at = ? WHERE id = ?",
            (handle, self._now().isoformat(), lane_id),
        )
        await self._conn.commit()

    async def set_active_turn(self, lane_id: str, turn_id: str | None) -> None:
        await self._conn.execute(
            "UPDATE lanes SET active_turn_id = ?, updated_at = ? WHERE id = ?",
            (turn_id, self._now().isoformat(), lane_id),
        )
        await self._conn.commit()

    async def record_turn_started(self, lane_id: str, turn_id: str | None) -> None:
        await self._conn.execute(
            "UPDATE lanes SET active_turn_id = ?, latest_turn_id = ?, "
            "latest_turn_status = 'started', latest_error = NULL, latest_error_at = NULL, "
            "status = 'busy', updated_at = ? WHERE id = ?",
            (turn_id, turn_id, self._now().isoformat(), lane_id),
        )
        await self._conn.commit()

    async def record_turn_completed(self, lane_id: str, turn_id: str | None) -> None:
        await self._conn.execute(
            "UPDATE lanes SET active_turn_id = NULL, latest_turn_id = COALESCE(?, latest_turn_id), "
            "latest_turn_status = 'completed', latest_error = NULL, latest_error_at = NULL, "
            "status = 'idle', updated_at = ? WHERE id = ?",
            (turn_id, self._now().isoformat(), lane_id),
        )
        await self._conn.commit()

    async def record_turn_failed(
        self, lane_id: str, turn_id: str | None, message: str | None
    ) -> None:
        now = self._now().isoformat()
        await self._conn.execute(
            "UPDATE lanes SET active_turn_id = NULL, latest_turn_id = COALESCE(?, latest_turn_id), "
            "latest_turn_status = 'failed', latest_error = ?, latest_error_at = ?, "
            "status = 'error', updated_at = ? WHERE id = ?",
            (turn_id, message, now if message is not None else None, now, lane_id),
        )
        await self._conn.commit()

    async def record_turn_request_failed(self, lane_id: str, message: str | None) -> None:
        now = self._now().isoformat()
        await self._conn.execute(
            "UPDATE lanes SET active_turn_id = NULL, latest_turn_id = NULL, "
            "latest_turn_status = 'failed', latest_error = ?, latest_error_at = ?, "
            "status = 'error', updated_at = ? WHERE id = ?",
            (message, now if message is not None else None, now, lane_id),
        )
        await self._conn.commit()

    async def mark_lane_idle(self, lane_id: str) -> None:
        lane = await self.find_lane(lane_id)
        status: LaneStatus = (
            "error" if lane is not None and lane.latest_turn_status == "failed" else "idle"
        )
        await self._conn.execute(
            "UPDATE lanes SET active_turn_id = NULL, status = ?, updated_at = ? WHERE id = ?",
            (status, self._now().isoformat(), lane_id),
        )
        await self._conn.commit()

    async def touch_lane_event(self, lane_id: str, when: datetime | None = None) -> None:
        stamp = (when or self._now()).isoformat()
        await self._conn.execute(
            "UPDATE lanes SET last_event_at = ?, updated_at = ? WHERE id = ?",
            (stamp, self._now().isoformat(), lane_id),
        )
        await self._conn.commit()

    # --- queued messages ------------------------------------------------------

    async def enqueue_message(self, *, lane: str, text: str) -> QueuedMessage:
        now = self._now().isoformat()
        cur = await self._conn.execute(
            "INSERT INTO queued_messages (lane, text, status, created_at, updated_at) "
            "VALUES (?, ?, 'pending', ?, ?)",
            (lane, text, now, now),
        )
        await self._conn.commit()
        message_id = cur.lastrowid
        if message_id is None:
            raise RuntimeError("queued message insert did not return an id")
        return await self.get_queued_message(message_id)

    async def get_queued_message(self, message_id: int) -> QueuedMessage:
        async with self._conn.execute(
            "SELECT * FROM queued_messages WHERE id = ?", (message_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise NotFoundError(f"no queued message {message_id!r}")
        return QueuedMessage.model_validate(_row_dict(row))

    async def next_pending_message(self, lane: str) -> QueuedMessage | None:
        async with self._conn.execute(
            "SELECT * FROM queued_messages WHERE lane = ? AND status = 'pending' "
            "ORDER BY id LIMIT 1",
            (lane,),
        ) as cur:
            row = await cur.fetchone()
        return QueuedMessage.model_validate(_row_dict(row)) if row is not None else None

    async def pending_message_count(self, lane: str) -> int:
        async with self._conn.execute(
            "SELECT COUNT(*) AS count FROM queued_messages WHERE lane = ? AND status = 'pending'",
            (lane,),
        ) as cur:
            row = await cur.fetchone()
        return int(row["count"]) if row is not None else 0

    async def claim_queued_message(self, message_id: int) -> bool:
        cur = await self._conn.execute(
            "UPDATE queued_messages SET status = 'sending', updated_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (self._now().isoformat(), message_id),
        )
        await self._conn.commit()
        return cur.rowcount == 1

    async def complete_queued_message(self, message_id: int) -> None:
        await self._conn.execute(
            "UPDATE queued_messages SET status = 'sent', updated_at = ?, error = NULL WHERE id = ?",
            (self._now().isoformat(), message_id),
        )
        await self._conn.commit()

    async def fail_queued_message(self, message_id: int, error: str) -> None:
        await self._conn.execute(
            "UPDATE queued_messages SET status = 'error', updated_at = ?, error = ? WHERE id = ?",
            (self._now().isoformat(), error, message_id),
        )
        await self._conn.commit()

    async def reset_sending_messages(self) -> int:
        cur = await self._conn.execute(
            "UPDATE queued_messages SET status = 'pending', updated_at = ? "
            "WHERE status = 'sending'",
            (self._now().isoformat(),),
        )
        await self._conn.commit()
        return cur.rowcount

    # --- lane sync -----------------------------------------------------------

    async def upsert_lane_sync(self, sync: LaneSync) -> LaneSync:
        now = sync.last_synced_at or self._now().isoformat()
        await self._conn.execute("BEGIN")
        try:
            await self._upsert_lane_sync_rows(sync, now)
        except Exception:
            await self._conn.rollback()
            raise
        await self._conn.commit()
        got = await self.get_lane_sync(sync.lane)
        if got is None:
            raise RuntimeError("lane sync upsert did not return a row")
        return got

    async def _upsert_lane_sync_rows(self, sync: LaneSync, last_synced_at: str) -> None:
        await self._conn.execute(
            "INSERT INTO lane_sync_sources (lane, state, source_path, source_device, "
            "source_inode, source_size, source_mtime_ns, line_count, first_offset, "
            "tail_offset, last_synced_at, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(lane) DO UPDATE SET state = excluded.state, "
            "source_path = excluded.source_path, source_device = excluded.source_device, "
            "source_inode = excluded.source_inode, source_size = excluded.source_size, "
            "source_mtime_ns = excluded.source_mtime_ns, line_count = excluded.line_count, "
            "first_offset = excluded.first_offset, tail_offset = excluded.tail_offset, "
            "last_synced_at = excluded.last_synced_at, error = excluded.error",
            (
                sync.lane,
                sync.state,
                sync.source_path,
                sync.source_device,
                sync.source_inode,
                sync.source_size,
                sync.source_mtime_ns,
                sync.line_count,
                sync.first_offset,
                sync.tail_offset,
                last_synced_at,
                sync.error,
            ),
        )
        await self._conn.execute(
            "INSERT INTO lane_snapshots (lane, display_name, preview, cwd, source, "
            "thread_source, model_provider, model, reasoning_effort, session_id, "
            "latest_event_at, latest_turn_id, transcript_partial) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(lane) DO UPDATE SET display_name = excluded.display_name, "
            "preview = excluded.preview, cwd = excluded.cwd, source = excluded.source, "
            "thread_source = excluded.thread_source, model_provider = excluded.model_provider, "
            "model = excluded.model, reasoning_effort = excluded.reasoning_effort, "
            "session_id = excluded.session_id, latest_event_at = excluded.latest_event_at, "
            "latest_turn_id = excluded.latest_turn_id, "
            "transcript_partial = excluded.transcript_partial",
            (
                sync.lane,
                sync.display_name,
                sync.preview,
                sync.cwd,
                sync.source,
                sync.thread_source,
                sync.model_provider,
                sync.model,
                sync.reasoning_effort,
                sync.session_id,
                sync.latest_event_at,
                sync.latest_turn_id,
                int(sync.transcript_partial),
            ),
        )

    async def get_lane_sync(self, lane_id: str) -> LaneSync | None:
        async with self._conn.execute(_LANE_SYNC_SELECT + " WHERE src.lane = ?", (lane_id,)) as cur:
            row = await cur.fetchone()
        return _row_to_lane_sync(row) if row is not None else None

    async def get_lane_sync_many(self, lane_ids: list[str]) -> dict[str, LaneSync]:
        if not lane_ids:
            return {}
        placeholders = ", ".join("?" for _ in lane_ids)
        async with self._conn.execute(
            _LANE_SYNC_SELECT + f" WHERE src.lane IN ({placeholders})", tuple(lane_ids)
        ) as cur:
            rows = await cur.fetchall()
        return {sync.lane: sync for sync in (_row_to_lane_sync(row) for row in rows)}

    # --- model catalog / lane model provenance ---------------------------------

    async def upsert_model_catalog(self, models: list[ModelCatalogEntry]) -> None:
        for model in models:
            existing = await self.get_model_catalog_entry(model.id, provider=model.provider)
            first_seen_at = existing.first_seen_at if existing is not None else model.first_seen_at
            await self._conn.execute(
                "INSERT INTO model_catalog (id, provider, display_name, description, "
                "is_default, hidden, default_reasoning_effort, supported_reasoning_efforts, "
                "default_service_tier, service_tiers, additional_speed_tiers, first_seen_at, "
                "last_seen_at, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(provider, id) DO UPDATE SET display_name = excluded.display_name, "
                "description = excluded.description, is_default = excluded.is_default, "
                "hidden = excluded.hidden, "
                "default_reasoning_effort = excluded.default_reasoning_effort, "
                "supported_reasoning_efforts = excluded.supported_reasoning_efforts, "
                "default_service_tier = excluded.default_service_tier, "
                "service_tiers = excluded.service_tiers, "
                "additional_speed_tiers = excluded.additional_speed_tiers, "
                "last_seen_at = excluded.last_seen_at, source = excluded.source",
                (
                    model.id,
                    model.provider,
                    model.display_name,
                    model.description,
                    _bool_or_none(model.is_default),
                    _bool_or_none(model.hidden),
                    model.default_reasoning_effort,
                    json.dumps(model.supported_reasoning_efforts),
                    model.default_service_tier,
                    json.dumps([tier.model_dump(mode="python") for tier in model.service_tiers]),
                    json.dumps(model.additional_speed_tiers),
                    first_seen_at,
                    model.last_seen_at,
                    model.source,
                ),
            )
        await self._conn.commit()

    async def list_model_catalog(self, provider: str | None = None) -> list[ModelCatalogEntry]:
        query = "SELECT * FROM model_catalog"
        params: tuple[str, ...] = ()
        if provider is not None:
            query += " WHERE provider = ?"
            params = (provider,)
        query += " ORDER BY provider, hidden, id"
        async with self._conn.execute(query, params) as cur:
            rows = await cur.fetchall()
        return [_row_to_model_catalog_entry(row) for row in rows]

    async def get_model_catalog_entry(
        self, model_id: str, *, provider: str = "openai"
    ) -> ModelCatalogEntry | None:
        async with self._conn.execute(
            "SELECT * FROM model_catalog WHERE provider = ? AND id = ?",
            (provider, model_id),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_model_catalog_entry(row) if row is not None else None

    async def upsert_lane_model_settings(self, settings: LaneModelSettings) -> None:
        await self._conn.execute(
            "INSERT INTO lane_model_settings (lane, model_provider, model, reasoning_effort, "
            "requested_service_tier, resolved_service_tier, service_tier_name, "
            "service_tier_source, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(lane) DO UPDATE SET model_provider = excluded.model_provider, "
            "model = excluded.model, reasoning_effort = excluded.reasoning_effort, "
            "requested_service_tier = excluded.requested_service_tier, "
            "resolved_service_tier = excluded.resolved_service_tier, "
            "service_tier_name = excluded.service_tier_name, "
            "service_tier_source = excluded.service_tier_source, updated_at = excluded.updated_at",
            (
                settings.lane,
                settings.model_provider,
                settings.model,
                settings.reasoning_effort,
                settings.requested_service_tier,
                settings.resolved_service_tier,
                settings.service_tier_name,
                settings.service_tier_source,
                settings.updated_at,
            ),
        )
        await self._conn.commit()

    async def get_lane_model_settings(self, lane_id: str) -> LaneModelSettings | None:
        async with self._conn.execute(
            "SELECT * FROM lane_model_settings WHERE lane = ?", (lane_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_lane_model_settings(row) if row is not None else None

    async def get_lane_model_settings_many(
        self, lane_ids: list[str]
    ) -> dict[str, LaneModelSettings]:
        if not lane_ids:
            return {}
        placeholders = ", ".join("?" for _ in lane_ids)
        async with self._conn.execute(
            f"SELECT * FROM lane_model_settings WHERE lane IN ({placeholders})", tuple(lane_ids)
        ) as cur:
            rows = await cur.fetchall()
        settings = (_row_to_lane_model_settings(row) for row in rows)
        return {item.lane: item for item in settings}

    # --- triggers -------------------------------------------------------------

    async def add_trigger(self, trigger: Trigger) -> Trigger:
        created = trigger.created_at or self._now()  # the scheduling baseline
        await self._conn.execute(
            "INSERT INTO triggers (id, name, lane_selector, when_spec, action_spec, "
            "guard_spec, enabled, created_at, last_fired_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                trigger.id,
                trigger.name,
                trigger.lane,
                WhenAdapter.dump_json(trigger.when).decode(),
                ActionAdapter.dump_json(trigger.action).decode(),
                trigger.guard.model_dump_json(),
                int(trigger.enabled),
                created.isoformat(),
                trigger.last_fired_at.isoformat() if trigger.last_fired_at else None,
            ),
        )
        await self._conn.commit()
        return trigger.model_copy(update={"created_at": created})

    async def find_trigger(self, trigger_id: str) -> Trigger | None:
        async with self._conn.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)) as cur:
            row = await cur.fetchone()
        return _row_to_trigger(row) if row is not None else None

    async def get_trigger(self, trigger_id: str) -> Trigger:
        trigger = await self.find_trigger(trigger_id)
        if trigger is None:
            raise NotFoundError(f"no trigger {trigger_id!r}")
        return trigger

    async def list_triggers(self) -> list[Trigger]:
        async with self._conn.execute("SELECT * FROM triggers ORDER BY id") as cur:
            rows = await cur.fetchall()
        return [_row_to_trigger(row) for row in rows]

    async def set_trigger_enabled(self, trigger_id: str, enabled: bool) -> None:
        await self._conn.execute(
            "UPDATE triggers SET enabled = ? WHERE id = ?", (int(enabled), trigger_id)
        )
        await self._conn.commit()

    async def set_trigger_fired(self, trigger_id: str, when: datetime) -> None:
        await self._conn.execute(
            "UPDATE triggers SET last_fired_at = ? WHERE id = ?", (when.isoformat(), trigger_id)
        )
        await self._conn.commit()

    async def remove_trigger(self, trigger_id: str) -> bool:
        cur = await self._conn.execute("DELETE FROM triggers WHERE id = ?", (trigger_id,))
        await self._conn.commit()
        return cur.rowcount > 0

    # --- audit log ------------------------------------------------------------

    async def log_action(
        self,
        op: str,
        *,
        lane: str | None = None,
        trigger_id: str | None = None,
        detail: str | None = None,
        outcome: str = "ok",
    ) -> None:
        await self._insert_action_log(
            op, lane=lane, trigger_id=trigger_id, detail=detail, outcome=outcome
        )
        await self._conn.commit()

    async def _insert_action_log(
        self,
        op: str,
        *,
        lane: str | None = None,
        trigger_id: str | None = None,
        detail: str | None = None,
        outcome: str = "ok",
    ) -> None:
        await self._conn.execute(
            "INSERT INTO actions_log (ts, op, lane, trigger_id, detail, outcome) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (self._now().isoformat(), op, lane, trigger_id, detail, outcome),
        )

    async def recent_actions(self, limit: int = 50) -> list[ActionRecord]:
        async with self._conn.execute(
            "SELECT * FROM actions_log ORDER BY id DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
        return [ActionRecord.model_validate(_row_dict(row)) for row in rows]


def _row_dict(row: aiosqlite.Row) -> dict[str, object]:
    # aiosqlite.Row iterates VALUES (not keys), so pair keys with values explicitly.
    return dict(zip(row.keys(), tuple(row), strict=True))


def _bool_or_none(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _json_str_list(value: object) -> list[str]:
    if not value:
        return []
    raw = json.loads(str(value))
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str)]


def _json_service_tiers(value: object) -> list[ServiceTierEntry]:
    if not value:
        return []
    raw = json.loads(str(value))
    if not isinstance(raw, list):
        return []
    return [ServiceTierEntry.model_validate(item) for item in raw if isinstance(item, dict)]


_LANE_SYNC_SELECT = """
SELECT
    src.lane AS lane,
    src.state AS state,
    src.source_path AS source_path,
    src.source_device AS source_device,
    src.source_inode AS source_inode,
    src.source_size AS source_size,
    src.source_mtime_ns AS source_mtime_ns,
    src.line_count AS line_count,
    src.first_offset AS first_offset,
    src.tail_offset AS tail_offset,
    src.last_synced_at AS last_synced_at,
    src.error AS error,
    snap.display_name AS display_name,
    snap.preview AS preview,
    snap.cwd AS cwd,
    snap.source AS source,
    snap.thread_source AS thread_source,
    snap.model_provider AS model_provider,
    snap.model AS model,
    snap.reasoning_effort AS reasoning_effort,
    snap.session_id AS session_id,
    snap.latest_event_at AS latest_event_at,
    snap.latest_turn_id AS latest_turn_id,
    snap.transcript_partial AS transcript_partial
FROM lane_sync_sources src
LEFT JOIN lane_snapshots snap ON snap.lane = src.lane
"""


def _row_to_lane(row: aiosqlite.Row) -> Lane:
    return Lane.model_validate(_row_dict(row))


def _row_to_lane_sync(row: aiosqlite.Row) -> LaneSync:
    data = _row_dict(row)
    data["transcript_partial"] = bool(data["transcript_partial"])
    return LaneSync.model_validate(data)


def _row_to_model_catalog_entry(row: aiosqlite.Row) -> ModelCatalogEntry:
    data = _row_dict(row)
    data["is_default"] = None if data["is_default"] is None else bool(data["is_default"])
    data["hidden"] = None if data["hidden"] is None else bool(data["hidden"])
    data["supported_reasoning_efforts"] = _json_str_list(data["supported_reasoning_efforts"])
    data["service_tiers"] = _json_service_tiers(data["service_tiers"])
    data["additional_speed_tiers"] = _json_str_list(data["additional_speed_tiers"])
    return ModelCatalogEntry.model_validate(data)


def _row_to_lane_model_settings(row: aiosqlite.Row) -> LaneModelSettings:
    return LaneModelSettings.model_validate(_row_dict(row))


def _row_to_trigger(row: aiosqlite.Row) -> Trigger:
    data = _row_dict(row)
    last_fired = data["last_fired_at"]
    created = data["created_at"]
    guard_spec = data["guard_spec"]
    return Trigger(
        id=str(data["id"]),
        name=str(data["name"]),
        lane=str(data["lane_selector"]),
        when=WhenAdapter.validate_json(str(data["when_spec"])),
        action=ActionAdapter.validate_json(str(data["action_spec"])),
        guard=Guard.model_validate_json(str(guard_spec)) if guard_spec else Guard(),
        enabled=bool(data["enabled"]),
        created_at=datetime.fromisoformat(str(created)) if created else None,
        last_fired_at=datetime.fromisoformat(str(last_fired)) if last_fired else None,
    )
