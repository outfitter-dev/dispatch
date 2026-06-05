"""The durable registry store (aiosqlite).

Async end-to-end (never blocks the loop). An injectable clock makes time-stamped
rows deterministic in tests. Holds ``lanes``, ``triggers`` (populated in Phase 3),
and the ``actions_log`` audit of every send/action.
"""

from __future__ import annotations

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
    LaneSource,
    LaneStatus,
    LaneSync,
    QueuedMessage,
    Trigger,
    WhenAdapter,
)

Clock = Callable[[], datetime]
SCHEMA_VERSION = 2


def _utcnow() -> datetime:
    return datetime.now(UTC)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS lanes (
    id TEXT PRIMARY KEY,
    handle TEXT NOT NULL,
    role TEXT,
    cwd TEXT,
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unknown',
    pinned INTEGER NOT NULL DEFAULT 0,
    active_turn_id TEXT,
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
CREATE TABLE IF NOT EXISTS queued_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lane TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error TEXT
);
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
            await store._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        await store._conn.commit()
        return store

    async def close(self) -> None:
        await self._conn.close()

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
        lane = Lane(
            id=id,
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
        lane = Lane(
            id=id,
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
            "INSERT INTO lanes (id, handle, role, cwd, source, status, pinned, active_turn_id, "
            "created_at, updated_at, last_event_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                lane.id,
                lane.handle,
                lane.role,
                lane.cwd,
                lane.source,
                lane.status,
                int(lane.pinned),
                lane.active_turn_id,
                lane.created_at.isoformat(),
                lane.updated_at.isoformat(),
                lane.last_event_at.isoformat() if lane.last_event_at else None,
            ),
        )

    async def find_lane(self, lane_id: str) -> Lane | None:
        async with self._conn.execute("SELECT * FROM lanes WHERE id = ?", (lane_id,)) as cur:
            row = await cur.fetchone()
        return _row_to_lane(row) if row is not None else None

    async def find_lane_by_handle(self, handle: str) -> Lane | None:
        async with self._conn.execute("SELECT * FROM lanes WHERE handle = ?", (handle,)) as cur:
            row = await cur.fetchone()
        return _row_to_lane(row) if row is not None else None

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
        await self._upsert_lane_sync_rows(sync, now)
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
