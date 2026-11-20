#!/usr/bin/env python3
"""Reconcile an Aura coach's dashboard listen counts against what they were paid.

Dashboard side  : Firebase RTDB /coachAnalytics/<uid>/<year>[monthIndex]
Payout side     : Aura API GET /payouts/analytics (archived monthly query results)
Burst side      : Aura API POST /payouts/listens/aggregate (recent months only)
"""

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

API_BASE = os.environ.get("AURA_API_BASE", "https://services.aurahealth.io")
RTDB_BASE = os.environ.get("FIREBASE_DATABASE_URL", "https://auratech16.firebaseio.com")
UID_RE = re.compile(r"^[A-Za-z0-9]{28}$")
PAGE_LIMIT = 500
ROW_LIMIT = 5000  # server-side maxLimit on /payouts/listens/aggregate
UID_PREFIX = 10
TIERS = [
    "<500Listens",
    "500-1000Listens",
    "1000-2000Listens",
    "2000-5000Listens",
    ">5000Listens",
]
MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
# monthlyListenTotalNoCap landed 2025-11-07 (AINative-adjacent: PaymentService e03d427f).
# Archived rows before this month do not follow the Nov-onward pattern - refuse them.
EARLIEST = (2025, 11)


class AuditError(Exception):
    """Fatal, user-actionable problem."""



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

def api_token() -> str:
    for name in ("AURA_ADMIN_TOKEN", "AURA_API_TOKEN"):
        token = os.environ.get(name)
        if token:
            return token
    raise AuditError(
        "Set AURA_ADMIN_TOKEN (preferred) or AURA_API_TOKEN. "
        "GET /payouts/analytics requires role admin, creator or service."
    )


def request_json(url: str, *, method: str = "GET", body=None, headers=None, timeout: int = 60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", user_agent())
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise AuditError(f"{method} {url.split('?')[0]} -> HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise AuditError(f"{method} {url.split('?')[0]} -> {error.reason}") from error


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {api_token()}"}


# --------------------------------------------------------------------------- coach


def fetch_coach_catalog() -> list:
    """Walk every page of /coaches. A single page is NOT the catalog - a name that looks
    unique on page one can have a twin further in, which would silently audit the wrong
    coach instead of stopping on the ambiguity."""
    coaches, offset = [], 0
    while True:
        page = request_json(
            f"{API_BASE}/coaches?limit={PAGE_LIMIT}&offset={offset}", headers=auth_headers()
        )
        if isinstance(page, dict):
            page = page.get("coaches") or page.get("results") or page.get("items") or []
        if not isinstance(page, list) or not page:
            break
        coaches.extend(page)
        if len(page) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
    return coaches


def resolve_coach(coach: str) -> tuple:
    """Return (uid, display_name). Raises with candidates when a name is ambiguous."""
    if UID_RE.match(coach):
        return coach, coach
    catalog = fetch_coach_catalog()
    needle = coach.strip().lower()
    # Exact name wins outright. Without this, a coach whose full name is a substring of
    # another's ("Hannah" inside "Hannah Castillo") is unreachable by name entirely -
    # the correct full name would always come back ambiguous.
    exact = [c for c in catalog if (c.get("name") or "").strip().lower() == needle]
    matches = exact or [c for c in catalog if needle in (c.get("name") or "").lower()]
    if not matches:
        raise AuditError(
            f"No ACTIVE coach matched {coach!r}. The catalog only carries active coaches, "
            "so a departed coach will not appear here even though their archived payout "
            "rows are still auditable - rerun with the coach id."
        )
    if len(matches) > 1:
        listed = "\n".join(f"  {c.get('id')}  {c.get('name')}" for c in matches[:15])
        raise AuditError(f"{coach!r} matched {len(matches)} coaches - rerun with the id:\n{listed}")
    return matches[0]["id"], matches[0].get("name") or matches[0]["id"]


# --------------------------------------------------------------------- dashboard


def read_rtdb(paths: list) -> dict:
    """Read exact RTDB paths through rtdb_get.js (Admin SDK realtime protocol, never REST). {path: value}."""
    env = {**os.environ, "AINATIVE_USER_AGENT": user_agent()}
    env.setdefault("RTDB_URL", RTDB_BASE)
    if not env.get("GCP_KEY") and env.get("GOOGLE_APPLICATION_CREDENTIALS"):
        env["GCP_KEY"] = env["GOOGLE_APPLICATION_CREDENTIALS"]
    script = pathlib.Path(__file__).resolve().parent / "rtdb_get.js"
    try:
        proc = subprocess.run(["node", str(script), "--values"], input="\n".join(paths),
                              capture_output=True, text=True, encoding="utf-8", env=env, timeout=300)
    except (OSError, subprocess.SubprocessError) as error:
        raise AuditError(f"RTDB read through rtdb_get.js failed to run: {error}") from error
    rows = {}
    for line in proc.stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("path") in paths:
            rows[row["path"]] = row
    missing = [p for p in paths if p not in rows]
    failed = [p for p in paths if "error" in rows.get(p, {})]
    if missing or failed:
        detail = rows[failed[0]]["error"] if failed else (proc.stderr.strip().splitlines() or ["no output"])[-1]
        raise AuditError(
            f"RTDB read failed for {len(missing) + len(failed)} of {len(paths)} path(s): {detail}. "
            "Needs node, GCP_KEY, and AURA_CLI_DIR (a checkout whose node_modules has firebase-admin)."
        )
    return {p: rows[p].get("value") for p in paths}


def fetch_dashboard(uid: str, years) -> dict:
    """{(year, month): {premiumListens, premiumPaidListens, totalListens}}"""
    paths = [f"/coachAnalytics/{uid}/{year}" for year in sorted(years)]
    values = read_rtdb(paths)
    out = {}
    for year in sorted(years):
        months = values[f"/coachAnalytics/{uid}/{year}"]
        if not months:
            continue
        # RTDB returns a 12-slot array (index 0 = January); sparse years arrive as a dict.
        if isinstance(months, dict):
            items = [(int(k), v) for k, v in months.items()]
        else:
            items = list(enumerate(months))
        for index, value in items:
            if isinstance(value, dict):
                out[(year, index + 1)] = value
    return out


# ------------------------------------------------------------------------ payout


def fetch_archive(kind: str, uid: str, since: str, until: str) -> dict:
    query = urllib.parse.urlencode(
        {"type": kind, "coachId": uid, "since": since, "until": until}
    )
    rows = request_json(f"{API_BASE}/payouts/analytics?{query}", headers=auth_headers())
    return {(r["year"], r["month"]): (r.get("data") or {}) for r in rows if r.get("month")}


def tier_sum(row: dict):
    """Sum the five tier columns = the query's row count (ranges are exhaustive+disjoint)."""
    if not row:
        return None
    return sum(int(row.get(tier) or 0) for tier in TIERS)


# ------------------------------------------------------------------------ bursts


def fetch_bursts(uid: str, year: int, month: int) -> dict:
    since = f"{year:04d}-{month:02d}-01T00:00:00.000Z"
    nyear, nmonth = (year + 1, 1) if month == 12 else (year, month + 1)
    until = f"{nyear:04d}-{nmonth:02d}-01T00:00:00.000Z"
    counts, summaries = {}, {}
    for respect_cap in (True, False):
        payload = {
            "coaches": [uid],
            "groupBy": "userId",
            "since": since,
            "until": until,
            "requirePremiumPaid": True,
            "respectDailyCap": respect_cap,
            "includeMusic": False,
            "includeNonMusic": True,
            "limit": ROW_LIMIT,
        }
        result = request_json(
            f"{API_BASE}/payouts/listens/aggregate",
            method="POST",
            body=payload,
            headers=auth_headers(),
        )
        counts[respect_cap] = {r["key"]: r["listen_count"] for r in result.get("rows", [])}
        summaries[respect_cap] = result.get("summary") or {}
    capped, uncapped = counts[True], counts[False]
    excluded = {u: uncapped[u] - capped.get(u, 0) for u in uncapped if uncapped[u] > capped.get(u, 0)}
    # Totals come from the SUMMARY, which the server computes over every group; the row
    # list is clamped to maxLimit (5000) server-side, so deriving totals by summing rows
    # would silently undercount a coach with more listeners than that. Only the
    # per-listener ranking depends on rows - flag when that list was cut short.
    listeners = summaries[False].get("groups")
    return {
        "listeners": listeners if listeners is not None else len(uncapped),
        # Not derivable from the summary (no per-group cap/no-cap comparison is exposed),
        # so this is only a count when the row list was complete - otherwise a lower bound.
        "affected": len(excluded),
        "affected_is_lower_bound": listeners is not None and len(uncapped) < listeners,
        "excluded_total": (summaries[False].get("total_listens", 0)
                           - summaries[True].get("total_listens", 0)),
        "live_no_cap": summaries[False].get("total_listens", 0),
        "rows_truncated": listeners is not None and len(uncapped) < listeners,
        "top": sorted(excluded.items(), key=lambda kv: -kv[1])[:10],
        "uncapped": uncapped,
        "capped": capped,
    }


def burst_coverage(live_no_cap: int, archive_no_cap):
    """Fraction of the month's premium listens still present in `userContentListen`.

    The ~40-day retention prunes mid-month, so a boundary month returns rows for only
    part of it. Those rows are real but the totals are NOT the month's - comparing the
    live no-cap count against the archived one is what distinguishes a partial month
    from a complete one."""
    if not archive_no_cap:
        return None
    return live_no_cap / archive_no_cap


# ------------------------------------------------------------------------ report


def month_range(start: str, end: str):
    sy, sm = (int(p) for p in start.split("-")[:2])
    ey, em = (int(p) for p in end.split("-")[:2])
    if (sy, sm) < EARLIEST:
        raise AuditError(
            f"{start} predates {EARLIEST[0]}-{EARLIEST[1]:02d}; the no-cap archive did not "
            "exist yet and earlier rows are not comparable. Start at 2025-11."
        )
    if (ey, em) < (sy, sm):
        raise AuditError(f"--to {end} is before --from {start}.")
    out, (y, m) = [], (sy, sm)
    while (y, m) <= (ey, em):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def build(uid: str, months):
    since = f"{months[0][0]:04d}-{months[0][1]:02d}"
    until = f"{months[-1][0]:04d}-{months[-1][1]:02d}"
    dashboard = fetch_dashboard(uid, {y for y, _ in months})
    combined = fetch_archive("monthlyCombined", uid, since, until)
    capped = fetch_archive("monthlyListenBreakdown", uid, since, until)
    uncapped = fetch_archive("monthlyListenBreakdownNoCap", uid, since, until)

    rows = []
    for key in months:
        dash = dashboard.get(key) or {}
        comb = combined.get(key) or {}
        cap, nocap = tier_sum(capped.get(key)), tier_sum(uncapped.get(key))
        dash_prem = dash.get("premiumPaidListens")
        entry = {
            "month": f"{key[0]:04d}-{key[1]:02d}",
            "label": MONTH_NAMES[key[1] - 1],
            "dashboard_premium": dash_prem,
            "dashboard_total": dash.get("totalListens"),
            "payout_all_listens": comb.get("listens"),
            "payout_premium_paid": comb.get("premiumPaidListens"),
            "query_with_cap": cap,
            "query_no_cap": nocap,
        }
        entry["excluded_by_cap"] = None if None in (cap, nocap) else nocap - cap
        entry["residual"] = None if None in (nocap, dash_prem) else nocap - dash_prem
        entry["gap"] = (
            None
            if None in (dash_prem, entry["payout_premium_paid"])
            else dash_prem - entry["payout_premium_paid"]
        )
        entry["cap_pct"] = (
            None
            if not nocap or entry["excluded_by_cap"] is None
            else round(entry["excluded_by_cap"] / nocap * 100, 2)
        )
        rows.append(entry)
    return rows


def render(rows, coach_name: str, uid: str) -> str:
    labels = [r["label"] for r in rows]
    spec = [
        ("Dashboard - Premium", "dashboard_premium"),
        ("Dashboard - Total", "dashboard_total"),
        ("Payout - All listens", "payout_all_listens"),
        ("Payout - Premium Paid Listens", "payout_premium_paid"),
        ("Query used for payout", "query_with_cap"),
        ("Same query (no 6/day cap)", "query_no_cap"),
        ("**Excluded by 6/day cap**", "excluded_by_cap"),
        ("Residual (no-cap - dashboard)", "residual"),
        ("**Gap the coach sees**", "gap"),
    ]
    fmt = lambda v: "-" if v is None else f"{v:,}"
    out = [
        f"**Payout audit - {coach_name}** — coach id `{uid}`",
        "",
        "| | " + " | ".join(labels) + " |",
        "|---|" + "---:|" * len(labels),
    ]
    for label, key in spec:
        out.append(f"| {label} | " + " | ".join(fmt(r[key]) for r in rows) + " |")
    out.append(
        "| Cap as % of no-cap premium | "
        + " | ".join("-" if r["cap_pct"] is None else f"{r['cap_pct']:.2f}%" for r in rows)
        + " |"
    )
    bad = [r["month"] for r in rows if None not in (r["gap"], r["excluded_by_cap"], r["residual"])
           and r["gap"] != r["excluded_by_cap"] - r["residual"]]
    out += ["", "_Identity `gap == excluded_by_cap - residual` holds"
            + (" for every month._" if not bad else f", EXCEPT {', '.join(bad)} - investigate._")]
    missing = [r["month"] for r in rows if r["query_no_cap"] is None]
    if missing:
        out.append(f"_No archived payout rows for: {', '.join(missing)}._")
    return "\n".join(out)


def render_bursts(bursts: dict, month: str) -> str:
    total = bursts["excluded_total"]
    coverage = bursts.get("coverage")
    partial = coverage is not None and coverage < 0.99
    unknown = coverage is None
    if not total:
        if partial:
            return (f"**{month} bursts** - PARTIAL MONTH: only ~{coverage * 100:.0f}% of "
                    "listens are still retained, and none of those hit the cap. "
                    "Not a month-level result.")
        return f"**{month} bursts** - no listens excluded by the cap."
    lines = [f"**{month} - who drove the cap**", ""]
    if unknown:
        lines += ["> **Coverage unverified** - no archived no-cap total for this month, so "
                  "there is nothing to check the retained listens against. Treat these as "
                  "a lower bound until the archive row exists.", ""]
    if bursts.get("rows_truncated"):
        lines += [f"> **Listener list truncated** - the endpoint returned "
                  f"{len(bursts['uncapped']):,} of {bursts['listeners']:,} listeners "
                  "(server caps rows at " + str(ROW_LIMIT) + "). The excluded-listens total and the listener "
                  "count are exact (server summary), but the **number of listeners hit by "
                  "the cap is a lower bound** and the ranking below may omit listeners.", ""]
    if partial:
        lines += [
            f"> **PARTIAL MONTH - do not quote these as {month} totals.** Only "
            f"{bursts['live_no_cap']:,} of the month's premium listens "
            f"(~{coverage * 100:.0f}%) are still inside the ~40-day retention window; "
            "the rest aged out. Ranking is indicative, totals are not. Use the "
            "backup-table SQL in references/method.md for the full month.",
            "",
        ]
    lower = bursts.get("affected_is_lower_bound")
    lines += [
        f"{total:,} listens excluded across {'at least ' if lower else ''}"
        f"{bursts['affected']} of {bursts['listeners']:,} listeners"
        + (" (retained portion only)." if partial else "."),
        "",
        "| listener | excluded | no-cap | paid | cum % |",
        "|---|---:|---:|---:|---:|",
    ]
    running = 0
    for uid, count in bursts["top"]:
        running += count
        lines.append(
            f"| `{uid[:UID_PREFIX]}...` | {count} | {bursts['uncapped'].get(uid, 0)} | "
            f"{bursts['capped'].get(uid, 0)} | {running / total * 100:.1f}% |"
        )
    return "\n".join(lines)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coach", required=True, help="Firebase UID or coach display name")
    parser.add_argument("--from", dest="since", required=True, metavar="YYYY-MM")
    parser.add_argument("--to", dest="until", required=True, metavar="YYYY-MM")
    parser.add_argument("--bursts", action="store_true",
                        help="per-listener cap breakdown (recent months only)")
    parser.add_argument("--json", action="store_true", help="emit raw JSON instead of markdown")
    args = parser.parse_args()

    try:
        months = month_range(args.since, args.until)
        uid, name = resolve_coach(args.coach)
        rows = build(uid, months)
        bursts = {}
        if args.bursts:
            by_month = {r["month"]: r for r in rows}
            for year, month in months:
                label = f"{year:04d}-{month:02d}"
                data = fetch_bursts(uid, year, month)
                # userContentListen keeps ~40 days. An older month comes back empty; a
                # BOUNDARY month comes back partial - real rows for only part of it.
                if not data["listeners"]:
                    bursts[label] = None
                    continue
                data["coverage"] = burst_coverage(
                    data["live_no_cap"], (by_month.get(label) or {}).get("query_no_cap")
                )
                bursts[label] = data
    except AuditError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)

    if args.json:
        # Allow-list the burst fields and redact listener ids to a prefix - the JSON
        # path is the one that gets persisted or pasted, so it must not be the leak.
        def safe(entry):
            if not entry:
                return None
            out = {k: entry[k] for k in
                   ("listeners", "affected", "excluded_total", "coverage",
                    "rows_truncated", "affected_is_lower_bound") if k in entry}
            out["top"] = [{"listenerPrefix": u[:UID_PREFIX], "excluded": n}
                          for u, n in entry.get("top", [])]
            return out

        print(json.dumps({"coachId": uid, "coachName": name, "months": rows,
                          "bursts": {k: safe(v) for k, v in bursts.items()}}, indent=2))
        return

    print(render(rows, name, uid))
    for label, data in bursts.items():
        print()
        if data is None:
            print(f"**{label} bursts** - raw listens aged out of `userContentListen` "
                  "(~40-day window); use the backup-table SQL in references/.")
        else:
            print(render_bursts(data, label))


if __name__ == "__main__":
    main()
