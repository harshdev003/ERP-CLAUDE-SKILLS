#!/usr/bin/env python3
"""Find the #support requests waiting on eng:support-loop, and sort every thread.

Support posts a structured request in #support (a workflow message with *UserID*,
*Assignee*, Submitted by and Tracker Link). This script reads the channel over a
lookback window, keeps the requests assigned to the loop's engineers, and puts every
thread in exactly one bucket:

  pending_new        a request nobody on the loop's side has answered yet
  pending_followup   Aura AI answered, then a person wrote something that needs an answer
  skipped_answered   Aura AI spoke last, or only an acknowledgement came after it
  skipped_engineer   an assignee is already working it in the thread
  skipped_mention    a person tagged Aura AI: shared:reply-to-aura-ai-mentions owns it
  skipped_privacy    an erasure / access request: eng:respond-to-erasure or -sar, never this loop
  skipped_not_ours   assigned to someone outside --assignees
  skipped_cap        Aura AI already replied --max-replies times
  not_request        a channel message that is not a support request (joins, notes)

Two sources, same classification:
  * live:      conversations.history + conversations.replies (SLACK_BOT_TOKEN, bot in channel)
  * --from-json FILE: threads read some other way, e.g. through the Slack MCP when no
    token is set. FILE is a JSON list of {"channel", "ts", "messages": [root, reply, ...]}
    where each message carries Slack's raw fields (user, bot_id, text, ts, files). A message a
    person posted through an app may also carry "author_is_person": true; without it, a
    message with a bot_id is read as a bot's.

A person who posts through an app keeps their own id in `user`, but the message also
carries that app's bot_id. The live read looks each such author up with users.info, so
their reply counts as an answer and a bot's does not.

`--check CHANNEL:TS` re-reads one thread fresh and exits 0 when it is still pending, 3 when
it is not or cannot be read, so a post is never made into a thread someone answered while
the tick was working.

Output is local JSON for the agent. It holds user ids because the investigation needs
them; nothing here posts, and nothing from it may be pasted into a PR, card or log.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

AURA_AI_USER = "U0ATMPESR37"
AURA_AI_BOT = "B0ATUPK38P6"
SUPPORT_CHANNEL = "C7KS8TWAG"
# Ashish and Naveed: the default assignees on Aura app support requests.
DEFAULT_ASSIGNEES = ("U064HNE8Q0Y", "U01CPPQ2MNJ")
PENDING = ("pending_new", "pending_followup")

MENTION_RE = re.compile(r"<@(U[A-Z0-9]+)(?:\|[^>]*)?>")
FIREBASE_UID_RE = re.compile(r"[A-Za-z0-9]{28}")
LINEAR_RE = re.compile(r"\b((?:PROD|ENG|ENGBACK|ENGMOB|ENGWEB|ENGSUP)-\d+)\b")
PR_RE = re.compile(r"https://github\.com/AuraHealth/[A-Za-z0-9_.-]+/pull/\d+")
LINK_RE = re.compile(r"<(https?://[^|>]+)(?:\|[^>]*)?>")
# One sign-off phrase. A message is an acknowledgement when every phrase in it is one of
# these, so "Noted. Thank you!" and "Will do, thanks" count, and "Thanks, but it's still
# broken" does not. "Yes" is left out on purpose: after a question it is an answer.
ACK_PHRASE_RE = re.compile(
    r"(thanks?|thx|ty|thank you|thanks (a lot|so much)|thank you (so much|very much)|"
    r"(much )?appreciated|appreciate it|ok(ay)?|got it|noted|will do|perfect|great|awesome|"
    r"sounds good|cool|done)( (team|all|everyone|so much))?",
    re.I,
)
EMOJI_RE = re.compile(r":[a-z0-9_+-]+:")
# Privacy-law requests have their own runbooks and owner; the loop never takes them.
PRIVACY_RE = re.compile(
    r"\b(gdpr|ccpa|pipeda|njdpa|data privacy act|right to (erasure|delete|be forgotten)|"
    r"subject access|deletion request|delete (my|their|his|her) (account|data)|"
    r"erasure of all|personal (data|information) (you hold|concerning))\b",
    re.I,
)


def is_aura(msg: dict[str, Any]) -> bool:
    return msg.get("user") == AURA_AI_USER or msg.get("bot_id") == AURA_AI_BOT


def is_human(msg: dict[str, Any]) -> bool:
    if is_aura(msg) or msg.get("subtype") in ("bot_message", "channel_join"):
        return False
    if msg.get("bot_id"):
        # Posted through an app: a person keeps their own id in `user`, a bot posts as its
        # bot user. Only the author lookup tells them apart; unknown stays a bot.
        return msg.get("author_is_person") is True
    return bool(msg.get("user"))


def needs_author(msg: dict[str, Any]) -> bool:
    """An app-posted message whose `user` may be a person or a bot user."""
    return bool(msg.get("bot_id") and msg.get("user") and not is_aura(msg)
                and msg.get("subtype") not in ("bot_message", "channel_join"))


def mark_authors(thread: list[dict[str, Any]], is_person) -> list[dict[str, Any]]:
    """Set `author_is_person` on each app-posted message whose author `is_person` knows.

    `is_person(user_id)` returns True for a person, False for a bot user and None when it
    cannot tell; None leaves the message unmarked, so it still reads as a bot's.
    """
    for msg in thread:
        if needs_author(msg) and "author_is_person" not in msg:
            verdict = is_person(msg["user"])
            if verdict is not None:
                msg["author_is_person"] = verdict
    return thread


_PERSON: dict[str, Optional[bool]] = {}


def slack_user_is_person(user_id: str) -> Optional[bool]:
    """users.info on the bot token, once per user per run. None when the lookup fails."""
    if user_id not in _PERSON:
        from slack_read import _get, _token  # live path only: needs SLACK_BOT_TOKEN

        try:
            user = _get("users.info", {"user": user_id}, token=_token(None)).get("user") or {}
        except Exception:  # noqa: BLE001 — an unknown author is read as a bot, never as a person
            user = {}
        _PERSON[user_id] = (not (user.get("is_bot") or user.get("id") == "USLACKBOT")) if user else None
    return _PERSON[user_id]


def mentions(msg: dict[str, Any]) -> set[str]:
    return set(MENTION_RE.findall(msg.get("text") or ""))


def is_acknowledgement(msg: dict[str, Any]) -> bool:
    # A screenshot with no caption is usually "it still looks like this", never a sign-off.
    if msg.get("files"):
        return False
    text = EMOJI_RE.sub("", MENTION_RE.sub("", msg.get("text") or ""))
    phrases = [p.strip() for p in re.split(r"[.,!;:)\-\n]+", text) if p.strip()]
    return all(ACK_PHRASE_RE.fullmatch(p) for p in phrases)


def readable(line: str) -> str:
    """A title line with Slack mentions shown as @name, for the tick summary."""
    return re.sub(r"<@U[A-Z0-9]+\|([^>]+)>", r"@\1", MENTION_RE.sub(lambda m: m.group(0) if "|" in m.group(0) else "@someone", line))


def section(text: str, label: str, stop: tuple[str, ...]) -> str:
    """The text after `label` up to the next label in `stop` (labels may be *bold*)."""
    m = re.search(r"\*?" + re.escape(label) + r"\*?:?", text)
    if not m:
        return ""
    rest = text[m.end():]
    cut = len(rest)
    for s in stop:
        n = re.search(r"\*?" + re.escape(s) + r"\*?", rest)
        if n:
            cut = min(cut, n.start())
    return rest[:cut]


def parse_request(root: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The fields of a support request, or None when the root is not one."""
    text = root.get("text") or ""
    labels = ("UserID", "CoachID", "Assignee", "Submitted by", "Tracker Link")
    structured = "UserID" in text and "Assignee" in text
    if not structured:
        if not is_human(root) or not mentions(root):
            return None
        return {
            "title": readable(text.strip().splitlines()[0])[:120] if text.strip() else "",
            "structured": False,
            "user_id": (FIREBASE_UID_RE.search(text) or [None])[0],
            "coach_id": None,
            "assignees": sorted(mentions(root)),
            "submitted_by": root.get("user"),
            "tracker_link": None,
        }
    first = text.strip().splitlines()[0] if text.strip() else ""
    title = re.sub(r"^(Add Support or Bug:\s*|:\s*)", "", first).strip()[:120]
    uid = FIREBASE_UID_RE.search(section(text, "UserID", labels[1:]))
    coach = FIREBASE_UID_RE.search(section(text, "CoachID", labels[2:]))
    submitted = MENTION_RE.findall(section(text, "Submitted by", labels[4:]))
    tracker = LINK_RE.findall(section(text, "Tracker Link", ()))
    return {
        "title": title,
        "structured": True,
        "user_id": uid.group(0) if uid else None,
        "coach_id": coach.group(0) if coach else None,
        "assignees": sorted(set(MENTION_RE.findall(section(text, "Assignee", labels[3:])))),
        "submitted_by": submitted[0] if submitted else None,
        "tracker_link": tracker[0] if tracker else None,
    }


def classify(thread: list[dict[str, Any]], assignees: set[str], max_replies: int) -> tuple[str, str]:
    """(bucket, reason) for one thread; thread[0] is the root."""
    root, rest = thread[0], thread[1:]
    req = parse_request(root)
    if req is None:
        return "not_request", "no UserID/Assignee and no tagged person"
    whole = "\n".join(m.get("text") or "" for m in thread)
    if PRIVACY_RE.search(whole):
        return "skipped_privacy", "privacy-law request: eng:respond-to-erasure / eng:respond-to-sar"
    if not set(req["assignees"]) & assignees:
        return "skipped_not_ours", "assigned to " + (", ".join(req["assignees"]) or "nobody")
    # Only a tag that skill will answer hands the thread over. It drops every message with a
    # bot_id, so a tag a person posted through an app stays the loop's.
    if any(is_human(m) and not m.get("bot_id") and AURA_AI_USER in mentions(m) for m in thread):
        return "skipped_mention", "Aura AI was tagged: shared:reply-to-aura-ai-mentions owns it"
    aura_idx = [i for i, m in enumerate(rest) if is_aura(m)]
    if len(aura_idx) >= max_replies:
        return "skipped_cap", f"Aura AI already replied {len(aura_idx)} times"
    if aura_idx:
        after = [m for m in rest[aura_idx[-1] + 1:] if is_human(m)]
        if not after or all(is_acknowledgement(m) for m in after):
            return "skipped_answered", "Aura AI spoke last"
        return "pending_followup", f"{len(after)} message(s) after Aura AI's last reply"
    # Anyone other than the person who filed it answering counts, not only the assignees:
    # a lead often answers a request assigned to someone else.
    humans = [m for m in rest if is_human(m)]
    # A message that tags Aura AI asks it something; it does not answer the request. Only a
    # tag posted through an app gets this far, and the mention skill never answers those.
    responders = [m for m in humans
                  if m.get("user") != req["submitted_by"] and AURA_AI_USER not in mentions(m)]
    if responders:
        last = humans[-1]
        if last.get("user") != req["submitted_by"] or is_acknowledgement(last):
            return "skipped_engineer", "a person already answered in the thread"
        return "skipped_engineer", "support followed up and is waiting on the person who answered"
    return "pending_new", "no answer in the thread yet"


def slim(msg: dict[str, Any]) -> dict[str, Any]:
    out = {k: msg[k] for k in ("ts", "user", "bot_id", "author_is_person", "text") if msg.get(k)}
    if msg.get("files"):
        out["files"] = [{k: f.get(k) for k in ("id", "name", "mimetype")} for f in msg["files"]]
    return out


def summarize(channel: str, thread: list[dict[str, Any]], assignees: set[str], max_replies: int) -> dict[str, Any]:
    bucket, reason = classify(thread, assignees, max_replies)
    req = parse_request(thread[0]) or {}
    whole = "\n".join(m.get("text") or "" for m in thread)
    item = {
        "bucket": bucket,
        "reason": reason,
        "channel": channel,
        "thread_ts": thread[0].get("ts"),
        "title": req.get("title"),
        "cards": sorted(set(LINEAR_RE.findall(whole))),
        "prs": sorted(set(PR_RE.findall(whole))),
    }
    if bucket in PENDING:
        item.update({k: req.get(k) for k in ("user_id", "coach_id", "assignees", "submitted_by", "tracker_link")})
        item["replies"] = len(thread) - 1
        item["thread"] = [slim(m) for m in thread]
    return item


def load_threads(args: argparse.Namespace) -> list[tuple[str, list[dict[str, Any]]]]:
    if args.from_json:
        try:
            with open(args.from_json, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            sys.exit(f"cannot read --from-json {args.from_json}: {exc}")
        return [(t.get("channel", args.channel), t["messages"]) for t in data if t.get("messages")]
    from slack_read import history, replies  # live path only: needs SLACK_BOT_TOKEN

    oldest = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.lookback_days)).timestamp()
    roots = [m for m in history(args.channel, oldest=oldest) if not m.get("thread_ts") or m.get("thread_ts") == m.get("ts")]
    out = []
    for root in sorted(roots, key=lambda m: float(m["ts"])):
        thread = replies(args.channel, root["ts"]) if root.get("reply_count") else [root]
        out.append((args.channel, mark_authors(thread or [root], slack_user_is_person)))
    return out


def fetch_thread(channel: str, ts: str) -> list[dict[str, Any]]:
    """One thread, read now through conversations.replies (live path, SLACK_BOT_TOKEN)."""
    from slack_read import replies

    return mark_authors(replies(channel, ts), slack_user_is_person)


def check_bucket(args: argparse.Namespace, assignees: set[str]) -> str:
    """The bucket of one thread, read fresh, for the pre-post re-check.

    Live, it always calls conversations.replies on that one thread: the history sweep's
    reply_count is a snapshot from the start of the tick and misses a reply that landed
    since. Any read failure is `check_failed`, which blocks the post: an unreadable thread
    must never read as still pending.
    """
    channel, _, ts = args.check.partition(":")
    if not channel or not ts:
        return "check_failed"
    if args.from_json:
        thread = next((t for ch, t in load_threads(args) if ch == channel and t[0].get("ts") == ts), None)
    else:
        try:
            thread = fetch_thread(channel, ts)
        except Exception:  # noqa: BLE001 — any failure to read means "do not post"
            return "check_failed"
        # An author the lookup could not place may be a person who already answered.
        if any(needs_author(m) and "author_is_person" not in m for m in thread or []):
            return "check_failed"
    if not thread:
        return "not_found"
    return classify(thread, assignees, args.max_replies)[0]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--channel", default=SUPPORT_CHANNEL)
    p.add_argument("--lookback-days", type=int, default=14)
    p.add_argument("--assignees", default=",".join(DEFAULT_ASSIGNEES),
                   help="comma-separated Slack user ids whose requests the loop takes")
    p.add_argument("--max-replies", type=int, default=3)
    p.add_argument("--from-json", help="classify threads read elsewhere (Slack MCP) instead of the API")
    p.add_argument("--check", metavar="CHANNEL:TS", help="exit 0 if this thread is still pending, else 3")
    args = p.parse_args()
    assignees = {a.strip() for a in args.assignees.split(",") if a.strip()}

    if args.check:
        bucket = check_bucket(args, assignees)
        print(bucket)
        sys.exit(0 if bucket in PENDING else 3)

    threads = load_threads(args)
    items = [summarize(ch, t, assignees, args.max_replies) for ch, t in threads]
    counts: dict[str, int] = {}
    for it in items:
        counts[it["bucket"]] = counts.get(it["bucket"], 0) + 1
    print(json.dumps({
        "channel": args.channel,
        "lookback_days": args.lookback_days,
        "threads_read": len(threads),
        "counts": counts,
        "pending": [it for it in items if it["bucket"] in PENDING],
        "skipped": [it for it in items if it["bucket"] not in PENDING and it["bucket"] != "not_request"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
