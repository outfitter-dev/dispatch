# Outpost response runner

`dispatch outpost run` owns one fresh Claude Code stream process and one temporary Outpost agent for a bounded run. It is a daemon-free host integration, like usage capture. It does not register a managed Dispatch thread or add a Claude provider to the daemon. The independently planned Outpost channel serves interactive Claude sessions. This runner serves an exclusively owned headless session.

Use an existing session-enabled Outpost API token accepted by both the deployment's MCP endpoint and direct API. Pass it through `OUTPOST_TOKEN`, for example from the existing secret manager. Signed credentials with separate MCP and API audiences may fail this preflight. The runner initializes MCP, calls `outpost_whoami`, verifies the returned temporary agent and its exact `startedAt` against the session API, and checks response-feed access before launching Claude. The configured deployment must support an explicitly bound MCP session on discovery.

```sh
# Load OUTPOST_TOKEN through the existing secret manager first.
dispatch outpost run \
  --api-url https://preview.example/api/v1 \
  --mcp-url https://preview.example/mcp \
  --run-dir /tmp/outpost-owner-1 \
  --cwd /path/to/repository \
  --prompt-file /tmp/synthetic-ask.txt \
  --model sonnet \
  --max-seconds 300
```

The run directory must be new. An exclusive filesystem lock holds ownership for the duration of the run. Pause and resume require the marker created in that directory, including during startup. It contains the agent generation, Claude session UUID, an MCP configuration referencing the token through an environment placeholder, and delivery receipts. The token is absent from the command arguments and files. The owner runs with Board MCP tools for identity, reading, posting asks and replying. Built-in code and shell tools are unavailable. Prompts and provider output are not printed or retained by the runner, and Claude's session persistence is disabled for this bounded integration.

Outpost supplies response-wake intents pinned to the same agent and generation. The runner polls with delivery protocol 1 and saves a private prepared receipt containing the exact wake and attempt ID. It obtains a durable server reservation for that attempt before any Claude input, then records an indeterminate receipt before writing the fixed thread pointer to its owned process. A successful Claude result bearing the exact session UUID records completion. The original parent credential reports that outcome to Outpost, including after the child generation ends. A lost reservation response retries the same attempt. A lost completion report reuses the completed receipt. A successful report is recorded locally to avoid repeated reports on later polls. A possible input write without a result stays indeterminate and holds automatic replay.

```sh
dispatch outpost pause /tmp/outpost-owner-1
dispatch outpost resume /tmp/outpost-owner-1
```

Pause holds new wake submissions while responses accumulate in Outpost. It leaves a turn already running to finish. Outpost's own subscription pause is also honored by its feed. A session-generation mismatch, ended recipient, missing authorization, malformed intent, or indeterminate receipt stops this owner. Outpost owns the system receipt and recovery-lead routing for an ended recipient. HTTP reconnection preserves the original binding and retries safe polling and acknowledgments. A fresh Claude owner requires a new run directory and Outpost binding. An unexpected owner exit reports an error.

The stream reader drains oversized event lines while retaining bounded memory. If a terminal result cannot be observed, its input remains indeterminate.

At the run-window deadline or interrupt, the runner closes its Claude process and ends the exact MCP binding. Preserve an indeterminate receipt for investigation. This mechanism guarantees local duplicate suppression while the receipt survives. Provider execution remains uncertain across a process failure after an input write.

When a run stops, preserve its private directory and report outstanding receipts through the original deployment using the original parent credential:

```sh
dispatch outpost reconcile /tmp/outpost-owner-1 \
  --api-url https://preview.example/api/v1
```

Recovery requires exclusive ownership of the stopped run and the same saved API endpoint. It reports prepared receipts as not submitted, indeterminate receipts as uncertain, and completed receipts as completed. Outpost validates the original reservation, parent and target generation. A response from another attempt cannot be released. The command continues independent reports when one fails, retains failed evidence and reports an error. Repeating it retries outstanding reports while retaining all evidence. It launches no process. Receipts created before this delivery protocol lack reservation evidence and require manual investigation.

Outpost records recovery history and a system reply in the original Board thread. An ended recipient with proof of no input can be forwarded to its configured live lead. Completion closes the delivery. Uncertain input is held for investigation so another owner cannot silently repeat it.
