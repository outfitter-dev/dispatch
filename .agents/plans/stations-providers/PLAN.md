# Dispatch stations and providers — execution plan

The [Linear project](https://linear.app/outfitter/project/dispatch-stations-and-providers-27755da2589a) is the source of truth for intent, live status and native dependencies. [ADR-0028](../../../docs/adrs/0028-stations-own-provider-bindings-and-durable-execution.md) records the shared technical direction. This packet records sequencing and execution evidence, not a second status tracker.

## Current assignment

#Dispatch coordinates implementation through the shared foundation and the Hermes integration. On September 12, Matt expanded the assignment beyond DIS-78/DIS-70 to proceed as far as the evidence supports, including direct Hermes interaction on the local `default` profile. Use dedicated synthetic conversations for live proof; automated tests and destructive fault simulations remain isolated. The portable handoff must show remaining blockers until their implementation evidence exists.

Use the dedicated foundation worktree and preserve other worktree owners. One branch owns each shared registry/admission change. Existing Claude and provider-history projects keep their provider-specific scope. DIS-50 consumes the shared foundation rather than implementing another migration. DIS-24 retains its existing parent and anchors network design.

## Slices and dependencies

| Issue | Deliverable | Prerequisites |
| --- | --- | --- |
| [DIS-78](https://linear.app/outfitter/issue/DIS-78) | Shared contract, repository reconciliation and project/handoff documents | Current source/design review |
| [DIS-70](https://linear.app/outfitter/issue/DIS-70) | Schema validation bound to the actual execution request | Existing compatibility contract; independent of provider migration |
| [DIS-79](https://linear.app/outfitter/issue/DIS-79) | Binding-scoped registry identity and complete Codex backfill | DIS-78 |
| [DIS-80](https://linear.app/outfitter/issue/DIS-80) | Guarded provider routing and truthful capabilities | DIS-79 |
| [DIS-81](https://linear.app/outfitter/issue/DIS-81) | Minimum immutable prepared-request/reservation seam on Codex | DIS-80 |
| [DIS-82](https://linear.app/outfitter/issue/DIS-82) | Minimum shared observation/receipt seam on Codex | DIS-80 |
| [DIS-83](https://linear.app/outfitter/issue/DIS-83) | Independent provider startup/recovery | DIS-80, DIS-82 |
| [DIS-84](https://linear.app/outfitter/issue/DIS-84) | Native Hermes API proof on the authorized default profile | Can start independently; findings inform the shared design |
| [DIS-85](https://linear.app/outfitter/issue/DIS-85) | Hermes adapter and two coherent local turns | DIS-70, DIS-79, DIS-80, DIS-81, DIS-82, DIS-84 |
| [DIS-86](https://linear.app/outfitter/issue/DIS-86) | Hermes ambiguity/replay/attention recovery | DIS-85 |
| [DIS-87](https://linear.app/outfitter/issue/DIS-87) | Desktop A → Dispatch B → Desktop C coexistence | DIS-86 |
| [DIS-88](https://linear.app/outfitter/issue/DIS-88) | Hermes diagnostics, docs, skills and package proof | DIS-86; attached/Hermes-only claims also require DIS-87/DIS-83 |
| [DIS-24](https://linear.app/outfitter/issue/DIS-24) | Network design and explicit remaining decisions | Existing design anchor; not a transport implementation |
| [DIS-89](https://linear.app/outfitter/issue/DIS-89) | Real cloud auth plus tiny derived manifest/synthetic reads | DIS-78; related to DIS-24's network design |
| [DIS-90](https://linear.app/outfitter/issue/DIS-90) | Two isolated stations and truthful authorized directory | DIS-79, DIS-82, DIS-83, DIS-89 |
| [DIS-91](https://linear.app/outfitter/issue/DIS-91) | Cloud mailbox queued text and receipt/result recovery | DIS-70, DIS-81, DIS-82, DIS-90 |
| [DIS-92](https://linear.app/outfitter/issue/DIS-92) | Explicit private Tailscale delivery and duplicate-path proof | DIS-91 |
| [DIS-93](https://linear.app/outfitter/issue/DIS-93) | Restore, revocation, retention, privacy and rollout evidence | DIS-83, DIS-92 |

This is a dependency graph, not a requirement to work through every row serially. Milestones group outcomes and are not sequential blocking gates. DIS-84 and the independent compatibility fix can start without network infrastructure. Provider startup independence is required for standalone-provider availability and Codex-down directory claims, not for an explicitly alongside-Codex first slice.

DIS-81/82 extract the minimum request/evidence contract with actual Codex callers and preserve current guarantees. Hermes native creation, replay windows, crash recovery and attention are exercised in DIS-85/86; Claude-specific hook/generation/resolution proof remains in DIS-50/51/52. The first adapter must not wait for a generic recovery framework or a full network read model.

Matt is the native Linear assignee for the active foundation and initial Hermes issues. #Dispatch owns execution and source-control coordination through DIS-88, with bounded research, implementation and review workers. A dedicated Hermes Desktop conversation on the local default profile provides independent native-runtime research. Keep one source owner for each shared migration and one live writer per synthetic Hermes session.

## Current wave gates

### 1. DIS-78: make the shared work reviewable

- Create the Linear project, focused issues, native dependencies and linked architecture/handoff documents.
- Record the station/thread/binding identity and receipt/evidence boundaries in ADR-0028.
- Reconcile obsolete SSH and handle assumptions in proposed network docs. Link Claude's plan to shared ownership without changing its transport decisions.
- Verify all project resources and dependencies by read-back; inspect for cycles and false blockers.
- Run `just check` and document/link checks. Request a local review and resolve P0–P2 before starting the next code phase.

### 2. DIS-70: bind schema compatibility to execution

Reproduce the daemon-swap race before implementation. A checked request must reach a handler only after the same receiving server validates the expected per-op schema hash. An older server that cannot enforce this must reject the checked request; adding a field it ignores is insufficient.

Keep the change within the control protocol and CLI/MCP transport projection. Define the version/legacy policy explicitly, preserve safe baseline reads only with proof, and never fall back to unchecked provider-bearing writes. No database migration or provider runtime work belongs here.

Verify matching/mismatching/malformed hashes, unknown checked method, changed daemon between preflight and execution, zero handler calls on rejection, CLI/MCP error parity and existing compatibility behavior. Run the smallest relevant suites, then `just check`; request local review after green checks. Keep the PR draft and record hosted CI for its exact head.

### 3. DIS-79–88: complete the foundation and native Hermes path

Implement and review the binding migration before enabling provider routing. Extract the minimum prepared-request and observation seams with real Codex callers, then consume the verified Hermes contract in a small native API adapter. Keep independent startup as a focused slice and prove it before claiming Hermes-only availability.

In parallel with shared code, inspect the installed Hermes contract and use the authorized local default profile for synthetic live checks. Record version, profile, session/run IDs, replay scope and canonical history evidence. Alternate Desktop and API writers only after the preceding turn is demonstrably idle. An acceptance response or a closed event stream alone never proves execution.

After two coherent Dispatch turns, exercise replay/conflict, lost acknowledgment, unavailable runtime and attention boundaries, using isolated services for destructive fault simulations. Verify Desktop continuity and operator diagnostics, then run a fresh full-stack review. Continue through correctable findings; stop only at a concrete capability or authority boundary and preserve a precise pickup record.

## Hermes pickup contract

The [Linear handoff](https://linear.app/outfitter/document/hermes-agent-handoff-prerequisites-workflow-and-pickup-gates-5dd9c614285a) is the portable entry point. It must work without another agent's gitignored files.

Before marking DIS-85 ready to start, the coordinator records:

1. Exact reviewed foundation base commit/PRs and whether the agent consumes merged main or an explicitly approved stack base.
2. DIS-70 and DIS-79–82 verification, migration version/coverage and no unresolved correctness/review blockers.
3. DIS-84's actual runtime/profile/API proof and unsupported cases.
4. Known Codex startup dependency and provider capability limits.
5. Worktree/branch ownership and the next concrete implementation step.

A completed document, a passing transport probe or a green unrelated revision does not pass this gate. Read live issue state and evidence. If the foundation is not ready, the Hermes agent may investigate DIS-84 but must not duplicate migrations or start live integration.

## Verification and authority

Use repository tasks and `uv`; new behavior follows TDD. Full gates include lint, format, strict types, unit/examples tests and package contents. Automated integration/scenarios use temporary Dispatch/provider homes and synthetic state. Matt separately authorized live interaction on Hermes's local default profile for this task; use dedicated synthetic sessions and retain their evidence. Documentation-only changes do not need a new live provider turn.

Each phase is a Graphite branch. A local reviewer must score at least 4/5 with no open P0/P1/P2 before the next phase. Record commands, revision, review and unresolved limits in [RETRO.md](RETRO.md). Keep source-control mutations with the coordinator. PRs remain draft until the applicable current-head checks and readiness authorization are satisfied. Merge, publication, installed-runtime changes and public deployment are separate actions.

Future network work must resolve actual client authentication, delegated authority, gateway trust and retention before remote writes. A cloud caller is not a station unless a runtime adapter registers it. Local Hermes start/stop/approval support does not enlarge the initial remote allowlist.
