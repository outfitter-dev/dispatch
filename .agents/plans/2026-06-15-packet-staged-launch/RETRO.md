# Packet-staged launch - execution ledger

Durable execution ledger for packet-based Dispatch launch work.

## Execution Summary

- 2026-06-15: Packet created from Matt/Codex design discussion about Dispatch as
  a control plane for parallel Codex worker lanes.
- Planner did not implement source changes, create branches, create PRs, or
  update trackers.
- Starting repo state from `context-prime.sh`:
  - cwd: `/Users/mg/Developer/outfitter/dispatch`
  - branch: `main`
  - state: `main...origin/main`
  - recent head: `9150aab fix: enforce registry lane relationships (#44)`
  - open PRs for current branch: none

## Branch / PR / Issue Ledger

- Planning packet only.
- Branch: `main` at planning time.
- PR: none.
- Issues: none created or updated by planner.
- Executor should create a focused Graphite branch per phase if implementing
  under repo conventions, and should record exact branch/PR/issue state here.

## Key Decisions Captured

- Use `--input-file`, not `--text-file`.
- Support stdin for file-like inputs, with a one-stdin-consumer limit.
- Use packet conventions:
  - `dispatch.toml`
  - `goal.md`
  - `prompt.md`
  - `output.schema.json`
  - `base.md`
  - `developer.md`
  - `hooks/`
  - `codex/`
- Stage packet files into `.agents/sessions/<dispatch-ref>/packet/` inside the
  actual launched cwd.
- Use Dispatch short refs as session ids.
- Keep Dispatch generic; repo-local tooling owns packet generation and domain
  workflow.
- Dispatch stages hooks/config but does not execute arbitrary hooks.
- Hook trust bypass is allowed only as explicit operator/trusted policy, not as
  packet-only authority.
- Native Codex worktree support requires a protocol spike; do not assume paths.

## Execution Log

### Phase 1 — packet + file input sources + dry-run (branch `phase-1-packet-inputs`)

Implemented the generic packet/file/inline launch substrate with no staging or
worktree behavior.

- `NewInput` gained `packet`, `input_file`, `goal_file`, `output_schema_file`.
- New `core/packet.py`: `load_packet()` reads `goal.md`/`prompt.md`/`base.md`/
  `developer.md`/`output.schema.json`/`dispatch.toml`; records `hooks/`+`codex/`
  as aux dirs and any other entry as unknown (never fatal). `dispatch.toml` parses
  into a curated `_PacketConfig` (extra=forbid) → `NewSettings` (no `cwd`/content;
  packets stay portable).
- New `core/launch.py`: pure `resolve_launch(NewInput) -> ResolvedLaunch`. Slot
  ownership model — each of goal/prompt/output_schema/base/developer is owned
  wholesale by exactly one layer with precedence `inline > file > packet > config`,
  so a CLI `--base-file` is never shadowed by packet `base.md`. Emits per-slot
  `LaunchSource` (origin/path/bytes/sha256). `resolve_new()` gained a `packet`
  settings layer between presets and CLI.
- Dry-run is a separate **mutation-free op** `new-plan` (intent=read, output
  `LaunchPlan`), reached via CLI compose route `new --dry-run → new-plan` (mirrors
  `list --unmanaged → discover`). This makes "no mutation" structural, not a guard,
  and keeps each op a single honest output.
- `new_lane` now launches from the resolved bundle (packet goal/prompt/schema/
  instructions honored); shared `_validate_launch` runs in both `new` and
  `new-plan` so dry-run previews the same failures.
- CLI `new` is a custom command: derives all `NewInput` options, adds `--dry-run`,
  reads one stdin consumer (`--goal-file -`/`--input-file -`, single-consumer +
  inline-conflict guards, exit 2), and absolutizes packet/file paths against the
  caller cwd (the daemon's cwd differs). stdin must be CLI-side; the daemon has no
  terminal.
- MCP: `new-plan` added to the `dispatch_thread_read` group (parity-reachable).

Not yet done in Phase 1 (later phases): staged session directory (`--stage`/
`--inline`), hook/config staging, worktree spike.

## Verification Log

### Phase 1

- `just check` → EXIT=0 (ruff check + ruff format --check + mypy --strict +
  pytest 258 passed/9 deselected + build + package-contents check).
- Focused: `tests/core/test_packet.py` (15), `tests/core/test_handlers.py` (incl.
  packet launch, dry-run no-mutation, invalid-schema-before-thread-start),
  `tests/surfaces/test_derive_cli.py` (new routing/stdin/conflict/absolutize),
  `tests/surfaces/test_parity.py`, `tests/core/test_examples.py` — all green.
- Manual: `dispatch schema "new --dry-run"` → op `new-plan`; `dispatch schema new`
  exposes `packet`/`input_file`/`goal_file`/`output_schema_file`.
- No live daemon/app-server/user-state touched (pure resolution + fakes only).

- Planning verification:
  - Read repo `AGENTS.md` supplied in prompt.
  - Read `.agents/plans/PLANNING.md`.
  - Ran `/Users/mg/.agents/skills/goal-planning/scripts/context-prime.sh`.
  - Read goal-planning `code-review.md`, `source-control.md`, and
    `goal-runtimes.md`.
  - Inspected current `dispatch schema new`, current `NewInput`, `NewSettings`,
    `thread/start`, and `turn/start` schema facts during the design discussion.

## Discoveries

- Current Dispatch already has internal `output_schema` plumbing through
  `NewSettings` and `turn_start`.
- Current generated App Server schema includes `turn/start.outputSchema`.
- Current generated App Server schema shows `thread/start.config` as a raw
  object but Dispatch does not model it yet.
- Current generated App Server `UserInput` supports text, image URL, local
  image, skill, and mention inputs; packet v1 should start with text only unless
  images/mentions become a concrete requirement.

## Tracker Mutations

- None by planner.
- Executor should record any Linear/GitHub issue creation, comments, status
  changes, labels, dependencies, or explicit non-mutation decisions here.

## Local Review Log

### Phase 1 — round 1 (local reviewer subagent)

- Score: 3/5. Summary: solid architecture + coverage; one real path-resolution
  inconsistency.
- P1 — `derive_cli.py` `_PATH_FIELDS` omitted `base_file`/`developer_file`, so
  relative `--base-file`/`--developer-file` would resolve against the daemon cwd,
  not the caller's. **Fixed**: added both to `_PATH_FIELDS` (CLI absolutizes them
  like the other file flags; config-provided instruction files still resolve
  daemon-side relative to `config_dir`).
- P2 — `LaunchOrigin` declared a dead `"stdin"` value (stdin is inlined CLI-side).
  **Fixed**: removed `"stdin"`; origin is honestly `"inline"`.
- P2 — instruction `LaunchSource.path` could be relative. **Fixed** by the P1
  change (CLI now forwards an absolute path); added a regression test.
- P2 — missing explicit source-origin tests. **Fixed**: added
  `test_resolve_launch_reports_instruction_file_source` and
  `test_resolve_launch_inlined_stdin_goal_reports_inline_origin`.
- Re-verified: `just check` EXIT=0 (260 passed). No open P0/P1/P2.

Known non-blocking item (recorded, out of Phase 1 scope): `--cwd` itself is still
resolved daemon-side for relative values (pre-existing `new` behavior). Changing
it touches the whole `new`/cwd contract; deferred rather than rewritten here.

## Remote Review / CI Log

- Not started.
- Executor must record draft PR submission, CI/check state, remote code-review
  bot/agent summaries/scores, unresolved review-thread state, and any skipped
  remote review here.

## Forbidden Actions Audit

- Planner did not implement the target feature.
- Planner did not mutate live Dispatch daemon/user Codex state for this packet.
- Planner did not create a branch, commit, push, submit a PR, merge, publish, or
  change release state.
- Executor must preserve these constraints unless explicitly authorized.

## Final State

- Status: planning packet seeded, execution not started.
- Completion criteria for executor are in [`PLAN.md`](./PLAN.md) and
  [`GOAL.md`](./GOAL.md).
