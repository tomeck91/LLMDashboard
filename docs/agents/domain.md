# Domain Docs — Layout & Consumer Rules

## Layout (single-context)

This repo is a single context. All domain documentation lives at the root:

- `GLOSSARY.md` — canonical project vocabulary (terms, acronyms, and what they mean in this codebase).
- `docs/adr/` — Architecture Decision Records, one file per decision (`NNN-title.md`).

There is no `GLOSSARY-MAP.md`; that only appears in multi-context (monorepo) layouts.

## Consumer rules

1. **Read before assuming.** Before introducing a new term or renaming an existing one, check `GLOSSARY.md`. If the concept already has a name here, reuse it — don't invent a synonym.
2. **Keep the glossary current.** When you add a domain concept that recurs across files (e.g. "Sampler", "reader", "snapshot"), record its definition in `GLOSSARY.md` so future sessions stay consistent.
3. **Record non-obvious decisions as ADRs.** If a choice is surprising or easy to accidentally undo (e.g. "the `/` route must be defined before the StaticFiles mount"), write an ADR explaining the decision, context, and consequences rather than leaving it as a code comment.
4. **Prefer updating over duplicating.** One entry per concept; edit in place instead of adding near-duplicate terms.
