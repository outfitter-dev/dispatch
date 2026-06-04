# CLI/MCP Ergonomics — retro

Durable execution ledger for the one-branch CLI/MCP ergonomics goal.

Status: **quality loop complete**.

## Current branch

- Branch: `feat/cli-mcp-ergonomics`
- Base: `main`
- Intended shape: one branch / one big swing, not a Graphite stack

## Preparation log

- 2026-06-04: Created the tracked execution packet under
  `.agents/plans/cli-mcp-ergonomics/` so a fresh goal worker can read the plan
  from a clean worktree.
- 2026-06-04: Added `.claude/worktrees/` to `.gitignore` so Claude-owned
  background worktrees do not pollute `git status`.

## Execution log

- 2026-06-04: Implemented ergonomic CLI projection metadata in
  `contracts/derive_cli.py`: root `send`, root cancel-only `stop`, grouped
  `lane`, `goal`, `trigger`, `daemon`, and local `schema <command>`.
- 2026-06-04: Changed the authored `send` contract to carry
  `mode=send|steer|queue|interject|context`; CLI mode flags are mutually
  exclusive aliases over that contract mode.
- 2026-06-04: Implemented `send --context`, `send --steer`, and
  `send --interject`; initial `send --queue` deliberately failed because
  durable queue storage/supervision was not implemented yet.
- 2026-06-04: Added `lane-rename` as a real authored op plus registry handle
  update support; owned lanes also attempt App Server `thread/name/set`.
- 2026-06-04: Kept MCP workflow grouping derived from the op registry and added
  `rename` to lane write grouping. MCP remains grouped by workflow/safety
  boundary rather than CLI spelling.
- 2026-06-04: Updated README, usage docs, design command-surface summary,
  plugin README, and `skills/dispatch` + `skills/dm` to the new grammar.
- 2026-06-04: Committed the green ergonomics baseline as
  `59b4fef feat: project ergonomic cli and mcp surfaces`.
- 2026-06-04: Implemented durable local `send --queue`: registry-backed
  queued messages, one pending message drained per idle transition, restart
  recovery for claimed-but-not-finished queue rows, and docs/help updates.
- 2026-06-04: Retired public registry ops `steer`, `brief`, and `interrupt`.
  Their behavior now lives under the single `send` contract (`mode=...`) and
  the `stop` contract. The lower-level handlers remain as small internals for
  triggers and send-mode composition.
- 2026-06-04: Resolved the coordinator P1 `turn/interrupt` active-turn-id
  finding. Made `TurnInterruptParams.turn_id` and `LaneClient.turn_interrupt`
  (client + `Ctx` protocol + fake) require `str`. Added a shared
  `_require_active_turn(lane, action)` helper so `steer`, `send --interject`,
  internal `interrupt`, and `stop` all raise a clean `ValidationError` on an
  idle lane instead of calling the App Server with a null `turnId`.
- 2026-06-04: Resolved the queued P3 forced-color JSON finding. Replaced Rich's
  module-level `print_json` calls with a plain `json.dumps`/`typer.echo` helper
  so command and schema JSON stay jq-friendly even when `FORCE_COLOR` is set.
- 2026-06-04: Quality loop pass 1 verified the forced-color schema regression
  remains fixed, then found and fixed a P2 schema projection drift: composed CLI
  routes `schema "lane list --unmanaged"` and `schema "lane tail --follow"` now
  resolve to `discover` and `watch` respectively, and unknown schema targets exit
  cleanly instead of rendering a traceback.
- 2026-06-04: Quality loop pass 2 reviewed docs/skills, live CLI help, schema
  smokes, queue semantics, trigger handlers, and daemon log behavior. Fixed a
  P3 audit gap: `trigger pause` and `trigger resume` now write `actions_log`
  entries like `trigger add` and `trigger rm`.
- 2026-06-04: Quality loop pass 3 reviewed the branch-vs-main diff, MCP
  routing/error projection, generated schemas, and stale-marker scan. Fixed a
  P3 projected-schema wording issue: `SendInput.mode` no longer says queue is
  "reserved" after durable queueing shipped.
- 2026-06-04: Final no-findings pass reviewed the current diff, retro drift,
  MCP/parity tests, schema smokes, and live help. No unresolved P0/P1/P2 and no
  easy worthwhile P3 remained.
- 2026-06-04: Post-Spark audit loop fixed the remaining derived-surface gaps:
  `schema "lane fork"`, `schema "lane rollback"`, `schema "lane compact"`, and
  `schema "lane archive"` now resolve to their canonical ops. Added a parity
  guard that all public CLI schema routes resolve, with `open` explicitly
  allowlisted as the only intentional MCP-only op.

## Verification log

- 2026-06-04: Focused surface/handler/example tests:
  `uv run pytest tests/surfaces/test_derive_cli.py tests/surfaces/test_derive_mcp.py tests/surfaces/test_mcp_routing.py tests/surfaces/test_parity.py tests/core/test_handlers.py tests/core/test_examples.py -q`
  -> 52 passed.
- 2026-06-04: Isolated CLI smokes used temporary `DISPATCH_HOME` only:
  `dispatch --help`, `dispatch lane --help`, `dispatch goal --help`,
  `dispatch trigger --help`, `dispatch send --help`, `dispatch goal set --help`,
  `dispatch schema send`, and `dispatch schema "goal set"`.
- 2026-06-04: Schema smoke with `jq` confirmed `schema "goal set"` maps to
  `goal-set` with `lane,objective,status,token_budget`.
- 2026-06-04: `git diff --check` -> clean.
- 2026-06-04: Final gate `just check` -> ruff check passed, ruff format
  passed, mypy passed, pytest 130 passed / 8 deselected.
- 2026-06-04: Queue-focused tests:
  `uv run pytest tests/registry/test_store.py tests/core/test_handlers.py tests/core/test_triggers.py tests/daemon/test_supervisor.py tests/surfaces/test_derive_cli.py tests/core/test_examples.py -q`
  -> 64 passed.
- 2026-06-04: Queue loop final gate `just check` -> ruff check passed, ruff
  format passed, mypy passed, pytest 136 passed / 8 deselected.
- 2026-06-04: Queue CLI smoke used temporary `DISPATCH_HOME`:
  `dispatch send --help` shows `--queue` as "Queue after current work"; `schema
  send` still derives mode schema from `SendInput`.
- 2026-06-04: Final cleanup focused tests:
  `uv run pytest tests/core/test_examples.py tests/surfaces/test_derive_mcp.py tests/surfaces/test_mcp_routing.py tests/surfaces/test_derive_cli.py tests/surfaces/test_parity.py tests/core/test_handlers.py -q`
  -> 54 passed.
- 2026-06-04: Final cleanup gate `just check` -> ruff check passed, ruff
  format passed, mypy passed, pytest 136 passed / 8 deselected.
- 2026-06-04: Final cleanup smokes used temporary `DISPATCH_HOME`: `schema stop`
  maps to op `stop` with input key `lane`; `send --help` and `stop --help`
  render successfully.
- 2026-06-04: `turn/interrupt` fix focused tests:
  `uv run pytest tests/core/test_handlers.py tests/core/test_examples.py tests/surfaces/test_parity.py -q`
  -> 41 passed. New cases: `test_stop_requires_active_turn`,
  `test_interrupt_requires_active_turn`, `test_interject_requires_active_turn`.
- 2026-06-04: `turn/interrupt` fix gate `just check` -> ruff check passed, ruff
  format passed, mypy passed, pytest 139 passed / 8 deselected. Note: this
  background shell exports `FORCE_COLOR=3`, which makes the `schema` command
  emit ANSI codes and fails the substring assertion in
  `test_derive_cli.py::test_schema_command_prints_derived_schema_without_daemon`;
  the gate is green once color is not force-enabled (`env -u FORCE_COLOR`). That
  test is a pre-existing P3 robustness gap (it should strip ANSI), unrelated to
  this fix.
- 2026-06-04: Forced-color JSON focused tests:
  `FORCE_COLOR=3 uv run pytest tests/surfaces/test_derive_cli.py::test_schema_command_prints_derived_schema_without_daemon tests/surfaces/test_derive_cli.py::test_schema_command_stays_plain_json_when_color_is_forced -q`
  -> 2 passed.
- 2026-06-04: Surface focused tests:
  `uv run pytest tests/surfaces/test_derive_cli.py tests/surfaces/test_parity.py tests/surfaces/test_derive_mcp.py tests/surfaces/test_mcp_routing.py -q`
  -> 20 passed.
- 2026-06-04: Forced-color JSON loop gate `just check` -> ruff check passed,
  ruff format passed, mypy passed, pytest 140 passed / 8 deselected.
- 2026-06-04: Forced-color seed verification:
  `FORCE_COLOR=3 uv run pytest tests/surfaces/test_derive_cli.py::test_schema_command_prints_derived_schema_without_daemon tests/surfaces/test_derive_cli.py::test_schema_command_stays_plain_json_when_color_is_forced -q`
  -> 2 passed.
- 2026-06-04: Schema composed-route focused tests:
  `uv run pytest tests/surfaces/test_derive_cli.py -q` -> 12 passed.
- 2026-06-04: Surface projection focused tests:
  `uv run pytest tests/surfaces/test_derive_cli.py tests/surfaces/test_parity.py tests/surfaces/test_derive_mcp.py tests/surfaces/test_mcp_routing.py -q`
  -> 22 passed.
- 2026-06-04: Schema composed-route CLI smokes:
  `uv run dispatch schema "lane list --unmanaged" | jq -r .op` -> `discover`;
  `uv run dispatch schema "lane tail --follow" | jq -r .op` -> `watch`.
- 2026-06-04: Schema composed-route gate `just check` -> ruff check passed,
  ruff format passed, mypy passed, pytest 142 passed / 8 deselected.
- 2026-06-04: Trigger audit focused tests:
  `uv run pytest tests/core/test_trigger_handlers.py tests/core/test_triggers.py tests/core/test_handlers.py::test_status_and_log_reflect_activity -q`
  -> 18 passed.
- 2026-06-04: Trigger audit surface/examples smoke:
  `uv run pytest tests/core/test_examples.py tests/surfaces/test_derive_cli.py tests/surfaces/test_mcp_routing.py -q`
  -> 18 passed.
- 2026-06-04: Trigger audit gate `just check` -> ruff check passed, ruff
  format passed, mypy passed, pytest 142 passed / 8 deselected.
- 2026-06-04: MCP/parity review:
  `uv run pytest tests/surfaces/test_derive_mcp.py tests/surfaces/test_mcp_routing.py tests/surfaces/test_parity.py tests/contracts/test_contracts.py -q`
  -> 14 passed.
- 2026-06-04: Schema wording focused tests:
  `uv run pytest tests/surfaces/test_derive_cli.py tests/surfaces/test_derive_mcp.py tests/surfaces/test_parity.py -q`
  -> 18 passed.
- 2026-06-04: Schema wording smoke:
  `uv run dispatch schema send | jq -r '.input.properties.mode.description'`
  -> describes queue as durable delivery for the next idle transition.
- 2026-06-04: Schema wording gate `just check` -> ruff check passed, ruff
  format passed, mypy passed, pytest 142 passed / 8 deselected.
- 2026-06-04: Final no-findings focused sweep:
  `uv run pytest tests/surfaces/test_derive_cli.py tests/surfaces/test_derive_mcp.py tests/surfaces/test_mcp_routing.py tests/surfaces/test_parity.py tests/core/test_examples.py tests/core/test_handlers.py tests/core/test_trigger_handlers.py tests/core/test_triggers.py tests/registry/test_store.py tests/daemon/test_supervisor.py -q`
  -> 85 passed.
- 2026-06-04: Final schema smokes:
  `uv run dispatch schema send`, `uv run dispatch schema "lane tail --follow"`,
  and `uv run dispatch schema "lane list --unmanaged"` -> ops `send`, `watch`,
  and `discover`.
- 2026-06-04: Final closure gate `just check` -> ruff check passed, ruff
  format passed, mypy passed, pytest 142 passed / 8 deselected.
- 2026-06-04: Post-Spark focused surface tests:
  `uv run pytest tests/surfaces/test_derive_cli.py tests/surfaces/test_parity.py -q`
  -> 16 passed.
- 2026-06-04: Post-Spark forced-color focused tests:
  `FORCE_COLOR=3 COLORTERM=truecolor uv run pytest tests/surfaces/test_derive_cli.py tests/surfaces/test_parity.py -q`
  -> 16 passed.
- 2026-06-04: Post-Spark schema route smokes:
  `send`, `stop`, `new`, all public `lane`, `goal`, `trigger`, and `daemon`
  schema commands resolved to their expected canonical ops; `open` remains
  intentionally MCP-only.
- 2026-06-04: Post-Spark closure gate `just check` -> ruff check passed, ruff
  format passed, mypy passed, pytest 143 passed / 8 deselected.

## Review log

- 2026-06-04 coordinator review:
  Overall score: not ready yet
  Summary: Branch is close and `just check` is green, but two non-PR findings
  should seed the next quality loop.
  Findings:
  - P1 — `src/outfitter/dispatch/core/handlers.py` / `src/outfitter/dispatch/client/models.py`
    — `stop` and `send --interject` can call App Server `turn/interrupt` without
    a known active turn id, while the installed App Server schema requires
    `turnId: string`. Make `TurnInterruptParams.turn_id` required, make the
    client require a `str`, and have idle-lane `stop`/`interject` raise a clean
    `ValidationError` like `steer`.
  - P2 — this retro's final state had stale source-control wording after the
    final cleanup commit. Keep `RETRO.md` accurate as part of the loop.

- 2026-06-04 local self-review:
  Overall score: 4/5
  Summary: Core grammar is implemented and verified; no open P0/P1/P2 findings.
  Findings: none P0/P1/P2.
  No-findings statement: Inspected CLI projection diff, send/rename handlers,
  MCP grouping, parity tests, docs/skills updates, and isolated CLI smokes.
  Residual risk is P3-level polish around whether future contract cleanup should
  retire internal legacy ops (`steer`, `brief`, `interrupt`) once all surfaces
  are fully send/stop-centered.
- 2026-06-04 queue-loop local self-review:
  Overall score: 4.5/5
  Summary: Durable local queue semantics are implemented and verified; no open
  P0/P1/P2 findings.
  Findings: none P0/P1/P2.
  No-findings statement: Inspected registry queue schema, queue drainer,
  handler enqueue path, reactor/supervisor drains, docs/help, and tests. The
  remaining P3 cleanup is contract naming polish around legacy internal message
  ops that no longer match the operator grammar.
- 2026-06-04 final cleanup local review:
  Overall score: 5/5
  Summary: CLI, MCP, contracts, docs, skills, queue behavior, and tests now tell
  one coherent story.
  Findings: none.
  No-findings statement: Inspected final registry IDs, CLI stop/schema routing,
  MCP route coverage, message-mode handlers, queue semantics, docs/skills, and
  full `just check`. Remaining deferred work (`preset list/new`, true infinite
  tail, optional `open` primitive retirement) is explicitly scoped outside this
  branch rather than a quality gap in the implemented grammar.
- 2026-06-04 turn/interrupt fix:
  Coordinator P1 (required `turn/interrupt` turnId) is resolved; coordinator P2
  (keep this retro accurate) is addressed by this update. No new P0/P1/P2
  findings. Residual risk: the `schema` command test does not strip ANSI, so it
  fails under forced color — a P3 test-robustness item left for the broader
  quality loop, not this scoped fix.
- 2026-06-04 forced-color JSON loop:
  Overall score: 5/5
  Summary: The queued P3 is fixed in production behavior and covered by a
  regression test.
  Findings: none.
  No-findings statement: Inspected CLI JSON rendering, schema command output,
  forced-color regression coverage, surface parity, and full `just check`.
  Queued findings are now closed; remaining deferred scope is intentional and
  documented below.
- 2026-06-04 schema composed-route loop:
  Overall score: 4.5/5
  Summary: The forced-color seed issue is verified closed; a new P2 schema drift
  for flag-composed CLI routes was found and fixed.
  Findings fixed:
  - P2 — `src/outfitter/dispatch/contracts/derive_cli.py` — `schema "lane tail --follow"`
    crashed with a traceback and could not show the `watch` schema, while
    `schema "lane list --unmanaged"` did not expose the `discover` schema.
  No-findings statement: Rechecked CLI schema routing, forced-color behavior,
  surface parity tests, direct schema smokes, and full `just check`. Continue
  looping on handlers, queue semantics, MCP action projection, and docs/skills
  consistency before declaring the branch boring.
- 2026-06-04 trigger audit loop:
  Overall score: 4.5/5
  Summary: Docs/skills and live help align with the ergonomic grammar; one easy
  worthwhile P3 in trigger-management audit coverage was found and fixed.
  Findings fixed:
  - P3 — `src/outfitter/dispatch/core/trigger_handlers.py` — `trigger pause`
    and `trigger resume` changed durable trigger state without appearing in
    `daemon log`, unlike `trigger add` and `trigger rm`.
  No-findings statement: Rechecked repo-wide public command references, usage
  docs, skills, live `send`/`lane list`/`trigger add`/`new` help, representative
  schema smokes, queue drain code, trigger handlers, and full `just check`.
  Continue one more skeptical pass over MCP routing, error projection, and
  branch-vs-main diff before closing.
- 2026-06-04 schema wording loop:
  Overall score: 4.5/5
  Summary: MCP routing/parity passed; one stale P3 projected-schema phrase was
  found and fixed.
  Findings fixed:
  - P3 — `src/outfitter/dispatch/core/models.py` — `SendInput.mode` still said
    queue was "reserved for durable queued delivery" even though durable queueing
    is implemented; this stale wording projected into CLI/MCP schemas.
  No-findings statement: Rechecked branch diff shape, MCP grouped tools/routes,
  error projection tests, representative schema outputs, and stale-marker scan.
  The focused schema/MCP tests and full gate passed.
- 2026-06-04 final no-findings review:
  Overall score: 5/5
  Summary: The branch is boring to ship locally.
  Findings: none.
  No-findings statement: Rechecked current git state, branch diff shape,
  docs/skills public command references, live CLI help, schema smokes, MCP
  grouped routes, MCP error projection, examples-as-tests, handler/queue/trigger
  tests, stale-marker scan, and full `just check`. Deferred items are intentional
  feature scope (`preset list/new`, true infinite tail, optional `open` primitive
  retirement) rather than unresolved quality findings in this branch.
- 2026-06-04 post-Spark audit review:
  Overall score: 5/5
  Summary: Two read-only Spark audits agreed current docs/skills/help/MCP
  wording is migrated; their actionable derived-surface findings are fixed.
  Findings fixed:
  - P2 — `src/outfitter/dispatch/contracts/derive_cli.py` — composed schema
    routes for `lane fork`, `lane rollback`, `lane compact`, and `lane archive`
    did not resolve to their canonical ops.
  - P3 — `src/outfitter/dispatch/contracts/derive_cli.py` — JSON output is now
    rendered without Rich so forced-color/highlighting environments cannot emit
    ANSI into jq-facing output.
  - P3 — `tests/surfaces/test_parity.py` — CLI op reachability now has an
    explicit public-route coverage assertion with `open` allowlisted as the only
    intentional MCP-only op.
  No-findings statement: Rechecked public docs/skills/help, schema route matrix,
  MCP grouped route coverage, forced-color output, focused surface tests, and
  full `just check`.

## Deferred scope / follow-up

- Durable `send --queue`: follow-up loop implemented local durable queued sends
  with a registry queue table, one-message idle draining, and restart recovery
  for in-flight queue claims.
- `preset list` / `preset new`: deferred to avoid growing this branch into config
  authoring. `new --preset` remains supported. Follow-up should expose config
  inspection/writer ops through the contract layer.
- Full `open` removal from internals: operator docs and CLI now steer users to
  `new --no-send`; the lower-level `open` op remains in the registry/MCP for
  existing core coverage until a separate contract cleanup decides whether to
  retire it.
- True live tail: `lane tail --follow` remains bounded `watch` behavior. A
  durable infinite tail still needs subscription-capable control socket support.

## Queued quality loop

1. ~~Fix the `turn/interrupt` active-turn-id contract for `stop` and
   `send --interject`, including client model and tests.~~ Done
   (`require turnId; raise ValidationError on idle lanes`).
2. ~~Keep this retro accurate after each loop and commit.~~ Done.

3. ~~Strip ANSI from CLI JSON/schema output under forced color.~~ Done
   (plain `json.dumps`/`typer.echo` JSON helper plus forced-color regression
   test).
4. ~~Fix schema projection drift for flag-composed ergonomic CLI routes.~~ Done
   (`schema "lane list --unmanaged"` -> `discover`; `schema "lane tail --follow"`
   -> `watch`; `schema "lane fork|rollback|compact|archive"` -> their
   canonical ops; unknown schema targets exit 2 without tracebacks).
5. ~~Audit trigger pause/resume mutations.~~ Done (`actions_log` now records
   `trigger-pause` and `trigger-resume`).
6. ~~Remove stale "queue is reserved" projected schema wording.~~ Done
   (`SendInput.mode` describes durable queued delivery as implemented).
7. ~~Add an explicit CLI reachability guard for ergonomic projections.~~ Done
   (public schema routes cover every op except intentionally MCP-only `open`).

No queued quality-loop findings remain open.

## Final state

- Branch: `feat/cli-mcp-ergonomics`.
- Source-control state: one Graphite-tracked commit over `main`, squashed and
  amended after the Spark audit findings.
- Proof constraints: no destructive git operations, no secrets, no live user
  agent state used in tests/smokes, no hand-written separate MCP world.
- Final review result: no unresolved P0/P1/P2; no easy worthwhile P3 left.
