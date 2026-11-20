#!/usr/bin/env python3
"""Format a Mixpanel Insights JSON response into the Aurie-usage report.

Expects the raw JSON returned by ``Run-Query(report_type="insights", ...)`` for
an insights report with three ``unique``-math metrics over a daily line chart:

  A. ``AI Conversation/Quick Process Session Started`` — any Aurie session
  B. ``AI Conversation Session Started``               — conversational only
  C. ``AI Quick Process Session Started``              — Quick Process only

The script does NOT call Mixpanel itself — it reads the JSON the SKILL.md
workflow already fetched via the Mixpanel MCP.

Output is a one-line headline followed by a per-day markdown table with the
7-day average row appended, e.g.::

    **Aurie DAU — last 7 complete days (2026-05-11 → 2026-05-17)**

    ~160 DAU avg · conversational share 93% · Quick Process avg ~26

    | Day | Any Aurie | Conversational | Quick Process |
    | --- | ---: | ---: | ---: |
    | Mon May 11 | 177 | 164 | 27 |
    | ...
    | **Avg** | **160** | **149** | **26** |

Stdlib only — no third-party dependencies.

Usage:
    python3 format_report.py --input /tmp/aurie_usage.json --label "last 7 complete days"
    python3 format_report.py --input /tmp/aurie_usage.json --label "..." --headline-only
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime


ANY_AURIE_LABEL = "Any Aurie"
CONVERSATIONAL_LABEL = "Conversational"
QUICK_PROCESS_LABEL = "Quick Process"

ANY_AURIE_EVENT = "AI Conversation/Quick Process Session Started"
CONVERSATIONAL_EVENT = "AI Conversation Session Started"
QUICK_PROCESS_EVENT = "AI Quick Process Session Started"

EVENT_TO_LABEL = {
    ANY_AURIE_EVENT: ANY_AURIE_LABEL,
    CONVERSATIONAL_EVENT: CONVERSATIONAL_LABEL,
    QUICK_PROCESS_EVENT: QUICK_PROCESS_LABEL,
}

COLUMN_ORDER = [ANY_AURIE_LABEL, CONVERSATIONAL_LABEL, QUICK_PROCESS_LABEL]


def _locate_results(payload: dict) -> dict | None:
    """Find the per-metric results dict regardless of envelope shape.

    The Mixpanel MCP wraps responses as ``{"result": {"results": {...}}}``.
    Older fixtures may pass the inner ``results`` dict directly.
    """
    if not isinstance(payload, dict):
        return None
    if "result" in payload and isinstance(payload["result"], dict):
        inner = payload["result"]
        if "results" in inner and isinstance(inner["results"], dict):
            return inner["results"]
    if "results" in payload and isinstance(payload["results"], dict):
        return payload["results"]
    return None


def _label_for_metric(metric_key: str) -> str | None:
    """Map a Mixpanel metric key (e.g. ``"A. AI Conversation Session Started [Unique Users]"``)
    back to one of the canonical column labels."""
    for event_name, label in EVENT_TO_LABEL.items():
        if event_name in metric_key:
            return label
    return None


def extract_series(payload: dict) -> dict[str, dict[str, int]]:
    """Return ``{label: {date: count}}`` keyed by canonical column labels.

    Skips any metric whose event name isn't one of the three expected ones, so
    the formatter is forgiving if the upstream query adds extra series.
    """
    results = _locate_results(payload)
    if not isinstance(results, dict):
        return {}

    out: dict[str, dict[str, int]] = {}
    for metric_key, body in results.items():
        label = _label_for_metric(metric_key)
        if label is None:
            continue
        if not isinstance(body, dict):
            continue
        rows = body.get("rows")
        if not isinstance(rows, list):
            continue
        day_map: dict[str, int] = {}
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            day = str(row[0])
            value = _coerce_int(row[1])
            if value is None:
                continue
            day_map[day] = value
        out[label] = day_map
    return out


def _coerce_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None


def _all_days(series: dict[str, dict[str, int]]) -> list[str]:
    days: set[str] = set()
    for day_map in series.values():
        days.update(day_map.keys())
    return sorted(days)


def detect_partial_day(series: dict[str, dict[str, int]]) -> str | None:
    """Return the trailing day if it looks partial, else None.

    A day is "likely partial" if its values are noticeably below the prior days'
    mean across at least two columns. Centralised so the headline and table can
    both consult and skip it when averaging.
    """
    days = _all_days(series)
    if len(days) < 3:
        return None
    last = days[-1]
    return last if _looks_partial(last, series) else None


def _format_day_label(day: str) -> str:
    try:
        dt = datetime.strptime(day, "%Y-%m-%d")
        return dt.strftime("%a %b %d")
    except ValueError:
        return day


def render_headline(
    series: dict[str, dict[str, int]],
    label: str,
    days_in: int,
    partial_day: str | None = None,
) -> str:
    any_avg = _avg_complete(series.get(ANY_AURIE_LABEL, {}), partial_day)
    conv_avg = _avg_complete(series.get(CONVERSATIONAL_LABEL, {}), partial_day)
    qp_avg = _avg_complete(series.get(QUICK_PROCESS_LABEL, {}), partial_day)

    if any_avg == 0 and conv_avg == 0 and qp_avg == 0:
        return f"**Aurie DAU — {label}**\n\nNo Aurie sessions in this window."

    share_text = ""
    if any_avg > 0:
        share = conv_avg / any_avg * 100
        share_text = f" · conversational share {share:.0f}%"

    qp_text = f" · Quick Process avg ~{qp_avg:,.0f}" if qp_avg else ""

    return (
        f"**Aurie DAU — {label}**\n\n"
        f"~{any_avg:,.0f} DAU avg{share_text}{qp_text}"
    )


def _avg_complete(day_map: dict[str, int], partial_day: str | None = None) -> float:
    """Average per-day value, excluding ``partial_day`` if provided."""
    if not day_map:
        return 0.0
    values = [v for d, v in day_map.items() if d != partial_day]
    if not values:
        return 0.0
    return sum(values) / len(values)


def render_table(series: dict[str, dict[str, int]], partial_day: str | None = None) -> str:
    days = _all_days(series)
    if not days:
        return "No data in this window."

    header = "| Day | " + " | ".join(COLUMN_ORDER) + " |"
    sep = "| --- | " + " | ".join(["---:"] * len(COLUMN_ORDER)) + " |"

    body: list[str] = []
    totals = {col: 0 for col in COLUMN_ORDER}
    counts = {col: 0 for col in COLUMN_ORDER}
    for day in days:
        cells: list[str] = []
        is_partial = day == partial_day
        for col in COLUMN_ORDER:
            v = series.get(col, {}).get(day)
            if v is None:
                cells.append("–")
            else:
                cells.append(f"{v:,}")
                if not is_partial:
                    totals[col] += v
                    counts[col] += 1
        suffix = " *(partial)*" if is_partial else ""
        body.append(f"| {_format_day_label(day)}{suffix} | " + " | ".join(cells) + " |")

    avg_cells = []
    for col in COLUMN_ORDER:
        n = counts[col]
        if n == 0:
            avg_cells.append("–")
        else:
            avg_cells.append(f"**{round(totals[col] / n):,}**")
    avg_row = "| **Avg** | " + " | ".join(avg_cells) + " |"

    lines = [header, sep, *body, avg_row]
    if partial_day is not None:
        lines.append("")
        lines.append(f"*Partial day ({partial_day}) excluded from Avg.*")
    return "\n".join(lines)


def _looks_partial(day: str, series: dict[str, dict[str, int]]) -> bool:
    """Heuristic — only used for the cosmetic *(partial)* suffix.

    The SKILL.md default window is ``last-7d-complete`` so this branch is rare;
    keep the rule simple. A trailing day is "likely partial" if its values are
    below ~60% of the mean of the prior days for the same series.
    """
    flagged = 0
    checked = 0
    for col in COLUMN_ORDER:
        day_map = series.get(col, {})
        if day not in day_map:
            continue
        prior = [v for d, v in day_map.items() if d != day]
        if len(prior) < 2:
            continue
        mean_prior = sum(prior) / len(prior)
        if mean_prior <= 0:
            continue
        checked += 1
        if day_map[day] < 0.6 * mean_prior:
            flagged += 1
    return checked >= 2 and flagged >= 2


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", required=True, help="Path to the JSON file with Run-Query output")
    p.add_argument("--label", required=True, help="Human label for the window (from resolve_window.py)")
    p.add_argument("--days-in", type=int, default=0, help="Days elapsed in the window (informational only)")
    p.add_argument("--headline-only", action="store_true", help="Print just the headline (for compact Slack posts)")
    args = p.parse_args()

    try:
        with open(args.input) as f:
            payload = json.load(f)
    except FileNotFoundError:
        sys.exit(f"Input file not found: {args.input}")
    except json.JSONDecodeError as e:
        sys.exit(f"Failed to parse {args.input} as JSON: {e}")

    series = extract_series(payload)
    partial_day = detect_partial_day(series)

    print(render_headline(series, args.label, args.days_in, partial_day))

    if not args.headline_only and _all_days(series):
        print()
        print(render_table(series, partial_day))


if __name__ == "__main__":
    main()
