---
description: Database schema and migration conventions for Aura services
globs: database/**/*.sql
alwaysApply: false
---

# Database Conventions

> **Shared by every backend service.** This file is identical in all 13 services, in `.claude/rules/` and `.cursor/rules/`. Change every copy in the same batch, and compare git blob hashes to check. Rules that apply to one service only go in that service's `.claude/rules/service-specific.md` (and `.cursor/rules/service-specific.mdc`).

## Schema files

- **`database/create.sql`**: Canonical full schema for a fresh install (all tables, columns, indexes).
- **`database/update.sql`**: Incremental changes for existing databases; applied in order. New changes are **appended at the bottom**.
- **Neither file is the production schema.** They drift: production has tables and columns they lack, and they list some it doesn't have. SQL that runs against prod (a backfill, an audit, an erasure) takes its tables and columns from production `information_schema`, never from `create.sql` (ENGSUP-160).

## Adding a new attribute (column)

When you add a new column to an existing table:

1. **Update `database/create.sql`**  
   Add the column to the correct table definition so new installs get the full schema.

2. **Append to `database/update.sql`**  
   Add an `ALTER TABLE` at the **bottom** of the file so existing databases receive the change.

Use `ADD COLUMN IF NOT EXISTS` in `update.sql` when the change might be applied multiple times or in different environments.

**Example (new column `"newField"` on table `"user"`):**

In **create.sql**, add the column in the right place in the table definition:

```sql
"newField" TEXT,
```

At the **bottom** of **update.sql**:

```sql
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS "newField" TEXT;
```

If the column has a default or constraint, mirror it in both places, e.g.:

```sql
-- create.sql (in table definition)
"newField" TEXT DEFAULT 'value',

-- bottom of update.sql
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS "newField" TEXT DEFAULT 'value';
```

## General conventions (from existing schema)

- **Identifiers**: Use double-quoted identifiers for table and column names (e.g. `"user"`, `"createdAt"`).
- **Primary key**: Prefer `"rowid" SERIAL PRIMARY KEY` plus a business `"id"` (TEXT or uuid with `DEFAULT uuid_generate_v4()`).
- **Timestamps**: Use `TIMESTAMPTZ`; typical columns: `"createdAt"`, `"updatedAt"`, `"deletedAt"` with `DEFAULT (NOW() AT TIME ZONE 'utc')` for `createdAt` where required.
- **Indexes**:
  - Unique index on business id: `CREATE UNIQUE INDEX "tableName_id_idx" ON "tableName" ("id");`
  - Index on foreign keys and frequently filtered columns: `CREATE INDEX "tableName_columnName_idx" ON "tableName" ("columnName");`
  - Composite indexes when needed: `CREATE INDEX "tableName_col1_col2_idx" ON "tableName" ("col1", "col2");`
- **Foreign keys**: Define in the column with `REFERENCES "otherTable" (id)` (or the relevant key column). Add indexes on FK columns used in joins or filters.
- **New indexes in update.sql**: Use `CREATE INDEX IF NOT EXISTS "tableName_columnName_idx" ON "tableName" ("columnName");` so the script is idempotent.
- **Hot-table indexes use `CONCURRENTLY`**: For tables on the live write path (in the Intelligence Service: `dialogue`, `conversation`, `usage`, `memory`, …), build new indexes with `CREATE INDEX CONCURRENTLY IF NOT EXISTS …`. Plain `CREATE INDEX` holds an `ACCESS EXCLUSIVE` lock for the duration of the build and stalls every insert; `CONCURRENTLY` lets writes continue at the cost of two scans + a brief wait for in-flight transactions. Note: `CONCURRENTLY` cannot run inside a transaction block, so paste each statement separately into psql (and turn off auto-transaction in GUI clients). If a `CONCURRENTLY` build fails partway, an `INVALID` index is left behind — drop it before re-running. ([ENG-676](https://linear.app/aura-health/issue/ENG-676))

## Releasing code that writes a new column

The service does not run `update.sql` on deploy; the user applies it by hand. So **confirm the `ALTER` landed on the production database before releasing code that writes the new column.** Through the prod tunnel, `SELECT current_database(), current_schema();` must return the service's database and `public`, and `\d "tableName"` must show the column. Otherwise every insert that names the column fails and the row is lost: the `llmCall` ledger dropped about 150 coach rows in ten minutes when the migration ran against another database, and they had to be rebuilt from the GCP logs by `transaction` ID.

## Columns for values an analysis compares

When a test or rollout needs per-row fields that will be grouped or filtered on later (e.g. the prompt-cache key and its arm on `llmCall`), add **dedicated typed columns** (`promptCacheKey`, `promptCacheBuckets`, …) rather than a catch-all `metadata` JSONB. Plain columns keep the comparison a simple `GROUP BY`, and an existing JSONB column with another meaning (`usage` holds OpenAI's raw token counts) must not be borrowed for it.

## JSONB conventions

Conventions for working with them:

- **Verify key shapes against live data before adding analytics paths.** JSONB keys aren't enforced by the schema and can drift from your assumptions. Before adding an allowlisted JSONB extraction path (e.g. for an aggregation endpoint), run `SELECT jsonb_object_keys(column->'subpath'), COUNT(*) FROM tableName WHERE … GROUP BY 1 ORDER BY 2 DESC` against a representative window. Don't trust the field name in the writer code — trust what's actually persisted.
- **camelCase vs snake_case**: external API responses (OpenAI, ElevenLabs) are usually snake_case, but services may normalise to camelCase before persisting. `services/openai.js` `parseUsage()` rewrites `prompt_tokens`/`completion_tokens` to `promptTokens`/`completionTokens` before writing `metrics.llm`. If your analytics path uses the source casing, every value comes back null. ([ENG-676](https://linear.app/aura-health/issue/ENG-676))
- **Don't add JSONB expression indexes for analytics aggregations.** Indexes like `((metrics->'llm'->>'ttft')::numeric)` only help queries with a `WHERE` filter on that JSONB path. `PERCENTILE_CONT` / `SUM` / `AVG` over a windowed scan reads every row in the window regardless — Postgres uses the `createdAt` btree to prune the window, then does the JSONB extractions inline per row. The expression indexes sit unused but still cost write overhead on every insert. Only add them when an endpoint actually filters on those values. ([ENG-676](https://linear.app/aura-health/issue/ENG-676) — verified via `EXPLAIN ANALYZE` against the prod `dialogue` table, then dropped before merge.)

## Dropping or renaming columns

- **Dropping**: Prefer `DROP COLUMN IF EXISTS` in `update.sql`; avoid removing the column from `create.sql` until you are sure no new installs should ever have it (or document that create.sql is for current installs only).
- **Renaming**: Use a `DO $$ ... END $$;` block in `update.sql` to check for the old name and rename, then `ADD COLUMN IF NOT EXISTS` for the new name so both old and new installs converge.

## Data migrations

- One-off data or structural migrations belong in `database/migrate.sql` (or a dedicated migration file), not in `update.sql`, when they are not idempotent or are complex.
