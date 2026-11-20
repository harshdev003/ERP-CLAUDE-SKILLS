# Naming (ERP services)

Adapted from a production team's naming rules (`library/aura/Rules/BusinessService/claude/naming.md`).

## Code
- Files: follow the repo's language convention (JS/TS `camelCase.ts`, Python `snake_case.py`).
- Functions and variables: `camelCase` (JS/TS) / `snake_case` (Python).
- Classes and types: `PascalCase`. Constants: `UPPER_SNAKE_CASE`.
- Descriptive names over short ones; name helpers for **what they produce**, not where
  they are called from.
- Handler names: `create`, `retrieve`, `list`, `update`, `remove`, plus domain verbs
  (`post`, `approve`, `cancel`, `reverse`). For reserved words use a descriptive name
  (`deleteInvoice`) and map it at the boundary.
- Use UUIDs for any synthetic identifier; don't hand-roll string ids.

## Domain vocabulary
Use one word per concept everywhere (code, API, UI, docs): e.g. `customer` (not
client/buyer), `supplier` (not vendor/seller), `item` (not product/SKU, unless both
exist), `invoice` for sales and `bill` for purchases, `warehouse`, `journalEntry`.
Record new terms in `docs/glossary.md` of the ERP-CLAUDE-SKILLS repo.

## Repositories
- Repos: `ERP-<PART>-SERVICE`, all caps, dash-joined (see rules/CLAUDE.md).

## Branch names
- `<type>/<ticket-id>-<short-slug>`, type one of `feature`, `bugfix`, `refactor`,
  `chore`, `quality` — e.g. `feature/erp-42-invoice-pdf`.
- Stacked PRs (stack-prs skill): `<firstname>/<ticket-id>-<slug>`.

## Commits
- Conventional commits with a scope: `feat(invoices): add partial payments [ERP-42]`.
  Subject under ~60 characters; body explains the why.
