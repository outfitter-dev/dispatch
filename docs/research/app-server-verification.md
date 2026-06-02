# Codex App Server — verification against local binary (2026-06-02)

Verifies the "Codex App Server Technical Report (§1–15)" against the actually installed binary. Report is accurate for its pinned era (~CLI 0.132); several conclusions are already stale on the newer local binary.

## Ground truth on this machine

- `codex-cli 0.136.0-alpha.2` at `~/.local/bin/codex`
- A **managed control daemon is already running**: `codex app-server daemon version` →
  ```json
  {"status":"running",
   "managedCodexPath":"~/.codex/packages/standalone/current/codex",
   "socketPath":"~/.codex/app-server-control/app-server-control.sock",
   "cliVersion":"0.136.0-alpha.2","appServerVersion":"0.135.0-alpha.1"}
  ```
- Desktop `Codex.app` separately spawns ~10 `codex app-server --listen stdio://` processes (one per thread) — so **both topologies run side by side** today.
- Python SDK (`openai-codex`) still pins `openai-codex-cli-bin==0.132.0`, i.e. installing it gives an **older** App Server than the local CLI. Pin deliberately.

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
Counts on this binary: stable v2 = 217 types, experimental v2 = 261. Protocol is namespaced `v1/` (just Initialize) + `v2/` (everything else). Initialize is still v1 and returns `{codexHome, platformFamily, platformOs, userAgent}`.

### Client → server methods (STABLE — non-experimental)
Lifecycle/threads/turns: `thread/start resume fork read list loaded/list archive unarchive unsubscribe metadata/update name/set rollback inject_items compact/start goal/{get,set,clear} approveGuardianDeniedAction shellCommand`, `turn/start steer interrupt`. Accounts/models/config: `account/{read,login/start,login/cancel,logout, rateLimits/read,sendAddCreditsNudgeEmail}`, `model/list`, `modelProvider/capabilities/read`, `config/{read,value/write,batchWrite, mcpServer/reload}`, `configRequirements/read`, `permissionProfile/list`. Tools/ecosystem: `skills/{list,config/write,extraRoots/set}`, `plugin/{install,installed,list,read,uninstall,skill/read,share/*}`, `marketplace/{add,remove,upgrade}`, `hooks/list`, `app/list`, `review/start`, `mcpServer/{tool/call,resource/read,oauth/login}`, `mcpServerStatus/list`. System: `command/exec{,/write,/resize,/terminate}`, full `fs/*` (`readFile writeFile readDirectory createDirectory copy remove getMetadata watch unwatch`), `externalAgentConfig/{detect,import}`, `feedback/upload`, `experimentalFeature/{list,enablement/set}`, `windowsSandbox/{readiness,setupStart}`.

### Client → server methods (EXPERIMENTAL-gated — diff stable↔exp)
`process/{spawn,kill,writeStdin,resizePty}` (unsandboxed; matches report's warning), `thread/{search, turns/list, turns/items/list, settings/update, memoryMode/set, increment/decrementElicitation, backgroundTerminals/clean}`, `thread/realtime/{start,stop,appendAudio,appendText,listVoices}`, `remoteControl/{enable,disable,status/read}`, `collaborationMode/list`, `environment/add`, `memory/reset`, `mockExperimentalMethod`.
> Note: `thread/turns/list` and `thread/search` are EXPERIMENTAL here — the report
> listed turns/list as Supported. The realtime *control* verbs are gated, but the
> realtime *notifications* below ship in stable.

### Server → client requests (block the agent loop until answered)
`item/commandExecution/requestApproval`, `item/fileChange/requestApproval`, `item/permissions/requestApproval`, `item/tool/call`, `item/tool/requestUserInput`, `mcpServer/elicitation/request`, `account/chatgptAuthTokens/refresh`, `attestation/generate`.

### Server → client notifications (reducer event grammar)
Turn/item: `turn/{started,completed,diff/updated,plan/updated}`, `item/{started,completed}`, `item/agentMessage/delta`, `item/reasoning/{textDelta,summaryTextDelta,summaryPartAdded}`, `item/commandExecution/{outputDelta,terminalInteraction}`, `item/fileChange/{outputDelta,patchUpdated}`, `item/plan/delta`, `item/mcpToolCall/progress`. Thread: `thread/{started,closed,archived,unarchived,compacted,status/changed, tokenUsage/updated,name/updated,settings/updated,goal/{set→updated,cleared}}`. NEW safety/automation layers absent from report:
- **Guardian / auto-approval review**: `item/autoApprovalReview/{started,completed}` + client method `thread/approveGuardianDeniedAction` + `GuardianWarningNotification`.
- **Hooks**: `hook/{started,completed}` + `hooks/list`.
- **Realtime (voice)**: `thread/realtime/{started,closed,error,itemAdded,sdp, outputAudio/delta,transcript/{delta,done}}` — ships in stable schema.
- **Remote control**: `remoteControl/status/changed`.
- **Fs watch**: `fs/changed`; **model**: `model/{rerouted,verification}`; `serverRequest/resolved`; `skills/changed`; `app/list/updated`.

## What still holds from the report
- Python SDK = the real App Server client (`CodexClient` launches `app-server --listen stdio://`, does `initialize`→`initialized`, exposes `approval_handler` defaulting to **auto-accept** — override before any writes).
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
- **Q2 multi-client — RESOLVED.** Resume of a *persisted* thread = live co-presence (full content fan-out). thread/list discovery unreliable; track ids yourself.
- **Q3 Guardian — RESOLVED (behaviorally).** auto_review is selective (escalations only); benign approvals still hit the client. Approval responder is mandatory.

## Remaining unknowns (lower priority / deliberately not chased)
1. The `proxy`/control-socket handshake — IS attaching to the live managed daemon supported for third parties at all? (Undocumented; volatile. Spawn-your-own is the pragmatic path regardless.)
2. Triggering `item/autoApprovalReview/*` — needs a real risk-category action (network/sandbox-escape/MCP) with `auto_review`; not yet exercised.
3. `thread/fork`, `thread/rollback`, `thread/compact/start` runtime semantics.
4. Python SDK (`openai-codex`) actual high-level API vs raw (it pins CLI 0.132, not local 0.136) — evaluate before adopting vs hand-rolling a stdio client.

## Building blocks confirmed (ready to design against)
- Transport: stdio JSONL (1:1) or ws loopback (multi-viewer via resume).
- Lifecycle: initialize → initialized → thread/start → turn/start → stream → resume.
- Approval responder: reply `{id,result:{decision}}`; watch `waitingOnApproval` flag + `serverRequest/resolved`; correlate file diffs by `itemId`.
- Sandbox: thread `sandbox:"read-only|workspace-write|danger-full-access"`; turn `sandboxPolicy:{type:"readOnly|workspaceWrite|externalSandbox|dangerFullAccess"}`.
- Schema: regenerate per binary; 217 stable / 261 experimental v2 types.

Scratch this session: schema at path in `/tmp/codex-appserver-scratch-path`; probe scripts `/tmp/codex_{stdio,ws,fanout,lab4}.py`.
