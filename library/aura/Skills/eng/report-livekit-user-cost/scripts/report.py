#!/usr/bin/env python3
"""
Report LiveKit-path voice cost for ONE specific user.

Reads the per-user daily cost store `aura-main.aurie_cost.aurie_per_user_daily_cost`
(written once per completed UTC day by `eng:store-aurie-user-daily-cost`) and sums
the LiveKit-path vendor columns for one UID over a window. Point-lookup sibling of
`eng:report-openai-user-cost` / `-elevenlabs-` / `-listens-`; all four roll up to
`eng:report-user-costs`.

A day with no rows in the store at all (today, or a store gap) is PENDING, never
$0.00: the store runs up to yesterday, and a missing day is unmeasured, not quiet.
A stored day with no row for this UID is a genuine $0.00.

Env:
  GCP_KEY  service-account JSON (inline or path) with bigquery.jobUser on aura-main.
           Needs google-cloud-bigquery — run with framework python on the host.

Usage:
  python3 report.py <UID>
  python3 report.py <UID> --window last-30d
  python3 report.py <UID> --from 2026-09-01 --to 2026-09-25
  python3 report.py <UID> --no-by-vendor
  python3 report.py <UID> --json out.json

Exit codes: 0 report produced (including all-pending), 2 BigQuery unreachable or the
query failed — the JSON (when requested) is still written with status set, so a
composing caller can name the leg rather than drop it.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from resolve_window import resolve  # noqa: E402

TABLE = "aura-main.aurie_cost.aurie_per_user_daily_cost"

# The LiveKit-path cost columns of the store, and ONLY those. Every other voice_*
# column belongs to another leg or to nobody:
#   voice_11labs_agent         ConvAI agent — counted by eng:report-elevenlabs-user-cost
#   voice_11labs_intelligence  Intelligence Service TTS — booked on the `Other` row only
#   voice_cost                 the sum column — would double-count
# voice_11labs_livekit IS here: the store splits ElevenLabs by calling API key, and the
# ConvAI walk the ElevenLabs leg does only ever sees the keyless ConvAI slice.
# tests/test_livekit_user_cost.py pins this set against the store's schema.
LIVEKIT_COST_COLUMNS = (
    "voice_livekit",
    "voice_livekit_platform",
    "voice_cartesia",
    "voice_fish",
    "voice_inworld",
    "voice_deepgram",
    "voice_11labs_livekit",
)
USAGE_COLUMN = "livekit_minutes"

VENDOR_LABELS = {
    "voice_livekit": "LiveKit agent minutes",
    "voice_livekit_platform": "LiveKit unused subscription",
    "voice_cartesia": "Cartesia TTS",
    "voice_fish": "Fish Audio TTS",
    "voice_inworld": "Inworld TTS",
    "voice_deepgram": "Deepgram STT",
    "voice_11labs_livekit": "ElevenLabs TTS (LiveKit path)",
}


def window_dates(win: dict) -> list[str]:
    """Every UTC date the window touches, at the store's daily grain.

    `to` is exclusive when it lands on midnight, otherwise the partial day counts.
    """
    start = date.fromisoformat(win["from"])
    end = date.fromisoformat(win["to"])
    if not win["to_iso"].endswith("T00:00:00Z"):
        end += timedelta(days=1)
    out, d = [], start
    while d < end:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _iso(v) -> str:
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def fetch(uid: str, first: str, last: str, query) -> tuple[set[str], dict[str, dict]]:
    """Return (dates the store holds in range, this UID's rows by date).

    `query` is bq.query — injected so tests stub the transport at one chokepoint.
    """
    params = {"f": first, "l": last}  # ISO strings; bq's param typing has no DATE
    stored = query(
        f"SELECT DISTINCT date FROM `{TABLE}` WHERE date BETWEEN DATE(@f) AND DATE(@l)",
        params=params,
    )
    cols = ", ".join(LIVEKIT_COST_COLUMNS + (USAGE_COLUMN,))
    rows = query(
        f"SELECT date, {cols} FROM `{TABLE}` "
        f"WHERE user_id = @u AND date BETWEEN DATE(@f) AND DATE(@l)",
        params={**params, "u": uid},
    )
    return {_iso(r["date"]) for r in stored}, {_iso(r["date"]): r for r in rows}


def build_report(uid: str, win: dict, dates: list[str], stored: set[str],
                 rows: dict[str, dict]) -> dict:
    by_vendor = {c: 0.0 for c in LIVEKIT_COST_COLUMNS}
    minutes = 0.0
    by_day = []
    for d in dates:
        if d not in stored:
            by_day.append({"date": d, "status": "pending", "cost_usd": None, "minutes": None})
            continue
        r = rows.get(d) or {}
        day_cost = 0.0
        for c in LIVEKIT_COST_COLUMNS:
            v = float(r.get(c) or 0.0)
            by_vendor[c] += v
            day_cost += v
        day_min = float(r.get(USAGE_COLUMN) or 0.0)
        minutes += day_min
        by_day.append({"date": d, "status": "stored", "cost_usd": round(day_cost, 4),
                       "minutes": round(day_min, 2)})

    pending = [x["date"] for x in by_day if x["status"] == "pending"]
    stored_days = len(dates) - len(pending)
    if stored_days == 0:
        status = "pending"
    elif pending:
        status = "partial"
    else:
        status = "ok"

    notes = []
    if pending:
        notes.append(
            f"{len(pending)} day(s) not yet in the per-user cost store "
            f"({_ranges(pending)}) — shown as pending, not $0. The store runs daily "
            "up to yesterday; LiveKit use on a pending day is not in this figure."
        )
    if not win["from_iso"].endswith("T00:00:00Z"):
        notes.append("The store is daily, so the window's partial first day counts as a whole day.")

    total = sum(by_vendor.values())
    return {
        "user_id": uid,
        "window": win,
        "status": status,
        "total": {
            # None when nothing is measured: a caller must not sum a pending leg as 0.
            "cost_usd": round(total, 4) if stored_days else None,
            "livekit_minutes": round(minutes, 2) if stored_days else None,
            "stored_days": stored_days,
            "pending_days": len(pending),
        },
        "by_vendor": {c: round(v, 4) for c, v in by_vendor.items()},
        "pending_dates": pending,
        "by_day": by_day,
        "notes": notes,
        "source": TABLE,
    }


def unavailable_report(uid: str, win: dict, status: str, reason: str) -> dict:
    return {
        "user_id": uid,
        "window": win,
        "status": status,
        "total": {"cost_usd": None, "livekit_minutes": None, "stored_days": 0,
                  "pending_days": 0},
        "by_vendor": {},
        "pending_dates": [],
        "by_day": [],
        "notes": [f"LiveKit leg {status}: {reason}"],
        "source": TABLE,
    }


def _ranges(dates: list[str]) -> str:
    """Collapse sorted ISO dates into `a → b` runs."""
    runs, start, prev = [], None, None
    for s in dates:
        d = date.fromisoformat(s)
        if prev and d == prev + timedelta(days=1):
            prev = d
            continue
        if start:
            runs.append((start, prev))
        start = prev = d
    if start:
        runs.append((start, prev))
    return ", ".join(a.isoformat() if a == b else f"{a.isoformat()} → {b.isoformat()}"
                     for a, b in runs)


def _money(v: float) -> str:
    return f"${v:,.2f}"


def render_markdown(report: dict, by_vendor: bool = True) -> str:
    uid_short = report["user_id"][:8] + "…"
    label = report["window"]["label"]
    t = report["total"]
    status = report["status"]
    lines: list[str] = []

    if status in ("unavailable", "error"):
        lines.append(f"## LiveKit cost — `{uid_short}` ({label}) — **{status}**")
        lines.append("")
        lines += [f"- {n}" for n in report["notes"]]
        return "\n".join(lines) + "\n"

    if status == "pending":
        lines.append(f"## LiveKit cost — `{uid_short}` ({label}) — **pending**")
        lines.append("")
        lines.append("No day in this window is in the per-user cost store yet.")
        lines.append("")
        lines += [f"- {n}" for n in report["notes"]]
        return "\n".join(lines) + "\n"

    pend = f", {t['pending_days']}d pending" if t["pending_days"] else ""
    lines.append(
        f"## LiveKit cost — `{uid_short}`  ({_money(t['cost_usd'])}, "
        f"{t['livekit_minutes']:,.1f} LiveKit min over {t['stored_days']} stored day"
        f"{'s' if t['stored_days'] != 1 else ''}{pend})"
    )
    lines.append("")
    if t["cost_usd"] == 0 and t["livekit_minutes"] == 0:
        lines.append("No LiveKit voice on any stored day in this window.")
        lines.append("")
    elif by_vendor:
        lines.append("| Vendor | Cost |")
        lines.append("|---|---:|")
        for c in LIVEKIT_COST_COLUMNS:
            v = report["by_vendor"].get(c, 0.0)
            if v:
                lines.append(f"| {VENDOR_LABELS[c]} | {_money(v)} |")
        lines.append("")
    if report["notes"]:
        lines.append("**Notes:**")
        lines += [f"- {n}" for n in report["notes"]]
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("user_id", help="Firebase UID")
    p.add_argument("--window", help="Preset: mtd | prev-month | last-7d | last-30d | wtd | "
                                    "today | yesterday-pt | today-pt")
    p.add_argument("--from", dest="since", help="Custom start (YYYY-MM-DD, UTC)")
    p.add_argument("--to", dest="until", help="Custom end (YYYY-MM-DD, UTC, exclusive)")
    p.add_argument("--by-vendor", dest="by_vendor", action="store_true", default=True,
                   help="Show the per-vendor breakdown (default on)")
    p.add_argument("--no-by-vendor", dest="by_vendor", action="store_false",
                   help="Total only")
    p.add_argument("--json", dest="json_out", help="Write structured report JSON to this path")
    args = p.parse_args()

    win = resolve(args.window, args.since, args.until)
    dates = window_dates(win)
    exit_code = 0
    try:
        import bq
        stored, rows = fetch(args.user_id, dates[0], dates[-1], bq.query)
        report = build_report(args.user_id, win, dates, stored, rows)
    except Exception as e:  # noqa: BLE001 — classified below, never swallowed silently
        kind = type(e).__name__
        status = "unavailable" if kind in ("BQUnavailable", "ImportError",
                                           "ModuleNotFoundError") else "error"
        report = unavailable_report(args.user_id, win, status, f"{kind}: {e}")
        print(f"# LiveKit leg {status}: {kind}: {e}", file=sys.stderr)
        exit_code = 2

    print(render_markdown(report, by_vendor=args.by_vendor))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2, default=str))
        print(f"# Wrote structured report to {args.json_out}", file=sys.stderr)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
