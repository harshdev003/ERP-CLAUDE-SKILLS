#!/usr/bin/env python3
"""
Fetch + aggregate daily/weekly OpenAI spend for ONE project.

Pulls per-day cost buckets from the OpenAI Costs API (`/v1/organization/costs`,
filtered to a single project) and aggregates them into a daily series and a
weekly (Monday-anchored, ISO) rollup. Project-level aggregate $ only — no
per-user, per-key, or per-model breakdown, no UID/PII.

Usage:
    python3 costs.py --project "Insights [Production]" --all
    python3 costs.py --project "Insights [Production]" --last 14
    python3 costs.py --project "Insights" --since 2026-06-01 --until 2026-07-01
    python3 costs.py --project proj_abc123 --all --json /tmp/insights_costs.json

Project matching (against `/v1/organization/projects`, active + archived):
    1. Exact name match (case-insensitive).
    2. Case-insensitive substring match — must be unique, else error listing
       the ambiguous matches so the user can pick.
    3. Exact project_id match (`proj_...`).
    4. No match → error listing all known project names.

Window:
    --since YYYY-MM-DD --until YYYY-MM-DD  (together; UTC, half-open — until
        is exclusive, matching the convention in eng:analyze-openai-costs)
    --last N                               (rolling N days, ending today,
        today's partial day included)
    --all                                  (default — from a safe floor of
        2025-01-01 through today; today's partial day included)

Output: JSON to stdout (and to --json PATH if given):
    {
      "meta": {"project": ..., "project_id": ..., "window": ..., "generated": ...},
      "daily": [{"date": "YYYY-MM-DD", "cost": 12.34}, ...],
      "weekly": [{"week_start": "YYYY-MM-DD", "cost": 137.91, "days": 7, "partial": false}, ...],
      "monthly": [{"month": "YYYY-MM", "cost": 155.60, "days": 31, "partial": false}, ...],
      "total": 317.50
    }

The `daily` series is gap-filled from the first day WITH spend through the
last day in the window (leading all-zero days trimmed) — a project with no
spend anywhere in the window returns empty `daily`/`weekly` and `total: 0`.

Env:
    OPENAI_ADMIN_API_KEY  (Admin API key, sk-admin-...; required) -- used
        against /v1/organization/costs + /v1/organization/projects. No MCP
        fallback exists for these org-level endpoints.
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import sys
import time
from collections import OrderedDict, defaultdict
from datetime import date, datetime, timedelta, timezone

import requests

OPENAI_API = "https://api.openai.com/v1"
COSTS_PATH = "/organization/costs"
PROJECTS_PATH = "/organization/projects"

COSTS_PAGE_SIZE = 180  # /organization/costs accepts up to 180 buckets per page
ALL_FLOOR = date(2025, 1, 1)  # safe floor predating any Aura OpenAI usage


# ---------------------------------------------------------------------------
# OpenAI API helpers
# ---------------------------------------------------------------------------

def _request(path: str, params: dict, api_key: str) -> dict:
    """GET with retry on 429 + 5xx."""
    url = f"{OPENAI_API}{path}"
    headers = {"Authorization": f"Bearer {api_key}"}
    last_err = ""
    for attempt in range(6):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=60)
        except requests.RequestException as e:
            last_err = str(e)
            time.sleep(min(2 ** attempt, 30))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            wait = float(r.headers.get("Retry-After", 2 ** attempt))
            time.sleep(min(wait, 30))
            last_err = f"{r.status_code}: {r.text[:200]}"
            continue
        if not r.ok:
            sys.exit(f"OpenAI {r.status_code} on {path}: {r.text[:300]}")
        return r.json()
    sys.exit(f"OpenAI {path}: gave up after retries — last error: {last_err}")


def fetch_projects(api_key: str) -> dict[str, dict]:
    """Map project_id → {name, status} (active + archived)."""
    projects: dict[str, dict] = {}
    after: str | None = None
    while True:
        params: dict = {"limit": 100, "include_archived": "true"}
        if after:
            params["after"] = after
        data = _request(PROJECTS_PATH, params, api_key)
        for p in data.get("data", []):
            projects[p["id"]] = {
                "name": p.get("name") or p["id"],
                "status": p.get("status", "unknown"),
            }
        if not data.get("has_more"):
            break
        last = data["data"][-1] if data.get("data") else None
        if not last:
            break
        after = last["id"]
    return projects


def fetch_project_cost_buckets(
    start_unix: int, end_unix: int, project_id: str, api_key: str
) -> list[dict]:
    """Return all daily cost buckets for one project in [start, end)."""
    buckets: list[dict] = []
    page: str | None = None
    while True:
        params: dict = {
            "start_time": start_unix,
            "end_time": end_unix,
            "bucket_width": "1d",
            "project_ids": [project_id],
            "limit": COSTS_PAGE_SIZE,
        }
        if page:
            params["page"] = page
        data = _request(COSTS_PATH, params, api_key)
        buckets.extend(data.get("data", []))
        page = data.get("next_page")
        if not page:
            break
    return buckets


# ---------------------------------------------------------------------------
# Project resolution
# ---------------------------------------------------------------------------

def resolve_project(query: str, projects: dict[str, dict]) -> tuple[str, str]:
    """Resolve a user-supplied project query to (project_id, project_name).

    Order: exact name (case-insensitive) -> unique substring (case-insensitive)
    -> exact project_id -> error.
    """
    q_lower = query.lower()

    exact = [(pid, meta["name"]) for pid, meta in projects.items() if meta["name"].lower() == q_lower]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        listing = "\n".join(f"  - {name} ({pid})" for pid, name in exact)
        sys.exit(f"Multiple projects exactly match {query!r} — disambiguate by project_id:\n{listing}")

    substr = [(pid, meta["name"]) for pid, meta in projects.items() if q_lower in meta["name"].lower()]
    if len(substr) == 1:
        return substr[0]
    if len(substr) > 1:
        listing = "\n".join(f"  - {name} ({pid})" for pid, name in substr)
        sys.exit(
            f"Multiple projects match {query!r} — narrow the name or pass the exact "
            f"project_id:\n{listing}"
        )

    if query in projects:
        return query, projects[query]["name"]

    if not projects:
        sys.exit("No projects returned by /v1/organization/projects — check OPENAI_ADMIN_API_KEY.")
    names = "\n".join(f"  - {meta['name']} ({pid})" for pid, meta in sorted(projects.items(), key=lambda kv: kv[1]["name"]))
    sys.exit(f"No project matches {query!r}. Available projects:\n{names}")


# ---------------------------------------------------------------------------
# Window resolution
# ---------------------------------------------------------------------------

def resolve_window(
    since: str | None, until: str | None, last: int | None
) -> tuple[date, date, str]:
    """Return (start_date, end_date_exclusive, label). All dates UTC."""
    today = datetime.now(tz=timezone.utc).date()

    if since or until:
        if not (since and until):
            sys.exit("--since and --until must be provided together")
        if last is not None:
            sys.exit("Pass either --since/--until or --last, not both")
        start_date = _parse_date(since)
        end_date = _parse_date(until)
        label = f"{since} → {until}"
    elif last is not None:
        if last <= 0:
            sys.exit("--last must be a positive integer")
        end_date = today + timedelta(days=1)
        start_date = today - timedelta(days=last - 1)
        label = f"last {last} days"
    else:
        start_date = ALL_FLOOR
        end_date = today + timedelta(days=1)
        label = f"all ({ALL_FLOOR.isoformat()} → today)"

    if start_date >= end_date:
        sys.exit(f"Empty window: {start_date.isoformat()} >= {end_date.isoformat()}")

    return start_date, end_date, label


def _parse_date(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        sys.exit(f"Bad date {s!r} — expected YYYY-MM-DD (UTC)")


def _date_to_unix(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def build_daily_series(
    buckets: list[dict], start_date: date, end_date: date
) -> list[dict]:
    """Gap-filled daily series from the first day WITH spend through the last
    day in [start_date, end_date). Leading all-zero days are trimmed; a
    project with zero spend across the whole window returns []."""
    per_date: dict[date, float] = defaultdict(float)
    for bucket in buckets:
        bdate = datetime.fromtimestamp(bucket["start_time"], tz=timezone.utc).date()
        for row in bucket.get("results", []):
            amount = row.get("amount") or {}
            per_date[bdate] += float(amount.get("value") or 0)

    nonzero_dates = [d for d, v in per_date.items() if v > 0]
    if not nonzero_dates:
        return []

    first_day = min(nonzero_dates)
    last_day = end_date - timedelta(days=1)
    if last_day < first_day:
        return []

    series = []
    d = first_day
    while d <= last_day:
        series.append({"date": d.isoformat(), "cost": round(per_date.get(d, 0.0), 6)})
        d += timedelta(days=1)
    return series


def build_weekly_series(daily_series: list[dict]) -> list[dict]:
    """Monday-anchored ISO weekly rollup of a daily series."""
    weeks: "OrderedDict[date, dict]" = OrderedDict()
    for entry in daily_series:
        d = date.fromisoformat(entry["date"])
        monday = d - timedelta(days=d.weekday())
        w = weeks.setdefault(monday, {"cost": 0.0, "days": 0})
        w["cost"] += entry["cost"]
        w["days"] += 1

    out = []
    for monday, w in sorted(weeks.items()):
        out.append({
            "week_start": monday.isoformat(),
            "cost": round(w["cost"], 6),
            "days": w["days"],
            "partial": w["days"] < 7,
        })
    return out


def build_monthly_series(daily_series: list[dict]) -> list[dict]:
    """Calendar-month rollup of a daily series. `partial` is true when fewer
    than the full calendar month's days are present in the series (either
    because the window clips the month, or leading zero days were trimmed)."""
    months: "OrderedDict[tuple[int, int], dict]" = OrderedDict()
    for entry in daily_series:
        d = date.fromisoformat(entry["date"])
        key = (d.year, d.month)
        m = months.setdefault(key, {"cost": 0.0, "days": 0})
        m["cost"] += entry["cost"]
        m["days"] += 1

    out = []
    for (year, month), m in sorted(months.items()):
        month_len = calendar.monthrange(year, month)[1]
        out.append({
            "month": f"{year:04d}-{month:02d}",
            "cost": round(m["cost"], 6),
            "days": m["days"],
            "partial": m["days"] < month_len,
        })
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="Daily/weekly OpenAI cost for one project")
    ap.add_argument("--project", required=True, help="Project name (exact/substring) or project_id")
    ap.add_argument("--since", help="UTC start date (YYYY-MM-DD). Use with --until.")
    ap.add_argument("--until", help="UTC end date, exclusive (YYYY-MM-DD). Use with --since.")
    ap.add_argument("--last", type=int, help="Rolling last N days (ending today).")
    ap.add_argument("--all", action="store_true", help="All available history (default).")
    ap.add_argument("--json", dest="json_out", help="Write structured JSON to this path.")
    args = ap.parse_args()

    if args.all and (args.since or args.until or args.last is not None):
        sys.exit("Pass only one of --all, --since/--until, or --last")

    api_key = os.environ.get("OPENAI_ADMIN_API_KEY")
    if not api_key:
        sys.exit(
            "Missing OPENAI_ADMIN_API_KEY. Create an Admin API key at "
            "https://platform.openai.com/settings/organization/admin-keys "
            "and add it to ~/.claude/settings.json under the `env` block."
        )

    start_date, end_date, label = resolve_window(args.since, args.until, args.last)

    projects = fetch_projects(api_key)
    project_id, project_name = resolve_project(args.project, projects)

    start_unix = _date_to_unix(start_date)
    end_unix = _date_to_unix(end_date)
    buckets = fetch_project_cost_buckets(start_unix, end_unix, project_id, api_key)

    daily = build_daily_series(buckets, start_date, end_date)
    weekly = build_weekly_series(daily)
    monthly = build_monthly_series(daily)
    total = round(sum(e["cost"] for e in daily), 6)

    payload = {
        "meta": {
            "project": project_name,
            "project_id": project_id,
            "window": {
                "label": label,
                "since": start_date.isoformat(),
                "until": end_date.isoformat(),
            },
            "generated": datetime.now(tz=timezone.utc).isoformat(),
        },
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
        "total": total,
    }

    print(json.dumps(payload, indent=2))

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(payload, f, indent=2)


if __name__ == "__main__":
    main()
