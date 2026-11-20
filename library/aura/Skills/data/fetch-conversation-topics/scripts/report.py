#!/usr/bin/env python3
"""
Braintrust Topics report for the Aura Health `Conversation` project.

Aggregates server-side classifications (BERTopic) by Task / Sentiment / Issues
to match the Braintrust UI Topics view exactly.

Usage:
    python report.py                                     # last 7 days
    python report.py --days 30
    python report.py --since 2026-04-01T00:00:00Z --until 2026-05-01T00:00:00Z
    python report.py --days 7 --json out.json

Env:
    BRAINTRUST_API_KEY
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

BT_API = "https://api.braintrust.dev"
ORG_NAME = "Aura Health 1"
PROJECT_NAME = "Conversation"
FACETS = ("Task", "Sentiment", "Issues")


def get_project_id(api_key: str) -> str:
    r = requests.get(
        f"{BT_API}/v1/project",
        headers={"Authorization": f"Bearer {api_key}"},
        params={"project_name": PROJECT_NAME, "org_name": ORG_NAME},
        timeout=30,
    )
    r.raise_for_status()
    objs = r.json().get("objects", [])
    if not objs:
        sys.exit(f"No project {PROJECT_NAME!r} in {ORG_NAME!r}")
    return objs[0]["id"]


def btql(api_key: str, query: str) -> list[dict]:
    r = requests.post(
        f"{BT_API}/btql",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"query": query, "fmt": "json"},
        timeout=120,
    )
    if not r.ok:
        sys.exit(f"Braintrust {r.status_code}: {r.text}")
    return r.json().get("data", [])


def topic_counts(
    api_key: str, project_id: str, since_iso: str, until_iso: str | None, facet: str
) -> list[dict]:
    until_clause = f" AND created < '{until_iso}'" if until_iso else ""
    q = f"""
        SELECT classifications.{facet}[0].label AS topic, COUNT(*) AS traces
        FROM project_logs('{project_id}')
        WHERE created >= '{since_iso}'{until_clause}
          AND classifications.{facet} IS NOT NULL
        GROUP BY 1
        ORDER BY 2 DESC
    """.strip()
    return btql(api_key, q)


def print_facet(facet: str, rows: list[dict]) -> None:
    total = sum(r["traces"] for r in rows)
    denom = total or 1  # avoid div-by-zero; report the real total in the header
    print(f"\n{facet}  —  {len(rows)} topics, {total:,} traces")
    print("-" * 80)
    label_w = min(max((len(r["topic"] or "") for r in rows), default=20), 70)
    for r in rows:
        pct = 100 * r["traces"] / denom
        label = (r["topic"] or "(unlabeled)")[:label_w]
        print(f"  {pct:5.1f}%  {r['traces']:>6,}  {label}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # em dashes / arrows fail on cp1252 (Windows)
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="Last N days (ignored if --since set)")
    ap.add_argument("--since", help="ISO timestamp lower bound (e.g. 2026-04-01T00:00:00Z)")
    ap.add_argument("--until", help="ISO timestamp upper bound (exclusive)")
    ap.add_argument("--json", dest="json_out")
    args = ap.parse_args()

    api_key = os.environ.get("BRAINTRUST_API_KEY")
    if not api_key:
        sys.exit("BRAINTRUST_API_KEY not set")

    if args.since:
        since = args.since
        window_label = f"{since} -> {args.until or 'now'}"
    else:
        since = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()
        window_label = f"last {args.days} day(s) (since {since})"

    project_id = get_project_id(api_key)
    print(f"Project: {PROJECT_NAME}  (org: {ORG_NAME})")
    print(f"Window:  {window_label}")

    report = {"since": since, "until": args.until, "facets": {}}
    for facet in FACETS:
        rows = topic_counts(api_key, project_id, since, args.until, facet)
        print_facet(facet, rows)
        report["facets"][facet] = rows

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\nWrote {args.json_out}")


if __name__ == "__main__":
    main()
