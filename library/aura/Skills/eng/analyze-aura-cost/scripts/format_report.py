#!/usr/bin/env python3
"""
Format the PaymentService `POST /payouts/listens/aggregate` response into the
aggregate analyze-aura-cost report.

Reads the summary-only response (groupBy=null) and renders:
- Headline: total qualifying listens, coach payout, distinct users, run-rate
- Cost breakdown by tier (lt500 / lt2000 / lt5000 / other / music)
- Exclusion footnote (daily cap / premium / content type)

Optionally with --by-day, accepts a directory of per-day response JSON files
and renders a daily time series in addition to the window-wide aggregate.

This skill is aggregate-only: no UIDs, no per-user data, no PII. Safe for
public channels.

Usage:
    # Window aggregate from a single summary-only response file
    python3 format_report.py \\
        --input /tmp/aura_cost.json \\
        --label "month to date (May 2026)" --days-in 19

    # Time series: --by-day points to a directory of YYYY-MM-DD.json files
    python3 format_report.py \\
        --input /tmp/aura_cost.json \\
        --label "last 7 days" --days-in 7 \\
        --by-day-dir /tmp/aura_cost_daily/

    # Structured payload for downstream use
    python3 format_report.py --input ... --label ... --days-in 7 \\
        --json /tmp/aura_cost_summary.json

Stdlib only — no third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import sys


# Tier display order — matches the endpoint's tier_breakdown keys.
TIER_ORDER = ("lt500", "lt2000", "lt5000", "other", "music")
TIER_LABELS = {
    "lt500":  "<500",
    "lt2000": "500–1999",
    "lt5000": "2000–4999",
    "other":  "≥5000",
    "music":  "music",
}


def _fmt_cents(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def render_headline(summary: dict, label: str, days_in: int) -> str:
    total_listens = int(summary.get("total_listens", 0))
    total_cost_cents = int(summary.get("total_cost_cents", 0))
    groups = int(summary.get("groups", 0))

    if total_listens == 0:
        return f"**Aura listen cost — {label}**\n\nNo qualifying listens in this window."

    run_rate_cents = total_cost_cents / max(1, days_in)
    avg_per_user_cents = (total_cost_cents / groups) if groups else 0

    head = (
        f"**Aura listen cost — {label}**\n\n"
        f"{total_listens:,} qualifying listens · {_fmt_cents(total_cost_cents)} coach payout · "
        f"{groups:,} users · {_fmt_cents(run_rate_cents)}/day run rate · "
        f"{_fmt_cents(avg_per_user_cents)}/user average."
    )

    excluded = []
    cap = int(summary.get("excluded_by_daily_cap", 0))
    if cap:
        excluded.append(f"{cap:,} excluded by daily cap")
    prem = int(summary.get("excluded_by_premium", 0))
    if prem:
        excluded.append(f"{prem:,} excluded by premium filter")
    ctype = int(summary.get("excluded_by_content_type", 0))
    if ctype:
        excluded.append(f"{ctype:,} excluded by content type")
    if excluded:
        head += "\n_" + " · ".join(excluded) + "._"

    return head


def render_tier_table(summary: dict) -> str:
    tier_breakdown = summary.get("tier_breakdown") or {}
    total_cost_cents = int(summary.get("total_cost_cents", 0))
    if not tier_breakdown:
        return ""

    out = [
        "| Tier | Listens | Cost | Share |",
        "| --- | ---: | ---: | ---: |",
    ]
    rendered_any = False
    for tier in TIER_ORDER:
        bucket = tier_breakdown.get(tier) or {}
        listens = int(bucket.get("listens", 0))
        cents = int(bucket.get("cents", 0))
        if listens == 0 and cents == 0:
            continue
        share = (cents / total_cost_cents * 100) if total_cost_cents else 0
        out.append(f"| {TIER_LABELS[tier]} | {listens:,} | {_fmt_cents(cents)} | {share:.1f}% |")
        rendered_any = True
    if not rendered_any:
        return ""
    return "\n".join(out)


def render_by_day_table(daily: list[dict]) -> str:
    """`daily` is a list of {date, summary} dicts sorted by date ASC."""
    if not daily:
        return ""

    out = [
        "| Date | Listens | Cost | Users |",
        "| --- | ---: | ---: | ---: |",
    ]
    total_listens = 0
    total_cents = 0
    for entry in daily:
        s = entry["summary"]
        listens = int(s.get("total_listens", 0))
        cents = int(s.get("total_cost_cents", 0))
        users = int(s.get("groups", 0))
        total_listens += listens
        total_cents += cents
        out.append(f"| {entry['date']} | {listens:,} | {_fmt_cents(cents)} | {users:,} |")
    out.append(
        f"| **Total** | **{total_listens:,}** | **{_fmt_cents(total_cents)}** | — |"
    )
    return "\n".join(out)


def load_daily_responses(by_day_dir: str) -> list[dict]:
    """Read one JSON file per day from `by_day_dir`, parse the summary block.

    Filenames must be YYYY-MM-DD.json. Other files are skipped. The summary
    response shape (groupBy=null, rows=[], summary={...}) is expected.
    """
    if not os.path.isdir(by_day_dir):
        sys.exit(f"--by-day-dir not found or not a directory: {by_day_dir}")

    out: list[dict] = []
    for name in sorted(os.listdir(by_day_dir)):
        if not name.endswith(".json"):
            continue
        date_part = name[:-5]
        # Loose validation — only insist on shape YYYY-MM-DD
        if len(date_part) != 10 or date_part[4] != "-" or date_part[7] != "-":
            continue
        path = os.path.join(by_day_dir, name)
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except json.JSONDecodeError as e:
            sys.exit(f"Failed to parse {path} as JSON: {e}")
        summary = payload.get("summary")
        if not isinstance(summary, dict):
            sys.exit(f"{path}: no 'summary' object in payload")
        out.append({"date": date_part, "summary": summary})

    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--input", required=True, help="Path to the JSON file with the window-wide summary-only PaymentService response")
    p.add_argument("--label", required=True, help="Human label for the window (from resolve_window.py)")
    p.add_argument("--days-in", type=int, required=True, help="Days elapsed in the window (used for run-rate computation)")
    p.add_argument("--no-tiers", action="store_true", help="Suppress the cost-by-tier table")
    p.add_argument("--no-headline", action="store_true", help="Suppress the one-line headline")
    p.add_argument(
        "--by-day-dir", default=None,
        help=(
            "Directory containing per-day response JSON files named YYYY-MM-DD.json "
            "(each one a summary-only response from call_aggregate.py for a single day). "
            "Appends a time-series table after the window aggregate."
        ),
    )
    p.add_argument("--json", dest="json_out", default=None,
                   help="Write a structured payload (window + summary + daily series) to this path")
    args = p.parse_args()

    try:
        with open(args.input, encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        sys.exit(f"Input file not found: {args.input}")
    except json.JSONDecodeError as e:
        sys.exit(f"Failed to parse {args.input} as JSON: {e}")

    if not isinstance(payload, dict) or "summary" not in payload:
        sys.exit(
            "Input does not look like a PaymentService /payouts/listens/aggregate response — "
            "expected top-level key 'summary'."
        )

    summary = payload.get("summary") or {}

    daily: list[dict] = []
    if args.by_day_dir:
        daily = load_daily_responses(args.by_day_dir)

    if not args.no_headline:
        print(render_headline(summary, args.label, args.days_in))
        print()

    if not args.no_tiers:
        block = render_tier_table(summary)
        if block:
            print("**Cost breakdown by tier**")
            print()
            print(block)
            print()

    if daily:
        print(f"**Daily breakdown ({len(daily)} days)**")
        print()
        print(render_by_day_table(daily))

    if args.json_out:
        out_payload = {
            "window": {"label": args.label, "days_in": args.days_in},
            "summary": summary,
            "daily": daily,
        }
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(out_payload, f, indent=2)


if __name__ == "__main__":
    main()
