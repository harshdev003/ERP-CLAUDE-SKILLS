# CLAUDE.md — <ERP-XXX-SERVICE>

Copy this file to the root of a new `ERP-*-SERVICE` repo as `CLAUDE.md` and fill in
the `<…>` parts. Shared rules live in `~/ERP-CLAUDE-SKILLS/rules/services/` — don't copy
them here; link to them and add only what is specific to this repo.

**Security review:** every PR is checked against
`~/ERP-CLAUDE-SKILLS/rules/services/security.md` (sections A + <B | C | E>).

## Purpose
<One paragraph: what this service does in the ERP and what it does not own.>

## Commands

```bash
<install>          # install dependencies
<dev>              # start the dev server
<test>             # run all tests
<test-one> <path>  # run one test file
<lint>             # lint — must pass before every commit
<migrate>          # apply database migrations (backend only)
```

## Architecture
<Folder layout and layer responsibilities — see shared architecture.md.>

| Layer | Path | Responsibility |
|---|---|---|
| HTTP | `<src/http>` | thin handlers |
| Domain | `<src/domain>` | business logic |
| Data | `<src/repositories>` | tenant-scoped data access |
| Integrations | `<src/services>` | external systems |
| Jobs | `<src/jobs>` | background work |

**Key files:** `<config>`, `<errors registry>`, `<logger>`, `<request context>`.

## Repo-specific rules
- <decisions that apply only to this repo>

## Git workflow
- Never commit to `main`; branch per naming.md, PR opens as a draft.
- Conventional commits with scope; ticket id at the end of the subject.
- Commits and pushes as the `harshdev003` account only.
