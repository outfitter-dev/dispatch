# Spec: search-baselines-turso

Date: 2026-07-02
Status: active

## Objective

After releasing Dispatch `0.8.1`, land the next local-substrate loop:
structured local history search, synthetic ingestion baselines, and a
Turso/libSQL decision memo.

## Context

The previous stack landed the local substrate roadmap, SQL compatibility
contract, and synthetic event-ingestion harness. The next product pressure is
making captured history useful without prematurely switching storage engines or
embedding raw transcripts.

## Scope

- `DIS-23`: semantic/local history search substrate and retention policy.
- `DIS-26`: repeatable SQLite registry ingestion baseline profiles.
- `DIS-27`: Turso/libSQL decision memo grounded in local search and baseline
  evidence.
- Docs, tests, CLI/MCP schema/help updates derived from existing contracts.

## Non-Goals

- No paid embedding/API calls.
- No real transcript embedding.
- No cloud credentials, remote Turso databases, or default backend migration.
- No multi-machine sync implementation (`DIS-24` remains separate).
- No live/private user data in benchmarks or fixtures.

## Acceptance Criteria

- Local search uses normalized registry history or derived artifacts, not broad
  App Server search, when explicitly requested.
- Search retention policy is documented: derived artifacts first, raw logs and
  transcripts excluded by default.
- At least three synthetic ingestion baseline profiles are recorded.
- Turso/libSQL recommendation is explicit, evidence-based, and tracked.
- Each milestone gets focused verification and local review.
- PRs are merged and local `main` is clean.
