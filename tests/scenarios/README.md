# Live Scenario Fixtures

Scenario fixtures exercise Dispatch as an agent-facing product: they start a real
daemon, create live Codex threads through the public CLI, wait for work to
complete, and assert observable Dispatch state.

These are not part of `just check`. They use real Codex auth/model calls, so run
them intentionally:

```bash
just scenario -- tests/scenarios/basic_coordination.toml
just scenario -- tests/scenarios/interactive_requests.toml
```

The runner creates temporary `DISPATCH_HOME`, `CODEX_HOME`, and work directories
under `/tmp`. It copies `~/.codex/auth.json` into the temporary `CODEX_HOME`,
starts `dispatchd`, and removes all temporary state afterward unless
`--keep-home` is passed.

Keep scenarios:

- small and deterministic;
- synthetic, with no private thread content;
- cheap (`effort = "low"` and a small preferred model when available);
- focused on user/agent workflows rather than one-off protocol details.
