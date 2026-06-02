# CLAUDE.md

## Compatibility Shim

Shared project guidance lives in [`./AGENTS.md`](./AGENTS.md). Only Claude-specific bootstrap belongs here.

## Agent Instructions

@AGENTS.md

## Cross-cutting Rules

@.claude/rules/python-conventions.md
@.claude/rules/contracts.md
@.claude/rules/agent-docs.md

Path-scoped rules (`client`, `surfaces`, and future modules) are delivered as `AGENTS.md` symlinks inside their directories and load contextually when you work there. See `.claude/rules/agent-docs.md`.
