# ADR-0019: Dispatch-Local Refs and Flat Thread CLI

## Status

Accepted.

## Context

Codex thread ids are full UUID-like identifiers and remain the only global identity.
They are durable but awkward for daily operator use. Earlier dispatch surfaces leaned on
`@handles`, titles, and `dispatch lane ...` commands. That made common operations verbose
and blurred stable identity with mutable labels that agents may change while working.

ADR-0018 moved rename/archive/restore/search to top-level thread actions. The next step is
to make all common thread operations use the same flat shape and give every managed lane a
short stable registry-local selector.

## Decision

Every managed lane stores a unique dispatch-local `ref`. The full Codex thread id is always
accepted and remains the durable escape hatch. Titles and `@handles` are mutable convenience
labels, not identity.

Codex refs use:

```text
<source><payload4><mixer>
```

`source` is `0` for Codex. `payload4` is the first four base58btc characters from
`sha256("codex:" + thread_id)`. `mixer` is allocated by the registry from the base58btc
alphabet. On collision, the registry tries the next mixer character and stores the
allocated ref. If the mixer alphabet is exhausted for one payload, dispatch fails loudly
and the operator can use the full Codex thread id.

The canonical CLI for common thread operations is flat:

```bash
dispatch new ...
dispatch attach <thread-id>
dispatch list
dispatch list --unmanaged
dispatch get <selector>
dispatch send <selector> <text>
dispatch stop <selector>
dispatch tail <selector>
dispatch watch <selector>
dispatch sync <selector>
dispatch rename <selector> <title>
dispatch archive <selector>
dispatch restore <selector>
dispatch search <query>
```

`dispatch new --no-send` is the open-without-initial-turn shape. `tail` means persisted
conversation history. `watch` means a bounded live App Server event sample; `tail --follow`
is not canonical.

MCP remains grouped by workflow and safety boundary. It uses thread/ref tool names
(`dispatch_thread_read`, `dispatch_thread_write`, `dispatch_thread_destroy`) and preserves
exact safety annotations instead of mirroring every CLI command one-for-one.

## Consequences

- Operators get stable short refs for managed lanes without giving up full Codex ids.
- Mutating and destructive operations do not fuzzy-resolve ambiguous titles or handles.
- Read/discovery flows may use fuzzy title matching only when it resolves uniquely.
- Registry migration must backfill refs for existing lanes in deterministic order.
- Docs, skills, and examples should prefer flat commands and refs. Older `lane ...`
  examples are historical unless explicitly labeled as internal/legacy.

## Alternatives Considered

- UUID prefixes: rejected because local Codex UUID prefixes already collide in timestamp-heavy
  UUIDv7 prefixes.
- Hash-only fixed prefixes: workable, but allocation gives clearer collision behavior in a
  local registry.
- Keep `dispatch lane ...` canonical: rejected because common thread actions are easier to
  learn and script as top-level commands, while internal code can still use `lane` for the
  managed-thread registry concept.
