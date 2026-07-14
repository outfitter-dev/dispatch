# Goal References: Provider Capacity Completion

## Repo Guidance

- `AGENTS.md` - contract-first architecture, testing, Graphite, privacy, and tracker rules.
- `docs/development/design.md` - approved provider-neutral and derived-surface design.
- `.agents/plans/v0/PLAN.md` - TDD, Graphite, review, and verification conventions.

## Tracker

- [DIS-34](https://linear.app/outfitter/issue/DIS-34/add-provider-account-and-capacity-inventory) - parent account/capacity objective.
- [DIS-36](https://linear.app/outfitter/issue/DIS-36/add-claude-account-and-runtime-probes) - first implementation milestone.
- [DIS-37](https://linear.app/outfitter/issue/DIS-37/capture-claude-capacity-from-statusline-snapshots) - second implementation milestone.
- [DIS-38](https://linear.app/outfitter/issue/DIS-38/store-normalized-provider-observations-for-mesh-heartbeats) - final model/tracker reconciliation milestone.
- [DIS-40](https://linear.app/outfitter/issue/DIS-40/spike-direct-claude-oauth-usage-endpoint-only-if-statusline-is) - explicitly deferred unsupported-path spike.
- `Provider Account and Capacity Inventory` Linear document - supported surface research and privacy framing.

## Source Files

- `src/outfitter/dispatch/core/capacity.py` - Codex normalization, redaction, and merge patterns.
- `src/outfitter/dispatch/core/handlers.py` - `usage` refresh and rendering.
- `src/outfitter/dispatch/core/models.py` - authored usage input/output models.
- `src/outfitter/dispatch/registry/models.py` - generic provider observation model.
- `src/outfitter/dispatch/registry/store.py` - latest observation persistence and component queries.
- `src/outfitter/dispatch/config.py` - `DISPATCH_HOME` path boundary.
- `tests/core/test_capacity.py` - existing provider behavior and surface expectations.
- `tests/registry/test_store.py` - replace-in-place and no-secret persistence tests.

## Docs / ADRs / Notes

- `docs/adrs/0023-provider-event-log-and-history-index.md` - latest replace-in-place provider observation decision.
- `docs/adrs/0013-dispatch-mesh-is-daemon-federation.md` - selected snapshot transport and local sovereignty.
- `docs/usage/README.md` - current provider-neutral usage contract.
- `skills/dispatch/SKILL.md` - operator/agent usage guidance.

## PRs / Branches

- [PR #79](https://github.com/outfitter-dev/dispatch/pull/79) - shipped Codex capacity observations.
- [PR #80](https://github.com/outfitter-dev/dispatch/pull/80) - shipped derived provider inventory CLI/MCP/docs.
- `dis-36-add-claude-account-and-runtime-probes` - first stack branch.
- `dis-37-capture-claude-capacity-from-statusline-snapshots` - second stack branch.
- `dis-38-store-normalized-provider-observations-for-mesh-heartbeats` - third stack branch.

## Commands

- `uv run pytest tests/core/test_capacity.py tests/registry/test_store.py -q` - baseline provider/store contract.
- `uv run pytest tests/core/test_claude_capacity.py tests/core/test_capacity.py tests/registry/test_store.py -q` - focused implementation contract.
- `uv run pytest tests/surfaces/test_derive_cli.py tests/surfaces/test_derive_mcp.py tests/surfaces/test_mcp_routing.py tests/surfaces/test_parity.py -q` - derived-surface parity.
- `just check` - repository gate.
- `gt log --stack` and `gh pr checks` - stack and CI proof.

## Prompt

- `.agents/goals/2026-07-14-provider-capacity-completion/PROMPT.md` - direct-start execution contract.

## Review Reports

- `.agents/goals/2026-07-14-provider-capacity-completion/tmp/reviews/` - scratch standing/targeted JSON reports; load-bearing summaries are copied into `RETRO.md`.
