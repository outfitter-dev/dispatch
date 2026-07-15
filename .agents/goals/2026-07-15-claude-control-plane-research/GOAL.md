# Goal Execution Contract: Claude Control-Plane Research

Date: 2026-07-15
Status: Active
Spec: `.agents/goals/2026-07-15-claude-control-plane-research/SPEC.md`
Prompt: `.agents/goals/2026-07-15-claude-control-plane-research/PROMPT.md`
Retro: `.agents/goals/2026-07-15-claude-control-plane-research/RETRO.md`
Refs: `.agents/goals/2026-07-15-claude-control-plane-research/REFS.md`

## Completion Horizon

`ready-pr`

Complete when:

- One research/decision PR contains the verified capability matrix, reproducible probe evidence, implementation-ready Claude provider plan, justified ADR updates/additions, and current tracker decomposition.
- The matrix covers every operation and lifecycle question with `verified`, `unsupported`, `product-decision`, or `blocked`; each blocked row names the exact blocker and next experiment.
- The disposable walking skeleton and failure/recovery sequences are completed, or a hard provider/tool blocker is proven through at least three materially different approaches and converted into a concrete decision path.
- Transport/protocol and security/product local reviews are 5/5 with zero open P0/P1/P2 findings; `just check`, docs/fixture checks, hosted CI, and review threads are clean; the PR is non-draft.
- `RETRO.md` carries the source/version ledger, experiment ledger, cleanup audit, confidence/contradiction log, review reports, Linear state, and final recommended milestone sequence.

Not complete when:

- The work only summarizes documentation, proves one happy-path prompt, or reports zmx/CLI exit success without hook-confirmed acceptance.
- Any operation remains an unlabeled unknown, semantics are inferred from Codex, or Agent SDK behavior is substituted for direct CLI evidence.
- The plan says “provider adapter” without specifying interfaces, capability negotiation, process ownership, receipt correlation, storage changes, security boundaries, tests, and ordered milestones.
- Disposable processes/sessions remain orphaned, live configuration changed, evidence contains secrets/raw transcripts, or tracker/docs disagree.

## Authority

- May browse/search: yes, prioritize official Claude documentation and primary zmx sources; record retrieval date and installed versions.
- May run local probes: yes, only disposable Claude sessions in temporary repositories, using the cheapest suitable model, minimal turns, bounded budgets where supported, and isolated per-session settings.
- May inspect existing sessions: metadata-only and read-only; do not send, interrupt, attach interactively, rename, or mutate them.
- May write: goal packet, `docs/research/`, `docs/development/`, relevant ADRs, `spikes/claude/`, sanitized fixtures/tests for probes, and scoped docs/skill corrections discovered by the research.
- May commit/push/open PR: yes, continue `docs/claude-control-plane-research`; submit one draft PR and mark ready only after gates pass.
- May mutate Linear: yes, scoped to DIS-9, DIS-49, directly related Claude-control issues, and dependency links/comments supported by evidence.
- May merge/release/publish: no.
- Needs user approval for: global/project Claude settings changes, messaging existing sessions, installing/upgrading zmx or Claude, private endpoints, auth/keychain access, production implementation, or expanded remote/gateway scope.

## Boundary

- In scope: direct Claude CLI/Agent View/session behavior, zmx transport, hooks/events/receipts, operation semantics, process supervision, provider-neutral integration plan, security/privacy, fixtures, docs, ADRs, tracker decomposition.
- Out of scope: production adapter implementation, public CLI provider flags, database migrations, mesh/SSH implementation, Slack/Linear gateway implementation, Claude SDK as the chosen runtime, release/publish.
- Do not touch: existing session input/state, `~/.claude` or project settings, auth files, tokens, keychain/cookies, raw user transcripts, unrelated worktrees/branches/issues.

## Topology

Delegated research goal with one primary synthesizer. Bounded subagents may independently audit official surfaces, current Dispatch seams, zmx/process behavior, hooks/security, or review artifacts. The primary agent owns experiments, conclusions, edits, tracker mutations, source control, and contradictions.

Callback: use the coordinator thread ID from `PROMPT.md` with `codex_app.send_message_to_thread`. Ping only for ready pickup, coordinator-needed, user-needed, blocked-with-evidence, surprising findings, or material scope/risk changes. Final callbacks must include the capability verdict, walking-skeleton result, artifacts, checks, open decisions, and next implementation slice.

## Steps

### 1. Baseline and question ledger

- Record repo commit, Dispatch/Claude/zmx versions, current official docs, installed CLI help/schema, current DIS-9/DIS-48/DIS-49 state, and current Dispatch provider architecture.
- Create the capability/unknown ledger before experimenting. Every later observation must resolve or refine a named row.
- Gate: `RETRO.md` has a baseline and cleanup inventory; no live state mutation occurred.

### 2. Supported surface verification

- Verify official semantics and local behavior for Agent View/background agents, `--resume`, `--continue`, `--fork-session`, `--session-id`, names, worktrees, permission modes, stream-JSON input/output, hook-event streaming, remote control, settings precedence, and hooks.
- Record exact JSON/event schemas as sanitized fixtures where stable and useful. Separate documented guarantees from observed implementation details.
- Gate: source/version matrix reviewed for omissions and contradictions.

### 3. Session lifecycle and identity experiments

- In a temporary repository, create named disposable sessions through each plausible launch path. Determine when the session ID becomes known, how it maps to Agent View, and whether it survives detach, resume, process exit, and a new shell invocation.
- Test `--resume <id>`, `--continue`, explicit `--session-id`, fork behavior, background agents, and any supported attach/control path. Do not touch pre-existing sessions.
- Gate: lifecycle state machine and identity mapping are evidence-backed; cleanup ledger is empty.

### 4. Message transport experiments

- Compare direct background/Agent View control, interactive PTY, stream-JSON, remote control, resume-based delivery, and zmx-backed interactive sessions.
- Prove cross-process input framing, acceptance, completion, repeated turns, backpressure, terminal behavior, and concurrent-writer handling. Use Dispatch message IDs and per-session hooks for correlation where supported.
- Gate: walking skeleton launches a disposable session, sends from another process, proves provider acceptance and completion, interrupts safely, resumes/attaches, and completes a second message, or records a hard supported-surface blocker.

### 5. Hooks, receipts, and attention

- Exercise supported lifecycle, prompt, tool, permission, notification, elicitation/user-input, stop, subagent, and session hooks with per-session settings.
- Establish event ordering and map events to `provider_events`, `message_receipts`, `lane_runtime_state`, queue-drain readiness, inbox/attention, and completion.
- Prove hooks compose with existing settings without global mutation and define failure behavior, timeouts, output limits, and spoof-resistant correlation.
- Gate: sequence diagrams and sanitized fixtures support the receipt/attention contract; scrollback is diagnostic only.

### 6. Operation capability matrix

- Resolve `new`, `attach`, `send`, `steer`, `queue`, `interject`, `context`, `stop`, `tail`, `watch`, `rename`, `archive`, `restore`, `goal`, permissions, structured output, and rich input.
- For each, name the Claude primitive, Dispatch composition, preconditions, receipt, failure modes, recovery, confidence, and whether the operation is unsupported or needs a product decision.
- Gate: two reviewers agree there are no false semantic equivalences or naked unknowns.

### 7. Failure, restart, and security matrix

- Test process death, zmx/transport loss, duplicate/retried messages, simultaneous sends, interrupt races, permission waits, user-input waits, stale identity, malformed hooks, hook timeout/failure, and Dispatch daemon restart.
- Threat-model PTY/control-byte injection, shell quoting, environment inheritance, settings precedence, sender spoofing, raw logs/transcripts, and cross-session routing.
- Gate: each failure has deterministic ownership, visible state, retry/idempotency rule, and cleanup/recovery path.

### 8. Implementation plan and tracker decomposition

- Produce `docs/development/claude-provider-plan.md` with provider interfaces, capability negotiation, session supervisor, transport choice, hooks/settings strategy, event/receipt storage, selector/routing changes, config/presets, CLI/MCP projection, migration needs, fixtures, rollout, and ordered milestones.
- Produce/update research evidence and ADRs. Reconcile DIS-9 and DIS-49, then create focused implementation issues with dependencies and acceptance criteria. The first slice must be the smallest end-to-end walking skeleton, not an abstraction-only PR.
- Gate: a fresh engineer can implement the sequence without reconstructing research or guessing semantics.

### 9. Review and ready-PR closure

- Run transport/protocol and security/product local-review loops. Fix P0-P2 and reasonable P3 findings, rerun affected experiments/checks, then run a full-stack research review.
- Run `just check`, packet doctor, prompt checker, docs/fixture validation, hosted CI, and thread reconciliation. Mark ready only when all evidence and tracker state agree.

## Reviews

- Run independent transport/protocol and security/product review loops after each material milestone.
- Store structured review reports under `tmp/reviews/`; fix all P0-P2 and reasonable P3 findings before advancing.
- Finish with a full-stack research review at 5/5 and zero open P0-P2 findings.

## Evidence Contract

Required durable artifacts:

- `docs/research/claude-control-plane-verification.md`: source/version ledger, capability matrix, experiment methods/results, contradictions, confidence, sanitized schemas, and unsupported findings.
- `docs/development/claude-provider-plan.md`: implementation architecture, operation mapping, milestones, risks, migration/testing/rollout, and explicit non-goals.
- `spikes/claude/README.md` plus the smallest reproducible scripts/fixtures needed to rerun claims; no raw transcripts, credentials, giant logs, or machine-specific paths.
- Relevant ADR additions/updates when a transport, lifecycle, receipt, or provider-boundary decision is justified.
- `RETRO.md`: chronological experiments, commands, costs where available, cleanup, review reports, tracker/PR state, final proof, and unresolved product decisions.
- Linear: evidence comments on DIS-9/DIS-49 and focused implementation issues with dependencies.

## Verification

- `/Users/mg/.agents/skills/goal-loop/scripts/check-goal-prompt --no-placeholders PROMPT.md`
- `/Users/mg/.agents/skills/goal-loop/scripts/goal-loop-doctor <packet>`
- `just check`
- Reproducible probe commands documented and rerun from a clean temporary directory.
- Hash or metadata checks prove live `~/.claude` settings were unchanged without printing their contents.
- Before/after process, Agent View, zmx, temp-directory, and session cleanup inventories show no disposable leftovers.
- Official-source links include retrieval date; local claims include tool version and command.
- Local review JSON reports under `tmp/reviews/transport/`, `tmp/reviews/security-product/`, and `tmp/reviews/full-stack/`.
- PR CI/review threads are green and resolved before ready.

## Next Move

- When docs and runtime disagree, reproduce on the installed version, search current official documentation/release notes, record both, and lower confidence rather than choosing silently.
- When one transport fails, test a materially different supported path before calling the capability blocked.
- When an operation has no safe Claude equivalent, mark it unsupported or product-decision and continue the rest of the matrix.
- When a probe exposes sensitive data, stop that probe, remove the artifact, record the privacy failure without the value, and redesign the fixture.
- After three materially different failed approaches to a required walking-skeleton step, document the blocker and implementation consequence, then continue independent research.

## Stop Rules

Stop only when:

- Continuing requires credentials/auth-file access, private endpoints, global/project settings mutation, messaging existing sessions, or installing/upgrading tools without approval.
- Required Claude functionality cannot be exercised because authentication/service access is unavailable after bounded retries and no isolated alternative exists.
- A security/privacy incident cannot be contained locally.
- The same hard blocker survives three materially different supported approaches and prevents the implementation plan from being honest; callback with exact evidence and a recommended decision.

Do not stop merely because one operation is unsupported, a model call fails, documentation is incomplete, or an experiment needs redesign.

## Waiting State

No routine external waiting is expected. For transient model/service limits, wait once with a bounded retry and then switch to documentation, fixture, code-seam, or non-model experiments. Do not burn tokens polling. CI/review waits should be checked only on meaningful state changes.

## Persistence And Resume

- Update `RETRO.md` after every milestone, surprising finding, failed approach, tracker mutation, and review round.
- Keep raw or machine-specific experiment output under gitignored `tmp/`; promote only sanitized evidence.
- Resume by reading `GOAL.md`, the latest `RETRO.md` entry, the capability matrix, current git/PR state, and DIS-9/DIS-49 comments.
- Continue until the completion horizon or a stop rule is satisfied; a context boundary is not a reason to stop.
