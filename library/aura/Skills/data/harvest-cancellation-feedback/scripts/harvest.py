#!/usr/bin/env python3
"""Harvest #ai-feedback cancellations → Signals/Cancellations Notion DB.

Canonical skill: data:harvest-cancellation-feedback

Usage:
    python3 harvest.py [--window 7d] [--dry-run]

Env:
    SLACK_BOT_TOKEN  required — reads #ai-feedback
    AURA_API_TOKEN   optional — profiles each user (skipped if unset, fields left blank)
    NOTION_TOKEN     required — writes rows to the Cancellations DB
                     (or use --dry-run to skip the Notion write)

Data source: collection://edce288d-5d89-4b4d-8083-e40d0ed7c1ee
"""
from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8")

import argparse
import json
import os
import ssl
import subprocess
import urllib.request
import pathlib
import re
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))
from notion_rest import (  # noqa: E402
    MCPFallback,
    make_date,
    make_rich_text,
    make_select,
    make_title,
    notion,
)


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


NOTION_DS = "edce288d-5d89-4b4d-8083-e40d0ed7c1ee"
AURA_BASE = "https://services.aurahealth.io"
AGE_MAP = {"a": "unknown", "b": "26-35", "c": "36-45", "d": "46-55", "e": "55+"}

VALID_REASON = {
    "Too expensive",
    "Not using it enough",
    "Didn't help me the way I hoped",
    "Technical issues or bugs",
    "Just trying it out",
    "Other",
}
VALID_CAMPAIGN = {"organic", "Scale", "CreativeTest", "other"}
VALID_SUPPORT = {
    "gentle/supportive",
    "practical/grounding",
    "encouraging/momentum",
    "clear/direct",
    "professional/structured",
}
VALID_ARCHETYPE = {"deepProcessor", "carryingALot"}


def uid_prefix(uid: str) -> str:
    """Return a short, non-identifying prefix of `uid` that still distinguishes users.

    A bare `uid[:8]` assumes the first 8 characters are distinguishing. For
    provider-prefixed ids they are not: every `google:112...` yields `"google:1"`,
    so distinct users collapse onto one value — and here that value is what the
    `User ID` column and the row title carry, so two users read as one.

    So the 8 characters are taken from the part *after* the provider, and the
    provider is kept as a readable label:

        google:112233445566778899  ->  google:11223344
        FhE3nMkP8yQ2wLxV0tRb9dJc   ->  FhE3nMkP        (unchanged)

    Firebase-28 ids (no colon) are untouched, which is the whole of this table's
    corpus today, so the provider branch is a guard against an id shape this feed
    has not yet carried rather than a transform it applies.

    Firebase **push keys** (20 chars, leading `-`) are deliberately left on the
    plain-truncation path. Their first 8 characters are the timestamp component,
    so ids minted close together share a prefix — weaker than intended, but a
    different problem needing the random suffix, not the provider split.

    PII: the return value is a prefix, never a full uid. The provider label is not
    personal data.

    Canonical copy lives in `report-conversation-patterns/scripts/sample.py`; other
    copies in `harvest-conversation-patterns/scripts/fetch_sweep_analyses.py` and
    `investigate-pmf-survey/scripts/fetch_pmf_responses.py`. Skills are
    self-contained, so the copies must stay in step — `tests/test_uid_prefix_rule.py`
    holds every one of them to the same answers.
    """
    uid = uid or ""
    if ":" in uid:
        provider, _, rest = uid.partition(":")
        return f"{provider}:{rest[:8]}"
    return uid[:8]


def _ssl_ctx() -> ssl.SSLContext:
    # Aura's services.aurahealth.io certificate fails local verification;
    # same bypass used across all Aura-API-calling skills in this repo.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def fetch_profile(uid: str, token: str) -> dict:
    """Fetch Aura user profile. Returns {} on failure (skill degrades gracefully)."""
    if not token:
        return {}
    # Only the fetch is guarded; the mapping runs outside so a single bad field
    # can't blank the whole profile.
    try:
        req = urllib.request.Request(
            f"{AURA_BASE}/users/{uid}",
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": user_agent(),
                "Content-Type": "application/json",
            },
        )
        r = json.loads(urllib.request.urlopen(req, context=_ssl_ctx()).read())
    except Exception as exc:
        print(f"  [warn] profile failed for {uid_prefix(uid)}: {exc}", file=sys.stderr)
        return {}
    return _map_profile(r)


def _map_profile(r: dict) -> dict:
    """Map an Aura /users/{uid} response to the Notion profile columns.

    Every accessor is null-safe: these fields are routinely present-but-null,
    which `dict.get(k, default)` does NOT cover — it defaults on a MISSING key
    only, so a null yields None and the next call raises.
    """
    highlight = (r.get("onboarding") or {}).get("highlight") or {}
    highlight_id = highlight.get("id") or ""
    archetype = (
        highlight_id[len("archetype_") :]
        if highlight_id.startswith("archetype_")
        else ""
    )
    raw_campaign = (
        r.get("aiAppstackCampaign") or r.get("campaign") or r.get("subscriptionCampaign") or ""
    ).strip()
    campaign_lower = raw_campaign.lower()
    if "creativetest" in campaign_lower:
        campaign = "CreativeTest"
    elif "scale" in campaign_lower:
        campaign = "Scale"
    elif raw_campaign:
        campaign = "other"
    else:
        campaign = "organic"
    age_letter = (r.get("ageGroup") or "").lower()
    return {
        "archetype": archetype,
        # Support Style isn't exposed anywhere in the Aura /users/{uid}
        # response — left blank; the Notion cell stays empty.
        "support_style": "",
        "campaign": campaign,
        "age_group": AGE_MAP.get(age_letter, "unknown"),
        "email_domain": (r.get("email") or "@").split("@")[-1],
        "timezone": r.get("timezone") or "",
    }


def existing_slack_urls() -> set[str]:
    """Return Slack Thread URLs already in the DB — used for deduplication."""
    if not notion.available:
        return set()
    urls: set[str] = set()
    for row in notion.query_data_source(NOTION_DS, page_size=200):
        url_prop = row.get("properties", {}).get("Slack Thread URL", {})
        u = url_prop.get("url") or ""
        if u:
            urls.add(u)
    return urls


def norm(value: str, valid: set, default: str) -> str:
    return value if value in valid else default


def ts_to_date(ts: str) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def write_row(entry: dict, profile: dict, dry_run: bool) -> bool:
    uid8 = uid_prefix(entry["uid"])
    cancel_date = ts_to_date(entry["ts"])
    reason = norm(entry.get("reason", ""), VALID_REASON, "Other")
    title_str = f"{uid8} — {reason[:35]} — {cancel_date}"

    props: dict = {
        "title": make_title(title_str),
        "User ID": make_rich_text(uid8),
        "Reason": make_select(reason),
        "Feedback Text": make_rich_text(entry.get("feedback", "")),
        "Slack Thread URL": {"url": entry.get("permalink", "")},
        "Creator": make_select("Agent"),
        "Cancellation Date": make_date(cancel_date),
    }
    # Only write profile fields when actual data is available — omitting them
    # leaves the Notion cell blank, which is correct when the profile is missing.
    campaign = profile.get("campaign", "")
    if campaign:
        props["Acquisition"] = make_select(norm(campaign, VALID_CAMPAIGN, "organic"))
    archetype = profile.get("archetype", "")
    if archetype:
        props["Archetype"] = make_select(norm(archetype, VALID_ARCHETYPE, "unknown"))
    support_style = profile.get("support_style", "")
    if support_style:
        props["Support Style"] = make_select(norm(support_style, VALID_SUPPORT, "unknown"))
    age_group = profile.get("age_group", "")
    if age_group and age_group != "unknown":
        props["Age Group"] = make_select(age_group)
    email_domain = profile.get("email_domain", "")
    if email_domain:
        props["Email Domain"] = make_rich_text(email_domain)

    days = entry.get("days_to_cancel")
    if days is not None:
        props["Days to Cancel"] = {"number": round(float(days), 2)}

    if dry_run:
        print(f"  [dry-run] {title_str}")
        return True

    notion.create_database_row(NOTION_DS, props)
    print(f"  ✓ {title_str}")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--window",
        default="7d",
        help="Nd or YYYY-MM-DD..YYYY-MM-DD (default: 7d)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without touching Notion",
    )
    args = ap.parse_args()

    if not notion.available and not args.dry_run:
        print(
            "NOTION_TOKEN not set. Either set it in ~/.claude/settings.json (env block) "
            "or pass --dry-run to preview without writing.",
            file=sys.stderr,
        )
        sys.exit(1)

    # 1. Fetch cancellation entries from Slack
    fetch_script = SCRIPTS / "fetch_cancellations.py"
    result = subprocess.run(
        [
            sys.executable,
            str(fetch_script),
            "--channel",
            "cancellations",
            "--window",
            args.window,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        print(f"fetch_cancellations failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    data = json.loads(result.stdout)
    # The cancellation feed spans two channels since the 2026-08 split — the sentinel
    # wraps them under `channels[]`. Merging is safe because step 2 dedups by permalink;
    # re-sorting restores the newest-first order a single channel came back in.
    channels = data.get("channels", [data])
    entries = [e for sub in channels for e in sub.get("entries", [])]
    entries.sort(key=lambda e: float(e.get("ts") or 0), reverse=True)
    test_dropped = sum(sub.get("counts", {}).get("test_dropped", 0) for sub in channels)
    print(
        f"Fetched {len(entries)} real entries from the cancellation feed "
        f"(window={args.window}, test_dropped={test_dropped})"
    )

    # 2. Dedup against existing Notion rows
    already = existing_slack_urls()
    if not already and not notion.available:
        print(
            "[warn] NOTION_TOKEN not set — dedup check skipped; all entries treated "
            "as new. Pass --dry-run to preview without writing.",
            file=sys.stderr,
        )
    print(f"Existing DB rows: {len(already)}")
    new_entries = [e for e in entries if e.get("permalink", "") not in already]
    print(f"New entries to add: {len(new_entries)}")

    if not new_entries:
        print("Nothing to add. DB is up to date.")
        return

    # 3. Profile each new user and write to Notion
    aura_token = os.environ.get("AURA_API_TOKEN", "")
    if not aura_token:
        print(
            "AURA_API_TOKEN not set — profile fields (archetype, support style, "
            "campaign, age group, email domain) will be left blank.",
            file=sys.stderr,
        )

    added = 0
    for e in new_entries:
        uid8 = uid_prefix(e["uid"])
        reason_preview = (e.get("reason", "") or "")[:30]
        print(f"Processing {uid8} ({reason_preview!r})...")
        profile = fetch_profile(e["uid"], aura_token)
        if write_row(e, profile, args.dry_run):
            added += 1

    verb = "Would add" if args.dry_run else "Added"
    print(f"\n{verb} {added}/{len(new_entries)} rows to Signals/Cancellations.")
    if not args.dry_run:
        print(
            "Run `data:update-cancellations-top10` to refresh the Top 10 "
            "synthesis on the Learnings page."
        )


if __name__ == "__main__":
    main()
