---
description: Documentation standards
globs:
alwaysApply: true
---

# Documentation

- **`CLAUDE.md`** is the entry point for agents/editors: a `## Rules` index (`@`-includes of
  `.claude/rules/*.md`), commands, and high-level architecture. Keep the `## Rules` list in sync
  whenever a rule file is added, renamed, or removed.
- **`.cursor/rules/*.mdc`** and **`.claude/rules/*.md`** are **byte-identical**, Cursor frontmatter
  included — *both* copies carry the `---` block; it is not something only the `.mdc` has. So the
  correct way to propagate an edit is a plain `cp` in either direction. Never rebuild one side by
  splicing the frontmatter onto the other: that prepends a second `---` block, and nothing at read
  time complains. `scripts/check-rules-sync.sh` enforces the mirror (wired into pre-commit); run it
  after touching either folder.
- **`README.md`** is for humans: setup, env vars, project tree. Update it when structure or the
  env contract changes.
- **`.env.sample`** documents every supported env var with a placeholder — update it alongside any
  new setting.
- **Docstrings — Google style (mirrors EmotionSystem / IntelligenceService JSDoc).** Every module
  opens with a docstring: a one-line summary plus the *why* / non-obvious notes (why a default was
  chosen, a latency trade-off). Public functions, methods, and classes carry a docstring with a
  one-line summary then, where they add clarity, `Args:` / `Returns:` / `Raises:` sections — list
  each parameter with its type in parens (e.g. `voice_id (str): the resolved TTS voice`), the return
  value, and any error raised. Keep summaries crisp and don't restate the obvious; trivial private
  helpers may stay one-liners. Types live in the signature (hints) *and* the docstring Args, matching
  the Aura Python convention.
- When you change a convention, update the relevant rule file in the same PR — stale rules are
  worse than none.
