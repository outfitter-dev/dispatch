# Stations and providers execution ledger

## September 11, 2026 — project and design phase

- User authorized #Dispatch to drive shared work ahead of Hermes and organize Linear project/tasks/dependencies/documentation with a portable handoff.
- Verified source `8ad072c`, no open PRs, and existing worktree ownership. Created a dedicated foundation worktree; preserved the Hermes setup checkout and all existing local state.
- Created Dispatch Stations and Providers with five milestones and DIS-78–93. Reused DIS-24 and DIS-70. DIS-50 retains its Claude project and original transport prerequisites while consuming shared foundation issues.
- Published the architecture and complete portable Hermes handoff as project documents. The handoff explicitly permits DIS-84 investigation but keeps DIS-85 blocked pending verified foundation code.
- Recorded ADR-0028 and this packet. Verification, review and PR evidence are pending below; no provider/model calls, runtime replacement, merge, release or deployment performed.

### Verification and review

- `just check` passed: Ruff lint/format, strict mypy (175 source files), 1,086 tests passed / 17 live tests deselected in 24.44 seconds, wheel/sdist build and package-content verification. No live provider turn was needed for this documentation-only phase.
- All 11 changed/new documents and 68 local references passed; `git diff --check` passed.
- Native Linear read-back verified all 18 project issues and three linked documents. The dependency graph is acyclic and preserves DIS-50's original Claude prerequisites. DIS-84 is unblocked; DIS-85 remains blocked on actual foundation code.
- Dependency review narrowed DIS-81/82 to concrete Codex-backed seams, separated downstream native fault proof, moved DIS-83 to later integration grouping, and made native dependencies authoritative over milestone order. DIS-89 can follow the shared contract independently of DIS-24; DIS-90 requires independent startup for outage discovery.
- Targeted repository documentation review passed at 5/5 with no open P0/P1/P2 after resolving three P2 findings: Claude binding/migration ownership, public identity/selector compatibility, and the superseded gateway contract. Current Linear architecture/identity issue/handoff were updated to match. DIS-70 may begin; this is a documentation gate, not provider runtime readiness.

## September 11, 2026 — execution-bound schema compatibility

DIS-78 is draft PR #111 at `8726aeb01cf8ded19a9129c203efa09da06b75d0`; hosted CI and CodeQL passed. Work resumed after a machine restart by verifying the clean foundation checkout, current branch, native Linear state and PR checks. Existing worktrees were preserved and the interrupted worker was replaced without duplicating work.

DIS-70 adds protocol version 2's reserved checked-execution envelope and receiving-daemon schema validation. CLI and MCP share compatibility policy. Proven legacy ops retain raw execution only after metadata admission on the same established socket; provider-bearing and other schema-sensitive ops require checked execution. Dropped connections do not trigger a raw resend. The daemon retains raw compatibility for older clients, which need an upgrade to obtain the new guarantee.

### Verification and review

- RED: receiver tests first failed because the checked-execution method was absent. A CLI request tracer then demonstrated the behavioral gap by observing raw `new-plan` instead of the checked envelope.
- GREEN: receiver tests passed (11); `uv run pytest tests/daemon tests/surfaces tests/contracts -q` passed (158) after compatibility and socket coverage was added.
- Final `just check` passed after the last source edit: Ruff lint/format, strict mypy (176 source/test files), 1,101 tests passed / 17 live tests deselected, wheel/sdist build and package-content checks.
- Real Unix-socket tests cover the same-connection legacy admission rule, a newer preflight followed by an older receiver, a receiving-daemon schema mismatch, and dropped connections without unsafe raw replay. Rejected envelopes cannot create a lane; CLI/MCP return actionable typed errors.
- Both updated operator/protocol documents passed fence and 11 local-link checks; `git diff --check` passed.
- Fresh targeted implementation review passed at 5/5 with no findings. The reviewer independently reran the 158-test daemon/surface/contract suite, a 49-test focused suite, scoped Ruff, strict mypy on changed source files and diff checks. No provider model call, registry migration, installed-runtime replacement, merge or release was performed.

DIS-79–82 remain unimplemented. DIS-84's isolated native Hermes API investigation may start; DIS-85 adapter integration remains blocked on real foundation and API proof. The Linear handoff must retain that distinction when PR evidence is added.

## September 12, 2026 — provider and binding identity

Matt expanded the assignment through the shared foundation and native Hermes implementation, including direct default-profile research. DIS-79 extends the reviewed `904386c` stack with schema v24. It scopes native sessions, history, topology, events, normalized receipts, runtime state and server requests by provider and binding, while preserving Dispatch lane keys and refs. Default Codex keeps `provider_session_id == id`; other bindings remain non-executable in this migration slice.

### Verification and review

- Added collision and no-fallback regressions, an exact populated v23 schema fixture, deterministic failed-migration rollback, and transaction recovery tests.
- Migration preserves child foreign keys, durable local row IDs and SQLite sequence high-water marks, including previously deleted rows. An independent probe with the prior sequence at 42 confirmed the next ID is 43 and `PRAGMA foreign_key_check` is clean.
- Review found and resolved provider leaks in read/recovery paths, native server-request lookup collisions, transaction cleanup on identity conflict, default-Codex identity reassignment, sequence reuse, and stale public identity documentation. The full client routing extraction remains DIS-80.
- Final implementer and independent reviewer `just check` runs passed Ruff lint/format, strict mypy, 1,115 tests / 17 live tests deselected, wheel/sdist build and package-content validation. `git diff --check` passed.
- Fresh targeted review passed at 5/5 with zero open P0/P1/P2. The reviewed source/doc diff fingerprint was `2dea8b8df01191053a93212f6c512f1faaba3ee0b2aa4293c68aecef56ca8861`; this ledger entry adds the resulting evidence.
- The migration was tested only on isolated state. It was not run on the installed Dispatch registry. No installed runtime replacement, merge, release or deployment was performed.

DIS-80 may begin from this reviewed slice. The separately researched Hermes transport remains subject to DIS-81/82 reservation/observation and adapter integration gates; its successful native probes do not bypass them.

## September 12, 2026 — native Hermes transport research

DIS-79 is draft PR #113 at `0390cbbc4d0842d79ad75b2825e8224680f8d242`, with exact-head CI passing. DIS-80 implementation continues in the foundation worktree while the independently reviewed DIS-84 research is recorded in this documentation slice.

The native HTTP Runs probe passed keyed replay/conflict, two-turn continuity, canonical history, cancellation and polling. It also proved an unsuitable coding-workspace boundary and a failed warm Desktop/API context sequence. The selected initial coding transport is the maintained native TUI gateway over owned stdio, using dedicated sessions and the explicitly authorized default profile.

### Verification and review

- Native stdio proved two coherent turns, actual terminal execution in the selected cwd, and clean process restart with a new runtime ID while retaining the stored session key, context and cwd. Both owned gateway processes exited normally after the synthetic sessions were idle. The portable evidence summary and limits are in the [provider contract](../../../docs/research/hermes-native-provider-contract.md).
- Three research collaborators, including a dedicated native Hermes Desktop conversation, inspected the installed source at `939e45c91d751fadd94dcd1b873ac3cb44846213`. No installed source, shared configuration, credentials or existing user conversation was edited. Native agent invocation can have provider-owned profile effects, which the report discloses.
- Hermes's canonical runner passed `tests/tui_gateway/test_auto_continue.py` with 20 tests in 2.9 seconds, using its clean environment and isolated per-test homes. This supports the documented native recovery behavior; it does not certify a Dispatch adapter.
- Independent research review reached 5/5 with zero open findings after correcting stale-marker resume safety, lazy-watch successor/runtime limits and interpreter provenance. The contract also records unpaginated history fallback and the need for unique attribution when native autonomous turns exist.
- The initial adapter must preserve unknown outcomes without retransmission and quarantine stored sessions after an owner generation change until supported safe resume exists. This concrete upstream limit does not prevent the correctly bounded same-generation implementation after DIS-80/81/82 pass.

No merge, release, installed Dispatch registry migration, runtime replacement or service deployment occurred. The owned native processes were temporary proof clients using the existing default profile; existing Desktop and API services remained running.
