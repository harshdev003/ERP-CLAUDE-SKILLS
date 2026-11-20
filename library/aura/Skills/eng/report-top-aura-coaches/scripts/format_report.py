#!/usr/bin/env python3
"""
Format the PaymentService `POST /payouts/listens/aggregate` response into a
condensed, Slack-ready top-N coach earnings report.

Sister skill to `analyze-aura-cost-per-coach` — same data, different shape:

- Defaults to **yesterday Pacific** (vs MTD for the deep-dive sibling)
- Top-N table only (no per-row tier breakdown lines, no window-wide tier
  table, no distribution stats) — Slack-sized
- Auto-detected outliers paragraph below the table — names concentration,
  tier-mix bias, and other patterns worth flagging

Reads the `--group-by coachId` payload written by `call_aggregate.py`. The
upstream contract (rows + summary, cost_cents, tier_breakdown, etc.) is the
same as the deep-dive sibling; only the rendering differs.

Coach IDs are public-facing creator identifiers but still truncated to
`first8…` for consistency with the rest of the family. Full IDs preserved
in --json output.

Usage:
    python3 format_report.py \\
        --input /tmp/aura_top_coaches.json \\
        --label "yesterday Pacific (2026-05-18)"
    python3 format_report.py --input ... --label ... --top 5
    python3 format_report.py --input ... --label ... --json /tmp/full.json

Stdlib only — no third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import sys


DEFAULT_TOP_N = 10

# Tier ordering for outlier detection — high-tier earnings ($ per listen) on
# the right, so "binge" tilt shows when ≥5000 dominates a coach's mix.
TIER_ORDER = ("lt500", "lt2000", "lt5000", "other", "music")
TIER_LABELS = {
    "lt500":  "<500",
    "lt2000": "500–1999",
    "lt5000": "2000–4999",
    "other":  "≥5000",
    "music":  "music",
}

# Output field whitelist for --json output. The endpoint may, in some
# configurations or future revisions, include PII fields (`givenName`,
# `familyName`, `email`) joined from the user/coach tables. Coach IDs are
# public-facing but still treated as sensitive for consistency.
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
    return {k: row[k] for k in _SAFE_ROW_FIELDS if k in row}


def _short_id(coach_id: str) -> str:
    """First 8 chars + ellipsis. Matches the per-coach sibling's _short_uid."""
    if len(coach_id) <= 8:
        return coach_id + "…"
    return coach_id[:8] + "…"


def _fmt_cents(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def render_headline(rows: list[dict], summary: dict, label: str) -> str:
    total_listens = int(summary.get("total_listens", 0))
    total_cost_cents = int(summary.get("total_cost_cents", 0))
    groups = int(summary.get("groups", 0))

    if not rows:
        if total_listens == 0:
            return f"**Aura coach earnings — {label}**\n\nNo qualifying listens in this window."
        return (
            f"**Aura coach earnings — {label}**\n\n"
            f"{total_listens:,} qualifying listens · {_fmt_cents(total_cost_cents)} total payout · "
            f"{groups:,} coaches."
        )

    top = rows[0]
    top_cents = int(top.get("cost_cents", 0))

    # Headline multiplier: top coach vs the population mean (mean from full-set
    # summary; the rows are top-N only so any row-based percentile would be a
    # biased subset). Matches the per-coach deep-dive sibling's headline logic.
    pop_mean_cents = (total_cost_cents / groups) if groups else 0
    nx = (top_cents / pop_mean_cents) if pop_mean_cents else 0
    nx_str = f"{nx:,.0f}×" if nx >= 1 else (f"{nx:,.1f}×" if nx else "—")
    share_pct = (top_cents / total_cost_cents * 100) if total_cost_cents else 0

    return (
        f"**Aura coach earnings — {label}**\n\n"
        f"{total_listens:,} qualifying listens · {_fmt_cents(total_cost_cents)} total payout · "
        f"{groups:,} coaches.\n"
        f"Top coach `{_short_id(top['key'])}` — {top['listen_count']:,} listens "
        f"({_fmt_cents(top_cents)}, {share_pct:.1f}% of total payout, "
        f"{nx_str} the average coach)."
    )


def render_table(rows: list[dict], summary_cost_cents: int) -> str:
    if not rows:
        return "No qualifying listens in this window."

    out = [
        "| Rank | Coach | Listens | Earned | Share | Cum. share |",
        "| ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    cum = 0.0
    for i, r in enumerate(rows, start=1):
        cents = int(r.get("cost_cents", 0))
        share = (cents / summary_cost_cents * 100) if summary_cost_cents else 0
        cum += share
        out.append(
            f"| {i} | `{_short_id(r['key'])}` | {r['listen_count']:,} | "
            f"{_fmt_cents(cents)} | {share:.1f}% | {cum:.1f}% |"
        )
    return "\n".join(out)


def render_outliers(rows: list[dict], summary: dict) -> str:
    """Auto-detected patterns worth flagging in the daily report.

    Each detector is a short, opinionated check that fires only when the
    threshold is meaningful. Returns an empty string when nothing fires —
    don't render a paragraph of noise on a normal day.
    """
    total_cost_cents = int(summary.get("total_cost_cents", 0))
    groups = int(summary.get("groups", 0))
    if not rows or not total_cost_cents:
        return ""

    notes: list[str] = []

    # 1) Concentration — top-5 cumulative share above 25% is worth calling out.
    top5_cents = sum(int(r.get("cost_cents", 0)) for r in rows[:5])
    top5_share = (top5_cents / total_cost_cents * 100) if total_cost_cents else 0
    if top5_share >= 25:
        notes.append(
            f"Top 5 coaches make {top5_share:.1f}% of total payout — concentrated at the top."
        )

    # 2) Top coach dominance — if top coach is >5× the average coach, flag it.
    pop_mean_cents = (total_cost_cents / groups) if groups else 0
    top_cents = int(rows[0].get("cost_cents", 0))
    if pop_mean_cents and (top_cents / pop_mean_cents) >= 5:
        nx = top_cents / pop_mean_cents
        notes.append(
            f"Top coach earned {nx:,.0f}× the average coach — power-law tail."
        )

    # 3) Tier-mix bias — if the top coach's earnings are >70% from a single
    # non-music tier, that's an interesting "binge a hit" or "long tail" story.
    top_tiers = rows[0].get("tier_breakdown") or {}
    tier_max_cents = 0
    tier_max_name = None
    for tier in TIER_ORDER:
        bucket = top_tiers.get(tier) or {}
        cents = int(bucket.get("cents", 0))
        if cents > tier_max_cents:
            tier_max_cents = cents
            tier_max_name = tier
    if tier_max_name and top_cents:
        bias = tier_max_cents / top_cents * 100
        if bias >= 70 and tier_max_name != "music":
            notes.append(
                f"Top coach's earnings are {bias:.0f}% from the `{TIER_LABELS[tier_max_name]}` tier — "
                f"{'binge listening on a hit' if tier_max_name == 'other' else 'concentrated in one popularity band'}."
            )

    # 4) Music-heavy day at the window level — if music is >10% of total cost
    # it's worth noting (music tier is 5¢ vs 6-17¢ for non-music, so a high
    # music share signals lots of low-rate listening).
    summary_tier = summary.get("tier_breakdown") or {}
    music_cents = int((summary_tier.get("music") or {}).get("cents", 0))
    music_share = (music_cents / total_cost_cents * 100) if total_cost_cents else 0
    if music_share >= 10:
        notes.append(
            f"Music/soundscape was {music_share:.1f}% of payout — high low-rate share."
        )

    if not notes:
        return ""
    return "_" + " ".join(notes) + "_"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--input", required=True, help="Path to JSON file with PaymentService aggregate response (--group-by coachId)")
    p.add_argument("--label", required=True, help="Human label for the window (e.g. 'yesterday Pacific (2026-05-18)')")
    p.add_argument("--top", type=int, default=DEFAULT_TOP_N, help=f"Top-N cutoff (default {DEFAULT_TOP_N})")
    p.add_argument("--no-outliers", action="store_true", help="Suppress the auto-detected outliers paragraph")
    p.add_argument("--no-headline", action="store_true", help="Suppress the one-line headline")
    p.add_argument("--json", dest="json_out", default=None,
                   help="Write the structured payload (full coach IDs) to this path")
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
    top_rows = rows[: args.top]

    if not args.no_headline:
        print(render_headline(rows, summary, args.label))
        print()

    print(f"**Top {min(args.top, len(rows))} coaches by earnings**")
    print()
    print(render_table(top_rows, summary_cost_cents))

    if not args.no_outliers:
        outliers = render_outliers(rows, summary)
        if outliers:
            print()
            print(outliers)

    if args.json_out:
        out_payload = {
            "window": {"label": args.label},
            "summary": summary,
            "top_coaches": [_safe_row(r) for r in top_rows],
            "coaches_all": [_safe_row(r) for r in rows],
        }
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(out_payload, f, indent=2)


if __name__ == "__main__":
    main()
