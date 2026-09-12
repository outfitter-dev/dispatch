# Dispatch Codex Plugin

This workspace-local plugin exposes:

- [`../../skills/dispatch/SKILL.md`](../../skills/dispatch/SKILL.md) - operator guidance.
- [`../../skills/dm/SKILL.md`](../../skills/dm/SKILL.md) - dispatch-backed direct messages.
- [`.mcp.json`](.mcp.json) - the `dispatch` MCP server, launched with
  `dispatch mcp`.

The MCP server and skills expose the same derived operation registry as the CLI,
including managed-thread creation/messaging, dispatch refs, persisted `tail`,
bounded live `watch`, native goals, triggers, schemas, model catalog reads, and
daemon status/log reads. The operator skill also documents the opt-in Hermes
provider, its owned stdio binding, keyed local receipts, generation fencing, and
unsupported controls.

The packaged operator references are available beside this plugin:

- [`../../docs/usage/README.md`](../../docs/usage/README.md) - CLI, config, and recovery guide.
- [`../../docs/usage/deliveries.md`](../../docs/usage/deliveries.md) - keyed receipt and reconciliation contract.
- [`../../docs/research/hermes-native-provider-contract.md`](../../docs/research/hermes-native-provider-contract.md) - native stdio boundary and evidence limits.
- [`../../docs/research/hermes-http-runs-contract.md`](../../docs/research/hermes-http-runs-contract.md) - explicitly API-managed alternative and limits.
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
initialize smoke. When `[providers.hermes]` is configured, it statically checks
the binding paths. It does not start Hermes or negotiate capabilities; after
`dispatch up`, use `dispatch daemon status --json` for readiness after capability
negotiation, supported-action, durability and generation diagnostics. Use `dispatch doctor
--no-app-server` when you only want local install checks. If doctor reports an old
registry schema, run `dispatch down`, `dispatch registry migrate`, then
`dispatch up`.

Hermes requires an explicitly configured local gateway and both
`prompt_submit_if_idle_v1` and `prompt_turn_correlation_v1`. The stock Hermes
`939e45c`/`0.21.2` runtime does not provide the correlation capability; Dispatch
must refuse durable Hermes sends there. The capability patch is a local,
unpublished dependency and this plugin does not install or replace Hermes.

Run `dispatch up --json` before MCP tool calls that need the daemon. `dispatch mcp`
serves the derived tools over stdio; the daemon remains the executor.

`skills` is a symlink to the repo-root [`../../skills`](../../skills) tree so the plugin
and standalone skill docs cannot drift.

PyPI installs include read-only packaged copies of the skills and plugin under
`outfitter.dispatch.assets`; edit the repo-root assets, not the installed copies.

Codex discovers the plugin through
[`../../.agents/plugins/marketplace.json`](../../.agents/plugins/marketplace.json).
Restart Codex if the plugin does not appear immediately.
