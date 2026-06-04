# Dispatch Codex Plugin

This workspace-local plugin exposes:

- [`../../skills/dispatch/SKILL.md`](../../skills/dispatch/SKILL.md) - operator guidance.
- [`../../skills/dm/SKILL.md`](../../skills/dm/SKILL.md) - dispatch-backed direct messages.
- [`.mcp.json`](.mcp.json) - the `dispatch` MCP server, launched with
  `uv --directory ../.. run dispatch mcp`.

The MCP server and skills expose the same derived operation registry as the CLI,
including lane creation/messaging, bounded watch, transcript snapshots, native goals,
history controls, triggers, and daemon status/log reads.

`skills` is a symlink to the repo-root [`../../skills`](../../skills) tree so the plugin
and standalone skill docs cannot drift.

Codex discovers the plugin through
[`../../.agents/plugins/marketplace.json`](../../.agents/plugins/marketplace.json).
Restart Codex if the plugin does not appear immediately.
