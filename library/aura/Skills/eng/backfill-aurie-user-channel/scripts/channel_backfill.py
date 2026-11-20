#!/usr/bin/env python3
"""Backfill the acquisition-channel user dimension: aura-main.aurie_cost.aurie_user_channel.

Caches each user's **non-PII acquisition attributes** from the Aura API so cost-by-channel analysis
can join instead of re-walking the API. Incremental + append-only: each run resolves only the UIDs
missing from the table and appends them. Acquisition channel is immutable (set at install), so rows
are never updated or deleted — no MERGE, no dedup, no conflicts.

Resolution per UID, prod first then dev on a 404 (`resolved_via` records which answered):
Aura API `GET /users/<uid>` (200) -> `aiAppstackCampaign` (paid-campaign name, null =
organic/other), `sourcePlatform` (AIiOS / AurieWeb / UserWeb …), `sourceType`. Only these
non-PII marketing/platform attributes are stored — never email, name, or any personal field.

The dev leg matters because a dev-environment user 404s on prod permanently: without it those UIDs
are re-fetched every run, can never resolve, and inflate a bucket that is supposed to mean something
broke. Matches the ladder in the sibling env / aiCreatedAt walks.

Outcomes are terminal-vs-transient rather than one `error` bucket: a 404 on **both** hosts is a
stable orphan, recorded as `resolved_via=not_found_both` with every channel attribute null so it is
never re-fetched; only genuine transients retry. The orphan's attributes stay null rather than
guessed, and `resolved_via` is what distinguishes "organic user, no campaign" from "no record to
read" — identical in the attribute columns alone.

The channel dimension the Mixpanel Nessie dataset can't supply: its `attribution_*` columns exist
only on the `ai_subscription_activated` event (converters) and `appsflyer_user_attributed` (~0.7% of
cost users), so the per-user Aura API is the only broad-coverage source (hence this walk).

Env: AURA_API_TOKEN (Aura API), GCP_KEY (BigQuery). Needs `requests` + `google-cloud-bigquery`.
Run with the framework python that has `requests`.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import sys
import json
import pathlib
from collections import Counter
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


TABLE = f"{bq.PROJECT}.{bq.DATASET}.aurie_user_channel"
COST_TABLE = f"{bq.PROJECT}.{bq.DATASET}.aurie_per_user_daily_cost"
PROD_BASE = "https://services.aurahealth.io"
DEV_BASE = "https://services.dev.aurahealth.io"

# Non-PII acquisition attributes only.
SCHEMA = [
    ("user_id", "STRING"),
    ("ai_appstack_campaign", "STRING"),  # paid Appstack campaign name; null = organic/other
    ("source_platform", "STRING"),       # AIiOS | AurieWeb | UserWeb | ...
    ("source_type", "STRING"),           # Organic | ... (often null)
    # prod_users | dev_users | not_found_both. NULL on rows written before ENG-1129,
    # which are prod-resolved by construction: prod was then the only path that stored.
    ("resolved_via", "STRING"),
    ("fetched_at", "TIMESTAMP"),
]

# User ids are alphanumeric plus ':' '.' (provider-prefixed, e.g. google:123 /
# apple:0012.ab) and '-' (legacy UUID form) — all real, resolvable ids. Reject
# anything else before inlining into SQL or a URL path; none of ':._-' can
# terminate a quoted SQL literal or break out of a URL path segment.
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


def _get_user(base: str, uid: str):
    """GET /users/<uid> against `base`. Returns (status_code, body_dict_or_None);
    status None on a network/parse-level failure (treated as a transient non-200)."""
    try:
        r = _session().get(f"{base}/users/{uid}", timeout=15)
        if r.status_code != 200:
            return (r.status_code, None)
        d = r.json()
        # A 200 whose body isn't an object is not a user record — treat it as transient
        # rather than letting `.get` raise inside a worker thread.
        return (200, d) if isinstance(d, dict) else (None, None)
    except Exception:  # noqa: BLE001 — resilient per-user; retried next run
        return (None, None)


def _attrs(d: dict, via: str) -> dict:
    return {
        "ai_appstack_campaign": d.get("aiAppstackCampaign") or None,
        "source_platform": d.get("sourcePlatform") or None,
        "source_type": d.get("sourceType") or None,
        "resolved_via": via,
    }


def fetch_aura(uid: str):
    """Return (fields_dict_or_None, outcome) — prod first, dev on a 404.

      - 'ok':        resolved on prod or dev, or a terminal 404 on both. Store the row;
                     a 200 with every attribute null is a real organic user, and a
                     both-404 is a stable orphan tagged `not_found_both`.
      - 'transient': a non-200/non-404 on either leg, a network or parse failure, or a
                     malformed uid — leave unresolved and retry next run.

    The split matters: lumping the terminal 404 in with transients is what made the
    unresolved count un-clearable and therefore uninformative (ENG-1129).
    """
    if not _UID_RE.match(uid):
        return (None, "transient")

    status, d = _get_user(PROD_BASE, uid)
    if status == 200:
        return (_attrs(d, "prod_users"), "ok")
    if status != 404:
        return (None, "transient")

    status, d = _get_user(DEV_BASE, uid)
    if status == 200:
        return (_attrs(d, "dev_users"), "ok")
    if status == 404:
        # Terminal: no record on either host. Recorded so it is never re-fetched, with
        # attributes left null rather than guessed — `resolved_via` carries the reason.
        return ({"ai_appstack_campaign": None, "source_platform": None,
                 "source_type": None, "resolved_via": "not_found_both"}, "ok")
    return (None, "transient")


def append_rows(rows: list) -> int:
    """Append resolved rows via a WRITE_APPEND load job. Never updates/deletes.

    `ALLOW_FIELD_ADDITION` migrates a pre-ENG-1129 table in place, gaining `resolved_via`
    on first append without a recreate. Deliberately additive rather than backfilling the
    13,452 legacy rows: they are prod-resolved by construction, and rewriting them would
    break the append-only contract to encode something NULL already implies.
    """
    if not rows:
        return 0
    from google.cloud import bigquery
    c = bq.client()
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        schema=bq.schema_fields(SCHEMA),
        schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
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
    ap = argparse.ArgumentParser(description="Backfill the aurie_user_channel dimension.")
    ap.add_argument("--uids", help="Comma-separated UIDs (default: all in the cost table)")
    ap.add_argument("--uids-file", help="File of UIDs, one per line")
    ap.add_argument("--limit", type=int, help="Cap the number of missing UIDs fetched (test runs)")
    ap.add_argument("--workers", type=int, default=16, help="Concurrent Aura API calls (default 16)")
    ap.add_argument("--dry-run", action="store_true", help="Report the missing count + sample; no writes")
    ap.add_argument("--project", default=bq.PROJECT)
    ap.add_argument("--dataset", default=bq.DATASET)
    args = ap.parse_args()

    if not args.dry_run and not os.environ.get("AURA_API_TOKEN"):
        print("AURA_API_TOKEN is not set — required to read acquisition attributes from the Aura API "
              "(--dry-run works without it).", file=sys.stderr)
        return 1

    global TABLE, COST_TABLE
    TABLE = f"{args.project}.{args.dataset}.aurie_user_channel"
    COST_TABLE = f"{args.project}.{args.dataset}.aurie_per_user_daily_cost"

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
        return 0

    ensure_table()

    workers = max(1, args.workers)
    _session(workers)  # init the shared pooled Session sized to the worker count
    resolved, transient, done = {}, 0, 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_aura, u): u for u in missing}
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
    by_via = Counter(f["resolved_via"] for f in resolved.values())
    print(f"  recorded: {len(resolved)}  transient (retry next run): {transient}")
    for via in ("prod_users", "dev_users", "not_found_both"):
        if by_via.get(via):
            print(f"    {via}: {by_via[via]}")

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

    with_campaign = sum(1 for f in resolved.values() if f["ai_appstack_campaign"])
    orphans = sum(1 for f in resolved.values() if f["resolved_via"] == "not_found_both")
    print("\n=== coverage ===")
    print(f"  rows appended:                 {written}")
    print(f"  of those, with a paid campaign:{with_campaign}  (rest organic/other)")
    if orphans:
        print(f"  of those, no record on either host: {orphans}  "
              "(recorded as not_found_both, attributes null — terminal, never re-fetched)")
    if skipped:
        print(f"  skipped (added by a concurrent run): {len(skipped)}")
    print(f"  transient, retried next run:   {transient}"
          f"{'  <- the only bucket worth investigating' if transient else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
