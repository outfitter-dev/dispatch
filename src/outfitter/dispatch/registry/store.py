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
    QueuedMessage,
    Trigger,
    WhenAdapter,
)

Clock = Callable[[], datetime]
SCHEMA_VERSION = 1


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
        if user_version == 0:
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
                None,
                lane.created_at.isoformat(),
                lane.updated_at.isoformat(),
                None,
            ),
        )
        await self._conn.commit()
        return lane

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
        await self._conn.execute(
            "INSERT INTO actions_log (ts, op, lane, trigger_id, detail, outcome) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (self._now().isoformat(), op, lane, trigger_id, detail, outcome),
        )
        await self._conn.commit()

    async def recent_actions(self, limit: int = 50) -> list[ActionRecord]:
        async with self._conn.execute(
            "SELECT * FROM actions_log ORDER BY id DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
        return [ActionRecord.model_validate(_row_dict(row)) for row in rows]


def _row_dict(row: aiosqlite.Row) -> dict[str, object]:
    # aiosqlite.Row iterates VALUES (not keys), so pair keys with values explicitly.
    return dict(zip(row.keys(), tuple(row), strict=True))


def _row_to_lane(row: aiosqlite.Row) -> Lane:
    return Lane.model_validate(_row_dict(row))


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
