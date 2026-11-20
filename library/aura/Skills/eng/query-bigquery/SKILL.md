---
name: query-bigquery
description: >
  Run ad-hoc BigQuery SQL against the Aura Data-team warehouse (`aura-main`) — the canonical
  "use BigQuery" access layer for reading the Aurie cost tables and any other `aura-main`
  dataset. Read-only by default: runs a SELECT and returns a text table or JSON, lists tables
  in a dataset, describes a table's schema, or dry-runs a query to price the scan before
  running it. The Aurie cost stores live in `aura-main.aurie_cost`, maintained by
  `eng:store-aurie-user-daily-cost`. Trigger on "query bigquery", "run a bigquery query", "use
  bigquery", "query the cost warehouse", "read the aurie_cost tables", "sql against
  aura-main", "how much did we spend on {day} from bigquery", "describe the {table} table",
  "list bigquery tables", or a pasted SQL statement to run. Do NOT use to compute fresh
  per-user cost (`eng:store-aurie-user-daily-cost`), chart the trend
  (`eng:chart-aurie-daily-cost`), or for GCP logs (`eng:investigate-gcp-logs`).

metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/BigQuery                 # aura-main.aurie_cost store access (GCP_KEY)
---

# Query BigQuery

## Goal

One clean entry point for reading the Aura BigQuery warehouse (`aura-main`) from
Claude Code — primarily the Aurie cost tables in the `aura-main.aurie_cost`
dataset, but any `aura-main` dataset the service account can see. Read-only by
default; destructive DML/DDL is refused unless explicitly allowed.

This is also the **canonical home** for `bq.py` — the shared BigQuery helper
(auth + `query` + partition read/write + `recreate_table`) that
`eng:store-aurie-user-daily-cost` and `eng:chart-aurie-daily-cost` import. See
"Shared helper" below.

## API-first rationale

No BigQuery MCP exists, so this skill leads with the API (per `/CLAUDE.md` →
"API-first by necessity"). Auth is the `GCP_KEY` service account
(`claude@firebase-auratech16`), granted `bigquery.jobUser` +
`bigquery.dataEditor` on `aura-main`. A missing `GCP_KEY` or absent
`google-cloud-bigquery` is a hard error — there is no fallback.

## Step 0 — Prerequisites

```bash
test -n "$GCP_KEY" && echo "gcp: OK" || echo "gcp: MISSING"
python3 -c "import google.cloud.bigquery" 2>/dev/null && echo "lib: OK" \
  || echo "lib: MISSING — python3 -m pip install --user google-cloud-bigquery"
```

Both are required. `GCP_KEY` is the inline service-account JSON (same key the
GCP-logs / LiveKit skills use); it must carry `bigquery.jobUser` +
`bigquery.dataEditor` on `aura-main`.

## Usage

```bash
Q=${CLAUDE_PLUGIN_ROOT}/skills/query-bigquery/scripts/query.py

# run a SELECT (text table)
python3 "$Q" --sql "SELECT date, total_cost FROM aurie_cost.aurie_daily_unit_costs ORDER BY date DESC LIMIT 7"

# JSON output (for piping into other tools)
python3 "$Q" --sql "SELECT * FROM aurie_cost.aurie_daily_eng_kpis WHERE date = '2026-07-12'" --json

# run a saved query file
python3 "$Q" --file /tmp/cost_report.sql

# list tables in the cost dataset
python3 "$Q" --list                       # defaults to aurie_cost
python3 "$Q" --list mixpanel              # any aura-main dataset

# describe a table's schema / partitioning / clustering
python3 "$Q" --describe aurie_cost.aurie_per_user_daily_cost

# price a scan WITHOUT running it
python3 "$Q" --sql "SELECT ... big query ..." --dry-run
```

**Conventions & guards:**

- **Project defaults to `aura-main`.** A bare `dataset.table` (e.g.
  `aurie_cost.aurie_daily_unit_costs`) resolves against it; fully-qualified
  `project.dataset.table` also works.
- **Read-only by default.** Any statement starting with
  `INSERT/UPDATE/DELETE/MERGE/CREATE/DROP/TRUNCATE/ALTER/GRANT` is refused unless
  you pass `--allow-write`. (Per-day corrections to the cost tables should go
  through `eng:store-aurie-user-daily-cost`'s idempotent partition replace, not
  hand-written DML.)
- **Scan cap.** Every query is capped at `--max-gb` (default 10) of bytes
  scanned, so a runaway query fails fast instead of billing the warehouse. Use
  `--dry-run` first when unsure.

## The Aurie cost tables

`aura-main.aurie_cost` (day-partitioned on `date`):

| Table | Grain | Notes |
|---|---|---|
| `aurie_per_user_daily_cost` | one row per Firebase UID per day (+ an `Other` reconciling row) | **holds full UIDs** — restricted; clustered by `user_id, subscription_state` |
| `aurie_daily_unit_costs` | one row per day | business-model unit costs ($/msg, $/voice-min, memory/ambient split) |
| `aurie_daily_eng_kpis` | one row per day | the engineering cost KPIs |

**Privacy.** `aurie_per_user_daily_cost` holds full Firebase UIDs. Do not export
raw UIDs to any shared surface (Slack/Notion/sheet) — truncate to a prefix, or
query through a UID-truncated authorized view. See `Compliance/Privacy`.

## Shared helper (`bq.py`)

`scripts/bq.py` is the canonical BigQuery helper for the cost-skill family. It
exposes: `client()` (GCP_KEY auth), `query(sql, dry_run, max_bytes, params)`,
`partition_exists(table, date)`, `table_exists`, `list_tables`,
`describe_table`, `load_partition(table, csv_bytes, schema, date)` (idempotent
per-day WRITE_TRUNCATE replace), and `recreate_table(table, schema,
partition_field, cluster)` (schema-migration drop+recreate). It raises
`BQUnavailable` (missing key/library) and `BQError` (request failed).

Import it from a consuming skill:

```python
import os, sys
sys.path.insert(0, f"{os.environ['CLAUDE_PLUGIN_ROOT']}/skills/<this-skill>/scripts")
from bq import query, load_partition, partition_exists, BQError
```

Per the repo's self-contained rule, each consumer ships its own byte-identical
copy of `bq.py`; this skill's copy is **canonical**. See `/CLAUDE.md` →
"Canonical source for `bq.py`" for the copy list + the drift check.

## Notes

- **Cost.** Read queries are billed by bytes scanned; the tables are small
  (tens of MB) and day-partitioned, so filtering on `date` keeps scans tiny.
- **Idempotency of writes.** `load_partition` replaces a whole day-partition, so
  re-loading a date is a no-op — this is how the store keeps BigQuery in sync
  without duplicates.
- **Schema migration.** For a structural change (drop/retype a column, change
  partitioning), use `recreate_table` then reload from the local CSV copy —
  BigQuery load jobs replace partitions but don't alter column types in place.
