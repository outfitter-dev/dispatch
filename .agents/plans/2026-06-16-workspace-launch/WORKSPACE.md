# Workspace Launch Design Notes

This document records the intended shape of Dispatch workspace launch support.
It is not an ADR yet; promote durable decisions after the implementation proves
the shape.

## Why This Exists

Parallel worker lanes often need more than a prompt. They need the right
checkout, repo bootstrap, generated helper files, and output directories before
the first turn. Packet staging solved "what should the worker read". Workspace
launch should solve "where should the worker run, and was that environment
prepared".

The feature should reduce ceremony for coordinators while keeping Dispatch out
of domain workflows. Repo-local tooling still owns what setup means.

## Core Model

Workspace launch is a preflight layer for `dispatch new`:

1. Resolve launch inputs and packet/config settings.
2. Resolve workspace mode from CLI/config.
3. Discover repo/workspace metadata from the input cwd.
4. Optionally run trusted setup.
5. Compute the exact effective cwd.
6. Call `thread/start` with that cwd.
7. Stage packet files under that same effective cwd.
8. Return JSON that explains every workspace decision.

The effective cwd is a value, not a guess. If setup or a future native Codex
feature returns a different cwd, Dispatch reports and uses that returned cwd.

## Terminology

- Workspace: the checkout/environment Dispatch launches into.
- Environment file: repo-local `.codex/environments/environment.toml`.
- Setup script: bounded pre-launch script declared by the environment file.
- Cleanup script: declared teardown command, recorded but not necessarily owned
  by Dispatch in the first implementation.
- Worktree: a git checkout. It may be managed by Codex, repo scripts, or a
  future Dispatch helper.

## JSON Shape Sketch

Dry-run and launch output should include something like:

```json
{
  "workspace": {
    "mode": "auto",
    "state": "discovered",
    "input_cwd": "/repo",
    "repo_root": "/repo",
    "effective_cwd": "/repo",
    "environment_file": "/repo/.codex/environments/environment.toml",
    "environment": {
      "version": 1,
      "name": "athena-vault",
      "setup_script": "./.codex/hooks/workspace-bootstrap.sh",
      "cleanup_script": "./.codex/hooks/workspace-teardown.sh"
    },
    "setup": {
      "policy": "not_allowed",
      "ran": false
    }
  }
}
```

If setup runs:

```json
{
  "setup": {
    "policy": "trusted",
    "ran": true,
    "script": "./scripts/bootstrap.sh codex",
    "cwd": "/repo",
    "exit_code": 0,
    "duration_ms": 827,
    "stdout_tail": "...",
    "stderr_tail": "",
    "effective_cwd": "/repo"
  }
}
```

Keep logs bounded. Do not leak secrets if scripts print them; prefer tails and
document the risk.

## Config Sketch

Possible minimal config:

```toml
[workspace]
default = "auto"
allow_setup = false
setup_timeout_seconds = 120

[workspace.presets.athena]
mode = "auto"
allow_setup = true

[workspace.presets.noop]
mode = "none"
```

Rules:

- CLI `--workspace none` always disables discovery/setup.
- CLI `--workspace auto` enables discovery. It should not by itself grant setup
  execution unless that is the explicit final product decision.
- Named presets come from trusted Dispatch config.
- Packet-local config can request a workspace mode, but cannot grant setup
  trust.

## Environment File v1

Supported first-pass schema:

```toml
version = 1
name = "repo-name"

[setup]
script = "./relative-or-shell-command"

[cleanup]
script = "./relative-or-shell-command"
```

Execution policy details to decide during implementation:

- Use shell or argv splitting. Shell is compatible with Trails
  `"./scripts/bootstrap.sh codex"` but has quoting/trust implications.
- Run from repo root or environment-file parent. Prefer repo root because both
  observed examples use repo-relative paths.
- Bound timeout and captured output.
- Decide whether cleanup is manual, future trigger-owned, or recorded only.

## Relationship To `--worktree`

Do not expose `--worktree` as native Codex behavior until the App Server has a
native worktree contract. If Dispatch later adds worktree creation, it should be
plain git setup before launch, clearly reported as Dispatch-created, and not
hidden behind Codex terminology.

Likely sequence:

1. Ship `--workspace auto` discovery/reporting.
2. Ship trusted setup execution.
3. Evaluate whether repo setup scripts cover worktree creation.
4. Add a small explicit git worktree helper only if needed.

## Open Questions

- Should setup execution be config-only, CLI-only, or require both?
- Should `auto` be the default immediately, or should config choose it?
- Should cleanup be a recorded command, a future trigger, or intentionally
  outside Dispatch?
- Should workspace presets live in `.dispatch/config.toml`,
  `~/.dispatch/config.toml`, or both with local/global precedence?

