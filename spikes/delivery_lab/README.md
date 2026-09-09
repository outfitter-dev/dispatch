# PAT-176 exploratory delivery lab

Disposable synthetic experiments only. The DIS-71 follow-up tests a locally
patched package without replacing the installed Dispatch executable.
Expected outcomes were recorded before execution in
`.agents/notes/pat-176/plan.md`; the result report is
[`docs/research/2026-09-08-delivery-lab.md`](../../docs/research/2026-09-08-delivery-lab.md).

Run from the repository root:

```sh
uv run python -m spikes.delivery_lab.mock_delivery
uv run python -m spikes.delivery_lab.live_delivery
```

The mock command uses the real handlers, Registry and queue with the existing
FakeLaneClient. It exercises six cases, including two explicit failure seams.
It makes no model calls and writes `.agents/notes/pat-176/mock-results.json`.

The live command is opt-in and uses real model calls. It reuses ScenarioRunner's
temporary Dispatch/Codex/work directories and documented auth-file copy. It
discovers a low-effort model from the isolated daemon, preferring the repo's
scenario model, creates one temporary persisted thread for history inspection,
then exercises idle send, busy queue, steer, idle/busy context, duplicates and a
clean daemon restart. Evidence is saved to a new timestamped JSON file under
`.agents/notes/pat-176/`. Cleanup stops only its isolated daemon and removes the
temporary homes and copied credential. On an unrecoverable harness failure,
cleanup can end an outstanding disposable turn; ordinary delivery never uses
`interject` or `stop`.

`--remaining-only` skips idle/queue; it was used to narrow the live steering
failure after those cases had passed. `--desktop-thread <disposable-id>` adds a
read through the isolated managed-thread surface. It does not share Desktop's
home or prove attached delivery. The September 8 Desktop task was created and
messaged separately with supported app tools and has been archived.

Pass `--dispatch-bin /absolute/path/to/dispatch` to test a separately installed
wheel; the runner clears `PYTHONPATH` for that executable. The runner retains
individual failures so independent cases can continue, then asserts expected
replies, turn counts, queue ordering, context turn count and restart history.
These checks prove the sampled matrix, not every delivery requirement. `tail`
contains items with turn IDs, not a `turns` array. One observed context timing
is not a general scheduling guarantee.

The updated full runner passed all 11 assertions against the isolated DIS-71
wheel, with nine completed turns. Its manifest is
`.agents/notes/pat-176/custom-compat/manifest.json` and the complete run is
`live-1788909774196084000.json` in the same evidence directory.
Raw September 8 evidence remains under `.agents/notes/pat-176/` (local and
gitignored); `live-attempt-2.json` contains idle/queue, and `live-results.json`
contains the narrowed follow-up. Failed setup evidence was retained separately.

The DIS-72 package proof is a separate bounded three-turn run:

```sh
uv run python -m spikes.delivery_lab.reliability --dispatch-bin /absolute/private/dispatch
```

It checks keyed replay concurrency, conflict, lookup, restart replay, busy queue,
completion and native receipt correlation. It performs no live fault injection;
crash and lost-ACK behavior are verified with isolated CI-safe tests.

`capabilities.py` inventories actual tools in an isolated invoked session.
`dynamic_bridge.py` is an opt-in externally mediated host-tool prototype. It
requires an explicitly supplied disposable target and supported host handlers;
it is not a native Desktop adapter or general forwarding service. Read the
script help and the report's capability section before using it. Raw tool and
package evidence is local under `.agents/notes/pat-176/`.
