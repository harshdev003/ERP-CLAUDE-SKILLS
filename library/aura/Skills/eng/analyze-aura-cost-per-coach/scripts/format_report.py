#!/usr/bin/env python3
"""
Format the PaymentService `POST /payouts/listens/aggregate` response into the
analyze-aura-cost-per-coach report.

Reads the JSON payload written by `call_aggregate.py` (with `--group-by coachId`)
and renders a top-N markdown table of coach earnings with real payout dollars
— no rate estimation. Coach earnings come from the endpoint's `cost_cents`
field (which is the listener-side cost = the coach-side payout for the same
qualifying listens), using the production payout-rate logic (see PROD-1961).

Coach IDs are public-facing creator identifiers but still treated as sensitive
for consistency with the per-user sibling — rendered markdown truncates IDs to
`first8…`. Full IDs are preserved in --json output for downstream joins.

Usage:
    python3 format_report.py \\
        --input /tmp/aura_per_coach.json \\
        --label "last 7 days (2026-05-12 → 2026-05-19)" \\
        --days-in 7
    python3 format_report.py --input ... --label ... --days-in 7 --top 25
    python3 format_report.py --input ... --label ... --days-in 7 --no-tiers
    python3 format_report.py --input ... --label ... --days-in 7 \\
        --json /tmp/aura_per_coach_full.json

Stdlib only — no third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from typing import Iterable


DEFAULT_TOP_N = 10
DEFAULT_TIERS_VISIBLE_FOR = 3  # default: show per-row tier breakdown for top-3 only

# Tier display order — matches the endpoint's tier_breakdown keys.
TIER_ORDER = ("lt500", "lt2000", "lt5000", "other", "music")
TIER_LABELS = {
    "lt500":  "<500",
    "lt2000": "500–1999",
    "lt5000": "2000–4999",
    "other":  "≥5000",
    "music":  "music",
}


def compute_stats(values: Iterable[int]) -> dict:
    """Distribution stats over a population (not a sample).

    Uses statistics.quantiles(method="inclusive") so percentiles are over the
    full population and p99 <= max. Stddev is sample (n-1); None when n < 2.
    """
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        return {"count": 0}

    out = {
        "count": n,
        "mean": sum(vals) / n,
        "median": statistics.median(vals),
        "max": vals[-1],
        "min": vals[0],
        "stddev": statistics.stdev(vals) if n >= 2 else None,
    }
    if n >= 2:
        qs = statistics.quantiles(vals, n=100, method="inclusive")
        out["p90"] = qs[89]
        out["p95"] = qs[94]
        out["p99"] = qs[98]
    else:
        out["p90"] = out["p95"] = out["p99"] = vals[0]
    return out


def _short_uid(uid: str) -> str:
    """Per Compliance/Privacy: first 8 chars + ellipsis. Never the full UID."""
    if len(uid) <= 8:
        return uid + "…"
    return uid[:8] + "…"


# Allow-list of fields written to --json output. When `groupBy=coachId`, the
# row's `key` is the coach ID (= `content.authorId` server-side). The endpoint
# may, in some configurations or future revisions, include PII fields
# (`givenName`, `familyName`, `email`) joined from `"user"` / `"coach"` tables.
# Whitelisting is defense-in-depth so the JSON output stays clean even if the
# upstream contract changes. Coach IDs themselves are public-facing creator
# identifiers but still treated as sensitive for consistency.
_SAFE_ROW_FIELDS = (
    "key",
    "listen_count",
    "cost_cents",
    "music_listens",
    "non_music_listens",
    "tier_breakdown",
    "distinct_coaches",
    "distinct_content",
)


def _safe_row(row: dict) -> dict:
    """Return a copy of `row` containing only the documented safe fields."""
    return {k: row[k] for k in _SAFE_ROW_FIELDS if k in row}


def _fmt_cents(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def render_table(rows: list[dict], summary_cost_cents: int, tier_visible_for: int) -> str:
    if not rows:
        return "No qualifying listens in this window."

    out = [
        "| Rank | Coach | Listens | Earned | Share | Cum. share |",
        "| ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    cum = 0.0
    for i, r in enumerate(rows, start=1):
        cost_cents = int(r.get("cost_cents", 0))
        share = (cost_cents / summary_cost_cents * 100) if summary_cost_cents else 0.0
        cum += share
        out.append(
            f"| {i} | `{_short_uid(r['key'])}` | {r['listen_count']:,} | "
            f"{_fmt_cents(cost_cents)} | {share:.1f}% | {cum:.1f}% |"
        )
        if i <= tier_visible_for:
            tier_line = _render_tier_inline(r.get("tier_breakdown") or {})
            if tier_line:
                out.append(f"|   | _{tier_line}_ |  |  |  |  |")
    return "\n".join(out)


def _render_tier_inline(tier_breakdown: dict) -> str:
    """One-line summary of a row's tier mix, e.g. '<500: 280 ($16.80) · music: 12 ($0.60)'."""
    parts = []
    for tier in TIER_ORDER:
        bucket = tier_breakdown.get(tier)
        if not bucket:
            continue
        listens = int(bucket.get("listens", 0))
        cents = int(bucket.get("cents", 0))
        if listens == 0:
            continue
        parts.append(f"{TIER_LABELS[tier]}: {listens:,} ({_fmt_cents(cents)})")
    return " · ".join(parts)


def render_stats(stats: dict) -> str:
    if stats.get("count", 0) == 0:
        return ""

    def fmt_cents_or_dash(v):
        return _fmt_cents(v) if isinstance(v, (int, float)) else "—"

    stddev = stats.get("stddev")
    stddev_str = "—" if stddev is None else _fmt_cents(stddev)

    return (
        "| Stat | Earnings per coach |\n"
        "| --- | ---: |\n"
        f"| Coaches | {stats['count']:,} |\n"
        f"| Mean | {fmt_cents_or_dash(stats['mean'])} |\n"
        f"| Median | {fmt_cents_or_dash(stats['median'])} |\n"
        f"| p90 | {fmt_cents_or_dash(stats['p90'])} |\n"
        f"| p95 | {fmt_cents_or_dash(stats['p95'])} |\n"
        f"| p99 | {fmt_cents_or_dash(stats['p99'])} |\n"
        f"| Max | {fmt_cents_or_dash(stats['max'])} |\n"
        f"| Std dev | {stddev_str} |"
    )


def render_summary_tier_table(summary_tier_breakdown: dict, summary_cost_cents: int) -> str:
    if not summary_tier_breakdown:
        return ""
    out = [
        "| Tier | Listens | Cost | Share |",
        "| --- | ---: | ---: | ---: |",
    ]
    for tier in TIER_ORDER:
        bucket = summary_tier_breakdown.get(tier)
        if not bucket:
            continue
        listens = int(bucket.get("listens", 0))
        cents = int(bucket.get("cents", 0))
        share = (cents / summary_cost_cents * 100) if summary_cost_cents else 0
        out.append(f"| {TIER_LABELS[tier]} | {listens:,} | {_fmt_cents(cents)} | {share:.1f}% |")
    if len(out) <= 2:
        return ""
    return "\n".join(out)


def render_headline(
    rows: list[dict],
    stats: dict,
    summary: dict,
    label: str,
) -> str:
    summary_cost_cents = int(summary.get("total_cost_cents", 0))
    total_listens = int(summary.get("total_listens", 0))
    groups = int(summary.get("groups", 0))

    if not rows:
        if total_listens == 0:
            return f"**Aura coach earnings — {label}**\n\nNo qualifying listens in this window."
        return (
            f"**Aura coach earnings — {label}**\n\n"
            f"{total_listens:,} qualifying listens · {_fmt_cents(summary_cost_cents)} total payout · "
            f"{groups:,} coaches."
        )

    top = rows[0]
    top_cents = int(top.get("cost_cents", 0))

    # Compare the top coach to the **population mean across all coaches**
    # (computed from the full-set summary), not to the median of the enumerated
    # rows. The API returns a server-side top-N (default 100, max 5000), so
    # any percentile computed from `rows` is a biased subset that dramatically
    # understates how concentrated earnings are at the top. The mean from
    # `summary.total_cost_cents / summary.groups` is computable across all
    # coaches in the window without enumerating every one, so it's the right
    # denominator for the headline multiplier.
    population_mean_cents = (summary_cost_cents / groups) if groups else 0
    nx = (top_cents / population_mean_cents) if population_mean_cents else 0
    nx_str = f"{nx:,.0f}×" if nx >= 1 else (f"{nx:,.1f}×" if nx else "—")

    share_pct = (top_cents / summary_cost_cents * 100) if summary_cost_cents else 0

    head = (
        f"**Aura coach earnings — {label}**\n\n"
        f"{total_listens:,} qualifying listens · {_fmt_cents(summary_cost_cents)} total payout · "
        f"{groups:,} coaches."
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

    head += (
        f"\n\nTop coach `{_short_uid(top['key'])}` — {top['listen_count']:,} listens "
        f"({_fmt_cents(top_cents)}, {share_pct:.1f}% of total payout, "
        f"{nx_str} the average coach)."
    )
    return head


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--input", required=True, help="Path to the JSON file with the PaymentService aggregate response")
    p.add_argument("--label", required=True, help="Human label for the window (from resolve_window.py)")
    p.add_argument("--days-in", type=int, required=True,
                   help="Days elapsed in the window (kept for parity with sibling skills)")
    p.add_argument("--top", type=int, default=DEFAULT_TOP_N, help=f"Top-N cutoff (default {DEFAULT_TOP_N})")
    p.add_argument("--no-stats", action="store_true", help="Suppress the distribution stats block")
    p.add_argument("--no-headline", action="store_true", help="Suppress the one-line headline")
    p.add_argument("--no-tiers", action="store_true", help="Suppress all tier-breakdown rendering (table + per-row)")
    p.add_argument("--tiers-for", type=int, default=DEFAULT_TIERS_VISIBLE_FOR,
                   help=f"Show per-row tier breakdown for top N rows (default {DEFAULT_TIERS_VISIBLE_FOR})")
    p.add_argument("--json", dest="json_out", default=None,
                   help="Write the structured payload (full UIDs) to this path")
    args = p.parse_args()

    try:
        with open(args.input, encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        sys.exit(f"Input file not found: {args.input}")
    except json.JSONDecodeError as e:
        sys.exit(f"Failed to parse {args.input} as JSON: {e}")

    if not isinstance(payload, dict) or "rows" not in payload or "summary" not in payload:
        sys.exit(
            "Input does not look like a PaymentService /payouts/listens/aggregate response — "
            "expected top-level keys 'rows' and 'summary'."
        )

    rows = payload.get("rows") or []
    summary = payload.get("summary") or {}
    summary_cost_cents = int(summary.get("total_cost_cents", 0))

    # Distribution stats over the full enumerated row set (cost_cents per coach).
    cost_values = [int(r.get("cost_cents", 0)) for r in rows if r.get("cost_cents") is not None]
    stats = compute_stats(cost_values)

    top_rows = rows[: args.top]
    tier_visible_for = 0 if args.no_tiers else args.tiers_for

    if not args.no_headline:
        print(render_headline(rows, stats, summary, args.label))
        print()

    print(f"**Top {min(args.top, len(rows))} coaches by earnings**")
    print()
    print(render_table(top_rows, summary_cost_cents, tier_visible_for))

    if not args.no_tiers:
        summary_tiers = summary.get("tier_breakdown") or {}
        block = render_summary_tier_table(summary_tiers, summary_cost_cents)
        if block:
            print()
            print("**Earnings breakdown by tier (entire window)**")
            print()
            print(block)

    if not args.no_stats and stats.get("count", 0) >= 2:
        print()
        print(
            "**Distribution across the top "
            f"{stats['count']:,} enumerated coaches** "
            "_(server-side `limit`; not the full population — population mean "
            "is in the headline)_"
        )
        print()
        print(render_stats(stats))

    if args.json_out:
        out_payload = {
            "window": {"label": args.label, "days_in": args.days_in},
            "summary": summary,
            "stats_cost_cents": stats,
            "top_coaches": [_safe_row(r) for r in top_rows],
            "coaches_all": [_safe_row(r) for r in rows],
        }
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(out_payload, f, indent=2)


if __name__ == "__main__":
    main()
