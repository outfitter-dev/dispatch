# Dispatch Test Fixture Corpus

This directory holds small, named fixtures for App Server protocol payloads,
Codex JSONL sources, CLI smoke recipes, and registry builders. Fixtures here are
for tests, not product docs or generated outputs.

Rules:

- Keep checked-in fixtures small and readable.
- Prefer JSON/JSONL text over binary artifacts.
- Prefer Python builders over committed SQLite database files.
- Add a test that loads every new fixture; unexercised fixtures rot.
- Use synthetic ids, paths, and prompts. Do not copy private thread content.

Layout:

- `app_server/` — raw App Server result payloads and notifications, plus the
  compact generated protocol manifest refreshed by `just app-server-manifest`.
  The manifest records canonical `ThreadItem` discriminants, and
  `thread_read/canonical_items_v0144.json` exercises every known 0.144 variant.
  `thread_list/descendants_v0144.json` exercises tagged subagent sources and
  parent, ancestor, and fork relationships.
- `transcripts/` — Codex persisted JSONL source files for sync parsing.
- `registry/` — builders for registry rows and migration test setup.
- `cli_smoke/` — notes and recipes for install/first-run smoke checks.
- `mcp/` — tiny synthetic stdio servers exercised by live App Server integration tests.
