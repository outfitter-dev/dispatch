# Semantic History Search

Status: active design note
Date: 2026-07-02

Dispatch should make local agent history searchable without turning the local
registry into an indiscriminate transcript archive. Search starts with normalized
history that Dispatch already captures; semantic/vector search comes later after
the retention and redaction boundary is proven.

## Current Search Modes

- `dispatch search <query>` uses App Server `thread/search` for broad persisted
  Codex search, then applies Dispatch filters for managed/unmanaged state, cwd,
  repo, archive state, and dates.
- `dispatch search <query> --thread <selector>` reads one thread through App
  Server `thread/read(includeTurns:true)` and scans the returned transcript.
- `dispatch search <query> --local` searches Dispatch's normalized local
  `thread_items` index for managed threads only. It does not call App Server
  search and rejects `--unmanaged`.

`--local` is intentionally keyword search. It proves the index contract, selectors,
filters, and scriptable schemas before embeddings or vector storage are added.

## Indexed By Default

The local search substrate may index derived or bounded facts from:

- thread summaries and lane metadata;
- turn status, completion, and error summaries;
- bounded message text retained under the active history capture policy;
- tool names and compact tool-call summaries;
- file, thread, and tool references;
- goal, retro, subscription, inbox, and receipt summaries;
- explicit operator notes or generated artifacts that are safe to retain.

## Excluded By Default

The local search substrate must not index these by default:

- raw provider payloads;
- full unbounded transcripts;
- secrets, credentials, private keys, and tokens;
- large tool outputs;
- private attachments;
- debug captures unless debug retention is explicitly enabled;
- remote/cloud copies of local history.

Debug capture can retain more data locally for development, but debug retention is
not a semantic search policy and should usually run against isolated state.

## Embedding Policy

Future embedding work should treat embeddings as derived local artifacts:

- generate embeddings only from retained, redacted, bounded source artifacts;
- store enough provenance to explain what source row/artifact produced a vector;
- allow deletion/rebuild when the source row is deleted or retention policy changes;
- keep embeddings local by default;
- avoid paid or remote embedding calls unless an operator explicitly opts in.

Embedding models, vector dimensions, chunking, redaction, and retention must be
configurable before real transcript embeddings are enabled.

## Storage Boundary

SQLite/`aiosqlite` remains the default backend. Turso/libSQL may be useful later
for vector search, selected sync, or concurrent ingestion, but the local search
contract should not depend on that engine. Any alternate backend must preserve:

- the registry/history behavior suite;
- the derived CLI/MCP schemas;
- local-first retention defaults;
- the ability to rebuild search artifacts from normalized history.

## Open Questions

- Which summary artifacts should be generated eagerly during sync versus lazily
  when search is requested?
- What redaction pass is required before real embeddings can be enabled?
- Should `--local` grow field filters, or should those stay under `history` until
  semantic search exists?
- What is the smallest fixture corpus that can catch search ranking and retention
  regressions without using private transcripts?
