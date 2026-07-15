/goal From `/Users/mg/Developer/outfitter/dispatch`, execute `.agents/goals/2026-07-14-provider-capacity-completion` to `ready-pr`.

## Read First
- `AGENTS.md`, design/plan docs, then all five packet files.
- Live DIS-34/DIS-36-DIS-38, `gt log --stack`, PRs, worktree state.

## Objective
Finish DIS-34's Claude account/capacity foundation as a DIS-36/DIS-37/DIS-38 Graphite stack with supported probes, provider-neutral observations, derived surfaces, and tracker truth.

## Authority
- Commit, push, open draft PRs, mark ready after clean review/CI, and update DIS-34/DIS-36-DIS-38 with evidence.
- Do not merge, release, publish, call private Claude endpoints, edit live Claude config, or access auth files/secrets without approval.

## Boundary
- In: provider observations, Claude probes/statusline capture, `usage` CLI/MCP, tests/docs, scoped tracker state.
- Out: routing, mesh transport, private OAuth usage, provider frameworks, broad backlog cleanup.

## Sequence
1. Reconcile DIS-34 against PRs #79/#80 and ADR-0023; defer DIS-40.
2. DIS-36: bounded async auth/agents probes, aggregate runtime/freshness, independent `usage` refresh.
3. DIS-37: atomic capture beneath `DISPATCH_HOME`, merge-only capacity, missing/stale states, setup docs.
4. DIS-38: tests/docs/provenance/TTL and latest-only tracker reconciliation.

## Loop
For each milestone: tests first; smallest contract-first slice; no Claude subprocesses in `client/`; focused checks; standing and targeted `local-review` JSON under `tmp/reviews`; fix P0-P2 and reasonable P3 on the owning branch; `just check`; draft PR; CI; mark ready; update `RETRO.md`.

## Verification
- `uv run pytest tests/core/test_capacity.py tests/core/test_claude_capacity.py tests/registry/test_store.py -q`
- `uv run pytest tests/surfaces -q`
- `just check` per branch and after final restack.
- Manual `uv run dispatch usage --provider claude --json` plus temporary-home capture; prove sensitive data absent and `~/.claude` unchanged.
- Prove stack order, clean worktree, CI, and review-thread state.

## Hard Rules
- Preserve unrelated drift; use `uv` and repo tasks; author once and derive CLI/MCP.
- Persist only bounded normalized facts, never raw auth, roster, statusline, daemon, or credential payloads.
- One provider failure must not hide the other.
- Do not create speculative frameworks, history warehouses, or routing policy.

## Stop Rules
- Supported read-only surfaces are insufficient and private/auth-file access would be required.
- Privacy-safe normalization cannot satisfy acceptance.
- Unrelated work prevents isolation, or new merge/release/config authority is required.

## Definition Of Done
- DIS-36/DIS-37/DIS-38 PR stack is non-draft, CI-green, cleanly reviewed, with zero open P0/P1/P2.
- Tracker matches evidence; DIS-34 remains In Progress until merge.
- `RETRO.md` has checks, privacy/non-mutation proof, reviews, stack/PR state, and risks.

## Evidence Contract
- Record results, redacted smoke, reviews, tracker/stack/CI state, and risks in `RETRO.md` and PR bodies.
- Final transcript proves `ready-pr` and states merge/release/publish were not authorized.

## Next Move
After failures, narrow the repro; change approach after three repeats. Ask only when authority or scope must expand.

## Not Done
- Local green only, draft/open-review PRs, or DIS-36 complete while DIS-37/DIS-38 are left uncontracted.
- Tracker cleanup without implementation and verification.

## Persistence
Poll CI/review every 2-5 minutes only while waiting. Update `RETRO.md` before handoffs/branch moves. Resume from `RETRO.md`, Linear, stack, PR checks, and `git status`.

## Waiting State
- Waiting on: PR CI/review. Check with `gh pr checks` and review threads.
- Continue when green/clean. Stop when access fails or user authority is required.

Keep going until done unless a stop rule fires.
