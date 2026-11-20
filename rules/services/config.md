# Configuration (ERP services)

Adapted from a production team's config rules (`library/aura/Rules/BusinessService/claude/config.md`).

- All configuration is read in **one config module**; other modules import named
  values from it (destructured at the top of the file).
- **Environment variables** are for secrets, connection strings, per-environment URLs
  and per-environment feature flags.
- **Config constants** (in code) are for values that are the same everywhere: timeouts,
  page sizes, business constants.
- When an environment variable is added or changed, update the repo `README.md` and
  `.env.example` in the same change (names and meaning only — never values).
- `.env` files are git-ignored; secrets never go in the repo (security.md A1).
- Defaults keyed on environment (`NODE_ENV`, etc.) must be overridable, and test/CI
  must never point at production.
- **Per-tenant settings** (tax registration, fiscal year, base currency, numbering
  series, approval thresholds) live in the database, not in config files.
- Locales: store canonical codes (`en_IN`), convert only at the edges; dates in UTC in
  storage, tenant timezone in display.
