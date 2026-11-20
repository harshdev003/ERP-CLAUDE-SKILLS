#!/usr/bin/env python3
"""
Coach upload monitor — daily Slack alert (per-locale cap).

Identifies (coach, locale) pairs that have hit >=8 uploads in the current
calendar month and posts a formatted summary to a Slack channel via an
incoming webhook. Designed to be run from a GitHub Action on a daily cron.

Policy context (Aura, effective July 1 2026):
- Coach upload cap: 10 tracks/month *per locale*
- Cap applies once a coach has >=100 lifetime approved tracks (any locale)
- Podcast feeds (`types.podcast` truthy) are excluded from cap enforcement
- Alert threshold: 8 uploads month-to-date per (coach, locale) — early warning

What counts toward the monthly number: tracks that are approved OR still
awaiting approval. Tracks QA has rejected (`approved is False`) are excluded,
so the count is a live "still needs action / counts toward cap" figure — as QA
rejects over-cap tracks the number draws down and the pair drops off the alert
once it's back within cap. A rejected track that is later re-submitted re-enters
the queue as pending (or as a new trackId) and counts again as a new
submission. Each flagged row breaks the number into approved vs awaiting.

A coach uploading 9 English + 9 German is within cap (each locale below 10).
A coach uploading 11 English + 3 German has the English lane over cap; German
is fine. The alert lists each (coach, locale) pair separately so the lane
that's over the limit is unambiguous.

Environment variables required:
- AURA_API_TOKEN     Bearer token for services.aurahealth.io
- SLACK_WEBHOOK_URL  Incoming webhook bound to #coach-upload-monitor

The script uses only the Python standard library — no pip dependencies on
the runner.
"""

from __future__ import annotations

import concurrent.futures
import getpass
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

# --- Policy thresholds (do not change without explicit approval) -------------
CAP = 10              # tracks/month/locale cap
THRESHOLD = 100       # cap applies once coach has >=THRESHOLD lifetime tracks (any locale)
ALERT_AT = 8          # min monthly uploads per (coach, locale) to surface

# --- Infra ------------------------------------------------------------------
BASE = "https://services.aurahealth.io"
PAGE_LIMIT = 500


def user_agent() -> str:
    """`AuraClaude/<who>/ci:coach-upload-monitor (session <id>)`: labels this job's Aura API calls.

    On the GitHub runner <who> is `github-actions` and the session is the run id, so
    eng:flag-suspicious-traffic can count the nightly run as our own traffic. A label, not proof.
    """
    who = os.environ.get("AINATIVE_UA_WHO") or (
        "github-actions" if os.environ.get("GITHUB_ACTIONS") else getpass.getuser())
    who = re.sub(r"[^A-Za-z0-9._-]+", "-", who).strip("-.") or "unknown"
    run = os.environ.get("GITHUB_RUN_ID")
    session = f"gha-{run}" if run else os.environ.get("AINATIVE_SESSION_NAME", "adhoc")
    return f"AuraClaude/{who}/ci:coach-upload-monitor (session {session})"
TRACK_FETCH_PARALLELISM = 16


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        print(f"ERROR: required env var {name} is not set", file=sys.stderr)
        sys.exit(2)
    return val


def _get_json(url: str, token: str, timeout: int = 30) -> Any:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent()})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_slack(webhook_url: str, message: str) -> None:
    body = json.dumps({"text": message}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"Slack webhook returned {resp.status}: {resp.read()!r}")


def _is_podcast(coach: dict) -> bool:
    """True if the coach is a podcast feed.

    The Aura API returns `types` in two different shapes depending on
    endpoint:
      - /coaches (listing):  {"podcast": <track_count>, ...}  (int counts)
      - /coaches/<id>:       {"podcast": true, ...}           (booleans)

    A truthy `types.podcast` in either shape means the coach is a podcast.
    """
    types = coach.get("types") or {}
    if not isinstance(types, dict):
        return False
    return bool(types.get("podcast"))


def _fetch_all_coaches(token: str) -> list[dict]:
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


def _parse_ts(ts: Any) -> datetime | None:
    """Parse an ISO 8601 createdAt; return None on malformed input.

    Defensive: if a track timestamp ever arrives without a Z/offset,
    fromisoformat produces a naive datetime. Treat naive as UTC so the
    comparison against month_start (tz-aware) can't raise TypeError.
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


def _tracks_this_month(coach_detail: dict, month_start: datetime) -> list[str]:
    """Return trackIds the coach created this calendar month (any approval state).

    This is a superset used to gate candidates cheaply from the /coaches/<id>
    `tracks` map alone (no per-track fetch). QA-rejected tracks are filtered out
    later in Pass 3, once each track's `approved` flag has been fetched — so
    including them here only widens the candidate set, never narrows it.
    """
    tracks = coach_detail.get("tracks") or {}
    if not isinstance(tracks, dict):
        return []
    out: list[str] = []
    for tid, ts in tracks.items():
        dt = _parse_ts(ts)
        if dt is not None and dt >= month_start:
            out.append(tid)
    return out


def _fetch_track_meta(track_id: str, token: str) -> tuple[str, str | None, Any]:
    """Fetch a single track's locale + approval state.

    Returns (trackId, locale, approved):
      - locale is None on fetch error (caller buckets under "(unknown)").
      - approved is the raw `approved` field: True (approved), False (rejected),
        or None (pending / not yet reviewed). On fetch error it is None, so an
        unreachable track is treated as pending and still counts — a track is
        only ever *excluded* when `approved` is explicitly False.
    """
    try:
        d = _get_json(f"{BASE}/contents/{track_id}", token)
        return track_id, (d.get("locale") or "(unknown)"), d.get("approved")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return track_id, None, None


def _tier(row: dict) -> int:
    """Lower tier => more urgent. Used for sorting and grouping in the alert."""
    if row["lifetime"] < THRESHOLD:
        return 3  # cap not yet active
    if row["this_month"] >= CAP:
        return 0           # over cap
    if row["this_month"] == CAP - 1:
        return 1           # approaching cap
    return 2               # at ALERT_AT — monitor


def _fmt_row(r: dict, *, bold_count: bool) -> str:
    """One line in the alert.

    r has keys: name, locale, this_month, approved, pending, lifetime.
    `this_month` == approved + pending (QA-rejected tracks already excluded).
    """
    cnt = f"*{r['this_month']} this month*" if bold_count else f"{r['this_month']} this month"
    breakdown = f"{r['approved']} approved · {r['pending']} awaiting"
    return (
        f"  • *{r['name']}* / `{r['locale']}` — {cnt} "
        f"({breakdown}) · {r['lifetime']:,} lifetime"
    )


def _build_message(flagged: list[dict], now: datetime, fetch_errors: int,
                   locale_fetch_misses: int) -> str:
    today_str = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%B %Y")

    if not flagged:
        body = (
            f"*Coach upload monitor — {today_str}*\n\n"
            f"No non-podcast (coach, locale) pairs at {ALERT_AT}+ approved-or-pending "
            f"tracks month-to-date. :white_check_mark:"
        )
        return body + _footer(today_str)

    flagged_sorted = sorted(
        flagged,
        key=lambda r: (_tier(r), -r["this_month"], r["name"].lower(), r["locale"]),
    )
    over_cap     = [r for r in flagged_sorted if _tier(r) == 0]
    near_cap     = [r for r in flagged_sorted if _tier(r) == 1]
    in_range     = [r for r in flagged_sorted if _tier(r) == 2]
    below_thresh = [r for r in flagged_sorted if _tier(r) == 3]

    lines = [
        f"*Coach upload monitor — {today_str}*",
        f"_(coach, locale) pairs with ≥{ALERT_AT} approved-or-pending tracks in "
        f"{month_str} (month-to-date); QA-rejected tracks excluded. "
        f"Cap is {CAP}/month per locale. Each row: approved · awaiting approval._",
    ]
    if over_cap:
        lines.append(f"\n:rotating_light: *Over the {CAP}/mo cap* ({len(over_cap)})")
        for r in over_cap:
            lines.append(_fmt_row(r, bold_count=True))
    if near_cap:
        lines.append(f"\n:warning: *At {CAP - 1} — approaching cap* ({len(near_cap)})")
        for r in near_cap:
            lines.append(_fmt_row(r, bold_count=False))
    if in_range:
        lines.append(f"\n:eyes: *At {ALERT_AT} — monitor* ({len(in_range)})")
        for r in in_range:
            lines.append(_fmt_row(r, bold_count=False))
    if below_thresh:
        lines.append(
            f"\n:seedling: *Under {THRESHOLD}-track lifetime threshold — cap not yet active* "
            f"({len(below_thresh)})"
        )
        for r in below_thresh:
            lines.append(_fmt_row(r, bold_count=False))

    lines.append(
        f"\n_Total flagged pairs: {len(flagged_sorted)}. "
        f"Cap: {CAP} tracks/month per locale, for coaches with ≥{THRESHOLD} lifetime tracks._"
    )
    if fetch_errors:
        lines.append(
            f"_(Note: {fetch_errors} coach record(s) could not be fetched; "
            f"results may be incomplete.)_"
        )
    if locale_fetch_misses:
        lines.append(
            f"_(Note: {locale_fetch_misses} track(s) had locale fetch failures; "
            f"those tracks are grouped under `(unknown)` and may need manual review.)_"
        )
    return "\n".join(lines) + _footer(today_str)


def _footer(today_str: str) -> str:
    """Slack post convention — italic attribution footer.

    See AINative/CLAUDE.md → Slack post conventions and Tools/Slack → Post format.
    """
    return (
        f"\n\n_Generated by `github-action: AINative/.github/workflows/"
        f"coach-upload-monitor.yml` · {today_str} · prod_"
    )


def main() -> int:
    token = _require_env("AURA_API_TOKEN")
    webhook = _require_env("SLACK_WEBHOOK_URL")

    now = datetime.now(timezone.utc)
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)

    print(f"[{now.isoformat()}] fetching coach catalog…", flush=True)
    catalog = _fetch_all_coaches(token)
    print(f"  retrieved {len(catalog)} coaches", flush=True)

    # Pass 1: for each non-podcast coach, fetch detail and identify this-month tracks
    # Build a list of (coach_meta, [trackIds_this_month]) for coaches with >=ALERT_AT.
    # Coaches below ALERT_AT total can't have any locale hit ALERT_AT — skip the
    # expensive per-track locale fetch for them.
    candidates: list[tuple[dict, list[str]]] = []
    fetch_errors = 0
    for c in catalog:
        if _is_podcast(c):
            continue
        cid = c.get("id")
        if not cid:
            continue
        try:
            detail = _get_json(f"{BASE}/coaches/{cid}", token)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as exc:
            print(f"  WARN: failed to fetch coach {cid}: {exc}", file=sys.stderr)
            fetch_errors += 1
            continue
        track_ids = _tracks_this_month(detail, month_start)
        if len(track_ids) >= ALERT_AT:
            candidates.append((
                {
                    "id": cid,
                    "name": detail.get("name", "?"),
                    "lifetime": detail.get("approvedTrackCount") or 0,
                },
                track_ids,
            ))

    print(
        f"  {len(candidates)} candidate coach(es) with >={ALERT_AT} this-month uploads; "
        f"fetching locale per track…",
        flush=True,
    )

    # Pass 2: parallel-fetch locale + approval state for every current-month
    # track of every candidate.
    all_tids = [(c, tid) for c, tids in candidates for tid in tids]
    locale_by_tid: dict[str, str] = {}
    approved_by_tid: dict[str, Any] = {}
    locale_fetch_misses = 0
    if all_tids:
        with concurrent.futures.ThreadPoolExecutor(max_workers=TRACK_FETCH_PARALLELISM) as ex:
            futures = {ex.submit(_fetch_track_meta, tid, token): tid for _, tid in all_tids}
            for fut in concurrent.futures.as_completed(futures):
                tid, locale, approved = fut.result()
                approved_by_tid[tid] = approved
                if locale is None:
                    locale_fetch_misses += 1
                    locale_by_tid[tid] = "(unknown)"
                else:
                    locale_by_tid[tid] = locale
        print(f"  done — {len(locale_by_tid)} tracks classified ({locale_fetch_misses} failed)",
              flush=True)

    # Pass 3: bucket by (coach, locale), excluding QA-rejected tracks, and split
    # each pair's count into approved vs awaiting-approval. Filter to >=ALERT_AT
    # on the combined (approved + pending) total.
    flagged: list[dict] = []
    for c, tids in candidates:
        appr_by_locale: dict[str, int] = defaultdict(int)
        pend_by_locale: dict[str, int] = defaultdict(int)
        for tid in tids:
            if approved_by_tid.get(tid) is False:
                continue  # QA-rejected — excluded from the cap count
            locale = locale_by_tid.get(tid, "(unknown)")
            if approved_by_tid.get(tid) is True:
                appr_by_locale[locale] += 1
            else:
                pend_by_locale[locale] += 1  # None => pending / not yet reviewed
        for locale in set(appr_by_locale) | set(pend_by_locale):
            approved = appr_by_locale[locale]
            pending = pend_by_locale[locale]
            total = approved + pending
            if total >= ALERT_AT:
                flagged.append({
                    "name": c["name"],
                    "coach_id": c["id"],
                    "locale": locale,
                    "this_month": total,
                    "approved": approved,
                    "pending": pending,
                    "lifetime": c["lifetime"],
                })

    message = _build_message(flagged, now, fetch_errors, locale_fetch_misses)
    print("---- Slack message ----")
    print(message)
    print("-----------------------")
    print(f"Posting to Slack…", flush=True)
    _post_slack(webhook, message)
    print(
        f"Posted alert with {len(flagged)} flagged (coach, locale) pair(s) at "
        f"{now.isoformat()}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
