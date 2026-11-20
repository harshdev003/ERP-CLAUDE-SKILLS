#!/usr/bin/env python3
"""Backfill the aiCreatedAt user dimension: aura-main.aurie_cost.aurie_user_ai_created.

Ensures we have an AI/Aurie join date (`ai_created_at`) for every user that a cohort
query will touch, so the cohort SQL never joins against incomplete data. Incremental
and append-only: each run resolves only the UIDs missing from the table and appends
the resolved ones. `ai_created_at` is immutable (a join date never changes), so rows
are never updated or deleted — no MERGE, no dedup, no conflicts.

Resolution per UID:
  1. Aura API `GET /users/<uid>` on **prod** -> `aiCreatedAt`  [source=postgres].
  2. Prod 404 -> the same call on **dev**    -> `aiCreatedAt`  [source=postgres_dev].
     Dev-environment users 404 on prod and carry a real aiCreatedAt on dev, and they emit
     no events into the prod Mixpanel project — so this leg is the only path that reaches
     them; the step-3 proxy cannot.
  3. aiCreatedAt null or the backfill sentinel 2026-02-11T12:00:00Z (stamped on users
     who joined AI before the field existed) -> earliest event across every AI event
     table in the Mixpanel Nessie dataset  [source=first_ai_login].
  4. Still unknown -> not inserted (retried next run) and classified by reason, so a
     stable "no join date exists" never reads the same as a failed lookup.

No PII is ever stored — only user_id + the two timestamps + source + fetched_at.

Env: AURA_API_TOKEN (Aura API), GCP_KEY (BigQuery + Mixpanel Nessie). Needs `requests`
and `google-cloud-bigquery`. Run with the framework python that has `requests`.
"""
from __future__ import annotations

import argparse
import collections
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


TABLE = f"{bq.PROJECT}.{bq.DATASET}.aurie_user_ai_created"
COST_TABLE = f"{bq.PROJECT}.{bq.DATASET}.aurie_per_user_daily_cost"
MIX_DS = "mixpanel-prod-1.mixpanel_nessie_day_partitioned_963821"
PROD_BASE = "https://services.aurahealth.io"
DEV_BASE = "https://services.dev.aurahealth.io"

# aiCreatedAt backfill sentinel — stamped on users who joined AI before the field was
# instrumented; not a real join date. aiInstalledAt shares it. Match on the minute prefix.
SENTINEL_PREFIX = "2026-02-11T12:00:00"

# The 2026-02-11 sentinel is not THE sentinel, it is one of a family. Postgres carries
# several exact-noon batch stamps (14 distinct values / 631 users at ENG discovery time),
# and hardcoding one value meant the skill rejected that one while storing the other
# thirteen as `source=postgres` — i.e. as a genuine join date, silently putting those
# users in the wrong cohort. So the stamp is detected by SHAPE, not by a value list:
# exactly 12:00:00 UTC *and* shared by several users. The time alone is not enough — a
# real join date can legitimately land on noon, and a lone noon date must not be flagged.
BATCH_STAMP_TIME = "12:00:00"
BATCH_STAMP_MIN_USERS = 4


def _norm_ts(v) -> str:
    """Canonical 'YYYY-MM-DDTHH:MM:SS' (UTC, no fraction) for comparing timestamps that
    arrive as Postgres ISO strings ('...T12:00:00.000Z') and as BigQuery datetimes."""
    if v is None:
        return ""
    s = v.isoformat() if hasattr(v, "isoformat") else str(v)
    s = s.strip().replace(" ", "T", 1)
    s = re.split(r"[+Z]", s)[0]
    return s.split(".")[0]


def _onboarding_ai_ts(rec: dict):
    """`onboarding.shown.consumerAIOnboarding` — the AI onboarding event on the user
    record the skill already fetches. Measured against 592 users holding a real
    (non-stamped) aiCreatedAt: 97.3% land on the same calendar day, median difference 0s,
    p90 0s — a tighter match than the Mixpanel proxy, at zero extra API calls."""
    ob = rec.get("onboarding")
    if not isinstance(ob, dict):
        return None
    shown = ob.get("shown")
    if not isinstance(shown, dict):
        return None
    ts = shown.get("consumerAIOnboarding")
    return ts if ts else None


def is_batch_stamp(aic, stamps: set) -> bool:
    """True when this aiCreatedAt is a batch stamp rather than a real join date."""
    n = _norm_ts(aic)
    if not n:
        return False
    if n.startswith(SENTINEL_PREFIX):
        return True
    return n in stamps


def stamp_by_onboarding(aic, ob) -> bool:
    """Cold-start guard: an exact-noon value whose onboarding timestamp lands on a
    DIFFERENT calendar day is a batch stamp, even though no cluster has been seen yet.

    `known_batch_stamps` can only see stamps already IN the dimension, and the run only
    fetches UIDs that are missing from it — so the first wave of a brand-new upstream
    batch would be stored as `source=postgres` and, being present, never retried. That
    is PROD-6109 repeating for every new batch.

    This closes it per user, with no cross-user information: a genuine noon join date
    agrees with `consumerAIOnboarding` on the calendar day (97.3% of 592 measured users,
    median 0s), while a batch stamp does not. Requires an onboarding timestamp, so it is
    paired with the in-run cluster net in `main` for users that have none.
    """
    n = _norm_ts(aic)
    if not n.endswith("T" + BATCH_STAMP_TIME):
        return False
    o = _norm_ts(ob)
    if not o:
        return False
    return o[:10] != n[:10]


def fresh_batch_stamps(resolved: dict) -> set:
    """Exact-noon values shared by >= BATCH_STAMP_MIN_USERS users *within one run*.

    The in-run half of the cold-start guard. `known_batch_stamps` reads the dimension,
    which by definition cannot contain a batch it has never stored; a new upstream wave
    arrives as many users at once, so it forms a cluster here first. Paired with
    `stamp_by_onboarding`, which catches the same thing per user but needs an onboarding
    timestamp.
    """
    counts = collections.Counter(
        _norm_ts(v[0]) for v in resolved.values()
        if v[2] in ("postgres", "postgres_dev")
        and _norm_ts(v[0]).endswith("T" + BATCH_STAMP_TIME))
    return {ts for ts, n in counts.items() if n >= BATCH_STAMP_MIN_USERS}


def reroute_fresh_stamps(resolved: dict, outcomes: dict, need_fallback: list) -> tuple:
    """Move users holding a freshly-detected batch stamp off the Postgres rung.

    Mutates `resolved` / `outcomes` / `need_fallback` in place; returns
    ``(fresh_stamps, moved_uids)``.

    **The re-routed outcome keeps prod/dev provenance.** A dev user must become
    `fallback_dev`, not `fallback`: the Mixpanel proxy reads the PROD project and so can
    never answer for them, and the residue reason is what says so. Collapsing both to
    `fallback` reports a dev user as `no_ai_activity` ("prod user, no AI event") instead
    of `dev_no_ai_activity` — a reason that is wrong about which environment was even
    searched, on precisely the users this net exists to catch.
    """
    fresh = fresh_batch_stamps(resolved)
    if not fresh:
        return (fresh, [])
    moved = [(u, v[2]) for u, v in resolved.items()
             if v[2] in ("postgres", "postgres_dev") and _norm_ts(v[0]) in fresh]
    for uid, src in moved:
        resolved.pop(uid, None)
        outcomes[uid] = "fallback_dev" if src == "postgres_dev" else "fallback"
        need_fallback.append(uid)
    return (fresh, [u for u, _ in moved])


def known_batch_stamps() -> set:
    """Exact-noon timestamps shared by >= BATCH_STAMP_MIN_USERS users in the dimension.

    Derived from the data each run rather than listed, because a list goes stale the next
    time the upstream batch runs — which is exactly how the 2026-02-11-only guard failed.
    Always includes the 2026-02-11 sentinel, which by construction is never stored (the
    skill refuses it) and so can never be rediscovered from the table.
    """
    stamps = {SENTINEL_PREFIX}
    if not bq.table_exists(TABLE):
        return stamps
    try:
        rows = bq.query(
            f"SELECT ai_created_at, COUNT(DISTINCT user_id) n FROM `{TABLE}` "
            f"WHERE EXTRACT(TIME FROM ai_created_at) = TIME '{BATCH_STAMP_TIME}' "
            f"GROUP BY 1 HAVING n >= {BATCH_STAMP_MIN_USERS}"
        )
    except BQError as e:  # noqa: BLE001 — degrade to the known sentinel only
        print(f"  (could not read batch stamps, using the known sentinel only: {e})",
              file=sys.stderr)
        return stamps
    for r in rows:
        stamps.add(_norm_ts(r["ai_created_at"]))
    return stamps

SCHEMA = [
    ("user_id", "STRING"),
    ("ai_created_at", "TIMESTAMP"),
    ("created_at", "TIMESTAMP"),
    ("source", "STRING"),        # postgres | postgres_dev | ai_onboarding | first_ai_login
    ("fetched_at", "TIMESTAMP"),
]

# Why unresolved, per UID. Only 'transient' is worth re-reading as a failure; the rest
# are stable properties of the user that a re-run cannot change on its own.
UNRESOLVED_REASONS = {
    "no_ai_activity": "prod user, no aiCreatedAt and no AI event in Mixpanel",
    "dev_no_ai_activity": "dev user, no aiCreatedAt on dev (prod Mixpanel can't proxy)",
    "no_user_record": "404 on both prod and dev — stable orphan (QA/test/deleted id)",
    "transient": "Aura API error, malformed uid, or the Mixpanel proxy never ran for it "
                 "— genuinely retried next run",
}

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
    default pool_maxsize (10) connections churn badly. A shared Session with
    pool_maxsize == workers keeps connections warm (keep-alive) and bounded."""
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
        r = _session().get(f"{base}/users/{uid}", timeout=30)
        if r.status_code != 200:
            return (r.status_code, None)
        d = r.json()
        # A 200 whose body isn't an object is not a user record — treat it as transient
        # rather than letting `.get` raise inside a worker thread.
        return (200, d) if isinstance(d, dict) else (None, None)
    except Exception:  # noqa: BLE001 — resilient per-user; retried next run
        return (None, None)


def fetch_aura(uid: str, stamps: set | None = None):
    """Return (ai_created_at, created_at, outcome, onboarding_ts).

    outcome distinguishes what Postgres actually said from a failure to read it:
      - 'postgres' / 'postgres_dev': a real aiCreatedAt on the prod / dev Aura API —
        non-null, and not a batch stamp (see `is_batch_stamp`) — use it.
      - 'fallback': the prod record exists but has no usable aiCreatedAt (null, the
        sentinel, or one of the exact-noon batch stamps) — eligible for the onboarding
        rung and then the Mixpanel first-AI-login proxy, which reads the prod project,
        so it can actually answer for this user.

    `onboarding_ts` is `onboarding.shown.consumerAIOnboarding` when present. It rides
    along on every outcome because it costs nothing (same response) and it RESCUES a
    user whose Postgres value is unusable — it never overrides a real one, so it can
    never move a cohort later than Postgres says.
      - 'fallback_dev': the same, but the record only exists on dev. The Mixpanel proxy
        is still attempted (it costs nothing extra — the query is already batched) but
        the prod project holds no dev traffic, so it almost never answers.
      - 'not_found_both': 404 on prod and on dev — no Aura user record anywhere. Still
        routed to the proxy (a cost-table user can have events but no /users row), then
        reported as a stable orphan rather than a failure.
      - 'error': a *transient/unknown* failure (non-200 other than 404, network, parse)
        or a malformed uid — we did NOT read Postgres, so leave the user unresolved and
        retry next run. Never fall back on an error: a transient outage would otherwise
        permanently cache a proxy date in this append-only table instead of the
        canonical postgres value.
    """
    stamps = stamps or {SENTINEL_PREFIX}
    if not _UID_RE.match(uid):
        return (None, None, "error", None)

    status, d = _get_user(PROD_BASE, uid)
    if status == 200:
        aic, created = d.get("aiCreatedAt"), d.get("createdAt")
        ob = _onboarding_ai_ts(d)
        if aic and not is_batch_stamp(aic, stamps) and not stamp_by_onboarding(aic, ob):
            return (aic, created, "postgres", ob)
        return (None, created, "fallback", ob)
    if status != 404:
        return (None, None, "error", None)  # non-200/non-404 on prod, or network error

    # Prod 404 — the user may live in the dev environment, where their aiCreatedAt is a
    # real value. Without this leg dev users are unresolvable: they 404 on prod and emit
    # nothing into the prod Mixpanel project the fallback reads.
    status, d = _get_user(DEV_BASE, uid)
    if status == 200:
        aic, created = d.get("aiCreatedAt"), d.get("createdAt")
        ob = _onboarding_ai_ts(d)
        if aic and not is_batch_stamp(aic, stamps) and not stamp_by_onboarding(aic, ob):
            return (aic, created, "postgres_dev", ob)
        return (None, created, "fallback_dev", ob)
    if status == 404:
        return (None, None, "not_found_both", None)
    return (None, None, "error", None)  # non-200/non-404 on dev, or network error


# The Aurie event namespaces the proxy searches. TWO, not one: Aurie used inside the
# Aura app emits `aiaura_*` (`aiaura_sign_up_start`, `aiaura_conversation_message_sent`,
# …), which does NOT match `ai_` — the underscore is the discriminator — so a union built
# on `ai_` alone is disjoint from the entire in-Aura surface and structurally cannot see
# those users. They were reported `no_ai_activity`, a reason whose meaning is "only once
# they act", while having signed up and conversed. ENG/PROD-6096.
AURIE_TABLE_PREFIXES = ("ai_", "aiaura_")

# Namespaces that look like Aurie and are NOT. Bare `chat_*` / `conversation_*` are the
# AURA app's own chat feature (`chat_beta_joined`, `chat_ai_onboarding_started`,
# `conversation_mode_selected`) — a different product, not the Aurie app. Folding them in
# is far worse than the bug above: measured against the rows already stored, 56 of 62
# resolved users who also have `chat_*` events would be back-dated, by 651 days on average
# and up to 1,921 days, silently placing real Aurie users in 2023/2024 cohorts they were
# never in. Named here so a grep for "AI-looking tables" meets the reason, not just a
# filter. Do not reconcile these into AURIE_TABLE_PREFIXES.
NON_AURIE_TABLE_PREFIXES = ("chat_", "conversation_")


def fallback_event_tables() -> list:
    """Every Aurie event table in the Nessie dataset that carries `userid` + `time`.

    Discovered from INFORMATION_SCHEMA, never hand-curated: the union *is* the definition
    of the proxy — the first AI activity of any kind — and any fixed list silently misses
    whoever's earliest event isn't on it (e.g. `ai_login`, `ai_conversation_session_ended`).

    **The prefix set is the hand-curated part**, and it was wrong: `ai_` alone excluded
    `aiaura_*`, the in-Aura Aurie surface. Discovering the table list from
    INFORMATION_SCHEMA never protected against that, because the predicate decides what
    the discovery can see. See `AURIE_TABLE_PREFIXES` / `NON_AURIE_TABLE_PREFIXES` above.

    The `userid`/`time` filter is load-bearing — a handful of tables lack one or both, and
    a single missing column makes the whole UNION fail to compile.

    Widening the union is safe at the grain consumers use: measured over the UIDs already
    resolved this way, the full union moves 44 of 119 timestamps earlier and none by a
    whole day, so day/week-grained cohort assignment is unchanged. Re-measure before
    changing what the union covers.
    """
    pred = " OR ".join(f"STARTS_WITH(table_name, '{p}')" for p in AURIE_TABLE_PREFIXES)
    rows = bq.query(
        f"SELECT table_name FROM `{MIX_DS}.INFORMATION_SCHEMA.COLUMNS` "
        f"WHERE ({pred}) GROUP BY table_name "
        "HAVING COUNTIF(column_name = 'userid') > 0 AND COUNTIF(column_name = 'time') > 0",
        project=bq.PROJECT,
    )
    return sorted(r["table_name"] for r in rows)


def fallback_first_ai(uids: list, chunk_size: int = 5000) -> tuple:
    """Earliest AI-event time per uid. Returns ``({uid: iso_time}, answered)``.

    `answered` is the subset of `uids` the proxy actually got a verdict on — a UID in it
    with no entry in the map genuinely has no AI event, while a UID **absent** from it was
    never asked. Callers must keep the two apart: "we looked and found nothing" is a
    stable property of the user, "the lookup did not run" is a transient failure, and
    collapsing them reports a BigQuery outage as a permanently unresolvable cohort.

    UIDs are inlined into an ``IN (...)`` list, so we chunk them — an initial backfill
    can push thousands to the fallback, and one giant IN-list would risk BigQuery's
    query-size/complexity limits. Bytes scanned are independent of the IN-list (the union
    is column-pruned to `userid` + `time`), so the chunk is sized to keep the query text
    well inside BigQuery's 1 MB limit while minimising how many times the union is
    re-scanned. A failed chunk is skipped (its UIDs stay unresolved *and* unanswered)
    rather than failing the whole fallback.
    """
    uids = [u for u in uids if _UID_RE.match(u)]
    if not uids:
        return ({}, set())
    try:
        tables = fallback_event_tables()
    except BQError as e:  # noqa: BLE001 — best-effort; every uid stays unanswered
        print(f"  (could not list Mixpanel event tables, fallback skipped: {e})",
              file=sys.stderr)
        return ({}, set())
    if not tables:
        print("  (no ai_* event tables with userid+time — fallback skipped)", file=sys.stderr)
        return ({}, set())
    union = " UNION ALL ".join(
        f"SELECT userid, time FROM `{MIX_DS}.{t}`" for t in tables
    )
    out, answered = {}, set()
    for i in range(0, len(uids), chunk_size):
        chunk = uids[i:i + chunk_size]
        in_list = ",".join("'" + u + "'" for u in chunk)  # uids validated by _UID_RE
        sql = (
            f"SELECT userid, MIN(time) AS first_ai FROM ({union}) "
            f"WHERE userid IN ({in_list}) GROUP BY userid"
        )
        try:
            rows = bq.query(sql, project=bq.PROJECT)
        except BQError as e:  # noqa: BLE001 — best-effort; this chunk stays unanswered
            print(f"  (fallback chunk {i // chunk_size} failed, {len(chunk)} left "
                  f"unresolved: {e})", file=sys.stderr)
            continue
        answered.update(chunk)
        for r in rows:
            t = r.get("first_ai")
            if t is not None:
                out[r["userid"]] = t.isoformat() if hasattr(t, "isoformat") else str(t)
    return (out, answered)


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
    ap = argparse.ArgumentParser(description="Backfill the aurie_user_ai_created dimension.")
    ap.add_argument("--uids", help="Comma-separated UIDs (default: all in the cost table)")
    ap.add_argument("--uids-file", help="File of UIDs, one per line")
    ap.add_argument("--limit", type=int, help="Cap the number of missing UIDs fetched (test runs)")
    ap.add_argument("--workers", type=int, default=16, help="Concurrent Aura API calls (default 16)")
    ap.add_argument("--dry-run", action="store_true", help="Report the missing count + sample; no writes")
    ap.add_argument("--project", default=bq.PROJECT)
    ap.add_argument("--dataset", default=bq.DATASET)
    args = ap.parse_args()

    # AURA_API_TOKEN is required to read aiCreatedAt (Step 0). Hard-fail early rather
    # than let every Aura call fail and mass-route users into the Mixpanel fallback.
    # (--dry-run only needs BigQuery, so it's exempt.)
    if not args.dry_run and not os.environ.get("AURA_API_TOKEN"):
        print("AURA_API_TOKEN is not set — required to read aiCreatedAt from the Aura API "
              "(--dry-run works without it).", file=sys.stderr)
        return 1

    global TABLE, COST_TABLE
    TABLE = f"{args.project}.{args.dataset}.aurie_user_ai_created"
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
    already = len(req_unique) - len(missing)  # requested UIDs already present (not the whole table)
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

    # Step 1: Aura API (prod, then dev on a 404) — concurrent.
    workers = max(1, args.workers)
    _session(workers)  # init the shared pooled Session sized to the worker count
    stamps = known_batch_stamps()
    print(f"  batch stamps in play: {len(stamps)} "
          f"(exact {BATCH_STAMP_TIME} UTC, >= {BATCH_STAMP_MIN_USERS} users, plus the sentinel)")

    resolved, need_fallback, created_map, outcomes = {}, [], {}, {}
    onboarding_ts = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_aura, u, stamps): u for u in missing}
        for fut in as_completed(futs):
            u = futs[fut]
            aic, created, outcome, ob = fut.result()
            outcomes[u] = outcome
            if created is not None:
                created_map[u] = created
            if ob:
                onboarding_ts[u] = ob
            if outcome in ("postgres", "postgres_dev") and aic:
                resolved[u] = (aic, created, outcome)
            elif outcome in ("fallback", "fallback_dev", "not_found_both"):
                need_fallback.append(u)
            # 'error' — couldn't read Postgres; leave unresolved (retry next run) and
            # never fall back (a proxy date would be cached over the canonical value).
    api_errors = sum(1 for o in outcomes.values() if o == "error")
    print(f"  postgres resolved: {sum(1 for v in resolved.values() if v[2] == 'postgres')}"
          f"  postgres(dev) resolved: {sum(1 for v in resolved.values() if v[2] == 'postgres_dev')}"
          f"  need fallback: {len(need_fallback)}  api errors: {api_errors}")

    # Cold-start net for users with no onboarding timestamp: count exact-noon values
    # among THIS run's postgres results. A new upstream batch arrives as many users at
    # once, so it forms a cluster in the run even when the dimension has never seen it.
    fresh, moved = reroute_fresh_stamps(resolved, outcomes, need_fallback)
    if fresh:
        print(f"  NEW batch stamp(s) detected in this run: {sorted(fresh)} "
              f"— {len(moved)} users re-routed off postgres")

    # Step 1b: the AI-onboarding rung. Only users Postgres could not answer for reach it,
    # so it RESCUES and never overrides — a real aiCreatedAt always wins, which is what
    # stops this rung from ever moving a cohort later than Postgres says. Free: the
    # timestamp came back on the same /users response Step 1 already read.
    ob_resolved = [u for u in need_fallback if onboarding_ts.get(u)]
    for u in ob_resolved:
        resolved[u] = (onboarding_ts[u], created_map.get(u), "ai_onboarding")
    need_fallback = [u for u in need_fallback if u not in resolved]
    print(f"  ai-onboarding resolved: {len(ob_resolved)}  still need proxy: {len(need_fallback)}")

    # Step 2: Mixpanel first-AI-login fallback — one batched query per chunk.
    fb, fb_answered = fallback_first_ai(need_fallback) if need_fallback else ({}, set())
    for u, t in fb.items():
        resolved[u] = (t, created_map.get(u), "first_ai_login")

    # Guard against an overlapping run (or a long run + a retry) having inserted some of
    # these UIDs since we computed `missing`. We're append-only with no unique key, so
    # re-check right before writing and drop any now present. This narrows — it doesn't
    # fully close — the race, so consumers should still dedup by user_id (latest
    # fetched_at wins) defensively (see SKILL.md "Reading the table").
    try:
        now_have = existing_uids()
    except BQError:
        now_have = have
    skipped_concurrent = [u for u in resolved if u in now_have]
    for u in skipped_concurrent:
        resolved.pop(u, None)

    unresolved = [u for u in missing if u not in resolved and u not in now_have]

    # Step 3: append resolved rows.
    now = _now_iso()
    out_rows = [
        {"user_id": u, "ai_created_at": aic, "created_at": created,
         "source": source, "fetched_at": now}
        for u, (aic, created, source) in resolved.items()
    ]
    written = append_rows(out_rows)

    print("\n=== coverage ===")
    for src in ("postgres", "postgres_dev", "ai_onboarding", "first_ai_login"):
        print(f"  newly resolved ({src}):".ljust(36),
              sum(1 for v in resolved.values() if v[2] == src))
    print("  rows appended:".ljust(36), written)
    if skipped_concurrent:
        print("  skipped (added by a concurrent run):".ljust(36), len(skipped_concurrent))
    print("  still unresolved:".ljust(36), len(unresolved))

    # Classify the residue: most of it is a stable property of the user, not a failure,
    # and one opaque "retry later" count cannot tell the two apart.
    if unresolved:
        by_reason = {}
        for u in unresolved:
            o = outcomes.get(u, "transient")
            # A stable reason may only be claimed when the proxy actually answered for
            # this UID. If its chunk (or the table discovery) failed, nobody established
            # that it has no AI event — that is a transient failure wearing a stable
            # label, and the cadence only flags 'transient'.
            if o != "error" and u not in fb_answered:
                reason = "transient"
            else:
                reason = {"fallback": "no_ai_activity",
                          "fallback_dev": "dev_no_ai_activity",
                          "not_found_both": "no_user_record",
                          "error": "transient"}.get(o, "transient")
            by_reason.setdefault(reason, []).append(u)
        for reason, us in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
            print(f"    {len(us):>5}  {reason} — {UNRESOLVED_REASONS[reason]}")
        stable = sum(len(v) for r, v in by_reason.items() if r != "transient")
        if stable:
            print(f"  {stable} of these have no join date to find; a re-run only helps once "
                  "the user acts (they stay in the count by design — see SKILL.md).")
        print("  unresolved sample:", ", ".join(u[:6] + "…" for u in unresolved[:10]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
