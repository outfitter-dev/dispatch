# Packet-staged launch - implementation plan

Goal-ready plan for adding durable launch packets, staged session files, and
worktree-aware launch groundwork to Dispatch. Goal loop: [`GOAL.md`](./GOAL.md).
References: [`REFS.md`](./REFS.md). Execution ledger:
[`RETRO.md`](./RETRO.md).

## Objective

Make `dispatch new` boring and verifiable for parallel worker-lane launches
that start from durable packet files instead of one-off shell strings.

The first product slice should support:

- packet directories containing `goal.md`, `prompt.md`, optional
  `output.schema.json`, optional instructions, and optional packet config;
- explicit file/stdin inputs such as `--input-file` and `--goal-file`;
- a staged session directory under `.agents/sessions/<ref>/`;
- `--stage` / `--inline` semantics that can later target specific packet
  parts;
- dry-run JSON that proves what would launch without mutating daemon/thread
  state;
- honest launch summaries with packet/stage/input details.

The goal must not turn Dispatch into an autoparalegal/domain workflow engine.
Repo-local tooling owns packet generation, branch/worktree decisions, and
domain schema. Dispatch owns the generic launch substrate.

## Product Decisions To Preserve

- Prefer `--input-file` over `--text-file`.
- Support stdin for file-like launch inputs, especially `--goal-file -` and
  `--input-file -`; reject multiple stdin consumers.
- Keep native App Server goals first-class; `goal.md` becomes a native goal,
  not `/goal` text inside the prompt.
- Packet staging path should be:

  ```text
  <actual-cwd>/.agents/sessions/<dispatch-ref>/
    packet/
    scratch/
    state.json
  ```

- Use the Dispatch short ref for the session id/path after the lane is
  registered.
- `--stage` with no value means stage all stageable packet parts. Later,
  `--stage prompt,goal,hooks` or similar should work.
- `--inline` is the dual vocabulary for launch content embedded into App
  Server fields. Some fields are necessarily inline at the protocol level
  (`goal`, `prompt`, `output_schema`, instructions), even when the files are
  also staged for durability.
- Dispatch must not execute arbitrary hooks. It may stage hook/config files and
  may pass verified Codex config overrides. Codex/hook trust policy remains the
  execution authority.
- Hook trust bypass may be configurable only as an explicit operator/trusted
  repo capability. A packet may request it, but packet-local config must not be
  enough to grant it silently.
- Native Codex worktree launch must be investigated before implementation.
  Do not assume Codex worktree paths; report the cwd/worktree Codex actually
  returns.

## Packet Format

Initial blessed packet directory:

```text
packet/
  dispatch.toml
  goal.md
  prompt.md
  output.schema.json
  base.md
  developer.md
  hooks/
  codex/
```

Semantics:

- `dispatch.toml`: packet-local launch settings. It should accept a safe subset
  of `NewSettings` plus packet/stage policy. Avoid naming it `config.toml`
  because repo-level `.dispatch/config.toml` already exists.
- `goal.md`: native goal objective. Equivalent to `--goal-file`.
- `prompt.md`: initial turn text. Equivalent to `--input-file`.
- `output.schema.json`: JSON Schema for `turn/start.outputSchema`.
- `base.md`: `thread/start.baseInstructions`.
- `developer.md`: `thread/start.developerInstructions`.
- `hooks/`: staged hook scripts or helper files. Dispatch stages them; it does
  not execute them.
- `codex/`: optional staged Codex config/hook definition files. Treat as
  experimental unless current App Server config/hook loading is verified.

Unknown files should not fail the packet by default. Record them in dry-run or
stage metadata when cheap, but do not infer behavior from them.

## Suggested CLI Shape

Core:

```bash
dispatch new --name lane-a --cwd /repo --packet packet-dir --json
dispatch new --name lane-a --cwd /repo --packet packet-dir --dry-run --json
dispatch new --name lane-a --cwd /repo --input-file prompt.md --goal-file goal.md
printf 'goal text' | dispatch new --name lane-a --goal-file - --input-file prompt.md
```

Staging:

```bash
dispatch new --packet packet-dir --stage
dispatch new --packet packet-dir --stage all
dispatch new --packet packet-dir --stage prompt,goal,output_schema
dispatch new --packet packet-dir --inline prompt,goal,output_schema
```

Hook/config trust:

```bash
dispatch new --packet packet-dir --stage hooks
dispatch new --packet packet-dir --stage hooks --trust-staged-hooks
```

`--trust-staged-hooks` should be an explicit operator/trusted-policy capability,
not something a packet can grant by itself.

Worktree, after spike:

```bash
dispatch new --packet packet-dir --worktree --cwd /repo --stage
```

In native worktree mode, `--cwd` is the source repo/root hint. Dispatch should
stage only after Codex returns the actual cwd/worktree path.

## Implementation Phases

### Phase 1 - packet and file input sources

One branch. Add the generic packet/file-source substrate without staged writes
or native worktree behavior.

- Add `NewInput` fields:
  - `packet: str | None`
  - `input_file: str | None`
  - `goal_file: str | None`
  - `output_schema_file: str | None`
  - `dry_run: bool`
- Decide whether explicit `base_file`/`developer_file` stay as-is or gain
  packet aliases via `base.md` / `developer.md`.
- Implement a packet resolver module. It should:
  - resolve paths relative to the packet directory;
  - read UTF-8 text for markdown files;
  - parse JSON for `output.schema.json`;
  - merge packet settings below CLI flags and above repo defaults only if that
    ordering is consciously chosen and tested;
  - reject multiple stdin consumers;
  - reject ambiguous conflicts clearly.
- Implement `--dry-run --json` with no daemon/thread mutation. Include resolved
  cwd, packet path, input sources, byte counts or hashes, effective settings,
  and whether a turn would be sent.
- Keep `--text` compatibility unless the plan consciously renames it. Prefer
  new docs/examples around `--input-file`.
- Tests:
  - packet happy path;
  - explicit file happy path;
  - stdin for one file-like input;
  - multiple stdin consumers rejected;
  - invalid JSON schema file rejected before thread creation;
  - dry-run does not call `thread_start`, `thread_goal_set`, or `turn_start`;
  - schema/help projection updated.

### Phase 2 - staged session directory

Add `--stage` / `--inline` grammar and staged copies under the actual launched
cwd.

- Add a stage plan model with part names such as:
  - `config`
  - `goal`
  - `prompt`
  - `output_schema`
  - `base`
  - `developer`
  - `hooks`
  - `codex_config`
  - `all`
- `--stage` with no explicit value should mean `all`.
- Use the registered Dispatch short ref for:

  ```text
  .agents/sessions/<ref>/packet/
  .agents/sessions/<ref>/scratch/
  .agents/sessions/<ref>/state.json
  ```

- Stage via a temp dir/atomic rename pattern so hooks or tools do not observe a
  half-written packet.
- Write `state.json` last with lane id/ref, source packet path, staged path,
  files, hashes/byte counts, phase, and launch state.
- If staging fails after thread creation but before first turn, leave the lane
  registered, do not start the turn, and return/report a clear typed error.
- Launch should read from staged files when staged; otherwise from original
  packet/source files.
- Tests:
  - stage path uses ref;
  - target path stays inside actual cwd;
  - no overwrite by default;
  - failure prevents turn start;
  - JSON launch output includes stage summary.

### Phase 3 - hooks/config and worktree protocol spike

Do not implement broad worktree/config assumptions until the spike passes.

- Regenerate current App Server schemas with:

  ```bash
  codex app-server generate-json-schema --out <tmp>
  codex app-server generate-json-schema --experimental --out <tmp>
  ```

- Verify current `thread/start` worktree/native-worktree request fields, if
  any. If none exist, record that and stop before adding a fake `--worktree`.
- Verify returned thread fields for cwd/path/git info and update Dispatch wire
  models only for fields actually returned.
- Verify whether project `.codex` config/hooks staged after `thread/start` but
  before `turn/start` are loaded for first-turn hooks. Include hook trust
  behavior.
- If current Codex supports raw `thread/start.config`, decide whether Dispatch
  should expose a narrow `codex_config` packet file or keep it staged-only.
- Add `--worktree` only if the current App Server supports native worktree
  creation. Dispatch must report the actual returned cwd/worktree, not a
  guessed path.
- Add hook trust bypass only as an explicit trusted capability such as
  `--trust-staged-hooks`, with dry-run reporting requested/allowed/effective.

### Phase 4 - docs, review, and release readiness

- Update README, `docs/usage/README.md`, `skills/dispatch/SKILL.md`,
  `plugins/dispatch/README.md`, and any ADR/design docs touched by the new
  launch semantics.
- Add examples for:
  - `--packet`;
  - `--input-file`;
  - `--goal-file -`;
  - `--dry-run --json`;
  - staged session directory behavior.
- Run focused tests after each phase and `just check` before review.
- Request local review using the repo review contract. Fix P0/P1/P2. Record
  P3s as fixed or deferred in `RETRO.md`.
- Submit draft PRs only if the execution instructions authorize source-control
  writes. Keep PRs draft until CI and review are clean. Do not merge.

## Validation Ladder

Use repo tasks first:

```bash
just lint
just typecheck
just test
just check
```

Focused tests likely include:

```bash
uv run pytest tests/core/test_new_config.py tests/core/test_handlers.py tests/surfaces/test_derive_cli.py tests/surfaces/test_parity.py -q
```

If live App Server behavior is spiked, isolate state:

```bash
DISPATCH_HOME=<tmp> CODEX_HOME=<tmp> uv run pytest tests/integration/test_app_server.py -q
```

Never point integration tests at the user's live `~/.codex` or live Dispatch
daemon.

## Stop Rules

Pause and report instead of guessing if:

- current App Server schemas do not expose a native worktree request field;
- hook/config reload semantics cannot be proven;
- a packet-local hook trust request would require bypassing Codex trust without
  explicit operator/trusted-config approval;
- implementing packet staging requires broad, unrelated CLI projection rewrites;
- tests would need live user `~/.codex` or live daemon state.

## Done

Done only when:

- packet/file input, dry-run, staged session behavior, and docs are implemented
  or explicitly scoped/deferred with evidence;
- the native worktree and hook/config questions are proven or clearly recorded
  as not supported by the current App Server;
- `just check` passes;
- local review has no unresolved P0/P1/P2;
- `RETRO.md` contains final verification, review state, source-control state,
  forbidden-action audit, and remaining risks.
