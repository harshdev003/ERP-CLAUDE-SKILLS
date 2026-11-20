#!/usr/bin/env python3
"""
Format a Mixpanel Insights JSON response from Run-Query into the listens
report that eng:analyze-listens emits inline.

Expects the raw JSON returned by `Run-Query(report_type="insights", ...)`
with a single metric (`Meditation done single`, total count) broken down by
**two** properties: `Source App` first, then `Browser`. Bucketing is then
done in Python — see `references/breakdown-key.md` for the canonical rule
(confirmed with Yawar 2026-05-13).

The script does NOT call Mixpanel itself — it reads the JSON the SKILL.md
workflow already fetched via the Mixpanel MCP.

Usage:
    python3 format_report.py --input /tmp/listens.json --label "month to date (May 2026)" --days-in 13
    python3 format_report.py --input /tmp/listens.json --label "last 7 days" --days-in 7 --by-day

Stdlib only — no third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict


# Tokens Mixpanel uses to signal "property not set" in a breakdown row.
NOT_SET_TOKENS = frozenset({
    "",
    "$non_property_value",
    "(not set)",
    "undefined",
    "null",
    "none",
    "<none>",
})


# Canonical bucketing rules — confirmed with Yawar 2026-05-13.
# See `references/breakdown-key.md` and `Tools/Mixpanel` → App-surface bucketing.
# Each rule: (source_app_lower, browser_set_or_None_to_match_any, bucket_name).
# `browser_set` value of None means "match any value of Browser".
BUCKET_RULES = [
    ("aurie mobile",  None,  "Aurie (mobile)"),
    ("aurie desktop", None,  "Aurie (web)"),
    ("",              False, "Aura (mobile)"),   # Source App not set + Browser not set
    ("",              True,  "Aura (web)"),      # Source App not set + Browser set
]

# Stable display order — keep Aurie first since most reports lead with its share.
DISPLAY_ORDER = ["Aurie (mobile)", "Aurie (web)", "Aura (mobile)", "Aura (web)", "Other / unknown"]


def is_not_set(value) -> bool:
    if value is None:
        return True
    return str(value).strip().lower() in NOT_SET_TOKENS


def bucket(source_app, browser) -> str:
    sa_norm = "" if is_not_set(source_app) else str(source_app).strip().lower()
    br_set = not is_not_set(browser)

    for rule_sa, rule_br, name in BUCKET_RULES:
        if sa_norm != rule_sa:
            continue
        if rule_br is None or rule_br == br_set:
            return name
    return "Other / unknown"


def extract_totals(payload: dict) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """Return (totals_by_bucket, by_day_by_bucket).

    Walks a two-breakdown Insights series:
        series[metric][source_app_value][browser_value][date] = count

    Also handles defensively:
    - A nested envelope (`{data: {series: ...}}`) — older exports.
    - A pre-bucketed flat fixture (`{by_bucket: {<bucket>: count}}`) for tests.
    - A single-layer series with already-canonical bucket names — fixture path.
    """
    totals: dict[str, int] = defaultdict(int)
    by_day: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    series = _locate_series(payload)
    if not isinstance(series, dict):
        return dict(totals), {k: dict(v) for k, v in by_day.items()}

    for _metric, level1 in series.items():
        if not isinstance(level1, dict):
            continue

        for level1_key, level2 in level1.items():
            if isinstance(level2, dict) and _looks_nested(level2):
                # Two-breakdown case: level1_key is Source App, level2 keyed by Browser
                for br_value, day_or_count in level2.items():
                    b = bucket(level1_key, br_value)
                    _accumulate(day_or_count, b, totals, by_day)
            else:
                # Single-layer fallback: level1_key is already a bucket name (fixture)
                # or a Source App value with no Browser breakdown.
                b = _canonical_or_bucket(level1_key)
                _accumulate(level2, b, totals, by_day)

    return dict(totals), {k: dict(v) for k, v in by_day.items()}


def _looks_nested(d: dict) -> bool:
    """Two-breakdown vs single-breakdown disambiguation.

    Two-breakdown shape:    level2 = {browser_value: {date: count}}      → first value is a dict
    Single-breakdown shape: level2 = {date: count}                       → first value is a number

    Caller passes level2. We're nested iff its first value is itself a dict.
    """
    if not d:
        return False
    sample = next(iter(d.values()))
    return isinstance(sample, dict)


def _canonical_or_bucket(raw) -> str:
    """For fixture / single-breakdown paths: if raw is already a canonical bucket
    name use it; otherwise treat it as a Source App value with Browser unknown."""
    s = str(raw).strip()
    if s in DISPLAY_ORDER:
        return s
    return bucket(raw, None)


def _accumulate(day_or_count, bucket_name: str, totals: dict, by_day: dict) -> None:
    if isinstance(day_or_count, dict):
        for day, count in day_or_count.items():
            n = _coerce_int(count)
            if n is None:
                continue
            totals[bucket_name] += n
            by_day[day][bucket_name] += n
    else:
        n = _coerce_int(day_or_count)
        if n is not None:
            totals[bucket_name] += n


def _locate_series(payload: dict):
    """Find the series dict regardless of envelope shape."""
    if not isinstance(payload, dict):
        return None
    if "series" in payload:
        return payload["series"]
    if "data" in payload and isinstance(payload["data"], dict) and "series" in payload["data"]:
        return payload["data"]["series"]
    if "by_bucket" in payload and isinstance(payload["by_bucket"], dict):
        return {"_flat": payload["by_bucket"]}
    return None


def _coerce_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None


def render_main_table(totals: dict[str, int]) -> str:
    total = sum(totals.values())
    if total == 0:
        return "No listens recorded in this window."

    rows = []
    seen = set()
    for app in DISPLAY_ORDER:
        if app in totals:
            rows.append((app, totals[app]))
            seen.add(app)
    for app, count in sorted(totals.items(), key=lambda kv: -kv[1]):
        if app not in seen:
            rows.append((app, count))

    out = ["| App | Listens | % of total |", "| --- | ---: | ---: |"]
    for app, count in rows:
        pct = (count / total * 100) if total else 0
        out.append(f"| {app} | {count:,} | {pct:.1f}% |")
    out.append(f"| **Total** | **{total:,}** | 100.0% |")
    return "\n".join(out)


def render_by_day_table(by_day: dict[str, dict[str, int]]) -> str:
    if not by_day:
        return ""
    apps = []
    for app in DISPLAY_ORDER:
        if any(app in row for row in by_day.values()):
            apps.append(app)
    for row in by_day.values():
        for app in row:
            if app not in apps:
                apps.append(app)

    header = "| Date | " + " | ".join(apps) + " | Total |"
    sep = "| --- | " + " | ".join(["---:"] * len(apps)) + " | ---: |"
    body = []
    grand = defaultdict(int)
    grand_total = 0
    for day in sorted(by_day.keys()):
        row = by_day[day]
        cells = [f"{row.get(app, 0):,}" for app in apps]
        day_total = sum(row.values())
        for app in apps:
            grand[app] += row.get(app, 0)
        grand_total += day_total
        body.append(f"| {day} | " + " | ".join(cells) + f" | {day_total:,} |")
    footer = "| **Total** | " + " | ".join(f"**{grand[app]:,}**" for app in apps) + f" | **{grand_total:,}** |"

    return "\n".join([header, sep, *body, footer])


def render_headline(totals: dict[str, int], label: str, days_in: int) -> str:
    total = sum(totals.values())
    if total == 0:
        return f"**Listens — {label}**\n\nNo listens recorded."

    run_rate = total / max(1, days_in)
    aurie = totals.get("Aurie (mobile)", 0) + totals.get("Aurie (web)", 0)
    aurie_share = (aurie / total * 100) if total else 0

    return (
        f"**Listens — {label}**\n\n"
        f"{total:,} listens · {run_rate:,.0f}/day run rate · "
        f"Aurie share {aurie_share:.1f}%"
    )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", required=True, help="Path to the JSON file with Run-Query output")
    p.add_argument("--label", required=True, help="Human label for the window (from resolve_window.py)")
    p.add_argument("--days-in", type=int, required=True, help="Days elapsed in the window (for run-rate)")
    p.add_argument("--by-day", action="store_true", help="Append a per-day breakdown table")
    args = p.parse_args()

    try:
        with open(args.input) as f:
            payload = json.load(f)
    except FileNotFoundError:
        sys.exit(f"Input file not found: {args.input}")
    except json.JSONDecodeError as e:
        sys.exit(f"Failed to parse {args.input} as JSON: {e}")

    totals, by_day = extract_totals(payload)

    print(render_headline(totals, args.label, args.days_in))
    print()
    print(render_main_table(totals))

    if args.by_day:
        block = render_by_day_table(by_day)
        if block:
            print()
            print("**By day**")
            print()
            print(block)


if __name__ == "__main__":
    main()
