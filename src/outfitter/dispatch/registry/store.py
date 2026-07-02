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
    InboxMessage,
    Lane,
    LaneModelSettings,
    LaneRuntimeSettings,
    LaneRuntimeState,
    LaneSource,
    LaneStatus,
    LaneSync,
    MessageReceipt,
    ModelCatalogEntry,
    ProviderEvent,
    QueuedMessage,
    ServiceTierEntry,
    Subscription,
    ThreadItem,
    ThreadItemRef,
    ThreadTurn,
    Trigger,
    WhenAdapter,
)
from .refs import BASE58BTC_ALPHABET, CODEX_REF_SOURCE, codex_ref_payload, make_ref

Clock = Callable[[], datetime]
SCHEMA_VERSION = 12

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

_PROVIDER_HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    provider_thread_id TEXT NOT NULL,
    lane TEXT,
    event_type TEXT NOT NULL,
    provider_event_id TEXT,
    provider_turn_id TEXT,
    provider_item_id TEXT,
    correlation_id TEXT,
    provider_ts TEXT,
    received_at TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '{}',
    payload TEXT,
    raw_retained INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(lane) REFERENCES lanes(id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_events_provider_event_id
ON provider_events(provider, provider_event_id)
WHERE provider_event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_provider_events_thread_received
ON provider_events(provider, provider_thread_id, received_at);
CREATE INDEX IF NOT EXISTS idx_provider_events_lane_received
ON provider_events(lane, received_at);

CREATE TABLE IF NOT EXISTS thread_turns (
    provider TEXT NOT NULL,
    provider_thread_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    lane TEXT,
    status TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    failed_at TEXT,
    error TEXT,
    completion_source TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(provider, provider_thread_id, turn_id),
    FOREIGN KEY(lane) REFERENCES lanes(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_thread_turns_lane_updated
ON thread_turns(lane, updated_at);

CREATE TABLE IF NOT EXISTS thread_items (
    provider TEXT NOT NULL,
    provider_thread_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    lane TEXT,
    turn_id TEXT,
    item_type TEXT NOT NULL,
    role TEXT,
    text TEXT,
    tool TEXT,
    created_at TEXT,
    position INTEGER,
    inserted_at TEXT NOT NULL,
    payload TEXT,
    raw_retained INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(provider, provider_thread_id, item_id),
    FOREIGN KEY(lane) REFERENCES lanes(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_thread_items_lane_inserted
ON thread_items(lane, position, inserted_at);
CREATE INDEX IF NOT EXISTS idx_thread_items_turn
ON thread_items(provider, provider_thread_id, turn_id);

CREATE TABLE IF NOT EXISTS thread_item_refs (
    provider TEXT NOT NULL,
    provider_thread_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    ref_type TEXT NOT NULL,
    ref_value TEXT NOT NULL,
    PRIMARY KEY(provider, provider_thread_id, item_id, ref_type, ref_value),
    FOREIGN KEY(provider, provider_thread_id, item_id)
        REFERENCES thread_items(provider, provider_thread_id, item_id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_thread_item_refs_lookup
ON thread_item_refs(ref_type, ref_value);

CREATE TABLE IF NOT EXISTS message_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lane TEXT,
    queued_message_id INTEGER,
    provider TEXT NOT NULL,
    provider_thread_id TEXT NOT NULL,
    dispatch_message_id TEXT,
    status TEXT NOT NULL,
    turn_id TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    accepted_at TEXT,
    completed_at TEXT,
    failed_at TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(lane) REFERENCES lanes(id) ON DELETE SET NULL,
    FOREIGN KEY(queued_message_id) REFERENCES queued_messages(id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_message_receipts_dispatch_message_id
ON message_receipts(dispatch_message_id)
WHERE dispatch_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_message_receipts_lane_updated
ON message_receipts(lane, updated_at);

CREATE TABLE IF NOT EXISTS lane_runtime_state (
    lane TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_thread_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unknown',
    active_turn_id TEXT,
    latest_turn_id TEXT,
    latest_turn_status TEXT,
    needs_attention INTEGER NOT NULL DEFAULT 0,
    attention_kind TEXT,
    attention_detail TEXT,
    updated_at TEXT NOT NULL,
    last_event_at TEXT,
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
CREATE TABLE IF NOT EXISTS lane_runtime_settings (
    lane TEXT PRIMARY KEY,
    sandbox TEXT,
    approval_policy TEXT,
    approvals_reviewer TEXT,
    effort TEXT,
    summary TEXT,
    model TEXT,
    service_tier TEXT,
    output_schema TEXT,
    personality TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(lane) REFERENCES lanes(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS inbox_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_lane TEXT NOT NULL,
    source_lane TEXT,
    subscription_id TEXT,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{{}}',
    state TEXT NOT NULL DEFAULT 'pending',
    delivery TEXT NOT NULL DEFAULT 'inbox',
    queued_message_id INTEGER,
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    acked_at TEXT,
    FOREIGN KEY(recipient_lane) REFERENCES lanes(id) ON DELETE CASCADE,
    FOREIGN KEY(source_lane) REFERENCES lanes(id) ON DELETE SET NULL,
    FOREIGN KEY(queued_message_id) REFERENCES queued_messages(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY,
    target_lane TEXT NOT NULL,
    subscriber_lane TEXT NOT NULL,
    when_spec TEXT NOT NULL,
    delivery TEXT NOT NULL,
    deliver_policy TEXT NOT NULL,
    tail INTEGER NOT NULL DEFAULT 1,
    once INTEGER NOT NULL DEFAULT 1,
    ack_policy TEXT NOT NULL DEFAULT 'auto',
    attribution INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_matched_at TEXT,
    last_inbox_message_id INTEGER,
    FOREIGN KEY(target_lane) REFERENCES lanes(id) ON DELETE CASCADE,
    FOREIGN KEY(subscriber_lane) REFERENCES lanes(id) ON DELETE CASCADE,
    FOREIGN KEY(last_inbox_message_id) REFERENCES inbox_messages(id) ON DELETE SET NULL
);
{_PROVIDER_HISTORY_SCHEMA}
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
        if user_version < 7:
            await self._ensure_lane_runtime_settings_table()
        if user_version < 8:
            await self._allow_nullable_lane_runtime_policy()
        if user_version < 9:
            await self._ensure_inbox_subscription_tables()
        if user_version < 10:
            await self._ensure_subscription_attribution_column()
        if user_version < 11:
            await self._ensure_provider_history_tables()
        if user_version < 12:
            await self._ensure_thread_item_position_column()

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

    async def _ensure_lane_runtime_settings_table(self) -> None:
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS lane_runtime_settings (
                lane TEXT PRIMARY KEY,
                sandbox TEXT,
                approval_policy TEXT,
                approvals_reviewer TEXT,
                effort TEXT,
                summary TEXT,
                model TEXT,
                service_tier TEXT,
                output_schema TEXT,
                personality TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(lane) REFERENCES lanes(id) ON DELETE CASCADE
            );
            """
        )

    async def _allow_nullable_lane_runtime_policy(self) -> None:
        async with self._conn.execute("PRAGMA table_info(lane_runtime_settings)") as cur:
            rows = await cur.fetchall()
        policy_columns = {
            str(row["name"]): int(row["notnull"])
            for row in rows
            if str(row["name"]) in {"sandbox", "approval_policy"}
        }
        if policy_columns.get("sandbox") == 0 and policy_columns.get("approval_policy") == 0:
            return
        await self._conn.executescript(
            """
            CREATE TABLE lane_runtime_settings_new (
                lane TEXT PRIMARY KEY,
                sandbox TEXT,
                approval_policy TEXT,
                approvals_reviewer TEXT,
                effort TEXT,
                summary TEXT,
                model TEXT,
                service_tier TEXT,
                output_schema TEXT,
                personality TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(lane) REFERENCES lanes(id) ON DELETE CASCADE
            );
            INSERT INTO lane_runtime_settings_new (
                lane, sandbox, approval_policy, approvals_reviewer, effort, summary, model,
                service_tier, output_schema, personality, updated_at
            )
            SELECT
                lane, sandbox, approval_policy, approvals_reviewer, effort, summary, model,
                service_tier, output_schema, personality, updated_at
            FROM lane_runtime_settings;
            DROP TABLE lane_runtime_settings;
            ALTER TABLE lane_runtime_settings_new RENAME TO lane_runtime_settings;
            """
        )

    async def _ensure_inbox_subscription_tables(self) -> None:
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS inbox_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient_lane TEXT NOT NULL,
                source_lane TEXT,
                subscription_id TEXT,
                kind TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                state TEXT NOT NULL DEFAULT 'pending',
                delivery TEXT NOT NULL DEFAULT 'inbox',
                queued_message_id INTEGER,
                created_at TEXT NOT NULL,
                delivered_at TEXT,
                acked_at TEXT,
                FOREIGN KEY(recipient_lane) REFERENCES lanes(id) ON DELETE CASCADE,
                FOREIGN KEY(source_lane) REFERENCES lanes(id) ON DELETE SET NULL,
                FOREIGN KEY(queued_message_id) REFERENCES queued_messages(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS subscriptions (
                id TEXT PRIMARY KEY,
                target_lane TEXT NOT NULL,
                subscriber_lane TEXT NOT NULL,
                when_spec TEXT NOT NULL,
                delivery TEXT NOT NULL,
                deliver_policy TEXT NOT NULL,
                tail INTEGER NOT NULL DEFAULT 1,
                once INTEGER NOT NULL DEFAULT 1,
                ack_policy TEXT NOT NULL DEFAULT 'auto',
                attribution INTEGER NOT NULL DEFAULT 1,
                state TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_matched_at TEXT,
                last_inbox_message_id INTEGER,
                FOREIGN KEY(target_lane) REFERENCES lanes(id) ON DELETE CASCADE,
                FOREIGN KEY(subscriber_lane) REFERENCES lanes(id) ON DELETE CASCADE,
                FOREIGN KEY(last_inbox_message_id) REFERENCES inbox_messages(id) ON DELETE SET NULL
            );
            """
        )

    async def _ensure_subscription_attribution_column(self) -> None:
        async with self._conn.execute("PRAGMA table_info(subscriptions)") as cur:
            rows = await cur.fetchall()
        columns = {str(row["name"]) for row in rows}
        if "attribution" not in columns:
            await self._conn.execute(
                "ALTER TABLE subscriptions ADD COLUMN attribution INTEGER NOT NULL DEFAULT 1"
            )

    async def _ensure_provider_history_tables(self) -> None:
        await self._conn.executescript(_PROVIDER_HISTORY_SCHEMA)

    async def _ensure_thread_item_position_column(self) -> None:
        async with self._conn.execute("PRAGMA table_info(thread_items)") as cur:
            rows = await cur.fetchall()
        columns = {str(row["name"]) for row in rows}
        if "position" not in columns:
            await self._conn.execute("ALTER TABLE thread_items ADD COLUMN position INTEGER")
        await self._conn.execute("DROP INDEX IF EXISTS idx_thread_items_lane_inserted")
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_thread_items_lane_inserted "
            "ON thread_items(lane, position, inserted_at)"
        )

    async def _prune_orphan_lane_children(self) -> None:
        for table in (
            "lane_sync_sources",
            "lane_snapshots",
            "lane_model_settings",
            "lane_runtime_settings",
            "queued_messages",
            "inbox_messages",
            "subscriptions",
        ):
            lane_column = "lane"
            if table == "inbox_messages":
                lane_column = "recipient_lane"
            elif table == "subscriptions":
                lane_column = "target_lane"
            await self._conn.execute(
                f"""
                DELETE FROM {table}
                WHERE NOT EXISTS (
                    SELECT 1 FROM lanes WHERE lanes.id = {table}.{lane_column}
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

    # --- inbox messages -------------------------------------------------------

    async def add_inbox_message(
        self,
        *,
        recipient_lane: str,
        subject: str,
        body: str,
        source_lane: str | None = None,
        subscription_id: str | None = None,
        kind: str = "system_notice",
        payload: dict[str, object] | None = None,
        delivery: str = "inbox",
    ) -> InboxMessage:
        now = self._now().isoformat()
        cur = await self._conn.execute(
            "INSERT INTO inbox_messages (recipient_lane, source_lane, subscription_id, kind, "
            "subject, body, payload, state, delivery, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (
                recipient_lane,
                source_lane,
                subscription_id,
                kind,
                subject,
                body,
                json.dumps(payload or {}, separators=(",", ":")),
                delivery,
                now,
            ),
        )
        await self._conn.commit()
        message_id = cur.lastrowid
        if message_id is None:
            raise RuntimeError("inbox message insert did not return an id")
        return await self.get_inbox_message(message_id)

    async def get_inbox_message(self, message_id: int) -> InboxMessage:
        async with self._conn.execute(
            "SELECT * FROM inbox_messages WHERE id = ?", (message_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise NotFoundError(f"no inbox message {message_id!r}")
        return _row_to_inbox_message(row)

    async def list_inbox_messages(
        self,
        *,
        lane: str | None = None,
        state: str | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[InboxMessage]:
        clauses: list[str] = []
        params: list[object] = []
        if lane is not None:
            clauses.append("recipient_lane = ?")
            params.append(lane)
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        sql = "SELECT * FROM inbox_messages"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        async with self._conn.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
        return [_row_to_inbox_message(row) for row in rows]

    async def ack_inbox_message(self, message_id: int) -> InboxMessage:
        now = self._now().isoformat()
        cur = await self._conn.execute(
            "UPDATE inbox_messages SET state = 'acked', acked_at = ?, delivered_at = "
            "COALESCE(delivered_at, ?)"
            " WHERE id = ? AND state != 'acked'",
            (now, now, message_id),
        )
        await self._conn.commit()
        if cur.rowcount == 0:
            return await self.get_inbox_message(message_id)
        return await self.get_inbox_message(message_id)

    async def ack_inbox_messages_for_lane(self, lane: str) -> int:
        cur = await self._conn.execute(
            "UPDATE inbox_messages SET state = 'acked', acked_at = ?, delivered_at = "
            "COALESCE(delivered_at, ?) WHERE recipient_lane = ? AND state = 'pending'",
            (self._now().isoformat(), self._now().isoformat(), lane),
        )
        await self._conn.commit()
        return cur.rowcount

    async def mark_inbox_delivered(
        self, message_id: int, *, queued_message_id: int | None = None, ack: bool = False
    ) -> InboxMessage:
        now = self._now().isoformat()
        state = "acked" if ack else "pending"
        await self._conn.execute(
            "UPDATE inbox_messages SET delivered_at = ?, queued_message_id = ?, state = ?, "
            "acked_at = CASE WHEN ? THEN ? ELSE acked_at END WHERE id = ?",
            (now, queued_message_id, state, int(ack), now, message_id),
        )
        await self._conn.commit()
        return await self.get_inbox_message(message_id)

    async def mark_inbox_delivered_for_queue(
        self, queued_message_id: int, *, ack: bool = False
    ) -> int:
        now = self._now().isoformat()
        state = "acked" if ack else "pending"
        cur = await self._conn.execute(
            "UPDATE inbox_messages SET delivered_at = ?, state = ?, "
            "acked_at = CASE WHEN ? THEN ? ELSE acked_at END "
            "WHERE queued_message_id = ?",
            (now, state, int(ack), now, queued_message_id),
        )
        await self._conn.commit()
        return cur.rowcount

    # --- subscriptions --------------------------------------------------------

    async def add_subscription(self, subscription: Subscription) -> Subscription:
        await self._conn.execute(
            "INSERT INTO subscriptions (id, target_lane, subscriber_lane, when_spec, delivery, "
            "deliver_policy, tail, once, ack_policy, attribution, state, created_at, updated_at, "
            "last_matched_at, last_inbox_message_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                subscription.id,
                subscription.target_lane,
                subscription.subscriber_lane,
                subscription.when,
                subscription.delivery,
                subscription.deliver,
                subscription.tail,
                int(subscription.once),
                subscription.ack,
                int(subscription.attribution),
                subscription.state,
                subscription.created_at.isoformat(),
                subscription.updated_at.isoformat(),
                subscription.last_matched_at.isoformat()
                if subscription.last_matched_at is not None
                else None,
                subscription.last_inbox_message_id,
            ),
        )
        await self._conn.commit()
        return await self.get_subscription(subscription.id)

    async def get_subscription(self, subscription_id: str) -> Subscription:
        async with self._conn.execute(
            "SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise NotFoundError(f"no subscription {subscription_id!r}")
        return _row_to_subscription(row)

    async def list_subscriptions(
        self,
        *,
        target_lane: str | None = None,
        subscriber_lane: str | None = None,
        state: str | None = None,
    ) -> list[Subscription]:
        clauses: list[str] = []
        params: list[object] = []
        if target_lane is not None:
            clauses.append("target_lane = ?")
            params.append(target_lane)
        if subscriber_lane is not None:
            clauses.append("subscriber_lane = ?")
            params.append(subscriber_lane)
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        sql = "SELECT * FROM subscriptions"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at, id"
        async with self._conn.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
        return [_row_to_subscription(row) for row in rows]

    async def remove_subscription(self, subscription_id: str) -> bool:
        cur = await self._conn.execute("DELETE FROM subscriptions WHERE id = ?", (subscription_id,))
        await self._conn.commit()
        return cur.rowcount > 0

    async def mark_subscription_matched(
        self, subscription_id: str, *, inbox_message_id: int
    ) -> Subscription:
        now = self._now().isoformat()
        await self._conn.execute(
            "UPDATE subscriptions SET last_matched_at = ?, last_inbox_message_id = ?, "
            "state = CASE WHEN once = 1 THEN 'done' ELSE state END, updated_at = ? WHERE id = ?",
            (now, inbox_message_id, now, subscription_id),
        )
        await self._conn.commit()
        return await self.get_subscription(subscription_id)

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

    # --- lane runtime settings -------------------------------------------------

    async def upsert_lane_runtime_settings(self, settings: LaneRuntimeSettings) -> None:
        await self._conn.execute(
            "INSERT INTO lane_runtime_settings (lane, sandbox, approval_policy, "
            "approvals_reviewer, effort, summary, model, service_tier, output_schema, "
            "personality, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(lane) DO UPDATE SET sandbox = excluded.sandbox, "
            "approval_policy = excluded.approval_policy, "
            "approvals_reviewer = excluded.approvals_reviewer, effort = excluded.effort, "
            "summary = excluded.summary, model = excluded.model, "
            "service_tier = excluded.service_tier, output_schema = excluded.output_schema, "
            "personality = excluded.personality, updated_at = excluded.updated_at",
            (
                settings.lane,
                settings.sandbox,
                settings.approval_policy,
                settings.approvals_reviewer,
                settings.effort,
                settings.summary,
                settings.model,
                settings.service_tier,
                json.dumps(settings.output_schema) if settings.output_schema is not None else None,
                settings.personality,
                settings.updated_at,
            ),
        )
        await self._conn.commit()

    async def get_lane_runtime_settings(self, lane_id: str) -> LaneRuntimeSettings | None:
        async with self._conn.execute(
            "SELECT * FROM lane_runtime_settings WHERE lane = ?", (lane_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_lane_runtime_settings(row) if row is not None else None

    # --- provider events / normalized history ---------------------------------

    async def record_provider_event(self, event: ProviderEvent) -> ProviderEvent:
        payload = _json_dump_compact(event.payload) if event.payload is not None else None
        await self._conn.execute(
            "INSERT INTO provider_events (provider, provider_thread_id, lane, event_type, "
            "provider_event_id, provider_turn_id, provider_item_id, correlation_id, "
            "provider_ts, received_at, summary, payload, raw_retained) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider, provider_event_id) WHERE provider_event_id IS NOT NULL "
            "DO NOTHING",
            (
                event.provider,
                event.provider_thread_id,
                event.lane,
                event.event_type,
                event.provider_event_id,
                event.provider_turn_id,
                event.provider_item_id,
                event.correlation_id,
                event.provider_ts,
                event.received_at,
                json.dumps(event.summary, separators=(",", ":")),
                payload,
                int(event.raw_retained),
            ),
        )
        await self._conn.commit()
        if event.provider_event_id is not None:
            existing = await self.find_provider_event(
                event.provider, provider_event_id=event.provider_event_id
            )
            if existing is None:
                raise RuntimeError("provider event insert did not return a row")
            return existing
        async with self._conn.execute(
            "SELECT * FROM provider_events WHERE id = last_insert_rowid()"
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise RuntimeError("provider event insert did not return a row")
        return _row_to_provider_event(row)

    async def find_provider_event(
        self, provider: str, *, provider_event_id: str
    ) -> ProviderEvent | None:
        async with self._conn.execute(
            "SELECT * FROM provider_events WHERE provider = ? AND provider_event_id = ?",
            (provider, provider_event_id),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_provider_event(row) if row is not None else None

    async def list_provider_events(
        self,
        *,
        lane: str | None = None,
        provider_thread_id: str | None = None,
        limit: int = 50,
    ) -> list[ProviderEvent]:
        clauses: list[str] = []
        params: list[object] = []
        if lane is not None:
            clauses.append("lane = ?")
            params.append(lane)
        if provider_thread_id is not None:
            clauses.append("provider_thread_id = ?")
            params.append(provider_thread_id)
        sql = "SELECT * FROM provider_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        async with self._conn.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
        return [_row_to_provider_event(row) for row in rows]

    async def upsert_thread_turn(self, turn: ThreadTurn) -> ThreadTurn:
        await self._conn.execute(
            "INSERT INTO thread_turns (provider, provider_thread_id, turn_id, lane, status, "
            "started_at, completed_at, failed_at, error, completion_source, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider, provider_thread_id, turn_id) DO UPDATE SET "
            "lane = COALESCE(excluded.lane, thread_turns.lane), "
            "status = CASE WHEN excluded.status = 'unknown' "
            "THEN thread_turns.status ELSE excluded.status END, "
            "started_at = COALESCE(excluded.started_at, thread_turns.started_at), "
            "completed_at = COALESCE(excluded.completed_at, thread_turns.completed_at), "
            "failed_at = COALESCE(excluded.failed_at, thread_turns.failed_at), "
            "error = COALESCE(excluded.error, thread_turns.error), "
            "completion_source = COALESCE(excluded.completion_source, "
            "thread_turns.completion_source), "
            "updated_at = excluded.updated_at",
            (
                turn.provider,
                turn.provider_thread_id,
                turn.turn_id,
                turn.lane,
                turn.status,
                turn.started_at,
                turn.completed_at,
                turn.failed_at,
                turn.error,
                turn.completion_source,
                turn.updated_at,
            ),
        )
        await self._conn.commit()
        return await self.get_thread_turn(turn.provider, turn.provider_thread_id, turn.turn_id)

    async def get_thread_turn(
        self, provider: str, provider_thread_id: str, turn_id: str
    ) -> ThreadTurn:
        async with self._conn.execute(
            "SELECT * FROM thread_turns WHERE provider = ? AND provider_thread_id = ? "
            "AND turn_id = ?",
            (provider, provider_thread_id, turn_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise NotFoundError(f"no thread turn {provider}:{provider_thread_id}:{turn_id}")
        return ThreadTurn.model_validate(_row_dict(row))

    async def list_thread_turns(self, *, lane: str, limit: int = 50) -> list[ThreadTurn]:
        async with self._conn.execute(
            "SELECT * FROM thread_turns WHERE lane = ? ORDER BY updated_at DESC LIMIT ?",
            (lane, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [ThreadTurn.model_validate(_row_dict(row)) for row in rows]

    async def upsert_thread_item(
        self, item: ThreadItem, *, refs: list[ThreadItemRef] | None = None
    ) -> ThreadItem:
        await self._conn.execute("BEGIN")
        try:
            await self._conn.execute(
                "INSERT INTO thread_items (provider, provider_thread_id, item_id, lane, "
                "turn_id, item_type, role, text, tool, created_at, position, inserted_at, "
                "payload, raw_retained) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(provider, provider_thread_id, item_id) DO UPDATE SET "
                "lane = excluded.lane, turn_id = excluded.turn_id, item_type = excluded.item_type, "
                "role = excluded.role, text = excluded.text, tool = excluded.tool, "
                "created_at = excluded.created_at, position = excluded.position, "
                "inserted_at = excluded.inserted_at, payload = excluded.payload, "
                "raw_retained = excluded.raw_retained",
                (
                    item.provider,
                    item.provider_thread_id,
                    item.item_id,
                    item.lane,
                    item.turn_id,
                    item.item_type,
                    item.role,
                    item.text,
                    item.tool,
                    item.created_at,
                    item.position,
                    item.inserted_at,
                    _json_dump_compact(item.payload) if item.payload is not None else None,
                    int(item.raw_retained),
                ),
            )
            if refs is not None:
                await self._conn.execute(
                    "DELETE FROM thread_item_refs WHERE provider = ? AND provider_thread_id = ? "
                    "AND item_id = ?",
                    (item.provider, item.provider_thread_id, item.item_id),
                )
                for ref in refs:
                    await self._conn.execute(
                        "INSERT OR IGNORE INTO thread_item_refs (provider, provider_thread_id, "
                        "item_id, ref_type, ref_value) VALUES (?, ?, ?, ?, ?)",
                        (
                            ref.provider,
                            ref.provider_thread_id,
                            ref.item_id,
                            ref.ref_type,
                            ref.ref_value,
                        ),
                    )
        except Exception:
            await self._conn.rollback()
            raise
        await self._conn.commit()
        return await self.get_thread_item(item.provider, item.provider_thread_id, item.item_id)

    async def get_thread_item(
        self, provider: str, provider_thread_id: str, item_id: str
    ) -> ThreadItem:
        async with self._conn.execute(
            "SELECT * FROM thread_items WHERE provider = ? AND provider_thread_id = ? "
            "AND item_id = ?",
            (provider, provider_thread_id, item_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise NotFoundError(f"no thread item {provider}:{provider_thread_id}:{item_id}")
        return _row_to_thread_item(row)

    async def list_thread_items(
        self, *, lane: str, turn_id: str | None = None, limit: int | None = 50
    ) -> list[ThreadItem]:
        clauses = ["lane = ?"]
        params: list[object] = [lane]
        if turn_id is not None:
            clauses.append("turn_id = ?")
            params.append(turn_id)
        sql = "SELECT * FROM thread_items WHERE " + " AND ".join(clauses)
        sql += " ORDER BY COALESCE(position, -1) DESC, inserted_at DESC, item_id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        async with self._conn.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
        return [_row_to_thread_item(row) for row in rows]

    async def list_thread_item_refs(self, item: ThreadItem) -> list[ThreadItemRef]:
        async with self._conn.execute(
            "SELECT * FROM thread_item_refs WHERE provider = ? AND provider_thread_id = ? "
            "AND item_id = ? ORDER BY ref_type, ref_value",
            (item.provider, item.provider_thread_id, item.item_id),
        ) as cur:
            rows = await cur.fetchall()
        return [ThreadItemRef.model_validate(_row_dict(row)) for row in rows]

    async def prune_thread_history_snapshot(
        self,
        *,
        provider: str,
        provider_thread_id: str,
        turn_ids: set[str],
        item_ids: set[str],
    ) -> None:
        await self._delete_missing_values(
            "thread_items",
            provider=provider,
            provider_thread_id=provider_thread_id,
            id_column="item_id",
            keep_ids=item_ids,
        )
        await self._delete_missing_values(
            "thread_turns",
            provider=provider,
            provider_thread_id=provider_thread_id,
            id_column="turn_id",
            keep_ids=turn_ids,
        )
        await self._conn.commit()

    async def _delete_missing_values(
        self,
        table: str,
        *,
        provider: str,
        provider_thread_id: str,
        id_column: str,
        keep_ids: set[str],
    ) -> None:
        params: list[object] = [provider, provider_thread_id]
        sql = f"DELETE FROM {table} WHERE provider = ? AND provider_thread_id = ?"
        if keep_ids:
            placeholders = ", ".join("?" for _ in keep_ids)
            sql += f" AND {id_column} NOT IN ({placeholders})"
            params.extend(sorted(keep_ids))
        await self._conn.execute(sql, tuple(params))

    async def upsert_message_receipt(self, receipt: MessageReceipt) -> MessageReceipt:
        await self._conn.execute(
            "INSERT INTO message_receipts (id, lane, queued_message_id, provider, "
            "provider_thread_id, dispatch_message_id, status, turn_id, error, created_at, "
            "sent_at, accepted_at, completed_at, failed_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(dispatch_message_id) WHERE dispatch_message_id IS NOT NULL "
            "DO UPDATE SET lane = excluded.lane, queued_message_id = excluded.queued_message_id, "
            "provider = excluded.provider, provider_thread_id = excluded.provider_thread_id, "
            "status = excluded.status, turn_id = excluded.turn_id, error = excluded.error, "
            "sent_at = excluded.sent_at, accepted_at = excluded.accepted_at, "
            "completed_at = excluded.completed_at, failed_at = excluded.failed_at, "
            "updated_at = excluded.updated_at",
            (
                receipt.id,
                receipt.lane,
                receipt.queued_message_id,
                receipt.provider,
                receipt.provider_thread_id,
                receipt.dispatch_message_id,
                receipt.status,
                receipt.turn_id,
                receipt.error,
                receipt.created_at,
                receipt.sent_at,
                receipt.accepted_at,
                receipt.completed_at,
                receipt.failed_at,
                receipt.updated_at,
            ),
        )
        await self._conn.commit()
        if receipt.dispatch_message_id is not None:
            got = await self.find_message_receipt(
                provider=receipt.provider, dispatch_message_id=receipt.dispatch_message_id
            )
            if got is None:
                raise RuntimeError("message receipt upsert did not return a row")
            return got
        async with self._conn.execute(
            "SELECT * FROM message_receipts WHERE id = last_insert_rowid()"
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise RuntimeError("message receipt upsert did not return a row")
        return MessageReceipt.model_validate(_row_dict(row))

    async def find_message_receipt(
        self, *, provider: str, dispatch_message_id: str
    ) -> MessageReceipt | None:
        async with self._conn.execute(
            "SELECT * FROM message_receipts WHERE provider = ? AND dispatch_message_id = ?",
            (provider, dispatch_message_id),
        ) as cur:
            row = await cur.fetchone()
        return MessageReceipt.model_validate(_row_dict(row)) if row is not None else None

    async def list_message_receipts(self, *, lane: str, limit: int = 50) -> list[MessageReceipt]:
        async with self._conn.execute(
            "SELECT * FROM message_receipts WHERE lane = ? ORDER BY updated_at DESC LIMIT ?",
            (lane, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [MessageReceipt.model_validate(_row_dict(row)) for row in rows]

    async def upsert_lane_runtime_state(self, state: LaneRuntimeState) -> LaneRuntimeState:
        await self._conn.execute(
            "INSERT INTO lane_runtime_state (lane, provider, provider_thread_id, status, "
            "active_turn_id, latest_turn_id, latest_turn_status, needs_attention, "
            "attention_kind, attention_detail, updated_at, last_event_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(lane) DO UPDATE SET provider = excluded.provider, "
            "provider_thread_id = excluded.provider_thread_id, status = excluded.status, "
            "active_turn_id = excluded.active_turn_id, latest_turn_id = excluded.latest_turn_id, "
            "latest_turn_status = excluded.latest_turn_status, "
            "needs_attention = excluded.needs_attention, attention_kind = excluded.attention_kind, "
            "attention_detail = excluded.attention_detail, updated_at = excluded.updated_at, "
            "last_event_at = excluded.last_event_at",
            (
                state.lane,
                state.provider,
                state.provider_thread_id,
                state.status,
                state.active_turn_id,
                state.latest_turn_id,
                state.latest_turn_status,
                int(state.needs_attention),
                state.attention_kind,
                state.attention_detail,
                state.updated_at,
                state.last_event_at,
            ),
        )
        await self._conn.commit()
        got = await self.get_lane_runtime_state(state.lane)
        if got is None:
            raise RuntimeError("lane runtime state upsert did not return a row")
        return got

    async def get_lane_runtime_state(self, lane_id: str) -> LaneRuntimeState | None:
        async with self._conn.execute(
            "SELECT * FROM lane_runtime_state WHERE lane = ?", (lane_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_lane_runtime_state(row) if row is not None else None

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


def _json_dump_compact(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def _json_str_list(value: object) -> list[str]:
    if not value:
        return []
    raw = json.loads(str(value))
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str)]


def _json_dict(value: object) -> dict[str, object]:
    if not value:
        return {}
    raw = json.loads(str(value))
    return raw if isinstance(raw, dict) else {}


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


def _row_to_lane_runtime_settings(row: aiosqlite.Row) -> LaneRuntimeSettings:
    data = _row_dict(row)
    raw_schema = data["output_schema"]
    data["output_schema"] = json.loads(str(raw_schema)) if raw_schema else None
    return LaneRuntimeSettings.model_validate(data)


def _row_to_provider_event(row: aiosqlite.Row) -> ProviderEvent:
    data = _row_dict(row)
    data["summary"] = _json_dict(data["summary"])
    payload = data["payload"]
    data["payload"] = json.loads(str(payload)) if payload else None
    data["raw_retained"] = bool(data["raw_retained"])
    return ProviderEvent.model_validate(data)


def _row_to_thread_item(row: aiosqlite.Row) -> ThreadItem:
    data = _row_dict(row)
    payload = data["payload"]
    data["payload"] = json.loads(str(payload)) if payload else None
    data["raw_retained"] = bool(data["raw_retained"])
    return ThreadItem.model_validate(data)


def _row_to_lane_runtime_state(row: aiosqlite.Row) -> LaneRuntimeState:
    data = _row_dict(row)
    data["needs_attention"] = bool(data["needs_attention"])
    return LaneRuntimeState.model_validate(data)


def _row_to_inbox_message(row: aiosqlite.Row) -> InboxMessage:
    data = _row_dict(row)
    data["payload"] = _json_dict(data["payload"])
    return InboxMessage.model_validate(data)


def _row_to_subscription(row: aiosqlite.Row) -> Subscription:
    data = _row_dict(row)
    data["when"] = data.pop("when_spec")
    data["deliver"] = data.pop("deliver_policy")
    data["ack"] = data.pop("ack_policy")
    data["once"] = bool(data["once"])
    data["attribution"] = bool(data["attribution"])
    return Subscription.model_validate(data)


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
