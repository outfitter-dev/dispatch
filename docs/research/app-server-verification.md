# Codex App Server — verification against local binary (refreshed 2026-07-09)

Verifies the "Codex App Server Technical Report (§1–15)" against the actually installed binary. Report is accurate for its pinned era (~CLI 0.132); several conclusions are already stale on the newer local binary.

## Ground truth on this machine

Refreshed 2026-07-09:

- `/Applications/ChatGPT.app/Contents/Resources/codex` reports
  `codex-cli 0.144.0-alpha.4`. The latest stable npm/GitHub release is
  `0.144.0`; its generated request inventory matches the bundled alpha used for
  the local probes.
- `codex app-server daemon version` reports a running managed daemon with CLI
  `0.144.0-alpha.4` and App Server `0.142.5`. Dispatch's current production
  topology still spawns its own stdio App Server from the resolved `codex`
  binary; it does not silently inherit the managed daemon's older protocol.
- The June process survey also found per-thread desktop stdio servers and an
  SSH/proxy process for a remote host. Those observations were not re-probed in
  this schema refresh and remain transport research, not a supported Dispatch
  dependency.

Original 2026-06-02 behavioral probes ran against `codex-cli 0.136.0-alpha.2`
with a managed daemon reporting `appServerVersion:"0.135.0-alpha.1"`. Keep the
empirical findings below unless re-probed; update schema facts per binary.

The public Python SDK (`openai-codex 0.1.0b3`) still pins
`openai-codex-cli-bin==0.137.0a4`. Dispatch therefore does not depend on that
package; it continues to drive the installed `codex` binary directly.

## Biggest delta vs the report: daemon + proxy = attach to a running server

The report concluded there is "no way to connect to an already-running App Server." **False on 0.136.** New CLI surface:

- `codex app-server daemon` → `bootstrap | start | restart | stop | enable-remote-control | disable-remote-control | version`
  - `bootstrap` = "Install durable local app-server management for SSH-driven use"
- `codex app-server proxy --sock <PATH>` = "Proxy stdio bytes to the running app-server control socket"
- `--listen` documents `stdio://` (default) `unix://[PATH]` `ws://IP:PORT` `off`

Topology implication for tooling: instead of spawn-per-client (Python SDK's current model), run **one managed daemon** and have N clients attach by speaking JSON-RPC through `proxy` over the Unix control socket. This is the multi-client / remote-steering backbone the report could only treat as experimental.

## Authoritative wire inventory (from `generate-json-schema`)

Regenerate any time (read-only, no network):
```bash
codex app-server generate-json-schema --out <DIR>                # stable
codex app-server generate-json-schema --experimental --out <DIR> # + gated
```
Current 0.144.0 generated schema files: stable = 267, experimental = 337.
Current method counts: stable client requests = 87, experimental client requests
= 122, server requests = 10, server notifications = 68. Protocol is namespaced
`v1/` (just Initialize) + `v2/` (everything else). Initialize is still v1 and
returns `{codexHome, platformFamily, platformOs, userAgent}`. The compact,
generated inventory is checked in at
`tests/fixtures/app_server/protocol_manifest/current.json`; refresh it with
`just app-server-manifest`.

### Client → server methods (STABLE — non-experimental)
Lifecycle/threads/turns: `thread/start resume fork read list loaded/list archive unarchive delete unsubscribe metadata/update name/set rollback inject_items compact/start goal/{get,set,clear} approveGuardianDeniedAction shellCommand`, `turn/start steer interrupt`. Accounts/models/config: `account/{read,login/start,login/cancel,logout, usage/read,workspaceMessages/read,rateLimits/read,rateLimitResetCredit/consume,sendAddCreditsNudgeEmail}`, `model/list`, `modelProvider/capabilities/read`, `config/{read,value/write,batchWrite,mcpServer/reload}`, `configRequirements/read`, `permissionProfile/list`. Tools/ecosystem: `skills/{list,config/write,extraRoots/set}`, `plugin/{install,installed,list,read,uninstall,skill/read,share/*}`, `marketplace/{add,remove,upgrade}`, `hooks/list`, `app/list`, `review/start`, `mcpServer/{tool/call,resource/read,oauth/login}`, `mcpServerStatus/list`, `externalAgentConfig/{detect,import,import/readHistories}`. System: `command/exec{,/write,/resize,/terminate}`, full `fs/*` (`readFile writeFile readDirectory createDirectory copy remove getMetadata watch unwatch`), stable `fuzzyFileSearch`, `feedback/upload`, `experimentalFeature/{list,enablement/set}`, `windowsSandbox/{readiness,setupStart}`.

0.144 deltas Dispatch should care about:

- `thread/list` now accepts native `archived`, `cwd`, `searchTerm`,
  `modelProviders`, `sourceKinds`, and sort filters. Dispatch should prefer
  those when they match existing CLI/MCP semantics, then keep dispatch-side
  filters for managed/unmanaged and date predicates.
- `turn/start` accepts `serviceTier` plus richer context/environment metadata.
  Dispatch resolves explicit `service_tier` values before projecting them
  through configured `new` turns.
- `model/list` is the authoritative catalog for model ids, reasoning-effort
  support, input modalities, personality support, upgrade targets, and service
  tiers. Reasoning effort is now a model-defined non-empty string, not a closed
  protocol enum; current models advertise values including `max` and `ultra`.
  Prefer `serviceTiers` over the deprecated
  `additionalSpeedTiers`; user-facing labels such as `fast` can map to a
  server-facing tier id such as `priority` when the catalog advertises a tier
  named `Fast`.
- `permissionProfile/list` is stable and paginated with `{cwd,cursor,limit}`;
  rows are `{id,description,allowed}` under `result.data`. On the local
  `0.144.0-alpha.4` binary, `/dispatch` returned `:read-only`, `:workspace`, and
  `:danger-full-access`. Selecting a profile uses the experimental
  `permissions` field on thread/turn start and cannot be combined with the
  corresponding sandbox field.
- `config/read` reports the current Codex defaults (model/provider,
  reasoning effort, service tier). Dispatch records those defaults for output
  truth but does not send omitted model/tier values just to mirror config.
- `thread/resume` accepts experimental `excludeTurns` / `initialTurnsPage`.
  Dispatch now uses them for metadata-only live observation and a one-turn recent
  bootstrap, then continues through capability-gated `thread/turns/list` and
  `thread/items/list` cursors. The compact manifest guards all request/page fields.
- `ThreadItem` has 18 canonical variants in 0.144: user/hook/agent messages,
  plan, reasoning, command execution, file change, MCP/dynamic/collaboration
  tool calls, subagent activity, web search, image view/generation, sleep,
  review-mode entry/exit, and context compaction. Both `item/started` and
  `item/completed` carry the full item object under `params.item` plus
  `threadId` and `turnId`; the item id is not a top-level field in the current
  shape. The compact protocol manifest records these discriminants so additions
  cannot silently bypass Dispatch's disposition table.
  Live verification also showed that a later `thread/read(includeTurns:true)`
  can omit a completed `commandExecution` that was present in the live stream
  and can assign different persisted message ids. Treat thread reads as
  additive replay, not an authoritative deletion snapshot.
- `thread/fork.lastTurnId` forks history through a specific completed turn,
  inclusive. Dispatch exposes it as `last_turn_id` in the authored fork op and
  its derived MCP schema.
- `thread/list` responses now include `backwardsCursor`; thread rows include
  `recencyAt`, `cliVersion`, and subagent nickname/role metadata.
- Experimental `thread/list` accepts mutually exclusive `parentThreadId` and
  `ancestorThreadId` filters when the client initializes with
  `experimentalApi:true`. The parent filter returns direct spawned children;
  the ancestor filter returns all spawned descendants and excludes the
  ancestor itself.
- `parentThreadId` is the direct subagent-parent relation. The richer tagged
  `source.subAgent.thread_spawn` shape also carries depth, parent id, nickname,
  role, and path metadata. `forkedFromId` is a separate history-fork relation:
  ordinary `thread/fork` does not create a parent/descendant edge, and no
  `rootThreadId` field exists. `sessionId` is not a reliable tree identity for
  ordinary forks.
- Thread lifecycle notifications are asymmetric: `thread/started` carries the
  full nested thread object, while archived, unarchived, and deleted carry a
  `threadId`. Forking emits `thread/started`; there is no separate
  `thread/forked` notification.
- Stable account usage/workspace-message methods and permanent `thread/delete`
  exist, but Dispatch does not yet expose them. Credits and permanent deletion
  need explicit product/policy decisions rather than implicit pass-throughs.

### Account and capacity reads (0.144)

- `account/read` returns `{account, requiresOpenaiAuth}`. `account` is nullable
  and currently distinguishes `apiKey`, `chatgpt`, and `amazonBedrock` shapes.
  ChatGPT accounts may include email and plan; responses must be normalized
  without retaining raw auth payloads or credentials.
- `account/rateLimits/read` returns the historical `rateLimits` snapshot plus
  optional `rateLimitsByLimitId` multi-bucket snapshots and
  `rateLimitResetCredits`. Snapshots can include primary/secondary rolling
  windows, plan, reached reason, credit availability, and individual spend
  control. Reset-credit ids are opaque mutation handles and should be
  fingerprinted for read-only inventory rather than persisted raw.
- `account/usage/read` returns a summary and optional daily token buckets. The
  current summary includes lifetime tokens, streaks, peak daily tokens, and
  longest-running turn seconds.
- `account/rateLimits/updated` is a threadless notification carrying one
  `rateLimits` snapshot. It can refresh local capacity without polling on every
  command, but does not carry account, historical usage, multi-bucket, or reset
  credit details.
- A live-safe 0.144 probe confirmed all three reads work without starting a
  model turn. Dispatch stores a masked account label and deterministic
  fingerprints for account/credit identity; raw email, credit id, token fields,
  and raw auth responses are excluded.

### Client → server methods (EXPERIMENTAL-gated — diff stable↔exp)
`process/{spawn,kill,writeStdin,resizePty}` (unsandboxed; matches report's warning), `thread/{search,turns/list,items/list,settings/update,memoryMode/set,increment_elicitation,decrement_elicitation,backgroundTerminals/{clean,list,terminate}}`, `thread/realtime/{start,stop,appendAudio,appendSpeech,appendText,listVoices}`, `remoteControl/{enable,disable,status/read,pairing/{start,status},client/{list,revoke}}`, `collaborationMode/list`, `environment/{add,info}`, experimental `fuzzyFileSearch/session{Start,Update,Stop}`, `memory/reset`, `mock/experimentalMethod`.
> Note: `thread/turns/list` and `thread/search` are EXPERIMENTAL here — the report
> listed turns/list as Supported. The realtime *control* verbs are gated, but the
> realtime *notifications* below ship in stable.

### Server → client requests (block the agent loop until answered)
`item/commandExecution/requestApproval`, `item/fileChange/requestApproval`, `item/permissions/requestApproval`, `item/tool/call`, `item/tool/requestUserInput`, `mcpServer/elicitation/request`, `account/chatgptAuthTokens/refresh`, `attestation/generate`.

### Server → client notifications (reducer event grammar)
Turn/item: `turn/{started,completed,diff/updated,plan/updated,moderationMetadata}`, `item/{started,completed}`, `item/agentMessage/delta`, `item/reasoning/{textDelta,summaryTextDelta,summaryPartAdded}`, `item/commandExecution/{outputDelta,terminalInteraction}`, `item/fileChange/{outputDelta,patchUpdated}`, `item/plan/delta`, `item/mcpToolCall/progress`. Thread: `thread/{started,closed,archived,unarchived,deleted,compacted,status/changed,tokenUsage/updated,name/updated,settings/updated,goal/{set→updated,cleared}}`. NEW safety/automation layers absent from report:
- **Guardian / auto-approval review**: `item/autoApprovalReview/{started,completed}` + client method `thread/approveGuardianDeniedAction` + `GuardianWarningNotification`.
- **Hooks**: `hook/{started,completed}` + `hooks/list`.
- **Realtime (voice)**: `thread/realtime/{started,closed,error,itemAdded,sdp, outputAudio/delta,transcript/{delta,done}}` — ships in stable schema.
- **Remote control**: `remoteControl/status/changed`.
- **Fs watch**: `fs/changed`; **model**: `model/{rerouted,verification,safetyBuffering/updated}`; `serverRequest/resolved`; `skills/changed`; `app/list/updated`.

## What still holds from the report
- Python SDK is a real App Server client, but its public package currently pins
  the older `0.137.0a4` binary. Dispatch's direct installed-binary client remains
  the compatibility path.
- TS SDK = `codex exec --experimental-json` wrapper, not App Server.
- Pin the binary + generate schemas from it; treat experimental as moving ground.
- `thread/shellCommand` runs outside sandbox; `process/spawn` unsandboxed.

## Verified empirically this session (live probes, read-only, never-approve)

Drove the binary many ways. Scripts: `/tmp/codex_stdio.py` (stdio grammar), `/tmp/codex_ws.py` + `/tmp/codex_fanout.py` (multi-client), `/tmp/codex_lab4.py` (approvals/guardian, workspace-write in throwaway git repos). All read-only or isolated-temp-repo; the user's daemon + desktop sessions were never touched.

NB on running these: the **default model is a slow reasoner** — benign turns can exceed 120s and look "stuck" (lots of `item/agentMessage/delta`, no `turn/completed`). Pass `turn/start.effort:"low"` to speed iteration. Also use `python3 -u` (stdout block-buffers when redirected; lost on timeout kill).

### Transports — only stdio is bare JSONL
- **`stdio://` = newline-delimited JSON** (one msg/line). `initialize` → `{userAgent, codexHome, platformFamily, platformOs}`. This is the SDK + desktop transport and the only one that "just works" with raw JSON. ✅
- **`unix://` and `ws://` are WebSocket-framed**, NOT bare JSONL. Sending raw JSON bytes to a `--listen unix://` socket → server silently closes the connection (connection stays open on idle; closes the instant non-WS bytes arrive). Use a real WebSocket client. Loopback `ws://` needs **no auth** (auth mode applies only to non-loopback listeners: `capability-token` | `signed-bearer-token`).
- **Daemon control socket** (`~/.codex/app-server-control/…sock`): direct connect is **closed instantly** (auth/credential gate). The sanctioned client is `codex app-server proxy --sock <PATH>` — but feeding it JSONL produced **no passthrough reply** in my tests; the control socket speaks an undocumented wrapper/handshake, not the app-server JSON-RPC directly. Attaching external tooling to the *running daemon* is not a solved path yet — treat as volatile.
- `--listen unix://PATH` quirk: the socket's parent dir must be a **real directory**, not a symlink — `/tmp` (→`/private/tmp` on macOS) is rejected with "socket directory path exists and is not a directory". Use a real subdir.

### Turn grammar (captured from a real read-only turn, agent replied "pong")
```
thread/status/changed → turn/started → warning
 → hook/started → hook/completed            (×3 hook pairs)
 → item/started → item/completed            (×3 items; one carries
                                              item/agentMessage/delta = "pong")
 → thread/tokenUsage/updated → account/rateLimits/updated
 → turn/completed
```
- **Hooks fire in-stream even on a trivial read-only turn** (`hook/started`/ `hook/completed`) — hooks are live, not just schema.
- `thread/start` returns a rich `thread` object: `id, sessionId, forkedFromId, preview, ephemeral, modelProvider, createdAt, updatedAt, status, path, cwd, cliVersion, source, threadSource, agentNickname, agentRole, gitInfo, name, turns`.
- **Two different sandbox encodings (gotcha):** `thread/start.sandbox` = string enum `"read-only" | "workspace-write" | "danger-full-access"`; `turn/start.sandboxPolicy` = object `{type:"readOnly", …}`. Don't mix them (mixing → `-32600 unknown variant`).

### Approvals — full server→client loop (RESOLVED, both kinds captured)
Workspace-write + `approvalPolicy:"untrusted"` in a throwaway git repo. The agent **actually wrote files and ran commands** (verified on disk: `?? notes.txt`, contents `HELLO`).
- **Mechanism:** server sends a JSON-RPC *request* (has `id` + `method` + `params`) to the client; client replies on the same stream with `{"id": <same>, "result": {"decision": "accept"}}`. While pending, the thread emits `thread/status/changed → status.activeFlags:["waitingOnApproval"]` (clean blocking-UI signal). After the reply, server emits `serverRequest/resolved {requestId}` and clears the flag, then the item completes.
- **Command approval** — `item/commandExecution/requestApproval`: params include the literal `command` (e.g. `/bin/zsh -lc "printf 'HELLO\n' > notes.txt …"`), `cwd`, `reason`, plus optional execpolicy/network amendment proposals. Codex runs commands through a **zsh login shell** (`/bin/zsh -lc …`).
- **File-change approval** — `item/fileChange/requestApproval`: params are only `{itemId, threadId, turnId, grantRoot?, reason?}` — **the diff is NOT in the request.** You must correlate by `itemId` to the `fileChange` item, whose `item/completed` carries `changes:[{path, kind:{type:"add"|…}, diff}]`; live diff also streams via `turn/diff/updated`. Build approval UIs around this correlation.
- **Decision vocabulary** (command): `accept`, `acceptForSession`, `acceptWithExecpolicyAmendment{…}`, `applyNetworkPolicyAmendment{…}`, `decline` (agent continues), `cancel` (turn interrupted). File-change: `accept`, `acceptForSession`, `decline`, … Sandbox readOnly + `never` ⇒ no approvals fire.
- Selecting WHICH commands prompt: under `untrusted`, trusted reads (e.g. `cat`) may auto-run while writes prompt — so a single turn can mix approved + auto steps.

### Q3 — Guardian / auto-review is SELECTIVE (RESOLVED enough)
`approvalsReviewer:"auto_review"` did **not** intercept a benign in-workspace file write — the `item/fileChange/requestApproval` still routed to the client, and **zero** `item/autoApprovalReview/*` events fired. Matches the enum docs: auto_review (a risk-scoring subagent) targets *escalations* — sandbox escapes, blocked network, MCP prompts, ARC — not routine approvals. So enabling it does NOT blanket-replace user approval; the client must still implement an approval responder. (Triggering the reviewer subagent + its `autoApprovalReview` events would need a real risk-category action — e.g. network/sandbox escape — not yet exercised.)

### Q2 — multi-client on ONE server (RESOLVED: resume = live co-presence)
Two WS clients (A, B), one `ws://` server. **Earlier "partial fan-out" was wrong** — it failed only because the thread wasn't persisted. Correct picture:
- A `thread/start` with `ephemeral:false`, runs turn 1 **to completion** (persists a rollout). B connects afterward and `thread/resume(tid)` **succeeds**, returning the thread with its turn history (`turns` count = 1).
- A then runs turn 2 → **B co-receives the FULL content stream**: `turn/started`, `item/started`, `item/agentMessage/delta`, `item/completed`, `turn/completed`, hooks, tokenUsage (B saw all ~15 notifications mirroring A). **Resume = live subscription.** Multi-viewer dashboards are viable.
- **Discovery works fine** (earlier "unreliable" claim was MY bug): `thread/list` returns `result.data` (+ `nextCursor`, `backwardsCursor`) — **NOT `result.threads`**. Reading the right key, a fresh server listed 183 persisted threads and paged cleanly; `useStateDbOnly:true` reads the persisted store. Filter rows by `cwd`.
- Pre-persistence, `thread/resume` errors `"no rollout found for thread id"`; thread *status* still broadcasts across connections regardless.
- **`thread/start` defaults to `ephemeral:false` (PERSISTED).** Every test thread I started — even quick taps — persisted to the shared store and showed in Codex.app. Set `ephemeral:true` for throwaway probes, or archive after (`thread/archive` {threadId}; reversible via `thread/unarchive`; verify via list `archived:true`).

### Practical takeaway for tooling
- Build on **stdio JSONL** (what the Python SDK does) for single-client; it's the only auth-free, well-framed transport. For **multi-viewer**, use **`ws://` loopback** (no auth) with each viewer `thread/resume`-ing the shared persisted thread — confirmed to fan out live.
- An **approval responder is mandatory** for any write/command work (and it's the one thing the SDKs under-expose). Model it on the request/`{id,result}`/ `serverRequest/resolved` loop above; correlate file-change diffs by `itemId`.
- `auto_review` is a *safety augmentation for risky escalations*, not a substitute for the responder.
- Attaching to the user's *existing managed daemon* still isn't solved (control socket handshake undocumented); spawn your own stdio/ws server.

## Status of original open questions
- **Q1 transports — RESOLVED.** stdio=JSONL (build here); unix/ws=WebSocket; daemon control socket=auth-gated + undocumented handshake.
- **Q2 multi-client — RESOLVED.** Resume of a *persisted* thread = live co-presence (full content fan-out). `thread/list(useStateDbOnly:true)` discovery works when reading `result.data`; track ids/refs yourself for authority.
- **Q3 Guardian — RESOLVED (behaviorally).** auto_review is selective (escalations only); benign approvals still hit the client. Approval responder is mandatory.

## Remaining unknowns (lower priority / deliberately not chased)
1. The `proxy`/control-socket handshake — IS attaching to the live managed daemon supported for third parties at all? (Undocumented; volatile. Spawn-your-own is the pragmatic path regardless.)
2. Triggering `item/autoApprovalReview/*` — needs a real risk-category action (network/sandbox-escape/MCP) with `auto_review`; not yet exercised.
3. `thread/fork`, `thread/rollback`, `thread/compact/start` runtime semantics.
4. Python SDK (`openai-codex`) actual high-level API vs raw — it has lagged the installed CLI before, so evaluate its bundled binary before adopting it over the direct stdio client.

## Building blocks confirmed (ready to design against)
- Transport: stdio JSONL (1:1) or ws loopback (multi-viewer via resume).
- Lifecycle: initialize → initialized → thread/start → turn/start → stream → resume.
- Approval responder: reply `{id,result:{decision}}`; watch `waitingOnApproval` flag + `serverRequest/resolved`; correlate file diffs by `itemId`.
- Sandbox: thread `sandbox:"read-only|workspace-write|danger-full-access"`; turn `sandboxPolicy:{type:"readOnly|workspaceWrite|externalSandbox|dangerFullAccess"}`.
- Schema: regenerate per binary; current 0.144.0 generated 267 stable / 337
  experimental schema files. The checked-in compact manifest makes drift
  reviewable without committing hundreds of generated schema files.

Scratch this session: schema at path in `/tmp/codex-appserver-scratch-path`; probe scripts `/tmp/codex_{stdio,ws,fanout,lab4}.py`.
