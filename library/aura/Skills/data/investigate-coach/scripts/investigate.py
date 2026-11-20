#!/usr/bin/env python3
"""
investigate-coach — end-to-end investigation report for one Aura coach.

Pulls catalog + per-coach + per-track detail from the Aura API, computes the
standard fact-sheet (cadence, peer comparison, content composition,
engagement quality, catalog concentration, boost-queue saturation), and prints
a Markdown report to stdout.

Codifies the investigation pattern developed during the Sonic Healer case
(May 2026). See plugins/data/skills/investigate-coach/SKILL.md for the full
contract.

Usage:
    python3 investigate.py --coach "<id-or-name>" [--no-tracks] [--peer-band MIN,MAX]

Env:
    AURA_API_TOKEN  required — bearer token for services.aurahealth.io

Stdlib only — no pip install needed on the runner.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import urllib.error
import urllib.request
import pathlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median
from typing import Any


def _ua_who() -> str:
    """Login name of whoever runs this, for the user agent: AINATIVE_UA_WHO, else the global git email's local part, git user.name, the OS account, or "unknown"."""
    who = re.sub(r"^\d+\+", "", os.environ.get("AINATIVE_UA_WHO", "").strip().partition("@")[0])
    if not who:
        if not hasattr(_ua_who, "found"):
            import getpass, subprocess
            found = ""
            for key in ("user.email", "user.name"):
                try:
                    val = subprocess.run(["git", "config", "--global", key], capture_output=True, encoding="utf-8", errors="replace", timeout=5).stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    val = ""
                found = re.sub(r"^\d+\+", "", val.partition("@")[0]) if key == "user.email" else val
                if found:
                    break
            if not found:
                try:
                    found = getpass.getuser()
                except Exception:
                    found = ""
            _ua_who.found = found
        who = _ua_who.found
    return re.sub(r"[^A-Za-z0-9._-]+", "-", who).strip("-.") or "unknown"


def user_agent() -> str:
    """`AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)` -- labels this skill's calls to RTDB, GCP and the Aura API.

    Same format as gcloud_sa.user_agent(). A label, not proof: anyone can send it.
    """
    skill = pathlib.Path(__file__).resolve().parents[1]
    root = skill.parents[1]
    try:
        plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError):
        plugin = root.name
    try:
        front = (skill / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        version = re.search(r'version:\s*"([^"]+)"', front).group(1)
    except (OSError, IndexError, AttributeError):
        version = "unknown"
    session = os.environ.get("AINATIVE_SESSION_NAME") or (os.environ.get("CLAUDE_CODE_SESSION_ID") or "")[:8]
    session = re.sub(r"[^A-Za-z0-9._-]+", "-", session).strip("-") or "none"
    return f"AuraClaude/{_ua_who()}/{plugin}:{skill.name}@{version} (session {session})"


BASE = "https://services.aurahealth.io"
PAGE_LIMIT = 500
PARALLEL_FETCHES = 16
FETCH_TIMEOUT = 30
UID_RE = re.compile(r"^[A-Za-z0-9]{28}$")
BOOST_THRESHOLD = 200      # score_n graduation threshold; we use listenedCount as upper-bound proxy
NEAR_CAP = 10              # 10/month upload cap (see Products/Aura/Coach Upload Policy)
LIFETIME_THRESHOLD = 100   # cap only applies to coaches with >=THRESHOLD lifetime tracks


# --- Infra -----------------------------------------------------------------

def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(f"ERROR: required env var {name} is not set")
    return val


def _get_json(url: str, token: str, timeout: int = FETCH_TIMEOUT) -> Any:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent()})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _is_podcast(coach: dict) -> bool:
    """True if the coach is a podcast feed.

    `types.podcast` shape differs between listing (int track count) and detail
    (bool). Truthy in either form means podcast.
    """
    types = coach.get("types") or {}
    if not isinstance(types, dict):
        return False
    return bool(types.get("podcast"))


def _fetch_catalog(token: str) -> list[dict]:
    coaches: list[dict] = []
    offset = 0
    while True:
        page = _get_json(f"{BASE}/coaches?limit={PAGE_LIMIT}&offset={offset}", token)
        if not isinstance(page, list) or not page:
            break
        coaches.extend(page)
        if len(page) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
    return coaches


def _fetch_track(track_id: str, token: str) -> dict | None:
    try:
        return _get_json(f"{BASE}/contents/{track_id}", token)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None


# --- Coach resolution ------------------------------------------------------

def _resolve_coach(catalog: list[dict], query: str) -> dict | list[dict]:
    """Return a single coach record, or a list of candidates for the caller
    to disambiguate. Raises SystemExit with a clear message on no match.
    """
    if UID_RE.match(query):
        hits = [c for c in catalog if c.get("id") == query]
        if not hits:
            sys.exit(f"ERROR: no coach found with id {query}")
        return hits[0]

    q = query.strip().lower()
    hits = [c for c in catalog if q in (c.get("name") or "").lower()]
    if not hits:
        # surface nearest matches by name prefix
        prefix = q[:3] if len(q) >= 3 else q
        near = [c for c in catalog if (c.get("name") or "").lower().startswith(prefix)][:5]
        msg = f"ERROR: no coach matched '{query}'."
        if near:
            msg += "\nNearest by name prefix:\n" + "\n".join(
                f"  - {c.get('name')} (id={c.get('id')})" for c in near
            )
        sys.exit(msg)
    return hits[0] if len(hits) == 1 else hits


# --- Analysis --------------------------------------------------------------

def _safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def _parse_iso(ts: str) -> datetime | None:
    """Parse ISO 8601 with Z or offset; return None on malformed input.
    Treat naive datetimes as UTC for safety.
    """
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _cadence(tracks_map: dict[str, str], track_types: dict[str, str],
             track_locales: dict[str, str], now: datetime) -> dict:
    """Compute per-month upload counts.

    Returns:
      - by_month_24: total uploads per month (last 24 months) — informational
      - by_month_type_12: trackType breakdown per month (last 12 months)
      - by_month_locale_12: locale breakdown per month (last 12 months) —
        the lens for the per-locale cap (10/month per locale)
    """
    months_24 = Counter()
    month_type_12 = defaultdict(Counter)
    month_locale_12 = defaultdict(Counter)

    # Window cutoffs are first-of-the-month timestamps used with `dt >= cutoff`.
    # To get exactly N months *including* the current calendar month, walk back
    # (N - 1) months from the start of the current month. Stepping back N
    # months would include the first of the month N back, producing N+1
    # buckets — which is the bug cursor-bot caught: 13 months in
    # by_month_locale_12 was being divided by 12 in the per-locale
    # recommendation, inflating averages by ~8%.
    cutoff_24 = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    for _ in range(23):
        y, m = cutoff_24.year, cutoff_24.month - 1
        if m == 0:
            y -= 1; m = 12
        cutoff_24 = datetime(y, m, 1, tzinfo=timezone.utc)
    cutoff_12 = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    for _ in range(11):
        y, m = cutoff_12.year, cutoff_12.month - 1
        if m == 0:
            y -= 1; m = 12
        cutoff_12 = datetime(y, m, 1, tzinfo=timezone.utc)

    for tid, ts in tracks_map.items():
        dt = _parse_iso(ts)
        if dt is None or dt < cutoff_24:
            continue
        month_key = dt.strftime("%Y-%m")
        months_24[month_key] += 1
        if dt >= cutoff_12:
            tt = track_types.get(tid, "(unknown)")
            month_type_12[month_key][tt] += 1
            loc = track_locales.get(tid, "(unknown)")
            month_locale_12[month_key][loc] += 1
    return {
        "by_month_24": dict(sorted(months_24.items())),
        "by_month_type_12": {k: dict(v) for k, v in sorted(month_type_12.items())},
        "by_month_locale_12": {k: dict(v) for k, v in sorted(month_locale_12.items())},
    }


def _peer_band(catalog: list[dict], coach: dict, explicit: tuple[int, int] | None) -> list[dict]:
    """Return list of peer coaches in the chosen follower band, excluding podcasts
    and the target coach itself. Widens adaptively if too few peers."""
    foll = coach.get("followersCount", 0) or 0
    if explicit:
        lo, hi = explicit
    else:
        lo = max(1000, int(foll * 0.7))
        hi = max(lo + 1, int(foll * 1.4))
    peers = [
        c for c in catalog
        if not _is_podcast(c)
        and c.get("id") != coach.get("id")
        and lo <= (c.get("followersCount", 0) or 0) <= hi
        and (c.get("approvedTrackCount", 0) or 0) >= 50  # need a real catalog to compare against
    ]
    # widen once if too small
    if len(peers) < 5 and not explicit:
        lo = max(1000, int(foll * 0.5))
        hi = max(lo + 1, int(foll * 2.0))
        peers = [
            c for c in catalog
            if not _is_podcast(c)
            and c.get("id") != coach.get("id")
            and lo <= (c.get("followersCount", 0) or 0) <= hi
            and (c.get("approvedTrackCount", 0) or 0) >= 50
        ]
    return peers


def _percentiles(values: list[float]) -> dict:
    vals = sorted(values)
    if not vals:
        return {"min": 0, "p25": 0, "median": 0, "p75": 0, "p90": 0, "max": 0}
    n = len(vals)
    return {
        "min": vals[0],
        "p25": vals[max(0, n // 4)],
        "median": median(vals),
        "p75": vals[min(n - 1, 3 * n // 4)],
        "p90": vals[min(n - 1, int(n * 0.9))],
        "max": vals[-1],
    }


def _peer_compare(coach: dict, peers: list[dict]) -> dict:
    metrics = ["playedCount", "listenedCount", "favoritedCount", "approvedTrackCount"]
    out = {}
    for m in metrics:
        out[m] = {
            "coach": coach.get(m, 0),
            "percentiles": _percentiles([(p.get(m, 0) or 0) for p in peers]),
        }
    # Ratios
    def ratio(c, n, d):
        return _safe_div(c.get(n, 0) or 0, c.get(d, 0) or 0)
    out["plays_per_follower"] = {
        "coach": ratio(coach, "playedCount", "followersCount"),
        "percentiles": _percentiles([ratio(p, "playedCount", "followersCount") for p in peers]),
    }
    out["listens_per_follower"] = {
        "coach": ratio(coach, "listenedCount", "followersCount"),
        "percentiles": _percentiles([ratio(p, "listenedCount", "followersCount") for p in peers]),
    }
    out["favs_per_follower"] = {
        "coach": ratio(coach, "favoritedCount", "followersCount"),
        "percentiles": _percentiles([ratio(p, "favoritedCount", "followersCount") for p in peers]),
    }
    out["completion"] = {
        "coach": ratio(coach, "listenedCount", "playedCount"),
        "percentiles": _percentiles([ratio(p, "listenedCount", "playedCount") for p in peers]),
    }
    # Coach's rank in band on key metrics (1 = highest)
    out["ranks"] = {}
    for key, getter in [
        ("plays_per_follower", lambda c: ratio(c, "playedCount", "followersCount")),
        ("favs_per_follower",  lambda c: ratio(c, "favoritedCount", "followersCount")),
        ("playedCount",        lambda c: c.get("playedCount", 0) or 0),
        ("approvedTrackCount", lambda c: c.get("approvedTrackCount", 0) or 0),
    ]:
        all_coaches = peers + [coach]
        ranked = sorted(all_coaches, key=lambda c: -getter(c))
        rank = next((i + 1 for i, c in enumerate(ranked) if c.get("id") == coach.get("id")), len(ranked))
        out["ranks"][key] = (rank, len(ranked))
    return out


def _content_composition(tracks: list[dict]) -> dict:
    by_type = defaultdict(list)
    by_locale = Counter()
    for t in tracks:
        by_type[t.get("trackType", "(unknown)")].append(t)
        by_locale[t.get("locale", "(unknown)")] += 1
    breakdown = []
    total_plays = sum((t.get("playedCount", 0) or 0) for t in tracks)
    for tt, ts in sorted(by_type.items(), key=lambda x: -sum((t.get("playedCount", 0) or 0) for t in x[1])):
        plays = sum((t.get("playedCount", 0) or 0) for t in ts)
        listens = sum((t.get("listenedCount", 0) or 0) for t in ts)
        favs = sum((t.get("favoritedCount", 0) or 0) for t in ts)
        breakdown.append({
            "trackType": tt,
            "count": len(ts),
            "plays": plays,
            "listens": listens,
            "favorites": favs,
            "plays_per_track": _safe_div(plays, len(ts)),
            "pct_plays": 100 * _safe_div(plays, total_plays),
        })
    return {"breakdown": breakdown, "locale": dict(by_locale)}


def _concentration(tracks: list[dict]) -> dict:
    if not tracks:
        return {"total_tracks": 0, "pareto": [], "zero_play": 0, "sub_100_play": 0}
    sorted_tracks = sorted(tracks, key=lambda t: -(t.get("playedCount", 0) or 0))
    total_plays = sum((t.get("playedCount", 0) or 0) for t in tracks)
    n = len(tracks)
    pareto = []
    for pct in (10, 20, 50):
        k = max(1, int(n * pct / 100))
        top_plays = sum((t.get("playedCount", 0) or 0) for t in sorted_tracks[:k])
        pareto.append({"top_pct": pct, "track_count": k, "pct_of_plays": 100 * _safe_div(top_plays, total_plays)})
    return {
        "total_tracks": n,
        "pareto": pareto,
        "zero_play":    sum(1 for t in tracks if (t.get("playedCount", 0) or 0) == 0),
        "sub_100_play": sum(1 for t in tracks if (t.get("playedCount", 0) or 0) < 100),
    }


def _boost_queue_proxy(tracks: list[dict]) -> dict:
    """Upper-bound proxy for tracks with score_n < 200, using listenedCount.
    See SKILL.md — score_n >= listenedCount because score_n counts impressions
    too, so this overstates the boost queue."""
    if not tracks:
        return {"under_threshold": 0, "pct": 0, "note": "no track data"}
    under = sum(1 for t in tracks if (t.get("listenedCount", 0) or 0) < BOOST_THRESHOLD)
    return {
        "under_threshold": under,
        "pct": 100 * _safe_div(under, len(tracks)),
        "note": "upper-bound proxy via listenedCount; true score_n queue is somewhat smaller",
    }


def _recommendation(coach: dict, peers_comp: dict, comp: dict, conc: dict, cadence: dict, has_track_data: bool) -> tuple[str, str]:
    """Return (color, reasoning). Colors: GREEN, YELLOW, RED.

    Cap-violation check is per-locale: the policy is 10 uploads/month *per locale*,
    so we compute each locale's 12-month average independently and flag against the
    cap. A coach uploading 9 English + 9 German is within cap; 12 English + 3 German
    has the English lane over cap and triggers RED.
    """
    reasons = []
    color = "GREEN"

    # Per-locale cap-violation signal. cadence["by_month_locale_12"] is already
    # scoped to the trailing 12 months; sum across months for each locale, then
    # divide by 12 for the per-locale monthly average.
    per_locale_12: dict[str, int] = defaultdict(int)
    for month_counts in cadence.get("by_month_locale_12", {}).values():
        for locale, count in month_counts.items():
            per_locale_12[locale] += count
    per_locale_avg = {loc: tot / 12.0 for loc, tot in per_locale_12.items()}

    lifetime = coach.get("approvedTrackCount", 0) or 0
    cap_applies = lifetime >= LIFETIME_THRESHOLD
    if cap_applies and per_locale_avg:
        over = sorted(
            ((loc, avg) for loc, avg in per_locale_avg.items() if avg > NEAR_CAP),
            key=lambda x: -x[1],
        )
        near = sorted(
            ((loc, avg) for loc, avg in per_locale_avg.items() if NEAR_CAP * 0.8 < avg <= NEAR_CAP),
            key=lambda x: -x[1],
        )
        if over:
            color = "RED"
            locales_str = ", ".join(f"{loc}: {avg:.1f}/mo" for loc, avg in over)
            reasons.append(f"over the {NEAR_CAP}/month per-locale cap on {locales_str}")
        elif near:
            if color == "GREEN":
                color = "YELLOW"
            locales_str = ", ".join(f"{loc}: {avg:.1f}/mo" for loc, avg in near)
            reasons.append(f"approaching the {NEAR_CAP}/month per-locale cap on {locales_str}")

    # Favs/follower ratio — discovery-driven signal
    fpf = peers_comp["favs_per_follower"]["coach"]
    fpf_med = peers_comp["favs_per_follower"]["percentiles"]["median"]
    if fpf_med > 0 and fpf >= 2.0 * fpf_med:
        if color == "GREEN":
            color = "YELLOW"
        reasons.append(
            f"favs/follower {fpf:.2f} is {fpf / fpf_med:.1f}× peer median ({fpf_med:.2f}) — content-driven, not coach-driven engagement"
        )

    # Catalog inflation by music
    if has_track_data:
        music = next((b for b in comp["breakdown"] if b["trackType"] == "music"), None)
        if music and music["count"] >= 200 and music["pct_plays"] < 50:
            if color == "GREEN":
                color = "YELLOW"
            reasons.append(
                f"{music['count']} music tracks ({music['count'] / max(1, conc['total_tracks']) * 100:.0f}% of catalog) generating only {music['pct_plays']:.0f}% of plays — catalog inflation"
            )

    # Zero-play / sub-100-play tail
    if has_track_data and conc["total_tracks"] > 50:
        sub_pct = 100 * conc["sub_100_play"] / conc["total_tracks"]
        if sub_pct > 50:
            if color == "GREEN":
                color = "YELLOW"
            reasons.append(
                f"{conc['sub_100_play']} of {conc['total_tracks']} tracks ({sub_pct:.0f}%) under 100 plays — large unsurfaced tail"
            )

    if not reasons:
        reasons.append("uploads, engagement, and catalog composition all within peer-band norms")
    return color, "; ".join(reasons)


# --- Report formatting -----------------------------------------------------

def _fmt_int(n: float) -> str:
    return f"{int(n):,}"


def _format_report(*, coach: dict, peers: list[dict], peers_comp: dict, cadence: dict,
                   tracks: list[dict] | None, comp: dict | None, conc: dict | None,
                   boost: dict | None, color: str, reason: str, band: tuple[int, int],
                   fetch_misses: int) -> str:
    lines: list[str] = []
    name = coach.get("name", "(unknown)")
    cid = coach.get("id", "")
    joined = (coach.get("createdAt") or "")[:10]
    foll = coach.get("followersCount", 0) or 0
    lifetime = coach.get("approvedTrackCount", 0) or 0
    lines.append(f"# Coach investigation — {name}")
    lines.append("")
    lines.append(f"**ID:** `{cid}`  |  **Joined:** {joined}  |  **Followers:** {_fmt_int(foll)}  |  **Lifetime approved tracks:** {_fmt_int(lifetime)}")
    lines.append("")

    # Headline numbers
    now = datetime.now(timezone.utc)
    twelve_ago = f"{now.year - 1:04d}-{now.month:02d}"
    last_12mo = sum(v for k, v in cadence["by_month_24"].items() if k > twelve_ago)
    avg = last_12mo / 12.0
    lines.append("## Headline")
    lines.append("")
    lines.append(f"- **Plays (lifetime):** {_fmt_int(coach.get('playedCount', 0))}")
    lines.append(f"- **Listens (lifetime):** {_fmt_int(coach.get('listenedCount', 0))}")
    lines.append(f"- **Favorites (lifetime):** {_fmt_int(coach.get('favoritedCount', 0))}")
    lines.append(f"- **Uploads in last 12 months:** {last_12mo}  (avg {avg:.1f}/month)")
    lines.append("")

    # Cadence
    lines.append("## Upload cadence")
    lines.append("")
    lines.append("Last 24 months, month by month:")
    lines.append("")
    lines.append("| Month | Uploads |")
    lines.append("|---|---|")
    for k, v in cadence["by_month_24"].items():
        # Total-volume table: no cap flag (cap is per-locale; see the locale
        # table below for cap-violation flagging).
        lines.append(f"| {k} | {v} |")
    lines.append("")
    if cadence.get("by_month_locale_12"):
        # Build locale columns; cap is per-locale, so flag any (month, locale)
        # cell that hits ≥CAP and approaching-cap cells at CAP-1.
        all_locales = sorted(
            {loc for m in cadence["by_month_locale_12"].values() for loc in m},
            key=lambda loc: -sum(
                cadence["by_month_locale_12"][m].get(loc, 0)
                for m in cadence["by_month_locale_12"]
            ),
        )
        lines.append("Last 12 months by locale (cap is 10/month per locale):")
        lines.append("")
        lines.append("| Month | " + " | ".join(all_locales) + " | total |")
        lines.append("|---" + "|---" * (len(all_locales) + 1) + "|")
        for m, loc_counts in cadence["by_month_locale_12"].items():
            cells = []
            for loc in all_locales:
                cnt = loc_counts.get(loc, 0)
                if cnt >= NEAR_CAP:
                    cells.append(f"**{cnt}** 🚨")
                elif cnt == NEAR_CAP - 1:
                    cells.append(f"{cnt} ⚠️")
                else:
                    cells.append(str(cnt))
            total = sum(loc_counts.values())
            lines.append(f"| {m} | " + " | ".join(cells) + f" | {total} |")
        lines.append("")
        # Per-locale 12-month averages — the diagnostic line.
        per_locale_total: dict[str, int] = defaultdict(int)
        for loc_counts in cadence["by_month_locale_12"].values():
            for loc, cnt in loc_counts.items():
                per_locale_total[loc] += cnt
        avg_line = ", ".join(
            f"{loc} {tot / 12:.1f}/mo"
            for loc, tot in sorted(per_locale_total.items(), key=lambda x: -x[1])
        )
        lines.append(f"**12-month average per locale:** {avg_line}")
        lines.append("")
    if cadence.get("by_month_type_12"):
        # Build trackType columns
        all_types = sorted({tt for m in cadence["by_month_type_12"].values() for tt in m},
                           key=lambda tt: -sum(cadence["by_month_type_12"][m].get(tt, 0) for m in cadence["by_month_type_12"]))
        lines.append("Last 12 months by trackType:")
        lines.append("")
        lines.append("| Month | " + " | ".join(all_types) + " | total |")
        lines.append("|---" + "|---" * (len(all_types) + 1) + "|")
        for m, type_counts in cadence["by_month_type_12"].items():
            row = [f"{type_counts.get(tt, 0)}" for tt in all_types]
            total = sum(type_counts.values())
            lines.append(f"| {m} | " + " | ".join(row) + f" | {total} |")
        lines.append("")

    # Peer comparison
    lines.append("## Peer comparison")
    lines.append("")
    lines.append(f"Follower band: **{band[0]:,}–{band[1]:,}** ({len(peers)} peers)")
    lines.append("")
    def row(label, key, fmt):
        c = peers_comp[key]["coach"]
        p = peers_comp[key]["percentiles"]
        ratio_str = ""
        if p["median"] > 0 and isinstance(c, (int, float)) and c > 0:
            ratio_str = f"{c / p['median']:.1f}× median"
        rank_str = ""
        if key in peers_comp["ranks"]:
            r, total = peers_comp["ranks"][key]
            rank_str = f" — rank #{r}/{total}"
        coach_cell = f"{fmt.format(c)}{rank_str}"
        if ratio_str:
            coach_cell += f" *({ratio_str})*"
        return (
            f"| {label} | {coach_cell} | {fmt.format(p['min'])} | "
            f"{fmt.format(p['median'])} | {fmt.format(p['p75'])} | "
            f"{fmt.format(p['p90'])} | {fmt.format(p['max'])} |"
        )

    lines.append("| Metric | Coach | Peer min | Peer median | Peer p75 | Peer p90 | Peer max |")
    lines.append("|---|---|---|---|---|---|---|")
    lines.append(row("Total plays", "playedCount", "{:,.0f}"))
    lines.append(row("Total listens", "listenedCount", "{:,.0f}"))
    lines.append(row("Approved tracks", "approvedTrackCount", "{:,.0f}"))
    lines.append(row("Plays / follower", "plays_per_follower", "{:,.1f}"))
    lines.append(row("Listens / follower", "listens_per_follower", "{:,.1f}"))
    lines.append(row("**Favs / follower** ⭐", "favs_per_follower", "{:,.2f}"))
    lines.append(row("Completion rate", "completion", "{:.1%}"))
    lines.append("")
    lines.append("*⭐ Favs/follower is the diagnostic for content-driven vs coach-driven engagement. >2× peer median suggests listeners like specific tracks but don't bond with the coach.*")
    lines.append("")

    # Content composition
    if comp:
        lines.append("## Content composition")
        lines.append("")
        lines.append("| trackType | Tracks | % of catalog | Plays | % of plays | Plays / track |")
        lines.append("|---|---|---|---|---|---|")
        total_tracks = sum(b["count"] for b in comp["breakdown"])
        for b in comp["breakdown"]:
            pct_cat = 100 * b["count"] / total_tracks if total_tracks else 0
            lines.append(f"| {b['trackType']} | {b['count']} | {pct_cat:.1f}% | {_fmt_int(b['plays'])} | {b['pct_plays']:.1f}% | {b['plays_per_track']:,.0f} |")
        lines.append("")
        if comp["locale"]:
            loc_str = ", ".join(f"{k} ({v})" for k, v in sorted(comp["locale"].items(), key=lambda x: -x[1]))
            lines.append(f"**Locale split:** {loc_str}")
            lines.append("")

    # Concentration
    if conc and conc["total_tracks"] > 0:
        lines.append("## Catalog concentration")
        lines.append("")
        for p in conc["pareto"]:
            lines.append(f"- Top {p['top_pct']}% of tracks ({p['track_count']} tracks) = **{p['pct_of_plays']:.1f}%** of plays")
        if conc["zero_play"]:
            lines.append(f"- **Zero-play tracks:** {conc['zero_play']}")
        if conc["sub_100_play"]:
            lines.append(f"- **Sub-100-play tracks:** {conc['sub_100_play']} ({100 * conc['sub_100_play'] / conc['total_tracks']:.0f}% of catalog)")
        lines.append("")

    # Boost queue proxy
    if boost:
        lines.append("## Boost-queue saturation (proxy)")
        lines.append("")
        lines.append(f"- **Tracks with <200 listens:** {boost['under_threshold']} ({boost['pct']:.1f}% of catalog)")
        lines.append(f"- *Note: {boost['note']}. For exact score_n queue size see the [Aura Recommendation score_n dashboard](https://datastudio.google.com/u/1/reporting/a522764f-b7e6-423d-bb92-47f905d66df5/page/VWw6B).*")
        lines.append("")

    # Top tracks
    if tracks:
        lines.append("## Top tracks (by lifetime plays)")
        lines.append("")
        lines.append("| # | trackType | Locale | Duration | Plays | Listens | Favs | Title |")
        lines.append("|---|---|---|---|---|---|---|---|")
        sorted_tracks = sorted(tracks, key=lambda t: -(t.get("playedCount", 0) or 0))[:10]
        for i, t in enumerate(sorted_tracks, 1):
            dur = t.get("duration", 0)
            dur_str = f"{dur // 60}:{dur % 60:02d}"
            title = (t.get("title") or "").replace("|", "\\|")[:60]
            lines.append(
                f"| {i} | {t.get('trackType', '?')} | {t.get('locale', '?')} | {dur_str} | "
                f"{_fmt_int(t.get('playedCount', 0))} | {_fmt_int(t.get('listenedCount', 0))} | "
                f"{_fmt_int(t.get('favoritedCount', 0))} | {title} |"
            )
        lines.append("")

    if fetch_misses:
        lines.append(f"*(Note: {fetch_misses} track detail fetch(es) failed; results may be slightly incomplete.)*")
        lines.append("")

    # Recommendation
    color_emoji = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(color, "⚪")
    lines.append("## Recommendation")
    lines.append("")
    lines.append(f"{color_emoji} **{color}** — {reason}.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*See [Products/Aura/Coach Upload Policy](https://www.notion.so/36675e23af20815a8d29e92f227d67f9) for cap details and the Sonic Healer case-study reference.*")
    return "\n".join(lines)


# --- Entry point -----------------------------------------------------------

def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # avoid cp1252 issues on Windows runners

    parser = argparse.ArgumentParser(description="Investigate a single Aura coach")
    parser.add_argument("--coach", required=True, help="Firebase UID or coach display name")
    parser.add_argument("--no-tracks", action="store_true", help="Skip per-track detail fetch (faster, less detail)")
    parser.add_argument("--peer-band", default=None, help="Explicit follower band MIN,MAX (e.g. 5000,10000)")
    args = parser.parse_args()

    token = _require_env("AURA_API_TOKEN")
    explicit_band: tuple[int, int] | None = None
    if args.peer_band:
        try:
            lo, hi = (int(x) for x in args.peer_band.split(","))
            explicit_band = (lo, hi)
        except ValueError:
            sys.exit("ERROR: --peer-band must be MIN,MAX (e.g. 5000,10000)")

    print(f"Fetching catalog…", file=sys.stderr)
    catalog = _fetch_catalog(token)
    print(f"  retrieved {len(catalog)} coaches", file=sys.stderr)

    resolved = _resolve_coach(catalog, args.coach)
    if isinstance(resolved, list):
        msg = f"Multiple coaches matched '{args.coach}':\n"
        for c in resolved[:8]:
            msg += (
                f"  - {c.get('name')} (id={c.get('id')[:10]}…, "
                f"tracks={c.get('approvedTrackCount', 0)}, followers={c.get('followersCount', 0)})\n"
            )
        msg += "Re-run with the full ID for the one you want."
        sys.exit(msg)

    coach = resolved
    if _is_podcast(coach):
        print(
            f"WARNING: '{coach.get('name')}' is a podcast feed (types.podcast = truthy). "
            "This skill is designed for individual coaches; the report will likely be misleading.",
            file=sys.stderr,
        )

    print(f"Fetching detail for {coach.get('name')}…", file=sys.stderr)
    detail = _get_json(f"{BASE}/coaches/{coach['id']}", token)
    coach_full = {**coach, **detail}  # detail wins; ensures tracks map present
    tracks_map = detail.get("tracks") or {}
    print(f"  has {len(tracks_map)} tracks in map", file=sys.stderr)

    # Fetch per-track detail (parallel)
    tracks: list[dict] = []
    fetch_misses = 0
    track_types: dict[str, str] = {}
    track_locales: dict[str, str] = {}
    if not args.no_tracks and tracks_map:
        track_ids = list(tracks_map.keys())
        print(f"Fetching {len(track_ids)} track details with {PARALLEL_FETCHES}-way parallelism…", file=sys.stderr)
        with concurrent.futures.ThreadPoolExecutor(max_workers=PARALLEL_FETCHES) as ex:
            futures = {ex.submit(_fetch_track, tid, token): tid for tid in track_ids}
            done = 0
            for fut in concurrent.futures.as_completed(futures):
                done += 1
                if done % 200 == 0:
                    print(f"  fetched {done}/{len(track_ids)}", file=sys.stderr)
                result = fut.result()
                if result is None:
                    fetch_misses += 1
                else:
                    tracks.append(result)
                    tid = result.get("id") or futures[fut]
                    track_types[tid] = result.get("trackType", "(unknown)")
                    track_locales[tid] = result.get("locale") or "(unknown)"
        print(f"  done — {len(tracks)} fetched, {fetch_misses} failed", file=sys.stderr)

    now = datetime.now(timezone.utc)
    cadence = _cadence(tracks_map, track_types, track_locales, now)

    peers = _peer_band(catalog, coach_full, explicit_band)
    band_lo = min((p.get("followersCount", 0) for p in peers), default=0)
    band_hi = max((p.get("followersCount", 0) for p in peers), default=0)
    print(f"Peer band: {band_lo}–{band_hi} ({len(peers)} peers)", file=sys.stderr)
    peers_comp = _peer_compare(coach_full, peers)

    comp = _content_composition(tracks) if tracks else None
    conc = _concentration(tracks) if tracks else None
    boost = _boost_queue_proxy(tracks) if tracks else None

    color, reason = _recommendation(
        coach_full, peers_comp, comp or {"breakdown": []}, conc or {"total_tracks": 0},
        cadence, has_track_data=bool(tracks),
    )

    report = _format_report(
        coach=coach_full, peers=peers, peers_comp=peers_comp, cadence=cadence,
        tracks=tracks if tracks else None, comp=comp, conc=conc, boost=boost,
        color=color, reason=reason, band=(band_lo, band_hi), fetch_misses=fetch_misses,
    )
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
