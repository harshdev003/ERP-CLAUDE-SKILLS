#!/usr/bin/env python3
"""Backfill Archetype/Acquisition/Age Group/Sessions/Turns/Signal/Vertical onto
existing Signals/Cancellations rows.

Canonical skill: data:enrich-cancellation-feedback

Two-phase, because Signal and Vertical need a human-language read of conversation
content that a script can't produce (deterministic-first is an explicit non-goal
for those two fields):

    python3 enrich.py fetch --limit 20 --out /tmp/enrich_batch.json
    # ... the running agent reads /tmp/enrich_batch.json, writes a 1-2 sentence
    #     "signal" string and picks a "vertical" for each entry, saves it back ...
    python3 enrich.py write --input /tmp/enrich_batch.json

`fetch` never includes a full Firebase UID in its output — only the existing
8-char prefix already in Notion. The full UID is re-derived from each row's
Slack Thread URL (PII rule: Cancellations DB never stores it), used in-memory
for the Aura API + OpenAI stored-completions calls, and discarded.

Env:
    SLACK_BOT_TOKEN       required for `fetch` — re-fetches the source k8bot
                          message by ts to recover the full UID
    AURA_API_TOKEN        optional for `fetch` — profiles each user (skipped
                          if unset, Archetype/Acquisition/Age Group left blank)
    OPENAI_CONVERSATION_API_KEY  optional for `fetch` — Sessions/Turns counts +
                          the conversation excerpt the agent reads for Signal
                          (skipped if unset, both left blank). Project-scoped
                          service-account key for Conversation [Production] —
                          OPENAI_ADMIN_API_KEY cannot hold the `model.request`
                          scope stored-completions reads require (PROD-3572).
    NOTION_TOKEN          required — reads target rows (`fetch`) and writes
                          enrichment fields back (`write`)

Data source: collection://edce288d-5d89-4b4d-8083-e40d0ed7c1ee
"""
from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8")

import argparse
import json
import os
import re
import ssl
import time
import urllib.request
import pathlib
from pathlib import Path

SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))
from notion_rest import notion, make_rich_text, make_select  # noqa: E402
from slack_read import message_by_ts, SlackReadError  # noqa: E402
from fetch_logs import run_user_id, CONVERSATION_PROJECT_ID  # noqa: E402


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
WORKSPACE = "auramobile"
AGE_MAP = {"a": "unknown", "b": "26-35", "c": "36-45", "d": "46-55", "e": "55+"}
VALID_CAMPAIGN = {"organic", "Scale", "CreativeTest", "other"}
VALID_ARCHETYPE = {"deepProcessor", "carryingALot"}
VALID_VERTICAL = {
    "grief", "anxiety", "relationships", "stress", "trauma", "loneliness",
    "self_worth", "parenting", "work", "health", "unknown",
}

# Per row: 1 Slack fetch + 1 Aura API call + 1 OpenAI stored-completions walk.
# A large backlog is real API traffic and wall-clock time — `--limit` chunks it
# so a single invocation doesn't try to do everything in one shot.
DEFAULT_LIMIT = 20
PACE_SECONDS = 0.4


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def norm(value: str, valid: set, default: str) -> str:
    return value if value in valid else default


def parse_permalink(url: str) -> tuple[str, str] | None:
    """`.../archives/<channel>/p<digits>` -> (channel, "<digits[:-6]>.<digits[-6:]>")."""
    m = re.search(r"archives/([A-Z0-9]+)/p(\d+)", url or "")
    if not m:
        return None
    channel, digits = m.group(1), m.group(2)
    if len(digits) <= 6:
        return None
    return channel, f"{digits[:-6]}.{digits[-6:]}"


def recover_full_uid(channel: str, ts: str, slack_token: str) -> str | None:
    """Re-fetch the source k8bot message and pull the full UID from `_User: <UID>_`.

    Never persisted — used only in-memory by the caller for this one run.
    """
    try:
        msgs = message_by_ts(channel, ts, token=slack_token)
    except SlackReadError as exc:
        print(f"  [warn] Slack refetch failed for ts={ts}: {exc}", file=sys.stderr)
        return None
    if not msgs:
        return None
    text = msgs[0].get("text", "")
    m = re.search(r"_User:\s*([A-Za-z0-9]+)", text)
    return m.group(1) if m else None


def fetch_aura_profile(uid: str, token: str) -> dict:
    """Same field mapping as harvest-cancellation-feedback's fixed fetch_profile()
    (PROD-3441) — copied rather than imported, per repo self-containment."""
    if not token:
        return {}
    # Only the fetch is guarded; the mapping runs outside so a single bad field
    # can't blank the whole profile.
    try:
        req = urllib.request.Request(
            f"{AURA_BASE}/users/{uid}",
            headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent(), "Content-Type": "application/json"},
        )
        r = json.loads(urllib.request.urlopen(req, context=_ssl_ctx()).read())
    except Exception as exc:
        print(f"  [warn] Aura profile failed for {uid[:8]}: {exc}", file=sys.stderr)
        return {}
    return _map_aura_profile(r)


def _map_aura_profile(r: dict) -> dict:
    """Map an Aura /users/{uid} response to the Cancellations profile columns.

    Every accessor is null-safe: these fields are routinely present-but-null,
    which `dict.get(k, default)` does NOT cover — it defaults on a MISSING key
    only, so a null yields None and the next call raises.
    """
    highlight = (r.get("onboarding") or {}).get("highlight") or {}
    highlight_id = highlight.get("id") or ""
    archetype = (
        highlight_id[len("archetype_") :] if highlight_id.startswith("archetype_") else ""
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
    # The Aura /users/{uid} response carries no Support Style field, so the
    # Cancellations "Support Style" column is left blank.
    return {
        "archetype": norm(archetype, VALID_ARCHETYPE, "") if archetype else "",
        "campaign": norm(campaign, VALID_CAMPAIGN, "organic"),
        "age_group": AGE_MAP.get(age_letter, "unknown"),
    }


def excerpt_conversations(conversations: list[dict], max_convs: int = 2, max_turns: int = 4, max_chars: int = 350) -> str:
    """Trim conversation turns to a short, readable excerpt for Signal-writing.

    Not the full transcript — 131 rows' worth of full transcripts would blow up
    the agent's context. Most recent conversations first, first+last turns of each.
    """
    lines: list[str] = []
    for conv in conversations[:max_convs]:
        turns = conv.get("turns", [])
        picked = turns[:2] + turns[-2:] if len(turns) > max_turns else turns
        for t in picked:
            user_msg = (t.get("input") or {}).get("content") or ""
            asst_msg = (t.get("output") or {}).get("content") or ""
            if user_msg:
                lines.append(f"U: {user_msg[:max_chars]}")
            if asst_msg:
                lines.append(f"A: {asst_msg[:max_chars]}")
    return "\n".join(lines)


def cmd_fetch(args: argparse.Namespace) -> None:
    if not notion.available:
        print("NOTION_TOKEN not set — can't read the Cancellations DB.", file=sys.stderr)
        sys.exit(1)
    slack_token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not slack_token:
        print(
            "SLACK_BOT_TOKEN not set — can't recover full UIDs from source messages. "
            "Nothing can be enriched without it.",
            file=sys.stderr,
        )
        sys.exit(1)
    aura_token = os.environ.get("AURA_API_TOKEN", "")
    openai_key = os.environ.get("OPENAI_CONVERSATION_API_KEY", "")
    if not aura_token:
        print("[warn] AURA_API_TOKEN not set — Archetype/Acquisition/Age Group will stay blank.", file=sys.stderr)
    if not openai_key:
        print("[warn] OPENAI_CONVERSATION_API_KEY not set — Sessions/Turns/conversation excerpt will stay blank.", file=sys.stderr)

    print(f"Scanning rows missing Sessions (stopping once {args.limit} are successfully enriched)...", file=sys.stderr)

    batch = []
    scanned = 0
    for row in notion.query_data_source(
        NOTION_DS,
        filter={"property": "Sessions", "number": {"is_empty": True}},
        page_size=100,
    ):
        if len(batch) >= args.limit:
            break
        scanned += 1
        props = row.get("properties", {})
        title = "".join(seg.get("plain_text", "") for seg in props.get("Title", {}).get("title", []))
        uid8 = "".join(seg.get("plain_text", "") for seg in props.get("User ID", {}).get("rich_text", []))
        permalink = (props.get("Slack Thread URL", {}) or {}).get("url", "")
        reason = (props.get("Reason", {}).get("select") or {}).get("name", "")
        feedback = "".join(seg.get("plain_text", "") for seg in props.get("Feedback Text", {}).get("rich_text", []))
        # Carry forward any Signal Notion already has (e.g. written in a prior
        # run whose Sessions/Turns got held back because OPENAI_CONVERSATION_API_KEY
        # was unset then) — otherwise a fresh "" here could get left blank
        # again by the agent, permanently withholding Sessions/Turns even
        # once the data becomes available.
        existing_signal = "".join(seg.get("plain_text", "") for seg in props.get("Signal", {}).get("rich_text", []))
        # Same carry-forward for Vertical (a select) — a prior run may have set it;
        # don't make the agent re-pick something Notion already has.
        existing_vertical = (props.get("Vertical", {}).get("select") or {}).get("name", "")

        print(f"Processing {uid8} ({title})...", file=sys.stderr)
        parsed = parse_permalink(permalink)
        if not parsed:
            print(f"  [warn] couldn't parse Slack Thread URL for {uid8}, skipping", file=sys.stderr)
            continue
        channel, ts = parsed
        full_uid = recover_full_uid(channel, ts, slack_token)
        if not full_uid:
            print(f"  [warn] couldn't recover full UID for {uid8} (source message gone?), skipping", file=sys.stderr)
            continue
        if not full_uid.startswith(uid8):
            print(
                f"  [warn] recovered UID {full_uid[:8]}... doesn't match this row's stored "
                f"prefix {uid8} — Slack Thread URL likely points at the wrong message, skipping",
                file=sys.stderr,
            )
            continue

        profile = fetch_aura_profile(full_uid, aura_token) if aura_token else {}

        sessions = turns = None
        excerpt = ""
        if openai_key:
            try:
                result = run_user_id(openai_key, CONVERSATION_PROJECT_ID, full_uid, args.window)
                sessions = result.get("conversation_count", 0)
                turns = result.get("total_turn_count", result.get("turn_count", 0))
                excerpt = excerpt_conversations(result.get("conversations", []))
            except (Exception, SystemExit) as exc:
                # fetch_logs.py raises FetchLogsError (a plain Exception) on an
                # HTTP/auth failure; SystemExit is kept in the catch defensively.
                # One bad row degrades to 0 sessions instead of aborting the batch.
                #
                # Treat this the same as "zero conversations found" (Sessions/Turns
                # = 0) rather than leaving them None: the row's Sessions field is
                # what marks it as already-processed (the `is_empty` filter above).
                # Leaving it None would make this row match that filter forever,
                # re-consuming a --limit slot on every future run even though its
                # other fields (Archetype/Acquisition/Age Group) already succeeded.
                print(f"  [warn] OpenAI lookup failed for {uid8}: {exc} — recording as 0 sessions/turns", file=sys.stderr)
                sessions = turns = 0

        # full_uid deliberately never enters `batch` — used only above, now discarded.
        batch.append({
            "page_id": row["id"],
            "uid8": uid8,
            "title": title,
            "existing_reason": reason,
            "existing_feedback": feedback,
            "archetype": profile.get("archetype", ""),
            "acquisition": profile.get("campaign", ""),
            "age_group": profile.get("age_group", ""),
            "sessions": sessions,
            "turns": turns,
            "conversation_excerpt": excerpt,
            # Pre-populated from Notion if a prior run already wrote one (see
            # the comment above `existing_signal`); the agent only needs to
            # fill this in when it's genuinely still blank.
            "signal": existing_signal,
            # Second agent-judgment field: the emotional theme of the
            # conversation, chosen from VALID_VERTICAL. Carried forward from
            # Notion if already set; otherwise the agent picks it during review.
            "vertical": existing_vertical,
        })
        time.sleep(PACE_SECONDS)

    print(f"\nScanned {scanned} row(s), successfully enriched {len(batch)}.", file=sys.stderr)
    out = json.dumps(batch, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"Wrote {len(batch)} row(s) to {args.out}. Fill in \"signal\" and \"vertical\" for each, then run:\n  python3 enrich.py write --input {args.out}", file=sys.stderr)
    else:
        print(out)


def cmd_write(args: argparse.Namespace) -> None:
    if not notion.available:
        print("NOTION_TOKEN not set — can't write to the Cancellations DB.", file=sys.stderr)
        sys.exit(1)
    batch = json.loads(Path(args.input).read_text(encoding="utf-8"))

    written = 0
    for entry in batch:
        page_id = entry["page_id"]
        props: dict = {}
        if entry.get("archetype"):
            props["Archetype"] = make_select(entry["archetype"])
        if entry.get("acquisition"):
            props["Acquisition"] = make_select(entry["acquisition"])
        if entry.get("age_group") and entry["age_group"] != "unknown":
            props["Age Group"] = make_select(entry["age_group"])
        # Vertical's select value writes whenever valid; a value outside the enum
        # is warned about and dropped, not written.
        vertical = entry.get("vertical", "")
        vertical_ok = vertical in VALID_VERTICAL  # non-empty AND a known value
        if vertical and not vertical_ok:
            print(
                f"  [warn] {entry.get('uid8', '?')} — vertical '{vertical}' not in "
                f"the allowed set, skipping that field",
                file=sys.stderr,
            )
        if vertical_ok:
            props["Vertical"] = make_select(vertical)

        signal = entry.get("signal", "")
        if signal:
            props["Signal"] = make_rich_text(signal)

        # Sessions is the "already enriched" marker `fetch`'s query filters on
        # (`Sessions is_empty`). Both agent-judgment fields — Signal and Vertical
        # — must be complete before writing it: once Sessions is set the row
        # drops out of every future `fetch`, so writing it while either is still
        # blank would strand that field with no way to revisit it.
        if signal and vertical_ok:
            if entry.get("sessions") is not None:
                props["Sessions"] = {"number": entry["sessions"]}
            if entry.get("turns") is not None:
                props["Turns"] = {"number": entry["turns"]}
        elif entry.get("sessions") is not None or entry.get("turns") is not None:
            missing = " + ".join(
                m for m, ok in (("signal", bool(signal)), ("vertical", vertical_ok)) if not ok
            )
            print(
                f"  [warn] {entry.get('uid8', '?')} — {missing} blank, holding back "
                f"Sessions/Turns so this row stays eligible for a future fetch",
                file=sys.stderr,
            )

        if not props:
            print(f"  [skip] {entry.get('uid8', '?')} — nothing to write", file=sys.stderr)
            continue

        if args.dry_run:
            print(f"  [dry-run] {entry.get('uid8', '?')} -> {list(props.keys())}")
        else:
            notion.update_page_props(page_id, props)
            print(f"  ✓ {entry.get('uid8', '?')} -> {list(props.keys())}")
        written += 1

    verb = "Would update" if args.dry_run else "Updated"
    print(f"\n{verb} {written}/{len(batch)} rows.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="Pull the next batch of un-enriched rows.")
    fetch.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"Max rows this run (default {DEFAULT_LIMIT}).")
    fetch.add_argument("--window", default="90d", help="OpenAI stored-completions lookback window (default 90d).")
    fetch.add_argument("--out", default=None, help="Write JSON to this path instead of stdout.")
    fetch.set_defaults(func=cmd_fetch)

    write = sub.add_parser("write", help="Write an enriched batch (with signal + vertical filled in) back to Notion.")
    write.add_argument("--input", required=True, help="Path to the JSON batch (edited after `fetch`).")
    write.add_argument("--dry-run", action="store_true", help="Preview without writing to Notion.")
    write.set_defaults(func=cmd_write)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
