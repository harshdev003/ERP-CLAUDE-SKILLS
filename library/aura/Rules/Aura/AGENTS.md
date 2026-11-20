# AGENTS.md

This repository uses Cursor and Claude Code. Both tools share the same source of truth.

**Security review:** `.claude/rules/security-review.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security-review.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every mobile app repo. Change that page first, then every copy.

- **Architecture overview, essential commands, and rule index**: see [`CLAUDE.md`](./CLAUDE.md).
- **Topic-by-topic standards** (coding, naming, accessibility, security, perf, PR process, etc.): see [`.claude/rules/`](./.claude/rules/) (Markdown) — mirrored at [`.cursor/rules/`](./.cursor/rules/) (Cursor `.mdc`).
- **PR review heuristics for Cursor BugBot**: see [`.cursor/BUGBOT.md`](./.cursor/BUGBOT.md).
- **Sub-agents** for code review and focused audits: see [`.cursor/agents/`](./.cursor/agents/) and [`.claude/agents/`](./.claude/agents/).

The `.cursor/rules/*.mdc` and `.claude/rules/*.md` files are kept in sync. When you change a rule, update **both** copies in the same change.
