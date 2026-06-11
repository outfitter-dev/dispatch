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

- `app_server/` — raw App Server result payloads and notifications.
- `transcripts/` — Codex persisted JSONL source files for sync parsing.
- `registry/` — builders for registry rows and migration test setup.
- `cli_smoke/` — notes and recipes for install/first-run smoke checks.

