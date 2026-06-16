# Dispatch Codex Plugin

This workspace-local plugin exposes:

- [`../../skills/dispatch/SKILL.md`](../../skills/dispatch/SKILL.md) - operator guidance.
- [`../../skills/dm/SKILL.md`](../../skills/dm/SKILL.md) - dispatch-backed direct messages.
- [`.mcp.json`](.mcp.json) - the `dispatch` MCP server, launched with
  `dispatch mcp`.

The MCP server and skills expose the same derived operation registry as the CLI,
including managed-thread creation/messaging, dispatch refs, persisted `tail`,
bounded live `watch`, native goals, triggers, schemas, model catalog reads, and
daemon status/log reads.
`new --goal` creates native App Server goal state; `/goal ...` in message text is
plain text and should not be used as a goal substitute. `new` also accepts launch
packets (`--packet DIR`), file/stdin inputs (`--goal-file`/`--input-file`/
`--output-schema-file`, `-` for stdin), a mutation-free `--dry-run`, and durable
staging (`--stage all|<parts>` → `.agents/sessions/<ref>/`).
Use `dispatch models` or the MCP daemon-read `models` op before pinning explicit
model/service-tier presets.

Run `dispatch doctor` after installing or upgrading dispatch. It verifies the CLI
entrypoints, Codex CLI/auth footprint, daemon socket/pidfile state, registry
schema/integrity, packaged skills/plugin assets, and a low-risk App Server
initialize smoke. Use `dispatch doctor --no-app-server` when you only want local
install checks. If doctor reports an old registry schema, run `dispatch down`,
`dispatch registry migrate`, then `dispatch up`.

Run `dispatch up --json` before MCP tool calls that need the daemon. `dispatch mcp`
serves the derived tools over stdio; the daemon remains the executor.

`skills` is a symlink to the repo-root [`../../skills`](../../skills) tree so the plugin
and standalone skill docs cannot drift.

PyPI installs include read-only packaged copies of the skills and plugin under
`outfitter.dispatch.assets`; edit the repo-root assets, not the installed copies.

Codex discovers the plugin through
[`../../.agents/plugins/marketplace.json`](../../.agents/plugins/marketplace.json).
Restart Codex if the plugin does not appear immediately.
