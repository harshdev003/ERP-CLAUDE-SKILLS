# ERP service rules

The shared engineering rules for every `ERP-*-SERVICE` repo. Claude reads them when
working in those repos (see `rules/CLAUDE.md` → "Working in an ERP service repo"), and
the `review-pr` skill uses them as its review rubric.

| File | Covers | Applies to |
|---|---|---|
| [security.md](security.md) | Security & privacy checklist, rule IDs A1–F | all repos (sections by repo type) |
| [architecture.md](architecture.md) | Layers, thin controllers, modules, documents & postings, multi-tenancy | backend (web/app: module boundaries) |
| [coding.md](coding.md) | Code style, handlers, domain layer, money | all |
| [database.md](database.md) | Migrations, schema, ledger tables | backend |
| [error-handling.md](error-handling.md) | Error keys, patterns, transactions | backend |
| [logging.md](logging.md) | Levels, safe meta, audit vs logs | all |
| [naming.md](naming.md) | Code names, domain vocabulary, branches, commits | all |
| [config.md](config.md) | Env vars vs constants, per-tenant settings | all |
| [performance.md](performance.md) | Paging, jobs, caching | backend |
| [documentation.md](documentation.md) | Comments, API docs, README | all |
| [CLAUDE.template.md](CLAUDE.template.md) | Starter `CLAUDE.md` for a new service repo | copy into each repo root |

## Precedence
1. The repo's own `CLAUDE.md` / `.claude/rules/` (repo-specific decisions)
2. These shared rules
3. General best practice

When a repo rule contradicts a shared rule, say so in the PR and update one of them.

## Origin
Written for this ERP from the per-service rules of a production engineering team
(kept unchanged for reference in `library/aura/Rules/`). Each file names its source.
