# References: Claude Control-Plane Research

## Repository

- `AGENTS.md` - repository workflow, no-drift contracts, source-control, tests, and safety rules.
- `docs/development/design.md` - approved daemon, lane, contract, and surface architecture.
- `docs/adrs/0002-single-daemon-over-one-app-server.md` - current Codex-only daemon ownership.
- `docs/adrs/0006-handler-context-and-di.md` - injected client/handler boundary.
- `docs/adrs/0007-normalized-internal-lane-events.md` - normalized lane event contract.
- `docs/adrs/0013-dispatch-mesh-is-daemon-federation.md` - future multi-machine constraints.
- `docs/adrs/0023-provider-event-log-and-history-index.md` - Claude hooks, receipts, provider events, and zmx as an adapter detail.
- `src/outfitter/dispatch/contracts/context.py` - current Codex client protocol.
- `src/outfitter/dispatch/core/handlers.py` - current `new`, `send`, steer, context, interject, queue, stop, and lifecycle behavior.
- `src/outfitter/dispatch/core/server_requests.py` - Codex attention/request behavior to compare rather than assume.
- `src/outfitter/dispatch/core/queue.py`, `subscriptions.py`, and `reactor.py` - provider-neutral queue and event consumers.
- `src/outfitter/dispatch/registry/models.py` and `registry/store.py` - lane/provider/event/receipt/runtime persistence seams.
- `spikes/README.md` - probe conventions.

## Linear

- [DIS-9](https://linear.app/outfitter/issue/DIS-9/map-claude-hook-events-onto-provider_events-after-codex-substrate) - existing Claude hook/event mapping issue and prior zmx findings.
- [DIS-48](https://linear.app/outfitter/issue/DIS-48/make-claude-usage-capture-installable-transparent-and-reversible) - Claude usage capture lifecycle; adjacent but not messaging scope.
- [DIS-49](https://linear.app/outfitter/issue/DIS-49/add-claude-and-codex-provider-selection-shorthands) - canonical execution-provider field and CLI shorthands.

## Official Claude Sources

- [Agent View](https://code.claude.com/docs/en/agent-view) - background agents, discovery, and control surface.
- [Hooks reference](https://code.claude.com/docs/en/hooks) - lifecycle, tool, prompt, permission, notification, stop, and session events.
- [CLI reference](https://code.claude.com/docs/en/cli-reference) - launch, resume, streaming, settings, permissions, remote control, and output/input formats.
- [Settings](https://code.claude.com/docs/en/settings) - settings sources, precedence, and hook configuration.
- [Headless mode](https://code.claude.com/docs/en/headless) - print/stream-JSON behavior; verify the current canonical URL if redirected.
- Claude Code release notes and local `claude --help`/subcommand help - reconcile docs against installed behavior.

Record retrieval date, URL, relevant version, and whether each statement is documented, observed, inferred, or contradicted.

## Local Baseline

- `claude --version` -> `2.1.210 (Claude Code)` at packet preparation.
- `claude --help` advertises `--bg`, `agents`, `--resume`, `--continue`, `--fork-session`, `--session-id`, `--name`, `--input-format stream-json`, `--output-format stream-json`, `--include-hook-events`, `--replay-user-messages`, `--remote-control`, permission modes, per-session `--settings`, and worktrees.
- `claude agents --help` advertises JSON discovery plus model, effort, permission, settings, MCP, plugin, and cwd controls for dispatched sessions.
- `zmx version` -> `0.6.0`; `zmx help` documents persistent sessions, raw fire-and-forget PTY input, history, tail, wait, detach, and kill.
- `dispatch --version` -> `0.10.0`; current control operations remain Codex App Server-backed.

## Required Baseline Commands

```bash
git status --short --branch
git rev-parse HEAD
dispatch --version
dispatch doctor --json
claude --version
claude --help
claude agents --help
claude agents --json
zmx version
zmx list --short
zmx help
```

Do not paste sensitive or unbounded command output into tracked artifacts. Sanitize fixtures mechanically and record hashes/shapes where full content is unnecessary.

## Required Research Artifacts

- `docs/research/claude-control-plane-verification.md`
- `docs/development/claude-provider-plan.md`
- `spikes/claude/README.md` and minimal reproducible probes/fixtures
- Relevant ADR additions/updates
- `.agents/goals/2026-07-15-claude-control-plane-research/RETRO.md`
- Local-review reports under the packet's gitignored `tmp/reviews/`

## Verification Commands

```bash
/Users/mg/.agents/skills/goal-loop/scripts/check-goal-prompt --no-placeholders \
  .agents/goals/2026-07-15-claude-control-plane-research/PROMPT.md
/Users/mg/.agents/skills/goal-loop/scripts/goal-loop-doctor \
  .agents/goals/2026-07-15-claude-control-plane-research
just check
git diff --check
```
