"""The durable registry store (aiosqlite).

Async end-to-end (never blocks the loop). An injectable clock makes time-stamped
rows deterministic in tests. Holds ``lanes``, ``triggers`` (populated in Phase 3),
and the ``actions_log`` audit of every send/action.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any, Concatenate, Literal, Protocol, cast
from uuid import uuid4

import aiosqlite

from outfitter.dispatch.contracts.errors import (
    DeliveryConflictError,
    NotFoundError,
    ValidationError,
)

from .delivery import (
    DeliveryExecutionStatus,
    DeliveryMode,
    DeliveryReceipt,
    DeliveryStatus,
    DeliveryTransport,
)
from .launch import LaneLaunch, LaneLaunchStatus
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
    PermissionProfileEntry,
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
from .observations import ProviderObservation, ReceiptTransition
from .refs import (
    BASE58BTC_ALPHABET,
    CODEX_REF_SOURCE,
    GENERIC_REF_SOURCE,
    codex_ref_payload,
    generic_ref_payload,
    make_ref,
)

Clock = Callable[[], datetime]
SCHEMA_VERSION = 27
DEFAULT_CODEX_BINDING_ID = "codex-default"


class _ReentrantAsyncLock:
    """Task-reentrant lock for nested registry write APIs."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task[object] | None = None
        self._depth = 0

    async def __aenter__(self) -> None:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("registry writes require an asyncio task")
        if self._owner is task:
            self._depth += 1
            return
        await self._lock.acquire()
        self._owner = task
        self._depth = 1

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        task = asyncio.current_task()
        if task is None or self._owner is not task:
            raise RuntimeError("registry write lock released by a non-owner")
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._lock.release()


class _RegistryAccessOwner(Protocol):
    _write_lock: _ReentrantAsyncLock


def _serialized_access[T: _RegistryAccessOwner, **P, R](
    func: Callable[Concatenate[T, P], Coroutine[Any, Any, R]],
) -> Callable[Concatenate[T, P], Coroutine[Any, Any, R]]:
    """Serialize public access to the shared SQLite connection by task."""

    @wraps(func)
    async def wrapped(self: T, *args: P.args, **kwargs: P.kwargs) -> R:
        async with self._write_lock:
            return await func(self, *args, **kwargs)

    wrapped.__dict__["__registry_serialized__"] = True

    return cast(
        "Callable[Concatenate[T, P], Coroutine[Any, Any, R]]",
        wrapped,
    )


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
    binding_id: str
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


ThreadItemIdentity = tuple[str, str, str, str]


def _thread_item_identity(item: ThreadItem | ThreadItemRef) -> ThreadItemIdentity:
    return (item.provider, item.binding_id, item.provider_thread_id, item.item_id)


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
        "AND refs.binding_id = items.binding_id "
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
        "AND refs.binding_id = items.binding_id "
        "AND refs.provider_thread_id = items.provider_thread_id "
        "AND refs.item_id = items.item_id "
        "AND refs.ref_type = 'file' "
        "AND (refs.ref_value = ? OR lower(refs.ref_value) LIKE lower(?)))"
    )


def _extension_suffix(ext: str) -> str:
    return ext if ext.startswith(".") else f".{ext}"


def _initial_provider_session_id(
    lane_id: str, provider: str, binding_id: str, provider_session_id: str | None
) -> str | None:
    if provider == "codex" and binding_id == DEFAULT_CODEX_BINDING_ID:
        if provider_session_id not in (None, lane_id):
            raise ValueError("a new default-Codex lane must retain its native id as the lane id")
        return lane_id
    if not lane_id.startswith("dsp_"):
        raise ValueError("non-default provider lanes require an opaque dsp_ Dispatch id")
    return provider_session_id


_QUEUED_MESSAGES_SCHEMA = """
CREATE TABLE IF NOT EXISTS queued_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lane TEXT NOT NULL,
    text TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error TEXT,
    FOREIGN KEY(lane) REFERENCES lanes(id) ON DELETE CASCADE
);
"""

_DELIVERIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS deliveries (
    id TEXT PRIMARY KEY,
    key TEXT UNIQUE,
    lane TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('send', 'queue')),
    transport TEXT NOT NULL DEFAULT 'turn',
    provider TEXT NOT NULL DEFAULT 'codex',
    binding_id TEXT NOT NULL DEFAULT 'codex-default',
    native_session_id TEXT,
    correlation_id TEXT,
    submission_id TEXT,
    submitted_payload TEXT,
    payload TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'queued', 'submitting', 'accepted', 'completed', 'failed', 'ambiguous'
    )),
    execution_status TEXT CHECK(execution_status IN (
        'inProgress', 'completed', 'failed', 'interrupted'
    )),
    turn_id TEXT,
    queue_id INTEGER UNIQUE,
    error TEXT,
    reconciliation_attempts INTEGER NOT NULL DEFAULT 0,
    evidence_source TEXT,
    evidence_provider_time TEXT,
    evidence_received_at TEXT,
    evidence_partial INTEGER NOT NULL DEFAULT 0,
    evidence_generation TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(lane) REFERENCES lanes(id) ON DELETE CASCADE,
    FOREIGN KEY(queue_id) REFERENCES queued_messages(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_deliveries_lane_status
ON deliveries(lane, status, created_at);
CREATE INDEX IF NOT EXISTS idx_deliveries_turn
ON deliveries(lane, turn_id);
"""

_LANE_LAUNCHES_SCHEMA = """
CREATE TABLE IF NOT EXISTS lane_launches (
    lane TEXT PRIMARY KEY,
    key TEXT UNIQUE,
    submitted_payload TEXT NOT NULL,
    request_payload TEXT NOT NULL,
    provider TEXT NOT NULL,
    binding_id TEXT NOT NULL,
    generation TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'reserved', 'creating', 'created', 'ambiguous', 'failed'
    )),
    runtime_session_id TEXT,
    stored_session_id TEXT,
    effective_cwd TEXT,
    first_delivery_id TEXT UNIQUE,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(lane) REFERENCES lanes(id) ON DELETE CASCADE,
    FOREIGN KEY(first_delivery_id) REFERENCES deliveries(id) ON DELETE SET NULL,
    CHECK ((runtime_session_id IS NULL) = (stored_session_id IS NULL)),
    CHECK (status != 'created' OR (
        runtime_session_id IS NOT NULL AND stored_session_id IS NOT NULL
    )),
    CHECK (first_delivery_id IS NULL OR status = 'created')
);
CREATE INDEX IF NOT EXISTS idx_lane_launches_status
ON lane_launches(status, created_at);
"""

_PROVIDER_THREADS_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_threads (
    provider TEXT NOT NULL,
    binding_id TEXT NOT NULL,
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
    PRIMARY KEY(provider, binding_id, provider_thread_id)
);
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
    binding_id TEXT NOT NULL,
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
CREATE INDEX IF NOT EXISTS idx_provider_events_lane_received
ON provider_events(lane, received_at);

CREATE TABLE IF NOT EXISTS thread_turns (
    provider TEXT NOT NULL,
    binding_id TEXT NOT NULL,
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
    PRIMARY KEY(provider, binding_id, provider_thread_id, turn_id),
    FOREIGN KEY(lane) REFERENCES lanes(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_thread_turns_lane_updated
ON thread_turns(lane, updated_at);

CREATE TABLE IF NOT EXISTS thread_items (
    provider TEXT NOT NULL,
    binding_id TEXT NOT NULL,
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
    PRIMARY KEY(provider, binding_id, provider_thread_id, item_id),
    FOREIGN KEY(lane) REFERENCES lanes(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_thread_items_lane_inserted
ON thread_items(lane, position, inserted_at);

CREATE TABLE IF NOT EXISTS thread_item_refs (
    provider TEXT NOT NULL,
    binding_id TEXT NOT NULL,
    provider_thread_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    ref_type TEXT NOT NULL,
    ref_value TEXT NOT NULL,
    PRIMARY KEY(provider, binding_id, provider_thread_id, item_id, ref_type, ref_value),
    FOREIGN KEY(provider, binding_id, provider_thread_id, item_id)
        REFERENCES thread_items(provider, binding_id, provider_thread_id, item_id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_thread_item_refs_lookup
ON thread_item_refs(ref_type, ref_value);

CREATE TABLE IF NOT EXISTS message_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lane TEXT,
    queued_message_id INTEGER,
    provider TEXT NOT NULL,
    binding_id TEXT NOT NULL,
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
    binding_id TEXT NOT NULL,
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
    binding_id TEXT NOT NULL,
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
    UNIQUE(provider, binding_id, provider_session_id, provider_thread_key, request_id_json),
    FOREIGN KEY(lane) REFERENCES lanes(id) ON DELETE SET NULL
);
"""


def _utcnow() -> datetime:
    return datetime.now(UTC)


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS lanes (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    binding_id TEXT NOT NULL,
    provider_session_id TEXT,
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
    next_offset INTEGER,
    last_synced_at TEXT,
    error TEXT,
    history_source TEXT,
    history_cursor TEXT,
    history_backwards_cursor TEXT,
    history_recent_cursor TEXT,
    history_pending_backwards_cursor TEXT,
    history_item_turn_id TEXT,
    history_item_turn_cursor TEXT,
    history_item_turn_direction TEXT,
    history_item_cursor TEXT,
    history_cursor_guard TEXT,
    history_complete INTEGER NOT NULL DEFAULT 0,
    history_capability TEXT NOT NULL DEFAULT 'unknown',
    observation_enabled INTEGER NOT NULL DEFAULT 0,
    pages_scanned INTEGER NOT NULL DEFAULT 0,
    turns_indexed INTEGER NOT NULL DEFAULT 0,
    items_indexed INTEGER NOT NULL DEFAULT 0,
    unchanged_skipped INTEGER NOT NULL DEFAULT 0,
    scanned_bytes INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    truncated INTEGER NOT NULL DEFAULT 0,
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
CREATE TABLE IF NOT EXISTS permission_profiles (
    id TEXT NOT NULL,
    cwd TEXT NOT NULL,
    description TEXT,
    allowed INTEGER NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'app-server',
    PRIMARY KEY(cwd, id)
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
    permission_profile TEXT,
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
{_DELIVERIES_SCHEMA}
{_LANE_LAUNCHES_SCHEMA}
"""

_BINDING_SCOPE_INDEX_STATEMENTS = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_lanes_provider_session "
    "ON lanes(provider, binding_id, provider_session_id) "
    "WHERE provider_session_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_provider_threads_parent "
    "ON provider_threads(provider, binding_id, parent_thread_id)",
    "CREATE INDEX IF NOT EXISTS idx_provider_threads_fork "
    "ON provider_threads(provider, binding_id, forked_from_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_events_provider_event_id "
    "ON provider_events(provider, binding_id, provider_event_id) "
    "WHERE provider_event_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_provider_events_thread_received "
    "ON provider_events(provider, binding_id, provider_thread_id, received_at)",
    "CREATE INDEX IF NOT EXISTS idx_provider_events_lane_received "
    "ON provider_events(lane, received_at)",
    "CREATE INDEX IF NOT EXISTS idx_thread_turns_lane_updated ON thread_turns(lane, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_thread_items_lane_inserted "
    "ON thread_items(lane, position, inserted_at)",
    "CREATE INDEX IF NOT EXISTS idx_thread_items_turn "
    "ON thread_items(provider, binding_id, provider_thread_id, turn_id)",
    "CREATE INDEX IF NOT EXISTS idx_thread_item_refs_lookup "
    "ON thread_item_refs(ref_type, ref_value)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_message_receipts_dispatch_message_id "
    "ON message_receipts(dispatch_message_id) "
    "WHERE dispatch_message_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_message_receipts_lane_updated "
    "ON message_receipts(lane, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_server_requests_pending "
    "ON server_requests(provider, binding_id, provider_session_id, state, "
    "deadline_at, received_at)",
    "CREATE INDEX IF NOT EXISTS idx_server_requests_lane_pending "
    "ON server_requests(lane, binding_id, provider_session_id, state, received_at)",
)

REGISTRY_SCHEMA_SQL = _SCHEMA + ";\n".join(_BINDING_SCOPE_INDEX_STATEMENTS) + ";\n"


class Registry:
    """The lane/trigger/audit store."""

    def __init__(self, conn: aiosqlite.Connection, now: Clock) -> None:
        self._conn = conn
        self._now = now
        self._write_lock = _ReentrantAsyncLock()

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
        try:
            await store._conn.executescript(_SCHEMA)
            if user_version < SCHEMA_VERSION:
                await store._migrate(user_version)
                await store._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            await store._conn.commit()
        except BaseException:
            await store._conn.close()
            raise
        return store

    @_serialized_access
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
        if user_version < 23:
            async with self._conn.execute("PRAGMA table_info(deliveries)") as cur:
                columns = {row["name"] for row in await cur.fetchall()}
            if "transport" not in columns:
                await self._conn.execute(
                    "ALTER TABLE deliveries ADD COLUMN transport TEXT NOT NULL DEFAULT 'turn'"
                )
            if "submission_id" not in columns:
                await self._conn.execute("ALTER TABLE deliveries ADD COLUMN submission_id TEXT")
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
        if user_version < 18:
            await self._ensure_lane_sync_progress_columns()
        if user_version < 19:
            await self._ensure_lane_sync_continuation_columns()
        if user_version < 20:
            await self._ensure_permission_profiles_table()
        if user_version < 21:
            await self._ensure_queued_message_content_column()
        if user_version < 22:
            await self._ensure_deliveries_table()
        if user_version < 24:
            await self._ensure_binding_scope_v24()
        if user_version < 25:
            await self._ensure_delivery_submitted_payload_column()
        if user_version < 26:
            await self._ensure_delivery_observation_columns()
        if user_version < 27:
            await self._ensure_lane_launches_table()

    async def _ensure_binding_scope_v24(self) -> None:
        """Add binding identity without changing stable lane keys or local row ids."""

        await self._conn.commit()
        await self._conn.execute("PRAGMA foreign_keys = OFF")
        try:
            await self._conn.execute("BEGIN IMMEDIATE")
            sequence_high_water: dict[str, int] = {}
            async with self._conn.execute(
                "SELECT name, seq FROM sqlite_sequence "
                "WHERE name IN ('provider_events', 'message_receipts', 'server_requests')"
            ) as cur:
                sequence_high_water = {
                    str(row["name"]): int(row["seq"]) for row in await cur.fetchall()
                }
            async with self._conn.execute("PRAGMA table_info(lanes)") as cur:
                lane_columns = {str(row["name"]) for row in await cur.fetchall()}
            if "provider" not in lane_columns:
                await self._conn.execute(
                    "ALTER TABLE lanes ADD COLUMN provider TEXT NOT NULL DEFAULT 'codex'"
                )
            if "binding_id" not in lane_columns:
                await self._conn.execute(
                    "ALTER TABLE lanes ADD COLUMN binding_id TEXT NOT NULL "
                    f"DEFAULT '{DEFAULT_CODEX_BINDING_ID}'"
                )
            added_provider_session_id = "provider_session_id" not in lane_columns
            if added_provider_session_id:
                await self._conn.execute("ALTER TABLE lanes ADD COLUMN provider_session_id TEXT")
                await self._conn.execute(
                    "UPDATE lanes SET provider = 'codex', binding_id = ?, provider_session_id = id",
                    (DEFAULT_CODEX_BINDING_ID,),
                )

            schemas = {
                "provider_threads": _PROVIDER_THREADS_SCHEMA,
                "provider_events": _PROVIDER_HISTORY_SCHEMA,
                "thread_turns": _PROVIDER_HISTORY_SCHEMA,
                "thread_items": _PROVIDER_HISTORY_SCHEMA,
                "thread_item_refs": _PROVIDER_HISTORY_SCHEMA,
                "message_receipts": _PROVIDER_HISTORY_SCHEMA,
                "lane_runtime_state": _PROVIDER_HISTORY_SCHEMA,
                "server_requests": _SERVER_REQUESTS_SCHEMA,
            }
            rebuilt: list[str] = []
            for table, schema in schemas.items():
                async with self._conn.execute(f"PRAGMA table_info({table})") as cur:
                    old_columns = [str(row["name"]) for row in await cur.fetchall()]
                if not old_columns or "binding_id" in old_columns:
                    continue
                match = re.search(
                    rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);",
                    schema,
                    re.DOTALL,
                )
                if match is None:
                    raise RuntimeError(f"missing v24 schema for {table}")
                create_sql = f"CREATE TABLE {table}_v24 ({match.group(1)}\n)"
                if table == "thread_item_refs":
                    create_sql = create_sql.replace(
                        "REFERENCES thread_items(", "REFERENCES thread_items_v24("
                    )
                await self._conn.execute(create_sql)
                new_columns = [
                    str(row["name"])
                    for row in await (
                        await self._conn.execute(f"PRAGMA table_info({table}_v24)")
                    ).fetchall()
                ]
                select_parts = ["?" if column == "binding_id" else column for column in new_columns]
                await self._conn.execute(
                    f"INSERT INTO {table}_v24 ({', '.join(new_columns)}) "
                    f"SELECT {', '.join(select_parts)} FROM {table}",
                    (DEFAULT_CODEX_BINDING_ID,),
                )
                rebuilt.append(table)

            if "thread_item_refs" in rebuilt:
                await self._conn.execute("DROP TABLE thread_item_refs")
            for table in rebuilt:
                if table != "thread_item_refs":
                    await self._conn.execute(f"DROP TABLE {table}")
            for table in rebuilt:
                if table != "thread_item_refs":
                    await self._conn.execute(f"ALTER TABLE {table}_v24 RENAME TO {table}")
            if "thread_item_refs" in rebuilt:
                await self._conn.execute(
                    "ALTER TABLE thread_item_refs_v24 RENAME TO thread_item_refs"
                )

            for table, sequence in sequence_high_water.items():
                cur = await self._conn.execute(
                    "UPDATE sqlite_sequence SET seq = CASE WHEN seq < ? THEN ? ELSE seq END "
                    "WHERE name = ?",
                    (sequence, sequence, table),
                )
                if cur.rowcount == 0:
                    await self._conn.execute(
                        "INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)",
                        (table, sequence),
                    )

            await self._create_binding_scope_indexes()
            async with self._conn.execute("PRAGMA foreign_key_check") as cur:
                violations = await cur.fetchall()
            if violations:
                raise RuntimeError(f"v24 foreign key check failed: {violations!r}")
            await self._conn.execute("PRAGMA user_version = 24")
            await self._conn.commit()
        except BaseException:
            await self._conn.rollback()
            raise
        finally:
            await self._conn.execute("PRAGMA foreign_keys = ON")

    async def _create_binding_scope_indexes(self) -> None:
        for statement in _BINDING_SCOPE_INDEX_STATEMENTS:
            await self._conn.execute(statement)

    async def _ensure_deliveries_table(self) -> None:
        await self._conn.executescript(_DELIVERIES_SCHEMA)

    async def _ensure_lane_launches_table(self) -> None:
        await self._conn.executescript(_LANE_LAUNCHES_SCHEMA)

    async def _ensure_delivery_submitted_payload_column(self) -> None:
        async with self._conn.execute("PRAGMA table_info(deliveries)") as cur:
            columns = {str(row["name"]) for row in await cur.fetchall()}
        if "submitted_payload" not in columns:
            await self._conn.execute("ALTER TABLE deliveries ADD COLUMN submitted_payload TEXT")

    async def _ensure_delivery_observation_columns(self) -> None:
        async with self._conn.execute("PRAGMA table_info(deliveries)") as cur:
            columns = {str(row["name"]) for row in await cur.fetchall()}
        additions = (
            ("provider", "TEXT NOT NULL DEFAULT 'codex'"),
            ("binding_id", f"TEXT NOT NULL DEFAULT '{DEFAULT_CODEX_BINDING_ID}'"),
            ("native_session_id", "TEXT"),
            ("correlation_id", "TEXT"),
            ("evidence_source", "TEXT"),
            ("evidence_provider_time", "TEXT"),
            ("evidence_received_at", "TEXT"),
            ("evidence_partial", "INTEGER NOT NULL DEFAULT 0"),
            ("evidence_generation", "TEXT"),
        )
        for name, definition in additions:
            if name not in columns:
                await self._conn.execute(f"ALTER TABLE deliveries ADD COLUMN {name} {definition}")
        await self._conn.execute(
            "UPDATE deliveries SET "
            "provider = CASE WHEN json_valid(payload) "
            "THEN COALESCE(json_extract(payload, '$.request.target.provider'), provider) "
            "ELSE provider END, "
            "binding_id = CASE WHEN json_valid(payload) "
            "THEN COALESCE(json_extract(payload, '$.request.target.binding_id'), binding_id) "
            "ELSE binding_id END, "
            "native_session_id = CASE WHEN json_valid(payload) "
            "THEN COALESCE(native_session_id, "
            "json_extract(payload, '$.request.target.native_session_id'), lane) "
            "ELSE COALESCE(native_session_id, lane) END, "
            "correlation_id = CASE WHEN json_valid(payload) "
            "THEN COALESCE(correlation_id, json_extract(payload, '$.request.correlation_id'), id) "
            "ELSE COALESCE(correlation_id, id) END"
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_deliveries_provider_run "
            "ON deliveries(provider, binding_id, native_session_id, turn_id)"
        )

    async def _ensure_queued_message_content_column(self) -> None:
        async with self._conn.execute("PRAGMA table_info(queued_messages)") as cur:
            columns = {str(row["name"]) for row in await cur.fetchall()}
        if "content" not in columns:
            await self._conn.execute(
                "ALTER TABLE queued_messages ADD COLUMN content TEXT NOT NULL DEFAULT '[]'"
            )

    async def _ensure_permission_profiles_table(self) -> None:
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS permission_profiles (
                id TEXT NOT NULL,
                cwd TEXT NOT NULL,
                description TEXT,
                allowed INTEGER NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'app-server',
                PRIMARY KEY(cwd, id)
            )
            """
        )
        async with self._conn.execute("PRAGMA table_info(lane_runtime_settings)") as cur:
            columns = {str(row["name"]) for row in await cur.fetchall()}
        if "permission_profile" not in columns:
            await self._conn.execute(
                "ALTER TABLE lane_runtime_settings ADD COLUMN permission_profile TEXT"
            )

    async def _ensure_lane_sync_progress_columns(self) -> None:
        async with self._conn.execute("PRAGMA table_info(lane_sync_sources)") as cur:
            rows = await cur.fetchall()
        columns = {str(row["name"]) for row in rows}
        column_defs = {
            "history_source": "TEXT",
            "next_offset": "INTEGER",
            "history_cursor": "TEXT",
            "history_backwards_cursor": "TEXT",
            "history_recent_cursor": "TEXT",
            "history_pending_backwards_cursor": "TEXT",
            "history_item_turn_id": "TEXT",
            "history_item_turn_cursor": "TEXT",
            "history_item_cursor": "TEXT",
            "history_complete": "INTEGER NOT NULL DEFAULT 0",
            "history_capability": "TEXT NOT NULL DEFAULT 'unknown'",
            "observation_enabled": "INTEGER NOT NULL DEFAULT 0",
            "pages_scanned": "INTEGER NOT NULL DEFAULT 0",
            "turns_indexed": "INTEGER NOT NULL DEFAULT 0",
            "items_indexed": "INTEGER NOT NULL DEFAULT 0",
            "unchanged_skipped": "INTEGER NOT NULL DEFAULT 0",
            "scanned_bytes": "INTEGER NOT NULL DEFAULT 0",
            "duration_ms": "INTEGER NOT NULL DEFAULT 0",
            "truncated": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, definition in column_defs.items():
            if name not in columns:
                await self._conn.execute(
                    f"ALTER TABLE lane_sync_sources ADD COLUMN {name} {definition}"
                )

    async def _ensure_lane_sync_continuation_columns(self) -> None:
        async with self._conn.execute("PRAGMA table_info(lane_sync_sources)") as cur:
            rows = await cur.fetchall()
        columns = {str(row["name"]) for row in rows}
        for name in ("history_item_turn_direction", "history_cursor_guard"):
            if name not in columns:
                await self._conn.execute(f"ALTER TABLE lane_sync_sources ADD COLUMN {name} TEXT")

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
                permission_profile TEXT,
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

    @_serialized_access
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
        provider: str = "codex",
        binding_id: str = DEFAULT_CODEX_BINDING_ID,
        provider_session_id: str | None = None,
    ) -> Lane:
        now = self._now()
        native_id = _initial_provider_session_id(id, provider, binding_id, provider_session_id)
        ref, ref_source, ref_payload, ref_mixer = await self._allocate_ref_parts(
            id, provider=provider, binding_id=binding_id, provider_session_id=native_id
        )
        lane = Lane(
            id=id,
            provider=provider,
            binding_id=binding_id,
            provider_session_id=native_id,
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
        async with self._transaction():
            await self._insert_lane(lane)
        return lane

    @_serialized_access
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
        provider: str = "codex",
        binding_id: str = DEFAULT_CODEX_BINDING_ID,
        provider_session_id: str | None = None,
    ) -> tuple[Lane, LaneSync]:
        if sync.lane != id:
            raise ValueError(f"sync lane {sync.lane!r} does not match lane id {id!r}")
        now = self._now()
        native_id = _initial_provider_session_id(id, provider, binding_id, provider_session_id)
        ref, ref_source, ref_payload, ref_mixer = await self._allocate_ref_parts(
            id, provider=provider, binding_id=binding_id, provider_session_id=native_id
        )
        lane = Lane(
            id=id,
            provider=provider,
            binding_id=binding_id,
            provider_session_id=native_id,
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
            "INSERT INTO lanes (id, provider, binding_id, provider_session_id, ref, ref_source, "
            "ref_payload, ref_mixer, handle, role, cwd, "
            "source, status, pinned, active_turn_id, latest_turn_id, latest_turn_status, "
            "latest_error, latest_error_at, created_at, updated_at, last_event_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                lane.id,
                lane.provider,
                lane.binding_id,
                lane.provider_session_id,
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

    async def _allocate_ref_parts(
        self,
        lane_id: str,
        *,
        provider: str = "codex",
        binding_id: str = DEFAULT_CODEX_BINDING_ID,
        provider_session_id: str | None = None,
    ) -> tuple[str, str, str, str]:
        native_id = (
            lane_id
            if provider == "codex"
            and binding_id == DEFAULT_CODEX_BINDING_ID
            and provider_session_id is None
            else provider_session_id
        )
        is_default_codex = (
            provider == "codex" and binding_id == DEFAULT_CODEX_BINDING_ID and native_id == lane_id
        )
        source = CODEX_REF_SOURCE if is_default_codex else GENERIC_REF_SOURCE
        payload = codex_ref_payload(lane_id) if is_default_codex else generic_ref_payload(lane_id)
        for mixer in BASE58BTC_ALPHABET:
            candidate = make_ref(source=source, payload=payload, mixer=mixer)
            existing = await self.find_lane_by_ref(candidate)
            if existing is None or existing.id == lane_id:
                return candidate, source, payload, mixer
        raise RuntimeError(
            f"ref mixer alphabet exhausted for lane payload {payload!r}; use the lane id"
        )

    @_serialized_access
    async def find_lane_by_provider_session(
        self, provider: str, binding_id: str, provider_session_id: str
    ) -> Lane | None:
        async with self._conn.execute(
            "SELECT * FROM lanes WHERE provider = ? AND binding_id = ? AND provider_session_id = ?",
            (provider, binding_id, provider_session_id),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_lane(row) if row is not None else None

    @_serialized_access
    async def update_lane_provider_session(
        self,
        lane_id: str,
        *,
        provider: str,
        binding_id: str,
        provider_session_id: str,
    ) -> Lane:
        """Record native continuation evidence without permitting a binding retarget."""

        if (
            provider == "codex"
            and binding_id == DEFAULT_CODEX_BINDING_ID
            and provider_session_id != lane_id
        ):
            raise ValidationError(
                "default-Codex provider session identity must equal the stable lane id"
            )
        async with self._transaction():
            cur = await self._conn.execute(
                "UPDATE lanes SET provider_session_id = ?, updated_at = ? "
                "WHERE id = ? AND provider = ? AND binding_id = ?",
                (provider_session_id, self.now_iso(), lane_id, provider, binding_id),
            )
            if cur.rowcount != 1:
                raise NotFoundError(
                    f"no lane {lane_id!r} for provider binding {provider}:{binding_id}"
                )
        return await self.get_lane(lane_id)

    @_serialized_access
    async def find_lane(self, lane_id: str) -> Lane | None:
        async with self._conn.execute("SELECT * FROM lanes WHERE id = ?", (lane_id,)) as cur:
            row = await cur.fetchone()
        return _row_to_lane(row) if row is not None else None

    @_serialized_access
    async def find_lane_by_ref(self, ref: str) -> Lane | None:
        async with self._conn.execute("SELECT * FROM lanes WHERE ref = ?", (ref,)) as cur:
            row = await cur.fetchone()
        return _row_to_lane(row) if row is not None else None

    @_serialized_access
    async def find_lane_by_handle(self, handle: str) -> Lane | None:
        async with self._conn.execute("SELECT * FROM lanes WHERE handle = ?", (handle,)) as cur:
            row = await cur.fetchone()
        return _row_to_lane(row) if row is not None else None

    @_serialized_access
    async def find_lanes_by_handle(self, handle: str) -> list[Lane]:
        async with self._conn.execute("SELECT * FROM lanes WHERE handle = ?", (handle,)) as cur:
            rows = await cur.fetchall()
        return [_row_to_lane(row) for row in rows]

    @_serialized_access
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

    @_serialized_access
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

    @_serialized_access
    async def get_lane(self, lane_id: str) -> Lane:
        lane = await self.find_lane(lane_id)
        if lane is None:
            raise NotFoundError(f"no lane {lane_id!r}")
        return lane

    @_serialized_access
    async def list_lanes(self, *, include_archived: bool = False) -> list[Lane]:
        sql = "SELECT * FROM lanes"
        if not include_archived:
            sql += " WHERE status != 'archived'"
        sql += " ORDER BY created_at"
        async with self._conn.execute(sql) as cur:
            rows = await cur.fetchall()
        return [_row_to_lane(row) for row in rows]

    @_serialized_access
    async def update_lane_status(self, lane_id: str, status: LaneStatus) -> None:
        async with self._write_lock:
            await self._conn.execute(
                "UPDATE lanes SET status = ?, updated_at = ? WHERE id = ?",
                (status, self._now().isoformat(), lane_id),
            )
            await self._conn.commit()

    @_serialized_access
    async def update_lane_handle(self, lane_id: str, handle: str) -> None:
        async with self._write_lock:
            await self._conn.execute(
                "UPDATE lanes SET handle = ?, updated_at = ? WHERE id = ?",
                (handle, self._now().isoformat(), lane_id),
            )
            await self._conn.commit()

    @_serialized_access
    async def set_active_turn(self, lane_id: str, turn_id: str | None) -> None:
        async with self._write_lock:
            await self._conn.execute(
                "UPDATE lanes SET active_turn_id = ?, updated_at = ? WHERE id = ?",
                (turn_id, self._now().isoformat(), lane_id),
            )
            await self._conn.commit()

    @_serialized_access
    async def record_turn_started(self, lane_id: str, turn_id: str | None) -> None:
        async with self._write_lock:
            await self._conn.execute(
                "UPDATE lanes SET active_turn_id = ?, latest_turn_id = ?, "
                "latest_turn_status = 'started', latest_error = NULL, latest_error_at = NULL, "
                "status = 'busy', updated_at = ? WHERE id = ?",
                (turn_id, turn_id, self._now().isoformat(), lane_id),
            )
            await self._conn.commit()

    @_serialized_access
    async def record_lane_activity_started(self, lane_id: str, turn_id: str) -> None:
        """Record provider activity without attributing it to a local receipt."""

        async with self._write_lock:
            await self._conn.execute(
                "UPDATE lanes SET active_turn_id = ?, status = 'busy', updated_at = ? "
                "WHERE id = ? AND status != 'archived'",
                (turn_id, self._now().isoformat(), lane_id),
            )
            await self._conn.commit()

    @_serialized_access
    async def record_lane_activity_idle_if_active(self, lane_id: str, turn_id: str) -> bool:
        """Clear only the same observed activity, never a newer active turn."""

        async with self._write_lock:
            cur = await self._conn.execute(
                "UPDATE lanes SET active_turn_id = NULL, status = 'idle', updated_at = ? "
                "WHERE id = ? AND (active_turn_id IS NULL OR active_turn_id = ?) "
                "AND status != 'archived'",
                (self._now().isoformat(), lane_id, turn_id),
            )
            await self._conn.commit()
        return cur.rowcount == 1

    @_serialized_access
    async def record_turn_completed(self, lane_id: str, turn_id: str | None) -> None:
        async with self._write_lock:
            await self._conn.execute(
                "UPDATE lanes SET active_turn_id = NULL, "
                "latest_turn_id = COALESCE(?, latest_turn_id), "
                "latest_turn_status = 'completed', latest_error = NULL, latest_error_at = NULL, "
                "status = 'idle', updated_at = ? WHERE id = ?",
                (turn_id, self._now().isoformat(), lane_id),
            )
            await self._conn.commit()

    @_serialized_access
    async def record_turn_completed_if_active(self, lane_id: str, turn_id: str | None) -> bool:
        """Complete lane activity only when this is still its active native run."""

        if turn_id is None:
            return False
        async with self._write_lock:
            cur = await self._conn.execute(
                "UPDATE lanes SET active_turn_id = NULL, latest_turn_id = ?, "
                "latest_turn_status = 'completed', latest_error = NULL, latest_error_at = NULL, "
                "status = 'idle', updated_at = ? WHERE id = ? "
                "AND (active_turn_id IS NULL OR active_turn_id = ?)",
                (turn_id, self._now().isoformat(), lane_id, turn_id),
            )
            await self._conn.commit()
        return cur.rowcount == 1

    @_serialized_access
    async def record_turn_failed(
        self,
        lane_id: str,
        turn_id: str | None,
        message: str | None,
        *,
        execution_status: Literal["failed", "interrupted"] = "failed",
    ) -> None:
        now = self._now().isoformat()
        async with self._write_lock:
            await self._conn.execute(
                "UPDATE lanes SET active_turn_id = NULL, "
                "latest_turn_id = COALESCE(?, latest_turn_id), "
                "latest_turn_status = ?, latest_error = ?, latest_error_at = ?, "
                "status = 'error', updated_at = ? WHERE id = ?",
                (
                    turn_id,
                    execution_status,
                    message,
                    now if message is not None else None,
                    now,
                    lane_id,
                ),
            )
            await self._conn.commit()

    @_serialized_access
    async def record_turn_failed_if_active(
        self,
        lane_id: str,
        turn_id: str | None,
        message: str | None,
        *,
        execution_status: Literal["failed", "interrupted"] = "failed",
    ) -> bool:
        """Fail lane activity only when this is still its active native run."""

        if turn_id is None:
            return False
        now = self._now().isoformat()
        async with self._write_lock:
            cur = await self._conn.execute(
                "UPDATE lanes SET active_turn_id = NULL, latest_turn_id = ?, "
                "latest_turn_status = ?, latest_error = ?, latest_error_at = ?, "
                "status = 'error', updated_at = ? WHERE id = ? "
                "AND (active_turn_id IS NULL OR active_turn_id = ?)",
                (
                    turn_id,
                    execution_status,
                    message,
                    now if message is not None else None,
                    now,
                    lane_id,
                    turn_id,
                ),
            )
            await self._conn.commit()
        return cur.rowcount == 1

    @_serialized_access
    async def record_turn_request_failed(self, lane_id: str, message: str | None) -> None:
        now = self._now().isoformat()
        async with self._write_lock:
            await self._conn.execute(
                "UPDATE lanes SET active_turn_id = NULL, latest_turn_id = NULL, "
                "latest_turn_status = 'failed', latest_error = ?, latest_error_at = ?, "
                "status = 'error', updated_at = ? WHERE id = ?",
                (message, now if message is not None else None, now, lane_id),
            )
            await self._conn.commit()

    @_serialized_access
    async def mark_lane_idle(self, lane_id: str) -> None:
        lane = await self.find_lane(lane_id)
        status: LaneStatus = (
            "error"
            if lane is not None and lane.latest_turn_status in ("failed", "interrupted")
            else "idle"
        )
        async with self._write_lock:
            await self._conn.execute(
                "UPDATE lanes SET active_turn_id = NULL, status = ?, updated_at = ? WHERE id = ?",
                (status, self._now().isoformat(), lane_id),
            )
            await self._conn.commit()

    @_serialized_access
    async def reconcile_lane_idle(
        self,
        lane_id: str,
        expected_updated_at: datetime,
        *,
        expected_status: LaneStatus,
        expected_active_turn_id: str | None,
    ) -> bool:
        """Mark a provider-confirmed idle lane only if no newer event won the race."""

        async with self._transaction():
            cur = await self._conn.execute(
                "UPDATE lanes SET status = 'idle', active_turn_id = NULL, updated_at = ? "
                "WHERE id = ? AND updated_at = ? AND status = ? AND active_turn_id IS ? "
                "AND status != 'archived' AND NOT EXISTS ("
                "SELECT 1 FROM deliveries WHERE deliveries.lane = lanes.id "
                "AND deliveries.status IN ('submitting', 'ambiguous')"
                ") AND NOT EXISTS ("
                "SELECT 1 FROM queued_messages WHERE queued_messages.lane = lanes.id "
                "AND queued_messages.status = 'sending')",
                (
                    self._now().isoformat(),
                    lane_id,
                    expected_updated_at.isoformat(),
                    expected_status,
                    expected_active_turn_id,
                ),
            )
        return cur.rowcount == 1

    @_serialized_access
    async def touch_lane_event(self, lane_id: str, when: datetime | None = None) -> None:
        stamp = (when or self._now()).isoformat()
        async with self._write_lock:
            await self._conn.execute(
                "UPDATE lanes SET last_event_at = ?, updated_at = ? WHERE id = ?",
                (stamp, self._now().isoformat(), lane_id),
            )
            await self._conn.commit()

    # --- lane launches --------------------------------------------------------

    @_serialized_access
    async def reserve_lane_launch(
        self,
        *,
        key: str | None,
        submitted_payload: str,
        request_payload: str,
        handle: str,
        cwd: str,
        provider: str,
        binding_id: str,
        generation: str,
    ) -> tuple[LaneLaunch, Lane, bool]:
        """Atomically reserve one opaque lane and its immutable creation attempt."""

        if not generation:
            raise ValidationError("lane launch generation cannot be empty")
        if key is not None:
            existing = await self.get_lane_launch_by_key(key)
            if existing is not None:
                if existing.submitted_payload != submitted_payload:
                    raise DeliveryConflictError(f"lane launch key {key!r} is already bound")
                return existing, await self.get_lane(existing.lane), False
            if await self.get_delivery_by_key(key) is not None:
                raise DeliveryConflictError(f"lane launch key {key!r} is already bound")

        lane_id = f"dsp_{uuid4().hex}"
        now = self._now()
        native_id = _initial_provider_session_id(lane_id, provider, binding_id, None)
        if native_id is not None:
            raise ValidationError("lane launch reservations require an unresolved provider session")
        ref, ref_source, ref_payload, ref_mixer = await self._allocate_ref_parts(
            lane_id,
            provider=provider,
            binding_id=binding_id,
            provider_session_id=native_id,
        )
        lane = Lane(
            id=lane_id,
            provider=provider,
            binding_id=binding_id,
            provider_session_id=native_id,
            ref=ref,
            ref_source=ref_source,
            ref_payload=ref_payload,
            ref_mixer=ref_mixer,
            handle=handle,
            cwd=cwd,
            source="own",
            status="unknown",
            created_at=now,
            updated_at=now,
        )
        launch = LaneLaunch(
            lane=lane_id,
            key=key,
            submitted_payload=submitted_payload,
            request_payload=request_payload,
            provider=provider,
            binding_id=binding_id,
            generation=generation,
            created_at=now,
            updated_at=now,
        )
        async with self._transaction():
            await self._insert_lane(lane)
            await self._insert_lane_launch(launch)
        return launch, lane, True

    async def _insert_lane_launch(self, launch: LaneLaunch) -> None:
        await self._conn.execute(
            "INSERT INTO lane_launches (lane, key, submitted_payload, request_payload, provider, "
            "binding_id, generation, status, runtime_session_id, stored_session_id, "
            "effective_cwd, first_delivery_id, error, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                launch.lane,
                launch.key,
                launch.submitted_payload,
                launch.request_payload,
                launch.provider,
                launch.binding_id,
                launch.generation,
                launch.status,
                launch.runtime_session_id,
                launch.stored_session_id,
                launch.effective_cwd,
                launch.first_delivery_id,
                launch.error,
                launch.created_at.isoformat(),
                launch.updated_at.isoformat(),
            ),
        )

    @_serialized_access
    async def get_lane_launch(self, lane_id: str) -> LaneLaunch:
        async with self._conn.execute(
            "SELECT * FROM lane_launches WHERE lane = ?", (lane_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise NotFoundError(f"no lane launch {lane_id!r}")
        return _row_to_lane_launch(row)

    @_serialized_access
    async def get_lane_launch_by_key(self, key: str) -> LaneLaunch | None:
        async with self._conn.execute("SELECT * FROM lane_launches WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
        return _row_to_lane_launch(row) if row is not None else None

    @_serialized_access
    async def get_caller_key_binding(
        self, key: str
    ) -> tuple[Literal["launch"], LaneLaunch] | tuple[Literal["delivery"], DeliveryReceipt] | None:
        """Atomically identify one key across the shared launch/delivery namespace."""

        async with self._conn.execute("SELECT * FROM lane_launches WHERE key = ?", (key,)) as cur:
            launch_row = await cur.fetchone()
        if launch_row is not None:
            return "launch", _row_to_lane_launch(launch_row)
        async with self._conn.execute("SELECT * FROM deliveries WHERE key = ?", (key,)) as cur:
            delivery_row = await cur.fetchone()
        if delivery_row is not None:
            return "delivery", _row_to_delivery(delivery_row)
        return None

    @_serialized_access
    async def claim_lane_launch(self, lane_id: str, *, generation: str) -> bool:
        """Claim an unattempted create once; an interrupted claim remains held."""

        async with self._transaction():
            cur = await self._conn.execute(
                "UPDATE lane_launches SET status = 'creating', updated_at = ? "
                "WHERE lane = ? AND generation = ? AND status = 'reserved'",
                (self.now_iso(), lane_id, generation),
            )
        return cur.rowcount == 1

    @_serialized_access
    async def record_lane_launch_mapping(
        self,
        lane_id: str,
        *,
        generation: str,
        runtime_session_id: str,
        stored_session_id: str,
        effective_cwd: str,
        first_delivery_required: bool,
    ) -> LaneLaunch:
        """Persist a positive native mapping without reconstructing a first submit."""

        if not runtime_session_id or not stored_session_id or not effective_cwd:
            raise ValidationError("native lane launch mapping fields cannot be empty")
        async with self._transaction():
            async with self._conn.execute(
                "SELECT * FROM lane_launches WHERE lane = ?", (lane_id,)
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                raise NotFoundError(f"no lane launch {lane_id!r}")
            launch = _row_to_lane_launch(row)
            if (
                launch.status != "creating"
                or launch.generation != generation
                or launch.runtime_session_id is not None
                or launch.stored_session_id is not None
            ):
                raise ValidationError(f"cannot record native mapping for lane launch {lane_id!r}")
            status = "creating" if first_delivery_required else "created"
            updated_at = self.now_iso()
            lane_status = "unknown" if first_delivery_required else "idle"
            changed = await self._conn.execute(
                "UPDATE lanes SET provider_session_id = ?, cwd = ?, status = ?, updated_at = ? "
                "WHERE id = ? AND provider = ? AND binding_id = ? "
                "AND provider_session_id IS NULL",
                (
                    stored_session_id,
                    effective_cwd,
                    lane_status,
                    updated_at,
                    lane_id,
                    launch.provider,
                    launch.binding_id,
                ),
            )
            if changed.rowcount != 1:
                raise ValidationError(f"cannot record native mapping for lane launch {lane_id!r}")
            await self._conn.execute(
                "UPDATE lane_launches SET runtime_session_id = ?, stored_session_id = ?, "
                "effective_cwd = ?, status = ?, updated_at = ? WHERE lane = ?",
                (
                    runtime_session_id,
                    stored_session_id,
                    effective_cwd,
                    status,
                    updated_at,
                    lane_id,
                ),
            )
        return await self.get_lane_launch(lane_id)

    @_serialized_access
    async def reserve_lane_launch_first_delivery(
        self,
        lane_id: str,
        *,
        submitted_payload: str,
        payload: str,
        text: str,
        delivery_id: str | None = None,
    ) -> DeliveryReceipt:
        """Atomically reserve and link the first submit after positive creation."""

        receipt_id = delivery_id or str(uuid4())
        now = self.now_iso()
        async with self._transaction():
            async with self._conn.execute(
                "SELECT * FROM lane_launches WHERE lane = ?", (lane_id,)
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                raise NotFoundError(f"no lane launch {lane_id!r}")
            launch = _row_to_lane_launch(row)
            if (
                launch.status != "creating"
                or launch.runtime_session_id is None
                or launch.stored_session_id is None
                or launch.first_delivery_id is not None
            ):
                raise ValidationError(f"cannot reserve first delivery for lane launch {lane_id!r}")
            async with self._conn.execute(
                "SELECT provider_session_id FROM lanes WHERE id = ? AND provider = ? "
                "AND binding_id = ?",
                (lane_id, launch.provider, launch.binding_id),
            ) as cur:
                lane_row = await cur.fetchone()
            if lane_row is None or lane_row["provider_session_id"] != launch.stored_session_id:
                raise ValidationError(f"cannot reserve first delivery for lane launch {lane_id!r}")
            await self._insert_delivery_reservation(
                receipt_id=receipt_id,
                key=None,
                lane=lane_id,
                mode="send",
                submitted_payload=submitted_payload,
                payload=payload,
                text=text,
                transport="turn",
                provider=launch.provider,
                binding_id=launch.binding_id,
                native_session_id=launch.stored_session_id,
                correlation_id=receipt_id,
                now=now,
            )
            await self._conn.execute(
                "UPDATE lane_launches SET first_delivery_id = ?, status = 'created', "
                "updated_at = ? WHERE lane = ?",
                (receipt_id, now, lane_id),
            )
            await self._conn.execute(
                "UPDATE lanes SET status = 'idle', updated_at = ? WHERE id = ?",
                (now, lane_id),
            )
        return await self.get_delivery(receipt_id)

    async def _finish_lane_launch(
        self,
        lane_id: str,
        *,
        generation: str,
        status: LaneLaunchStatus,
        error: str,
    ) -> LaneLaunch:
        async with self._transaction():
            changed = await self._conn.execute(
                "UPDATE lane_launches SET status = ?, error = ?, updated_at = ? "
                "WHERE lane = ? AND generation = ? AND status = 'creating'",
                (status, error, self.now_iso(), lane_id, generation),
            )
            if changed.rowcount != 1:
                raise ValidationError(f"cannot mark lane launch {lane_id!r} {status}")
            await self._conn.execute(
                "UPDATE lanes SET status = 'error', updated_at = ? WHERE id = ?",
                (self.now_iso(), lane_id),
            )
        return await self.get_lane_launch(lane_id)

    @_serialized_access
    async def fail_lane_launch(self, lane_id: str, *, generation: str, error: str) -> LaneLaunch:
        return await self._finish_lane_launch(
            lane_id, generation=generation, status="failed", error=error
        )

    @_serialized_access
    async def mark_lane_launch_ambiguous(
        self, lane_id: str, *, generation: str, error: str
    ) -> LaneLaunch:
        return await self._finish_lane_launch(
            lane_id, generation=generation, status="ambiguous", error=error
        )

    # --- deliveries ----------------------------------------------------------

    @_serialized_access
    async def reserve_delivery(
        self,
        *,
        key: str | None,
        lane: str,
        mode: DeliveryMode,
        payload: str,
        text: str,
        submitted_payload: str | None = None,
        delivery_id: str | None = None,
        transport: DeliveryTransport = "turn",
        provider: str = "codex",
        binding_id: str = DEFAULT_CODEX_BINDING_ID,
        native_session_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[DeliveryReceipt, bool]:
        """Reserve an idempotent delivery and its optional legacy queue row."""

        receipt_id = delivery_id or str(uuid4())
        now = self._now().isoformat()
        if key is not None and await self.get_lane_launch_by_key(key) is not None:
            raise DeliveryConflictError(f"delivery key {key!r} is already bound")
        try:
            async with self._transaction():
                await self._insert_delivery_reservation(
                    receipt_id=receipt_id,
                    key=key,
                    lane=lane,
                    mode=mode,
                    submitted_payload=submitted_payload,
                    payload=payload,
                    text=text,
                    transport=transport,
                    provider=provider,
                    binding_id=binding_id,
                    native_session_id=native_session_id or lane,
                    correlation_id=correlation_id or receipt_id,
                    now=now,
                )
        except aiosqlite.IntegrityError:
            existing = await self.get_delivery_by_key(key) if key is not None else None
            if existing is not None:
                exact_submitted_replay = (
                    existing.submitted_payload is not None
                    and submitted_payload is not None
                    and existing.submitted_payload == submitted_payload
                )
                legacy_effective_replay = (
                    existing.submitted_payload is None
                    and submitted_payload is None
                    and (existing.lane, existing.mode, existing.payload, existing.transport)
                    == (lane, mode, payload, transport)
                )
                if not exact_submitted_replay and not legacy_effective_replay:
                    raise DeliveryConflictError(f"delivery key {key!r} is already bound") from None
                return existing, False
            raise
        return await self.get_delivery(receipt_id), True

    async def _insert_delivery_reservation(
        self,
        *,
        receipt_id: str,
        key: str | None,
        lane: str,
        mode: DeliveryMode,
        submitted_payload: str | None,
        payload: str,
        text: str,
        transport: DeliveryTransport,
        provider: str,
        binding_id: str,
        native_session_id: str,
        correlation_id: str,
        now: str,
    ) -> None:
        """Insert one delivery reservation inside the caller's transaction."""

        await self._conn.execute(
            "INSERT INTO deliveries "
            "(id, key, lane, mode, submitted_payload, payload, transport, provider, "
            "binding_id, native_session_id, correlation_id, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
            (
                receipt_id,
                key,
                lane,
                mode,
                submitted_payload,
                payload,
                transport,
                provider,
                binding_id,
                native_session_id,
                correlation_id,
                now,
                now,
            ),
        )
        if mode == "queue" and transport == "turn":
            cur = await self._conn.execute(
                "INSERT INTO queued_messages "
                "(lane, text, content, status, created_at, updated_at) "
                "VALUES (?, ?, '[]', 'pending', ?, ?)",
                (lane, text, now, now),
            )
            queue_id = cur.lastrowid
            if queue_id is None:
                raise RuntimeError("queued message insert did not return an id")
            await self._conn.execute(
                "UPDATE deliveries SET queue_id = ? WHERE id = ?",
                (queue_id, receipt_id),
            )

    @_serialized_access
    async def get_delivery(self, delivery_id: str) -> DeliveryReceipt:
        async with self._conn.execute(
            "SELECT * FROM deliveries WHERE id = ?", (delivery_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise NotFoundError(f"no delivery {delivery_id!r}")
        return _row_to_delivery(row)

    @_serialized_access
    async def get_delivery_by_key(self, key: str) -> DeliveryReceipt | None:
        async with self._conn.execute("SELECT * FROM deliveries WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
        return _row_to_delivery(row) if row is not None else None

    @_serialized_access
    async def delivery_for_queue(self, queue_id: int) -> DeliveryReceipt | None:
        async with self._conn.execute(
            "SELECT * FROM deliveries WHERE queue_id = ?", (queue_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_delivery(row) if row is not None else None

    @_serialized_access
    async def claim_delivery(self, delivery_id: str) -> bool:
        """Claim one queued delivery while excluding uncertain work on its lane."""

        now = self._now().isoformat()
        async with self._transaction():
            cur = await self._conn.execute(
                "UPDATE deliveries SET status = 'submitting', updated_at = ? "
                "WHERE id = ? AND status = 'queued' "
                "AND NOT EXISTS ("
                "SELECT 1 FROM deliveries held "
                "WHERE held.lane = deliveries.lane AND held.id != deliveries.id "
                "AND held.status IN ('submitting', 'ambiguous')"
                ") AND (transport != 'native_queue' OR NOT EXISTS ("
                "SELECT 1 FROM deliveries earlier "
                "WHERE earlier.lane = deliveries.lane AND earlier.transport = 'native_queue' "
                "AND earlier.status = 'queued' AND earlier.rowid < deliveries.rowid"
                ")) AND (transport != 'native_queue' OR NOT EXISTS ("
                "SELECT 1 FROM queued_messages legacy WHERE legacy.lane = deliveries.lane "
                "AND legacy.status IN ('pending', 'sending')"
                ")) AND (queue_id IS NULL OR (EXISTS ("
                "SELECT 1 FROM queued_messages queued "
                "WHERE queued.id = deliveries.queue_id AND queued.status = 'pending'"
                ") AND NOT EXISTS ("
                "SELECT 1 FROM queued_messages earlier "
                "WHERE earlier.lane = deliveries.lane AND earlier.status = 'pending' "
                "AND earlier.id < deliveries.queue_id"
                ")))",
                (now, delivery_id),
            )
            if cur.rowcount != 1:
                return False
            await self._conn.execute(
                "UPDATE queued_messages SET status = 'sending', updated_at = ? "
                "WHERE id = (SELECT queue_id FROM deliveries WHERE id = ?) "
                "AND status = 'pending'",
                (now, delivery_id),
            )
        return True

    @_serialized_access
    async def update_delivery(
        self,
        delivery_id: str,
        *,
        status: DeliveryStatus,
        turn_id: str | None = None,
        error: str | None = None,
        execution_status: DeliveryExecutionStatus | None = None,
        submission_id: str | None = None,
    ) -> DeliveryReceipt:
        """Record observed delivery progress and update its linked queue row."""

        now = self._now().isoformat()
        async with self._transaction():
            changed = await self._conn.execute(
                "UPDATE deliveries SET "
                "status = CASE WHEN status = 'completed' AND ? = 'accepted' "
                "THEN status ELSE ? END, "
                "turn_id = COALESCE(turn_id, ?), "
                "submission_id = COALESCE(submission_id, ?), error = ?, "
                "execution_status = COALESCE(?, execution_status), "
                "evidence_partial = CASE WHEN ? = 'accepted' THEN 0 "
                "ELSE evidence_partial END, updated_at = ? "
                "WHERE id = ? AND NOT ("
                "(? IN ('ambiguous', 'submitting', 'failed') "
                "AND status IN ('accepted', 'completed', 'failed')) "
                "OR (? = 'accepted' AND status = 'completed') "
                "OR (status = 'accepted' AND COALESCE(execution_status, '') "
                "IN ('failed', 'interrupted')))",
                (
                    status,
                    status,
                    turn_id,
                    submission_id,
                    error,
                    execution_status,
                    status,
                    now,
                    delivery_id,
                    status,
                    status,
                ),
            )
            async with self._conn.execute(
                "SELECT queue_id FROM deliveries WHERE id = ?", (delivery_id,)
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                raise NotFoundError(f"no delivery {delivery_id!r}")
            queue_id = row["queue_id"]
            if (
                changed.rowcount == 1
                and queue_id is not None
                and status in ("accepted", "completed")
            ):
                await self._conn.execute(
                    "UPDATE queued_messages SET status = 'sent', updated_at = ?, error = NULL "
                    "WHERE id = ?",
                    (now, queue_id),
                )
            elif changed.rowcount == 1 and queue_id is not None and status == "failed":
                await self._conn.execute(
                    "UPDATE queued_messages SET status = 'error', updated_at = ?, error = ? "
                    "WHERE id = ?",
                    (now, error, queue_id),
                )
        return await self.get_delivery(delivery_id)

    @_serialized_access
    async def apply_receipt_observation(
        self, observation: ProviderObservation
    ) -> ReceiptTransition:
        """Apply evidence only when it identifies one frozen delivery target exactly."""

        correlation = observation.correlation
        if correlation.delivery_id is None or correlation.correlation_id is None:
            receipt = (
                await self.get_delivery(correlation.delivery_id)
                if correlation.delivery_id is not None
                else None
            )
            return ReceiptTransition(
                receipt=receipt,
                matched=False,
                reason="missing receipt correlation",
            )
        receipt = await self.get_delivery(correlation.delivery_id)
        if (receipt.provider, receipt.binding_id, receipt.native_session_id) != (
            observation.provider,
            observation.binding_id,
            observation.native_session_id,
        ):
            return ReceiptTransition(
                receipt=receipt, matched=False, reason="provider binding mismatch"
            )
        if receipt.correlation_id != correlation.correlation_id:
            return ReceiptTransition(
                receipt=receipt, matched=False, reason="request correlation mismatch"
            )
        if receipt.turn_id is not None and receipt.turn_id != correlation.native_run_id:
            return ReceiptTransition(receipt=receipt, matched=False, reason="native run mismatch")
        if (
            receipt.submission_id is not None
            and correlation.native_submission_id is not None
            and receipt.submission_id != correlation.native_submission_id
        ):
            return ReceiptTransition(
                receipt=receipt, matched=False, reason="native submission mismatch"
            )
        if observation.kind in {"started", "completed", "failed", "interrupted"} and (
            correlation.native_run_id is None
        ):
            return ReceiptTransition(
                receipt=receipt, matched=False, reason="missing native run evidence"
            )
        if observation.kind == "accepted" and (
            correlation.native_run_id is None and correlation.native_submission_id is None
        ):
            return ReceiptTransition(
                receipt=receipt, matched=False, reason="missing positive provider evidence"
            )
        if receipt.status == "completed" or receipt.execution_status in {
            "completed",
            "failed",
            "interrupted",
        }:
            return ReceiptTransition(
                receipt=receipt,
                matched=True,
                reason="terminal receipt already settled",
            )
        if (
            receipt.status == "accepted"
            and receipt.execution_status is None
            and receipt.evidence_partial
            and receipt.evidence_source == "submit_result"
            and observation.kind in {"started", "completed", "failed", "interrupted"}
        ):
            return ReceiptTransition(
                receipt=receipt,
                matched=True,
                reason="partial accepted receipt remains held",
            )
        status: DeliveryStatus = receipt.status
        execution_status = receipt.execution_status
        if observation.kind in {"accepted", "started"}:
            if receipt.status not in {"completed", "failed"}:
                status = "accepted"
            if observation.kind == "started" and execution_status not in {
                "completed",
                "failed",
                "interrupted",
            }:
                execution_status = "inProgress"
        elif observation.kind == "completed":
            status = "completed"
            execution_status = "completed"
        elif observation.kind in {"failed", "interrupted"}:
            status = "accepted"
            execution_status = cast(DeliveryExecutionStatus, observation.kind)
        elif observation.kind == "uncertain" and receipt.status not in {
            "accepted",
            "completed",
            "failed",
        }:
            status = "ambiguous"

        provider_time = (
            observation.provider_time.isoformat() if observation.provider_time is not None else None
        )
        received_at = observation.received_at.isoformat()
        updated_at = self.now_iso()
        async with self._transaction():
            changed = await self._conn.execute(
                "UPDATE deliveries SET status = ?, execution_status = ?, "
                "turn_id = COALESCE(turn_id, ?), submission_id = COALESCE(submission_id, ?), "
                "error = ?, evidence_source = ?, evidence_provider_time = ?, "
                "evidence_received_at = ?, evidence_partial = CASE "
                "WHEN status = 'accepted' AND execution_status IS NULL "
                "AND evidence_partial = 1 AND evidence_source = 'submit_result' "
                "THEN 1 ELSE ? END, "
                "evidence_generation = ?, "
                "updated_at = ? WHERE id = ? AND provider = ? AND binding_id = ? "
                "AND native_session_id = ? AND correlation_id = ? "
                "AND (turn_id IS NULL OR ? IS NULL OR turn_id = ?) "
                "AND (submission_id IS NULL OR ? IS NULL OR submission_id = ?)",
                (
                    status,
                    execution_status,
                    correlation.native_run_id,
                    correlation.native_submission_id,
                    observation.reason,
                    observation.source,
                    provider_time,
                    received_at,
                    observation.partial,
                    observation.generation,
                    updated_at,
                    receipt.id,
                    observation.provider,
                    observation.binding_id,
                    observation.native_session_id,
                    correlation.correlation_id,
                    correlation.native_run_id,
                    correlation.native_run_id,
                    correlation.native_submission_id,
                    correlation.native_submission_id,
                ),
            )
            if (
                changed.rowcount == 1
                and receipt.queue_id is not None
                and status in {"accepted", "completed"}
            ):
                await self._conn.execute(
                    "UPDATE queued_messages SET status = 'sent', updated_at = ?, error = NULL "
                    "WHERE id = ?",
                    (updated_at, receipt.queue_id),
                )
        current = await self.get_delivery(receipt.id)
        if changed.rowcount != 1:
            reason = (
                "native run mismatch"
                if current.turn_id != correlation.native_run_id
                else "native submission mismatch"
            )
            return ReceiptTransition(receipt=current, matched=False, reason=reason)
        return ReceiptTransition(receipt=current, matched=True, changed=current != receipt)

    @_serialized_access
    async def lane_delivery_held(self, lane: str) -> bool:
        async with self._conn.execute(
            "SELECT 1 FROM deliveries WHERE lane = ? "
            "AND (status IN ('submitting', 'ambiguous') OR "
            "(status = 'accepted' AND execution_status IS NULL AND evidence_partial = 1)) "
            "LIMIT 1",
            (lane,),
        ) as cur:
            return await cur.fetchone() is not None

    @_serialized_access
    async def list_waiting_deliveries(self) -> list[DeliveryReceipt]:
        async with self._conn.execute(
            "SELECT * FROM deliveries WHERE status = 'queued' ORDER BY created_at, id"
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_delivery(row) for row in rows]

    @_serialized_access
    async def list_unresolved_deliveries(self) -> list[DeliveryReceipt]:
        async with self._conn.execute(
            "SELECT * FROM deliveries WHERE status IN ('submitting', 'ambiguous') "
            "ORDER BY created_at, id"
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_delivery(row) for row in rows]

    @_serialized_access
    async def list_accepted_unfinished_deliveries(self) -> list[DeliveryReceipt]:
        async with self._conn.execute(
            "SELECT * FROM deliveries WHERE status = 'accepted' "
            "AND (execution_status IS NULL OR execution_status = 'inProgress') "
            "ORDER BY created_at, id"
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_delivery(row) for row in rows]

    @_serialized_access
    async def delivery_for_turn(self, lane: str, turn_id: str) -> list[DeliveryReceipt]:
        async with self._conn.execute(
            "SELECT * FROM deliveries WHERE lane = ? AND turn_id = ? ORDER BY created_at, id",
            (lane, turn_id),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_delivery(row) for row in rows]

    @_serialized_access
    async def delivery_for_provider_run(
        self, provider: str, binding_id: str, native_session_id: str, native_run_id: str
    ) -> list[DeliveryReceipt]:
        async with self._conn.execute(
            "SELECT * FROM deliveries WHERE provider = ? AND binding_id = ? "
            "AND native_session_id = ? AND turn_id = ? ORDER BY created_at, id",
            (provider, binding_id, native_session_id, native_run_id),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_delivery(row) for row in rows]

    @_serialized_access
    async def recover_deliveries(self) -> int:
        now = self._now().isoformat()
        async with self._transaction():
            cur = await self._conn.execute(
                "UPDATE deliveries SET status = 'ambiguous', updated_at = ? "
                "WHERE status = 'submitting'",
                (now,),
            )
        return cur.rowcount

    @_serialized_access
    async def note_delivery_check(
        self,
        delivery_id: str,
        error: str,
        *,
        max_attempts: int | None = None,
    ) -> DeliveryReceipt | None:
        async with self._transaction():
            cur = await self._conn.execute(
                "UPDATE deliveries SET reconciliation_attempts = reconciliation_attempts + 1, "
                "error = ?, updated_at = ? WHERE id = ? AND status = 'ambiguous' "
                "AND (? IS NULL OR reconciliation_attempts < ?)",
                (error, self._now().isoformat(), delivery_id, max_attempts, max_attempts),
            )
            if cur.rowcount == 1:
                return await self.get_delivery(delivery_id)
            async with self._conn.execute(
                "SELECT 1 FROM deliveries WHERE id = ?", (delivery_id,)
            ) as existing:
                found = await existing.fetchone()
            if found is None:
                raise NotFoundError(f"no delivery {delivery_id!r}")
        return None

    # --- queued messages ------------------------------------------------------

    @_serialized_access
    async def enqueue_message(
        self,
        *,
        lane: str,
        text: str,
        content: list[dict[str, object]] | None = None,
    ) -> QueuedMessage:
        now = self._now().isoformat()
        async with self._transaction():
            cur = await self._conn.execute(
                "INSERT INTO queued_messages "
                "(lane, text, content, status, created_at, updated_at) "
                "VALUES (?, ?, ?, 'pending', ?, ?)",
                (lane, text, json.dumps(content or []), now, now),
            )
            message_id = cur.lastrowid
        if message_id is None:
            raise RuntimeError("queued message insert did not return an id")
        return await self.get_queued_message(message_id)

    @_serialized_access
    async def get_queued_message(self, message_id: int) -> QueuedMessage:
        async with self._conn.execute(
            "SELECT * FROM queued_messages WHERE id = ?", (message_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise NotFoundError(f"no queued message {message_id!r}")
        return _row_to_queued_message(row)

    @_serialized_access
    async def next_pending_message(self, lane: str) -> QueuedMessage | None:
        async with self._conn.execute(
            "SELECT * FROM queued_messages WHERE lane = ? AND status = 'pending' "
            "ORDER BY id LIMIT 1",
            (lane,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_queued_message(row) if row is not None else None

    @_serialized_access
    async def pending_message_count(self, lane: str) -> int:
        async with self._conn.execute(
            "SELECT COUNT(*) AS count FROM queued_messages WHERE lane = ? AND status = 'pending'",
            (lane,),
        ) as cur:
            row = await cur.fetchone()
        return int(row["count"]) if row is not None else 0

    @_serialized_access
    async def claim_queued_message(self, message_id: int) -> bool:
        async with self._transaction():
            cur = await self._conn.execute(
                "UPDATE queued_messages SET status = 'sending', updated_at = ? "
                "WHERE id = ? AND status = 'pending' AND NOT EXISTS ("
                "SELECT 1 FROM deliveries WHERE deliveries.lane = queued_messages.lane "
                "AND deliveries.status IN ('submitting', 'ambiguous'))",
                (self._now().isoformat(), message_id),
            )
        return cur.rowcount == 1

    @_serialized_access
    async def complete_queued_message(self, message_id: int) -> None:
        async with self._transaction():
            await self._conn.execute(
                "UPDATE queued_messages SET status = 'sent', updated_at = ?, "
                "error = NULL WHERE id = ?",
                (self._now().isoformat(), message_id),
            )

    @_serialized_access
    async def fail_queued_message(self, message_id: int, error: str) -> None:
        async with self._transaction():
            await self._conn.execute(
                "UPDATE queued_messages SET status = 'error', updated_at = ?, "
                "error = ? WHERE id = ?",
                (self._now().isoformat(), error, message_id),
            )

    @_serialized_access
    async def reset_sending_messages(self) -> int:
        async with self._transaction():
            cur = await self._conn.execute(
                "UPDATE queued_messages SET status = 'pending', updated_at = ? "
                "WHERE status = 'sending' AND NOT EXISTS ("
                "SELECT 1 FROM deliveries WHERE deliveries.queue_id = queued_messages.id "
                "AND deliveries.status IN ('submitting', 'ambiguous'))",
                (self._now().isoformat(),),
            )
        return cur.rowcount

    # --- inbox messages -------------------------------------------------------

    @_serialized_access
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
        async with self._transaction():
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
            message_id = cur.lastrowid
        if message_id is None:
            raise RuntimeError("inbox message insert did not return an id")
        return await self.get_inbox_message(message_id)

    @_serialized_access
    async def get_inbox_message(self, message_id: int) -> InboxMessage:
        async with self._conn.execute(
            "SELECT * FROM inbox_messages WHERE id = ?", (message_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise NotFoundError(f"no inbox message {message_id!r}")
        return _row_to_inbox_message(row)

    @_serialized_access
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

    @_serialized_access
    async def ack_inbox_message(self, message_id: int) -> InboxMessage:
        now = self._now().isoformat()
        async with self._transaction():
            cur = await self._conn.execute(
                "UPDATE inbox_messages SET state = 'acked', acked_at = ?, delivered_at = "
                "COALESCE(delivered_at, ?)"
                " WHERE id = ? AND state != 'acked'",
                (now, now, message_id),
            )
        if cur.rowcount == 0:
            return await self.get_inbox_message(message_id)
        return await self.get_inbox_message(message_id)

    @_serialized_access
    async def ack_inbox_messages_for_lane(self, lane: str) -> int:
        async with self._transaction():
            cur = await self._conn.execute(
                "UPDATE inbox_messages SET state = 'acked', acked_at = ?, delivered_at = "
                "COALESCE(delivered_at, ?) WHERE recipient_lane = ? AND state = 'pending'",
                (self._now().isoformat(), self._now().isoformat(), lane),
            )
        return cur.rowcount

    @_serialized_access
    async def mark_inbox_delivered(
        self, message_id: int, *, queued_message_id: int | None = None, ack: bool = False
    ) -> InboxMessage:
        now = self._now().isoformat()
        state = "acked" if ack else "pending"
        async with self._transaction():
            await self._conn.execute(
                "UPDATE inbox_messages SET delivered_at = ?, queued_message_id = ?, state = ?, "
                "acked_at = CASE WHEN ? THEN ? ELSE acked_at END WHERE id = ?",
                (now, queued_message_id, state, int(ack), now, message_id),
            )
        return await self.get_inbox_message(message_id)

    @_serialized_access
    async def mark_inbox_delivered_for_queue(
        self, queued_message_id: int, *, ack: bool = False
    ) -> int:
        now = self._now().isoformat()
        state = "acked" if ack else "pending"
        async with self._transaction():
            cur = await self._conn.execute(
                "UPDATE inbox_messages SET delivered_at = ?, state = ?, "
                "acked_at = CASE WHEN ? THEN ? ELSE acked_at END "
                "WHERE queued_message_id = ?",
                (now, state, int(ack), now, queued_message_id),
            )
        return cur.rowcount

    # --- subscriptions --------------------------------------------------------

    @_serialized_access
    async def add_subscription(self, subscription: Subscription) -> Subscription:
        async with self._transaction():
            await self._conn.execute(
                "INSERT INTO subscriptions (id, target_lane, subscriber_lane, when_spec, "
                "delivery, deliver_policy, tail, once, ack_policy, attribution, state, "
                "created_at, updated_at, last_matched_at, last_inbox_message_id) "
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
        return await self.get_subscription(subscription.id)

    @_serialized_access
    async def get_subscription(self, subscription_id: str) -> Subscription:
        async with self._conn.execute(
            "SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise NotFoundError(f"no subscription {subscription_id!r}")
        return _row_to_subscription(row)

    @_serialized_access
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

    @_serialized_access
    async def remove_subscription(self, subscription_id: str) -> bool:
        async with self._transaction():
            cur = await self._conn.execute(
                "DELETE FROM subscriptions WHERE id = ?", (subscription_id,)
            )
        return cur.rowcount > 0

    @_serialized_access
    async def mark_subscription_matched(
        self, subscription_id: str, *, inbox_message_id: int
    ) -> Subscription:
        now = self._now().isoformat()
        async with self._transaction():
            await self._conn.execute(
                "UPDATE subscriptions SET last_matched_at = ?, last_inbox_message_id = ?, "
                "state = CASE WHEN once = 1 THEN 'done' ELSE state END, "
                "updated_at = ? WHERE id = ?",
                (now, inbox_message_id, now, subscription_id),
            )
        return await self.get_subscription(subscription_id)

    # --- lane sync -----------------------------------------------------------

    @_serialized_access
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
            "tail_offset, next_offset, last_synced_at, error, history_source, history_cursor, "
            "history_backwards_cursor, history_recent_cursor, "
            "history_pending_backwards_cursor, history_item_turn_id, history_item_cursor, "
            "history_item_turn_cursor, history_item_turn_direction, history_cursor_guard, "
            "history_complete, history_capability, observation_enabled, "
            "pages_scanned, "
            "turns_indexed, items_indexed, unchanged_skipped, scanned_bytes, duration_ms, "
            "truncated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(lane) DO UPDATE SET state = excluded.state, "
            "source_path = excluded.source_path, source_device = excluded.source_device, "
            "source_inode = excluded.source_inode, source_size = excluded.source_size, "
            "source_mtime_ns = excluded.source_mtime_ns, line_count = excluded.line_count, "
            "first_offset = excluded.first_offset, tail_offset = excluded.tail_offset, "
            "next_offset = excluded.next_offset, "
            "last_synced_at = excluded.last_synced_at, error = excluded.error, "
            "history_source = excluded.history_source, "
            "history_cursor = excluded.history_cursor, "
            "history_backwards_cursor = excluded.history_backwards_cursor, "
            "history_recent_cursor = excluded.history_recent_cursor, "
            "history_pending_backwards_cursor = excluded.history_pending_backwards_cursor, "
            "history_item_turn_id = excluded.history_item_turn_id, "
            "history_item_turn_cursor = excluded.history_item_turn_cursor, "
            "history_item_turn_direction = excluded.history_item_turn_direction, "
            "history_item_cursor = excluded.history_item_cursor, "
            "history_cursor_guard = excluded.history_cursor_guard, "
            "history_complete = excluded.history_complete, "
            "history_capability = excluded.history_capability, "
            "observation_enabled = excluded.observation_enabled, "
            "pages_scanned = excluded.pages_scanned, turns_indexed = excluded.turns_indexed, "
            "items_indexed = excluded.items_indexed, "
            "unchanged_skipped = excluded.unchanged_skipped, "
            "scanned_bytes = excluded.scanned_bytes, duration_ms = excluded.duration_ms, "
            "truncated = excluded.truncated",
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
                sync.next_offset,
                last_synced_at,
                sync.error,
                sync.history_source,
                sync.history_cursor,
                sync.history_backwards_cursor,
                sync.history_recent_cursor,
                sync.history_pending_backwards_cursor,
                sync.history_item_turn_id,
                sync.history_item_cursor,
                sync.history_item_turn_cursor,
                sync.history_item_turn_direction,
                sync.history_cursor_guard,
                int(sync.history_complete),
                sync.history_capability,
                int(sync.observation_enabled),
                sync.pages_scanned,
                sync.turns_indexed,
                sync.items_indexed,
                int(sync.unchanged_skipped),
                sync.scanned_bytes,
                sync.duration_ms,
                int(sync.truncated),
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

    @_serialized_access
    async def get_lane_sync(self, lane_id: str) -> LaneSync | None:
        async with self._conn.execute(_LANE_SYNC_SELECT + " WHERE src.lane = ?", (lane_id,)) as cur:
            row = await cur.fetchone()
        return _row_to_lane_sync(row) if row is not None else None

    @_serialized_access
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

    @_serialized_access
    async def upsert_model_catalog(self, models: list[ModelCatalogEntry]) -> None:
        async with self._transaction():
            for model in models:
                existing = await self.get_model_catalog_entry(model.id, provider=model.provider)
                first_seen_at = (
                    existing.first_seen_at if existing is not None else model.first_seen_at
                )
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
                        json.dumps(
                            [tier.model_dump(mode="python") for tier in model.service_tiers]
                        ),
                        json.dumps(model.additional_speed_tiers),
                        first_seen_at,
                        json.dumps(model.input_modalities),
                        _bool_or_none(model.supports_personality),
                        model.upgrade,
                        model.last_seen_at,
                        model.source,
                    ),
                )

    @_serialized_access
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

    @_serialized_access
    async def get_model_catalog_entry(
        self, model_id: str, *, provider: str = "openai"
    ) -> ModelCatalogEntry | None:
        async with self._conn.execute(
            "SELECT * FROM model_catalog WHERE provider = ? AND id = ?",
            (provider, model_id),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_model_catalog_entry(row) if row is not None else None

    @_serialized_access
    async def replace_permission_profiles(
        self, cwd: str, profiles: list[PermissionProfileEntry]
    ) -> None:
        async with self._transaction():
            existing = {entry.id: entry for entry in await self.list_permission_profiles(cwd=cwd)}
            await self._conn.execute("DELETE FROM permission_profiles WHERE cwd = ?", (cwd,))
            for profile in profiles:
                previous = existing.get(profile.id)
                await self._conn.execute(
                    "INSERT INTO permission_profiles (id, cwd, description, allowed, "
                    "first_seen_at, last_seen_at, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        profile.id,
                        cwd,
                        profile.description,
                        int(profile.allowed),
                        previous.first_seen_at if previous is not None else profile.first_seen_at,
                        profile.last_seen_at,
                        profile.source,
                    ),
                )

    @_serialized_access
    async def list_permission_profiles(self, *, cwd: str) -> list[PermissionProfileEntry]:
        async with self._conn.execute(
            "SELECT * FROM permission_profiles WHERE cwd = ? ORDER BY id", (cwd,)
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_permission_profile_entry(row) for row in rows]

    @_serialized_access
    async def get_permission_profile(
        self, profile_id: str, *, cwd: str
    ) -> PermissionProfileEntry | None:
        async with self._conn.execute(
            "SELECT * FROM permission_profiles WHERE cwd = ? AND id = ?",
            (cwd, profile_id),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_permission_profile_entry(row) if row is not None else None

    @_serialized_access
    async def upsert_lane_model_settings(self, settings: LaneModelSettings) -> None:
        async with self._transaction():
            await self._conn.execute(
                "INSERT INTO lane_model_settings (lane, model_provider, model, reasoning_effort, "
                "requested_service_tier, resolved_service_tier, service_tier_name, "
                "service_tier_source, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(lane) DO UPDATE SET model_provider = excluded.model_provider, "
                "model = excluded.model, reasoning_effort = excluded.reasoning_effort, "
                "requested_service_tier = excluded.requested_service_tier, "
                "resolved_service_tier = excluded.resolved_service_tier, "
                "service_tier_name = excluded.service_tier_name, "
                "service_tier_source = excluded.service_tier_source, "
                "updated_at = excluded.updated_at",
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

    @_serialized_access
    async def get_lane_model_settings(self, lane_id: str) -> LaneModelSettings | None:
        async with self._conn.execute(
            "SELECT * FROM lane_model_settings WHERE lane = ?", (lane_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_lane_model_settings(row) if row is not None else None

    @_serialized_access
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

    @_serialized_access
    async def upsert_lane_runtime_settings(self, settings: LaneRuntimeSettings) -> None:
        async with self._transaction():
            await self._conn.execute(
                "INSERT INTO lane_runtime_settings "
                "(lane, permission_profile, sandbox, approval_policy, "
                "approvals_reviewer, effort, summary, model, service_tier, output_schema, "
                "personality, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(lane) DO UPDATE SET permission_profile = excluded.permission_profile, "
                "sandbox = excluded.sandbox, "
                "approval_policy = excluded.approval_policy, "
                "approvals_reviewer = excluded.approvals_reviewer, effort = excluded.effort, "
                "summary = excluded.summary, model = excluded.model, "
                "service_tier = excluded.service_tier, output_schema = excluded.output_schema, "
                "personality = excluded.personality, updated_at = excluded.updated_at",
                (
                    settings.lane,
                    settings.permission_profile,
                    settings.sandbox,
                    settings.approval_policy,
                    settings.approvals_reviewer,
                    settings.effort,
                    settings.summary,
                    settings.model,
                    settings.service_tier,
                    json.dumps(settings.output_schema)
                    if settings.output_schema is not None
                    else None,
                    settings.personality,
                    settings.updated_at,
                ),
            )

    @_serialized_access
    async def get_lane_runtime_settings(self, lane_id: str) -> LaneRuntimeSettings | None:
        async with self._conn.execute(
            "SELECT * FROM lane_runtime_settings WHERE lane = ?", (lane_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_lane_runtime_settings(row) if row is not None else None

    # --- provider threads / topology -----------------------------------------

    @_serialized_access
    async def upsert_provider_thread(
        self, observation: ProviderThreadObservation
    ) -> ProviderThread:
        """Persist non-null metadata without implicitly changing lifecycle state."""

        return (await self.upsert_provider_threads([observation]))[0]

    @_serialized_access
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
                observation.provider,
                observation.provider_thread_id,
                binding_id=observation.binding_id,
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
            "INSERT INTO provider_threads (provider, binding_id, provider_thread_id, session_id, "
            "parent_thread_id, forked_from_id, source_kind, thread_source, agent_nickname, "
            "agent_role, agent_depth, lifecycle_state, relationship_source, confidence, "
            "first_seen_at, last_seen_at, archived_at, deleted_at) VALUES ("
            "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider, binding_id, provider_thread_id) DO UPDATE SET "
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
                observation.binding_id,
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

    @_serialized_access
    async def get_provider_thread(
        self,
        provider: str,
        provider_thread_id: str,
        *,
        binding_id: str = DEFAULT_CODEX_BINDING_ID,
    ) -> ProviderThread | None:
        async with self._conn.execute(
            "SELECT * FROM provider_threads WHERE provider = ? AND binding_id = ? "
            "AND provider_thread_id = ?",
            (provider, binding_id, provider_thread_id),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_provider_thread(row) if row is not None else None

    @_serialized_access
    async def list_provider_threads(
        self,
        *,
        provider: str | None = None,
        binding_id: str | None = None,
        lifecycle_state: ProviderThreadLifecycleState | None = None,
    ) -> list[ProviderThread]:
        clauses: list[str] = []
        params: list[str] = []
        if provider is not None:
            clauses.append("provider = ?")
            params.append(provider)
        if binding_id is not None:
            clauses.append("binding_id = ?")
            params.append(binding_id)
        if lifecycle_state is not None:
            clauses.append("lifecycle_state = ?")
            params.append(lifecycle_state)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        async with self._conn.execute(
            "SELECT * FROM provider_threads"
            f"{where} ORDER BY provider, binding_id, first_seen_at, provider_thread_id",
            tuple(params),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_provider_thread(row) for row in rows]

    @_serialized_access
    async def mark_provider_thread_state(
        self,
        provider: str,
        provider_thread_id: str,
        lifecycle_state: ProviderThreadLifecycleState,
        *,
        binding_id: str = DEFAULT_CODEX_BINDING_ID,
        observed_at: str | None = None,
    ) -> ProviderThread:
        return await self.upsert_provider_thread(
            ProviderThreadObservation(
                provider=provider,
                binding_id=binding_id,
                provider_thread_id=provider_thread_id,
                lifecycle_state=lifecycle_state,
                observed_at=observed_at,
            )
        )

    @_serialized_access
    async def get_provider_thread_topology(
        self,
        provider: str,
        provider_thread_ids: str | list[str],
        *,
        binding_id: str = DEFAULT_CODEX_BINDING_ID,
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
            fetched = await self._get_provider_thread_nodes(provider, binding_id, candidates)
            nodes.update(fetched)
            missing.update(set(candidates) - set(fetched))

        async def related_ids(column: str, thread_id: str) -> list[str]:
            if column not in {"parent_thread_id", "forked_from_id"}:
                raise ValueError(f"unsupported provider thread relation {column!r}")
            async with self._conn.execute(
                "SELECT provider_thread_id FROM provider_threads "
                f"WHERE provider = ? AND binding_id = ? AND {column} = ? "
                "ORDER BY provider_thread_id",
                (provider, binding_id, thread_id),
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
            binding_id=binding_id,
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
        self, provider: str, binding_id: str, provider_thread_ids: list[str]
    ) -> dict[str, ProviderThreadNode]:
        if not provider_thread_ids:
            return {}
        placeholders = ", ".join("?" for _ in provider_thread_ids)
        async with self._conn.execute(
            "SELECT provider_threads.*, lanes.id AS lane_id, lanes.ref, lanes.handle, "
            "lanes.status AS lane_status FROM provider_threads "
            "LEFT JOIN lanes ON lanes.provider = provider_threads.provider "
            "AND lanes.binding_id = provider_threads.binding_id "
            "AND lanes.provider_session_id = provider_threads.provider_thread_id "
            "WHERE provider_threads.provider = ? AND provider_threads.binding_id = ? "
            f"AND provider_threads.provider_thread_id IN ({placeholders}) "
            "ORDER BY provider_threads.provider_thread_id",
            (provider, binding_id, *provider_thread_ids),
        ) as cur:
            rows = await cur.fetchall()
        result: dict[str, ProviderThreadNode] = {}
        for row in rows:
            node = _row_to_provider_thread_node(row)
            result[node.thread.provider_thread_id] = node
        return result

    # --- provider account / capacity observations ----------------------------

    @_serialized_access
    async def upsert_provider_capacity_observation(
        self, observation: ProviderCapacityObservation
    ) -> ProviderCapacityObservation:
        observation = ProviderCapacityObservation.model_validate(
            observation.model_dump(mode="python")
        )
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
        return observation

    @_serialized_access
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
        return _provider_capacity_observation_from_payload(row["payload"])

    @_serialized_access
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
        return [_provider_capacity_observation_from_payload(row["payload"]) for row in rows]

    # --- provider events / normalized history ---------------------------------

    @_serialized_access
    async def record_provider_event(self, event: ProviderEvent) -> ProviderEvent:
        payload = _json_dump_compact(event.payload) if event.payload is not None else None
        async with self._write_lock:
            await self._conn.execute(
                "INSERT INTO provider_events (provider, binding_id, provider_thread_id, lane, "
                "event_type, "
                "provider_event_id, provider_turn_id, provider_item_id, correlation_id, "
                "provider_ts, received_at, summary, payload, raw_retained) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT DO NOTHING",
                (
                    event.provider,
                    event.binding_id,
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
                event.provider,
                binding_id=event.binding_id,
                provider_event_id=event.provider_event_id,
            )
            if existing is None:
                raise RuntimeError("provider event insert did not return a row")
            return existing
        raise RuntimeError("provider event insert did not return a row")

    @_serialized_access
    async def find_provider_event(
        self,
        provider: str,
        *,
        binding_id: str = DEFAULT_CODEX_BINDING_ID,
        provider_event_id: str,
    ) -> ProviderEvent | None:
        async with self._conn.execute(
            "SELECT * FROM provider_events WHERE provider = ? AND binding_id = ? "
            "AND provider_event_id = ?",
            (provider, binding_id, provider_event_id),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_provider_event(row) if row is not None else None

    @_serialized_access
    async def list_provider_events(
        self,
        *,
        lane: str | None = None,
        provider: str | None = None,
        binding_id: str | None = None,
        provider_thread_id: str | None = None,
        limit: int = 50,
    ) -> list[ProviderEvent]:
        clauses: list[str] = []
        params: list[object] = []
        if lane is not None:
            clauses.append("lane = ?")
            params.append(lane)
        if provider is not None:
            clauses.append("provider = ?")
            params.append(provider)
        if binding_id is not None:
            clauses.append("binding_id = ?")
            params.append(binding_id)
        if provider_thread_id is not None:
            if lane is None and (provider is None or binding_id is None):
                raise ValueError(
                    "provider_thread_id requires provider and binding_id when lane is omitted"
                )
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

    @_serialized_access
    async def observe_server_request(self, request: ServerRequest) -> ServerRequest:
        """Persist a pending Codex request without reopening a terminal outcome."""

        return (await self.observe_server_request_once(request)).request

    @_serialized_access
    async def observe_server_request_once(self, request: ServerRequest) -> ServerRequestObservation:
        """Persist a request and report whether this call won the insert."""

        if request.state != "pending":
            raise ValueError("only pending server requests may be observed")
        thread_key = _server_request_thread_key(request.provider_thread_id)
        request_id_json = _json_dump_compact(request.request_id)
        async with self._write_lock:
            cur = await self._conn.execute(
                "INSERT INTO server_requests (provider, binding_id, provider_session_id, "
                "provider_thread_id, "
                "provider_thread_key, request_id_json, lane, method, category, state, "
                "received_at, deadline_at, resolved_at, response_summary, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(provider, binding_id, provider_session_id, provider_thread_key, "
                "request_id_json) "
                "DO NOTHING",
                (
                    request.provider,
                    request.binding_id,
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
            binding_id=request.binding_id,
            provider_session_id=request.provider_session_id,
            provider_thread_id=request.provider_thread_id,
            request_id=request.request_id,
        )
        if saved is None:
            raise RuntimeError("server request upsert did not return a row")
        return ServerRequestObservation(request=saved, inserted=cur.rowcount == 1)

    @_serialized_access
    async def get_server_request(
        self,
        *,
        provider: str,
        binding_id: str = DEFAULT_CODEX_BINDING_ID,
        provider_session_id: str,
        provider_thread_id: str | None,
        request_id: int | str,
    ) -> ServerRequest | None:
        async with self._conn.execute(
            "SELECT * FROM server_requests WHERE provider = ? AND binding_id = ? "
            "AND provider_session_id = ? "
            "AND provider_thread_key = ? AND request_id_json = ?",
            (
                provider,
                binding_id,
                provider_session_id,
                _server_request_thread_key(provider_thread_id),
                _json_dump_compact(request_id),
            ),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_server_request(row) if row is not None else None

    @_serialized_access
    async def get_server_request_by_id(self, request_id: int) -> ServerRequest | None:
        """Return a request by its dispatch-local operator selector."""

        async with self._conn.execute(
            "SELECT * FROM server_requests WHERE id = ?", (request_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_server_request(row) if row is not None else None

    @_serialized_access
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

    @_serialized_access
    async def list_pending_server_requests(
        self,
        *,
        lane: str | None = None,
        provider_session_id: str | None = None,
        binding_id: str = DEFAULT_CODEX_BINDING_ID,
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
            sql += " AND binding_id = ? AND provider_session_id = ?"
            params.extend((binding_id, provider_session_id))
        sql += " ORDER BY deadline_at, received_at, request_id_json LIMIT ?"
        params.append(limit)
        async with self._conn.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
        return [_row_to_server_request(row) for row in rows]

    @_serialized_access
    async def claim_server_request(
        self,
        *,
        provider: str,
        binding_id: str = DEFAULT_CODEX_BINDING_ID,
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
                "AND binding_id = ? "
                "AND provider_session_id = ? AND provider_thread_key = ? "
                "AND request_id_json = ? AND state = 'pending'",
                (provider, binding_id, provider_session_id, thread_key, request_id_json),
            )
            await self._conn.commit()
        if cur.rowcount != 1:
            return None
        return await self.get_server_request(
            provider=provider,
            binding_id=binding_id,
            provider_session_id=provider_session_id,
            provider_thread_id=provider_thread_id,
            request_id=request_id,
        )

    @_serialized_access
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

    @_serialized_access
    async def finalize_server_request(
        self,
        *,
        provider: str,
        binding_id: str = DEFAULT_CODEX_BINDING_ID,
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
                "error = ? WHERE provider = ? AND binding_id = ? AND provider_session_id = ? "
                "AND provider_thread_key = ? AND request_id_json = ? AND state = 'responding'",
                (
                    state,
                    resolved_at or self.now_iso(),
                    _bound_server_request_text(response_summary),
                    _bound_server_request_text(error),
                    provider,
                    binding_id,
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
            binding_id=binding_id,
            provider_session_id=provider_session_id,
            provider_thread_id=provider_thread_id,
            request_id=request_id,
        )

    @_serialized_access
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

    @_serialized_access
    async def fail_open_server_requests_except_session(
        self,
        current_session_id: str,
        *,
        binding_id: str = DEFAULT_CODEX_BINDING_ID,
        error: str = "app-server connection replaced before a response was sent",
    ) -> int:
        """Terminalize rows that cannot be answered after an App Server reconnect."""

        async with self._write_lock:
            cur = await self._conn.execute(
                "UPDATE server_requests SET state = 'failed', resolved_at = ?, "
                "response_summary = NULL, error = ? WHERE binding_id = ? "
                "AND provider_session_id != ? "
                "AND state IN ('pending', 'responding')",
                (
                    self.now_iso(),
                    _bound_server_request_text(error),
                    binding_id,
                    current_session_id,
                ),
            )
            await self._conn.commit()
        return cur.rowcount

    @_serialized_access
    async def list_open_server_requests_except_session(
        self,
        current_session_id: str,
        *,
        binding_id: str = DEFAULT_CODEX_BINDING_ID,
    ) -> list[ServerRequest]:
        """Return pending/responding rows that a replacement connection cannot answer."""

        async with self._conn.execute(
            "SELECT * FROM server_requests WHERE binding_id = ? AND provider_session_id != ? "
            "AND state IN ('pending', 'responding') ORDER BY id",
            (binding_id, current_session_id),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_server_request(row) for row in rows]

    @_serialized_access
    async def upsert_thread_turn(self, turn: ThreadTurn) -> ThreadTurn:
        async with self._transaction():
            await self._upsert_thread_turn_row(turn)
        return await self.get_thread_turn(
            turn.provider,
            turn.provider_thread_id,
            turn.turn_id,
            binding_id=turn.binding_id,
        )

    async def _upsert_thread_turn_row(self, turn: ThreadTurn) -> None:
        await self._conn.execute(
            "INSERT INTO thread_turns (provider, binding_id, provider_thread_id, turn_id, lane, "
            "status, "
            "started_at, completed_at, failed_at, error, completion_source, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider, binding_id, provider_thread_id, turn_id) DO UPDATE SET "
            "lane = COALESCE(excluded.lane, thread_turns.lane), "
            "status = CASE WHEN thread_turns.status IN ('completed', 'failed', 'interrupted') "
            "THEN thread_turns.status WHEN excluded.status = 'unknown' "
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
                turn.binding_id,
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

    @_serialized_access
    async def get_thread_turn(
        self,
        provider: str,
        provider_thread_id: str,
        turn_id: str,
        *,
        binding_id: str = DEFAULT_CODEX_BINDING_ID,
    ) -> ThreadTurn:
        async with self._conn.execute(
            "SELECT * FROM thread_turns WHERE provider = ? AND binding_id = ? "
            "AND provider_thread_id = ? "
            "AND turn_id = ?",
            (provider, binding_id, provider_thread_id, turn_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise NotFoundError(f"no thread turn {provider}:{provider_thread_id}:{turn_id}")
        return ThreadTurn.model_validate(_row_dict(row))

    @_serialized_access
    async def list_thread_turns(self, *, lane: str, limit: int = 50) -> list[ThreadTurn]:
        async with self._conn.execute(
            "SELECT * FROM thread_turns WHERE lane = ? ORDER BY updated_at DESC LIMIT ?",
            (lane, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [ThreadTurn.model_validate(_row_dict(row)) for row in rows]

    @_serialized_access
    async def upsert_thread_item(
        self, item: ThreadItem, *, refs: list[ThreadItemRef] | None = None
    ) -> ThreadItem:
        async with self._transaction():
            await self._upsert_thread_item_row(item)
            if refs is not None:
                await self._replace_thread_item_refs(item, refs)
        return await self.get_thread_item(
            item.provider,
            item.provider_thread_id,
            item.item_id,
            binding_id=item.binding_id,
        )

    @_serialized_access
    async def upsert_thread_history_snapshot(
        self,
        *,
        turns: list[ThreadTurn],
        items: list[tuple[ThreadItem, list[ThreadItemRef]]],
        provider: str,
        binding_id: str = DEFAULT_CODEX_BINDING_ID,
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
                    binding_id=binding_id,
                    provider_thread_id=provider_thread_id,
                    turn_ids=turn_ids,
                    item_ids=item_ids,
                )

    async def _upsert_thread_item_row(self, item: ThreadItem) -> None:
        await self._conn.execute(
            "INSERT INTO thread_items (provider, binding_id, provider_thread_id, item_id, lane, "
            "turn_id, item_type, role, phase, status, text, tool, server, command, cwd, "
            "error, duration_ms, arguments, success, agent_nickname, agent_role, created_at, "
            "position, inserted_at, payload, raw_retained) VALUES ("
            "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider, binding_id, provider_thread_id, item_id) DO UPDATE SET "
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
                item.binding_id,
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
            "DELETE FROM thread_item_refs WHERE provider = ? AND binding_id = ? "
            "AND provider_thread_id = ? "
            "AND item_id = ?",
            (item.provider, item.binding_id, item.provider_thread_id, item.item_id),
        )
        for ref in refs:
            await self._conn.execute(
                "INSERT OR IGNORE INTO thread_item_refs (provider, binding_id, provider_thread_id, "
                "item_id, ref_type, ref_value) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    ref.provider,
                    ref.binding_id,
                    ref.provider_thread_id,
                    ref.item_id,
                    ref.ref_type,
                    ref.ref_value,
                ),
            )

    @_serialized_access
    async def find_thread_item(
        self,
        provider: str,
        provider_thread_id: str,
        item_id: str,
        *,
        binding_id: str = DEFAULT_CODEX_BINDING_ID,
    ) -> ThreadItem | None:
        async with self._conn.execute(
            "SELECT * FROM thread_items WHERE provider = ? AND binding_id = ? "
            "AND provider_thread_id = ? "
            "AND item_id = ?",
            (provider, binding_id, provider_thread_id, item_id),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_thread_item(row) if row is not None else None

    @_serialized_access
    async def get_thread_item(
        self,
        provider: str,
        provider_thread_id: str,
        item_id: str,
        *,
        binding_id: str = DEFAULT_CODEX_BINDING_ID,
    ) -> ThreadItem:
        item = await self.find_thread_item(
            provider, provider_thread_id, item_id, binding_id=binding_id
        )
        if item is None:
            raise NotFoundError(f"no thread item {provider}:{provider_thread_id}:{item_id}")
        return item

    @_serialized_access
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

    @_serialized_access
    async def list_recent_thread_items(self, *, lane: str, limit: int = 50) -> list[ThreadItem]:
        """Return the newest indexed items by observation time for a compact transcript."""

        async with self._conn.execute(
            "SELECT * FROM thread_items WHERE lane = ? "
            "ORDER BY inserted_at DESC, COALESCE(position, -1) DESC, item_id DESC LIMIT ?",
            (lane, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_thread_item(row) for row in rows]

    @_serialized_access
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

    @_serialized_access
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

    @_serialized_access
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
                AND items.binding_id = refs.binding_id
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
                AND items.binding_id = refs.binding_id
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
                AND items.binding_id = refs.binding_id
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

    @_serialized_access
    async def list_thread_item_refs(self, item: ThreadItem) -> list[ThreadItemRef]:
        async with self._conn.execute(
            "SELECT * FROM thread_item_refs WHERE provider = ? AND binding_id = ? "
            "AND provider_thread_id = ? "
            "AND item_id = ? ORDER BY ref_type, ref_value",
            (item.provider, item.binding_id, item.provider_thread_id, item.item_id),
        ) as cur:
            rows = await cur.fetchall()
        return [ThreadItemRef.model_validate(_row_dict(row)) for row in rows]

    @_serialized_access
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
                clauses.append(
                    "(provider = ? AND binding_id = ? AND provider_thread_id = ? AND item_id = ?)"
                )
                params.extend(
                    (item.provider, item.binding_id, item.provider_thread_id, item.item_id)
                )
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

    @_serialized_access
    async def prune_thread_history_snapshot(
        self,
        *,
        provider: str,
        binding_id: str = DEFAULT_CODEX_BINDING_ID,
        provider_thread_id: str,
        turn_ids: set[str],
        item_ids: set[str],
    ) -> None:
        async with self._transaction():
            await self._prune_thread_history_snapshot_rows(
                provider=provider,
                binding_id=binding_id,
                provider_thread_id=provider_thread_id,
                turn_ids=turn_ids,
                item_ids=item_ids,
            )

    async def _prune_thread_history_snapshot_rows(
        self,
        *,
        provider: str,
        binding_id: str,
        provider_thread_id: str,
        turn_ids: set[str],
        item_ids: set[str],
    ) -> None:
        await self._delete_missing_values(
            "thread_items",
            provider=provider,
            binding_id=binding_id,
            provider_thread_id=provider_thread_id,
            id_column="item_id",
            keep_ids=item_ids,
        )
        await self._delete_missing_values(
            "thread_turns",
            provider=provider,
            binding_id=binding_id,
            provider_thread_id=provider_thread_id,
            id_column="turn_id",
            keep_ids=turn_ids,
        )

    async def _delete_missing_values(
        self,
        table: str,
        *,
        provider: str,
        binding_id: str,
        provider_thread_id: str,
        id_column: str,
        keep_ids: set[str],
    ) -> None:
        params: list[object] = [provider, binding_id, provider_thread_id]
        sql = (
            f"DELETE FROM {table} WHERE provider = ? AND binding_id = ? AND provider_thread_id = ?"
        )
        if keep_ids:
            placeholders = ", ".join("?" for _ in keep_ids)
            sql += f" AND {id_column} NOT IN ({placeholders})"
            params.extend(sorted(keep_ids))
        await self._conn.execute(sql, tuple(params))

    @_serialized_access
    async def upsert_message_receipt(self, receipt: MessageReceipt) -> MessageReceipt:
        async with self._write_lock:
            await self._conn.execute(
                "INSERT INTO message_receipts (id, lane, queued_message_id, provider, binding_id, "
                "provider_thread_id, dispatch_message_id, status, turn_id, error, created_at, "
                "sent_at, accepted_at, completed_at, failed_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(dispatch_message_id) WHERE dispatch_message_id IS NOT NULL "
                "DO UPDATE SET lane = excluded.lane, "
                "queued_message_id = excluded.queued_message_id, "
                "provider = excluded.provider, provider_thread_id = excluded.provider_thread_id, "
                "status = excluded.status, turn_id = excluded.turn_id, error = excluded.error, "
                "sent_at = excluded.sent_at, accepted_at = excluded.accepted_at, "
                "completed_at = excluded.completed_at, failed_at = excluded.failed_at, "
                "updated_at = excluded.updated_at "
                "WHERE message_receipts.provider = excluded.provider "
                "AND message_receipts.binding_id = excluded.binding_id "
                "AND message_receipts.provider_thread_id = excluded.provider_thread_id",
                (
                    receipt.id,
                    receipt.lane,
                    receipt.queued_message_id,
                    receipt.provider,
                    receipt.binding_id,
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
                provider=receipt.provider,
                dispatch_message_id=receipt.dispatch_message_id,
                binding_id=receipt.binding_id,
            )
            if got is None:
                raise RuntimeError("message receipt upsert did not return a row")
            return got
        raise RuntimeError("message receipt upsert did not return a row")

    @_serialized_access
    async def find_message_receipt(
        self,
        *,
        provider: str,
        binding_id: str = DEFAULT_CODEX_BINDING_ID,
        dispatch_message_id: str,
    ) -> MessageReceipt | None:
        async with self._conn.execute(
            "SELECT * FROM message_receipts WHERE provider = ? AND binding_id = ? "
            "AND dispatch_message_id = ?",
            (provider, binding_id, dispatch_message_id),
        ) as cur:
            row = await cur.fetchone()
        return MessageReceipt.model_validate(_row_dict(row)) if row is not None else None

    @_serialized_access
    async def list_message_receipts(self, *, lane: str, limit: int = 50) -> list[MessageReceipt]:
        async with self._conn.execute(
            "SELECT * FROM message_receipts WHERE lane = ? ORDER BY updated_at DESC LIMIT ?",
            (lane, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [MessageReceipt.model_validate(_row_dict(row)) for row in rows]

    @_serialized_access
    async def upsert_lane_runtime_state(self, state: LaneRuntimeState) -> LaneRuntimeState:
        async with self._write_lock:
            await self._conn.execute(
                "INSERT INTO lane_runtime_state (lane, provider, binding_id, provider_thread_id, "
                "status, "
                "active_turn_id, latest_turn_id, latest_turn_status, needs_attention, "
                "attention_kind, attention_detail, updated_at, last_event_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(lane) DO UPDATE SET provider = excluded.provider, "
                "binding_id = excluded.binding_id, "
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
                    state.binding_id,
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

    @_serialized_access
    async def get_lane_runtime_state(self, lane_id: str) -> LaneRuntimeState | None:
        async with self._conn.execute(
            "SELECT * FROM lane_runtime_state WHERE lane = ?", (lane_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_lane_runtime_state(row) if row is not None else None

    # --- triggers -------------------------------------------------------------

    @_serialized_access
    async def add_trigger(self, trigger: Trigger) -> Trigger:
        created = trigger.created_at or self._now()  # the scheduling baseline
        async with self._transaction():
            await self._conn.execute(
                "INSERT INTO triggers (id, name, lane_selector, when_spec, action_spec, "
                "guard_spec, enabled, created_at, last_fired_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        return trigger.model_copy(update={"created_at": created})

    @_serialized_access
    async def find_trigger(self, trigger_id: str) -> Trigger | None:
        async with self._conn.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)) as cur:
            row = await cur.fetchone()
        return _row_to_trigger(row) if row is not None else None

    @_serialized_access
    async def get_trigger(self, trigger_id: str) -> Trigger:
        trigger = await self.find_trigger(trigger_id)
        if trigger is None:
            raise NotFoundError(f"no trigger {trigger_id!r}")
        return trigger

    @_serialized_access
    async def list_triggers(self) -> list[Trigger]:
        async with self._conn.execute("SELECT * FROM triggers ORDER BY id") as cur:
            rows = await cur.fetchall()
        return [_row_to_trigger(row) for row in rows]

    @_serialized_access
    async def set_trigger_enabled(self, trigger_id: str, enabled: bool) -> None:
        async with self._transaction():
            await self._conn.execute(
                "UPDATE triggers SET enabled = ? WHERE id = ?", (int(enabled), trigger_id)
            )

    @_serialized_access
    async def set_trigger_fired(self, trigger_id: str, when: datetime) -> None:
        async with self._transaction():
            await self._conn.execute(
                "UPDATE triggers SET last_fired_at = ? WHERE id = ?",
                (when.isoformat(), trigger_id),
            )

    @_serialized_access
    async def remove_trigger(self, trigger_id: str) -> bool:
        async with self._transaction():
            cur = await self._conn.execute("DELETE FROM triggers WHERE id = ?", (trigger_id,))
        return cur.rowcount > 0

    # --- audit log ------------------------------------------------------------

    @_serialized_access
    async def log_action(
        self,
        op: str,
        *,
        lane: str | None = None,
        trigger_id: str | None = None,
        detail: str | None = None,
        outcome: str = "ok",
    ) -> None:
        async with self._write_lock:
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

    @_serialized_access
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


def _provider_capacity_observation_from_payload(value: object) -> ProviderCapacityObservation:
    data = json.loads(str(value))
    if not isinstance(data, dict):
        raise ValueError("provider capacity payload must be an object")
    for field, limit in (
        ("windows", 64),
        ("reset_credits", 100),
        ("daily_usage", 90),
        ("source", 16),
    ):
        rows = data.get(field)
        if isinstance(rows, list) and len(rows) > limit:
            data[field] = rows[-limit:]
    return ProviderCapacityObservation.model_validate(data)


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
    src.next_offset AS next_offset,
    src.last_synced_at AS last_synced_at,
    src.error AS error,
    src.history_source AS history_source,
    src.history_cursor AS history_cursor,
    src.history_backwards_cursor AS history_backwards_cursor,
    src.history_recent_cursor AS history_recent_cursor,
    src.history_pending_backwards_cursor AS history_pending_backwards_cursor,
    src.history_item_turn_id AS history_item_turn_id,
    src.history_item_turn_cursor AS history_item_turn_cursor,
    src.history_item_turn_direction AS history_item_turn_direction,
    src.history_item_cursor AS history_item_cursor,
    src.history_cursor_guard AS history_cursor_guard,
    src.history_complete AS history_complete,
    src.history_capability AS history_capability,
    src.observation_enabled AS observation_enabled,
    src.pages_scanned AS pages_scanned,
    src.turns_indexed AS turns_indexed,
    src.items_indexed AS items_indexed,
    src.unchanged_skipped AS unchanged_skipped,
    src.scanned_bytes AS scanned_bytes,
    src.duration_ms AS duration_ms,
    src.truncated AS truncated,
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
    data["history_complete"] = bool(data["history_complete"])
    data["observation_enabled"] = bool(data["observation_enabled"])
    data["unchanged_skipped"] = bool(data["unchanged_skipped"])
    data["truncated"] = bool(data["truncated"])
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


def _row_to_permission_profile_entry(row: aiosqlite.Row) -> PermissionProfileEntry:
    data = _row_dict(row)
    data["allowed"] = bool(data["allowed"])
    return PermissionProfileEntry.model_validate(data)


def _row_to_lane_model_settings(row: aiosqlite.Row) -> LaneModelSettings:
    return LaneModelSettings.model_validate(_row_dict(row))


def _row_to_lane_runtime_settings(row: aiosqlite.Row) -> LaneRuntimeSettings:
    data = _row_dict(row)
    raw_schema = data["output_schema"]
    data["output_schema"] = json.loads(str(raw_schema)) if raw_schema else None
    return LaneRuntimeSettings.model_validate(data)


def _row_to_queued_message(row: aiosqlite.Row) -> QueuedMessage:
    data = _row_dict(row)
    raw_content = data["content"]
    data["content"] = json.loads(str(raw_content)) if raw_content else []
    return QueuedMessage.model_validate(data)


def _row_to_delivery(row: aiosqlite.Row) -> DeliveryReceipt:
    data = _row_dict(row)
    data["evidence_partial"] = bool(data["evidence_partial"])
    return DeliveryReceipt.model_validate(data)


def _row_to_lane_launch(row: aiosqlite.Row) -> LaneLaunch:
    return LaneLaunch.model_validate(_row_dict(row))


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
