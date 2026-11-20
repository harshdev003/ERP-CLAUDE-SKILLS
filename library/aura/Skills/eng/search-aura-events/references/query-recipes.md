# Raw SQL recipes

`scripts/search_events.py` covers every pivot below and adds the cost gate, the
retention check and the PT rendering. Reach for raw SQL only when you need a shape
the script does not build — an aggregate, a join, a window function.

Source: the [Big Query page](https://app.notion.com/p/aurahealth/Big-Query-11575e23af20802dbcfdf76c1c2e3c5e)
in Notion, corrected against the live table (the partition predicates below are not
on that page — see "Partition pruning" at the end, which is the part that is easy to
get wrong and expensive when you do).

Run them through `eng:query-bigquery`:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/query-bigquery/scripts/query.py --sql "<sql>" --dry-run
```

## Discover the vocabulary

```sql
SELECT name, COUNT(*) AS n
FROM `aura-main.firebase.raw_events`
GROUP BY name ORDER BY n DESC
```

Reads the `name` column only, so it is far cheaper than anything touching `data`.

## User lifecycle — "when was this user updated / deleted / role-changed"

```sql
SELECT createdAt,
       DATETIME(createdAt, "America/Los_Angeles") AS pt,
       name, data
FROM `aura-main.firebase.raw_events`
WHERE JSON_VALUE(data, '$.userId') = '<uid>'
  AND name IN ('UserCreated','UserUpdated','UserDeleted','RoleUpdated',
               'UserAttributed','UserLoggedIn','EmailValidated','EmailInvalidated')
ORDER BY createdAt ASC
```

## Everything for one user, newest first

```sql
SELECT createdAt, name
FROM `aura-main.firebase.raw_events`
WHERE JSON_VALUE(data, '$.userId') = '<uid>'
ORDER BY createdAt DESC LIMIT 10
```

## One event type for one user

```sql
SELECT *
FROM `aura-main.firebase.raw_events`
WHERE name = 'UserUpdated'
  AND JSON_VALUE(data, '$.userId') = '<uid>'
ORDER BY createdAt DESC
```

## Experiment assignment for a user

```sql
SELECT createdAt, name, data
FROM `aura-main.firebase.raw_events`
WHERE JSON_VALUE(data, '$.userId')       = '<uid>'
  AND JSON_VALUE(data, '$.experimentId') = '<experimentId>'
  AND name = 'ExperimentAssigned'
ORDER BY createdAt DESC
```

## Experiment changes over time

```sql
SELECT createdAt, DATETIME(createdAt, "America/Los_Angeles") AS pt, name, data
FROM `aura-main.firebase.raw_events`
WHERE name = 'ExperimentUpdated'
  AND JSON_VALUE(data, '$.experimentId') = '<experimentId>'
ORDER BY createdAt DESC LIMIT 10
```

## By any other JSON key

```sql
SELECT createdAt, name, data
FROM `aura-main.firebase.raw_events`
WHERE JSON_VALUE(data, '$.serieId') = '<serieId>'
ORDER BY createdAt DESC LIMIT 10
```

## Partition pruning — the part that is easy to get wrong

Add this to any recipe above that has a time bound. Both halves matter:

```sql
  AND (_PARTITIONTIME IS NULL OR _PARTITIONTIME >= TIMESTAMP('2026-09-01'))
```

- **`createdAt` does not prune.** The table is MONTH-partitioned on *ingestion*
  time, so `WHERE createdAt >= …` reads every partition.
- **`IS NULL` is not optional.** Streaming-buffer rows have a NULL `_PARTITIONTIME`
  and that is where the newest events are. A bare `_PARTITIONTIME >= …` silently
  drops them, and `INFORMATION_SCHEMA.PARTITIONS` reports that partition as empty
  while it holds thousands, so the metadata will not warn you.
- **Do not add a `_PARTITIONTIME <=` ceiling.** Ingestion time is not event time, so
  a ceiling on ingestion drops rows that are inside the event-time window you asked
  for.
- **`LIMIT` does not reduce bytes scanned.** BigQuery reads the columns, then
  limits. Narrow the columns or the partitions instead.

**Canonical source for all of the above, with the measured scan sizes and the
reasoning: `Tools/BigQuery` → *Firebase event stream — firebase.raw_events*.** Read
it before writing a query this file does not cover.

## Adjacent, non-event sources

- User age / country: `mixpanel-prod-1.mixpanel_nessie_day_partitioned_963821.mp_people_data_view`
- Per-user cost: `aura-main.aurie_cost.*` — see `eng:query-bigquery`
