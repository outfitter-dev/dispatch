# Stable refs and flat CLI - references

## Current repo context

- PR #32 introduced top-level `rename`, `archive`, `restore`, and `search`, plus managed/unmanaged/sync language.
- Current public docs still use `lane` heavily because that was the prior operator grammar.
- The contract projection intentionally allows CLI command paths to differ from op ids and MCP grouping as long as schemas/errors/annotations derive from the op registry.
- Existing `dispatch mcp` entrypoint must stay available for plugin/MCP clients.

## Live Codex ID observations

Recent Codex thread IDs are UUID-looking and UUIDv7-ish/time-sortable:

```text
019e8a09-5021-7b63-9d95-402b7c7d345e  @Dispatch
019e92f0-8eb5-7723-a733-1ec8af27b0db  @Skillset
019e844c-e49e-7450-8845-77140f51db52  @Lewis
019e8476-0ccf-79b3-8967-58f47df22c9e  @Numero
019e9598-9214-7ed1-ac40-52d6d675d3e7  Ship dispatch reliability fixes
```

Local `~/.config/codex/session_index.jsonl` sample had 837 known ids at investigation time. First UUID chunks already collided because the left side is timestamp-heavy. Examples:

```text
019e78e3-a298-70d3-97af-1d682fae392d
019e78e3-ed44-7c93-b786-df085e30f9b3
019e78e3-ca27-7f73-95bc-b8b695810c3c
019e78e3-5e64-7be0-b866-d231d4e431eb
019e78e3-8295-7340-91d0-de5999bc5cf6
019e78e3-a779-7432-a74a-73f0141a6db6
```

Raw left-prefix truncation of the Codex UUID should remain an escape hatch only. It should not be the primary short ref strategy.

## Ref design notes

Rejected:

- First UUID chunk as ref: collisions already exist locally.
- Last 3 chars of UUID group 3 plus group 4: collision-free in the sample, but only about 26 random-ish bits and depends on UUIDv7 layout.
- Fixed 8-char hash prefix with no allocation: workable, but if refs are dispatch-local, allocation gives better user experience and simpler collision handling.

Preferred:

- Dispatch-local assigned ref.
- Full Codex UUID remains canonical.
- Hash payload avoids dependence on UUID layout.
- Mixer character resolves collisions by allocation, not by hoping birthday math never bites.

## CLI decisions from discussion

- Flatten canonical operator commands: `dispatch list`, `dispatch get`, `dispatch attach`, `dispatch sync`, `dispatch tail`, `dispatch watch`.
- Keep grouped subdomains where they are real: `goal`, `trigger`, `daemon`, `schema`, `mcp`.
- Keep `dispatch mcp` open and reserved as the MCP server entrypoint.
- Fold "open but do not send" into `dispatch new --no-send`.
- Make `dispatch list` the normal operational overview and `dispatch list --unmanaged` the discover path.
- Keep `dispatch search <query>` top-level, with filters for managed/unmanaged, one focused thread, repo/dir, and date windows where the underlying data supports it.
- Split `tail` and `watch`:
  - `tail`: persisted conversation history.
  - `watch`: bounded live App Server event sample.
- Do not use `tail --follow` as the canonical shape until real streaming exists.

## Files likely affected

This plan intentionally avoids file-by-file implementation lock-in, but likely surfaces include:

- Registry schema/store and lane models.
- Core handlers and any selector helpers.
- Contract models/ops and derived CLI/MCP projections.
- Surface parity/schema tests.
- README, docs/usage, docs/development/design, ADRs, root AGENTS/CLAUDE guidance, `.claude/rules`, first-party skills, and plugin/MCP docs.
