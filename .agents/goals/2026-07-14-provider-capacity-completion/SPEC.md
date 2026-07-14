# Goal Spec: Provider Capacity Completion

Date: 2026-07-14
Status: Active

## Objective

Complete the remaining provider account and capacity foundation under DIS-34 by adding safe Claude account/runtime probes, supported statusline capacity capture, and an evidence-backed reconciliation of the provider-neutral observation model and tracker contract.

## Context

Codex account and capacity observations plus the derived `dispatch usage` CLI/MCP surface shipped in PRs #79 and #80. The remaining work is Claude support. The current registry already stores one provider-neutral latest observation per provider, host, and config scope; DIS-38 predates the later ADR-0023 decision to replace observations in place and therefore needs reconciliation before its older trend-history wording is treated as implementation scope.

## Scope

### In

- DIS-36: bounded read-only probes for `claude auth status --json`, `claude agents --json`, and conservative daemon health/version facts when stable.
- Minimal provider-neutral model additions required for Claude identity, CLI version, aggregate runtime state, and component freshness.
- DIS-37: a non-destructive Claude statusline snapshot capture/reader path under `DISPATCH_HOME`, merged without erasing account/runtime observations.
- DIS-38: verify the shipped latest-observation store, TTL/provenance/privacy semantics, and reconcile Linear/docs with ADR-0023.
- The existing authored `usage` op, derived CLI/MCP surfaces, docs, fixtures, and tests.
- Evidence-backed Linear state/comments for DIS-34 and DIS-36 through DIS-38.

### Out

- Capacity-based routing or scheduling policy.
- Multi-machine transport or mesh sync implementation.
- The private Claude `/api/oauth/usage` endpoint tracked by DIS-40.
- A general provider plugin framework or changes to the Codex-only `client/` layer.
- Automatic edits to the user's Claude configuration or statusline settings.
- Broad cleanup of unrelated stale Dispatch issues.

## Source Of Truth

- `AGENTS.md` - repository workflow and architecture rules.
- `docs/development/design.md` - approved contract-first architecture.
- `docs/adrs/0023-provider-event-log-and-history-index.md` - replace-in-place observation decision.
- `src/outfitter/dispatch/core/capacity.py` - existing Codex normalization and merge behavior.
- `src/outfitter/dispatch/registry/models.py` and `registry/store.py` - provider-neutral observation contract and persistence.
- Linear DIS-34, DIS-36, DIS-37, and DIS-38 - tracker intent to reconcile against shipped behavior.

## Acceptance Criteria

- Claude auth and runtime probes are asynchronous, bounded, read-only, and explicit about missing CLI, timeout, signed-out, nonzero, malformed, and incompatible output states.
- Persisted and rendered observations contain no raw email, org id, cwd, session id, token, cookie, auth-file, keychain, or raw daemon-status data.
- Runtime persistence is aggregate and bounded; component timestamps preserve independent account, runtime, and capacity freshness.
- `dispatch usage` refreshes supported local Codex and Claude providers independently, so one provider failure does not hide the other.
- Statusline capture accepts supported JSON, writes only beneath `DISPATCH_HOME`, is atomic and bounded, and never modifies `~/.claude`.
- Claude statusline windows merge into the existing observation without erasing account/runtime facts; missing `rate_limits` is represented explicitly.
- Existing CLI/MCP derivation remains contract-first; no per-provider public op proliferation is introduced.
- DIS-38 is reconciled to the latest-observation decision unless new measured product pressure justifies bounded history.
- Focused tests and `just check` pass; manual smoke proves privacy and non-mutation on a machine with Claude installed.

## Decisions

- Use a Graphite milestone stack with one issue-owned branch/PR for DIS-36, DIS-37, and DIS-38.
- Keep Claude subprocess handling out of `client/`; use a focused provider module and injected bounded async command runner.
- Reuse the existing `usage` op and generic registry store.
- Prefer latest replace-in-place observations over a speculative trend warehouse.
- Completion horizon is `ready-pr`; merge, release, and publish require separate user authority.

## Risks

- Claude CLI JSON shapes may drift; fixtures and explicit incompatible states must prevent silent misclassification.
- Whole-observation upserts can lose account/runtime or capacity components unless all writers use merge semantics.
- Runtime commands expose sensitive paths and session metadata; normalization must aggregate before persistence or logs.
- A statusline capture entry point could accidentally imply configuration authority; docs must make setup explicit and manual.
