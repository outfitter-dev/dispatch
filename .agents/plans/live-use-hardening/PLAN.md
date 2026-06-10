# Live-use Hardening - implementation plan

One-branch hardening packet for the real-use failures found during a Trails
delegation attempt. Goal loop: [`GOAL.md`](./GOAL.md). References:
[`REFS.md`](./REFS.md). Execution ledger: [`RETRO.md`](./RETRO.md).

## Objective

Make dispatch trustworthy when an agent uses it for real coordination:

- keep surface projection honest and guarded;
- make launch results distinguish "accepted" from "alive/responded";
- surface latest turn/model/system failures in normal status surfaces;
- make native goals first-class instead of relying on slash-command text;
- make daemon lifecycle and destructive cleanup scriptable;
- make registry migration/recovery safe and obvious;
- update docs, skills, and tests so this class of failure does not sneak
  through again.

## Incident facts

The real Trails use case found these product failures:

- A stale registry with schema v1 and missing tables required manual DB backup
  and recreation because `dispatch up` no-oped while a daemon answered.
- `dispatch up --json` failed even though most agent-operated commands are
  JSON-shaped.
- `dispatch new --model gpt-5.5-codex --text "$goal_prompt"` used a stale,
  guessed explicit model id and returned
  `sent: true` and `status: idle`, but no assistant work happened.
- `/goal ...` sent as initial text did not create native goal state.
- The unsupported model failure was only obvious through `dispatch watch`, not
  `dispatch get`.
- `trigger rm --json` and `archive --json` still required interactive stdin.
- The existing parity/handler tests stayed green.

## Root causes to address

1. Projection doctrine is written down, but CLI has bespoke route functions and
   control commands without an enforceable manifest/allowlist.
2. Tests prove routing and accepted calls, not live coordination trust.
3. Normal state models do not persist latest turn failures or suspicious
   no-assistant completions.
4. Goal text and native App Server goals are separate, but docs/skills do not
   make the boundary loud enough.
5. Integration tests are intentionally out of the default gate, so real
   semantics need cheap fake-level regression tests plus release smoke guidance.

## Implementation chunks

### Chunk 1 - regression tests and projection guardrails

- Add failing tests for:
  - destroy commands supporting an explicit non-interactive confirmation flag;
  - `up --json` / `down --json`;
  - `/goal` text guard or first-class `new --goal`;
  - `TurnFailed.message` being persisted and exposed by `get`;
  - `new` not overclaiming that a turn produced work.
- Introduce CLI projection metadata/manifest or a strict allowlist that
  classifies public commands as:
  - op projection;
  - composed op projection;
  - surface control.
- Add tests that fail for ungoverned public commands and mismatched schema/help
  routes.

### Chunk 2 - launch, goal, and status semantics

- Replace or supplement `NewLane.sent` with explicit launch fields such as
  `message_accepted`, `goal_set`, `first_turn`, and/or a structured launch
  result. Maintain honest naming in docs/schemas.
- Add `NewInput.goal` or equivalent. If text starts with `/goal` and no native
  goal field is used, fail or warn clearly.
- Persist latest turn/error state in the registry and expose it in `get`,
  relevant list outputs, and MCP schemas.
- Ensure model/system failures show in normal status without raw `watch`.

### Chunk 3 - scriptable surfaces and registry recovery

- Add `--yes`/`--no-interactive` support for destroy-intent CLI commands from
  projection rules, not one-off commands.
- Add JSON output to `up` and `down`.
- Improve doctor recovery for versioned missing tables.
- Add a safe registry migrate/repair command or lifecycle helper if it can be
  done without broad architecture churn. At minimum, make `up`/doctor refuse
  misleading no-op recovery and provide exact safe commands.
- Add tests for older schema v1/v2 cases with existing lanes/triggers.

### Chunk 4 - docs, skills, and release smoke

- Update README, docs/usage, skills/dispatch, skills/dm if affected, plugin docs,
  AGENTS/rules/ADRs where behavior or doctrine changed.
- Add a documented pre-release/live-dogfood smoke that uses isolated state and
  proves lane liveness.
- Update examples/schema expectations.

### Chunk 5 - local review and finalization

- Run focused tests after each chunk.
- Run `just check`.
- Run a local review pass focused on P0/P1/P2:
  - surface derivation drift;
  - live-use trust;
  - destructive/scripted safety;
  - registry migration safety;
  - docs/skill truthfulness.
- Fix P2+; fix cheap P3s; record deferred P3s in RETRO.

## Deferral policy

Acceptable deferrals only if recorded in RETRO with evidence:

- account-specific model preflight if `model/list`/verification does not expose
  reliable support in the current App Server;
- optional Graphite/worktree ownership, which is useful but not the root control
  plane trust failure;
- long-lived streaming subscriptions beyond bounded `watch`.

## Done

Done only when tests and docs prove the full objective, `just check` passes, a
review loop has no unresolved P0/P1/P2, and RETRO contains exact verification
commands, final git state, remaining risks, and PR state if submitted.
