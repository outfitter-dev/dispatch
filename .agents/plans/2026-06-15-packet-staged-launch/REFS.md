# Packet-staged launch - references

## Repo Guidance

- `AGENTS.md`
  - Author once, derive surfaces.
  - App Server access only through `client/`.
  - Async core, sync CLI.
  - Use repo tasks via `just`.
  - Read `docs/development/design.md` and `.agents/plans/v0/PLAN.md` before
    implementation.
- `.agents/plans/PLANNING.md`
  - One phase = one Graphite branch.
  - PRs stay draft until checks/review are clean.
  - `just check` is the gate.
  - Local review output must include score, summary, P0-P3 findings, and
    prompt-to-fix.

## Product Discussion Summary

- Matt wants Dispatch to be the control plane for parallel Codex worker lanes.
- Repo-local tools generate durable packet files; Dispatch launches and records
  the generic lane/session mechanics.
- Desired lane pattern:
  - concise native goal;
  - longer prompt/input file;
  - optional structured output schema;
  - optional config/instruction files;
  - optional staged hooks/config files;
  - exact cwd or Codex-native worktree once verified;
  - compact JSON summaries.
- Reviewer agents for domain workflows should be subagents inside worker
  threads, not separate Dispatch/Codex lanes unless the domain explicitly
  chooses that later.

## Current Dispatch Hot Spots

- `src/outfitter/dispatch/core/models.py`
  - `NewInput`, `NewLane`, output models.
- `src/outfitter/dispatch/core/new_config.py`
  - `NewSettings`, repo `.dispatch/config.toml` merge, instruction file
    resolution.
- `src/outfitter/dispatch/core/handlers.py`
  - `new_lane`, native goal set, initial `turn_start`.
- `src/outfitter/dispatch/client/models.py`
  - `ThreadStartParams`, `TurnStartParams`, `UserInput` modeling gaps.
- `src/outfitter/dispatch/client/client.py`
  - App Server request wrappers, including `turn_start`.
- `src/outfitter/dispatch/contracts/derive_cli.py`
  - CLI projection and custom `new` command handling.
- `src/outfitter/dispatch/contracts/derive_mcp.py`
  - MCP grouped tool projection.
- `tests/core/test_handlers.py`
- `tests/core/test_new_config.py`
- `tests/surfaces/test_derive_cli.py`
- `tests/surfaces/test_parity.py`

## Existing Docs To Update

- `README.md`
- `docs/usage/README.md`
- `docs/development/design.md`
- `docs/adrs/0015-new-command-config-presets-and-name-prefixes.md`
- `skills/dispatch/SKILL.md`
- `plugins/dispatch/README.md`

## Verified Schema Facts From Planning Discussion

Fresh schema generation command used during planning:

```bash
codex app-server generate-json-schema --out /tmp/dispatch-appschema.NnuTPw
```

Relevant current App Server fields:

- `thread/start` params include:
  - `approvalPolicy`
  - `approvalsReviewer`
  - `baseInstructions`
  - `config`
  - `cwd`
  - `developerInstructions`
  - `sandbox`
  - `serviceTier`
  - `ephemeral`
  - `serviceName`
  - `sessionStartSource`
  - `model`
  - `modelProvider`
  - `threadSource`
  - `personality`
- `turn/start` params include:
  - `input`
  - `threadId`
  - `serviceTier`
  - `approvalPolicy`
  - `approvalsReviewer`
  - `clientUserMessageId`
  - `cwd`
  - `effort`
  - `sandboxPolicy`
  - `model`
  - `outputSchema`
  - `summary`
  - `personality`
- `UserInput` currently supports:
  - text
  - image URL
  - local image
  - skill
  - mention

Do not rely on `/tmp/dispatch-appschema.NnuTPw` existing during execution;
regenerate schemas if protocol details matter.

## Validation Commands

Focused likely commands:

```bash
uv run pytest tests/core/test_new_config.py tests/core/test_handlers.py tests/surfaces/test_derive_cli.py tests/surfaces/test_parity.py -q
```

Gate:

```bash
just check
```

Optional App Server probe must isolate state:

```bash
DISPATCH_HOME="$(mktemp -d)" CODEX_HOME="$(mktemp -d)" uv run pytest tests/integration/test_app_server.py -q
```

## Review Contract

Use the local review shape from `.agents/plans/PLANNING.md`:

```text
Overall score: n/5
Summary: <one line>
Findings:
- P0|P1|P2|P3 — <file:line> — <finding>
  Prompt To Fix With AI: <concise fix prompt>
No-findings statement: <inspected, residual risk>
```

Fix all P0/P1/P2 before ready/handoff unless Matt explicitly accepts the risk.
