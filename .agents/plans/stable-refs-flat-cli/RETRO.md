# Stable refs and flat CLI - execution ledger

Status: implementation in progress on `feat/stable-refs-flat-cli`, stacked on PR #32
(`feat/top-level-thread-actions`).

## Handoff

- Plan written from the 2026-06-05 dispatch identity/CLI/MCP design discussion.
- No implementation has been performed in this packet yet.
- Before starting, verify current branch/PR state, especially whether PR #32 has merged or whether this work should stack on it.

## Decisions to preserve

- Full Codex UUID is canonical and always accepted.
- Dispatch ref is short, local, assigned, and stored.
- Titles and `@handles` are mutable convenience labels.
- Flat CLI is canonical for thread operations.
- `dispatch mcp` remains the MCP server entrypoint.
- MCP stays grouped by workflow/safety, with exact annotations.
- `tail` and `watch` are separate commands with different semantics.

## Execution log

- 2026-06-05: Verified live repo state before editing:
  - Current branch was `feat/top-level-thread-actions`, matching PR #32 head.
  - PR #32 is open/draft, merge state clean, and CI checks green.
  - Graphite visible stack was `main` -> `feat/top-level-thread-actions`.
  - Created stacked branch `feat/stable-refs-flat-cli`.
- Added dispatch-local Codex ref allocator:
  - Format `<source><payload4><mixer>`.
  - Source `0` for Codex.
  - Payload is base58btc from `sha256("codex:" + thread_id)`.
  - Mixer allocated from the base58btc alphabet on collision.
- Bumped registry schema to v3 and backfilled refs for existing lanes in
  `created_at, id` order.
- Added shared selector resolver:
  - Exact ref, full thread id, lane id, handle, title.
  - Fuzzy title matching only when read flows opt in.
  - Mutating/destructive flows use exact resolution and return ambiguity
    candidates instead of guessing.
- Added refs to managed-thread output models and examples.
- Flattened canonical CLI routes:
  - `attach`, `list`, `list --unmanaged`, `get`, `tail`, `watch`, `sync`.
  - `search --thread` is canonical; `--lane` remains accepted as a temporary
    compatibility alias.
  - `tail --follow` no longer maps to `watch`.
- Renamed grouped MCP tools from lane language to thread language:
  - `dispatch_thread_read`
  - `dispatch_thread_write`
  - `dispatch_thread_destroy`
- Updated README, usage docs, design doc, ADR index, new ADR-0019, root
  AGENTS lexicon, contract rules, dispatch/dm skills, and plugin README.
- Local review fixes:
  - Exact title resolution now accepts an owned handle without the leading `@`
    so current Codex titles like `Docs Thread` resolve when the stored handle is
    `@Docs Thread`.
  - Managed-thread outputs now include optional `cwd` alongside `ref`, full id,
    handle/title, source, and status.
  - Trigger runner resolution uses the shared selector resolver so trigger lane
    selectors can be dispatch refs.
- Follow-up P2 fix from PR #33 review:
  - `ActionAck`, `GoalView`, `LaneSyncResult`, `TranscriptOutput`, and
    `WatchOutput` now include the same managed-thread identity fields:
    `lane`, `ref`, full `id`, `title`, `handle`, `managed`, `source`, `status`,
    and `cwd`.
  - `lane` remains as a compatibility field for the full Codex thread id.
  - Added schema and MCP structured-content regression tests for the identity
    fields.
  - Cleaned cheap user-facing help/docs wording from lane terminology to
    thread/ref terminology where it did not require a broad rename.
- Submitted draft PR:
  - PR #33: https://github.com/outfitter-dev/dispatch/pull/33
  - Base: `feat/top-level-thread-actions` (PR #32)
  - Head: `feat/stable-refs-flat-cli`

## Verification log

- Focused:
  - `uv run pytest tests/registry/test_store.py tests/core/test_examples.py -q`
    -> 14 passed.
  - `uv run pytest tests/core/test_selectors.py tests/core/test_handlers.py tests/core/test_examples.py -q`
    -> 56 passed.
  - `uv run pytest tests/surfaces -q` -> 25 passed.
  - `uv run pytest tests/registry/test_store.py tests/core/test_selectors.py tests/core/test_handlers.py tests/surfaces -q`
    -> 91 passed.
- Manual schema checks:
  - `uv run dispatch schema list`
  - `uv run dispatch schema tail`
  - `uv run dispatch schema watch`
  - Verified `list` resolves to `roster`, `tail` to `transcript`, `watch` to
    `watch`, and managed list output requires `ref`.
  - Follow-up schema verification:
    - `uv run dispatch schema send`
    - `uv run dispatch schema 'goal status'`
    - `uv run dispatch schema sync`
    - `uv run dispatch schema tail`
    - `uv run dispatch schema watch`
    - Verified each output schema includes `lane`, `ref`, `id`, `title`,
      `handle`, `managed`, `source`, `status`, and `cwd`.
- Manual isolated CLI smoke:
  - `DISPATCH_HOME=$(mktemp -d)/dispatch uv run dispatch up`
  - `uv run dispatch list --json` -> `{"lanes": []}`
  - `uv run dispatch schema list`
  - `uv run dispatch schema tail`
  - `uv run dispatch schema watch`
  - `uv run dispatch down`
- Static checks:
  - `uv run ruff check src/outfitter/dispatch tests` -> passed.
  - `uv run mypy src tests --strict` -> passed.
  - Follow-up static checks:
    - `uv run ruff check src/outfitter/dispatch tests` -> passed.
    - `uv run ruff format --check src/outfitter/dispatch tests` -> passed.
    - `uv run mypy src tests --strict` -> passed.
- Full gate:
  - `just check` -> passed.
  - Latest run: 189 passed, 9 deselected; build and package content check passed.
  - Follow-up run after managed-output identity fix: `just check` -> passed,
    190 passed, 9 deselected; build and package content check passed.
- PR checks:
  - PR #33 CI `check` completed successfully:
    https://github.com/outfitter-dev/dispatch/actions/runs/27037566995/job/79805326778
  - Follow-up PR #33 CI `check` after managed-output identity fix completed successfully:
    https://github.com/outfitter-dev/dispatch/actions/runs/27038216121/job/79807480244

## Review log

- Local self-review, 2026-06-05:
  - Reviewed registry migration/allocation, selector resolution, CLI/MCP
    projection changes, output schemas, and docs/skill drift.
  - P2 fixed: exact title matching did not recognize handle-without-`@`.
  - P2 fixed: managed outputs did not expose `cwd` where available.
  - No open P0/P1/P2 after fixes and `just check`.
- Draft PR #33 body updated with context, test proof, and risks.
- Follow-up local self-review, 2026-06-05:
  - Reviewed the reported P2 against `PLAN.md:146` and `PLAN.md:188`.
  - Fixed identity context for send/goal/sync/tail/watch outputs.
  - Added regression coverage at schema and MCP structured-content levels.
  - No open P0/P1/P2 before rerunning the full gate.

## Deferred/P3 notes

- No deferred P3s recorded yet.
