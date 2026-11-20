---
name: search-aura-events
description: >
  Answer "what happened to this user / experiment / subscription, and when" from the Aura
  event stream in BigQuery (`aura-main.firebase.raw_events`), instead of hand-writing the SQL
  each time. Pivots by Firebase UID, by event name, by experiment id, by any `data` JSON key,
  and by date range; renders a timeline with UTC and Pacific side by side. Prices every scan
  before running it and states the retention boundary when the window predates it. Trigger on
  "search aura events", "what happened to user {uid}", "when was this user deleted", "was this
  account deleted and recreated", "lifecycle timeline for {uid}", "when did this subscription
  cancel", "when was this user assigned to {experiment}", "what changed on experiment {id}",
  "find the event for {uid}", "query the firebase event stream", or a Firebase UID paired with
  a question about history. Do NOT use for current state (`eng:call-aura-api`), ad-hoc
  warehouse SQL (`eng:query-bigquery`), or application logs (`eng:search-gcp-by-user`).

metadata:
  version: "1.2.1"
knowledge:
  reads:
    - Guide/Index         # baseline routing aid (universal)
    - Tools/BigQuery      # aura-main access via GCP_KEY, dry-run + scan-cost conventions
    - Compliance/Privacy  # UserCreated/UserUpdated carry email + givenName; Coach* carry a real name
---

# search-aura-events

Read the Aura event stream — `aura-main.firebase.raw_events` — to reconstruct what
happened to a user, an experiment, or a subscription, and when. Read-only.

## Goal

Answer *history* questions that no current-state source can answer. It is the
event-stream counterpart to the current-state skills: `eng:call-aura-api` tells you
what a record looks like **now**, `eng:search-gcp-by-user` tells you what the
**services** logged, and this tells you what the **domain** emitted.

That distinction is the whole reason the skill exists. In the
[PROD-6468](https://linear.app/aura-health/issue/PROD-6468) investigation, Firebase
Auth, the `deletedAt` column and `GET /users/<uid>` all came back clean — because the
record had been deleted and re-created. The `UserDeleted` + `RoleUpdated role=null`
pair was only ever visible here.

## Prerequisites

```bash
test -n "$GCP_KEY" && echo "gcp: OK" || echo "gcp: MISSING"
python3 -c "import google.cloud.bigquery" 2>/dev/null && echo "lib: OK" \
  || echo "lib: MISSING — python3 -m pip install --user google-cloud-bigquery"
```

Both are hard requirements. There is no BigQuery MCP, so this is API-first by
necessity (`/CLAUDE.md` → "API-first by necessity"); auth is the same `GCP_KEY`
service account the rest of the BigQuery family uses.

## The three things that fail silently

Each returns *plausible wrong output* instead of an error, so none of them surfaces
anywhere. The script enforces all three; this section is what you need to know
before reading its output.

**The measurements and the full rationale live on `Tools/BigQuery` →
*Firebase event stream — firebase.raw_events*** — they are properties of the table,
not of this skill, and the next person to hand-write SQL against it will not be
running this skill. Read that section before writing your own query.

### 1. `firebase.events` is a decoy — always route to `raw_events`

A different, flat table with **no user-lifecycle events at all**. It is the obvious
first guess, and it answers a deletion lookup with **zero rows rather than an
error**, which reads as "it never happened" — it cost real time in
[PROD-6468](https://linear.app/aura-health/issue/PROD-6468).

The table name is a constant in the script; there is no `--table` flag. To explain
the routing to someone:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/search-aura-events/scripts/search_events.py --why
```

### 2. `createdAt` does not prune — only `_PARTITIONTIME` does

The table is MONTH-partitioned on **ingestion time**, not on `createdAt`, so a
`createdAt` filter reads every partition. `--since` emits both predicates.

`--until` emits only the `createdAt` half, deliberately: ingestion time is not event
time, so a `_PARTITIONTIME <=` ceiling would drop rows *inside* the window asked for.

### 3. A bare `_PARTITIONTIME >= …` drops the newest events

Streaming-buffer rows carry a **NULL** `_PARTITIONTIME`, and that is where the most
recent events live — so the filter that makes a query affordable is the same one that
discards today's data, unless written `(_PARTITIONTIME IS NULL OR _PARTITIONTIME >= …)`.
That is what the script always emits. `INFORMATION_SCHEMA.PARTITIONS` will not warn
you; it reports that partition as empty while it holds thousands of rows.

## Retention — an empty result is not "it never happened"

`raw_events` expires partitions after 45 days, and **the boundary is on ingestion**,
so the script reports the real one — the oldest live partition, read from
`INFORMATION_SCHEMA` — rather than computing `today − 45`.

Whenever the requested window falls partly or wholly before that boundary, the output
leads with a warning, and an empty result prints the boundary alongside the routing
note. **Never report "no such event" from a window outside retention** — report that
the window is outside retention.

## Usage

```bash
S=${CLAUDE_PLUGIN_ROOT}/skills/search-aura-events/scripts/search_events.py

# full lifecycle timeline for one user — the primary ask
python3 "$S" --uid <uid> --lifecycle

# everything for one user in the live window, newest first
python3 "$S" --uid <uid> --order desc --limit 50

# one event type, pruned to September (cheap)
python3 "$S" --uid <uid> --name UserDeleted --name RoleUpdated --since 2026-09-01

# experiment pivots
python3 "$S" --uid <uid> --experiment <experimentId> --name ExperimentAssigned
python3 "$S" --experiment <experimentId> --name ExperimentUpdated --order desc

# any other data JSON key
python3 "$S" --key serieId=<serieId> --since 2026-09-01

# the bare word `null` matches a JSON null — this is the de-role signal
python3 "$S" --key role=null --name RoleUpdated --since 2026-09-01

# discover the vocabulary instead of guessing a name (cheap — reads only `name`)
python3 "$S" --list-names
python3 "$S" --list-names --match subscription

# learn an event's data shape before filtering on one of its keys
python3 "$S" --sample SubscriptionCancelled

# price a scan and stop
python3 "$S" --uid <uid> --dry-run
```

Useful flags: `--fields a,b,c` (which `data` keys to show), `--order asc|desc`,
`--limit N`, `--json`, `--show-pii`, `--max-gb N`, `--why`.

`--limit` always keeps the **newest** N, then re-sorts for display, and says so when
the cap bites. An `ORDER BY createdAt ASC` + `LIMIT` would keep the *oldest* N and
silently drop the recent `UserDeleted` most questions here are about.

`userId` is hidden from the timeline only when you pivoted on `--uid` (it is the
constant you asked for). On a `--name` / `--key` / experiment search it is shown —
that is the answer to "who?". `at` and `origin` are always hidden as noise; bring any
of them back with `--fields`.

`--key <k>=null` matches a JSON null. BigQuery collapses JSON-null and key-absent
to the same SQL NULL, so pair it with `--name` to make the match exact.

`--json` emits `{meta, events}`. The `meta` block carries the scan estimate, the
retention floor and warning, and `row_limit_hit` — without it a caller cannot tell an
empty `events` list from a window whose partitions expired. Both output paths redact
through the same allow-list, so neither can emit what the other hides.

## Workflow

1. **Resolve the pivot.** UID → `--uid`. A question phrased in English ("when did
   they cancel?") → `--list-names --match <word>` first, then `--name`. An experiment
   → `--experiment`. Anything else → `--sample <EventName>` to learn the key shape,
   then `--key k=v`. `references/event-vocabulary.md` maps the families.

2. **Bound the window if you can.** `--since` is the only flag that reduces cost.
   An unbounded `data`-touching query reads the whole table — affordable but not
   free, and the default `--max-gb 6.0` is sized to allow exactly one of those and
   refuse a careless join. You never have to guess the figure: every run dry-runs
   first and prints the real one.

3. **Read the scan estimate.** Every run dry-runs first and prints
   `scan estimate: N.NNN GB` before executing. Surface that number to the user. A
   scan above `--max-gb` is refused, not billed.

4. **Read the retention banner**, if one appears, before interpreting an empty
   result.

5. **Check whether the cap bit.** `--limit` defaults to 200. If the run reports it
   hit the cap, older events exist beyond what you are looking at — raise it or
   narrow the filter before concluding anything about the earliest event.

6. **Render the timeline.** Output carries UTC and PT side by side. September–October
   is PDT (UTC−7); the conversion is done by BigQuery's
   `DATETIME(createdAt, "America/Los_Angeles")`, so it stays correct across the
   November DST change without an edit here.

7. **Interpret, don't just dump.** A raw timeline is rarely the answer. Call out the
   pairs that carry meaning — `UserDeleted` followed by `UserCreated` for the same
   UID is a delete-and-recreate, and `RoleUpdated role=null` is a de-role. Duplicate
   rows differing only by `$.origin` are two producers emitting the same logical
   change, not two events.

## Privacy

`UserCreated` and `UserUpdated` carry `email` and `givenName`; `CoachCreated` /
`CoachUpdated` carry a real person's name in `$.name`. These are **redacted by
default**; `--show-pii` opts in.

`$.name` is deliberately *not* redacted on other events — across the live corpus it
is an entity name (experiment, serie, course), and blanket redaction hid the
experiment identity on every `ExperimentUpdated` row.

Per `Compliance/Privacy`: a UID prefix and an email domain are the most that goes to
a shared surface. Do not paste a raw timeline containing `--show-pii` output into
Slack, Notion, or a ticket.

## Output contract

```
table: aura-main.firebase.raw_events
live partitions: 202608 (8,733,994 rows), 202609 (6,418,801 rows)  + streaming buffer
scan estimate: 1.808 GB

UTC                  PT                   event        data
-------------------  -------------------  -----------  ----
2026-09-23 04:59:32  2026-09-22 21:59:32  UserDeleted
2026-09-23 04:59:36  2026-09-22 21:59:36  RoleUpdated  role=null

6 event(s). Timestamps: createdAt is UTC; PT is America/Los_Angeles (PDT = UTC−7 …).
```

## References

- [`references/event-vocabulary.md`](references/event-vocabulary.md) — the 253-name
  vocabulary by family: account lifecycle, the four `Subscription*` families,
  experiments, which events carry `$.userId` and which do not, and the PII keys.
- [`references/query-recipes.md`](references/query-recipes.md) — the raw SQL for
  every pivot (from the Notion Big Query page, corrected against the live table),
  plus the partition-pruning clause to add to each. Reach for these only when you
  need a shape the script does not build — an aggregate, a join, a window function.

## Related skills

- `eng:query-bigquery` — ad-hoc SQL against `aura-main`; the canonical home of the
  bundled `bq.py` helper. Use it for a shape this skill does not build.
- `eng:search-gcp-by-user` — what the **services** logged for a UID (application
  logs, not domain events).
- `eng:call-aura-api` — the record's **current** state, which is exactly what the
  event stream is needed to go behind.

## Notes

- `scripts/bq.py` is a byte-identical copy of the canonical at
  `plugins/eng/skills/query-bigquery/scripts/bq.py` (repo principle 1 — skills are
  self-contained). See `/CLAUDE.md` → "Canonical source for `bq.py`" for the drift
  check.
- `$.at` duplicates the `createdAt` column to the millisecond, so `createdAt` is
  authoritative. `$.at` and `$.origin` are hidden from the timeline by default as
  noise; `--fields at,origin,…` brings them back.
- `LIMIT` does **not** reduce bytes scanned — BigQuery reads the columns first, then
  limits. Narrow the partitions instead.
