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

- Planning packet only (planner).
- Execution (Graphite stack on `main`, one phase per branch, bottom → top):
  - `phase-1-packet-inputs` — packet + file inputs + mutation-free dry-run
    (commits: plan packet, feat, review-fix).
  - `phase-2-staged-session` — `--stage`/`--inline` staging (feat, review-fix).
  - `phase-3-protocol-spike` — protocol findings (docs only; no code).
  - `phase-4-docs` — operator/agent docs (docs, docs review-fix).
- PRs: **none submitted** (held for explicit user approval per goal stop rules).
- Issues: none created or updated.
- Working tree clean; all commits local. `just check` green at each phase tip.

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

### Phase 2 — staged session directory (branch `phase-2-staged-session`)

Durable staging of packet parts under `<launch-cwd>/.agents/sessions/<ref>/`.

- New `core/staging.py`: `resolve_stage_plan(stage, inline, available)` (pure;
  `all`/comma-list/`inline` removal; rejects unknown/unavailable parts) and
  `stage_session(...)` (sync, atomic: build under `.staging-<ref>/`, then
  `os.replace` into `<ref>/`). Writes `packet/` (resolved content twins +
  copied `hooks/`/`codex/`), empty `scratch/`, and `state.json` last.
  No-overwrite by default; target-escapes-cwd guard; cleans temp dir on any
  failure.
- Parts: config, goal, prompt, output_schema, base, developer, hooks,
  codex_config. Content parts are written from the *resolved* bytes (staged ==
  inlined); hooks/codex/config copied from the packet dir.
- CLI grammar: `--stage all` / `--stage prompt,goal` and `--inline ...` (dual
  vocabulary). Note: Typer ignores click `flag_value`, so bare `--stage`
  (no value) is not supported — `--stage all` is the canonical "everything"
  spelling. Recorded as a forced framework deviation from the plan's bare-flag.
- `resolve_launch` computes stage availability + a validated `StagePlan`;
  `plan_new_lane` reports `stage` (parts only, no writes); `new_lane` stages via
  `asyncio.to_thread(stage_session, ...)` AFTER goal-set, BEFORE the first turn.
- Staging failure (`StagingError`, new typed error exit 9 / rpc 1009) leaves the
  lane registered, logs a `stage` audit failure, and does NOT start the turn —
  exactly the plan's failure rule.
- `NewLane.staged` / `LaunchPlan.stage` (`StageView`) report parts + session dir
  + per-file byte/sha provenance.
- `.gitignore`: added `.agents/sessions/` (runtime artifacts).

## Phase 3 — protocol spike (branch `phase-3-protocol-spike`)

Investigation only — **no speculative code**, per the plan's stop rules. Regenerated
the current App Server protocol schema with an isolated `CODEX_HOME` (never the
user's live `~/.codex`/daemon):

```bash
CODEX_HOME=<tmp> codex app-server generate-json-schema --out <tmp/std>
CODEX_HOME=<tmp> codex app-server generate-json-schema --experimental --out <tmp/exp>
```

Binary pinned: **codex-cli 0.140.0-alpha.2** (protocol v2).

Findings (evidence: regenerated schema):

- **Worktree: NOT supported.** `worktree` appears nowhere in the standard OR
  experimental schema. `v2/ThreadStartParams.json` properties are: approvalPolicy,
  approvalsReviewer, baseInstructions, **config**, cwd, developerInstructions,
  ephemeral, model, modelProvider, personality, sandbox, serviceName, serviceTier,
  sessionStartSource, threadSource — no worktree request field.
  → **Stop rule fired: did NOT add `--worktree`.** Do not guess Codex worktree paths.
- **Returned thread fields:** `ThreadStartResponse` returns `cwd` (top-level) and a
  `thread` whose props include `cwd`, `path`, and **`gitInfo`** (there is a `GitInfo`
  definition). So when/if Codex adds native worktree creation, Dispatch can report
  the *actual* returned cwd rather than guessing. No wire-model change made now
  (YAGNI — no worktree request to pair it with; Dispatch's `ThreadInfo` already
  carries `cwd`/`path`).
- **`thread/start.config`:** present as a raw passthrough object
  (`type: [object, null], additionalProperties: true`). Dispatch deliberately does
  **NOT** wire packet→raw-config passthrough: that is an unverified trust/injection
  surface. The packet `codex/` dir is **staged-only** (Phase 2 `codex_config` part),
  which is the safe, verified behavior. → **Stop rule respected.**
- **Hooks:** the protocol has a hook system (`HookStartedNotification`,
  `HookPromptFragment`), but execution and trust are Codex's authority. Dispatch
  stages hook files (Phase 2 `hooks` part) and never executes them. A
  `--trust-staged-hooks` execution bypass would require explicit operator/trusted
  authority Dispatch does not model, and Dispatch has no hook-execution path at all.
  → **Stop rule fired: did NOT add `--trust-staged-hooks`.**
- **Hook/config reload semantics** (does Codex load project `.codex` hooks staged
  after `thread/start` but before `turn/start`?) is a Codex-side, experimental,
  version-specific behavior. Dispatch's contract is stage-not-execute, so the
  feature does not depend on it; recorded as not-relied-upon rather than proven via
  a heavy live probe.

Net: the worktree and hook/config questions are resolved — worktree is **not
supported** (recorded with evidence), and the safe hook/config posture is
**stage-only** with no trust bypass. No code changed in Phase 3.

### Phase 4 — docs (branch `phase-4-docs`)

Documented the new launch surface (no code):

- `docs/usage/README.md`: new "Launch Packets And File Inputs" + "Staged Session
  Directories" sections (packet layout, `--packet`/`--input-file`/`--goal-file -`/
  `--output-schema-file`, precedence, `--dry-run --json`, `--stage`/`--inline`,
  no-`--worktree` rationale).
- `skills/dispatch/SKILL.md`: packet/file/dry-run/stage paragraph under `new`.
- `README.md`: quickstart pointer to packet/dry-run/stage + usage doc.
- `plugins/dispatch/README.md`: one-line summary of the new `new` inputs.
- `docs/development/design.md`: extended the `new` command-surface line with the
  packet/file/dry-run/stage flags and the new-plan compose + no-worktree note.
- `just check` EXIT=0 (278 passed) after docs.

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

### Phase 2

- `just check` → EXIT=0 (276 passed/9 deselected, mypy clean, build + package check).
- New: `tests/core/test_staging.py` (12) — plan resolution + atomic writer +
  no-overwrite + atomic-cleanup-on-failure.
- `tests/core/test_handlers.py` — `new` stages packet parts (files on disk,
  state.json, turn still runs), staging failure prevents the turn (lane stays
  registered, no turn_start), `new-plan` reports stage parts without writing.
- `tests/surfaces/test_derive_cli.py` — `--stage`/`--inline` pass through.
- Manual: `dispatch schema new` exposes `stage`/`inline` + `staged`; `new --dry-run`
  output carries `stage`.
- Staging tests write only under `tmp_path`; no live daemon/user-state touched.

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
- Executor: **none** — no Linear/GitHub issues created, commented, or changed
  (the goal named no tracker target; staying read-free here is the safe default).

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

### Phase 2 — round 1 (local reviewer subagent)

- Score: 3/5. Summary: sound core; a few correctness/quality items.
- P0 (os.replace race on same ref) — **assessed overstated**: refs are unique
  per registry-assigned lane, so temp+target paths never collide and the rename
  into a non-existent target is atomic; OSError is already caught with cleanup.
  Added a clarifying comment instead of file-locking (YAGNI for a non-occurring
  failure mode).
- P1 (config part re-read dispatch.toml inside the writer) — **Fixed**: pre-read
  the raw `dispatch.toml` into `PacketContent.config_text` → `StageContent`; the
  writer no longer re-reads disk, so config matches goal/prompt handling.
- P1 (lane left `idle` after staging failure) — **Fixed**: mark the lane `error`
  on `StagingError` (mirrors the turn-failure path); test asserts it.
- P1 (`state.json` missing `source`) — **declined**: `source_packet` (None ⇒
  inline) already discriminates; no consumer needs a separate field. Recorded.
- P2 (status assertion; surface JSON staged test) — **Fixed**: added both
  (`test_new_lane_staging_failure_prevents_turn` asserts error status;
  `test_new_json_output_includes_staged_summary` checks rendered JSON).
- Re-verified: `just check` EXIT=0 (278 passed). No open P0/P1/P2.

### Phase 4 — round 1 (local reviewer subagent, docs accuracy)

- Score: 3/5. Three doc-vs-code mismatches, all **fixed**:
  - P1 — `SKILL.md` referenced `--text-file` (never implemented; it was the plan's
    design guidance). Reworded to describe `--input-file` (the file form of `--text`).
  - P2 — `docs/usage` said "`--goal` overrides `--goal-file`"; they are mutually
    exclusive (error), not precedence-ordered. Corrected to "a CLI input overrides
    the packet's goal.md".
  - P2 — `design.md` command surface showed `new <name>` (positional); it is
    `--name`. Changed to `new --name <name>`.
- Re-verified: no remaining `--text-file` refs; `just check` EXIT=0 (278 passed).
  No open P0/P1/P2.

## Remote Review / CI Log

- Not started.
- Executor must record draft PR submission, CI/check state, remote code-review
  bot/agent summaries/scores, unresolved review-thread state, and any skipped
  remote review here.

## Forbidden Actions Audit

Executor (this run):

- **No merge.** No `gt merge`/`gh pr merge`/`git merge` to `main` or anywhere.
- **No PR submission.** No `gt submit`/`gh pr create` — zero PRs (draft or not).
- **No publish / release.** `just check` runs `uv build` locally into `dist/`;
  nothing was uploaded/published; no version/release state changed.
- **No live state touched.** All tests use ephemeral stores / fakes / `tmp_path`.
  The Phase 3 schema probe used an isolated `CODEX_HOME` (job tmp dir), never the
  user's `~/.codex` or a live daemon. No live `dispatchd` was started or mutated.
- **No tracker mutations.** No Linear/GitHub issues created or changed.
- Local Graphite branches + commits only (explicitly authorized by the goal:
  "create a focused Graphite branch per phase").
- Planner constraints preserved: planner did not implement, branch, or mutate.

## Final State

- **Status: complete (pending user decision on PR submission).** All four phases
  implemented or recorded with evidence; `just check` EXIT=0 (278 passed,
  9 deselected) at the top of the stack; local reviews each closed to no open
  P0/P1/P2.
- Delivered: `--packet`, `--input-file`/`--goal-file`/`--output-schema-file`
  (with `-` stdin, single-consumer), mutation-free `--dry-run` (`new-plan` op),
  `--stage`/`--inline` staging to `.agents/sessions/<ref>/`, output-schema support,
  honest source/stage provenance, and full docs.
- Recorded-not-built (stop rules, with schema evidence): native `--worktree`
  (no App Server field), raw `thread/start.config` passthrough, `--trust-staged-hooks`
  (hooks stage-only; execution/trust is Codex's authority).
- Remaining risks / follow-ups:
  - `--cwd` relative values still resolve daemon-side (pre-existing; out of scope).
  - Bare `--stage` (no value) unsupported by Typer; `--stage all` is the spelling.
  - Hook/config reload semantics are Codex-side/experimental and deliberately not
    depended on; revisit if Codex adds native worktree or stable hook loading.
  - PRs not submitted — awaiting user approval to `gt submit --stack --draft`.
