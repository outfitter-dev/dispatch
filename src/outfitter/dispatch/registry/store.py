"""The durable registry store (aiosqlite).

Async end-to-end (never blocks the loop). An injectable clock makes time-stamped
rows deterministic in tests. Holds ``lanes``, ``triggers`` (populated in Phase 3),
and the ``actions_log`` audit of every send/action.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import aiosqlite

from outfitter.dispatch.contracts.errors import NotFoundError

from .models import (
    SERVER_REQUEST_TEXT_LIMIT,
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
    ProviderCapacityObservation,
    ProviderEvent,
    ProviderThread,
    ProviderThreadLifecycleState,
    ProviderThreadNode,
    ProviderThreadObservation,
    QueuedMessage,
    ServerRequest,
    ServerRequestOutcome,
    ServerRequestState,
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
SCHEMA_VERSION = 17


@dataclass(frozen=True)
class ThreadHistoryFileStat:
    path: str
    count: int


@dataclass(frozen=True)
class ThreadHistoryToolStat:
    tool: str
    count: int
    item_types: list[str]


@dataclass(frozen=True)
class ThreadHistorySummaryStats:
    turns: int
    items: int
    messages: int
    tool_calls: int
    transcript_bytes: int | None
    first_event_at: str | None
    last_event_at: str | None
    tools: list[ThreadHistoryToolStat]
    files_changed_count: int
    files: list[ThreadHistoryFileStat]
    child_thread_ids: list[str]


@dataclass(frozen=True)
class ServerRequestObservation:
    request: ServerRequest
    inserted: bool


@dataclass(frozen=True)
class ProviderThreadTopology:
    """Bounded parent/fork topology around one or more provider threads."""

    provider: str
    requested_thread_ids: list[str]
    nodes: list[ProviderThreadNode]
    roots: dict[str, ProviderThreadNode | None]
    parent_ancestry: dict[str, list[ProviderThreadNode]]
    children: dict[str, list[ProviderThreadNode]]
    descendants: dict[str, list[ProviderThreadNode]]
    fork_origins: dict[str, ProviderThreadNode | None]
    forks: dict[str, list[ProviderThreadNode]]
    missing_thread_ids: list[str]
    cycle_detected: bool
    complete: bool
    truncated: bool


ThreadItemIdentity = tuple[str, str, str]


def _thread_item_identity(item: ThreadItem | ThreadItemRef) -> ThreadItemIdentity:
    return (item.provider, item.provider_thread_id, item.item_id)


def _ref_exists_sql(ref_type: str, *, operator: str = "instr", exact: bool = False) -> str:
    value_predicate = (
        "refs.ref_value = ?"
        if exact
        else (
            "lower(refs.ref_value) LIKE lower(?)"
            if operator == "LIKE"
            else "instr(lower(refs.ref_value), lower(?)) > 0"
        )
    )
    return (
        "EXISTS (SELECT 1 FROM thread_item_refs refs "
        "WHERE refs.provider = items.provider "
        "AND refs.provider_thread_id = items.provider_thread_id "
        "AND refs.item_id = items.item_id "
        "AND refs.ref_type = "
        f"{json.dumps(ref_type)} "
        f"AND {value_predicate})"
    )


def _path_prefix(path: str) -> str:
    normalized = path.rstrip("/")
    return f"{normalized}/" if normalized else path


def _ref_path_under_sql() -> str:
    return (
        "EXISTS (SELECT 1 FROM thread_item_refs refs "
        "WHERE refs.provider = items.provider "
        "AND refs.provider_thread_id = items.provider_thread_id "
        "AND refs.item_id = items.item_id "
        "AND refs.ref_type = 'file' "
        "AND (refs.ref_value = ? OR lower(refs.ref_value) LIKE lower(?)))"
    )


def _extension_suffix(ext: str) -> str:
    return ext if ext.startswith(".") else f".{ext}"


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

_PROVIDER_THREADS_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_threads (
    provider TEXT NOT NULL,
    provider_thread_id TEXT NOT NULL,
    session_id TEXT,
    parent_thread_id TEXT,
    forked_from_id TEXT,
    source_kind TEXT,
    thread_source TEXT,
    agent_nickname TEXT,
    agent_role TEXT,
    agent_depth INTEGER,
    lifecycle_state TEXT NOT NULL DEFAULT 'unknown'
        CHECK(lifecycle_state IN ('active', 'archived', 'deleted', 'unknown')),
    relationship_source TEXT,
    confidence REAL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    archived_at TEXT,
    deleted_at TEXT,
    PRIMARY KEY(provider, provider_thread_id)
);
CREATE INDEX IF NOT EXISTS idx_provider_threads_parent
ON provider_threads(provider, parent_thread_id);
CREATE INDEX IF NOT EXISTS idx_provider_threads_fork
ON provider_threads(provider, forked_from_id);
"""

_PROVIDER_CAPACITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_capacity_observations (
    provider TEXT NOT NULL,
    host_scope TEXT NOT NULL,
    config_scope TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN (
        'ready', 'partial', 'signed_out', 'unsupported', 'unavailable', 'disabled'
    )),
    account_type TEXT,
    account_fingerprint TEXT,
    account_label TEXT,
    plan TEXT,
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    confidence REAL NOT NULL,
    payload TEXT NOT NULL,
    error TEXT,
    PRIMARY KEY(provider, host_scope, config_scope)
);
CREATE INDEX IF NOT EXISTS idx_provider_capacity_state
ON provider_capacity_observations(provider, state, observed_at);
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
    phase TEXT,
    status TEXT,
    text TEXT,
    tool TEXT,
    server TEXT,
    command TEXT,
    cwd TEXT,
    error TEXT,
    duration_ms INTEGER,
    arguments TEXT,
    success INTEGER,
    agent_nickname TEXT,
    agent_role TEXT,
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

_SERVER_REQUESTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS server_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL CHECK (provider = 'codex'),
    provider_session_id TEXT NOT NULL,
    provider_thread_id TEXT,
    provider_thread_key TEXT NOT NULL,
    request_id_json TEXT NOT NULL,
    lane TEXT,
    method TEXT NOT NULL,
    category TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'responding', 'responded', 'denied', 'timed_out', 'failed')),
    received_at TEXT NOT NULL,
    deadline_at TEXT,
    resolved_at TEXT,
    response_summary TEXT,
    error TEXT,
    UNIQUE(provider, provider_session_id, provider_thread_key, request_id_json),
    FOREIGN KEY(lane) REFERENCES lanes(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_server_requests_pending
ON server_requests(provider, provider_session_id, state, deadline_at, received_at);
CREATE INDEX IF NOT EXISTS idx_server_requests_lane_pending
ON server_requests(lane, provider_session_id, state, received_at);
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
{_PROVIDER_THREADS_SCHEMA}
{_PROVIDER_CAPACITY_SCHEMA}
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
    input_modalities TEXT NOT NULL DEFAULT '[]',
    supports_personality INTEGER,
    upgrade TEXT,
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
{_SERVER_REQUESTS_SCHEMA}
"""

REGISTRY_SCHEMA_SQL = _SCHEMA


class Registry:
    """The lane/trigger/audit store."""

    def __init__(self, conn: aiosqlite.Connection, now: Clock) -> None:
        self._conn = conn
        self._now = now
        self._write_lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: str | Path = ":memory:", now: Clock = _utcnow) -> Registry:
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA busy_timeout = 5000")
        if str(path) != ":memory:":
            await conn.execute("PRAGMA journal_mode = WAL")
            await conn.execute("PRAGMA synchronous = NORMAL")
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

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[None]:
        async with self._write_lock:
            await self._conn.execute("BEGIN")
            try:
                yield
            except BaseException:
                await self._conn.rollback()
                raise
            await self._conn.commit()

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
        if user_version < 13:
            await self._ensure_model_catalog_capability_columns()
        if user_version < 14:
            await self._ensure_server_requests_table()
        if user_version < 15:
            await self._ensure_thread_item_canonical_columns()
        if user_version < 16:
            await self._ensure_provider_threads_table()
        if user_version < 17:
            await self._ensure_provider_capacity_table()

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
                input_modalities TEXT NOT NULL DEFAULT '[]',
                supports_personality INTEGER,
                upgrade TEXT,
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

    async def _ensure_model_catalog_capability_columns(self) -> None:
        async with self._conn.execute("PRAGMA table_info(model_catalog)") as cur:
            rows = await cur.fetchall()
        columns = {str(row["name"]) for row in rows}
        column_defs = {
            "input_modalities": "TEXT NOT NULL DEFAULT '[]'",
            "supports_personality": "INTEGER",
            "upgrade": "TEXT",
        }
        for name, definition in column_defs.items():
            if name not in columns:
                await self._conn.execute(
                    f"ALTER TABLE model_catalog ADD COLUMN {name} {definition}"
                )

    async def _ensure_server_requests_table(self) -> None:
        await self._conn.executescript(_SERVER_REQUESTS_SCHEMA)

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

    async def _ensure_provider_threads_table(self) -> None:
        await self._conn.executescript(_PROVIDER_THREADS_SCHEMA)

    async def _ensure_provider_capacity_table(self) -> None:
        await self._conn.executescript(_PROVIDER_CAPACITY_SCHEMA)

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

    async def _ensure_thread_item_canonical_columns(self) -> None:
        async with self._conn.execute("PRAGMA table_info(thread_items)") as cur:
            rows = await cur.fetchall()
        columns = {str(row["name"]) for row in rows}
        column_defs = {
            "phase": "TEXT",
            "status": "TEXT",
            "server": "TEXT",
            "command": "TEXT",
            "cwd": "TEXT",
            "error": "TEXT",
            "duration_ms": "INTEGER",
            "arguments": "TEXT",
            "success": "INTEGER",
            "agent_nickname": "TEXT",
            "agent_role": "TEXT",
        }
        for name, definition in column_defs.items():
            if name not in columns:
                await self._conn.execute(f"ALTER TABLE thread_items ADD COLUMN {name} {definition}")

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
        async with self._transaction():
            await self._insert_lane(lane)
            await self._upsert_lane_sync_rows(sync, synced_at)
            if audit_op is not None:
                await self._insert_action_log(audit_op, lane=lane.id, detail=audit_detail)
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
        async with self._transaction():
            await self._upsert_lane_sync_rows(sync, now)
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
                "input_modalities, supports_personality, upgrade, last_seen_at, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(provider, id) DO UPDATE SET display_name = excluded.display_name, "
                "description = excluded.description, is_default = excluded.is_default, "
                "hidden = excluded.hidden, "
                "default_reasoning_effort = excluded.default_reasoning_effort, "
                "supported_reasoning_efforts = excluded.supported_reasoning_efforts, "
                "default_service_tier = excluded.default_service_tier, "
                "service_tiers = excluded.service_tiers, "
                "additional_speed_tiers = excluded.additional_speed_tiers, "
                "input_modalities = excluded.input_modalities, "
                "supports_personality = excluded.supports_personality, "
                "upgrade = excluded.upgrade, "
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
                    json.dumps(model.input_modalities),
                    _bool_or_none(model.supports_personality),
                    model.upgrade,
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

    # --- provider threads / topology -----------------------------------------

    async def upsert_provider_thread(
        self, observation: ProviderThreadObservation
    ) -> ProviderThread:
        """Persist non-null metadata without implicitly changing lifecycle state."""

        return (await self.upsert_provider_threads([observation]))[0]

    async def upsert_provider_threads(
        self, observations: list[ProviderThreadObservation]
    ) -> list[ProviderThread]:
        """Persist a discovery page in one serialized transaction."""

        if not observations:
            return []
        async with self._write_lock:
            try:
                for observation in observations:
                    await self._upsert_provider_thread_row(observation)
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise
        saved: list[ProviderThread] = []
        for observation in observations:
            thread = await self.get_provider_thread(
                observation.provider, observation.provider_thread_id
            )
            if thread is None:
                raise RuntimeError("provider thread upsert did not return a row")
            saved.append(thread)
        return saved

    async def _upsert_provider_thread_row(self, observation: ProviderThreadObservation) -> None:

        observed_at = observation.observed_at or self.now_iso()
        lifecycle_state = observation.lifecycle_state or "unknown"
        lifecycle_explicit = observation.lifecycle_state is not None
        archived_at = observed_at if lifecycle_state == "archived" else None
        deleted_at = observed_at if lifecycle_state == "deleted" else None
        await self._conn.execute(
            "INSERT INTO provider_threads (provider, provider_thread_id, session_id, "
            "parent_thread_id, forked_from_id, source_kind, thread_source, agent_nickname, "
            "agent_role, agent_depth, lifecycle_state, relationship_source, confidence, "
            "first_seen_at, last_seen_at, archived_at, deleted_at) VALUES ("
            "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider, provider_thread_id) DO UPDATE SET "
            "session_id = COALESCE(excluded.session_id, provider_threads.session_id), "
            "parent_thread_id = COALESCE(excluded.parent_thread_id, "
            "provider_threads.parent_thread_id), "
            "forked_from_id = COALESCE(excluded.forked_from_id, provider_threads.forked_from_id), "
            "source_kind = COALESCE(excluded.source_kind, provider_threads.source_kind), "
            "thread_source = COALESCE(excluded.thread_source, provider_threads.thread_source), "
            "agent_nickname = COALESCE(excluded.agent_nickname, "
            "provider_threads.agent_nickname), "
            "agent_role = COALESCE(excluded.agent_role, provider_threads.agent_role), "
            "agent_depth = COALESCE(excluded.agent_depth, provider_threads.agent_depth), "
            "lifecycle_state = CASE WHEN ? THEN excluded.lifecycle_state "
            "ELSE provider_threads.lifecycle_state END, "
            "relationship_source = COALESCE(excluded.relationship_source, "
            "provider_threads.relationship_source), "
            "confidence = COALESCE(excluded.confidence, provider_threads.confidence), "
            "last_seen_at = excluded.last_seen_at, "
            "archived_at = CASE WHEN ? AND excluded.lifecycle_state = 'archived' "
            "THEN COALESCE(provider_threads.archived_at, excluded.archived_at) "
            "ELSE provider_threads.archived_at END, "
            "deleted_at = CASE WHEN ? AND excluded.lifecycle_state = 'deleted' "
            "THEN COALESCE(provider_threads.deleted_at, excluded.deleted_at) "
            "ELSE provider_threads.deleted_at END",
            (
                observation.provider,
                observation.provider_thread_id,
                observation.session_id,
                observation.parent_thread_id,
                observation.forked_from_id,
                observation.source_kind,
                observation.thread_source,
                observation.agent_nickname,
                observation.agent_role,
                observation.agent_depth,
                lifecycle_state,
                observation.relationship_source,
                observation.confidence,
                observed_at,
                observed_at,
                archived_at,
                deleted_at,
                lifecycle_explicit,
                lifecycle_explicit,
                lifecycle_explicit,
            ),
        )

    async def get_provider_thread(
        self, provider: str, provider_thread_id: str
    ) -> ProviderThread | None:
        async with self._conn.execute(
            "SELECT * FROM provider_threads WHERE provider = ? AND provider_thread_id = ?",
            (provider, provider_thread_id),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_provider_thread(row) if row is not None else None

    async def list_provider_threads(
        self,
        *,
        provider: str | None = None,
        lifecycle_state: ProviderThreadLifecycleState | None = None,
    ) -> list[ProviderThread]:
        clauses: list[str] = []
        params: list[str] = []
        if provider is not None:
            clauses.append("provider = ?")
            params.append(provider)
        if lifecycle_state is not None:
            clauses.append("lifecycle_state = ?")
            params.append(lifecycle_state)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        async with self._conn.execute(
            "SELECT * FROM provider_threads"
            f"{where} ORDER BY provider, first_seen_at, provider_thread_id",
            tuple(params),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_provider_thread(row) for row in rows]

    async def mark_provider_thread_state(
        self,
        provider: str,
        provider_thread_id: str,
        lifecycle_state: ProviderThreadLifecycleState,
        *,
        observed_at: str | None = None,
    ) -> ProviderThread:
        return await self.upsert_provider_thread(
            ProviderThreadObservation(
                provider=provider,
                provider_thread_id=provider_thread_id,
                lifecycle_state=lifecycle_state,
                observed_at=observed_at,
            )
        )

    async def get_provider_thread_topology(
        self,
        provider: str,
        provider_thread_ids: str | list[str],
        *,
        max_nodes: int = 200,
        max_depth: int = 16,
    ) -> ProviderThreadTopology:
        """Return bounded parent and fork relationships without repairing bad edges."""

        if max_nodes < 1:
            raise ValueError("max_nodes must be at least 1")
        if max_depth < 0:
            raise ValueError("max_depth must be non-negative")
        requested = (
            [provider_thread_ids] if isinstance(provider_thread_ids, str) else provider_thread_ids
        )
        requested = sorted(set(requested))
        if not requested:
            raise ValueError("at least one provider thread id is required")

        nodes: dict[str, ProviderThreadNode] = {}
        missing: set[str] = set()
        cycle_detected = False
        truncated = False

        async def load(thread_ids: list[str]) -> None:
            nonlocal truncated
            candidates = sorted({thread_id for thread_id in thread_ids if thread_id not in nodes})
            candidates = [thread_id for thread_id in candidates if thread_id not in missing]
            remaining = max_nodes - len(nodes)
            if len(candidates) > remaining:
                candidates = candidates[:remaining]
                truncated = True
            if not candidates:
                return
            fetched = await self._get_provider_thread_nodes(provider, candidates)
            nodes.update(fetched)
            missing.update(set(candidates) - set(fetched))

        async def related_ids(column: str, thread_id: str) -> list[str]:
            if column not in {"parent_thread_id", "forked_from_id"}:
                raise ValueError(f"unsupported provider thread relation {column!r}")
            async with self._conn.execute(
                "SELECT provider_thread_id FROM provider_threads "
                f"WHERE provider = ? AND {column} = ? ORDER BY provider_thread_id",
                (provider, thread_id),
            ) as cur:
                rows = await cur.fetchall()
            return [str(row["provider_thread_id"]) for row in rows]

        await load(requested)
        roots: dict[str, ProviderThreadNode | None] = {}
        parent_ancestry: dict[str, list[ProviderThreadNode]] = {}
        for thread_id in requested:
            node = nodes.get(thread_id)
            ancestry: list[ProviderThreadNode] = []
            parent_ancestry[thread_id] = ancestry
            if node is None:
                roots[thread_id] = None
                continue
            current = node
            visited = {thread_id}
            for depth in range(max_depth + 1):
                parent_id = current.thread.parent_thread_id
                if parent_id is None:
                    roots[thread_id] = current
                    break
                if depth >= max_depth:
                    truncated = True
                    roots[thread_id] = None
                    break
                if parent_id in visited:
                    cycle_detected = True
                    roots[thread_id] = None
                    break
                await load([parent_id])
                parent = nodes.get(parent_id)
                if parent is None:
                    roots[thread_id] = None
                    break
                ancestry.append(parent)
                visited.add(parent_id)
                current = parent
            else:
                truncated = True
                roots[thread_id] = None

        children: dict[str, list[ProviderThreadNode]] = {}
        descendants: dict[str, list[ProviderThreadNode]] = {}
        for thread_id in requested:
            direct_ids = await related_ids("parent_thread_id", thread_id)
            direct: list[ProviderThreadNode] = []
            children[thread_id] = direct
            descendant_nodes: list[ProviderThreadNode] = []
            descendants[thread_id] = descendant_nodes
            if max_depth < 1:
                if direct_ids:
                    truncated = True
                continue
            await load(direct_ids)
            direct.extend(nodes[child_id] for child_id in direct_ids if child_id in nodes)
            visited = {thread_id}
            queue: list[tuple[str, int]] = []
            for child_id in direct_ids:
                if child_id == thread_id:
                    cycle_detected = True
                    continue
                child = nodes.get(child_id)
                if child is not None and child_id not in visited:
                    visited.add(child_id)
                    descendant_nodes.append(child)
                    queue.append((child_id, 1))
            while queue:
                current_id, depth = queue.pop(0)
                child_ids = await related_ids("parent_thread_id", current_id)
                if depth >= max_depth:
                    if child_ids:
                        truncated = True
                    continue
                await load(child_ids)
                for child_id in child_ids:
                    if child_id in visited:
                        cycle_detected = True
                        continue
                    child = nodes.get(child_id)
                    if child is not None:
                        visited.add(child_id)
                        descendant_nodes.append(child)
                        queue.append((child_id, depth + 1))

        fork_origins: dict[str, ProviderThreadNode | None] = {}
        forks: dict[str, list[ProviderThreadNode]] = {}
        for thread_id in requested:
            node = nodes.get(thread_id)
            origin: ProviderThreadNode | None = None
            if node is not None and node.thread.forked_from_id is not None:
                if max_depth < 1:
                    truncated = True
                else:
                    origin_id = node.thread.forked_from_id
                    await load([origin_id])
                    origin = nodes.get(origin_id)
                    if origin_id == thread_id or (
                        origin is not None and origin.thread.forked_from_id == thread_id
                    ):
                        cycle_detected = True
            fork_origins[thread_id] = origin
            fork_ids = await related_ids("forked_from_id", thread_id)
            if max_depth < 1:
                if fork_ids:
                    truncated = True
                forks[thread_id] = []
                continue
            await load(fork_ids)
            forks[thread_id] = [nodes[fork_id] for fork_id in fork_ids if fork_id in nodes]
            if thread_id in fork_ids:
                cycle_detected = True

        complete = not (missing or cycle_detected or truncated)
        return ProviderThreadTopology(
            provider=provider,
            requested_thread_ids=requested,
            nodes=sorted(nodes.values(), key=lambda node: node.thread.provider_thread_id),
            roots=roots,
            parent_ancestry=parent_ancestry,
            children=children,
            descendants=descendants,
            fork_origins=fork_origins,
            forks=forks,
            missing_thread_ids=sorted(missing),
            cycle_detected=cycle_detected,
            complete=complete,
            truncated=truncated,
        )

    async def _get_provider_thread_nodes(
        self, provider: str, provider_thread_ids: list[str]
    ) -> dict[str, ProviderThreadNode]:
        if not provider_thread_ids:
            return {}
        placeholders = ", ".join("?" for _ in provider_thread_ids)
        async with self._conn.execute(
            "SELECT provider_threads.*, lanes.id AS lane_id, lanes.ref, lanes.handle, "
            "lanes.status AS lane_status FROM provider_threads "
            "LEFT JOIN lanes ON lanes.id = provider_threads.provider_thread_id "
            "WHERE provider_threads.provider = ? "
            f"AND provider_threads.provider_thread_id IN ({placeholders}) "
            "ORDER BY provider_threads.provider_thread_id",
            (provider, *provider_thread_ids),
        ) as cur:
            rows = await cur.fetchall()
        result: dict[str, ProviderThreadNode] = {}
        for row in rows:
            node = _row_to_provider_thread_node(row)
            result[node.thread.provider_thread_id] = node
        return result

    # --- provider account / capacity observations ----------------------------

    async def upsert_provider_capacity_observation(
        self, observation: ProviderCapacityObservation
    ) -> ProviderCapacityObservation:
        payload = _json_dump_compact(observation.model_dump(mode="json"))
        async with self._write_lock:
            try:
                await self._conn.execute(
                    "INSERT INTO provider_capacity_observations (provider, host_scope, "
                    "config_scope, state, account_type, account_fingerprint, account_label, "
                    "plan, source, observed_at, confidence, payload, error) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(provider, host_scope, config_scope) DO UPDATE SET "
                    "state = excluded.state, account_type = excluded.account_type, "
                    "account_fingerprint = excluded.account_fingerprint, "
                    "account_label = excluded.account_label, plan = excluded.plan, "
                    "source = excluded.source, observed_at = excluded.observed_at, "
                    "confidence = excluded.confidence, payload = excluded.payload, "
                    "error = excluded.error",
                    (
                        observation.provider,
                        observation.host_scope,
                        observation.config_scope,
                        observation.state,
                        observation.account_type,
                        observation.account_fingerprint,
                        observation.account_label,
                        observation.plan,
                        _json_dump_compact(observation.source),
                        observation.observed_at,
                        observation.confidence,
                        payload,
                        observation.error,
                    ),
                )
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise
        saved = await self.get_provider_capacity_observation(
            observation.provider,
            host_scope=observation.host_scope,
            config_scope=observation.config_scope,
        )
        if saved is None:
            raise RuntimeError("provider capacity upsert did not return a row")
        return saved

    async def get_provider_capacity_observation(
        self,
        provider: str,
        *,
        host_scope: str = "local",
        config_scope: str = "default",
    ) -> ProviderCapacityObservation | None:
        async with self._conn.execute(
            "SELECT payload FROM provider_capacity_observations "
            "WHERE provider = ? AND host_scope = ? AND config_scope = ?",
            (provider, host_scope, config_scope),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return ProviderCapacityObservation.model_validate_json(str(row["payload"]))

    async def list_provider_capacity_observations(
        self,
        *,
        provider: str | None = None,
        host_scope: str | None = None,
    ) -> list[ProviderCapacityObservation]:
        clauses: list[str] = []
        params: list[str] = []
        if provider is not None:
            clauses.append("provider = ?")
            params.append(provider)
        if host_scope is not None:
            clauses.append("host_scope = ?")
            params.append(host_scope)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        async with self._conn.execute(
            "SELECT payload FROM provider_capacity_observations"
            f"{where} ORDER BY provider, host_scope, config_scope",
            tuple(params),
        ) as cur:
            rows = await cur.fetchall()
        return [
            ProviderCapacityObservation.model_validate_json(str(row["payload"])) for row in rows
        ]

    # --- provider events / normalized history ---------------------------------

    async def record_provider_event(self, event: ProviderEvent) -> ProviderEvent:
        payload = _json_dump_compact(event.payload) if event.payload is not None else None
        async with self._write_lock:
            await self._conn.execute(
                "INSERT INTO provider_events (provider, provider_thread_id, lane, event_type, "
                "provider_event_id, provider_turn_id, provider_item_id, correlation_id, "
                "provider_ts, received_at, summary, payload, raw_retained) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT DO NOTHING",
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
            if event.provider_event_id is None:
                async with self._conn.execute(
                    "SELECT * FROM provider_events WHERE id = last_insert_rowid()"
                ) as cur:
                    row = await cur.fetchone()
                if row is None:
                    raise RuntimeError("provider event insert did not return a row")
                return _row_to_provider_event(row)
        if event.provider_event_id is not None:
            existing = await self.find_provider_event(
                event.provider, provider_event_id=event.provider_event_id
            )
            if existing is None:
                raise RuntimeError("provider event insert did not return a row")
            return existing
        raise RuntimeError("provider event insert did not return a row")

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

    # --- server requests ------------------------------------------------------

    async def observe_server_request(self, request: ServerRequest) -> ServerRequest:
        """Persist a pending Codex request without reopening a terminal outcome."""

        return (await self.observe_server_request_once(request)).request

    async def observe_server_request_once(self, request: ServerRequest) -> ServerRequestObservation:
        """Persist a request and report whether this call won the insert."""

        if request.state != "pending":
            raise ValueError("only pending server requests may be observed")
        thread_key = _server_request_thread_key(request.provider_thread_id)
        request_id_json = _json_dump_compact(request.request_id)
        async with self._write_lock:
            cur = await self._conn.execute(
                "INSERT INTO server_requests (provider, provider_session_id, provider_thread_id, "
                "provider_thread_key, request_id_json, lane, method, category, state, "
                "received_at, deadline_at, resolved_at, response_summary, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(provider, provider_session_id, provider_thread_key, request_id_json) "
                "DO NOTHING",
                (
                    request.provider,
                    request.provider_session_id,
                    request.provider_thread_id,
                    thread_key,
                    request_id_json,
                    request.lane,
                    request.method,
                    request.category,
                    request.state,
                    request.received_at,
                    request.deadline_at,
                    request.resolved_at,
                    request.response_summary,
                    request.error,
                ),
            )
            await self._conn.commit()
        saved = await self.get_server_request(
            provider=request.provider,
            provider_session_id=request.provider_session_id,
            provider_thread_id=request.provider_thread_id,
            request_id=request.request_id,
        )
        if saved is None:
            raise RuntimeError("server request upsert did not return a row")
        return ServerRequestObservation(request=saved, inserted=cur.rowcount == 1)

    async def get_server_request(
        self,
        *,
        provider: str,
        provider_session_id: str,
        provider_thread_id: str | None,
        request_id: int | str,
    ) -> ServerRequest | None:
        async with self._conn.execute(
            "SELECT * FROM server_requests WHERE provider = ? AND provider_session_id = ? "
            "AND provider_thread_key = ? AND request_id_json = ?",
            (
                provider,
                provider_session_id,
                _server_request_thread_key(provider_thread_id),
                _json_dump_compact(request_id),
            ),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_server_request(row) if row is not None else None

    async def get_server_request_by_id(self, request_id: int) -> ServerRequest | None:
        """Return a request by its dispatch-local operator selector."""

        async with self._conn.execute(
            "SELECT * FROM server_requests WHERE id = ?", (request_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_server_request(row) if row is not None else None

    async def list_server_requests(
        self,
        *,
        state: ServerRequestState | None = "pending",
        lane: str | None = None,
        limit: int = 50,
    ) -> list[ServerRequest]:
        sql = "SELECT * FROM server_requests"
        params: list[object] = []
        clauses: list[str] = []
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        if lane is not None:
            clauses.append("lane = ?")
            params.append(lane)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY deadline_at, received_at, request_id_json LIMIT ?"
        params.append(limit)
        async with self._conn.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
        return [_row_to_server_request(row) for row in rows]

    async def list_pending_server_requests(
        self,
        *,
        lane: str | None = None,
        provider_session_id: str | None = None,
        limit: int = 50,
    ) -> list[ServerRequest]:
        if provider_session_id is None:
            return await self.list_server_requests(lane=lane, limit=limit)

        sql = "SELECT * FROM server_requests WHERE state = 'pending'"
        params: list[object] = []
        if lane is not None:
            sql += " AND lane = ?"
            params.append(lane)
        if provider_session_id is not None:
            sql += " AND provider_session_id = ?"
            params.append(provider_session_id)
        sql += " ORDER BY deadline_at, received_at, request_id_json LIMIT ?"
        params.append(limit)
        async with self._conn.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
        return [_row_to_server_request(row) for row in rows]

    async def claim_server_request(
        self,
        *,
        provider: str,
        provider_session_id: str,
        provider_thread_id: str | None,
        request_id: int | str,
    ) -> ServerRequest | None:
        """Atomically reserve a pending request for one response sender."""

        thread_key = _server_request_thread_key(provider_thread_id)
        request_id_json = _json_dump_compact(request_id)
        async with self._write_lock:
            cur = await self._conn.execute(
                "UPDATE server_requests SET state = 'responding' WHERE provider = ? "
                "AND provider_session_id = ? AND provider_thread_key = ? "
                "AND request_id_json = ? AND state = 'pending'",
                (provider, provider_session_id, thread_key, request_id_json),
            )
            await self._conn.commit()
        if cur.rowcount != 1:
            return None
        return await self.get_server_request(
            provider=provider,
            provider_session_id=provider_session_id,
            provider_thread_id=provider_thread_id,
            request_id=request_id,
        )

    async def claim_server_request_by_id(self, request_id: int) -> ServerRequest | None:
        """Atomically reserve a pending request using its local selector."""

        async with self._write_lock:
            cur = await self._conn.execute(
                "UPDATE server_requests SET state = 'responding' "
                "WHERE id = ? AND state = 'pending'",
                (request_id,),
            )
            await self._conn.commit()
        if cur.rowcount != 1:
            return None
        return await self.get_server_request_by_id(request_id)

    async def finalize_server_request(
        self,
        *,
        provider: str,
        provider_session_id: str,
        provider_thread_id: str | None,
        request_id: int | str,
        state: ServerRequestOutcome,
        response_summary: str | None = None,
        error: str | None = None,
        resolved_at: str | None = None,
    ) -> ServerRequest | None:
        """Persist a terminal result only for the sender that holds the claim."""

        thread_key = _server_request_thread_key(provider_thread_id)
        request_id_json = _json_dump_compact(request_id)
        async with self._write_lock:
            cur = await self._conn.execute(
                "UPDATE server_requests SET state = ?, resolved_at = ?, response_summary = ?, "
                "error = ? WHERE provider = ? AND provider_session_id = ? "
                "AND provider_thread_key = ? AND request_id_json = ? AND state = 'responding'",
                (
                    state,
                    resolved_at or self.now_iso(),
                    _bound_server_request_text(response_summary),
                    _bound_server_request_text(error),
                    provider,
                    provider_session_id,
                    thread_key,
                    request_id_json,
                ),
            )
            await self._conn.commit()
        if cur.rowcount != 1:
            return None
        return await self.get_server_request(
            provider=provider,
            provider_session_id=provider_session_id,
            provider_thread_id=provider_thread_id,
            request_id=request_id,
        )

    async def finalize_server_request_by_id(
        self,
        request_id: int,
        *,
        state: ServerRequestOutcome,
        response_summary: str | None = None,
        error: str | None = None,
        resolved_at: str | None = None,
    ) -> ServerRequest | None:
        """Persist a claimed request's terminal result using its local selector."""

        async with self._write_lock:
            cur = await self._conn.execute(
                "UPDATE server_requests SET state = ?, resolved_at = ?, response_summary = ?, "
                "error = ? WHERE id = ? AND state = 'responding'",
                (
                    state,
                    resolved_at or self.now_iso(),
                    _bound_server_request_text(response_summary),
                    _bound_server_request_text(error),
                    request_id,
                ),
            )
            await self._conn.commit()
        if cur.rowcount != 1:
            return None
        return await self.get_server_request_by_id(request_id)

    async def fail_open_server_requests_except_session(
        self,
        current_session_id: str,
        *,
        error: str = "app-server connection replaced before a response was sent",
    ) -> int:
        """Terminalize rows that cannot be answered after an App Server reconnect."""

        async with self._write_lock:
            cur = await self._conn.execute(
                "UPDATE server_requests SET state = 'failed', resolved_at = ?, "
                "response_summary = NULL, error = ? WHERE provider_session_id != ? "
                "AND state IN ('pending', 'responding')",
                (self.now_iso(), _bound_server_request_text(error), current_session_id),
            )
            await self._conn.commit()
        return cur.rowcount

    async def list_open_server_requests_except_session(
        self, current_session_id: str
    ) -> list[ServerRequest]:
        """Return pending/responding rows that a replacement connection cannot answer."""

        async with self._conn.execute(
            "SELECT * FROM server_requests WHERE provider_session_id != ? "
            "AND state IN ('pending', 'responding') ORDER BY id",
            (current_session_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_server_request(row) for row in rows]

    async def upsert_thread_turn(self, turn: ThreadTurn) -> ThreadTurn:
        async with self._transaction():
            await self._upsert_thread_turn_row(turn)
        return await self.get_thread_turn(turn.provider, turn.provider_thread_id, turn.turn_id)

    async def _upsert_thread_turn_row(self, turn: ThreadTurn) -> None:
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
        async with self._transaction():
            await self._upsert_thread_item_row(item)
            if refs is not None:
                await self._replace_thread_item_refs(item, refs)
        return await self.get_thread_item(item.provider, item.provider_thread_id, item.item_id)

    async def upsert_thread_history_snapshot(
        self,
        *,
        turns: list[ThreadTurn],
        items: list[tuple[ThreadItem, list[ThreadItemRef]]],
        provider: str,
        provider_thread_id: str,
        turn_ids: set[str],
        item_ids: set[str],
        prune_missing: bool = True,
    ) -> None:
        async with self._transaction():
            for turn in turns:
                await self._upsert_thread_turn_row(turn)
            for item, refs in items:
                await self._upsert_thread_item_row(item)
                await self._replace_thread_item_refs(item, refs)
            if prune_missing:
                await self._prune_thread_history_snapshot_rows(
                    provider=provider,
                    provider_thread_id=provider_thread_id,
                    turn_ids=turn_ids,
                    item_ids=item_ids,
                )

    async def _upsert_thread_item_row(self, item: ThreadItem) -> None:
        await self._conn.execute(
            "INSERT INTO thread_items (provider, provider_thread_id, item_id, lane, "
            "turn_id, item_type, role, phase, status, text, tool, server, command, cwd, "
            "error, duration_ms, arguments, success, agent_nickname, agent_role, created_at, "
            "position, inserted_at, payload, raw_retained) VALUES ("
            "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider, provider_thread_id, item_id) DO UPDATE SET "
            "lane = COALESCE(excluded.lane, thread_items.lane), "
            "turn_id = COALESCE(excluded.turn_id, thread_items.turn_id), "
            "item_type = excluded.item_type, "
            "role = COALESCE(excluded.role, thread_items.role), "
            "phase = COALESCE(excluded.phase, thread_items.phase), "
            "status = CASE "
            "WHEN thread_items.status = 'completed' AND excluded.status = 'inProgress' "
            "THEN thread_items.status "
            "ELSE COALESCE(excluded.status, thread_items.status) END, "
            "text = COALESCE(excluded.text, thread_items.text), "
            "tool = COALESCE(excluded.tool, thread_items.tool), "
            "server = COALESCE(excluded.server, thread_items.server), "
            "command = COALESCE(excluded.command, thread_items.command), "
            "cwd = COALESCE(excluded.cwd, thread_items.cwd), "
            "error = COALESCE(excluded.error, thread_items.error), "
            "duration_ms = COALESCE(excluded.duration_ms, thread_items.duration_ms), "
            "arguments = COALESCE(excluded.arguments, thread_items.arguments), "
            "success = COALESCE(excluded.success, thread_items.success), "
            "agent_nickname = COALESCE(excluded.agent_nickname, thread_items.agent_nickname), "
            "agent_role = COALESCE(excluded.agent_role, thread_items.agent_role), "
            "created_at = COALESCE(excluded.created_at, thread_items.created_at), "
            "position = COALESCE(excluded.position, thread_items.position), "
            "inserted_at = thread_items.inserted_at, "
            "payload = COALESCE(excluded.payload, thread_items.payload), "
            "raw_retained = MAX(excluded.raw_retained, thread_items.raw_retained)",
            (
                item.provider,
                item.provider_thread_id,
                item.item_id,
                item.lane,
                item.turn_id,
                item.item_type,
                item.role,
                item.phase,
                item.status,
                item.text,
                item.tool,
                item.server,
                item.command,
                item.cwd,
                item.error,
                item.duration_ms,
                _json_dump_compact(item.arguments) if item.arguments is not None else None,
                None if item.success is None else int(item.success),
                item.agent_nickname,
                item.agent_role,
                item.created_at,
                item.position,
                item.inserted_at,
                _json_dump_compact(item.payload) if item.payload is not None else None,
                int(item.raw_retained),
            ),
        )

    async def _replace_thread_item_refs(self, item: ThreadItem, refs: list[ThreadItemRef]) -> None:
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

    async def find_thread_item(
        self, provider: str, provider_thread_id: str, item_id: str
    ) -> ThreadItem | None:
        async with self._conn.execute(
            "SELECT * FROM thread_items WHERE provider = ? AND provider_thread_id = ? "
            "AND item_id = ?",
            (provider, provider_thread_id, item_id),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_thread_item(row) if row is not None else None

    async def get_thread_item(
        self, provider: str, provider_thread_id: str, item_id: str
    ) -> ThreadItem:
        item = await self.find_thread_item(provider, provider_thread_id, item_id)
        if item is None:
            raise NotFoundError(f"no thread item {provider}:{provider_thread_id}:{item_id}")
        return item

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

    async def search_thread_items(
        self,
        *,
        query: str,
        lanes: set[str] | None = None,
        limit: int = 50,
        max_scan: int = 500,
    ) -> tuple[list[ThreadItem], int]:
        clauses = ["text IS NOT NULL", "instr(lower(text), lower(?)) > 0"]
        params: list[object] = [query]
        if lanes is not None:
            if not lanes:
                return [], 0
            placeholders = ", ".join("?" for _ in lanes)
            clauses.append(f"lane IN ({placeholders})")
            params.extend(sorted(lanes))
        sql = "SELECT * FROM thread_items WHERE " + " AND ".join(clauses)
        sql += " ORDER BY inserted_at DESC, COALESCE(position, -1) DESC, item_id DESC LIMIT ?"
        params.append(max_scan)
        async with self._conn.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
        items = [_row_to_thread_item(row) for row in rows]
        return items[:limit], len(items)

    async def query_thread_items(
        self,
        *,
        query: str | None = None,
        lanes: set[str] | None = None,
        item_type: str | None = None,
        role: str | None = None,
        tool: str | None = None,
        tool_server: str | None = None,
        tool_status: str | None = None,
        errored: bool | None = None,
        file: str | None = None,
        file_under: str | None = None,
        ext: str | None = None,
        mentions_thread: str | None = None,
        turn_id: str | None = None,
        item_id: str | None = None,
        arg_key: str | None = None,
        raw_retained: bool | None = None,
        limit: int = 50,
        max_scan: int = 500,
    ) -> tuple[list[ThreadItem], int]:
        clauses: list[str] = []
        params: list[object] = []
        if query is not None:
            clauses.append("items.text IS NOT NULL")
            clauses.append("instr(lower(items.text), lower(?)) > 0")
            params.append(query)
        if lanes is not None:
            if not lanes:
                return [], 0
            placeholders = ", ".join("?" for _ in lanes)
            clauses.append(f"items.lane IN ({placeholders})")
            params.extend(sorted(lanes))
        if item_type is not None:
            clauses.append("instr(lower(items.item_type), lower(?)) > 0")
            params.append(item_type)
        if role is not None:
            clauses.append("items.role IS NOT NULL")
            clauses.append("instr(lower(items.role), lower(?)) > 0")
            params.append(role)
        if tool is not None:
            clauses.append("items.tool IS NOT NULL")
            clauses.append("instr(lower(items.tool), lower(?)) > 0")
            params.append(tool)
        if turn_id is not None:
            clauses.append("items.turn_id = ?")
            params.append(turn_id)
        if item_id is not None:
            clauses.append("items.item_id = ?")
            params.append(item_id)
        if raw_retained is not None:
            clauses.append("items.raw_retained = ?")
            params.append(int(raw_retained))
        for ref_type, ref_value in (
            ("file", file),
            ("thread", mentions_thread),
        ):
            if ref_value is not None:
                clauses.append(_ref_exists_sql(ref_type))
                params.append(ref_value)
        for column, ref_type, value in (
            ("server", "tool_server", tool_server),
            ("status", "tool_status", tool_status),
        ):
            if value is not None:
                clauses.append(
                    f"(instr(lower(items.{column}), lower(?)) > 0 OR {_ref_exists_sql(ref_type)})"
                )
                params.extend((value, value))
        if arg_key is not None:
            clauses.append(
                "(EXISTS (SELECT 1 FROM json_each(items.arguments) args "
                "WHERE CAST(args.key AS TEXT) = ?) OR "
                f"{_ref_exists_sql('tool_arg_key')})"
            )
            params.extend((arg_key, arg_key))
        if file_under is not None:
            clauses.append(_ref_path_under_sql())
            path = file_under.rstrip("/")
            params.extend([path, f"{_path_prefix(path)}%"])
        if ext is not None:
            clauses.append(_ref_exists_sql("file", operator="LIKE"))
            params.append(f"%{_extension_suffix(ext).casefold()}")
        if errored is not None:
            clause = _ref_exists_sql("tool_error", exact=True)
            if errored:
                clauses.append(f"(items.error IS NOT NULL OR items.success = 0 OR {clause})")
            else:
                clauses.append(
                    "(items.error IS NULL AND (items.success IS NULL OR items.success != 0) "
                    f"AND NOT {clause})"
                )
            params.append("true")
        where = " AND ".join(clauses) if clauses else "1 = 1"
        sql = "SELECT items.* FROM thread_items items WHERE " + where
        sql += (
            " ORDER BY items.inserted_at DESC,"
            " COALESCE(items.position, -1) DESC,"
            " items.item_id DESC"
        )
        sql += " LIMIT ?"
        params.append(max_scan)
        async with self._conn.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
        items = [_row_to_thread_item(row) for row in rows]
        return items[:limit], len(items)

    async def get_thread_history_summary_stats(self, *, lane: str) -> ThreadHistorySummaryStats:
        async with self._conn.execute(
            """
            SELECT
                COUNT(DISTINCT turns.turn_id) AS turns,
                MIN(turns.started_at) AS first_event_at,
                MAX(COALESCE(turns.completed_at, turns.failed_at, turns.updated_at))
                    AS last_turn_at
            FROM thread_turns turns
            WHERE turns.lane = ?
            """,
            (lane,),
        ) as cur:
            turn_row = await cur.fetchone()
        async with self._conn.execute(
            """
            SELECT
                COUNT(*) AS items,
                SUM(
                    CASE
                        WHEN lower(item_type) LIKE '%message%'
                            OR role IN ('user', 'assistant', 'system')
                        THEN 1
                        ELSE 0
                    END
                ) AS messages,
                SUM(CASE WHEN tool IS NOT NULL THEN 1 ELSE 0 END) AS tool_calls,
                SUM(length(COALESCE(text, ''))) AS transcript_bytes
            FROM thread_items
            WHERE lane = ?
            """,
            (lane,),
        ) as cur:
            item_row = await cur.fetchone()
        async with self._conn.execute(
            """
            SELECT tool, COUNT(*) AS count, GROUP_CONCAT(DISTINCT item_type) AS item_types
            FROM thread_items
            WHERE lane = ? AND tool IS NOT NULL
            GROUP BY tool
            ORDER BY count DESC, tool ASC
            """,
            (lane,),
        ) as cur:
            tool_rows = await cur.fetchall()
        async with self._conn.execute(
            """
            SELECT refs.ref_value AS path, COUNT(*) AS count
            FROM thread_item_refs refs
            INNER JOIN thread_items items
                ON items.provider = refs.provider
                AND items.provider_thread_id = refs.provider_thread_id
                AND items.item_id = refs.item_id
            WHERE items.lane = ? AND refs.ref_type = 'file'
            GROUP BY refs.ref_value
            ORDER BY count DESC, refs.ref_value ASC
            LIMIT 25
            """,
            (lane,),
        ) as cur:
            file_rows = await cur.fetchall()
        async with self._conn.execute(
            """
            SELECT COUNT(DISTINCT refs.ref_value) AS count
            FROM thread_item_refs refs
            INNER JOIN thread_items items
                ON items.provider = refs.provider
                AND items.provider_thread_id = refs.provider_thread_id
                AND items.item_id = refs.item_id
            WHERE items.lane = ? AND refs.ref_type = 'file'
            """,
            (lane,),
        ) as cur:
            file_count_row = await cur.fetchone()
        async with self._conn.execute(
            """
            SELECT DISTINCT refs.ref_value AS thread_id
            FROM thread_item_refs refs
            INNER JOIN thread_items items
                ON items.provider = refs.provider
                AND items.provider_thread_id = refs.provider_thread_id
                AND items.item_id = refs.item_id
            WHERE items.lane = ? AND refs.ref_type = 'child_thread'
            ORDER BY refs.ref_value ASC
            """,
            (lane,),
        ) as cur:
            child_thread_rows = await cur.fetchall()
        sync = await self.get_lane_sync(lane)
        transcript_bytes = _optional_int(item_row["transcript_bytes"] if item_row else None)
        return ThreadHistorySummaryStats(
            turns=_optional_int(turn_row["turns"] if turn_row else None) or 0,
            items=_optional_int(item_row["items"] if item_row else None) or 0,
            messages=_optional_int(item_row["messages"] if item_row else None) or 0,
            tool_calls=_optional_int(item_row["tool_calls"] if item_row else None) or 0,
            transcript_bytes=transcript_bytes,
            first_event_at=turn_row["first_event_at"] if turn_row else None,
            last_event_at=(
                sync.latest_event_at
                if sync is not None and sync.latest_event_at is not None
                else (turn_row["last_turn_at"] if turn_row else None)
            ),
            tools=[
                ThreadHistoryToolStat(
                    tool=row["tool"],
                    count=_optional_int(row["count"]) or 0,
                    item_types=sorted(
                        item_type
                        for item_type in str(row["item_types"] or "").split(",")
                        if item_type
                    ),
                )
                for row in tool_rows
            ],
            files_changed_count=_optional_int(file_count_row["count"] if file_count_row else None)
            or 0,
            files=[
                ThreadHistoryFileStat(
                    path=row["path"],
                    count=_optional_int(row["count"]) or 0,
                )
                for row in file_rows
            ],
            child_thread_ids=[str(row["thread_id"]) for row in child_thread_rows],
        )

    async def list_thread_item_refs(self, item: ThreadItem) -> list[ThreadItemRef]:
        async with self._conn.execute(
            "SELECT * FROM thread_item_refs WHERE provider = ? AND provider_thread_id = ? "
            "AND item_id = ? ORDER BY ref_type, ref_value",
            (item.provider, item.provider_thread_id, item.item_id),
        ) as cur:
            rows = await cur.fetchall()
        return [ThreadItemRef.model_validate(_row_dict(row)) for row in rows]

    async def list_thread_item_refs_many(
        self, items: list[ThreadItem]
    ) -> dict[ThreadItemIdentity, list[ThreadItemRef]]:
        refs_by_item: dict[ThreadItemIdentity, list[ThreadItemRef]] = {
            _thread_item_identity(item): [] for item in items
        }
        for chunk_start in range(0, len(items), 500):
            clauses = []
            params: list[object] = []
            for item in items[chunk_start : chunk_start + 500]:
                clauses.append("(provider = ? AND provider_thread_id = ? AND item_id = ?)")
                params.extend((item.provider, item.provider_thread_id, item.item_id))
            if not clauses:
                continue
            sql = (
                "SELECT * FROM thread_item_refs WHERE "
                + " OR ".join(clauses)
                + " ORDER BY item_id, ref_type, ref_value"
            )
            async with self._conn.execute(sql, tuple(params)) as cur:
                rows = await cur.fetchall()
            for row in rows:
                ref = ThreadItemRef.model_validate(_row_dict(row))
                refs_by_item.setdefault(_thread_item_identity(ref), []).append(ref)
        return refs_by_item

    async def prune_thread_history_snapshot(
        self,
        *,
        provider: str,
        provider_thread_id: str,
        turn_ids: set[str],
        item_ids: set[str],
    ) -> None:
        async with self._transaction():
            await self._prune_thread_history_snapshot_rows(
                provider=provider,
                provider_thread_id=provider_thread_id,
                turn_ids=turn_ids,
                item_ids=item_ids,
            )

    async def _prune_thread_history_snapshot_rows(
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
        async with self._write_lock:
            await self._conn.execute(
                "INSERT INTO message_receipts (id, lane, queued_message_id, provider, "
                "provider_thread_id, dispatch_message_id, status, turn_id, error, created_at, "
                "sent_at, accepted_at, completed_at, failed_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(dispatch_message_id) WHERE dispatch_message_id IS NOT NULL "
                "DO UPDATE SET lane = excluded.lane, "
                "queued_message_id = excluded.queued_message_id, "
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
            if receipt.dispatch_message_id is None:
                async with self._conn.execute(
                    "SELECT * FROM message_receipts WHERE id = last_insert_rowid()"
                ) as cur:
                    row = await cur.fetchone()
                if row is None:
                    raise RuntimeError("message receipt upsert did not return a row")
                return MessageReceipt.model_validate(_row_dict(row))
        if receipt.dispatch_message_id is not None:
            got = await self.find_message_receipt(
                provider=receipt.provider, dispatch_message_id=receipt.dispatch_message_id
            )
            if got is None:
                raise RuntimeError("message receipt upsert did not return a row")
            return got
        raise RuntimeError("message receipt upsert did not return a row")

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
        async with self._write_lock:
            await self._conn.execute(
                "INSERT INTO lane_runtime_state (lane, provider, provider_thread_id, status, "
                "active_turn_id, latest_turn_id, latest_turn_status, needs_attention, "
                "attention_kind, attention_detail, updated_at, last_event_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(lane) DO UPDATE SET provider = excluded.provider, "
                "provider_thread_id = excluded.provider_thread_id, status = excluded.status, "
                "active_turn_id = excluded.active_turn_id, "
                "latest_turn_id = excluded.latest_turn_id, "
                "latest_turn_status = excluded.latest_turn_status, "
                "needs_attention = excluded.needs_attention, "
                "attention_kind = excluded.attention_kind, "
                "attention_detail = excluded.attention_detail, "
                "updated_at = excluded.updated_at, last_event_at = excluded.last_event_at",
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


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float | str | bytes | bytearray):
        return int(value)
    raise TypeError(f"expected SQLite integer-compatible value, got {type(value).__name__}")


def _bool_or_none(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _json_dump_compact(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def _server_request_thread_key(provider_thread_id: str | None) -> str:
    return "threadless" if provider_thread_id is None else f"thread:{provider_thread_id}"


def _bound_server_request_text(value: str | None) -> str | None:
    return value[:SERVER_REQUEST_TEXT_LIMIT] if value is not None else None


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
    data["input_modalities"] = _json_str_list(data["input_modalities"])
    data["supports_personality"] = (
        None if data["supports_personality"] is None else bool(data["supports_personality"])
    )
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


def _row_to_provider_thread(row: aiosqlite.Row) -> ProviderThread:
    return ProviderThread.model_validate(_row_dict(row))


def _row_to_provider_thread_node(row: aiosqlite.Row) -> ProviderThreadNode:
    data = _row_dict(row)
    lane_id = data.pop("lane_id")
    ref = data.pop("ref")
    handle = data.pop("handle")
    lane_status = data.pop("lane_status")
    return ProviderThreadNode(
        thread=ProviderThread.model_validate(data),
        managed=lane_id is not None,
        ref=str(ref) if ref is not None else None,
        handle=str(handle) if handle is not None else None,
        lane_status=cast(LaneStatus, str(lane_status)) if lane_status is not None else None,
    )


def _row_to_server_request(row: aiosqlite.Row) -> ServerRequest:
    data = _row_dict(row)
    request_id = json.loads(str(data.pop("request_id_json")))
    if not isinstance(request_id, int | str) or isinstance(request_id, bool):
        raise ValueError("server request id must be an int or string")
    data["request_id"] = request_id
    return ServerRequest.model_validate(data)


def _row_to_thread_item(row: aiosqlite.Row) -> ThreadItem:
    data = _row_dict(row)
    arguments = data["arguments"]
    data["arguments"] = json.loads(str(arguments)) if arguments is not None else None
    payload = data["payload"]
    data["payload"] = json.loads(str(payload)) if payload else None
    data["success"] = None if data["success"] is None else bool(data["success"])
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
