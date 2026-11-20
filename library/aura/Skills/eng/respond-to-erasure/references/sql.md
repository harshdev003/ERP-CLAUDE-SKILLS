# SQL recipes

Production Postgres is `aura-db-prod` (project `firebase-auratech16`), reached through the Cloud
SQL proxy on `localhost:5432`. **Default: each DB's owner runs its own statements** (owners in
data-map.md), on a go typed where that owner works; a go relayed by a third party is refused, so
give one paste-ready go line per owner. The agent may run them only when the user explicitly
allows it in this session and approves each call (manual permission mode). Either way: one
statement at a time, each delete followed by a count from a separate session.

Throughout, replace `{uid}` and `{email}` with the person's values.

## Discovery

1. List the databases:
   ```sql
   SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY 1;
   ```
2. In **each** database, find the columns that can point at the person. This catches foreign keys
   and the non-FK columns that `create.sql` doesn't show:
   ```sql
   SELECT table_name, column_name FROM information_schema.columns
   WHERE table_schema = 'public'
     AND data_type IN ('text', 'character varying')
     AND (column_name IN ('userId','user_id','uid','actorId','referrer','referree','recipientId',
                          'redeemedBy','ownerId','familyPlanPrimary','familySharing','coachId',
                          'authorId','email','userEmail','recipient')
          OR (table_name = 'user' AND column_name = 'id'))
   ORDER BY 1, 2;
   ```
3. Build one count query per database from that list. Email-like columns match
   `lower(col) = lower('{email}')`; the rest match `col = '{uid}'`. Report only non-zero rows:
   ```sql
   SELECT * FROM (
     SELECT 'user.id' t, count(*) n FROM "user" WHERE "id" = '{uid}'
     UNION ALL SELECT 'emailBackup.userId', count(*) FROM "emailBackup" WHERE "userId" = '{uid}'
     -- … one line per (table, column) from step 2
   ) s WHERE n > 0;
   ```
   Large unindexed tables make this slow (minutes). Run it in the background.

If you write a CTE for the person's id, call the column `pid`, not `id`.

## Export (access requested)

For every non-zero table except the bare `"user"` row (already in the pack from step 2), save
the rows to `$PACK/raw/pg_{db}_{table}.json` before deleting:

```sql
SELECT * FROM "notificationBackup" WHERE "userId" = '{uid}';
```

## Delete

One statement per call, in auto-commit. Child tables first, `"user"` last.

```sql
SET lock_timeout = '5s';
DELETE FROM "emailBackup" WHERE "userId" = '{uid}' RETURNING "id", "createdAt";
```
```sql
SET lock_timeout = '5s';
DELETE FROM "user" WHERE "id" = '{uid}' RETURNING "id", "deletedAt";
```

Then verify, from a session that did **not** run the delete:

```sql
SELECT current_database(), count(*) FROM "user" WHERE "id" = '{uid}';   -- expect 0
SELECT pid, application_name, state, xact_start FROM pg_stat_activity
WHERE datname = current_database() AND state LIKE 'idle in transaction%';  -- expect none
```

A lock timeout means another session holds the row: usually the human's own uncommitted delete.
Ask them to commit in that tab, rather than terminating their session.

**Rows that point at the person from someone else's record** (they referred, coached or own a
family plan): `UPDATE … SET "referrer" = NULL` instead of deleting the other person's row.

**Billing rows kept for tax:** anonymise instead of deleting. Null every PII column and replace
`userId` with a constant such as `'erased'`, once the FK constraints allow it. Decide per case
with the user.

## BigQuery

```sql
-- find (TO_JSON_STRING catches the person anywhere in the row)
SELECT name, createdAt FROM `aura-main.firebase.raw_events` r
WHERE STRPOS(TO_JSON_STRING(r), '{uid}') > 0;

-- delete exactly what the find returned (same predicate), once the streaming buffer's
-- oldest entry is later than every one of these rows
DELETE FROM `aura-main.firebase.raw_events`
WHERE messageId IN (
  SELECT messageId FROM `aura-main.firebase.raw_events` r
  WHERE STRPOS(TO_JSON_STRING(r), '{uid}') > 0);
```

Re-run the find afterwards: it must return 0. Rows still in the streaming buffer can't be deleted
yet; wait and repeat rather than narrowing the predicate.

Also check `firebase.events`, `firebase.dev_events` and `firebase.user_following*` by `userId`,
and every `aurie_cost` table with a `user_id` column.

The IntelligenceService `llmCall` ledger (`aura-main.llm.llm_call`, Aurie users) requires a
`createdAt` filter on every statement, the delete included:

```sql
-- find; keep the openaiIds for the OpenAI deletes
SELECT openaiId, project, createdAt FROM `aura-main.llm.llm_call`
WHERE createdAt >= TIMESTAMP('2000-01-01') AND userId = '{uid}';

-- delete, same predicate
DELETE FROM `aura-main.llm.llm_call`
WHERE createdAt >= TIMESTAMP('2000-01-01') AND userId = '{uid}';
```

Re-run the find: it must return 0. IntelligenceService streams rows in, so a row written in the
last few minutes may still be in the streaming buffer; wait and repeat.
