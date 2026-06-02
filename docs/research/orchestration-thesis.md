# Codex/Claude orchestration skills → proper tooling thesis (2026-06-02)

Companion to `2026-06-02-codex-app-server-0136-verification.md`. Read of Matt's existing orchestration skills (`~/.config/codex/skills/{delegate,delegate-init,delegate-goal,dm}` and the `crew` skill) plus what they imply for tooling.

## What the skills actually are

All of these encode **multi-agent orchestration as prompt conventions over a substrate** — there is no software enforcing them; the model is trusted to remember ~500 lines of rules.

- **`delegate`** (coordinator side): when/whether to spawn a background Codex thread vs a subagent; thread naming (`→ @project:name <context>`, bare `@Name` reserved for coordinators); tracker anchoring (PAT-###); source-control ownership grants + collision rules; callback contracts; heartbeat automations (`⏲️ @Name …`); closeout discipline (harvest → unpin → rename → archive → delete heartbeat).
- **`delegate-init`** (worker side): how a spawned lane identifies itself, sets its own title, and calls back to the coordinator thread id.
- **`delegate-goal`**: routes a delegated `/goal` (first message must start `/goal`, embeds `Use $delegate-init.`), picks goal-prompting vs goal-planning.
- **`dm`**: lightweight *synchronous* thread-to-thread Q&A (knock, ask, harvest) — explicitly not delegation.
- **`crew`**: the Claude-side analog — orchestrates Claude Agent Mode runs via the `crew` CLI over cmux + ZMX + worktrees.

## The key architectural insight: two layers, asymmetric substrates

**In-band (`codex_app.*` MCP tools):** what delegate/dm use. A model running *inside* a Codex thread orchestrates *other* Codex threads. App-level tools layered on App Server: `create_thread`, `list_threads`, `read_thread`, `send_message_to_thread` (the callback channel), `set_thread_title|pinned|archived`, `automation_update` (heartbeats). These map onto the App Server methods verified in the companion note (`thread/start`, `thread/list`, `thread/read`, `thread/name/set`, `thread/archive`, …).

**Out-of-band (raw App Server JSON-RPC):** what an *external* Python tool/daemon uses — what I reverse-engineered. Same primitives, but driven as real software from outside the agent.

**Codex has a clean programmatic substrate (App Server) that the in-band skills only partially exploit and that nothing external currently uses.** **Claude Agent Mode has *no* clean API**, so `crew` fakes one: `claude agents --json` + `crew run *` for facts, and **cmux UI automation** for control — literally typing into terminal surfaces, sending a literal space to open quick-reply, walking Agent-View section headers, verifying a "row-safe footer" before sending. The crew skill is saturated with "live testing showed…", probabilistic-delivery, and idle-is-ambiguous caveats. That fragility is the substrate gap, not bad skill-writing.

## Why this matters: conventions that want to be guarantees

The skills spend most of their length defending against failure modes that a real control plane would make impossible:
- **Heartbeat drift** (target ids not titles; narrow/delete promptly) → replace with **event subscription**. App Server *pushes* `turn/completed`, `thread/status/changed` (`waitingOnApproval`), item events. An external coordinator that resumes a thread gets reliable real-time signals (verified: resume = live fan-out) with zero polling and zero "delegate forgot to ping."
- **Title-as-truth churn / discovery** → a real registry keyed by thread id (the skills already say "titles are hints, use ids").
- **Callback reliability** (the delegate must remember to ping with a useful summary) → the coordinator reads the stream directly; the summary is derived, not begged for.
- **Source-control ownership / collision rules in prose** → enforce as code (which lane owns which branch/worktree; preflight dirty-state checks).
- **Approval handling** → a central **policy engine** (the responder loop I built): auto-allow safe, escalate risky to the human, audit every decision. The in-band model can't do this; the worker just runs under its approval policy.

## Tooling opportunity (what could be built)

1. **External orchestration control plane over App Server (Python).** A real coordinator process: spawn lane → registry by id; subscribe to each lane's event stream; central approval policy engine; source-control ownership as code; a dashboard of lanes + live timelines + pending approvals. Turns the `delegate` skill's conventions into enforced behavior. This is the highest-leverage build and sits directly on the verified App Server expertise.
2. **Unified `Lane` abstraction across runtimes.** delegate (Codex) and crew (Claude) implement the same verbs — spawn / identify / message / monitor / harvest / retire — with shared naming + callback + ownership semantics. One `Lane` interface, two backends: `CodexLane`→App Server (clean), `ClaudeLane`→crew/Agent SDK (messy but wrapped). Coordinator logic written once.
3. **Replace heartbeats with subscriptions** wherever the lane is a Codex thread — strict upgrade over the ~100 lines of heartbeat management.

## VERIFIED: cross-thread messaging primitives (2026-06-02 live test, `/tmp/codex_dm.py`)

Three distinct primitives; `send_message_to_thread` is NOT a protocol method (app-level), but it's reproducible externally:

- **DM / `send_message_to_thread` ≈ `turn/start` on the target thread.** Sent a message to an idle thread A via `turn/start`; A ran a turn and replied "ACK". So to deliver a message that the target *processes and answers*, start a turn on it with the message as `input`. The `From @X via dm:` envelope is just text convention. Worker→coordinator callbacks are the same thing in reverse: `turn/start` on the coordinator thread id.
- **`thread/inject_items` = silent context injection (no turn).** Injected a Responses-API user message (`{type:"message",role:"user",content:[{type:"input_text",text:…}]}`) into thread B; B did not run, but on its *next* turn it recalled the injected "codeword BANANA". Use this to seed a worker's model-visible history without triggering execution. Response body is empty.
- **`turn/steer` requires `expectedTurnId`** (`{threadId, expectedTurnId, input}`) — the active turn id from `turn/started`. This adds input to an in-flight turn. Delivering via `turn/start` to a *busy* thread was accepted (queued) rather than rejected, but steer is the precise "interject into the current turn" path.

Coordinator messaging toolkit, then: `turn/start`=deliver+process, `inject_items`=seed context, `turn/steer`=interject live. An external control plane reproduces the whole delegate/dm callback model with these three.

## VERIFIED: automations are filesystem TOML, daemon-scheduled (not protocol)

- **No `automation`/`cron`/`rrule`/`heartbeat` anywhere in the App Server protocol** (stable or experimental). No `codex automation|cron|schedule` CLI either (those fall through to the interactive CLI). So `codex_app.automation_update` is an app/daemon-level facility, not JSON-RPC.
- **Storage (decoded):** `~/.codex/automations/<id>/automation.toml` (+ optional `memory.md`). Fields:
  - common: `version=1`, `id` (=dir name), `kind` (`"cron"` | `"heartbeat"`), `name`, `prompt`, `status` (`"ACTIVE"` | `"PAUSED"`), `rrule`, `created_at`/`updated_at` (epoch ms).
  - `cron`: adds `model`, `reasoning_effort`, `execution_environment` (`"local"`), `cwds=[…]`; `rrule` uses full iCal form `"RRULE:FREQ=WEEKLY;BYHOUR=2;BYMINUTE=0;BYDAY=…"`. Spawns a NEW thread in a cwd with the prompt on schedule.
  - `heartbeat`: adds `target_thread_id` (the existing thread it fires INTO); `rrule` seen as bare `"FREQ=MINUTELY;INTERVAL=5"` (no `RRULE:` prefix). This is exactly the delegate heartbeat-into-coordinator mechanism.
- **Pickup caveat (tested):** wrote a PAUSED probe TOML; the running daemon did NOT touch/normalize it over 25s (same mtime+hash, no sibling files), then removed it. So the daemon does **not** live-watch the dir. Writing the file is necessary but **not confirmed sufficient** for live registration — pickup likely needs an app/daemon rescan (restart) OR the in-band `codex_app.automation_update` tool, which probably writes the file *and* registers it with the live scheduler/state-db. Confirm before relying on pure file-write for live scheduling.

## Remaining open questions

- **Automation live-registration:** does the desktop app rescan on focus, or only on restart? Is there a daemon-control-socket call (the uncracked handshake) that `automation_update` uses to register without restart? Determines whether external tooling can create a *live* automation by file-write alone.
- **`codex_app.*` vs raw App Server fidelity:** in-band `create_thread` etc. may do extra setup vs raw `thread/start`. Mostly cosmetic for our purposes (we reproduced the important behaviors), but worth a spot-check before claiming 1:1.
- **Busy-thread `turn/start` semantics:** confirmed "accepted" but not whether it queues-and-runs-after vs is dropped. Minor; coordinator targets are usually idle.
