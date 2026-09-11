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
