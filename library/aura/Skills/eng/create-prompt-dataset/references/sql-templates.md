# As-of SQL for the user to run

Use SQL only for an input no stored request carries completely. The user runs it against the
prod `intelligenceDB` through their own tunnel or client and saves the result as a file. You
never execute it.

## Rules

- First statement: `SET default_transaction_read_only = on;`.
- **One statement for every case**, with the cases in a `VALUES` list of `(userId, asOf)`.
  Take both values from the manifest (ledger data), never from typed text, and write each as
  a SQL string literal built in code:
  - **user id:** any Aura id format is valid (`:`, `.`, `=`, `@`, `+` all occur, e.g.
    provider-prefixed ids), so don't restrict the character set. Double every `'`, and refuse
    a value that contains a backslash or a control character.
  - **asOf:** parse it as a timestamp and write the parsed value back in ISO 8601; refuse
    anything that doesn't parse.
- Everything **as of T**: `createdAt <= asOf`, deleted only if `deletedAt > asOf`, expired
  only if `expiryAt <= asOf`, and the input's own relevance window evaluated at asOf.
- Mirror the new prompter's filter, order and limit exactly, so the rows equal what the
  prompter would have read at T.
- Return **one JSON value** (`json_agg` of one object per case) so the export is a single
  cell. GUI clients wrap it as `{"<query>": [{"json_agg": "<json string>"}]}`; unwrap in code.
- The file lists real user ids: keep it in the scratch directory and offer to delete it.

## Template: active running memories at T

```sql
SET default_transaction_read_only = on;
SELECT json_agg(x ORDER BY x."userId")
FROM (
  SELECT
    c."userId",
    c."asOf",
    COALESCE(
      json_agg(
        json_build_object(
          'createdAt', m."createdAt",
          'memory', m."memory",
          'recall', m."recall"
        ) ORDER BY m."createdAt" DESC
      ) FILTER (WHERE m."memory" IS NOT NULL),
      '[]'::json
    ) AS "runningMemories"
  FROM (VALUES
    ({userId literal}, {asOf literal}::timestamptz)
    -- one row per case; literals built in code as described under Rules
  ) AS c("userId", "asOf")
  LEFT JOIN LATERAL (
    SELECT "createdAt", "memory", "recall"
    FROM "memory"
    WHERE "userId" = c."userId"
      AND "type" = 'running memory'
      AND "createdAt" <= c."asOf"
      AND ("deletedAt" IS NULL OR "deletedAt" > c."asOf")
      AND ("expiryAt" IS NULL OR "expiryAt" > c."asOf")
    ORDER BY "createdAt" DESC
    LIMIT 20
  ) m ON TRUE
  GROUP BY c."userId", c."asOf"
) x;
```

Adapt the inner `SELECT` for other inputs: conversation summaries (`conversation`, filter
`lastMessageAt` in the window, `deletedAt`, `incognito IS NOT TRUE`), or a feature's own state
(`completedAt`, `removedAt` on its table). Keep the outer shape.

## Matching the export back

The export's `asOf` comes back in the client's session time zone (`+05:30`, `-07:00`). Match
cases on the parsed instant, not on the string.
