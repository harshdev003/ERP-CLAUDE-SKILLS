# Database conventions (ERP services)

Adapted from a production team's database rules (`library/aura/Rules/BusinessService/claude/database.md`).

## Migrations
- Every schema change is a **versioned migration** checked into the repo (the repo's
  migration tool, or `database/migrations/NNNN_description.sql`). Never edit an applied
  migration; add a new one.
- Migrations are idempotent where possible (`IF NOT EXISTS` / `IF EXISTS`).
- Data migrations are separate from schema migrations, dry-run first, and print counts
  only (security.md A3).
- **Confirm the migration landed on production before releasing code that depends on
  it.** Code that writes a missing column loses every row it tries to insert.
- Renames go through add-new → dual-write → backfill → switch reads → drop-old.
- Take a backup before a destructive migration.

## Schema conventions
- Primary key: UUID (or bigint identity plus a UUID public id). Never expose sequential
  internal ids in URLs.
- Every tenant-owned table has `tenantId` NOT NULL, indexed, and part of the unique
  constraints that must be per tenant (e.g. `UNIQUE (tenantId, invoiceNumber)`).
- Timestamps: `createdAt`, `updatedAt` (timestamptz, UTC); `createdBy`, `updatedBy`
  user ids on business records.
- Soft delete (`deletedAt`) only for master data; posted financial records are never
  deleted.
- Money: `NUMERIC(19,4)` or integer minor units, plus a `currency` column. Never `REAL`/`FLOAT`.
- Quantities: `NUMERIC` with an explicit scale per unit of measure.
- Foreign keys declared and indexed; index columns used in filters and joins.
- Use typed columns for values you will group or filter on; don't hide them in JSON.

## Hot tables
- Build indexes on large, busy tables without long locks (e.g. Postgres
  `CREATE INDEX CONCURRENTLY`), one statement at a time.

## Ledger tables
- `journalEntry` (header) and `journalLine` (lines) with a check or test that each
  entry's debits equal credits.
- Stock moves are append-only; on-hand quantity is derived (or a cached projection
  that can be rebuilt from moves).
