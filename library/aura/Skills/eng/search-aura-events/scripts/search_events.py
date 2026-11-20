#!/usr/bin/env python3
"""Search the Aura event stream in BigQuery — `aura-main.firebase.raw_events`.

Pivots: by Firebase UID, by event name, by experiment id, by any `data` JSON key,
by date range. Renders a timeline with UTC and Pacific side by side.

Three properties are load-bearing and are enforced here rather than left to the
caller, because each fails SILENTLY when you get it wrong:

1. **The table is `raw_events`, never `events`.** `firebase.events` is a different
   flat table with no user-lifecycle events at all — querying it for a deletion
   returns zero rows instead of an error. `TABLE` is a constant; there is no
   `--table` flag.

2. **`createdAt` does not prune.** `raw_events` is MONTH-partitioned on INGESTION
   time (`time_partitioning.field is None`), so a `WHERE createdAt >= …` filter
   reads every partition — measured 4.274 GB either way. Only `_PARTITIONTIME`
   prunes, so `--since` emits a `_PARTITIONTIME` floor alongside the `createdAt`
   predicate.

3. **A bare `_PARTITIONTIME >= …` drops the newest rows.** Streaming-buffer rows
   sit in the `__NULL__` partition with `_PARTITIONTIME IS NULL` — that is where
   the most recent events live, and `INFORMATION_SCHEMA.PARTITIONS` reports it as
   0 rows even when it holds thousands. Every partition predicate this script
   emits is `(_PARTITIONTIME IS NULL OR _PARTITIONTIME >= …)`.

Read-only. Every run dry-runs first and reports scanned bytes before touching the
table; a scan above `--max-gb` is refused rather than billed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bq import BQError, BQUnavailable, query  # noqa: E402

PROJECT = "aura-main"
TABLE = "aura-main.firebase.raw_events"
DECOY = "aura-main.firebase.events"
PT_ZONE = "America/Los_Angeles"

# Why `raw_events` and not `events` — printed by --why and on an empty result.
ROUTING_NOTE = (
    f"Routed to `{TABLE}` (JSON `data` column, MONTH partitions on ingestion time).\n"
    f"`{DECOY}` is a different, flat table (messageId, name, contentId, userId, count,\n"
    "emotion, rating, createdAt) carrying no user-lifecycle events — its only delete-ish\n"
    "name is ContentDeleted. Querying it for a user deletion returns zero rows rather than\n"
    "an error, which reads as 'it never happened'."
)

# The account-lifecycle vocabulary — what `--lifecycle` (and a bare --uid) selects.
# Not the full 253 names; run --list-names to enumerate those from the table.
LIFECYCLE_NAMES = [
    "UserCreated", "UserUpdated", "UserDeleted", "RoleUpdated", "UserAttributed",
    "UserLoggedIn", "UserAgentUpdated", "EmailValidated", "EmailInvalidated",
    "EmailPermissionsUpdated",
    # Social graph — listed under account lifecycle in references/event-vocabulary.md,
    # so --lifecycle must select them or the doc and the flag disagree.
    "UserFollower", "UserFollowing", "UserUnfollowing", "UserUnfollower",
    "UserDonationCreated",
]
# Subscription families are matched by prefix so a new member is picked up without
# an edit here: Subscription*, AISubscription*, CommunitySubscription*, Coach*.
LIFECYCLE_PREFIXES = ["Subscription", "AISubscription", "CommunitySubscription",
                      "CoachSubscription", "CoachingSubscription", "BusinessSubscription"]

# `data` keys carrying PII. Redacted unless --show-pii. UserCreated / UserUpdated
# carry email + givenName, so a raw dump of either is a PII leak to whatever
# surface the output lands on. See Compliance/Privacy.
PII_KEYS = {"email", "givenName", "familyName", "phone", "phoneNumber",
            "fullName", "displayName"}
# `name` is NOT in PII_KEYS: across the live corpus it is an entity name on nearly
# every event that carries it (experiment / serie / course / paywall), and blanket
# redaction hid the experiment id on every ExperimentUpdated row. It is a person's
# name only on the Coach* events, so redact it there and nowhere else.
PII_KEYS_BY_EVENT = {"CoachCreated": {"name"}, "CoachUpdated": {"name"}}

# `--uid` and `--experiment` are bound as query parameters, so they need no charset
# restriction — only a sanity bound. A stricter check refuses real identifiers:
# `google:111555896239269076429` is a live userId, and 25 of 2,429 live
# experimentIds are short or punctuated (`bm1`, `quiz`, `subscription50%OffLabel`).
# The two positions BigQuery cannot parameterize — a JSON path and an event name —
# are interpolated, so those stay strict.
_JSON_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def _bound(value: str, label: str) -> str:
    """Sanity-check a value that will be sent as a bound query parameter."""
    if not value or not value.strip():
        raise Refused(f"empty {label}")
    if len(value) > 256:
        raise Refused(f"{label} too long ({len(value)} chars)")
    return value


class Refused(Exception):
    """The request was rejected before any query ran."""


# --------------------------------------------------------------------------- #
# retention
# --------------------------------------------------------------------------- #

def live_partitions() -> list:
    """Partition ids currently alive, oldest first, from INFORMATION_SCHEMA.

    Metadata-only (~10 MB billed), so this is cheap enough to run on every
    invocation. `__NULL__` (the streaming buffer) is excluded: it is not a
    calendar month, and its `total_rows` is reported as 0 even when it holds
    thousands of rows.
    """
    rows = query(
        "SELECT partition_id, total_rows FROM "
        "`aura-main.firebase.INFORMATION_SCHEMA.PARTITIONS` "
        "WHERE table_name = 'raw_events' ORDER BY partition_id",
        PROJECT,
    )
    return [r for r in rows if re.fullmatch(r"\d{6}", r["partition_id"] or "")]


def retention_floor(parts: list) -> date:
    """First day covered by the oldest live partition."""
    oldest = parts[0]["partition_id"]
    return date(int(oldest[:4]), int(oldest[4:]), 1)


def retention_warning(since: date | None, until: date | None, floor: date) -> str:
    """Say explicitly when the requested window falls outside what still exists.

    An empty result from an expired window is not evidence the event never
    happened — the partition was dropped. Retention is 45 days on INGESTION, so
    the boundary is the oldest live partition, not `today - 45`.
    """
    if since is None and until is None:
        return ""
    head = (f"⚠️  RETENTION — `{TABLE}` keeps ~45 days of INGESTED events "
            f"(MONTH partitions, expirationMs 3888000000).\n"
            f"    Oldest live partition starts {floor.isoformat()}.")
    if until is not None and until < floor:
        return (f"{head}\n"
                f"    The requested window ends {until.isoformat()} — WHOLLY outside "
                f"retention.\n"
                f"    An empty result here means the partition expired, NOT that nothing "
                f"happened.")
    if since is not None and since < floor:
        return (f"{head}\n"
                f"    The requested window starts {since.isoformat()} — PARTLY outside "
                f"retention.\n"
                f"    Anything before {floor.isoformat()} is gone; absence there is not "
                f"evidence of absence.")
    return ""


# --------------------------------------------------------------------------- #
# query building
# --------------------------------------------------------------------------- #

def month_floor(d: date) -> date:
    return d.replace(day=1)


def build_where(args, params: dict) -> list:
    """Predicates for the timeline query. Mutates `params` with bound values.

    Everything user-supplied is bound as a query parameter. The two exceptions
    that cannot be — a JSON path and an event name — are regex-validated instead,
    because BigQuery does not parameterize either position.
    """
    where = []

    if args.uid:
        where.append("JSON_VALUE(data, '$.userId') = @uid")
        params["uid"] = _bound(args.uid, "uid")

    if args.experiment:
        where.append("JSON_VALUE(data, '$.experimentId') = @exp")
        params["exp"] = _bound(args.experiment, "experiment id")

    for i, kv in enumerate(args.key or []):
        if "=" not in kv:
            raise Refused(f"--key expects key=value, got {kv!r}")
        k, v = kv.split("=", 1)
        k = k.lstrip("$.").strip()
        if not _JSON_KEY_RE.match(k):
            raise Refused(f"unsafe JSON key {k!r} — letters, digits and underscore only")
        if v == "null":
            # The timeline renders a JSON null as `null` and the docs make
            # `role=null` the de-role signal, so `--key role=null` is the obvious
            # thing to type — but `JSON_VALUE(...) = 'null'` never matches, giving a
            # silent zero rows. BigQuery collapses JSON-null and key-absent to the
            # same SQL NULL (`JSON_QUERY` does not separate them either), so IS NULL
            # is the only available match; pair it with --name to make it exact.
            where.append(f"JSON_VALUE(data, '$.{k}') IS NULL")
        else:
            where.append(f"JSON_VALUE(data, '$.{k}') = @k{i}")
            params[f"k{i}"] = v

    names = list(args.name or [])
    for n in names:
        if not _NAME_RE.match(n):
            raise Refused(f"unsafe event name {n!r}")
    if names:
        where.append("name IN (" + ", ".join(f"'{n}'" for n in names) + ")")
    elif args.lifecycle:
        lit = ", ".join(f"'{n}'" for n in LIFECYCLE_NAMES)
        pre = " OR ".join(f"STARTS_WITH(name, '{p}')" for p in LIFECYCLE_PREFIXES)
        where.append(f"(name IN ({lit}) OR {pre})")

    if args.since:
        where.append("createdAt >= TIMESTAMP(@since)")
        params["since"] = f"{args.since.isoformat()} 00:00:00+00"
        # The ONLY predicate that prunes. Floored to the partition's month, and
        # OR'd with IS NULL so the streaming buffer — which holds the newest rows —
        # survives. `_PARTITIONTIME` is ingestion time and ingestion never precedes
        # the event, so a month floor cannot drop a row inside the window.
        where.append(
            "(_PARTITIONTIME IS NULL OR _PARTITIONTIME >= "
            f"TIMESTAMP('{month_floor(args.since).isoformat()}'))"
        )
    if args.until:
        where.append("createdAt < TIMESTAMP(@until)")
        params["until"] = f"{(args.until + timedelta(days=1)).isoformat()} 00:00:00+00"
        # Deliberately NO `_PARTITIONTIME <=` ceiling: a row with an old `createdAt`
        # can be ingested recently (1,168 rows carry a pre-2026 createdAt), so a
        # ceiling on ingestion time would drop rows that are inside the event-time
        # window the caller asked for.

    return where


def build_sql(args, params: dict) -> str:
    where = build_where(args, params)
    if not where:
        raise Refused(
            "no filter given — that is a full-table scan. Pass at least one of "
            "--uid / --name / --experiment / --key / --since."
        )
    # Always take the NEWEST rows when capping: `ORDER BY createdAt ASC` + LIMIT
    # keeps the OLDEST N, which silently drops the recent UserDeleted / RoleUpdated
    # that most questions here are about. `--order asc` re-sorts for display only.
    order = "DESC" if args.limit else ("ASC" if args.order == "asc" else "DESC")
    limit = f"\nLIMIT {int(args.limit)}" if args.limit else ""
    return (
        "SELECT createdAt,\n"
        f'       DATETIME(createdAt, "{PT_ZONE}") AS pt,\n'
        "       name,\n"
        "       data\n"
        f"FROM `{TABLE}`\n"
        "WHERE " + "\n  AND ".join(where) + "\n"
        f"ORDER BY createdAt {order}{limit}"
    )


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #

def redact_data(raw: str, show_pii: bool, event: str = ""):
    """Parse a row's `data` and blank the PII values. The single allow-list both
    the table renderer and the --json path go through, so neither can drift into
    emitting what the other redacts."""
    try:
        obj = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return raw
    if not isinstance(obj, dict) or show_pii:
        return obj
    pii = PII_KEYS | PII_KEYS_BY_EVENT.get(event, set())
    return {k: ("<redacted>" if k in pii else v) for k, v in obj.items()}


def flatten(raw: str, fields: list | None, show_pii: bool, event: str = "",
            hide: tuple = ("at", "origin")) -> tuple:
    """Render one row's `data` JSON as `k=v` pairs. Returns (text, redacted_any)."""
    try:
        obj = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return (str(raw)[:200], False)
    if not isinstance(obj, dict):
        return (str(obj)[:200], False)
    # `at` duplicates createdAt to the millisecond and `origin` is on every event;
    # both are noise in a timeline unless explicitly asked for.
    keys = fields if fields else [k for k in obj if k not in hide]
    pii = PII_KEYS | PII_KEYS_BY_EVENT.get(event, set())
    out, redacted = [], False
    for k in keys:
        if k not in obj:
            continue
        v = obj[k]
        if k in pii and not show_pii:
            v, redacted = "<redacted>", True
        if v is None:
            v = "null"
        elif isinstance(v, (dict, list)):
            v = json.dumps(v, separators=(",", ":"))
        out.append(f"{k}={v}")
    return (" ".join(out), redacted)


def render(rows: list, fields: list | None, show_pii: bool,
           hide: tuple = ("at", "origin")) -> str:
    if not rows:
        return "(no rows)"
    lines, any_redacted = [], False
    body = []
    for r in rows:
        utc = r["createdAt"]
        utc_s = utc.strftime("%Y-%m-%d %H:%M:%S") if hasattr(utc, "strftime") else str(utc)
        pt = r["pt"]
        pt_s = pt.strftime("%Y-%m-%d %H:%M:%S") if hasattr(pt, "strftime") else str(pt)
        d, red = flatten(r.get("data"), fields, show_pii, r["name"], hide)
        any_redacted = any_redacted or red
        body.append((utc_s, pt_s, r["name"], d))
    wn = max(len(b[2]) for b in body)
    lines.append(f"{'UTC':<19}  {'PT':<19}  {'event':<{wn}}  data")
    lines.append(f"{'-'*19}  {'-'*19}  {'-'*wn}  {'-'*4}")
    for utc_s, pt_s, name, d in body:
        lines.append(f"{utc_s:<19}  {pt_s:<19}  {name:<{wn}}  {d}")
    lines.append("")
    lines.append(f"{len(rows)} event(s). Timestamps: createdAt is UTC; PT is "
                 f"{PT_ZONE} (PDT = UTC−7 through early November).")
    if any_redacted:
        lines.append("PII fields redacted (email / givenName / …) — pass --show-pii to "
                     "include them, and see Compliance/Privacy before pasting anywhere.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# modes
# --------------------------------------------------------------------------- #

def mode_list_names(args) -> str:
    """Enumerate the event vocabulary rather than guessing a name.

    Only the `name` column is read, so this is ~0.31 GB against the 4.27 GB a
    `data`-touching query costs.
    """
    sql = (f"SELECT name, COUNT(*) AS n FROM `{TABLE}`\n"
           "GROUP BY name ORDER BY n DESC")
    est = query(sql, PROJECT, dry_run=True)[0]["bytes"]
    print(f"scan estimate: {est/1e9:.3f} GB", file=sys.stderr)
    if args.dry_run:
        return f"scan estimate: {est/1e9:.3f} GB\n\n(dry run — no query executed)"
    if not gate(est, args):
        return "(refused)"
    rows = query(sql, PROJECT, max_bytes=int(args.max_gb * 1e9))
    if args.match:
        pat = args.match.lower()
        rows = [r for r in rows if pat in r["name"].lower()]
    out = [f"{len(rows)} event name(s)" + (f" matching {args.match!r}" if args.match else ""),
           ""]
    out += [f"{r['n']:>10,}  {r['name']}" for r in rows]
    return "\n".join(out)


def mode_sample(args) -> str:
    """Show the `data` key shape for one event name.

    `data` keys vary per event — `$.userId`, `$.experimentId`, `$.serieId`,
    `$.role`, `$.status` — so sample before filtering on a key. Note that
    experiment-level events (ExperimentUpdated) carry NO `$.userId` at all.
    """
    name = args.sample
    if not _NAME_RE.match(name):
        raise Refused(f"unsafe event name {name!r}")
    # Sampling reads `data`, the fat column — unpruned that is the whole 4.27 GB.
    # Newest-first sampling only needs the newest live partition plus the streaming
    # buffer, which is where the newest rows are.
    parts = live_partitions()

    def sample_sql(floor: str | None) -> str:
        part = (f"  AND (_PARTITIONTIME IS NULL OR "
                f"_PARTITIONTIME >= TIMESTAMP('{floor}'))\n") if floor else ""
        return (f"SELECT data FROM `{TABLE}`\n"
                f"WHERE name = '{name}'\n{part}"
                f"ORDER BY createdAt DESC LIMIT {int(args.sample_rows)}")

    newest = parts[-1]["partition_id"]
    sql = sample_sql(f"{newest[:4]}-{newest[4:]}-01")
    est = query(sql, PROJECT, dry_run=True)[0]["bytes"]
    print(f"scan estimate: {est/1e9:.3f} GB", file=sys.stderr)
    if args.dry_run:
        return f"scan estimate: {est/1e9:.3f} GB\n\n(dry run — no query executed)"
    if not gate(est, args):
        return "(refused)"
    rows = query(sql, PROJECT, max_bytes=int(args.max_gb * 1e9))
    widened = False
    if not rows and len(parts) > 1:
        # Rare names can live only in an older still-live partition. Reporting them
        # absent would be the "empty result reads as never happened" failure.
        sql = sample_sql(None)
        est2 = query(sql, PROJECT, dry_run=True)[0]["bytes"]
        print(f"no rows in the newest partition — widening to all live partitions "
              f"({est2/1e9:.3f} GB)", file=sys.stderr)
        if gate(est2, args):
            rows = query(sql, PROJECT, max_bytes=int(args.max_gb * 1e9))
            widened = True
    if not rows:
        scope = "any live partition" if widened else "the newest live partition"
        return (f"no rows for {name!r} in {scope}.\n"
                f"Live window starts {retention_floor(parts).isoformat()} — an event "
                f"ingested before that has expired.\n\n{ROUTING_NOTE}")
    keys, samples = set(), {}
    for r in rows:
        try:
            obj = json.loads(r["data"])
        except (TypeError, ValueError):
            continue
        if isinstance(obj, dict):
            keys |= set(obj)
            for k, v in obj.items():
                samples.setdefault(k, v)
    out = [f"{name} — {len(rows)} sampled row(s), {len(keys)} distinct `data` key(s)", ""]
    for k in sorted(keys):
        v = samples.get(k)
        if k in (PII_KEYS | PII_KEYS_BY_EVENT.get(name, set())) and not args.show_pii:
            v = "<redacted — PII>"
        out.append(f"  $.{k:<22} e.g. {json.dumps(v, default=str)[:80]}")
    if "userId" not in keys:
        out += ["", "NOTE: this event carries no $.userId — it cannot be pivoted by UID."]
    return "\n".join(out)


def gate(est_bytes: int, args) -> bool:
    """Refuse a scan above --max-gb. Multi-GB and unclustered, so this matters."""
    cap = args.max_gb * 1e9
    if est_bytes <= cap:
        return True
    print(f"REFUSED: estimated scan {est_bytes/1e9:.3f} GB exceeds --max-gb "
          f"{args.max_gb:.1f}. Narrow the window (--since prunes; --until does not) "
          f"or raise --max-gb deliberately.", file=sys.stderr)
    return False


def mode_timeline(args) -> str:
    params: dict = {}
    sql = build_sql(args, params)

    parts = live_partitions()
    floor = retention_floor(parts)
    warn = retention_warning(args.since, args.until, floor)

    est = query(sql, PROJECT, dry_run=True, params=params)[0]["bytes"]
    header = [
        f"table: {TABLE}",
        "live partitions: " + ", ".join(
            f"{p['partition_id']} ({p['total_rows']:,} rows)" for p in parts
        ) + "  + streaming buffer (_PARTITIONTIME IS NULL)",
        f"scan estimate: {est/1e9:.3f} GB",
    ]
    if warn:
        header += ["", warn]

    if args.dry_run:
        return "\n".join(header) + "\n\n(dry run — no query executed)"
    if not gate(est, args):
        return "\n".join(header) + "\n\n(refused — scan above --max-gb)"

    rows = query(sql, PROJECT, params=params, max_bytes=int(args.max_gb * 1e9))

    # Applied before the --json branch, not after it: SQL always takes the newest N
    # when capping, so --order asc is honoured here for BOTH output paths.
    capped = bool(args.limit and len(rows) == args.limit)
    if args.limit and args.order == "asc":
        rows = list(reversed(rows))

    if args.json:
        # `meta` is not decoration: without the retention banner a caller cannot
        # tell an empty `events` list from a window whose partitions expired.
        return json.dumps({
            "meta": {
                "table": TABLE,
                "scan_bytes": est,
                "live_partitions": [p["partition_id"] for p in parts],
                "retention_floor": floor.isoformat(),
                "retention_warning": warn or None,
                "row_limit_hit": capped,
                "pii_redacted": not args.show_pii,
            },
            "events": [{
                "createdAt": str(r["createdAt"]),
                "pt": str(r["pt"]),
                "name": r["name"],
                "data": redact_data(r.get("data"), args.show_pii, r["name"]),
            } for r in rows],
        }, indent=2, default=str)

    out = ["\n".join(header), ""]
    # Hide `userId` only when the query was pivoted on it — otherwise it is the
    # answer to "who?", which is the point of a --name / --key / experiment search.
    hide = ("at", "origin", "userId") if args.uid else ("at", "origin")
    out.append(render(rows, args.fields.split(",") if args.fields else None,
                      args.show_pii, hide))
    if capped:
        out.append(f"\n⚠️  Hit the --limit cap of {args.limit}. These are the NEWEST "
                   f"{args.limit} matching events — OLDER ones exist and are not shown. "
                   f"Raise --limit or narrow the filter.")
    if not rows:
        out += ["", ROUTING_NOTE]
        if not warn:
            out.append(f"\nThe live window starts {floor.isoformat()} — anything ingested "
                       "before that has expired.")
    return "\n".join(out)


# --------------------------------------------------------------------------- #

def parse_day(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {s!r}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser(
        description="Search the Aura event stream in aura-main.firebase.raw_events.")
    p.add_argument("--uid", help="Firebase UID — filters JSON_VALUE(data,'$.userId')")
    p.add_argument("--name", action="append",
                   help="event name (repeatable); omit with --uid for the full stream")
    p.add_argument("--experiment", help="experiment id — filters $.experimentId")
    p.add_argument("--key", action="append", metavar="KEY=VALUE",
                   help="any other data JSON key, e.g. serieId=abc (repeatable)")
    p.add_argument("--lifecycle", action="store_true",
                   help="restrict to the account-lifecycle vocabulary "
                        "(User*/Role*/Email*/Subscription*)")
    p.add_argument("--since", type=parse_day, metavar="YYYY-MM-DD",
                   help="event-time lower bound; also emits the partition floor "
                        "(the only predicate that prunes)")
    p.add_argument("--until", type=parse_day, metavar="YYYY-MM-DD",
                   help="event-time upper bound, inclusive")
    p.add_argument("--order", choices=("asc", "desc"), default="asc")
    p.add_argument("--limit", type=int, default=200,
                   help="row cap (default 200; 0 = no cap). Does NOT reduce bytes "
                        "scanned — BigQuery reads the columns first.")
    p.add_argument("--fields", help="comma-separated data keys to show (default: all "
                                    "but at/origin/userId)")
    p.add_argument("--show-pii", action="store_true",
                   help="include email / givenName / … — see Compliance/Privacy")
    p.add_argument("--list-names", action="store_true",
                   help="enumerate the event vocabulary with counts")
    p.add_argument("--match", help="with --list-names: case-insensitive substring filter")
    p.add_argument("--sample", metavar="EVENT_NAME",
                   help="show the data JSON key shape for one event name")
    p.add_argument("--sample-rows", type=int, default=20)
    p.add_argument("--why", action="store_true",
                   help="explain why this routes to raw_events and not events")
    p.add_argument("--dry-run", action="store_true",
                   help="price the scan and stop")
    p.add_argument("--max-gb", type=float, default=6.0,
                   help="refuse a scan above this many GB (default 6.0)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    try:
        if args.why:
            print(ROUTING_NOTE)
            return
        if args.list_names:
            print(mode_list_names(args))
        elif args.sample:
            print(mode_sample(args))
        else:
            print(mode_timeline(args))
    except Refused as e:
        sys.exit(f"refused: {e}")
    except BQUnavailable as e:
        sys.exit(f"BigQuery unavailable: {e}")
    except BQError as e:
        sys.exit(f"BigQuery error: {e}")


if __name__ == "__main__":
    main()
