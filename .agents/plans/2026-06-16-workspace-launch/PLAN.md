# Workspace launch - implementation plan

Goal-ready plan for adding a first-class workspace preparation layer to
`dispatch new`. Goal loop: [`GOAL.md`](./GOAL.md). Design notes:
[`WORKSPACE.md`](./WORKSPACE.md). References: [`REFS.md`](./REFS.md). Execution
ledger: [`RETRO.md`](./RETRO.md).

## Objective

Make worktree-backed `dispatch new` lanes boring and verifiable without
pretending the current Codex App Server has a native worktree request.

The first product slice should add a small `--workspace` layer that runs before
`thread/start` and resolves the exact cwd Dispatch will pass to the App Server.
It should understand repo-local Codex environment metadata, report what it did
in dry-run and launch JSON, and keep trust-sensitive setup execution explicit
and auditable.

This is the next step after packet/staging. Packets describe what to ask the
worker to do; workspace launch describes where and how the worker checkout is
prepared.

## Current Truth To Preserve

- Previous protocol spike found no native App Server `worktree` request field.
  Do not add a fake protocol field or assume a Codex worktree path.
- Dispatch already supports packets, file/stdin launch inputs, output schemas,
  staging to `.agents/sessions/<ref>/`, and stage-only `hooks/`/`codex/`.
- Current working tree also contains runtime-settings persistence for follow-up
  sends and a `history` MVP. Before implementing this packet, verify those
  changes are present or landed; do not start from a stale checkout silently.
- The main skill/docs now say exact `--cwd` and runtime identity are
  authoritative. This packet should turn that doctrine into a launch helper.

## Product Shape

Start with:

```bash
dispatch new --name lane-a --cwd /repo --packet packet --workspace auto --dry-run --json
dispatch new --name lane-a --cwd /repo --packet packet --workspace auto --stage all --json
dispatch new --name lane-a --cwd /repo --packet packet --workspace none
dispatch new --name lane-a --cwd /repo --packet packet --workspace athena
```

Recommended semantics:

- `--workspace none`: preserve current behavior. The effective cwd is the
  resolved `--cwd` / config cwd. No environment discovery or setup.
- `--workspace auto`: discover the repo/workspace metadata from cwd. If no
  supported metadata is present, degrade to a no-op with a clear JSON reason.
- `--workspace <preset>`: load a named workspace preset from Dispatch config.
  Presets should be generic and variable-backed, not domain-specific.
- Config should be able to choose the default, for example:

  ```toml
  [workspace]
  default = "auto"
  setup = "trusted-only"

  [workspace.presets.athena]
  mode = "auto"
  setup = "trusted-only"
  ```

Names are tentative; keep the implementation smaller than the vocabulary if a
first pass only needs `none` and `auto`.

## Supported Environment Metadata

First target: repo-local Codex environment files at:

```text
.codex/environments/environment.toml
```

Observed examples:

```toml
version = 1
name = "athena-vault"

[setup]
script = "./.codex/hooks/workspace-bootstrap.sh"

[cleanup]
script = "./.codex/hooks/workspace-teardown.sh"
```

```toml
version = 1
name = "trails"

[setup]
script = "./scripts/bootstrap.sh codex"

[cleanup]
script = "./scripts/bootstrap.sh teardown"
```

Phase 1 should parse `version`, `name`, `[setup].script`, and
`[cleanup].script`. Unknown fields should be preserved in diagnostics but not
executed.

## Trust And Execution Boundary

Dispatch must not become a general hook runner that silently executes packet
contents. This feature is different because repo-local environment files are
already the repo's declared Codex workspace setup contract. Even so, execution
must be explicit and auditable.

Initial policy:

- `--workspace auto` may discover and report environment metadata by default.
- Setup execution requires trusted local config or an explicit CLI opt-in. Pick
  one small policy and document it before implementation.
- Packet-local `dispatch.toml` may request workspace behavior, but it must not
  be enough by itself to grant script execution.
- Scripts run from the resolved repo root/workspace root, with a bounded timeout
  and captured stdout/stderr summary.
- Cleanup/teardown should be recorded for future work, but do not build a
  daemon lifecycle manager in the first slice unless required by setup.

## Implementation Phases

### Phase 1 - Workspace discovery and dry-run

Add pure discovery and preview without executing setup scripts.

- Add input/model fields for `workspace` on `NewInput` and `LaunchPlan`.
- Create a focused module such as `core/workspace.py`.
- Resolve:
  - input cwd;
  - git repo root when available;
  - environment file path;
  - environment name/version;
  - setup/cleanup script strings;
  - effective launch cwd.
- Add `workspace` block to `new --dry-run --json` and `new` JSON output.
- Keep `--workspace auto` no-op when no metadata exists, with
  `state = "not_found"` or similar.
- Tests:
  - no metadata means no-op;
  - Athena/Trails environment TOML parses;
  - invalid TOML fails before thread creation;
  - dry-run reports workspace facts and performs no script execution;
  - CLI/MCP/schema projections include the field.

### Phase 2 - Trusted setup execution before launch

Execute environment setup only when policy allows it.

- Decide minimal operator policy:
  - config-only, for example `[workspace] allow_setup = true`; or
  - explicit flag, for example `--workspace-setup run`; or
  - both, where CLI can narrow but not widen trust.
- Run setup after launch resolution and before `thread/start`.
- If setup changes the cwd/worktree, update the effective launch cwd from
  runtime evidence, not guessed paths.
- Include setup result in JSON:
  - script;
  - cwd;
  - exit code;
  - duration;
  - stdout/stderr tail;
  - effective cwd after setup.
- Failure should be typed and should prevent thread creation unless the mode is
  explicitly advisory.
- Tests:
  - setup not run without trust;
  - trusted setup runs with cwd and timeout;
  - setup failure prevents `thread/start`;
  - dry-run never runs setup;
  - output captures bounded logs.

### Phase 3 - Worktree creation policy, if still needed

Only after Phase 1/2 evidence, decide whether Dispatch should create git
worktrees itself.

Possible shape:

```bash
dispatch new --workspace auto --worktree create --worktree-name lane-a
```

Constraints:

- Prefer repo-local setup scripts if they already create/select the worktree.
- If Dispatch creates git worktrees, use vanilla `git worktree add` with exact,
  visible paths and branch diagnostics.
- Never guess Codex's private worktree location as the default.
- Report branch/head/cwd in dry-run and launch JSON.
- Give clear diagnostics when a branch is already checked out elsewhere.

This phase can be deferred if `environment.toml` setup gives enough coverage.

### Phase 4 - Docs, skill, and smoke

- Update `docs/usage/README.md`, `docs/development/design.md`,
  `skills/dispatch/SKILL.md`, and packet docs.
- Add examples for Athena/Trails-style environment files.
- Add `history` guidance for post-launch worktree inspection.
- Run `just check`.
- Run a disposable smoke with isolated `DISPATCH_HOME` and `CODEX_HOME` only if
  the executor can do so without touching live user daemon/state.

## Acceptance Criteria

- `dispatch new --workspace auto --dry-run --json` reports workspace discovery
  facts without mutating daemon, registry, threads, or workspace files.
- `dispatch new --workspace none` exactly preserves current launch behavior.
- Supported `.codex/environments/environment.toml` files parse and are reported.
- Trusted setup execution, if implemented, is impossible from packet-only config
  and is visible in JSON output.
- Effective cwd passed to App Server is exact and reported.
- Stage path remains under the effective launch cwd.
- Follow-up docs/skill make clear that Dispatch does not execute packet hooks
  and does not assume Codex worktree paths.
- `just check` passes.
- `RETRO.md` is finalized before claiming completion.

## Stop Rules

Stop and report if:

- Current App Server adds native worktree fields that make this plan obsolete;
  regenerate schemas and redesign around the native contract.
- Setup execution would require trusting packet-local files without operator
  policy.
- Workspace setup needs long-running lifecycle management or teardown ownership
  beyond a bounded pre-launch script.
- Implementing this requires broad CLI/MCP projection rewrites unrelated to
  `new`.
- Existing uncommitted user changes make it unsafe to edit a target file.

