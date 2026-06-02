# Agent-docs convention (no-drift rule docs)

How dispatch keeps agent guidance single-sourced across Claude and other agents (Codex, etc.).

## The pattern

- **Canonical content lives in `.claude/rules/<name>.md`.**
- **`AGENTS.md` in a directory is a symlink** back to its corresponding rule, so any agent that reads `AGENTS.md` (Codex, etc.) and any tool that reads `.claude/rules/` get the *same bytes*. One source, no drift — the same principle the framework applies to surfaces.
- Exception: the **root `AGENTS.md` is a real file** (the canonical fieldguide). `CLAUDE.md` is a thin shim that `@`-imports it plus cross-cutting rules.

## Naming / correspondence

A path-scoped rule for the module directory `src/outfitter/dispatch/<module>/` is named `.claude/rules/<module>.md`, and that directory's `AGENTS.md` is a relative symlink to it:

```bash
# from repo root, for a module dir at src/outfitter/dispatch/<module>/
ln -s ../../../../.claude/rules/<module>.md src/outfitter/dispatch/<module>/AGENTS.md
```

(Four `../` hops: module → dispatch → outfitter → src → repo root.) Always use **relative** symlinks so they survive clone/move.

## When adding a new module

1. Write the rule at `.claude/rules/<module>.md`.
2. Symlink `src/outfitter/dispatch/<module>/AGENTS.md` → it (command above).
3. If the rule is cross-cutting (applies everywhere), also `@`-import it in `CLAUDE.md`.

Keep rules concise and behavioral ("do X, not Y"), not tutorials. They grow as the code lands.
