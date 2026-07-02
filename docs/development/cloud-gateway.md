# Dispatch Cloud Gateway

Status: exploratory design note
Date: 2026-06-30

This note sketches an optional always-on Dispatch Gateway for external work
surfaces such as Slack, Linear, and future team tools. It is not an
implementation commitment yet. The goal is to preserve Dispatch's local-first
authority model while making external integrations reliable, inspectable, and
team-aware.

## Problem

Slack and Linear need a stable public endpoint. Local `dispatchd` processes do
not provide that by themselves: laptops sleep, machines move networks, and
private LAN processes are not webhook targets.

At the same time, Slack and Linear should not become execution owners. A message
in a channel or an issue comment must not gain ambient authority over a local
machine, filesystem, Codex account, or App Server. Dispatch's local daemon must
remain the execution boundary.

The missing layer is an always-on gateway that can receive external events,
resolve routing, queue work, expose configuration, and deliver authorized
requests to local Dispatch daemons when they are online.

## Thesis

Add an optional Cloudflare-hosted Dispatch Gateway:

- External surfaces talk to the gateway.
- The gateway owns surface ingress, route configuration, installation state,
  queues, delivery state, audit, and a small configuration UI.
- Local `dispatchd` instances connect outbound to the gateway, pull or receive
  queued requests, authorize them locally, execute Dispatch ops, and report
  compact delivery/status updates back.
- The gateway never directly controls Codex App Server, local filesystems,
  local shells, or local credentials.
- The gateway is not the default store for agent logs, transcripts, raw provider
  events, debug payloads, or detailed local history.

In short: the gateway routes intent; local Dispatch executes authority.

## Prior art in this repo

This design extends, rather than replaces, the existing remote and delivery
decisions:

- [ADR-0013: Dispatch Mesh Is Daemon Federation](../adrs/0013-dispatch-mesh-is-daemon-federation.md)
  says remote coordination federates Dispatch daemons, not Codex App Servers.
- [ADR-0014: Mesh Auth, Discovery, and Durable Queues](../adrs/0014-mesh-auth-discovery-and-durable-queues.md)
  says remote delivery needs envelopes, idempotency, capabilities, and durable
  queues.
- [ADR-0021: Lane Inbox and Delivery](../adrs/0021-lane-inbox-and-delivery.md)
  separates durable inbox records from turn-start delivery.
- [ADR-0022: Event Subscriptions](../adrs/0022-event-subscriptions.md)
  models event matches as durable messages with delivery adapters.

The Cloud Gateway is another transport and control plane around those concepts,
not a second contract system.

## Cloudflare fit

Cloudflare is a plausible first host because its current platform primitives map
well to this shape:

- Workers provide the public HTTP edge for Slack, Linear, CLI remote admin, and
  UI/API routes.
- Durable Objects provide single-point coordination and WebSocket-friendly state
  for machines, organizations, route resolvers, and live delivery sessions.
- Queues provide durable command and event delivery.
- D1 provides relational configuration, bindings, route tables, and audit logs.
- R2 can store optional larger artifacts such as exported transcripts or debug
  bundles.
- KV may cache read-heavy route snapshots, but should not be the source of truth
  for authority.

Useful references:

- [Cloudflare Durable Objects](https://developers.cloudflare.com/durable-objects/)
- [Cloudflare Durable Objects WebSockets](https://developers.cloudflare.com/durable-objects/best-practices/websockets/)
- [Cloudflare storage options](https://developers.cloudflare.com/workers/platform/storage-options/)
- [Cloudflare Agents](https://developers.cloudflare.com/agents/)
- [Cloudflare Slack agent example](https://developers.cloudflare.com/agents/examples/slack-agent/)
- [Slack agents](https://docs.slack.dev/ai/developing-agents)
- [Linear agents](https://linear.app/docs/agents-in-linear)

## Architecture

```text
Slack / Linear / future work surfaces
              |
              v
        Cloudflare Gateway
   +---------------------------+
   | surface ingress           |
   | route resolver            |
   | config UI/API             |
   | queues and idempotency    |
   | machine presence          |
   | audit log                 |
   | event fanout              |
   +---------------------------+
              |
              | outbound WebSocket, pull queue, or tunnel
              v
        local dispatchd
   +---------------------------+
   | local policy              |
   | repo/path resolution      |
   | registry                  |
   | op execution              |
   | Codex App Server client   |
   +---------------------------+
```

The gateway is not a cloud `dispatchd`. It is a routing, policy, queue, and
inspection service for local Dispatch daemons.

## Core model

The gateway should normalize every external request into a Dispatch-level
envelope before routing.

### Surface installation

Represents an installed external surface:

- `surface`: `slack`, `linear`, or another adapter.
- `external_org_id`: Slack workspace/team id, Linear workspace id, etc.
- OAuth/signing configuration references.
- Installation status and scopes.
- Default route policy.

### External place

The durable source location of an invocation:

- Slack: workspace id, channel id, thread timestamp, message id.
- Linear: workspace id, team id, project id, issue id, comment id.
- Future surfaces: equivalent stable ids.

Store ids as authority. Names such as `#dispatch`, project titles, and issue
titles are labels only.

### External actor

The user or agent that invoked Dispatch:

- Surface-local user id.
- Optional mapped Dispatch identity.
- Display labels for UI.
- Roles/groups when provided by the surface.

Actor identity participates in policy, but is not enough by itself. A trusted
user in an untrusted channel should not inherit broad machine authority.

### Route binding

Maps external places to Dispatch work contexts:

- Source selector: surface, org, channel/team/project/issue, optional actor.
- Target repo key, workspace key, preset, name prefix, and default machine scope.
- Allowed operations and confirmation rules.
- Fallback behavior when the preferred local machine is offline.

Routes should be hierarchical:

1. Exact external object binding, such as a Linear issue or Slack thread.
2. Channel or project binding.
3. Team binding.
4. Organization/workspace default.
5. No route found: ask for configuration instead of guessing.

### Machine

A registered local Dispatch daemon:

- Stable machine id and display name.
- Owner identity or team ownership.
- Online/offline/stale presence.
- Reported capabilities.
- Allowed repo keys and local path mappings.
- Local policy digest.
- Last heartbeat and connector version.

Cloud routes should refer to repo keys and machine scopes, not absolute local
paths. Each machine maps repo keys to local paths in local config.

### Invocation envelope

The canonical queued request:

```json
{
  "id": "inv_...",
  "idempotency_key": "surface-event-id-or-derived-key",
  "surface": "linear",
  "external_place": {
    "workspace_id": "...",
    "team_id": "...",
    "project_id": "...",
    "issue_id": "..."
  },
  "actor": {
    "surface_user_id": "...",
    "display": "..."
  },
  "intent": "goal.start",
  "payload": {
    "text": "Investigate the failing release workflow"
  },
  "route_id": "route_...",
  "target": {
    "repo_key": "outfitter/dispatch",
    "machine_scope": "team:outfitter"
  },
  "reply_to": {
    "surface": "linear",
    "issue_id": "..."
  }
}
```

The envelope is the shared substrate for Slack, Linear, CLI remote admin, future
mesh relays, and any later UI.

### Delivery binding

Records where updates should go:

- Slack thread or channel.
- Linear issue comment, update, or agent activity.
- Gateway UI event stream.
- Dispatch inbox or subscription destination.

The same Dispatch event can render differently per surface.

## Personal vs team routing

The gateway must not assume every route targets one user's personal laptop. Team
deployments need a different routing model.

### Personal machine routes

Use when work should run on a specific user's machine, account, filesystem, and
local Codex setup.

Examples:

- A personal Slack DM to Dispatch routes to an operator-owned laptop.
- A private Linear issue routes to a personal repo checkout.
- A one-off command targets a named machine because only that machine has the
  correct credentials or worktree.

Properties:

- The machine owner remains the authority boundary.
- Local policy may reject commands even if the cloud route allows them.
- External users may need explicit delegation from the machine owner.
- Offline behavior should be visible: queued, expired, or rerouted only when
  policy permits.

### Team machine routes

Use when the team wants a shared Dispatch runtime that any authorized teammate or
surface can use.

Examples:

- A Linear team routes implementation issues to a team-owned Dispatch machine.
- A Slack project channel routes review requests to a shared repo checkout.
- A CI-adjacent Dispatch worker handles low-risk read/search/status operations
  without touching a personal machine.

Properties:

- The route is owned by an organization/team, not one person.
- Machine policy should be stricter and more explicit.
- Repo access is configured as team infrastructure.
- Audit, approval, and human ownership matter more than convenience.
- The gateway may choose among an eligible pool of team machines.

### Hybrid routes

Some routes may prefer a personal machine and fall back to a team machine:

```text
Linear project Dispatch
  preferred: personal machine owned by issue assignee
  fallback: team dispatch runner for read/status/review ops only
```

Fallback must be capability- and policy-aware. It should never silently move a
write-capable operation from a personal machine to a team machine unless the route
explicitly allows that.

### Routing dimensions

Useful route dimensions:

- Surface: Slack, Linear, CLI remote, future.
- External place: workspace, channel, thread, team, project, issue.
- Actor: individual user or group.
- Repo key and workspace key.
- Machine scope: `personal:<identity>`, `machine:<id>`, `team:<id>`,
  `pool:<id>`.
- Capability class: read, write, destroy, approval, shell, artifact export.
- Preset: model/reasoning/service tier, sandbox, approval policy, name prefix.
- Privacy class: personal, team-visible, public-channel, external-customer.

## Configuration layers

Configuration should be split so no layer has to know everything.

### Cloud config

Owned by the gateway:

- Surface installations.
- Route bindings.
- External object to Dispatch thread bindings.
- Machine registry and presence.
- Team/user ownership.
- Remote presets and policy allowlists.
- Audit and delivery state.

### Local machine config

Owned by local `dispatchd`:

- Gateway pairing credentials.
- Machine display name and owner/team identity.
- Allowed gateway organizations.
- Repo key to local path mappings.
- Local policy, including final allowed operations.
- Local Codex defaults and machine-only secrets.

### Repo config

Owned by `.dispatch/config.toml`:

- Repo defaults.
- Presets.
- Name prefixes.
- Model/reasoning/sandbox defaults.
- Repo-level allowed operation hints.

The effective policy is the intersection of cloud route policy, local machine
policy, and repo policy. The most restrictive layer wins.

## Example route config

Cloud-side route:

```toml
[[routes]]
id = "route_linear_dispatch_project"
surface = "linear"
linear_workspace_id = "lin_ws_..."
linear_team_id = "lin_team_outfitter"
linear_project_id = "lin_project_dispatch"
repo_key = "outfitter/dispatch"
machine_scope = "team:outfitter"
preset = "implementation"
name_prefix = "[linear:dispatch]"
allowed_ops = ["status", "search", "send", "goal.start", "stop"]
requires_confirmation = ["stop"]
```

Local machine mapping:

```toml
[gateway]
enabled = true
machine_id = "mach_devbook"
allowed_orgs = ["outfitter"]

[repos."outfitter/dispatch"]
path = "/work/outfitter/dispatch"
allowed_ops = ["status", "search", "send", "goal.start", "stop"]
```

The cloud route can say "send this to `outfitter/dispatch`." The local daemon is
the only layer that knows where that repo lives on that machine.

## Delivery lifecycle

1. Slack or Linear sends an event to the gateway.
2. The Worker verifies the request signature/OAuth context.
3. The surface adapter normalizes the event into an invocation envelope.
4. The route resolver finds the target repo, preset, machine scope, and policy.
5. The gateway writes a durable queue record with an idempotency key.
6. If an eligible local daemon is online, a Durable Object nudges it over a
   WebSocket. If not, the record remains pending.
7. Local `dispatchd` receives or pulls the envelope.
8. Local policy validates the request against machine and repo config.
9. Local `dispatchd` executes the derived Dispatch op through its normal control
   path.
10. Compact local status/results are reported back to the gateway according to
    route policy.
11. The gateway renders updates to Slack, Linear, the UI, and/or Dispatch inbox
    destinations.

The queue is the source of truth. The WebSocket is a latency optimization.

## Event rendering

Do not mirror raw token streams by default. External surfaces should receive
high-signal Dispatch events:

- `thread.created`
- `thread.routed`
- `thread.status_changed`
- `thread.awaiting_approval`
- `thread.blocked`
- `thread.completed`
- `thread.failed`
- `thread.summary_available`
- `delivery.failed`

Slack can render these as thread replies, assistant status, or compact updates.
Linear can render them as issue comments, agent activity, or status updates,
but neither surface should receive raw provider event logs, full transcripts,
debug payloads, or large tool outputs unless an operator explicitly exports a
bounded artifact.
depending on what the integration supports.

Streaming can be added later per route, but should be opt-in.

## Gateway UI

The UI should make routing inspectable:

- Installed Slack/Linear workspaces.
- Route table.
- External object bindings.
- Machines and presence.
- Repo key mappings reported by machines.
- Active and recent Dispatch threads.
- Pending, delivered, failed, and expired invocations.
- Audit log.
- Policy warnings, such as broad channel routes with write permissions.

This UI is not just convenience. It is how operators understand where work will
go before they trust Slack or Linear to invoke it.

## CLI interaction

The CLI can administer the gateway, but should not be the only config surface.

Possible future commands:

```bash
dispatch gateway login
dispatch gateway status
dispatch gateway pair
dispatch gateway routes
dispatch gateway routes add linear --project dispatch --repo outfitter/dispatch
dispatch gateway machines
dispatch gateway audit
```

These commands should still project from Dispatch contracts where possible. If a
gateway command cannot be represented as a local Dispatch op, that should be an
explicit surface decision rather than hand-wired drift.

## Security model

Minimum guardrails:

- Verify every external request at ingress.
- Use idempotency keys for every external event and queued command.
- Store external ids, not names, as authority.
- Treat channel/issue text as untrusted input unless promoted by policy.
- Keep local machine policy as the final authority check.
- Never let cloud route config grant operations the local daemon disallows.
- Require explicit confirmation for destructive or high-authority ops.
- Audit before and after local execution.
- Keep Slack/Linear secrets in the gateway; keep Codex/local machine secrets
  local.
- Support revocation for machines, installations, routes, and actors.

The gateway should make useful work easy, but it should make surprising authority
hard.

## Open questions

- Should gateway state live in one D1 database per Dispatch organization, or one
  database with organization scoping?
- Is the primary live connector a Durable Object WebSocket, a pull loop, or a
  hybrid from day one?
- How should a local daemon advertise repo keys without leaking sensitive local
  paths?
- What is the smallest route UI that makes the product trustworthy?
- How should team machine pools claim work: first-online, lease-based,
  capability-ranked, or manual assignment?
- How much of Linear's agent model can be used directly versus a conventional
  integration/webhook app?
- How much of Slack's agent surface should be used initially versus a narrower
  slash-command/app-mention surface?
- What event detail is safe to send back to shared channels by default?

## Suggested phases

### Phase 0 - decision and spike

- Promote or adapt this note into an ADR.
- Spike Cloudflare Worker + Durable Object + D1 + Queue locally with Miniflare.
- Verify Slack and Linear event payloads and installation/auth flows.
- Define the first `InvocationEnvelope` schema.

### Phase 1 - gateway skeleton

- Build route config storage, audit log, and fake external adapter.
- Build local `dispatchd` connector in dry-run mode.
- Deliver fake invocations to a local daemon and record outcomes.

### Phase 2 - local authority and queues

- Add machine pairing.
- Add local policy validation.
- Add durable command/event queues and idempotency.
- Add presence and reconnect behavior.

### Phase 3 - first real surface

- Implement either Linear or Slack first.
- Linear is better for structured work routing through teams/projects/issues.
- Slack is better for conversational operator UX.
- Keep the first surface narrow: route, start/send/status/stop, and completion
  updates.

### Phase 4 - UI

- Add route and machine inspection.
- Add route editing.
- Add audit and delivery views.
- Add warnings for unsafe or broad routes.

### Phase 5 - team routing

- Add machine scopes, team-owned machines, and pools.
- Add assignment/fallback policy.
- Add per-team route ownership and admin controls.

## Non-goals for the first implementation

- Running Codex in Cloudflare.
- Exposing arbitrary shell execution from Slack or Linear.
- Mirroring all Dispatch ops into every external surface.
- Streaming every token to Slack/Linear by default.
- Making route names authoritative.
- Replacing local Dispatch config.
- Replacing daemon federation; this gateway should complement the mesh model.

## Bottom line

The Cloud Gateway is how Dispatch becomes reliable in team work surfaces without
giving those surfaces unsafe authority. It should provide always-on ingress,
inspectable routing, durable queues, and a configuration UI. Local `dispatchd`
should continue to own execution, local policy, repo access, Codex App Server,
and final authorization.
