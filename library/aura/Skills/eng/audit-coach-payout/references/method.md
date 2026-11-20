# Method, provenance, and data-availability limits

Why the archived rows this skill reads are the *same* numbers a hand-run SQL audit
produces, where that stops being true, and what to do instead.

## The archive is the SQL, not a reimplementation

`PaymentService/src/jobs/analytics.js` runs on a schedule and, for each configured type,
reads the matching `.sql` file off disk and stores the raw result set:

```js
const sqlPath = path.join(SQL_FOLDER_PATH, `${type}.sql`);
const query = await readFile(sqlPath, 'utf-8');
const result = await postgres.executeMany(query, {context});
// -> upsert into "analytics": { data: { result } }, id = `${type}-${year}-${month}`
```

So a row of type `monthlyListenBreakdown` **is** the verbatim output of
`PaymentService/sql/monthlyListenBreakdown.sql`, executed on the 1st for the prior
month. The read side (`src/models/analytics.js`) only unwraps and filters it:

```sql
SELECT ... FROM (
  SELECT "year","month","week","day", jsonb_array_elements("data"->'result') AS "obj"
  FROM "analytics" WHERE "type" = '<type>' AND <period bounds>
) AS "temp" WHERE "obj"->>'coachId' = '<uid>'
```

Nothing is recomputed, re-aggregated or re-filtered. Reading the archive and running the
SQL by hand differ in exactly one way: **when the query ran.**

## Payout-time snapshot vs a later re-run

The archive holds the value at payout time — what the coach was actually paid. Running
the same SQL months later can return a slightly larger number, because late-arriving
listens keep landing in `userContentListen` after the payout.

Observed on the original Hannah Castillo audit (ENGSUP-115/153): a live re-run on
2026-04-02 gave January and February totals **3 higher** than the archive, on both the
capped and no-cap rows. Because both rows shifted together, the derived
`excluded_by_cap` was identical — which is why that row is the one to quote.

**Prefer the archive.** It is reproducible, it matches the statement the coach received,
and it does not drift between runs.

## Retention: `userContentListen` keeps ~40 days

This is the trap the skill exists to prevent. The table is pruned to a rolling window
(measured 2026-09-01: earliest row `2026-07-23`, i.e. ~40 days). A live query for an
older month returns **0 rows and no error**, which reads as "this coach had no listens"
rather than "the data is gone".

| Question | Age of month | Source |
|---|---|---|
| Monthly cap / no-cap totals | any | `analytics` archive (always) |
| Per-listener burst detail | inside ~40 days | `POST /payouts/listens/aggregate` |
| Per-listener burst detail | older | `userContentListenBackup` (SQL below) |

## Earliest supported month: 2025-11

`monthlyListenTotalNoCap` was added 2025-11-07 (PaymentService `e03d427f`, "Add cap for
total listens"). Archived rows before that month don't follow the Nov-onward pattern —
October 2025's capped breakdown sits 437 *below* its own `premiumPaidListens`, and its
no-cap row equals the capped figure. The script refuses ranges starting earlier.

## When the identity breaks

`gap == excluded_by_cap - residual` is an accounting identity over four independently
sourced numbers. If it fails for a month, one of these is true — check in this order:

1. **A month has no archived row** (job failure, or not yet paid). Shows as `-`.
2. **The tier columns didn't partition the rows.** Row 8 is the sum of `<500`,
   `500-1000`, `1000-2000`, `2000-5000`, `>5000` over `contentListenedCount`. These are
   exhaustive and disjoint for any non-NULL value, so the sum equals the query's row
   count and must equal `monthlyCombined.premiumPaidListens`. A NULL
   `contentListenedCount` would fall out of all five buckets. Compare the two rows.
3. **The coach has music/soundscape content.** The payout breakdown excludes
   `music`, `soundscape` and `podcast`; `monthlyCombined.premiumPaidListens` excludes
   only `podcast`. Check `premiumPaidMusicListens` — if it's non-zero the two rows
   legitimately differ and the residual needs interpreting per content type.
4. **Something genuinely new.** Escalate rather than attributing it to the cap.

## Per-listener detail for purged months

`userContentListenBackup` carries the same columns as the live table, including
`dailyCoachListenedCount`. Run query 1 first — if the backup doesn't cover the month
either, the per-listener question is unanswerable and should be said so plainly.

```sql
-- 1) coverage check
select min("createdAt") as earliest, max("createdAt") as latest, count(*) as rows
from "userContentListenBackup";

-- 2) monthly cap vs no-cap (should reproduce the audit rows)
select
  date_trunc('month', l."createdAt")::date                   as "month",
  count(*) filter (where l."dailyCoachListenedCount" <= 6)   as "payoutWithCap",
  count(*)                                                   as "noCap",
  count(*) filter (where l."dailyCoachListenedCount" >  6)   as "excludedByCap"
from "userContentListenBackup" l
join "content" c on c."id" = l."contentId"
join "coach"   k on k."id" = c."authorId"
where k."id" = '<COACH_UID>'
  and l."createdAt" >= '<YYYY-MM-01>' and l."createdAt" < '<YYYY-MM-01 exclusive>'
  and l."premiumPaid" is true
  and (c."deletedAt" is null or c."deletedAt" > l."createdAt")
  and c."type" not in ('music', 'soundscape', 'podcast')
  and k."podcaster" is not true
  and k."deletedAt" is null
group by 1 order by 1;

-- 3) who drove the cap
select
  l."userId",
  date_trunc('month', l."createdAt")::date                  as "month",
  count(*) filter (where l."dailyCoachListenedCount" > 6)   as "excluded",
  count(*)                                                  as "noCap",
  max(l."dailyCoachListenedCount")                          as "worstDay"
from "userContentListenBackup" l
join "content" c on c."id" = l."contentId"
join "coach"   k on k."id" = c."authorId"
where k."id" = '<COACH_UID>'
  and l."createdAt" >= '<YYYY-MM-01>' and l."createdAt" < '<YYYY-MM-01 exclusive>'
  and l."premiumPaid" is true
  and (c."deletedAt" is null or c."deletedAt" > l."createdAt")
  and c."type" not in ('music', 'soundscape', 'podcast')
  and k."podcaster" is not true
  and k."deletedAt" is null
group by 1, 2
having count(*) filter (where l."dailyCoachListenedCount" > 6) > 0
order by 2, 3 desc limit 50;
```

Reaching the database needs the Cloud SQL Auth Proxy — see `eng:setup-backend-database`.
Redact listener UIDs to a prefix in any output (`Compliance/Privacy`).

## Validation record

The skill was checked against the original hand-built audit
([Hannah Payout Audit sheet](https://docs.google.com/spreadsheets/d/1-PjkKcx7JGiU6dNXk0qX9luwoksI6Rgk8sfAijz2KrA/edit),
Nov 2025 - Mar 2026), which was produced by running the two `.sql` files live on
2026-04-02:

| Row | Nov | Dec | Jan | Feb | Mar |
|---|---:|---:|---:|---:|---:|
| Payout - All listens | exact | exact | exact | exact | exact |
| Payout - Premium Paid | exact | exact | exact | exact | exact |
| **Excluded by cap** | **336** | **358** | **384** | **273** | **356** — all exact |
| Query used for payout | exact | exact | -3 | -3 | exact |
| Same query, no cap | exact | exact | -3 | -3 | exact |

The only deltas are the two rows affected by the payout-time vs re-run timing described
above, and they cancel in the row that carries the conclusion.
