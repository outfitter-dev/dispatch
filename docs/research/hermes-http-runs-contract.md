# Hermes HTTP Runs alternative contract

Research date: September 12, 2026. Governing work: [DIS-84](https://linear.app/outfitter/issue/DIS-84), [DIS-85](https://linear.app/outfitter/issue/DIS-85), and [DIS-87](https://linear.app/outfitter/issue/DIS-87).

## Scope

This investigation describes the HTTP automation alternative. The initial coding-thread adapter uses the native stdio gateway selected in [the provider contract](hermes-native-provider-contract.md), because it supports per-session cwd. No HTTP fallback is implied.

For an explicitly API-managed conversation, use Hermes's native HTTP Runs API for API-managed conversations. Hermes owns inference, tools, memory and canonical history. Dispatch owns the stable thread identity, prepared requests, local reservations and receipt projections. Connect to the existing runtime without owning its process lifecycle. Do not substitute Desktop JSON-RPC, import private Hermes Python modules or send a copied transcript as conversation input. This is a separately authorized API-managed path; selecting `provider = "hermes"` in Dispatch uses the owned stdio binding and never routes here automatically.

The first supported path is plain text, keyed native creation, continuation through an observed session ID, run polling and bounded canonical-history reads. Advertise only the controls independently implemented and verified. Ordinary native Runs has no per-request working directory, toolset, sandbox or approval-policy override. Transparent alternation with an already-open Desktop agent failed the live context test below and must remain unsupported. The HTTP API's keyed replay evidence does not satisfy the native stdio adapter's required `prompt_submit_if_idle_v1` and `prompt_turn_correlation_v1` capabilities.

## Evidence boundaries

The installed source was `939e45c91d751fadd94dcd1b873ac3cb44846213`; Desktop displayed version `0.21.2` and short revision `939e45c`. The existing HTTP runtime separately reported `0.21.2`. Those observations do not prove the exact source revision already loaded by every process.

Live checks used the explicitly authorized local `default` profile and new synthetic conversations. No existing user conversation was used. Authentication used the existing credential in process memory. The runtime's configuration, credentials and services were not changed. Automated tests, forced credential rotation, forced retention expiry and destructive fault simulations remain isolated.

The two read-only research collaborators supplied source analysis; the coordinator performed the live checks. The Hermes Desktop collaborator also ran its native self-improvement step, which reported creating a research skill in its profile. This is provider-owned behavior and is separate from the intentionally written research report; no claim is made that invoking a native agent is free of profile side effects.

Operator-specific endpoints, filesystem paths, process IDs and full probe records remain in local evidence and the Linear handoff. The portable protocol facts and sanitized synthetic identifiers below are sufficient to interpret the result.

## Live result matrix

| Check | Observed result | Limit |
| --- | --- | --- |
| Authenticated discovery | HTTP 200; `runtime.mode=server_agent`, `tool_execution=server`, `split_runtime=false`; native runs and status supported; keyed replay advertised durable with 86,400-second retention. | Capability support is not per-session readiness or permanent durability. |
| Two API turns | First turn stored a synthetic marker; the second recalled it through body `session_id` without a supplied transcript. Both terminal responses and four canonical message rows agreed. | Synthetic continuity proof, not a compression or arbitrary-session guarantee. |
| Exact replay and conflict | Exact first request/key returned the same run ID with `replayed=true`; changed input returned HTTP 409 `idempotency_key_conflict`; no extra user row appeared. | Same runtime/profile/credential, within minutes of admission. |
| Keyed first creation | A Runs request without a session ID created one run whose observed initial session ID equaled the run ID. Exact replay reused it and canonical history still held one user/assistant pair. | No separate empty-session creation is necessary for Dispatch's first slice. |
| Tool execution directory | A dedicated `pwd` request returned the user's home directory, confirmed by the persisted terminal tool result. | The Dispatch caller's cwd was not applied. This was not a sandbox test. |
| SSE disconnect and stop | One SSE reader received a terminal-tool start event, deliberately disconnected, then stop returned `stopping`; polling later reported `cancelled` for the same run. | Polling recovered the result; event replay and completion-wins behavior were not proven by this check. |
| API → cold Desktop | Desktop opened the API transcript and recalled the API-only marker in a new Desktop turn. | First open of this saved session, not a general cold-reset mechanism. |
| Warm Desktop A → API B → Desktop C | A introduced a Desktop marker. B correctly recalled A and introduced a new API marker. Desktop visibly rendered B. C, instructed to use only current model context, answered `UNKNOWN` instead of B's marker. All messages remained in the same canonical session. | Transparent warm context continuity failed. Transcript refresh does not establish backend context refresh. |

Correlation examples: the continuity session was `dispatch_contract_probe_20260912_1341`; API turns used distinct `run_341dc5e4bbdb46c7af000baf00744420` and `run_a4cb6ee9322c400bbaa185943b118ec9`. The warm middle turn was `run_db3ab40f049144d39dce540b6352baf4`. Its API-only marker was `amber-reef-592`; the following Desktop response was `UNKNOWN`. These are synthetic evidence identifiers, not application defaults or fixtures to hard-code.

## Wire contract and invariants

Discovery requires authenticated `GET /v1/capabilities`. Check the actual capability names: approval support is `features.run_approval_response`, while its URL is in `endpoints.run_approval`. Require durable native idempotency before advertising durable native admission. A degraded in-memory store is reported as `durable:false`.

Submit a plain-string `input` to `POST /v1/runs` with a stable `Idempotency-Key`. For first creation omit `session_id`; reserve the Dispatch lane/request before this mutation. For later turns send the positively observed native session ID in the body. The Runs handler does not use the advertised `X-Hermes-Session-Id` header as its session selector. Omit caller history, response chains and `X-Hermes-Session-Key` from the minimal path; the last is a separate memory/affinity scope.

HTTP 202 carries `run_id`, `status` and `replayed`, not a session ID or execution proof. Poll `GET /v1/runs/{run_id}`. Treat `completed`, `failed`, `cancelled` and `interrupted` as distinct terminal native facts. `running` can precede agent construction or acquisition of the durable turn lease. A terminal foreground run does not prove all detached work ended.

Native replay scope depends on both selected profile and credential. Its fingerprint covers the canonical request body and normalized memory-scope header. Freeze the body, key, target, endpoint/profile provenance and non-secret credential-scope fingerprint with the local reservation. A stable Dispatch binding ID must survive connection changes without pretending a replaced credential or endpoint is the original native recovery scope.

Ordinary terminal replay records become eligible for pruning 24 hours after their last persisted status update. Use a conservative locally recorded recovery horizon; do not extend it merely because a read still succeeds. Keep exact local receipt replay independent from this native window. After a possibly submitted call, a missing record, changed scope, expired horizon or absent transcript preserves uncertainty; none authorizes a new key or automatic replacement turn. Prefer polling a known run. Any identical-key recovery POST must satisfy the original durable scope/window and Dispatch recovery policy.

SSE uses an in-memory consuming queue, has no event ID/cursor and is not multicast. If used, allow one Dispatch reader per run and treat disconnect as partial observation. Polling remains the completion/reconciliation path. Never create another run to repair missing progress events.

## Session, workspace and ownership limits

Native message reads return a resolved `session_id` after compression. The initial/terminal run status can retain the pre-compression ID; do not blindly adopt it as the final continuation tip. Update the native mapping only from positive successor evidence while preserving the Dispatch key and ref. Unknown or empty enrolled-session history must fail closed locally. Hermes's history preflight and subsequent submission are not an atomic conditional-continuation operation; document that upstream limit.

The durable turn lease serializes existing-session writers, but its history refresh occurs after contention. Desktop's warm agent can retain stale history during uncontended acquisition, as the live test demonstrated. Native open-session ownership and active-turn ownership are different mechanisms. Keep arbitrary Desktop attachment disabled and document API-managed execution as the first supported ownership model. Do not invent a takeover, automatic fallback or transcript replay to hide the failure.

Ordinary Runs accepts no cwd, workspace, toolset, sandbox or approval-policy field and does not restore a Desktop session's cwd. Reject such settings before workspace creation, file staging or provider calls. Keep a requested Dispatch directory distinct from Hermes's actual server-side execution policy; do not populate a misleading cwd. Session model overrides and native fallback can also outrank a requested model, while run status may contain an alias; requested and observed effective settings are different evidence.

Stop is a request, not completion: retain `stopping` until native terminal evidence, and allow genuine completion to win. Steer acceptance means queued, potentially still pending when the run ends. Approval responses must target the exact observed request and run; default to a single scoped response, and do not infer durable live approval state from an old status snapshot. These controls do not inherit Runs admission idempotency.

## Source map and remaining proof

Paths refer to the inspected Hermes source revision:

- `gateway/platforms/api_server.py`: capability construction at 2253–2291; authentication/profile routing at 1338–1508; agent construction at 2108–2179; empty-session creation at 2799–2864; messages and successor response at 2926–2956.
- `gateway/platforms/api_server_runs.py`: scope/fingerprint at 188–199 and 395–406; admission at 377–504; execution/status at 598–656; status/SSE/control at 689–878; successor resolution at 360–374.
- `gateway/platforms/api_server_run_idempotency.py`: durable reservation, memory fallback and pruning. `agent/turn_facade_lease.py:233–302`: contention-dependent history reload. `tui_gateway/prompt_turn.py:435–551`: warm history snapshot.

Official references: [API Server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server) and [Programmatic Integration](https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration). Prefer inspected code and actual wire values where documentation is less precise.

Remaining isolated gates include unknown first-create admission, crash/status hydration, credential replacement, expired replay, memory-store fallback, duplicate/out-of-order observations, compression successors, stop/completion races and attention correlation. The Dispatch adapter still needs its own end-to-end CLI/MCP, reservation, restart and Codex-regression proof. This native probe does not substitute for that implementation gate.
