# Hermes native provider contract

Research date: September 12, 2026. Governing work: [DIS-84](https://linear.app/outfitter/issue/DIS-84), [DIS-85](https://linear.app/outfitter/issue/DIS-85), [DIS-86](https://linear.app/outfitter/issue/DIS-86), and [DIS-87](https://linear.app/outfitter/issue/DIS-87).

## Integration decision

Use a Dispatch-owned, long-lived Hermes TUI gateway over stdio JSON-RPC for the initial coding-thread adapter. This is the maintained native boundary used by Hermes's TUI and supported for custom hosts. Hermes owns inference, tools, memory and canonical history. Dispatch owns stable thread identity, immutable requests, reservations, ordering, local receipts and recovery decisions. Start one gateway per explicitly configured provider binding, not a process per turn.

The first supported ownership model is dedicated Dispatch-managed sessions on the selected profile. Implement plain text, explicit existing session cwd, creation, continuation, native completion observations and bounded history. Advertise other controls only after their own tests pass. Do not attach to arbitrary Desktop sessions or infer shared live context from a shared database.

The [HTTP Runs investigation](hermes-http-runs-contract.md) remains valid for explicitly API-managed automation. It proved native keyed replay and polling, but ordinary Runs cannot honor session cwd and failed the warm Desktop context check. It is not the initial coding adapter, and no automatic transport fallback is permitted.

## Evidence boundaries

Installed source was `939e45c91d751fadd94dcd1b873ac3cb44846213`; Desktop displayed `0.21.2` / `939e45c`. The existing HTTP runtime independently reported `0.21.2`. The owned stdio probe launched the installed source explicitly. These observations do not establish the exact code loaded by every pre-existing service.

Matt authorized direct interaction with Hermes on the local `default` profile. The coordinator used new synthetic sessions for live proof. Automated tests and destructive fault simulations remain isolated. Existing user conversations, credentials, configuration and services were preserved. The owned probe used a process-scoped startup-sweep fence and shut down only its own gateway after its sessions were idle. Native startup still registered its own heartbeat and could perform configured MCP discovery/model prewarming.

A dedicated Hermes Desktop research collaborator inspected the source and wrote a local report. Its first native self-improvement step also reported creating a research skill in its profile. This is provider-owned behavior; invoking an agent is not claimed to be free of profile side effects. Subsequent synthetic prompts explicitly prohibited self-improvement.

Full local endpoints, process IDs, paths and probe logs remain in local evidence and the Linear handoff. This document preserves the portable wire contract and sanitized synthetic correlations.

## Live proof

| Check | Result | Practical limit |
| --- | --- | --- |
| Native stdio startup | `gateway.ready` observed; `gateway.capabilities` returned `per_session_exclusive_submit:true`; session info reported Desktop contract 6 and default profile. | Sparse negotiation, not proof of every native method. |
| Native creation | `session.create` returned a short runtime ID, a separate stored session key and the requested existing cwd. | An empty draft is not yet a durable stored session. |
| Two coherent turns | A replied `ACK silver-pine-846`; B recalled `silver-pine-846` without a supplied transcript. Both emitted `message.complete` with `status:complete`, then settled info with `running:false`; canonical user/assistant rows agreed. | Live single-owner evidence, not native idempotency or crash recovery. |
| Tool working directory | Native terminal `pwd` reported the selected temporary project directory. | Directory selection, not a sandbox or approval-policy guarantee. |
| Clean gateway restart | After all turns settled, the probe closed its session and gateway, started another gateway, and resumed the same stored key. The runtime ID changed; stored identity, cwd and prior history remained. No auto-continue descriptor or running turn appeared. | Clean completed-session restart only. |
| Continuity and tool after restart | The next turn recalled the marker and ran only `pwd`. `tool.complete` confirmed the selected path and exit code 0; completion and settled info agreed. Canonical history retained the earlier rows plus the new turn. | No forced crash, compression, lost-ack or contested ownership was exercised live. |
| Shutdown | Both owned gateways exited normally with code 0 after the synthetic session was closed. | Normal lifecycle, not a stalled-child or abrupt-death test. |
| HTTP alternative | Two turns, exact keyed replay, conflicting-key rejection, polling and stop worked in dedicated API sessions. | Ordinary Runs omitted session cwd; warm Desktop A → API B → Desktop C returned `UNKNOWN` for B-only context despite displaying B. |

The stdio stored key was `20260912_101845_f177ea`. Runtime IDs were `7ab24788`, then `b94618e1` after clean restart. Canonical user/assistant rows for A/B were 1660–1663; the after-restart user and assistant rows were 1670 and 1673. These are synthetic evidence identifiers, not application defaults. The native history response's raw `count` includes records omitted or combined by its message projection; do not require it to equal the projected array length.

## Process and profile contract

Launch the configured Hermes interpreter with `-m tui_gateway.entry`, using Hermes's installed source root as process cwd. Match the native TUI launcher: put the source root first on `PYTHONPATH` and set `HERMES_PYTHON_SRC_ROOT`. Select the profile using an explicit `HERMES_HOME` and the matching RPC profile. Do not infer it from a mutable active-profile marker at submission time. Resolve an installed interpreter explicitly; native installations may use either `venv` or `.venv`.

Set `HERMES_TUI_WS_ORPHAN_REAP_GRACE_S=0` in the owned child environment. The inspected implementation uses it to suppress the startup orphan-session sweep without editing shared profile configuration. It also disables WebSocket orphan reaping, which does not apply to the owned stdio connection. Do not inherit sidecar or alternate-gateway attachment variables into this mode. Preserve secrets in Hermes's own profile resolver; do not copy them into Dispatch records or command arguments.

Use UTF-8 JSONL JSON-RPC on stdin/stdout and continuously drain bounded stderr diagnostics without exposing secrets. Startup waits for `gateway.ready`; EOF, protocol corruption or startup timeout makes that binding unavailable. The child lifecycle is separate from an externally attached service's lifecycle. Do not stop existing Desktop, serve or Runs processes.

`gateway.capabilities` currently advertises only per-session exclusive submit. `gateway.ready` carries a process replay epoch; session info carries `desktop_contract` (6 in the inspected build). Pin and test the minimum result shapes and methods the adapter consumes. These fields are not a complete stable API schema or evidence of durable admission.

## Session identity and workspace

`session.create` takes `profile`, `cwd`, title and supported settings. Validate cwd locally before side effects and verify the returned effective cwd. Native create only treats an existing directory as explicit; a bad path can otherwise become a fallback. Process cwd is not the session workspace.

The returned `session_id` is an ephemeral runtime route. `stored_session_id` is the durable conversation key used for later resume. Persist both with their binding and process generation, without treating the runtime ID as the stable Dispatch identity. Native create deliberately leaves an empty draft in memory; its row is created on first prompt. Reserve the Dispatch lane and first request before either native call. A lost first-create response is not permission to create another session automatically.

Ordinary `session.resume` resolves a stored key and compression successor, restores history and stored cwd, and returns the current runtime route and resolved session key. The separate `lazy:true` watch path deliberately skips successor resolution and reads only the exact target segment. Positive successor evidence updates the native mapping while the Dispatch key/ref remains stable. Never restore a process-local route after a generation change or infer a successor from a coincident title.

Default cold resume can automatically continue an interrupted prompt. Marker clearing is best effort, so even a previously observed terminal result does not guarantee that ordinary resume is non-executing. The clean live probe demonstrates one successful case, not an automatic recovery policy. Automated ordinary resume requires a verified supported suppression mechanism on the owned runtime; until then, a changed generation leaves its stored sessions unavailable for automatic continuation. Do not delete markers, alter shared profile settings or race an interrupt after resume.

For bounded investigation of unresolved work, the inspected `lazy:true` watch path creates a runtime without building an agent or scheduling auto-continue. It reads only the exact stored target segment, skips compression-successor resolution and does not restore the full ordinary-resume runtime context. It reopens the native row and may repair its message projection, so it is not a pure read or an execution-resume substitute. It must never lead to prompt submission while the receipt remains unresolved. Missing history, compression or ambiguous matching leaves the receipt unknown.

## Submission and receipt semantics

Freeze the submitted intent separately from the effective provider request. The latter includes the exact binding, stored/runtime identities, process generation, cwd, supported settings, prompt digest, JSON-RPC request ID and pre-submit history watermark. Local reservation and claim happen before native I/O; no database transaction spans that I/O.

Allow one unresolved Dispatch submission per Hermes thread. `prompt.submit` takes the runtime `session_id` and `text`. Its JSON-RPC response currently reports only `status:streaming`. The request ID correlates that acknowledgment, not a durable native turn. Do not invent a Hermes run/turn ID from the local delivery ID or label the acknowledgment execution completion.

Events arrive as JSON-RPC `event` notifications with type, runtime session ID and payload. `message.complete` carries native status/text; settled `session.info` separately reports whether the foreground session is running. Tool completion carries native tool correlation and result. Map these to the pending local request only when the exact binding/runtime/generation, exclusive-owner scope and evidence uniquely establish correlation. One pending client request alone is insufficient if Hermes starts goals, loops, delegation/process notifications or other unsolicited turns. Track unsolicited activity separately, keep those modes outside the initial advertised contract, and retain uncertainty whenever the native event cannot be attributed. Persist the received evidence and canonical row correlation. A terminal answer, readiness for the next input and completion of detached background work remain separate facts.

### Request-correlation prerequisite

The source-pinned `939e45c91d751fadd94dcd1b873ac3cb44846213` gateway cannot positively establish that an arbitrary `message.complete` belongs to an accepted `prompt.submit`. Its idle check and in-process claim are separate: a notification poller can claim an idle session before direct submission acquires its lock, and the direct path then sets the shared running flag without rechecking it. Heartbeat and bot-mailbox turns can each produce the same single `message.start` → `message.complete` shape as an ordinary prompt. A pre-submit sequence watermark, `status:streaming`, one pending receipt, replay order, or a later `running:false` are therefore insufficient ownership proof.

Dispatch must fail closed for durable Hermes send receipts on that stock contract. The additive upstream contract is the capability `prompt_turn_correlation_v1`: atomically allocate an opaque `turn_id` while claiming an idle session; keep a per-turn owner token so only that owner can release the claim; return the id from accepted submit; and echo it in start, terminal completion, terminal error, active/settled info, and retained or replayed evidence. Dispatch may resolve only an exact matching id in the current gateway generation; missing, mismatched, truncated, or restart-era evidence remains unknown. [DIS-94](https://linear.app/outfitter/issue/DIS-94) is actively implementing and testing this in an isolated upstream worktree. It is not a verified capability or an installed-runtime change. The synthetic two-turn/cwd probe remains positive evidence of native continuity, not production request-correlation proof.

Hermes has no native idempotency key or durable request-indexed turn record on this transport. A local key prevents Dispatch from issuing the same request again; it does not prove native admission. After a write failure, timeout, lost acknowledgment or process death, preserve unknown outcome and block later work. Never resend the prompt, replace the session, switch transport or perform ordinary auto-continuing resume to repair uncertainty.

A known native error before admission can reject the request. An accepted response proves only native admission progress. Historical text equality alone cannot prove that a particular local request ran. Reconcile only with unique, positive correlation; absent, duplicated, partial or compressed history remains uncertain. Local exact-key receipt replay stays durable even when native evidence cannot recover the result.

`session.history` is unpaginated and can fall back to in-memory history on a native storage read error. Its result is conversation evidence with explicit limits, not an unconditional durable-storage receipt. Apply adapter response-size bounds and preserve partiality; do not claim server-side pagination.

The TUI replay ring is bounded in memory and resets with the process epoch. It can repair live progress display within one process; it is not an admission ledger. A changed epoch or truncated replay never authorizes retransmission.

## Controls, ownership and Desktop

Native methods for interrupt, approval and steering exist, but each needs exact target/generation/request correlation and its own verified capability. A stop acknowledgment is not terminal completion. Native policy and model fallbacks can affect execution; distinguish requested settings from observed effective settings and reject unsupported overrides before workspace creation or provider calls.

The per-session exclusive-submit capability does not make separate backend processes share warm history. Dispatch-managed ownership must be explicit, and lease refusal cannot trigger a takeover or fallback. Keep attachment to arbitrary Desktop conversations disabled until the full continuity/ownership gate passes.

The currently running Desktop child uses an ephemeral loopback endpoint and token supplied through Electron. No supported external discovery/attach contract was found. A separate served WebSocket gateway uses its own authentication, which is not the Runs bearer key. Deliberately sharing one authenticated gateway could provide same-process fanout, but that requires its own configuration, lifecycle, restart and alternating-writer proof. Sharing `state.db` alone is insufficient.

`session.close` tears down the native runtime and records an end reason. Use it only as the owned-session lifecycle action; do not confuse it with detaching an external observer. The first slice must report exactly which process/session lifecycle it owns.

## Remaining implementation gates

The native probe is not Dispatch integration. DIS-85/86 still require frozen-request and local-receipt tests, typed observations, CLI/MCP end-to-end turns, lost first-create/submit acknowledgment, process death, blocked unknown outcomes, exact local replay/conflict, source-pinned lazy recovery with crash markers, malformed protocol, capability/version mismatch, duplicate/out-of-order events, compression, ownership contention, and controls/attention correlation. Destructive fault tests use isolated native state or synthetic transports.

DIS-83 must prove provider startup independence before advertising Hermes-only operation or Codex-down availability. DIS-87 must prove live Desktop coexistence before enabling attachment. DIS-88 covers truthful diagnostics, unsupported modes, packaging and operator recovery. Neither a successful native probe nor a green shared migration passes those gates by itself.

## Source map

Paths refer to the inspected Hermes source revision:

- `ui-tui/src/gatewayClient.ts:62–80,476–488`: native interpreter resolution and stdio launch.
- `tui_gateway/entry.py:241–285`: startup, ready notification and JSONL dispatch.
- `tui_gateway/methods_session.py:325–395,689–853,1673–1711,1893–1897`: creation identities, lazy/ordinary resume, status/history and close.
- `tui_gateway/methods_prompt.py:540–653`: admission, exclusive submit and immediate response.
- `tui_gateway/session_notifications.py:127–132,180–210,492–541`: notification claim plus the single-start heartbeat and bot-mailbox paths.
- `tui_gateway/prompt_turn.py:622–672,784–878`: native terminal status, markers and settled observations.
- `tui_gateway/server.py:2047–2094`: session info identity/cwd/running provenance.
- `tui_gateway/session_auto_continue.py:21–114`: crash-marker automatic continuation.
- `tui_gateway/session_reaper.py:290–427`: startup sweep, heartbeat and process-scoped suppression.
- `tui_gateway/event_replay.py` and `methods_voice.py:434–446`: bounded replay and sparse capabilities.

Official references: [Programmatic Integration](https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration) and [API Server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server). Prefer inspected code and actual wire values where documentation is less precise.
