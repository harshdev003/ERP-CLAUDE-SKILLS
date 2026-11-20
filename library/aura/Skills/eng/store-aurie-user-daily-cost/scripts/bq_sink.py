#!/usr/bin/env python3
"""Write the three daily-cost stores into BigQuery, one day at a time.

BigQuery (`aura-main.aurie_cost.*`) is the **source of truth** for the cost
stores; the local CSVs are a rebuildable copy (see bq_pull.py). Each run does an
idempotent **partition replace** for one date (load into `table$YYYYMMDD` with
WRITE_TRUNCATE), so re-running a date is a no-op and a corrected day cleanly
overwrites its partition. `--backfill` re-syncs every date in the per-user CSV;
`--recreate` drops + recreates each table (schema migration) then backfills.
Every non-`--recreate` run is gated by `bq_preflight.probe`: a column the store
declares and BigQuery lacks refuses the run outright (it would 400 on every
partition load), and a store-vs-warehouse or cross-table max-date gap is printed.

Auth reuses the GCP auth ladder (GCP_KEY inline JSON, same as livekit_minutes.py
and eng:investigate-gcp-logs). **Hard requirement:** a missing GCP_KEY or absent
google-cloud-bigquery is a non-zero exit, not a degrade — BigQuery is the record,
so the daily flow must not run BQ-less. A per-store load failure is reported and
the other stores still run; the command exits non-zero so a mixed state is
visible and reconciled by an (idempotent) re-run.

Privacy: the per-user table holds full Firebase UIDs, so the dataset is
access-restricted. Any broad team surface must go through a UID-truncated
authorized view, never this raw table (see Compliance/Privacy).

Stdlib + google-cloud-bigquery only.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # for `from bq import ...`
import paths as store_paths  # noqa: E402  (BQ-primary CSV cache path resolution)

PROJECT = "aura-main"
DATASET = "aurie_cost"

# Each store: table name, its default CSV path + env var, the column->BQ-type
# schema (order matches the CSV header), and clustering fields. The schema is
# the contract — a CSV column absent here is dropped (and warned about) so
# schema drift is visible rather than silent.
STORES = {
    "per_user": {
        "table": "aurie_per_user_daily_cost",
        "env": "AURIE_COST_CSV",
        "default_csv": store_paths.cache_file("per_user"),
        "cluster": ["user_id", "subscription_state"],
        "schema": [
            ("date", "DATE"), ("user_id", "STRING"), ("llm_cost", "FLOAT"),
            ("voice_11labs_agent", "FLOAT"),
            # ENG-982: raw ElevenLabs TTS by CALLER. Intelligence Service (what
            # mobile calls) was previously mis-booked into voice_11labs_livekit.
            ("voice_11labs_intelligence", "FLOAT"),
            ("voice_11labs_livekit", "FLOAT"),
            ("voice_cartesia", "FLOAT"),
            # ENG-1306: Fish Audio TTS on the LiveKit path (PROD-6068 voice experiment).
            # Derived from the agent's own synthesised-character count × the s2-pro rate;
            # Fish has no usage API. Without the column its spend hid in `Other`.
            ("voice_fish", "FLOAT"),
            # Inworld TTS on the LiveKit path (aiVoiceProvider arm `a`). Characters are
            # actual from Inworld's usage API × the TTS-2 rate; the column did not exist
            # before, so its spend reached no column at all.
            ("voice_inworld", "FLOAT"), ("voice_deepgram", "FLOAT"),
            # PROD-6008: dollars drawn against LiveKit's included Inference credit
            # block. NOT a cost column -- it is spend against prepaid capacity the
            # subscription already books, so it stays out of voice_cost/total_cost.
            # Stored because nothing can derive it: voice_deepgram is $0 inside the
            # block by design, so the dollar column erases the quantity you would need.
            ("deepgram_inference_drawdown", "FLOAT"),
            # PROD-5998: LiveKit is a flat monthly subscription plus measured overage,
            # not a per-minute price. voice_livekit is OVERAGE only ($0 inside the
            # allotment); voice_livekit_platform is the subscription accrued per day and
            # allocated by minute share. livekit_minutes ends the practice of recovering
            # minutes by dividing a dollar column by the rate that produced it.
            ("voice_livekit", "FLOAT"), ("voice_livekit_platform", "FLOAT"),
            ("livekit_minutes", "FLOAT"), ("voice_cost", "FLOAT"),
            ("total_cost", "FLOAT"), ("subscription_state", "STRING"),
            ("engaged", "INTEGER"), ("active", "INTEGER"),
            ("activity_source", "STRING"),
        ],
    },
    "unit_costs": {
        "table": "aurie_daily_unit_costs",
        "env": "AURIE_UNIT_COST_CSV",
        "default_csv": store_paths.cache_file("unit_costs"),
        "cluster": [],
        "schema": [
            ("date", "DATE"), ("openai_total", "FLOAT"),
            ("msg_driven_cost", "FLOAT"), ("memory_cost", "FLOAT"),
            ("ambient_cost", "FLOAT"), ("visible_messages", "INTEGER"),
            ("cost_per_msg", "FLOAT"), ("cost_per_msg_paying", "FLOAT"),
            ("cost_per_msg_free", "FLOAT"), ("voice_cost", "FLOAT"),
            ("voice_minutes", "FLOAT"), ("cost_per_voice_min", "FLOAT"),
            ("cost_per_voice_min_11labs", "FLOAT"),
            ("cost_per_voice_min_livekit", "FLOAT"),
            ("livekit_voice_users", "INTEGER"),
            ("elevenlabs_agent_voice_users", "INTEGER"),
        ],
    },
    "eng_kpis": {
        "table": "aurie_daily_eng_kpis",
        "env": "AURIE_KPI_CSV",
        # Defaults to the cache dir like the other two stores. The KPI CSV is
        # fully rewritten by unit_costs.py each run (from the unit + per-user
        # stores), so the cache-dir copy is always fresh; sync_day still skips a
        # date with no rows, so a not-yet-written KPI day is a skip, not a stale
        # push.
        "default_csv": store_paths.cache_file("eng_kpis"),
        "cluster": [],
        "schema": [
            ("date", "DATE"), ("llm_cost_per_paying_msg", "FLOAT"),
            ("voice_cost_per_min", "FLOAT"), ("ambient_per_dau_month", "FLOAT"),
            ("ambient_per_active_user_month", "FLOAT"),
            ("ambient_per_engaged_user_month", "FLOAT"),
        ],
    },
}


def _ua_who() -> str:
    """Login name of whoever runs this, for the user agent: AINATIVE_UA_WHO, else the global git email's local part, git user.name, the OS account, or "unknown"."""
    who = re.sub(r"^\d+\+", "", os.environ.get("AINATIVE_UA_WHO", "").strip().partition("@")[0])
    if not who:
        if not hasattr(_ua_who, "found"):
            import getpass, subprocess
            found = ""
            for key in ("user.email", "user.name"):
                try:
                    val = subprocess.run(["git", "config", "--global", key], capture_output=True, encoding="utf-8", errors="replace", timeout=5).stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    val = ""
                found = re.sub(r"^\d+\+", "", val.partition("@")[0]) if key == "user.email" else val
                if found:
                    break
            if not found:
                try:
                    found = getpass.getuser()
                except Exception:
                    found = ""
            _ua_who.found = found
        who = _ua_who.found
    return re.sub(r"[^A-Za-z0-9._-]+", "-", who).strip("-.") or "unknown"


def user_agent() -> str:
    """`AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)` -- labels this skill's calls in RTDB and GCP logs.

    Same format as gcloud_sa.user_agent(). A label, not proof: anyone can send it.
    """
    skill = pathlib.Path(__file__).resolve().parents[1]
    root = skill.parents[1]
    try:
        plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError):
        plugin = root.name
    try:
        front = (skill / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        version = re.search(r'version:\s*"([^"]+)"', front).group(1)
    except (OSError, IndexError, AttributeError):
        version = "unknown"
    session = os.environ.get("AINATIVE_SESSION_NAME") or (os.environ.get("CLAUDE_CODE_SESSION_ID") or "")[:8]
    session = re.sub(r"[^A-Za-z0-9._-]+", "-", session).strip("-") or "none"
    return f"AuraClaude/{_ua_who()}/{plugin}:{skill.name}@{version} (session {session})"


class BQUnavailable(Exception):
    """Soft failure — degrade to skip, don't break the daily run."""


def _client(project: str):
    """Resolve the SA from GCP_KEY and return a BigQuery client (or raise BQUnavailable)."""
    try:
        from google.api_core.gapic_v1.client_info import ClientInfo
        from google.cloud import bigquery
        from google.oauth2 import service_account
    except ImportError as e:
        raise BQUnavailable(
            f"google-cloud-bigquery is not installed for {sys.executable} ({e}); "
            "run it with ~/.claude/python-env/bin/python3 (built by shared:setup-ainative; "
            "if that one lacks it: uv pip install --python ~/.claude/python-env/bin/python "
            "google-cloud-bigquery)"
        )
    raw = os.environ.get("GCP_KEY")
    if not raw:
        raise BQUnavailable("GCP_KEY not set")
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        try:
            info = json.load(open(os.path.expanduser(raw)))
        except Exception as e:
            raise BQUnavailable(f"GCP_KEY set but not JSON or a readable path ({e})")
    creds = service_account.Credentials.from_service_account_info(info)
    return bigquery.Client(project=project, credentials=creds, client_info=ClientInfo(user_agent=user_agent()))


def _ensure_table(client, spec: dict, project: str, dataset: str) -> str:
    """Create the partitioned+clustered table if missing; return its full id."""
    from google.cloud import bigquery
    from google.api_core.exceptions import NotFound

    table_id = f"{project}.{dataset}.{spec['table']}"
    try:
        client.get_table(table_id)
        return table_id
    except NotFound:
        pass
    schema = [bigquery.SchemaField(n, t) for n, t in spec["schema"]]
    table = bigquery.Table(table_id, schema=schema)
    table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY, field="date"
    )
    if spec["cluster"]:
        table.clustering_fields = spec["cluster"]
    client.create_table(table)
    print(f"  created {table_id} (partition=date, cluster={spec['cluster'] or 'none'})")
    return table_id


def _rows_for_date(csv_path: str, spec: dict, date: str):
    """Yield CSV rows for `date` as lists in schema-column order; warn on drift."""
    cols = [n for n, _ in spec["schema"]]
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in cols if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(
                f"{csv_path} is missing schema columns {missing} — "
                "update bq_sink.py STORES to match the CSV before syncing"
            )
        extra = [c for c in (reader.fieldnames or []) if c not in cols]
        if extra:
            print(f"  ! {os.path.basename(csv_path)} has columns not in the BQ schema "
                  f"(dropped): {extra}", file=sys.stderr)
        for row in reader:
            if row.get("date") == date:
                yield [row[c] for c in cols]


def _load_partition(client, table_id: str, spec: dict, rows: list, date: str) -> int:
    """Replace one date-partition with `rows` (data-only, schema order)."""
    from google.cloud import bigquery

    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    data = buf.getvalue().encode("utf-8")
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        schema=[bigquery.SchemaField(n, t) for n, t in spec["schema"]],
        skip_leading_rows=0,
    )
    target = f"{table_id}${date.replace('-', '')}"  # partition decorator
    job = client.load_table_from_file(io.BytesIO(data), target, job_config=job_config)
    job.result()
    return job.output_rows or 0


def sync_day(client, date: str, csv_paths: dict, project: str, dataset: str) -> tuple:
    """Partition-replace `date` across the stores present on disk.

    Returns `(synced, failures)` — lists of store keys that were written vs.
    errored. A store with no CSV path or no rows for the date is a legitimate
    skip (in neither list). Best-effort per store: one store's failure is
    reported and the remaining stores still run. BigQuery has no cross-table
    transaction for load jobs, so a partial failure can leave one table's `date`
    partition refreshed and another's stale — the caller exits non-zero on any
    failure (and on a no-op run where nothing synced), and re-running is
    idempotent (per-partition WRITE_TRUNCATE) so a re-run fully reconciles.
    """
    synced, failures = [], []
    for key, spec in STORES.items():
        path = csv_paths.get(key)
        if not path or not os.path.exists(path):
            print(f"  - {key}: no CSV at {path or '(unset)'} — skipped")
            continue
        try:
            rows = list(_rows_for_date(path, spec, date))
            if not rows:
                print(f"  - {key}: no rows for {date} in {os.path.basename(path)} — skipped")
                continue
            table_id = _ensure_table(client, spec, project, dataset)
            n = _load_partition(client, table_id, spec, rows, date)
            print(f"  + {key}: replaced partition {date} with {n} row(s) -> {table_id}")
            synced.append(key)
        except Exception as e:  # report this store, keep the others going
            print(f"  ! {key}: FAILED — {e}", file=sys.stderr)
            failures.append(key)
    return synced, failures


def _distinct_dates(csv_path: str) -> list:
    with open(csv_path, newline="") as f:
        return sorted({r["date"] for r in csv.DictReader(f) if r.get("date")})


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Mirror daily-cost CSVs into BigQuery (partition replace).")
    ap.add_argument("--date", help="YYYY-MM-DD to sync (default: latest date in the per-user CSV)")
    ap.add_argument("--backfill", action="store_true",
                    help="sync every distinct date found across the CSVs (one partition load per date)")
    ap.add_argument("--recreate", action="store_true",
                    help="drop + recreate each table (schema migration) then backfill from the local CSVs")
    ap.add_argument("--project", default=os.environ.get("AURIE_BQ_PROJECT", PROJECT))
    ap.add_argument("--dataset", default=os.environ.get("AURIE_BQ_DATASET", DATASET))
    ap.add_argument("--csv", help="per-user CSV path (else $AURIE_COST_CSV / default)")
    ap.add_argument("--unit-csv", help="unit-cost CSV path (else $AURIE_UNIT_COST_CSV / default)")
    ap.add_argument("--kpi-csv", help="KPI CSV path (else $AURIE_KPI_CSV / default)")
    args = ap.parse_args()

    # --flag > $AURIE_*_CSV > cache-dir default; expanduser so a legacy env var
    # with ~ resolves to the same path the writer scripts (via paths.resolve) use.
    csv_paths = {
        "per_user": os.path.expanduser(args.csv or os.environ.get(STORES["per_user"]["env"]) or STORES["per_user"]["default_csv"]),
        "unit_costs": os.path.expanduser(args.unit_csv or os.environ.get(STORES["unit_costs"]["env"]) or STORES["unit_costs"]["default_csv"]),
        "eng_kpis": os.path.expanduser(args.kpi_csv or os.environ.get(STORES["eng_kpis"]["env"]) or STORES["eng_kpis"]["default_csv"]),
    }

    # Bring the per-user CSV up to the current schema BEFORE anything reads it.
    # bq_pull emits blanks for columns BigQuery doesn't have yet, so a pull done
    # mid-migration lands an old-shaped file here; _migrate_old_csv both adds the
    # column and applies the ENG-982 Other-row caller correction. Without this the
    # documented pull-then-recreate backfill can't actually perform the migration.
    try:
        import store as _store_mod
        _store_mod._migrate_old_csv(csv_paths["per_user"])
    except Exception as e:  # noqa: BLE001 — never block the sync on the migration
        print(f"  ! per-user CSV migration skipped ({type(e).__name__}: {e})", file=sys.stderr)

    try:
        client = _client(args.project)
    except BQUnavailable as e:
        # BigQuery is the source of truth — a missing key/library is a hard fail,
        # not a degrade (the daily flow must not silently run BQ-less).
        print(f"BigQuery unavailable: {e}", file=sys.stderr)
        print("  BigQuery is the source of truth for the cost stores — fix GCP_KEY / "
              "install google-cloud-bigquery and re-run.", file=sys.stderr)
        return 1

    # Schema drift is fatal to every partition load and has no benign cause, so
    # refuse before writing rather than letting days accumulate unsyncable. Skip
    # the gate on --recreate: that IS the remedy. (PROD-6242 / the voice_fish
    # incident, where the 400 was printed daily and nothing upstream stopped.)
    if not args.recreate:
        try:
            import bq_preflight
            report = bq_preflight.probe(args.project, args.dataset, csv_paths)
        except Exception as e:  # noqa: BLE001 — a probe that cannot run must not block the sync
            print(f"  ! schema preflight skipped ({type(e).__name__}: {e})", file=sys.stderr)
        else:
            if report["refuse"]:
                print(bq_preflight.render(report), file=sys.stderr)
                print("\nRefusing to sync: the store declares columns BigQuery does not have, so "
                      "every partition load below would 400 and the day would only land locally.",
                      file=sys.stderr)
                return 1
            if report["behind"] or report["tables_divergent"]:
                # Not a refusal — a store legitimately skips a date it has no rows
                # for. Reported because a gap is otherwise invisible downstream.
                print(bq_preflight.render(report), file=sys.stderr)

    if args.recreate:
        from bq import recreate_table
        for key, spec in STORES.items():
            path = csv_paths.get(key)
            if not path or not os.path.exists(path):
                print(f"  - {key}: no local CSV — not recreating {spec['table']}")
                continue
            table_id = f"{args.project}.{args.dataset}.{spec['table']}"
            recreate_table(table_id, spec["schema"], partition_field="date",
                           cluster=spec["cluster"] or None, project=args.project)
            print(f"  ~ recreated {table_id} (empty, partition=date, cluster={spec['cluster'] or 'none'})")
        args.backfill = True  # repopulate the fresh tables from the local CSVs

    if args.backfill:
        per_user = csv_paths["per_user"]
        if not os.path.exists(per_user):
            print(f"backfill: per-user CSV not found at {per_user}", file=sys.stderr)
            return 1
        dates = _distinct_dates(per_user)
        if not dates:
            print(f"backfill: no dated rows in {per_user}", file=sys.stderr)
            return 1
        print(f"backfill: {len(dates)} date(s) {dates[0]} -> {dates[-1]} into {args.project}:{args.dataset}")
        failed_dates = []
        for d in dates:
            print(f"[{d}]")
            synced, failures = sync_day(client, d, csv_paths, args.project, args.dataset)
            if failures or not synced:
                failed_dates.append(d)
        if failed_dates:
            print(f"backfill finished WITH FAILURES on {len(failed_dates)} date(s): "
                  f"{', '.join(failed_dates)} — re-run (idempotent) to reconcile.", file=sys.stderr)
            return 1
        print("backfill complete")
        return 0

    date = args.date
    if not date:
        per_user = csv_paths["per_user"]
        if not os.path.exists(per_user):
            print("no --date and no per-user CSV to infer it from", file=sys.stderr)
            return 1
        dates = _distinct_dates(per_user)
        if not dates:
            print(f"no --date and no dated rows in {per_user}", file=sys.stderr)
            return 1
        date = dates[-1]
    print(f"syncing {date} into {args.project}:{args.dataset}")
    synced, failures = sync_day(client, date, csv_paths, args.project, args.dataset)
    if failures:
        print(f"BigQuery sync incomplete for {date}: {', '.join(failures)} failed — "
              "other tables' partitions may now be newer; re-run (idempotent) to reconcile.",
              file=sys.stderr)
        return 1
    if not synced:
        print(f"BigQuery sync wrote nothing for {date}: every store was skipped "
              "(no local CSV / no rows for the date). Check the cache dir "
              f"({store_paths.cache_dir()}), --csv / $AURIE_*_CSV, and the date.", file=sys.stderr)
        return 1
    print(f"synced {date}: {', '.join(synced)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
