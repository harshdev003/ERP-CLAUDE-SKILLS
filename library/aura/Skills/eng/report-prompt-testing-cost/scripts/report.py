#!/usr/bin/env python3
"""Daily DERIVED cost for the `Claude`-named OpenAI prompt-testing API keys.

OpenAI's Costs API reports dollars per project, never per API key, so the
per-key dollars here are derived — see `cost.py` for the derivation and its two
silent failure modes, and `references/derivation.md` for the full method.

Usage:
    python3 report.py                      # last 30 days (default)
    python3 report.py --last 7
    python3 report.py --date 2026-08-21
    python3 report.py --from 2026-08-01 --to 2026-08-21
    python3 report.py --last 7 --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import openai_admin as api  # noqa: E402
from cost import (  # noqa: E402
    RateTable,
    is_claude_key,
    normalize_tier,
    parse_line_item,
    token_fields,
)

DEFAULT_DAYS = 30
KEY_NAME = "claude"

# `/costs` buckets are small; `/usage` buckets are one per day.
COSTS_LIMIT = 180
USAGE_LIMIT = 31


# ---------------------------------------------------------------------------
# Window resolution
# ---------------------------------------------------------------------------

def _midnight_utc(when: datetime) -> datetime:
    return when.replace(hour=0, minute=0, second=0, microsecond=0)


def _parse_date(text: str) -> datetime:
    try:
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise SystemExit(f"Bad date {text!r} — expected YYYY-MM-DD (UTC).")


def resolve_window(args, *, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return a half-open [start, end) UTC window aligned to day boundaries."""
    now = now or datetime.now(timezone.utc)
    today = _midnight_utc(now)

    if args.date:
        start = _parse_date(args.date)
        return start, start + timedelta(days=1)

    if args.since or args.until:
        if not (args.since and args.until):
            raise SystemExit("--from and --to must be given together.")
        start = _parse_date(args.since)
        end = _parse_date(args.until) + timedelta(days=1)  # --to is inclusive
        if end <= start:
            raise SystemExit("--to must be on or after --from.")
        return start, end

    # `or` would swallow --last 0 into the 30-day default instead of rejecting it.
    days = DEFAULT_DAYS if args.last is None else args.last
    if days < 1:
        raise SystemExit("--last must be at least 1.")
    end = today + timedelta(days=1)  # include today's partial bucket
    return end - timedelta(days=days), end


def _day_of(unix_ts: int) -> str:
    return datetime.fromtimestamp(unix_ts, timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

def build_rate_table(start: int, end: int, api_key: str) -> RateTable:
    """Derive $/token from org-wide dollars ÷ org-wide tokens."""
    rates = RateTable()

    for bucket in api.paged(
        api.COSTS_PATH,
        {
            "start_time": start,
            "end_time": end,
            "bucket_width": "1d",
            "group_by": ["line_item"],
            "limit": COSTS_LIMIT,
        },
        api_key,
    ):
        day = _day_of(bucket["start_time"])
        for result in bucket.get("results", []):
            parsed = parse_line_item(result.get("line_item"))
            if not parsed:
                continue
            model, tier, dimension = parsed
            usd = (result.get("amount") or {}).get("value", 0.0) or 0.0
            rates.add_cost(day, model, tier, dimension, usd)

    for bucket in api.paged(
        api.USAGE_COMPLETIONS_PATH,
        {
            "start_time": start,
            "end_time": end,
            "bucket_width": "1d",
            "group_by": ["model", "service_tier"],
            "limit": USAGE_LIMIT,
        },
        api_key,
    ):
        day = _day_of(bucket["start_time"])
        for result in bucket.get("results", []):
            tier = normalize_tier(result.get("service_tier"))
            model = result.get("model")
            for dimension, tokens in token_fields(result).items():
                if tokens:
                    rates.add_tokens(day, model, tier, dimension, tokens)

    return rates


def discover_keys(api_key: str) -> tuple[dict[str, str], dict[str, str]]:
    """Find every `Claude`-named key across the org.

    Returns `(key_id -> project_id, project_id -> project name)` for the
    projects that actually hold one.
    """
    projects = api.fetch_projects(api_key)
    key_to_project: dict[str, str] = {}
    hosting: dict[str, str] = {}

    for project_id, project_name in projects.items():
        for item in api.fetch_project_api_keys(project_id, api_key):
            if is_claude_key(item, KEY_NAME) and item.get("id"):
                key_to_project[item["id"]] = project_id
                hosting[project_id] = project_name

    return key_to_project, hosting


def collect(start: int, end: int, api_key: str) -> dict:
    """Walk the keys' usage and price it. Returns a JSON-serialisable report."""
    rates = build_rate_table(start, end, api_key)
    key_to_project, hosting = discover_keys(api_key)

    per_day: dict[str, dict[str, float]] = defaultdict(
        lambda: {"usd": 0.0, "requests": 0, "input": 0, "cached": 0, "output": 0}
    )
    per_project: dict[str, float] = defaultdict(float)
    per_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {"usd": 0.0, "requests": 0, "output": 0}
    )

    for project_id, project_name in hosting.items():
        for bucket in api.paged(
            api.USAGE_COMPLETIONS_PATH,
            {
                "start_time": start,
                "end_time": end,
                "bucket_width": "1d",
                "group_by": ["api_key_id", "model", "service_tier"],
                "project_ids": [project_id],
                "limit": USAGE_LIMIT,
            },
            api_key,
        ):
            day = _day_of(bucket["start_time"])
            for result in bucket.get("results", []):
                if result.get("api_key_id") not in key_to_project:
                    continue

                model = result.get("model")
                tier = normalize_tier(result.get("service_tier"))
                fields = token_fields(result)
                usd = rates.cost_of(day, model, tier, fields)
                requests_made = int(result.get("num_model_requests") or 0)

                row = per_day[day]
                row["usd"] += usd
                row["requests"] += requests_made
                row["input"] += fields["uncached"] + fields["cached"]
                row["cached"] += fields["cached"]
                row["output"] += fields["output"]

                per_project[project_name] += usd

                label = f"{model} ({tier})"
                model_row = per_model[label]
                model_row["usd"] += usd
                model_row["requests"] += requests_made
                model_row["output"] += fields["output"]

    days = sorted(per_day)
    return {
        "window": {"start": _day_of(start), "end_exclusive": _day_of(end)},
        "key_count": len(key_to_project),
        "project_count": len(hosting),
        "total_usd": sum(per_day[d]["usd"] for d in days),
        "daily": [{"date": d, **per_day[d]} for d in days],
        "by_project": sorted(
            ({"project": n, "usd": v} for n, v in per_project.items()),
            key=lambda r: -r["usd"],
        ),
        "by_model": sorted(
            ({"model_tier": n, **v} for n, v in per_model.items()),
            key=lambda r: -r["usd"],
        ),
        "derivation": {
            "fallback_rate_cells": len(rates.fallbacks),
            "unpriced": sorted("|".join(u) for u in rates.unpriced),
        },
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

DERIVATION_NOTE = (
    "_**Dollars here are DERIVED, not billed.** OpenAI reports cost per *project*, "
    "never per *API key*. Per-`(model, service_tier, dimension)` rates are "
    "back-calculated each day from org-wide `/costs?group_by=line_item` ÷ org-wide "
    "`/usage/completions` token counts, then applied to these keys' own uncached / "
    "cached / cache-write / output tokens. Treat the total as a well-founded "
    "estimate, not an invoice line._"
)


def _pct(part: float, whole: float) -> float:
    return (100.0 * part / whole) if whole else 0.0


def render_markdown(data: dict) -> str:
    window = data["window"]
    days = data["daily"]
    total = data["total_usd"]
    out: list[str] = []

    out.append(
        f"# `Claude` prompt-testing keys — daily derived cost "
        f"({window['start']} → {window['end_exclusive']} UTC, end-exclusive)\n"
    )
    out.append(
        f"**Total derived spend:** ${total:,.2f} across {data['key_count']} "
        f"`Claude`-named key(s) in {data['project_count']} project(s)\n"
    )

    out.append("| Date | Derived $ | Requests | Input tok | Cached % | Output tok | $/1k req |")
    out.append("|---|---:|---:|---:|---:|---:|---:|")
    for row in days:
        if not row["requests"] and not row["usd"]:
            continue
        per_1k = (row["usd"] / row["requests"] * 1000) if row["requests"] else 0.0
        out.append(
            f"| {row['date']} | {row['usd']:,.2f} | {row['requests']:,} | "
            f"{row['input']:,} | {_pct(row['cached'], row['input']):.1f}% | "
            f"{row['output']:,} | {per_1k:,.2f} |"
        )
    out.append(
        f"| **Total** | **{total:,.2f}** | "
        f"**{sum(r['requests'] for r in days):,}** | "
        f"**{sum(r['input'] for r in days):,}** | | "
        f"**{sum(r['output'] for r in days):,}** | |"
    )

    out.append("\n## By project\n")
    out.append("| Project | Derived $ | Share |")
    out.append("|---|---:|---:|")
    for row in data["by_project"]:
        if row["usd"] <= 0:
            continue
        out.append(f"| {row['project']} | {row['usd']:,.2f} | {_pct(row['usd'], total):.1f}% |")

    out.append("\n## By model / tier\n")
    out.append("| Model (tier) | Derived $ | Requests | Output tok | Out/req |")
    out.append("|---|---:|---:|---:|---:|")
    for row in data["by_model"]:
        if row["usd"] <= 0 and not row["requests"]:
            continue
        per_req = int(row["output"] // row["requests"]) if row["requests"] else 0
        out.append(
            f"| `{row['model_tier']}` | {row['usd']:,.2f} | {row['requests']:,} | "
            f"{row['output']:,} | {per_req:,} |"
        )

    out.append("\n" + DERIVATION_NOTE)

    derivation = data["derivation"]
    if derivation["fallback_rate_cells"]:
        out.append(
            f"\n_{derivation['fallback_rate_cells']} (day, model, tier, dimension) cell(s) "
            "were too thin to price on their own and fell back to the window-wide rate "
            "for that model._"
        )
    if derivation["unpriced"]:
        out.append(
            f"\n_⚠️ {len(derivation['unpriced'])} (model, tier, dimension) combination(s) "
            "carried tokens but no derivable rate on either basis, so they contribute "
            "$0.00 to the total — it is an undercount. Unpriced: "
            + ", ".join(f"`{u}`" for u in derivation["unpriced"][:10])
            + "._"
        )

    return "\n".join(out)


# ---------------------------------------------------------------------------

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Daily derived cost for the `Claude` prompt-testing OpenAI API keys."
    )
    parser.add_argument("--last", type=int, help=f"Trailing N days (default {DEFAULT_DAYS}).")
    parser.add_argument("--date", help="A single UTC day, YYYY-MM-DD.")
    parser.add_argument("--from", dest="since", help="Window start, YYYY-MM-DD (inclusive).")
    parser.add_argument("--to", dest="until", help="Window end, YYYY-MM-DD (inclusive).")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown.")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_ADMIN_API_KEY")
    if not api_key:
        raise SystemExit(
            "OPENAI_ADMIN_API_KEY is not set. The /v1/organization/* endpoints this "
            "skill reads have no MCP equivalent and require an Admin key — see "
            "/CLAUDE.md → Token inventory."
        )

    start, end = resolve_window(args)
    data = collect(int(start.timestamp()), int(end.timestamp()), api_key)

    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(render_markdown(data))


if __name__ == "__main__":
    main()
