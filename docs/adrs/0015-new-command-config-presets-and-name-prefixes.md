---
id: 0015
slug: new-command-config-presets-and-name-prefixes
title: New Command, Config Presets, and Name Prefixes
status: proposed
created: 2026-06-03
updated: 2026-06-09
owners: ['[galligan](https://github.com/galligan)']
---

# ADR-0015: New Command, Config Presets, and Name Prefixes

## Context

`dispatch open` currently creates an owned lane with only `name` and `cwd`; follow-up work is sent with `dispatch send`. The App Server exposes richer session and turn configuration than dispatch currently projects: sandbox, approval policy/reviewer, model/provider, base/developer instructions, personality, service tier, reasoning effort, summaries, output schema, and related settings.

Users need an ergonomic creation workflow that applies repo-local defaults and reusable presets, starts a lane with the right name shape, and optionally sends an initial payload. Consistent name prefixes matter because Codex desktop and multi-lane workflows quickly become visually noisy: a lane created from the dispatch repo should be easy to distinguish as `[dispatch] review` or similar.

## Decision

Add an ergonomic `dispatch new` workflow. `open` remains the primitive for creating an owned lane; `new` is the configured product command:

- Resolve configuration and presets.
- Create an owned lane with the resolved App Server session settings.
- Register the lane.
- Optionally start the initial turn unless `--no-send` is used.

Example:

```bash
dispatch new --name review --preset reviewer --text "Review the current branch."
dispatch new --name builder --preset builder --preset fast --no-send
```

### Config

Support global configuration at `~/.dispatch/config.toml` plus repo-local
configuration at `.dispatch/config.toml`. The repo config is discovered from
the requested `cwd` by walking upward to the repo/project root and overrides
the global layer.

Configuration has defaults plus named presets:

```toml
[defaults]
cwd = "."
sandbox = "read-only"
approval_policy = "never"
effort = "medium"
ephemeral = false
prefix = "[${DISPATCH.CWD.REPO}]"

[defaults.instructions]
developer_file = ".dispatch/instructions/default.md"

[presets.reviewer]
effort = "high"
sandbox = "read-only"
approval_policy = "never"
developer_file = ".dispatch/instructions/reviewer.md"
prefix = "[${DISPATCH.CWD.REPO}]"

[presets.builder]
sandbox = "workspace-write"
approval_policy = "on-request"
developer_file = ".dispatch/instructions/builder.md"
prefix = "[${DISPATCH.CWD.REPO}]"

[presets.fast]
effort = "low"

[presets.safe-profile]
permission_profile = ":read-only"
```

Omit `model` unless you intentionally want Codex to use an explicit model.
When a preset does pin a model, choose it from the live App Server catalog
(`dispatch models`) rather than from docs or stale examples.

Merge order:

1. Built-in safe defaults.
2. Global `~/.dispatch/config.toml`.
3. Repo `.dispatch/config.toml`.
4. Presets in CLI order, left to right.
5. CLI flags.

Later presets win over earlier presets. CLI flags always win.

### Name Prefixes

Lane names support an optional prefix derived from config or presets. Prefixes are applied consistently before registration and before any App Server thread naming updates.

For a repo named `dispatch`, the default template:

```toml
prefix = "[${DISPATCH.CWD.REPO}]"
```

creates names like:

```text
[dispatch] review
[dispatch] builder
```

Prefix templates are deliberately small. Initial supported variables should be limited to stable, local facts:

- `${DISPATCH.CWD.REPO}` — repository/project directory basename.
- `${DISPATCH.CWD.BASENAME}` — cwd basename.
- `${DISPATCH.PRESET}` — final selected preset name, when unambiguous.

Do not expose arbitrary environment interpolation in v1; it is too easy to leak secrets or create non-reproducible names.

### Option Coverage

dispatch should project App Server/SDK options where they are available and verified, but not invent fake knobs. Initial candidates:

- thread/session: `cwd`, `permission_profile`, `sandbox`, `approval_policy`, `approvals_reviewer`, `model`, `model_provider`, `base_instructions`, `developer_instructions`, `personality`, `ephemeral`, `service_tier`.
- initial turn: `text`, `effort`, `summary`, `sandbox_policy`, `approval_policy`, `approvals_reviewer`, `model`, `service_tier`, `output_schema`.

Options should be added through the contract layer so CLI, MCP, remote, docs, schemas, and examples derive from one source.

`permission_profile` is validated against the cwd-aware App Server catalog and
is mutually exclusive with explicit sandbox and granular approval settings.

## Consequences

### Positive

- Gives users one command for the common "create a configured lane and maybe start work" flow.
- Makes repo/project identity visible in Codex session lists.
- Keeps rich App Server settings declarative and repeatable.
- Lets teams encode safe defaults and common lane profiles without long command lines.

### Tradeoffs

- Config merge semantics become a user-facing contract and need tests.
- Prefix templating needs careful escaping and duplicate-prefix handling.
- More App Server options mean more surface area to verify against the generated schema.

## Alternatives considered

- **Add every option to `open` only** — rejected: `open` is the primitive; `new` should own the ergonomic configured workflow.
- **No config; only CLI flags** — rejected: too repetitive and discourages safe project defaults.
- **Arbitrary template/environment expansion** — rejected for v1: powerful, but too easy to leak secrets or create surprising names.
- **Hard-code `[repo]` prefixes** — rejected: useful default, but users need preset/repo control.

## References

- ADR-0000 (Contract-First, Surface-Derived Design)
- ADR-0010 (Surface Projections Are Ergonomic, Not Isomorphic)
- `docs/research/app-server-verification.md`
