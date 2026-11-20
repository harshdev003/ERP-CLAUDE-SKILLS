# Documentation (ERP services)

Adapted from a production team's documentation rules.

## Code comments
- Default to **no comments**. Add one only when the *why* is non-obvious: a hidden
  constraint, a legal/tax rule, a workaround for an upstream quirk, an invariant.
- Don't restate what the code does.
- **No ticket ids in source comments** — they go in the commit and PR.
- Public functions of a module's API get a short doc comment (params, return, errors).

## API documentation
- The API is documented in one place (OpenAPI spec in the backend repo, or a shared
  Postman collection) — pick one per repo and keep it current.
- A new endpoint or parameter is not done until it is documented: required
  role/permission, what it does and deliberately does not do, error keys and statuses.

## README (per repo)
Include: purpose, local setup, environment variables (names + meaning), jobs and
scripts, health endpoints, database setup/migrations, how to run tests.
Don't put detailed endpoint docs in the README.

## Domain docs
- Business rules (tax calculation, posting rules, numbering series, approval flows)
  are written down in `docs/` of the repo that owns them, with worked examples.
