# dispatch Usage

This is the operator path for dispatch. It covers how to start the daemon, create
lanes, send work, add triggers, and expose the same op registry through MCP.

For implementation guidance, use [`AGENTS.md`](../../AGENTS.md). For design context, use
[`docs/development/design.md`](../development/design.md) and [`docs/adrs/`](../adrs/).

## Install And Run Locally

Install the CLI from PyPI:

```bash
uv tool install outfitter-dispatch
dispatch --help
dispatchd --help
command -v dispatch-claude-statusline
dispatch doctor
```

Upgrade or remove the installed tool with:

```bash
uv tool upgrade outfitter-dispatch
uv tool uninstall outfitter-dispatch
```

The PyPI package installs `dispatch`, `dispatchd`, and the opt-in
`dispatch-claude-statusline` capture helper, including the `dispatch mcp`
entrypoint. It also ships read-only copies of the first-party
`dispatch` and `dm` skills plus the local plugin bundle under the installed
`outfitter.dispatch.assets` package. Edit source assets in this repository under
`skills/` and `plugins/dispatch/`; installed copies are for setup and inspection.

Use this clean-machine smoke after installing or upgrading:

```bash
dispatch doctor
dispatch schema send
dispatch models --no-refresh
dispatch up --json
dispatch daemon status
dispatch down --json
```

Maintainers can run the same release smoke from the repository against the
published package:

```bash
just pypi-smoke -- --package-spec outfitter-dispatch==0.5.0
```

The smoke installs with `uvx --refresh-package outfitter-dispatch`, uses a
temporary `DISPATCH_HOME`, verifies the derived `models` schema, starts the
daemon, reads the live App Server model catalog, verifies the cached registry
read, checks the empty first-run lane list, and shuts the daemon down.

For an agent-level live scenario against the in-tree CLI, run:

```bash
just scenario -- tests/scenarios/basic_coordination.toml
```

This starts Dispatch with temporary `DISPATCH_HOME` and `CODEX_HOME`, creates
synthetic Codex lanes, waits for their turns to complete, verifies `list`/`get`
/`tail` state, then shuts the daemon down. It uses real Codex auth/model calls,
so it is intentionally separate from `just check`.

If `dispatch doctor` fails before the app-server smoke because the Codex CLI is
not installed or authenticated, fix that first and rerun the doctor. Use
`dispatch doctor --no-app-server` when you only need to inspect package, PATH,
daemon, and registry state without starting a Codex App Server process.

For development from this repo, use `uv`:

```bash
uv sync
uv run dispatch --help
uv run dispatchd --help
uv run dispatch doctor --no-app-server
```

Start the singleton daemon:

```bash
uv run dispatch up
uv run dispatch daemon status
```

After an upgrade, a running daemon may be older than the current CLI. If the CLI
requests an op that the daemon does not know, dispatch treats that as
daemon/client skew. When `dispatch daemon status` proves the daemon is idle, the
CLI restarts it automatically and retries the command once. If any lane is busy
or waiting on approval, dispatch leaves the daemon alone and asks you to restart
manually:

```bash
uv run dispatch down
uv run dispatch up
```

For foreground debugging, run the daemon directly:

```bash
uv run dispatchd run
```

Stop it when you are done:

```bash
uv run dispatch down
```

Runtime state defaults to `~/.dispatch`. Override it only when you need isolation:

```bash
DISPATCH_HOME=/tmp/dispatch-dev uv run dispatch up
```

The lower-level overrides are `DISPATCH_SOCKET`, `DISPATCH_DB`, and `DISPATCH_PIDFILE`.

## Shell Completions

Dispatch exposes completion scripts from the derived CLI surface:

```bash
uv run dispatch completion bash
uv run dispatch completion zsh
uv run dispatch completion fish
```

For ad hoc use, evaluate the generated script in your shell. For durable installs,
write it to your shell's completion directory.

## Doctor And Recovery

`dispatch doctor` is the first diagnostic command for users and agents. It returns JSON
by default and exits non-zero only when a check fails:

```bash
dispatch doctor
dispatch doctor --text
dispatch doctor --no-app-server
```

Checks include:

- PATH visibility for `dispatch` and `dispatchd`.
- `codex --version` and Codex auth-file presence without reading secret contents.
- daemon reachability, socket path, pidfile, and stale runtime files.
- registry database readability, SQLite `quick_check`, required tables, and schema version.
- packaged `dispatch`/`dm` skills and plugin MCP config.
- low-risk `codex app-server --listen stdio://` initialize smoke.

Common recovery paths:

- Missing `dispatch` or `dispatchd`: install with `uv tool install outfitter-dispatch`;
  if uv reports the tool is installed but the shell cannot see it, run
  `uv tool update-shell` and restart the shell/Codex context.
- Missing `codex`: install or expose Codex CLI, then verify `codex --version` in the
  same environment that will run dispatch.
- Missing Codex auth: run `codex login` or start Codex once. The doctor only checks
  for auth material; it does not print or parse credentials.
- Stale socket or pidfile: run `dispatch down`, then `dispatch up`. If you are using
  isolated state, confirm `DISPATCH_HOME`, `DISPATCH_SOCKET`, and `DISPATCH_PIDFILE`.
- Stale daemon/client op mismatch: dispatch restarts and retries once when the
  daemon is idle. If it reports active work, wait for the work to finish or
  explicitly run `dispatch down`, then `dispatch up`.
- Registry schema newer than the installed binary: upgrade with
  `uv tool upgrade outfitter-dispatch` before starting the daemon.
- Registry schema older than the installed binary: run `dispatch down`, then
  `dispatch registry migrate`, then `dispatch up`. Migration backs up the registry
  by default and refuses to run while the daemon is reachable unless
  `--allow-running` is explicitly set for a controlled recovery.
- Registry integrity failure: stop the daemon, back up the database at the path shown
  by doctor, and recreate it or inspect with `sqlite3`.
- App Server initialize failure: run `codex app-server --listen stdio://` directly in
  the same shell and fix the Codex CLI/auth problem before relying on thread operations.

## Release Publishing

`project.version` in `pyproject.toml` is the release trigger. Maintainers bump
that version (and regenerate `uv.lock`) on a PR. After the PR merges to `main`
and CI `check` is green, Actions cuts GitHub Release `v<version>` when that tag
does not already exist, then `workflow_dispatch`es `publish.yml`. GitHub does
not start other workflows from a `GITHUB_TOKEN` `release` event, so the
dispatch step is required for Trusted Publishing. `publish.yml` also still
runs from a manually published GitHub Release. It uploads to PyPI and then
confirms the version is installable with `just pypi-smoke -- --install-only`.

The PyPI pending/trusted publisher must match:

- project: `outfitter-dispatch`
- repository: `outfitter-dev/dispatch`
- workflow: `publish.yml`
- environment: `pypi`

Do not upload with a long-lived PyPI token unless the trusted publisher path is
unavailable and the maintainer explicitly chooses that fallback. If a release
tag already exists, a later `main` push is a no-op for publishing.

The full daemon/App Server smoke still needs a local Codex install. After PyPI
has the version, run:

```bash
just pypi-smoke -- --package-spec outfitter-dispatch==<version>
```

The smoke refreshes the package under test in uv's cache, so an immediate
post-publish check does not reuse an early "version not found" resolver result.
Use `just release-status` to see whether the current tree would cut a GitHub
Release.

## Lanes

An owned lane is a Codex thread created by dispatch. Owned lanes are writable. Use
`new` for the configured creation workflow:

```bash
uv run dispatch new \
  --name docs-review \
  --cwd /path/to/dispatch \
  --goal "Review until no P2 findings remain." \
  --text "Review the README for missing usage steps." \
  --image ./current-layout.png
```

`new` reads global `~/.dispatch/config.toml` settings first and then the nearest
repo `.dispatch/config.toml`, applies presets left-to-right,
decorates the name with the configured prefix, starts the lane, and sends the initial
message when `--text` is present. Use `--no-send` to create/configure the lane without
starting a turn:

```bash
uv run dispatch new --name docs-review --preset reviewer --no-send
```

Use `new --no-send` when you want to create the lane first and send later:

```bash
uv run dispatch new --name docs-review --cwd /path/to/dispatch --no-send
uv run dispatch list
uv run dispatch send <dispatch-ref> "Review the README for missing usage steps."
```

Every managed thread gets a dispatch-local `ref`, for example `0k7M4a`. Use refs
for day-to-day commands. The full Codex thread id is still the canonical global
identity and is accepted everywhere. Titles and `@handles` are mutable labels;
they are convenient, but not stable identity.

Example `.dispatch/config.toml`:

```toml
[defaults]
sandbox = "read-only"
approval_policy = "never"
prefix = "[${DISPATCH.CWD.REPO}]"

[defaults.instructions]
developer_file = ".dispatch/instructions/default.md"

[presets.reviewer]
effort = "high"
developer_file = ".dispatch/instructions/reviewer.md"

[presets.builder]
sandbox = "workspace-write"
approval_policy = "on-request"
developer_file = ".dispatch/instructions/builder.md"

[presets.fast]
service_tier = "fast"
effort = "low"

[presets.safe-profile]
permission_profile = ":read-only"
```

Preset order matters: global settings load first, repo settings override them,
later presets win, and CLI flags win over presets. Omit permission-profile,
sandbox, approval, model, and service-tier fields unless you intentionally
want Dispatch to send explicit overrides. When these fields are omitted, Dispatch
omits them from `thread/start` and the initial `turn/start` so Codex/App Server can
apply its global, profile, and project-local configuration. Dispatch still records
the configured model defaults reported by `config/read` when available.

Use `models` before pinning model or service-tier presets:

```bash
uv run dispatch models
uv run dispatch models --no-refresh
uv run dispatch schema models
```

`models` refreshes from App Server `model/list` by default and reports the
configured default from `config/read`, every page of the catalog, each model's
reasoning efforts, input modalities, personality support, upgrade target,
service tiers, and aliases. Reasoning efforts are model-defined strings; do not
assume the old `low` through `xhigh` set covers current models. For example, the
user-facing `fast` alias resolves through the advertised service tier named
`Fast` and may send `serviceTier:"priority"` to the App Server. If a requested
model, effort, or tier is unavailable, `new` fails before starting the thread
and prints the available choices.
`--no-refresh` reads the local catalog cache plus current config defaults. On a
first run, an empty cache reports `catalog_state: "empty"` plus a hint to run
`dispatch models` without `--no-refresh`.

Discover project-aware Codex permission profiles before pinning one in a preset:

```bash
uv run dispatch permissions --cwd /path/to/repo
uv run dispatch permissions --cwd /path/to/repo --include-disallowed
uv run dispatch permissions --cwd /path/to/repo --no-refresh --json
uv run dispatch schema permissions
uv run dispatch new --name review --permission-profile :read-only --no-send
```

`permissions` reads every `permissionProfile/list` page and stores only profile
id, description, allowed state, scope, source, and freshness. A named profile is
mutually exclusive with `sandbox`, `approval_policy`, and `approvals_reviewer`;
omit all four to inherit Codex defaults. Invalid or disallowed profiles fail
before thread creation with the currently allowed choices. Older App Servers
report `catalog_state: "unsupported"` rather than fabricating profiles.

Codex permission profiles and Dispatch interactive-request policy are separate
mechanisms. A Codex profile selects the provider's effective filesystem and
execution permissions for a thread or turn. Dispatch `[policy]` settings decide
how this daemon answers inbound approval, elicitation, and permission requests;
they do not select or redefine a Codex permission profile.

Inspect provider account and capacity inventory with the authored `usage` read:

```bash
uv run dispatch usage
uv run dispatch usage --no-refresh --json
uv run dispatch usage --provider codex --host local
uv run dispatch usage --provider claude --host local
uv run dispatch usage --all-hosts --no-refresh
uv run dispatch usage --include-daily --stale-after-seconds 300
uv run dispatch schema usage
```

`usage` refreshes supported local Codex and Claude providers independently,
then reads the provider-neutral observation store. One unavailable provider
does not hide the other. The default response is compact: account type, masked
label and fingerprint, plan, Claude CLI version, aggregate runtime counts,
capacity windows,
reset-credit availability, usage summary, source, confidence, and component
freshness. Daily buckets are bounded and opt-in with `--include-daily`.
`--no-refresh` makes the command a local database read; provider, host, and
config-scope filters also work for future mesh observations without changing
the contract. The default host is `local`; use `--all-hosts` to inspect every
observed machine.

Claude refresh uses only `claude auth status --json`, `claude --version`, and
`claude agents --json`. It persists a masked/fingerprinted account identity and
aggregate agent state counts, not the roster: cwd values, agent/session ids, agent names,
raw command output, auth files, cookies, and tokens are excluded. Claude
capacity windows remain absent until a supported statusline snapshot has been
captured; account/runtime state can still be `ready` without capacity data.
Dispatch does not run `claude daemon status` because its free-form output
includes local paths and operational detail unnecessary for this inventory.

### Capture Claude capacity from a statusline

Claude Code sends supported subscriber rate-limit fields to statusline commands
after the first API response in a Pro/Max session. Dispatch provides a narrow
stdin capture helper:

```bash
dispatch-claude-statusline
```

The helper emits no statusline text. It atomically writes only a bounded,
normalized snapshot to
`$DISPATCH_HOME/providers/claude/statusline.json` (default
`~/.dispatch/providers/claude/statusline.json`): capture time, Claude Code
version, a fingerprint of the session id, bounded model label, and the
`five_hour`/`seven_day` percentages and reset times. Raw stdin, cwd, transcript
path, model id, and raw session id are never retained. Missing `rate_limits` is
recorded as unavailable because the field is absent before the first API
response and may be absent for non-subscriber sessions.

Dispatch never edits `~/.claude/settings.json`. To opt in without replacing an
existing statusline, create a wrapper you control that feeds the same JSON to
both commands:

```bash
#!/bin/sh
input="$(cat)"
printf '%s' "$input" | dispatch-claude-statusline
printf '%s' "$input" | "$HOME/.claude/existing-statusline.sh"
```

Then manually point Claude Code's `statusLine.command` at that wrapper. If no
statusline exists yet, the wrapper can render any text you prefer after the
capture call. A fresh snapshot is merged into `dispatch usage`; a missing or
stale snapshot never erases the last valid capacity windows. The undocumented
`/api/oauth/usage` endpoint is intentionally not used.

States are explicit: `ready`, `partial`, `signed_out`, `disabled`,
`unsupported`, and `unavailable`. `stale` is separate from state and is computed
from component and per-window timestamps using `--stale-after-seconds`; a
rate-limit push does not make older account or historical usage facts look
fresh. JSON never includes raw email or organization ids, tokens, raw
auth/roster responses, credit balances, or reset-credit mutation ids. Reset
credits are inventory only; Dispatch does not redeem them.

Use `--goal` to create a native App Server goal before the initial message is sent.
Slash commands in `--text` are not interpreted by dispatch; `--text "/goal ..."`
is rejected so agents do not accidentally create a thread that looks goal-driven but
has no native goal state. Goals require non-ephemeral threads, so `new --goal`
cannot be combined with `--ephemeral`.

The `new` response reports `message_accepted` and `goal_set`. `message_accepted`
means the App Server accepted the initial turn request; it does not prove the
assistant produced work. Use `get` to inspect `latest_turn`, `tail` for persisted
history, or `watch` for a bounded live event sample after launch.

### Launch Packets And File Inputs

For durable, repeatable launches (especially parallel worker lanes), point `new`
at a **launch packet** — a directory of files instead of one-off shell strings:

```text
packet/
  dispatch.toml          # safe subset of new settings (permission/model/effort/...)
  goal.md                # native App Server goal (== --goal-file)
  prompt.md              # initial turn text (== --input-file)
  output.schema.json     # JSON Schema for structured turn output
  base.md                # thread baseInstructions
  developer.md           # thread developerInstructions
  hooks/                 # staged hook files (dispatch never executes these)
  codex/                 # staged Codex config files (staged, not applied)
```

```bash
uv run dispatch new --name lane-a --cwd /repo --packet ./packet
```

You can also pass file inputs directly, or read one from stdin with `-`:

```bash
uv run dispatch new --name lane-a --cwd /repo \
  --goal-file goal.md --input-file prompt.md --output-schema-file out.schema.json

printf 'Review until no P2 findings remain.' \
  | uv run dispatch new --name lane-a --goal-file - --input-file prompt.md
```

Precedence per slot is **inline flag > explicit file > packet > repo config**, so a
CLI input (`--goal` or `--goal-file`) overrides a packet's `goal.md`, which
overrides repo config. The inline flag and its file form are mutually exclusive:
`--goal`/`--goal-file` (and `--text`/`--input-file`) cannot both be set, and at
most one input may come from stdin (`-`). `goal.md` becomes a native goal — it is not
`/goal` slash-command text.

The initial turn may include images alongside text. Repeat `--image PATH` for local files and `--image-url HTTPS_URL` for remote images; `--image-detail auto|low|high|original` applies to all images in the invocation:

```bash
uv run dispatch new --name visual-review --cwd /repo \
  --text "Compare these states." \
  --image ./before.png --image ./after.webp \
  --image-url https://example.test/reference.jpg \
  --image-detail high
```

Local images must be PNG, JPEG, GIF, or WebP files no larger than 20 MiB. Remote images must use HTTPS and resolve only to public network addresses. Dispatch fetches remote references without redirects, validates the resulting image under the same 20 MiB bound, and sends an ephemeral inline image because App Server 0.144 does not fetch ordinary remote URLs itself. Neither local nor remote image bytes are stored in the Dispatch database. Dispatch validates image-capable model selection before starting the turn; omit `--model` to let Codex choose its default from the live catalog.

Use `--dry-run` to resolve a launch and print exactly what *would* happen, with no
daemon or thread mutation. The plan reports the resolved cwd, effective settings,
per-input sources (origin + byte count + SHA-256), and whether a turn would start:

```bash
uv run dispatch new --name lane-a --cwd /repo --packet ./packet --dry-run --json
```

### Staged Session Directories

`--stage` writes durable copies of packet parts into the launched cwd at
`.agents/sessions/<ref>/` (alongside an empty `scratch/` and a `state.json`
manifest), so the worker lane and repo tooling can read the launch from disk.
Staging is additive — protocol fields (goal/prompt/schema/instructions) are still
delivered inline; the staged files are durable twins built from the same bytes.

```bash
uv run dispatch new --name lane-a --cwd /repo --packet ./packet --stage all
uv run dispatch new --name lane-a --cwd /repo --packet ./packet --stage prompt,goal
uv run dispatch new --name lane-a --cwd /repo --packet ./packet --stage all --inline prompt
```

Stageable parts: `config`, `goal`, `prompt`, `output_schema`, `base`, `developer`,
`hooks`, `codex_config`. `--stage all` stages every available part; a comma list
stages a subset; `--inline` removes parts from the staged set. Staging refuses to
overwrite an existing `.agents/sessions/<ref>/` and is atomic (a half-written
packet is never visible). If staging fails after the lane is created, the lane is
left registered and marked `error`, and the first turn does not start.

Dispatch **stages** `hooks/` and `codex/` files but never executes hooks or applies
Codex config — execution and trust remain Codex's authority. The current App Server
has no native worktree request; Dispatch's `--worktree create` helper is a vanilla
git preflight, not a Codex protocol feature.

For worktree-backed lanes, pass the exact `--cwd` you want Dispatch to launch in
and treat the runtime checkout as authoritative. Do not depend on a fixed Codex
worktree layout such as `.codex/worktrees/<run>/<lane>` or
`~/.config/codex/worktrees/<name>`; Codex-managed worktrees may be detached or
unnamed, and an empty `git branch --show-current` is not automatically a failure.
Verify identity with `pwd`, `git rev-parse --show-toplevel`,
`git rev-parse --short HEAD`, `git status --short`, and any repo-provided runtime
or workspace doctor command. If a repo provides `.codex/environments/environment.toml`,
setup/teardown hooks, or bootstrap scripts, repo-local tooling owns those
semantics; Dispatch only stages files and reports the lane/ref/cwd facts.

### Workspace Preflight

Use `--workspace` when you want Dispatch to resolve repo-local workspace metadata before
creating the thread:

```bash
uv run dispatch new --name lane-a --cwd /repo --packet ./packet --workspace auto --dry-run --json
uv run dispatch new --name lane-a --cwd /repo --packet ./packet --workspace auto --stage all --json
uv run dispatch new --name lane-a --cwd /repo --packet ./packet --workspace none
```

`--workspace none` preserves the normal exact-`--cwd` launch path. `--workspace auto`
looks for `.codex/environments/environment.toml` from the launch cwd/repo root and
reports the environment name, version, setup script, cleanup script, repo root, and
effective cwd. If no supported metadata exists, it is a no-op with `state:
"not_found"` in JSON output.

Use `--worktree create` when Dispatch should create a vanilla git worktree before
launch:

```bash
uv run dispatch new --name lane-a --cwd /repo --worktree create --dry-run --json
uv run dispatch new --name lane-a --cwd /repo --worktree create --worktree-branch dispatch/lane-a --json
uv run dispatch new --name lane-a --cwd /repo --worktree create --worktree-path /tmp/lane-a
```

By default, Dispatch-created worktrees live under
`~/.dispatch/worktrees/<repo>/<lane>/`. Override the root with
`DISPATCH_WORKTREE_ROOT` or pass an explicit `--worktree-path`. Dispatch does not use
repo-local `.dispatch/worktrees/` by default, and it does not mimic Claude/Codex
private worktree layouts. The JSON output reports the exact worktree path, branch,
base ref, source repo, and head used for the branch.

Worktree defaults can live in repo `.dispatch/config.toml` workspace settings, with
CLI flags winning:

```toml
[workspace]
default = "auto"
worktree = "create"
worktree_branch = "dispatch/default"
worktree_base = "HEAD"

[workspace.presets.athena]
mode = "auto"
worktree = "create"
worktree_branch = "dispatch/athena"
```

Relative `worktree_path` values in `.dispatch/config.toml` resolve from the repo
configuration root. Prefer the default global root or explicit absolute paths for
cross-machine packets.

Default branch naming is `dispatch/<lane-slug>`. If the branch is already checked
out in another worktree, launch fails before thread creation with the owning
worktree path. If the branch does not exist, Dispatch creates it from
`--worktree-base` (default `HEAD`); if it exists and is not checked out elsewhere,
Dispatch checks it out in the new worktree.

The first supported environment file shape is:

```toml
version = 1
name = "repo-name"

[setup]
script = "./scripts/bootstrap.sh codex"

[cleanup]
script = "./scripts/bootstrap.sh teardown"
```

Discovery is automatic, but setup execution is not granted by packet files. A setup
script runs only when explicitly requested with `--workspace-setup run` or allowed by
local daemon policy:

```toml
[policy]
allow_workspace_setup = true
workspace_setup_timeout_seconds = 120
```

Setup runs before `thread/start`; a failing or timed-out setup prevents thread
creation. Dry runs never execute setup. Launch JSON includes bounded stdout/stderr
tails so operators can see what happened without treating Dispatch as a full
workspace lifecycle manager.

Use `send --context` for silent context injection. It adds model-visible context without
starting a turn:

```bash
uv run dispatch send 019ead04-d2f4-77e2-acf7-f34d25456fa8 "Picking this up."
uv run dispatch send @docs-review "Context: check lane capabilities before writing." --context
```

Rich image input is available for normal sends, `--steer`, `--queue`, and `--interject`. Text may be positional, read from a file, or read from stdin with `--input-file -`:

```bash
printf 'Inspect this screenshot for regressions.' \
  | uv run dispatch send @docs-review --input-file - --image ./screen.png

uv run dispatch send @docs-review "Compare both references." \
  --image-url https://example.test/expected.png \
  --image ./actual.png --image-detail high --steer
```

`--image` and `--image-url` are repeatable. Local files have the same PNG/JPEG/GIF/WebP and 20 MiB limits as `new`; URLs must use HTTPS and resolve only to public addresses. Dispatch ignores environment proxies, does not follow redirects, pins the validated address for the TLS connection, and gives all remote images in one delivery a shared 15-second fetch deadline. Image input is intentionally rejected with `--context` because the App Server's silent `thread/inject_items` image shape is not yet a verified Dispatch contract. Use a normal send when the model needs to inspect an image.

Use `send --steer` only while the lane has an active turn:

```bash
uv run dispatch send @docs-review "Focus on operator docs first." --steer
```

Use `send --interject` to cancel the active turn and start a replacement message:

```bash
uv run dispatch send @docs-review "Stop that and focus on operator docs first." --interject
```

Use `stop` to cancel the active turn without sending replacement text:

```bash
uv run dispatch stop <dispatch-ref>
```

Use `send --queue` when delivery should wait for the lane to become idle. The message is
stored in dispatch's durable registry and starts one queued turn per idle transition:

```bash
uv run dispatch send @docs-review "Run this after the active turn." --queue
```

Queued images are durable references, not copied blobs: Dispatch stores the normalized local path or HTTPS URL plus bounded validation metadata, never image bytes. It revalidates local files, public URL resolution and content, and model image support at delivery time, so a missing or invalid image fails clearly instead of starting a malformed turn.

Use `send --intro` for managed Codex-to-Codex coordination. It appends the
standard visible Dispatch attribution footer with a Codex thread link and reply
hint. The sender is derived from `CODEX_THREAD_ID`, so the current Codex thread
must already be managed by dispatch:

```bash
uv run dispatch send @docs-review "Can you sanity-check this?" --intro
```

```text
Can you sanity-check this?

dispatch (dm): [@Dispatch](codex://threads/<thread-id>) `<ref>`
↳ reply `dispatch send <ref> "..."`
```

## Inbox And Subscriptions

Subscriptions are event-to-inbox bindings. When a target lane matches a condition,
Dispatch records a durable inbox message for the subscriber. Delivery can stop there
(`delivery:inbox`) or bridge the inbox message into the existing queued-turn path
(`delivery:turn`).

The default compact subscription is:

```text
when:done,to:self,delivery:turn,deliver:idle,tail:1,once:true,ack:auto,attribution:true
```

That default uses `delivery:turn` only when the subscriber is writable. If `self`
resolves to an attached/read-only lane, Dispatch falls back to `delivery:inbox` so
the subscription can still collect durable updates. Explicit `delivery:turn` still
fails unless attached writes are enabled. `self` is derived from `CODEX_THREAD_ID`,
so the current Codex thread must already be managed by Dispatch. Use explicit
`--to <ref>` when one lane is subscribing on behalf of another.

```bash
uv run dispatch subscribe @worker
uv run dispatch subscribe @worker when:done,delivery:inbox
uv run dispatch subscribe @worker --when approval --delivery inbox --repeat
uv run dispatch new --name worker --cwd /repo --text "Do it." --subscribe
uv run dispatch new --name worker --cwd /repo --text "Do it." --subscribe when:done,to:self
uv run dispatch new --name worker --cwd /repo --text "Do it." --subscribe-spec when:done,to:self
```

Useful `when` buckets:

- `done`: completed or failed turns.
- `completed` / `failed`: one terminal turn outcome.
- `approval`: command, file-change, and permission approval requests.
- `needs-attention`: approvals plus user-input, elicitation, and dynamic-tool requests.
- `idle`: idle status events.
- `activity`: any tracked lane event.

`tail:1` includes the latest message by default. Use `tail:0` when the notification
should only carry event metadata. `once:true` marks the subscription done after the
first match; use `--repeat` or `once:false` for ongoing subscriptions.
Turn-delivered updates append the standard visible Dispatch attribution footer by
default:

```text
Latest message:
...

dispatch (sub): [@worker](codex://threads/<thread-id>) `<ref>`
↳ completed | done
```

Use `attribution:false` in the compact spec or `--no-attribution` to suppress the
footer when a subscriber needs the compact legacy body.

Inbox commands are JSON-shaped and jq-friendly:

```bash
uv run dispatch inbox list
uv run dispatch inbox list --lane <dispatch-ref> --state pending
uv run dispatch inbox read <message-id>
uv run dispatch inbox ack <message-id>
uv run dispatch inbox ack --all
uv run dispatch subscriptions
uv run dispatch unsubscribe <subscription-id> --yes --json
```

## Interactive Requests

App Server requests block a turn until the client responds. Dispatch classifies every current stable request, records a compact request lifecycle without raw payloads or credentials, and gives each request a local integer id. Requests that need a person appear in the target thread's inbox and in `needs-attention` subscriptions.

```bash
uv run dispatch request list
uv run dispatch request list --lane <dispatch-ref> --state pending --json
uv run dispatch request respond 17 '{"decision":"decline"}' --json
uv run dispatch schema "request respond" | jq '.input, .output'
```

`request list` includes an `expected_response` example for each answerable category. `request respond` accepts one JSON result object and can win the response claim only once; a timeout, automatic policy decision, or another operator response makes later attempts fail without sending a second wire response.

The small near-term policy lives in `~/.dispatch/config.toml`:

```toml
[policy]
owned_interactive_requests = "attention"    # attention | deny | permissive
attached_interactive_requests = "deny"      # attention | deny | permissive
interactive_request_timeout_seconds = 60
```

One-shot overrides use `DISPATCH_OWNED_INTERACTIVE_REQUESTS`,
`DISPATCH_ATTACHED_INTERACTIVE_REQUESTS`, and
`DISPATCH_INTERACTIVE_REQUEST_TIMEOUT_SECONDS`.

Owned threads default to durable operator attention. Attached and unmanaged threads default to deny because Dispatch does not own their writer. `permissive` accepts command/file approvals and grants the exact permission profile requested for an owned thread; it does not invent user answers, execute unknown dynamic tools, mint auth tokens, or generate attestation. Unsupported and threadless host requests receive an explicit JSON-RPC error and are audited without storing credential material. Attention timeouts decline/cancel where the protocol provides a safe result and otherwise return an explicit timeout error.

Use `delivery:inbox` when you want a durable message bus without waking the
subscriber. Use `delivery:turn` when the subscriber should start a new turn after the
target stops or needs attention.

## Thread History, Watch, And Goals

`get` is the compact managed-thread summary:

```bash
uv run dispatch get <dispatch-ref>
```

The response includes `latest_turn` when dispatch has observed turn lifecycle events:
the latest turn id, runtime status (`started`, `completed`, or `failed`), and the
last App Server error message/time when a turn fails. This is the first place to look
when a send or initial message was accepted but no assistant output appears.

Use `tail` when you want persisted turn history. It reads `thread/read` with
`includeTurns:true` and returns a compact item list; it is a history snapshot, not a
full execution log. App Server does not support `includeTurns` on ephemeral threads.

```bash
uv run dispatch tail <dispatch-ref> --limit 50
```

Use `history` when you want transcript inspection and rollups rather than only recent
items. Bare `history` summarizes managed lanes; passing a selector drills into one
thread and can show summary, items, tools, or files. Overview rows include indexed
transcript size, estimated tokens, active dates, deduped tool names, best-effort git
worktree identity, and dirty changed-file names from the lane cwd. Item views
filter by `--type`, `--role`, `--phase`, `--tool`, `--tool-server`,
`--tool-status`, `--errored/--not-errored`, `--mentions-thread`, `--arg-key`,
and `--grep`; `--cwd`, `--source`,
`--status`, `--has-tool`, `--changed/--clean`, and `--min-bytes` filter overview
rows; `--raw` includes raw App Server item payloads for jq-heavy inspection.
Bare `history` reads the local normalized index only, so overview scans stay
bounded and do not wake every App Server thread. When selector-scoped `history`,
`tail`, or transcript-inclusive `get` read `thread/read` with turns, Dispatch
backfills its local normalized history index with turns, items, tool/file refs,
and compact retained item payloads for that one thread. Normal `history`
item/tool/file views render from that normalized index after the refresh, so
capture byte caps and provider-neutral refs are honored consistently. The App
Server remains the canonical transcript source, and `history --raw` intentionally
uses the live App Server payload for jq-heavy inspection.

```bash
uv run dispatch history
uv run dispatch history <dispatch-ref>
uv run dispatch history <dispatch-ref> --view tools
uv run dispatch history <dispatch-ref> --view files
uv run dispatch history <dispatch-ref> --view items --tool bash --grep "git status" --raw
uv run dispatch history <dispatch-ref> --view items --tool-server linear --tool-status completed --arg-key id
uv run dispatch history <dispatch-ref> --view items --mentions-thread 019f
uv run dispatch history --has-tool bash --changed --min-bytes 100000
```

Use `watch` for a bounded live event sample from dispatch's app-server stream.
It returns raw App Server method names and params for the selected lane until `--limit`
events arrive or `--timeout` elapses. It is intentionally bounded because the current
control socket is request/response JSONL, not a subscription protocol.

```bash
uv run dispatch watch <dispatch-ref> --limit 20 --timeout 10
```

Native App Server goals can be read, set, and cleared on owned lanes:

```bash
uv run dispatch goal status @docs-review
uv run dispatch goal set @docs-review "Review until no P2 findings remain."
uv run dispatch goal clear @docs-review
```

Creating a goal requires an objective. After a goal exists, `goal set` can update
`--status` or `--token-budget`. App Server goals require non-ephemeral threads.

`tail --follow` is not canonical; use `watch`. True long-lived streaming will use a
future subscription-capable watch surface.

## Thread Actions And Search

`rename`, `archive`, and `restore` are top-level thread actions. They accept a managed
dispatch ref, a full Codex thread id, or a unique convenience label:

```bash
uv run dispatch rename <dispatch-ref> docs-review-final
uv run dispatch archive <dispatch-ref>
uv run dispatch restore <codex-thread-id>
```

`restore` unarchives the thread only; it does not resume the thread or start a new turn.
Destroy-intent commands prompt on a TTY. In scripts, use `--yes --json`; if you also
set `--no-interactive`, `--yes` is required or the command exits with a usage error.

Use `search` to ask the Codex App Server to search thread history without first
attaching every thread:

```bash
uv run dispatch search "schema drift"
uv run dispatch search "schema drift" --managed
uv run dispatch search "schema drift" --unmanaged
uv run dispatch search "schema drift" --thread <dispatch-ref>
uv run dispatch search "schema drift" --repo .
uv run dispatch search "schema drift" --dir /path/to/project
uv run dispatch search "schema drift" --since 2026-06-01 --until 2026-06-05
```

Broad search uses the App Server experimental `thread/search` primitive, then applies
dispatch-side filters for managed/unmanaged state, repo/directory, and date bounds.
Lane-focused search reads that one thread with `thread/read(includeTurns:true)` and
performs a local substring scan because App Server search has no thread-id filter.

Use `query` when you want Dispatch's normalized local managed-history index. Query does
not call App Server search; it only sees managed threads that Dispatch has indexed through
sync, history, tail, watch, live events, or other capture paths. Text is optional when at
least one structural filter is present.

```bash
uv run dispatch query "schema drift"
uv run dispatch query --tool linear.save_issue
uv run dispatch query --tool linear.save_issue --tool-status completed --arg-key id
uv run dispatch query --file convex/support/lineage.ts
uv run dispatch query --repo . --since 2026-06-01 --until 2026-06-05
uv run dispatch query --type mcpToolCall --errored
uv run dispatch query --mentions-thread 019e
uv run dispatch schema query
```

`query` returns item-level JSON that is meant for `jq`: thread ref/id/handle,
item id, turn id, item type, role, concrete tool name, snippet, file and
child-thread refs, and timestamps. Server, status, phase, command, command cwd,
arguments, success, error, duration, and agent identity remain available without
enabling raw-payload retention.

## Discover Sessions

`list` shows the threads dispatch already manages. `list --unmanaged` is the other
half: it lists the persisted Codex sessions on this machine — desktop threads and prior
runs — that you could attach. It uses App Server `thread/list` in state-db-only mode,
asks for active sessions sorted by recent updates, and remains read-only; it never
resumes, writes, or registers anything. Use `--archived` with `--unmanaged` when
the first-run cleanup target is an archived Codex thread.

```bash
uv run dispatch list --unmanaged --limit 20
uv run dispatch list --unmanaged --archived --limit 20
uv run dispatch list --parent <ref-or-thread-id>
uv run dispatch list --ancestor <ref-or-thread-id>
uv run dispatch list --root <ref-or-thread-id>
uv run dispatch get <ref-or-thread-id> --topology
```

Topology is provider metadata, not lane authority. `--parent` uses the App
Server's direct-child filter; `--ancestor` returns all spawned descendants;
`--root` returns the rooted managed tree and includes the root when it is a
managed lane. Unmanaged discovery still excludes every already-managed thread.
Ordinary history forks appear under `forked_from`/`forks`, never as child
agents. Results include `complete`, `truncated`, and `cycle_detected` so callers
do not have to mistake a bounded observation for a complete graph.

Plain `list` and `get` project cached topology. `get --topology` explicitly
refreshes one thread and a bounded descendant page from App Server. Lifecycle
events and normal discovery/sync keep the cache current without registering
unmanaged threads. Use `--topology-limit` to bound provider reads and output.

Each row carries `id`, `name`, a shortened `preview`, `cwd`, `status`, `source`, and
`ephemeral`; unmanaged archived rows also set `archived: true` in JSON output. Use the
`id` with `attach` to bring a session under management:

```bash
uv run dispatch attach <id-from-list-unmanaged>
uv run dispatch attach <id-from-list-unmanaged> --sync
```

Keep the two straight: `list --unmanaged` shows unmanaged Codex sessions that are not
registered in dispatch; `list` shows managed threads (owned or already attached). Sync is
separate from both for already managed threads. When you explicitly run `sync` against a
raw unmanaged Codex thread id, dispatch first registers it as an attached read/metadata
managed lane, then refreshes the local index. That does not grant write authority.
Sending to a raw unmanaged Codex thread id follows the same pickup path before the
message action runs: Dispatch registers the thread, quick-syncs/indexes it, then
applies the attached-lane write policy.

## Attached Lanes

Attach registers an existing Codex thread by raw thread id:

```bash
uv run dispatch attach <codex-thread-id>
uv run dispatch sync <dispatch-ref-or-thread-id>
```

Attached lanes allow observation, sync, and explicit metadata/lifecycle actions such as
`rename`, `archive`, and `restore`. Dispatch does not write turns or mutate history on
attached lanes by default because the desktop app uses a separate app-server process and
there is no cross-process write interlock. ADR-0005 and ADR-0018 are the authoritative decisions:
[`docs/adrs/0005-lane-authority-capability-ladder.md`](../adrs/0005-lane-authority-capability-ladder.md)
and [`docs/adrs/0018-top-level-thread-actions-and-search.md`](../adrs/0018-top-level-thread-actions-and-search.md).

Local operators can explicitly opt in to attached-lane writes:

```toml
# ~/.dispatch/config.toml
[policy]
allow_attached_writes = true
```

With that policy enabled, `send`, `send --context`, `goal set`, and other
turn-writing/history-mutating commands may target attached lanes through
Dispatch's daemon. This is a local trust override, not a cross-process interlock:
the desktop app still cannot be gated by Dispatch's advisory lock.

## History Capture Policy

Dispatch keeps a local SQLite registry and normalized history index. The default
history capture mode is `standard`: capture operational state and bounded
searchable facts by default, but do not retain raw provider payloads unless the
raw retention policy allows it. Live `item/started` and `item/completed`
notifications and transcript reads use the same canonical item normalizer. It
covers the App Server 0.144 message, reasoning, command, file,
MCP/dynamic/collaboration tool, subagent, web, image, review, sleep, and
compaction variants; unknown future variants remain visible. Concrete status,
arguments, errors, durations, files, and child-thread refs remain normalized
even when raw payload retention is off. Transcript replay is additive: a
persisted read cannot erase richer live items merely because the provider omits
them, and lower-retention replay does not silently purge an already retained
bounded payload. Normalized text, command, cwd, error, and argument values are
bounded; common credential forms and sensitive argument keys are redacted.
Use `minimal` for a smaller footprint that keeps turn-level
state but skips item-level transcript rows. Bare `history` overview renders from
the local index. Selector-scoped `history` item/tool/file views render from the
normalized index after refreshing one thread from the App Server; `history --raw`
remains a live App Server raw-payload view. Use `debug` while
developing reducers, search, or provider adapters. Debug retention can store
bounded raw provider event and item payloads with truncation markers; it should
usually run against isolated state.

Configure capture in `~/.dispatch/config.toml`:

```toml
[history]
capture = "standard" # minimal | standard | debug
raw_payload_retention = "debug" # off | errors | debug | all
max_text_bytes = 8192
max_payload_bytes = 65536
```

Environment overrides are available for one-shot runs:

```bash
DISPATCH_CAPTURE=debug \
DISPATCH_RAW_PAYLOAD_RETENTION=debug \
DISPATCH_CAPTURE_MAX_TEXT_BYTES=8192 \
DISPATCH_CAPTURE_MAX_PAYLOAD_BYTES=65536 \
uv run dispatch up
```

`dispatch doctor` reports the active capture mode. It warns when debug mode or
raw payload retention is enabled because that posture can retain larger provider
payloads and should usually run with isolated `DISPATCH_HOME`/`CODEX_HOME` while
developing.

Managed lane JSON exposes authority for filtering:

```bash
uv run dispatch list --json | jq '.lanes[] | select(.writable)'
uv run dispatch list --json | jq '.lanes[] | select(.capabilities.context)'
uv run dispatch get <ref> --json | jq '{ref, source, writable, capabilities, write_locked_reason}'
```

Attach is compact by default: it verifies the thread with App Server
`thread/read(includeTurns:false)`, registers the lane, and stores metadata sync state. It
does not call `thread/resume` or load turn history. If the app-server is wedged and the
metadata read stalls, attach fails with a clear `app_server` error and registers no lane —
it never leaves a half-attached entry behind.

Use `sync` to refresh dispatch's local indexed view of an attached lane. Sync reads the
official metadata and, when Codex exposes a local rollout path, parses bounded top+tail JSONL
records into a compact cache: source file identity, sync state, latest event timestamp,
latest turn id, and a preview. It also resumes live observation without hydrating all turns,
indexes the newest App Server turn first, hydrates bounded item pages, and persists opaque
cursors so later calls first reconcile newer turns missed during downtime, then continue
backwards without repeating completed pages. Sync also
reconciles known App Server archive membership for the target: if Codex lists the thread as
archived, dispatch marks the managed lane archived locally; if a locally archived lane is
listed active again, dispatch marks it idle.

```bash
uv run dispatch sync <dispatch-ref-or-thread-id>
uv run dispatch sync <dispatch-ref-or-thread-id> --max-turns 20 --max-items 200
uv run dispatch sync <dispatch-ref-or-thread-id> --max-bytes 1048576 --max-seconds 10
uv run dispatch sync <dispatch-ref-or-thread-id> --full --max-bytes 16777216
uv run dispatch get <dispatch-ref-or-thread-id>
uv run dispatch list
```

`sync <raw-codex-thread-id>` is the quickest way to pick up a previously unmanaged thread:
it verifies the id with `thread/read(includeTurns:false)`, registers an attached lane, and
then runs the same index refresh. Unresolved `@handles` remain errors; they are not treated
as raw thread ids.

`sync --full` scans the current source from byte zero, but remains bounded by
`--max-bytes`; a larger source stays explicitly partial/truncated. Ordinary sync skips an
unchanged complete file, resumes unread bytes from a bounded prior scan, continues
same-file appends from the last complete line, and resets safely
after rotation or truncation. App Server paging is experimental and capability-gated; an
App Server binary without turn paging reports `history_capability=unsupported` and retains
metadata/JSONL fallback instead of silently loading an unbounded transcript. When turn
paging works but item paging does not, `turn-page-fallback` re-fetches one exact turn with
full items. If that atomic turn exceeds the configured item/byte persistence budget, it
stays pending and `truncated=true`; rerun with a larger explicit budget to continue. It is
still an index refresh, not a write to the Codex thread. Bare `history` stays local-index
only. Selector-scoped `history`, `tail`, and
transcript-inclusive `get` continue to use official `thread/read(includeTurns:true)`
when they need transcript turns, and those reads feed the normalized local history
index for that thread as a side effect.

`--max-bytes` is an aggregate target across local and provider history. Dispatch
checks it between provider pages; receiving one page can make `scanned_bytes`
slightly higher, but a page that would exceed the remaining persistence budget is
not indexed and remains pending/truncated. One `--max-seconds` deadline bounds metadata,
provider history, local parsing, persistence, and archive reconciliation. Durable
cursor-cycle detection fails closed rather than spinning across repeated syncs.

If experimental paged resume is unavailable, Dispatch retries stable
metadata-only resume. The result remains `history_capability=unsupported`, but
`observation_enabled=true` records that live observation was established. A
complete JSONL record larger than the remaining local budget stays at its
current offset and reports an actionable error asking for a larger
`--max-bytes`; it is never silently skipped.

The JSON result is designed for operational checks:

```bash
uv run dispatch sync <ref> --json | jq '.sync | {
  history_capability, history_complete, truncated, pages_scanned,
  turns_indexed, items_indexed, unchanged_skipped, scanned_bytes, duration_ms
}'
```

Archive state is lifecycle metadata, not a cleanup command. Dispatch keeps provider events
and normalized history evidence unless an explicit future retention command/policy prunes
bulky cached payloads. `archive`, `restore`, sync reconciliation, and event indexing do not
delete the append-only provider event log.

When referring to a Codex thread in docs or prompts, prefer a readable handle with a URI:

```markdown
[@Dispatch](codex://threads/<codex-thread-id>)
```

Use refs or raw thread ids for command arguments. Use the Markdown link in human-facing text.

## Triggers

A trigger binds `when -> action -> lane`.

Interval trigger:

```bash
uv run dispatch trigger add \
  --name docs-pulse \
  --lane <dispatch-ref> \
  --when interval \
  --seconds 1800 \
  --action send \
  --text "Check whether the docs branch needs attention."
```

Cron trigger:

```bash
uv run dispatch trigger add \
  --name weekday-standup \
  --lane <dispatch-ref> \
  --when cron \
  --cron "0 9 * * 1-5" \
  --action send \
  --text "Post a short standup summary."
```

Idle trigger:

```bash
uv run dispatch trigger add \
  --name after-idle \
  --lane <dispatch-ref> \
  --when idle_for \
  --seconds 900 \
  --action brief \
  --text "If you resume, first re-read the current diff."
```

Useful guards:

- `--idle-only` fires only when the lane is idle.
- `--min-interval <seconds>` suppresses rapid refires.
- `--dedupe` suppresses identical consecutive firings within the current daemon process.

Manage triggers:

```bash
uv run dispatch trigger list
uv run dispatch trigger pause <trigger-id>
uv run dispatch trigger resume <trigger-id>
uv run dispatch trigger rm <trigger-id> --yes --json
```

## Schemas

Successful CLI output is JSON-shaped for `jq` by default. Use `--json` when you want to
make that contract explicit in scripts. `schema` prints the input and output schemas
derived from the contract registry:

```bash
uv run dispatch list --json
uv run dispatch schema send
uv run dispatch schema "list --unmanaged"
uv run dispatch schema sync
uv run dispatch schema watch
uv run dispatch schema models
uv run dispatch schema permissions
uv run dispatch schema usage
uv run dispatch schema "goal set"
```

`schema` uses the CLI projection manifest, including composed command spellings such
as `list --unmanaged`. It is the preferred way to discover stable fields for `jq`
instead of scraping `--help` or hand-copying Pydantic schemas.

## MCP

The MCP surface is derived from the same op registry as the CLI. The local entrypoint is:

```bash
uv run dispatch mcp
```

MCP is grouped for agent ergonomics rather than one tool per op. Tools are grouped by
workflow and safety boundary, for example thread read/write/destroy, trigger
read/write/destroy, and daemon read tools. The daemon read tool includes the
`models`, `permissions`, and `usage` ops so agents can discover valid model,
permission-profile, service-tier, and provider-capacity choices without
guessing. Each grouped call chooses an `op` inside the tool, and that op's
arguments/schema still derive from the same contract registry.
The thread-write `new` and `send` ops accept a structured `content` array with `text`, `image`, and `local_image` items. MCP clients should send image URLs and local paths as typed content rather than reproducing CLI flags; the same HTTPS, format, size, mode, and queue-delivery validation applies.
The thread-write tool's `fork` op accepts `last_turn_id`, which asks App Server
to fork history through that completed turn, inclusive.
The thread-read tool's `roster`, `discover`, and `show` ops expose the same
parent/ancestor/root filters and bounded topology fields as the CLI. Reading or
discovering topology does not create a lane or grant write authority.
Structured MCP outputs that identify a managed thread include the dispatch `ref`, full
Codex id, title/handle, managed/source/status, and cwd when available.

The workspace Codex plugin at [`plugins/dispatch/`](../../plugins/dispatch/) exposes that
MCP server through [`plugins/dispatch/.mcp.json`](../../plugins/dispatch/.mcp.json). The
workspace marketplace entry is [`.agents/plugins/marketplace.json`](../../.agents/plugins/marketplace.json).

If Codex does not pick up the plugin immediately, restart Codex for this workspace.

## Safety Notes

- Do not use dispatch tests or ad hoc integration probes against the user's live `~/.codex`.
  The integration suite uses an isolated `CODEX_HOME`.
- Do not expect attached lanes to receive live event fan-out across processes. The Phase-1
  spike confirmed cross-process history discovery/resume, not live co-presence.
- Do not install the generated launchd plist with `launchctl` unless the user explicitly
  wants persistent autostart.
- `tail` is a persisted history snapshot. `watch` is a bounded live event sample.
  Neither is a durable infinite tail yet; that needs a subscription-capable control socket.
- `rollback` does not revert workspace files. Use Git or another workspace mechanism for
  file-level undo.
