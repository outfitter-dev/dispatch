/goal Verify Claude session control and deliver an implementation-ready Dispatch provider plan in a clean ready research PR, without production implementation.

## Objective

Turn every Claude control unknown into evidence, unsupported/product-decision, or a proven blocker. Leave ordered issues.

## Authority

Use $delegate-init.
Coordinator: [@Dispatch](codex://threads/019e8a09-5021-7b63-9d95-402b7c7d345e)
You: @Dispatch:ClaudeControlResearch
Title: `→ @Dispatch:ClaudeControlResearch Verify Claude Session Control`
Context: `Verify Claude Session Control`
Tracker: DIS-9, DIS-49, and directly supported implementation issues.
Continue `docs/claude-control-plane-research`; commit scoped work, open one draft PR, and mark ready after all gates. Do not merge, release, or publish.

## Boundary

Work in `/Users/mg/Developer/outfitter/dispatch`. Read `AGENTS.md` and the entire goal packet; `GOAL.md` is authoritative. Research official Claude CLI, Agent View, hooks, settings, streaming, remote control, zmx, and Dispatch seams. Use disposable temp-repo sessions, cheap models, minimal prompts, and per-session settings. Existing sessions are read-only metadata.

## Sequence

Ledger first; then surfaces, identity/lifecycle, transports, hooks/receipts, operations, failures/security, architecture, and tracker. Prove a persistent disposable-session skeleton: durable identity, cross-process send, hook-confirmed acceptance, completion, interrupt, resume/attach, and second message. Test transport loss, duplicate/concurrent sends, attention, hook failure, and cleanup.

## Loop

Per milestone: sources/code -> isolated experiments -> sanitized evidence -> matrix/RETRO -> transport and security/product reviews -> fix P0-P2 and reasonable P3 -> rerun. Finish with full-stack review, packet checks, `just check`, CI, resolved threads, tracker reconciliation, and 5/5 with zero P0-P2.

## Hard Rules

Never read auth/raw user transcripts; change global/project settings; message existing sessions; use private endpoints; or install/upgrade tools. Exit status and scrollback are not delivery proof. Do not infer Claude semantics from Codex or substitute Agent SDK evidence.

## Stop Rules

After three distinct supported approaches fail, record the blocker and continue independent work. Stop only for required approval/access, uncontainable security/privacy risk, or a blocker preventing an honest plan.

## Definition Of Done

Resolve every capability row in `RETRO.md`. Each needs its primitive/composition, preconditions, acceptance/completion receipts, failure/recovery, evidence/version/confidence, and one status: verified, unsupported, product-decision, or blocked. No naked unknowns or forced Codex parity.

## Evidence Contract

Deliver the research, provider plan, minimal `spikes/claude/`, ADR changes, current RETRO, and Linear issues required by `GOAL.md`. Settle provider boundaries, capabilities, supervision/transport, hooks/settings, receipts/storage, queue/attention/restart, security, CLI/MCP/config, migrations, fixtures, rollout, and the first walking-skeleton PR.

## Next Move

When docs and runtime differ, preserve both and lower confidence. Try distinct supported transports before blocking. Mark absent safe equivalents unsupported or product-decision and continue. Keep only sanitized reproducible evidence.

## Not Done

Do not implement the adapter, provider flags, migrations, mesh/gateway, or release. A summary, one happy path, unconfirmed zmx input, open P0-P2, draft PR, or orphaned state is not completion.

## Persistence

Update RETRO after every milestone, failed approach, surprise, tracker mutation, and review. Resume from packet, RETRO, matrix, git/PR, and tracker. Callback via the coordinator ID using `codex_app.send_message_to_thread`; do not discover your ID. Ping only for pickup, required input, evidenced blocker, surprise, or material risk. Final ping: verdict, skeleton, artifacts, checks, decisions, issues/PR, cleanup, and first slice.
