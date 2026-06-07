# ADR-0020: Live-Use Trust Contracts

## Status

Accepted.

## Context

Real dispatch use exposed a trust failure: an agent could create or message a
thread, receive JSON that looked successful, and still have no useful evidence
that the Codex App Server accepted native goal state or produced assistant work.
Separately, some CLI behavior had drifted into hand-coded surface branches that
were not fully represented in the projection/parity tests.

The project already has a contract-first design, but live users and agents need
more than a clean architecture diagram. They need explicit recovery paths,
machine-readable control output, and status fields that distinguish request
acceptance from completed work.

## Decision

dispatch will treat live-use trust as part of the public contract:

- Initial launch output distinguishes `message_accepted` from assistant
  completion and exposes the latest observed turn state.
- Native goals are created through first-class goal fields/ops. Slash commands
  embedded in message text are not interpreted as App Server control commands.
- Turn lifecycle events are persisted into the registry so `get`/`list` can show
  recent runtime status and App Server error text.
- Recovery-oriented control commands (`up --json`, `down --json`,
  `registry migrate`, and `doctor`) return scriptable output and concrete next
  steps.
- Ergonomic CLI routes, including composed routes such as `list --unmanaged`,
  must be declared in the CLI projection manifest and covered by parity tests.
- Destroy-intent CLI operations keep derived confirmation behavior and require
  explicit `--yes` for non-interactive scripts.

## Consequences

### Positive

- Agents can verify whether work actually started, failed, or only had its
  request accepted.
- Registry schema changes have a documented, tested recovery path.
- CLI/MCP/no-drift work has a sharper guardrail: custom shell ergonomics are
  allowed, but unmanifested command paths are not.

### Tradeoffs

- Public output models gain a small amount of runtime status detail.
- The CLI has a few process/control commands that are intentionally not ops.
  They need an explicit allowlist and tests because they manage the surface
  itself rather than business behavior inside the daemon.

## Alternatives Considered

- **Keep `sent: true` as the launch signal** — rejected because it conflates
  request acceptance with completed assistant work.
- **Allow `/goal ...` text to pass through silently** — rejected because it
  makes a thread look goal-driven while bypassing native goal state.
- **Document recovery without a command** — rejected because agents need a
  scriptable path that can be tested locally.

## References

- [ADR-0000: Contract-First, Surface-Derived Design](0000-contract-first-surface-derived.md)
- [ADR-0010: Surface Projections Are Ergonomic, Not Isomorphic](0010-surface-projections-are-ergonomic-not-isomorphic.md)
- [ADR-0019: Dispatch-Local Refs and Flat Thread CLI](0019-dispatch-local-refs-and-flat-thread-cli.md)
