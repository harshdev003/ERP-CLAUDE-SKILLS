#!/usr/bin/env python3
"""Backfill the per-user environment dimension: aura-main.aurie_cost.aurie_user_env.

Tags each user_id in the cost table as `prod`, `dev`, or `unknown` — the foundation for
env-filtering the prod cost aggregates (PROD-3589). Incremental + append-only: each run
resolves only the UIDs missing from the table and appends them. Environment is treated as
immutable once resolved (a uid doesn't move Aura backends), so rows are never updated or
deleted — no MERGE, no dedup, no conflicts.

Resolution per missing uid (prod first, dev fallback), using a pooled session:
  1. GET https://services.aurahealth.io/users/<uid>
     - 200      -> env=prod, resolved_via=prod_users. Record.
     - 404      -> go to step 2.
     - other/transient -> leave unresolved (retry next run).
  2. GET https://services.dev.aurahealth.io/users/<uid>  (same Bearer token)
     - 200      -> env=dev, resolved_via=dev_users. Record.
     - 404      -> env=unknown, resolved_via=not_found_both. Record (terminal — a uid that
                   404s on both is a stable orphan, e.g. a QA/test id).
     - other/transient -> leave unresolved (retry next run).

After every run (and standalone via --view-only, which skips the API walk entirely), this
also idempotently (re)creates `aura-main.aurie_cost.aurie_per_user_daily_cost_prod` — a
prod-only view over `aurie_per_user_daily_cost` joined to this table on `env='prod'`. The
`chart-aurie-*` family reads that view instead of the base table so prod cost/margin
aggregates exclude dev + unknown/QA traffic (PROD-3589 Part B).

Env: AURA_API_TOKEN (Aura API, both prod + dev), GCP_KEY (BigQuery). Needs `requests` +
`google-cloud-bigquery`. Run with the framework python that has `requests`.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import sys
import json
import pathlib
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bq  # noqa: E402  (sibling copy — self-contained skill)
from bq import BQError, BQUnavailable  # noqa: E402


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
    """`AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)` -- labels this skill's calls to RTDB, GCP and the Aura API.

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


TABLE = f"{bq.PROJECT}.{bq.DATASET}.aurie_user_env"
COST_TABLE = f"{bq.PROJECT}.{bq.DATASET}.aurie_per_user_daily_cost"
VIEW = f"{bq.PROJECT}.{bq.DATASET}.aurie_per_user_daily_cost_prod"
PROD_BASE = "https://services.aurahealth.io"
DEV_BASE = "https://services.dev.aurahealth.io"

SCHEMA = [
    ("user_id", "STRING"),
    ("env", "STRING"),           # prod | dev | unknown
    ("resolved_via", "STRING"),  # prod_users | dev_users | not_found_both
    ("fetched_at", "TIMESTAMP"),
]

# User ids are alphanumeric plus ':' '.' (provider-prefixed, e.g. google:123 /
# apple:0012.ab) and '-' (legacy UUID form) — all real, resolvable ids. Reject
# anything else before inlining into a URL path; none of ':._-' can break out
# of a URL path segment.
_UID_RE = re.compile(r"^[A-Za-z0-9:._-]+$")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


_SESSION = None


def _session(pool: int = 24):
    """One shared, connection-pooled Session for the whole walk. A new `requests.get` per
    call opens a fresh TLS connection each time; under many worker threads with urllib3's
    default pool_maxsize (10) connections churn badly (CPU thrash + slowdowns). A shared
    Session with pool_maxsize == workers keeps connections warm (keep-alive) and bounded."""
    global _SESSION
    if _SESSION is not None:
        return _SESSION
    try:
        import requests
        from requests.adapters import HTTPAdapter
    except ImportError as e:  # noqa: BLE001
        raise SystemExit(
            f"`requests` is required ({e}); run with the framework python that has it "
            "(see CLAUDE.md -> 'Default python3 here lacks requests')."
        )
    s = requests.Session()
    s.headers["User-Agent"] = user_agent()
    token = os.environ.get("AURA_API_TOKEN", "")
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    adapter = HTTPAdapter(pool_connections=pool, pool_maxsize=pool, max_retries=0)
    s.mount("https://", adapter)
    _SESSION = s
    return _SESSION


def ensure_table() -> None:
    """Create the table if it doesn't exist (clustered by user_id, unpartitioned)."""
    from google.cloud import bigquery
    if bq.table_exists(TABLE):
        return
    c = bq.client()
    t = bigquery.Table(TABLE, schema=bq.schema_fields(SCHEMA))
    t.clustering_fields = ["user_id"]
    c.create_table(t, exists_ok=True)


def requested_uids(args) -> list:
    if args.uids:
        return [u.strip() for u in args.uids.split(",") if u.strip()]
    if args.uids_file:
        with open(args.uids_file) as f:
            return [line.strip() for line in f if line.strip()]
    rows = bq.query(f"SELECT DISTINCT user_id FROM `{COST_TABLE}` WHERE user_id != 'Other'")
    return [r["user_id"] for r in rows]


def existing_uids() -> set:
    if not bq.table_exists(TABLE):
        return set()
    return {r["user_id"] for r in bq.query(f"SELECT DISTINCT user_id FROM `{TABLE}`")}


def _get(base: str, uid: str):
    """GET /users/<uid> against `base`. Returns an HTTP status code, or None on a
    network/parse-level failure (treated the same as a transient non-200)."""
    try:
        r = _session().get(f"{base}/users/{uid}", timeout=15)
        return r.status_code
    except Exception:  # noqa: BLE001 — resilient per-user; retried next run
        return None


def resolve_env(uid: str):
    """Return (fields_dict_or_None, outcome).

      - 'ok':        resolved (prod, dev, or unknown-terminal) — store the row.
      - 'transient':  non-terminal failure (non-200/404 on either leg, network error,
                      or a malformed uid) — leave unresolved, retry next run.
    """
    if not _UID_RE.match(uid):
        return (None, "transient")

    status = _get(PROD_BASE, uid)
    if status == 200:
        return ({"env": "prod", "resolved_via": "prod_users"}, "ok")
    if status != 404:
        return (None, "transient")  # non-200/non-404 on prod, or network error

    status = _get(DEV_BASE, uid)
    if status == 200:
        return ({"env": "dev", "resolved_via": "dev_users"}, "ok")
    if status == 404:
        return ({"env": "unknown", "resolved_via": "not_found_both"}, "ok")
    return (None, "transient")  # non-200/non-404 on dev, or network error


# Days whose per-user LLM attribution collapsed before the ENG-1120 guard existed,
# and which cannot be repaired: stored chat completions for June 2026 are long past
# OpenAI's ~30-day retention and the LLM ledger has no pre-epoch rows, so re-storing
# them would resolve nothing. They are excluded here rather than left to each reader,
# because the failure is invisible in the data itself — the dollars still reconcile
# to the authoritative bill (the unattributed remainder sits on the `Other` row), so
# a reader that forgets to special-case them silently averages over an empty or
# wildly over-concentrated distribution.
#
#   2026-06-23  317 users instead of ~1,630 — a PARTIAL loss, so the survivors
#               absorbed the whole day's LLM bill at ~5x their true cost
#   2026-06-24  0 users; $144.88 of LLM entirely on the `Other` row
#   2026-06-25  0 users; $151.00 of LLM entirely on the `Other` row
#
# Scope note: this is the per-user view only. The base table keeps every row, so
# `eng:chart-aurie-daily-cost` and `eng:report-livekit-daily-cost` — which read the
# base table precisely so their headline totals reconcile to the vendor bill — still
# see these days in full. Voice attribution was intact throughout; only the LLM
# per-user join collapsed.
UNUSABLE_ATTRIBUTION_DATES = ("2026-06-23", "2026-06-24", "2026-06-25")


def _unusable_dates_sql() -> str:
    return ", ".join(f"DATE '{d}'" for d in UNUSABLE_ATTRIBUTION_DATES)


def refresh_prod_view() -> None:
    """Idempotently (re)create the shared prod-only per-user daily cost view — the same
    columns as `aurie_per_user_daily_cost`, restricted to rows whose user_id resolves to
    `env='prod'` in this table. Excludes the `Other` reconciling row (it has no user_id
    match in aurie_user_env) along with every dev/unknown user. Consumed by the whole
    `chart-aurie-*` family so prod-facing cost aggregates exclude dev/QA traffic
    (PROD-3589 Part B) without ever touching the base table.

    `aurie_user_env` is append-only with no unique key, so a user_id can carry >1 row; the
    join dedups to the latest row per user (`ROW_NUMBER() … ORDER BY fetched_at DESC`) before
    filtering to `env='prod'`, otherwise a duplicated dimension row would fan out the cost
    rows and inflate every downstream aggregate.

    The view also drops `UNUSABLE_ATTRIBUTION_DATES` (ENG-1120) — see that constant."""
    sql = f"""
    CREATE OR REPLACE VIEW `{VIEW}` AS
    SELECT c.* FROM `{COST_TABLE}` c
    JOIN (
      SELECT user_id FROM (
        SELECT user_id, env,
               ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY fetched_at DESC) AS rn
        FROM `{TABLE}`
      ) WHERE rn = 1 AND env = 'prod'
    ) e USING (user_id)
    WHERE c.date NOT IN ({_unusable_dates_sql()})
    """
    bq.query(sql)


def append_rows(rows: list) -> int:
    """Append resolved rows via a WRITE_APPEND load job. Never updates/deletes."""
    if not rows:
        return 0
    from google.cloud import bigquery
    c = bq.client()
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        schema=bq.schema_fields(SCHEMA),
    )
    try:
        job = c.load_table_from_json(rows, TABLE, job_config=job_config)
        job.result()
    except Exception as e:  # noqa: BLE001
        raise BQError(str(e))
    return job.output_rows or 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Backfill the aurie_user_env dimension.")
    ap.add_argument("--uids", help="Comma-separated UIDs (default: all in the cost table)")
    ap.add_argument("--uids-file", help="File of UIDs, one per line")
    ap.add_argument("--limit", type=int, help="Cap the number of missing UIDs fetched (test runs)")
    ap.add_argument("--workers", type=int, default=16, help="Concurrent Aura API calls (default 16)")
    ap.add_argument("--dry-run", action="store_true", help="Report the missing count + sample; no writes")
    ap.add_argument("--view-only", action="store_true",
                     help="Skip the Aura API walk entirely; just (re)create the "
                          "aurie_per_user_daily_cost_prod view and exit")
    ap.add_argument("--project", default=bq.PROJECT)
    ap.add_argument("--dataset", default=bq.DATASET)
    args = ap.parse_args()

    global TABLE, COST_TABLE, VIEW
    TABLE = f"{args.project}.{args.dataset}.aurie_user_env"
    COST_TABLE = f"{args.project}.{args.dataset}.aurie_per_user_daily_cost"
    VIEW = f"{args.project}.{args.dataset}.aurie_per_user_daily_cost_prod"

    if args.view_only:
        try:
            refresh_prod_view()
        except BQUnavailable as e:
            print(f"BigQuery unavailable: {e}", file=sys.stderr)
            return 1
        except BQError as e:
            print(f"BigQuery error: {e}", file=sys.stderr)
            return 1
        print(f"view refreshed: {VIEW}")
        return 0

    if not args.dry_run and not os.environ.get("AURA_API_TOKEN"):
        print("AURA_API_TOKEN is not set — required to resolve env via the Aura API "
              "(--dry-run works without it).", file=sys.stderr)
        return 1

    try:
        req = requested_uids(args)
        have = existing_uids()
    except BQUnavailable as e:
        print(f"BigQuery unavailable: {e}", file=sys.stderr)
        return 1
    except BQError as e:
        print(f"BigQuery error: {e}", file=sys.stderr)
        return 1

    req_unique = list(dict.fromkeys(req))
    missing = [u for u in req_unique if u not in have]
    already = len(req_unique) - len(missing)
    print(f"requested: {len(req_unique)}  already in table: {already}  missing: {len(missing)}")

    if args.limit:
        missing = missing[: args.limit]
        print(f"  (--limit) fetching {len(missing)} this run")

    if args.dry_run:
        print("dry-run — sample missing UID prefixes:",
              ", ".join(u[:6] + "…" for u in missing[:10]) or "(none)")
        return 0

    if not missing:
        print("nothing to backfill — coverage complete.")
        try:
            refresh_prod_view()
            print(f"view refreshed: {VIEW}")
        except (BQUnavailable, BQError) as e:
            print(f"view refresh failed (non-fatal): {e}", file=sys.stderr)
        return 0

    ensure_table()

    workers = max(1, args.workers)
    _session(workers)  # init the shared pooled Session sized to the worker count
    resolved, transient, done = {}, 0, 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(resolve_env, u): u for u in missing}
        for fut in as_completed(futs):
            u = futs[fut]
            fields, outcome = fut.result()
            if outcome == "ok":
                resolved[u] = fields
            else:
                transient += 1
            done += 1
            if done % 1000 == 0:
                print(f"  … {done}/{len(missing)} fetched", flush=True)
    print(f"  resolved: {len(resolved)}  transient-unresolved (retry): {transient}")

    # Narrow the overlapping-run race (append-only, no unique key): re-check right before writing.
    try:
        now_have = existing_uids()
    except BQError:
        now_have = have
    skipped = [u for u in resolved if u in now_have]
    for u in skipped:
        resolved.pop(u, None)

    now = _now_iso()
    out_rows = [{"user_id": u, **f, "fetched_at": now} for u, f in resolved.items()]
    written = append_rows(out_rows)

    n_prod = sum(1 for f in resolved.values() if f["env"] == "prod")
    n_dev = sum(1 for f in resolved.values() if f["env"] == "dev")
    n_unknown = sum(1 for f in resolved.values() if f["env"] == "unknown")
    print("\n=== coverage ===")
    print(f"  rows appended: {written}")
    print(f"    prod:    {n_prod}")
    print(f"    dev:     {n_dev}")
    print(f"    unknown: {n_unknown}  (404 on both prod and dev — stable orphan)")
    if skipped:
        print(f"  skipped (added by a concurrent run): {len(skipped)}")
    if transient:
        print(f"  transient-unresolved (retry next run): {transient}")

    try:
        refresh_prod_view()
        print(f"view refreshed: {VIEW}")
    except (BQUnavailable, BQError) as e:
        print(f"view refresh failed (non-fatal): {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
