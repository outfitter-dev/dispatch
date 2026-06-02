# spikes

Exploratory scripts that probed the Codex App Server (`codex-cli 0.136.0-alpha.2`) to verify the primitives `dispatch` is built on. They are **reference + the seed of the integration suite** — Phase 1 of `PLAN.md` promotes them into `tests/` against a real ephemeral app-server. Preserved here from a verification session (they previously lived in `/tmp`).

Run ad hoc with: `python3 spikes/<file>.py` (drives a real `codex app-server`; uses your `~/.codex` unless edited — prefer an isolated `CODEX_HOME` when re-running).

| Script | Proves |
| --- | --- |
| `01_stdio_grammar.py` | stdio is bare newline-delimited JSON; full turn/item grammar (hooks fire in-stream); `thread/start.sandbox` is a string enum. |
| `02_messaging.py` | `inject_items` = silent model-visible context (no turn); DM = `turn/start` on target; `turn/steer` needs `expectedTurnId`. |
| `03_approvals_guardian.py` | command + file-change approval loop (`waitingOnApproval` → `{decision}` → `serverRequest/resolved`); file-change carries no diff (correlate by `itemId`); `auto_review` (Guardian) is selective. |
| `04_resume_fanout.py` | persisted-thread `thread/resume` yields full live event fan-out to a second connection; `thread/list` results are under `result.data`. |
| `05_ws_multiclient.py` | `unix://`/`ws://` are WebSocket-framed (not JSONL); loopback `ws://` needs no auth; multi-client connect. |

## Regenerate the protocol schema (per binary)

```bash
codex app-server generate-json-schema --out <dir>                # stable (217 v2 types)
codex app-server generate-json-schema --experimental --out <dir> # + gated (261)
```

Full written findings: see the research notes referenced in `.agents/plans/v0/REFS.md`.
