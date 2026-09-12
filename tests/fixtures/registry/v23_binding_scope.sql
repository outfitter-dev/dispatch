-- Exact binding-related schema from dispatch v0.11.0 / schema v23.

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
