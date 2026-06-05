# Lazy Thread Sync — goal prompt

```text
/goal From `/Users/mg/Developer/outfitter/dispatch`, implement lazy sync for existing Codex threads so pickup is instant and honest.

Read first: `AGENTS.md`, `.agents/plans/PLANNING.md`, `.agents/plans/lazy-thread-sync/PLAN.md`, `.agents/plans/lazy-thread-sync/REFS.md`, `.agents/plans/lazy-thread-sync/RETRO.md`, `docs/adrs/0005-lane-authority-capability-ladder.md`, `docs/adrs/0011-codex-session-registration-is-explicit.md`, and current attach/discover/sync-adjacent code/tests. Verify live branch/state before editing.

Objective: ship metadata-only `dispatch lane attach <thread-id>` plus explicit progressive `dispatch lane sync <lane>` using compact App Server metadata and bounded Codex JSONL top+tail indexing. Update registry state, CLI/MCP/schema projections, docs, skills, and tests. Preserve observe-only attached-lane authority.

Constraints: work on `feat/lazy-thread-sync` or create it from main if missing. Follow contract-first/no-drift patterns; add ops through the registry, not hand-written surface forks. Do not unlock attached-lane writes, copy whole transcripts by default, index all unattached threads by default, merge, publish, mutate releases, commit secrets, or disturb live Codex threads. If live Codex smoke is useful, use temp `DISPATCH_HOME`, read-only metadata/sync only, no sends/stops/renames/archives.

Loop: define success before each slice; make small reversible changes; run focused tests first; update `RETRO.md`; do local review; fix P0/P1/P2; repeat until the feature is correct and you would ship it. Use bounded subagents for parser/schema/docs/review scouting, but verify their claims and keep synthesis, edits, commits, PRs, and tracker/source-control writes centralized.

Verification: parser/index fixture tests; attach tests proving default attach does not call `thread/resume`; registry migration tests; CLI/schema/MCP parity tests for `lane sync`; docs/skills checks; safe local runtime smoke; final `just check`.

Source control: commit coherent slices with conventional messages. Submit draft PR(s) only after local checks/review are clean. Do not mark ready, merge, or publish without explicit user approval.

Stop if unsafe ambiguity, missing credentials, unauthorized external side effects, impossible verification, or the same blocker repeats 3 times.

Done only when metadata-only attach, explicit sync, sync state surfaces, docs/skills/schema parity, tests, safe local smoke, green `just check`, and local review with no unresolved P0/P1/P2 are all complete; `RETRO.md` must record changed files, checks, review rounds, risks, forbidden-action audit, PR/branch state, and exact proof the completion condition is satisfied or blocked.
```

