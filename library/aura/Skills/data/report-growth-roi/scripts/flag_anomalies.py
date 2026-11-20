#!/usr/bin/env python3
"""Rank weekly Growth ROI metrics by how far the latest reading sits outside its own band.

Turns "what feels normal to us and what doesn't" into something reproducible. Given a
metric's trailing weekly series, it reports the latest filled value, which week that value
belongs to, the week-over-week delta, the band built from the preceding weeks, a z-score,
a same-direction streak, and a verdict -- sorted so the note's agenda falls out of the top
of the table.

Three behaviours matter more than the statistics:

* **`window-high` / `window-low` mean highest or lowest in the band, not a record.** The
  band is the trailing `BAND_WINDOW` weeks, so a verdict says only that the reading tops
  its own recent history. Verify any "record" or "all year" claim against the full column
  before it reaches a note -- a blended CAC that this script called `window-high` was third
  highest in the year, behind two weeks four months earlier.

* **The latest filled value is not always the latest week.** The trackers fill cost columns
  a week before conversion and revenue columns, so a metric's newest number can belong to
  `W-1` while its neighbour's belongs to `W`. The `week` column says which, per metric, so
  a note can never silently mix two weeks.
* **A trailing `null` is "not loaded yet", never zero.** Nulls are skipped, not zeroed, and
  never enter a band.

Feed it **eleven** weeks (the target week plus ten): a band needs eight prior weeks, and the
late columns start one or two rows back -- most fill a week late, and Aura's
`Paid -> Cancellation (7D)` columns fill two weeks late -- so a shorter pull leaves them with
seven or fewer and they degrade to `insufficient-history`.

Input JSON (stdin, or --file):

    {
      "app": "aura",
      "weeks":  ["7/5 - 7/11", "...", "8/30 - 9/5"],
      "metrics": {
        "Blended CPT":     [31.5, 31.9, 31.2, 33.9, 25.1, 28.6, 29.3, 31.0, 31.3],
        "Blended CAC":     [78.4, 72.8, 65.2, 74.9, 51.6, 58.0, 62.9, 74.6, null],
        "Web family subs": [56,   43,   50,   50,   73,   70,   62,   33,   null]
      }
    }

Values may be numbers, nulls, or strings the sheet returned ("$31.3", "15.79%", "1,092",
"#DIV/0!"). Strings are coerced; anything uncoercible is treated as null.

Usage:
    python3 flag_anomalies.py --file week.json
    python3 flag_anomalies.py --file week.json --json
    python3 flag_anomalies.py --file week.json --min-history 6 --z 2.0
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from statistics import mean, stdev

# A band needs enough points to mean anything. Below this, report the delta only.
DEFAULT_MIN_HISTORY = 8
# |z| at or above this is out-of-band. 1.5 catches real moves without crying wolf weekly.
DEFAULT_Z = 1.5
# Weeks of history a band is built from. Older weeks describe a different business.
BAND_WINDOW = 12

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def coerce(raw: object) -> float | None:
    """Sheet cell -> float, or None when there is no usable number in it."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or text.startswith("#"):  # "", "#DIV/0!", "#REF!"
        return None
    negative = text.startswith("(") and text.endswith(")")  # accounting negatives
    match = _NUM.search(text.replace(",", ""))
    if match is None:
        return None
    value = float(match.group(0))
    return -value if negative and value > 0 else value


def analyse(
    name: str,
    series: list[object],
    weeks: list[str],
    min_history: int,
    z_threshold: float,
) -> dict | None:
    """One metric -> one row. None when the series holds no usable value at all."""
    filled = [(i, v) for i, v in ((i, coerce(r)) for i, r in enumerate(series)) if v is not None]
    if not filled:
        return None

    idx, latest = filled[-1]
    prior = [v for _, v in filled[:-1]][-BAND_WINDOW:]
    week = weeks[idx] if idx < len(weeks) else f"index {idx}"
    # Weeks behind the newest column, i.e. how stale this metric's newest value is.
    lag = (len(series) - 1) - idx

    row: dict = {
        "metric": name,
        "week": week,
        "lag_weeks": lag,
        "latest": latest,
        "prior": prior[-1] if prior else None,
        "wow_pct": None,
        "band_low": min(prior) if prior else None,
        "band_high": max(prior) if prior else None,
        "mean": None,
        "z": None,
        "streak": 0,
        "verdict": "no-history",
        "history_n": len(prior),
    }

    if prior and prior[-1]:
        row["wow_pct"] = (latest - prior[-1]) / abs(prior[-1]) * 100.0

    # Consecutive same-direction moves ending at the latest reading, latest move included.
    moves = [b - a for a, b in zip([v for _, v in filled], [v for _, v in filled][1:])]
    if moves and moves[-1] != 0:
        sign = 1 if moves[-1] > 0 else -1
        streak = 0
        for move in reversed(moves):
            if move == 0 or (1 if move > 0 else -1) != sign:
                break
            streak += 1
        row["streak"] = streak * sign

    if len(prior) < min_history:
        row["verdict"] = "insufficient-history"
        return row

    row["mean"] = mean(prior)
    spread = stdev(prior) if len(prior) > 1 else 0.0
    row["z"] = (latest - row["mean"]) / spread if spread else 0.0

    if latest > row["band_high"]:
        row["verdict"] = "window-high"
    elif latest < row["band_low"]:
        row["verdict"] = "window-low"
    elif row["z"] >= z_threshold:
        row["verdict"] = "high"
    elif row["z"] <= -z_threshold:
        row["verdict"] = "low"
    else:
        row["verdict"] = "in-band"
    return row


# Records first, then by distance from the mean. Ties broken by streak length.
_RANK = {"window-high": 0, "window-low": 0, "high": 1, "low": 1,
         "in-band": 2, "insufficient-history": 3, "no-history": 4}


def sort_key(row: dict) -> tuple:
    return (_RANK.get(row["verdict"], 9), -abs(row["z"] or 0.0), -abs(row["streak"]))


def fmt(value: float | None, places: int = 1) -> str:
    return "-" if value is None else f"{value:,.{places}f}"


def render(rows: list[dict], app: str, newest_week: str) -> str:
    out = [
        f"Growth ROI anomaly scan - {app}",
        f"Newest week column: {newest_week}",
        "",
        "| Metric | Week | Latest | Prior | WoW % | Band (n) | z | Streak | Verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        band = (
            f"{fmt(r['band_low'])} - {fmt(r['band_high'])} ({r['history_n']})"
            if r["band_low"] is not None
            else f"- ({r['history_n']})"
        )
        streak = f"{r['streak']:+d}" if r["streak"] else "-"
        stale = " *" if r["lag_weeks"] else ""
        out.append(
            f"| {r['metric']} | {r['week']}{stale} | {fmt(r['latest'])} | {fmt(r['prior'])} "
            f"| {fmt(r['wow_pct'])} | {band} | {fmt(r['z'], 2)} | {streak} | {r['verdict']} |"
        )

    stale = [r for r in rows if r["lag_weeks"]]
    if stale:
        out += [
            "",
            f"* {len(stale)} metric(s) have not filled for the newest week yet - their "
            "latest value belongs to an earlier week, marked above. Label the week when "
            "reporting them; a blank is not a zero.",
        ]

    flagged = [r for r in rows if _RANK.get(r["verdict"], 9) <= 1]
    out += [
        "",
        f"{len(flagged)} of {len(rows)} metric(s) are outside their band. "
        "Everything else is normal for this metric and does not earn a bullet.",
    ]
    thin = [r for r in rows if r["verdict"] == "insufficient-history"]
    if thin:
        out.append(
            f"{len(thin)} metric(s) lack the history for a band - report the delta only "
            "and say the band is not established."
        )
    return "\n".join(out)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--file", help="input JSON path; omit to read stdin")
    parser.add_argument("--min-history", type=int, default=DEFAULT_MIN_HISTORY,
                        help=f"prior weeks required for a band (default {DEFAULT_MIN_HISTORY})")
    parser.add_argument("--z", type=float, default=DEFAULT_Z,
                        help=f"|z| at or above which a reading is out of band (default {DEFAULT_Z})")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = parser.parse_args()

    try:
        raw = open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"could not read input JSON: {exc}")

    metrics = payload.get("metrics") or {}
    if not isinstance(metrics, dict) or not metrics:
        sys.exit("input JSON needs a non-empty 'metrics' object")
    weeks = payload.get("weeks") or []
    app = payload.get("app", "unknown")

    lengths = {len(s) for s in metrics.values() if isinstance(s, list)}
    if weeks and lengths and max(lengths) > len(weeks):
        sys.exit(
            f"'weeks' has {len(weeks)} labels but a metric series has {max(lengths)} values "
            "- they must line up, or the week attributed to each reading will be wrong"
        )

    rows = []
    for name, series in metrics.items():
        if not isinstance(series, list):
            continue
        row = analyse(name, series, weeks, args.min_history, args.z)
        if row is not None:
            rows.append(row)
    if not rows:
        sys.exit("no metric held a usable value")
    rows.sort(key=sort_key)

    if args.json:
        print(json.dumps({"app": app, "rows": rows}, indent=2))
    else:
        print(render(rows, app, weeks[-1] if weeks else "unknown"))


if __name__ == "__main__":
    main()
