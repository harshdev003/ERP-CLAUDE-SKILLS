#!/usr/bin/env python3
"""Find Slack threads where a human is waiting on a reply from the Aura AI bot.

Discovery: `search.messages` for `<@AURA_AI>` over a lookback window (user token —
search rejects bot tokens). Aura AI answers only in public channels it has joined, so a
hit in a DM, group DM or private channel, or in a public channel Aura AI is not a member
of, is counted as `ignored` before any thread read. A hit in one of the `ai-loop-*`
channels, which each have their own loop agent, is counted as `excluded_loop_channel`, also
unread. Every other hit resolves to its thread
root, and the thread is re-read in full with `conversations.replies` using the bot token.

A thread is pending when a human message newer than Aura AI's last message in it is
directed at Aura AI and falls inside the reply window. Directed = it @-mentions Aura AI,
or it @-mentions nobody and Aura AI has already spoken in the thread. Bot messages never
count, so two bots cannot reply to each other forever.

Stateless: Aura AI speaking last is the dedup record. Every thread found ends up in
exactly one bucket, and the counts are printed alongside the pending list.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slack_post import search_messages  # noqa: E402
from slack_read import SlackReadError, _get  # noqa: E402

AURA_AI_USER = "U0ATMPESR37"
AURA_AI_BOT = "B0ATUPK38P6"
MENTION = re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>")
SEARCH_COUNT = 100
TEXT_CAP = 4000
# Messages a person wrote. A screenshot arrives as `file_share`, and an "also send to
# channel" reply as `thread_broadcast`. Every other subtype is a join, an edit tombstone,
# a bot or an app.
HUMAN_SUBTYPES = (None, "thread_broadcast", "file_share", "me_message")
# An untagged "thanks" after Aura AI's answer closes the exchange rather than opening a new
# one. Aura AI has no reactions:write scope, so it cannot ack with an emoji, and replying
# "you're welcome" would invite the next "thanks".
ACK_WORDS = {
    "thanks", "thank", "you", "ty", "thx", "ok", "okay", "k", "great", "perfect", "got", "it",
    "awesome", "cool", "nice", "noted", "sounds", "good", "makes", "sense", "amazing", "much",
    "so", "appreciate", "cheers", "lgtm",
}
SLACK_LINK = re.compile(r"<[^>]*>")
EMOJI_CODE = re.compile(r":[a-z0-9_+\-]+:")

BUCKETS = (
    "pending",
    "skipped_already_answered",
    "skipped_not_directed",
    "skipped_acknowledgement",
    "skipped_stale",
    "skipped_cap",
    "failed",
)
# Conversations Aura AI stays out of. Only the count is reported: nothing in them is a
# failure, and listing them would put private conversations into the run summary.
IGNORED = "ignored"
# What the bot token gets back from a conversation it is not in. `missing_scope` is not
# here: a lost scope would otherwise read as "every channel ignored" and hide the fault.
NOT_A_MEMBER = ("not_in_channel", "channel_not_found")
# Public channels Aura AI belongs to where a dedicated loop agent owns the replies. Keyed by
# id, because a rename must not bring the sweep back into them.
EXCLUDED_CHANNELS = {
    "C0C1JTK3YCC": "ai-loop-aurie-features",
    "C0C0ZSMU7C7": "ai-loop-aura-recommendations",
    "C0C29JPEUQG": "ai-loop-aurie-conversations",
    "C0C0ZSMNZM5": "ai-loop-aura-features",
}
EXCLUDED = "excluded_loop_channel"


def is_aura(msg: dict[str, Any]) -> bool:
    return msg.get("user") == AURA_AI_USER or msg.get("bot_id") == AURA_AI_BOT


def is_human(msg: dict[str, Any]) -> bool:
    if msg.get("bot_id") or msg.get("subtype") not in HUMAN_SUBTYPES:
        return False
    return bool(msg.get("user")) and not is_aura(msg)


def mentions(msg: dict[str, Any]) -> set[str]:
    return set(MENTION.findall(msg.get("text") or ""))


def is_acknowledgement(msg: dict[str, Any]) -> bool:
    if msg.get("files"):
        return False
    text = EMOJI_CODE.sub(" ", (msg.get("text") or "").lower())
    words = re.findall(r"[a-z]+", text)
    return len(words) <= 6 and all(w in ACK_WORDS for w in words)


def asks_question(msg: dict[str, Any]) -> bool:
    """A literal '?' outside links, whose query strings carry one."""
    return "?" in SLACK_LINK.sub(" ", msg.get("text") or "")


def is_directed(msg: dict[str, Any], aura_spoke_before: bool) -> bool:
    tagged = mentions(msg)
    if AURA_AI_USER in tagged:
        return True
    return not tagged and aura_spoke_before


def classify(thread: list[dict[str, Any]], since: float, cap: int) -> tuple[str, list[dict[str, Any]]]:
    """Return (bucket, directed messages awaiting a reply) for one thread."""
    msgs = sorted(thread, key=lambda m: float(m["ts"]))
    aura_ts = [float(m["ts"]) for m in msgs if is_aura(m)]
    last_aura = max(aura_ts, default=0.0)

    waiting = [m for m in msgs if is_human(m) and float(m["ts"]) > last_aura]
    if not waiting:
        return "skipped_already_answered", []

    directed = []
    for m in waiting:
        spoke_before = any(t < float(m["ts"]) for t in aura_ts)
        if is_directed(m, spoke_before):
            directed.append(m)
    if not directed:
        return "skipped_not_directed", []

    # After Aura AI asks something, "ok" is an answer, not a sign-off.
    last_aura_msg = next((m for m in reversed(msgs) if is_aura(m)), None)
    aura_asked = last_aura_msg is not None and asks_question(last_aura_msg)
    tagged_aura = [m for m in directed if AURA_AI_USER in mentions(m)]
    if not aura_asked and not tagged_aura and all(is_acknowledgement(m) for m in directed):
        return "skipped_acknowledgement", directed

    fresh = [m for m in directed if float(m["ts"]) >= since]
    if not fresh:
        return "skipped_stale", directed

    if sum(1 for t in aura_ts if t >= since) >= cap:
        return "skipped_cap", fresh

    return "pending", fresh


def thread_root(match: dict[str, Any]) -> str:
    query = parse_qs(urlparse(match.get("permalink") or "").query)
    return (query.get("thread_ts") or [match["ts"]])[0]


def is_private(channel: dict[str, Any]) -> bool:
    return bool(channel.get("is_im") or channel.get("is_mpim") or channel.get("is_private"))


def bot_token() -> str:
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        raise SlackReadError("SLACK_BOT_TOKEN is not set")
    return token


def is_ignored(channel: str, known: Optional[dict[str, Any]], cache: dict[str, bool]) -> bool:
    """True when Aura AI stays out of this conversation.

    `known` is the channel object from the search match, which already says whether the
    conversation is private. Membership needs `conversations.info` with the bot token, one
    call per channel. The `--threads` path has no channel object and gets both from there.
    """
    if known is not None and is_private(known):
        return True
    if channel not in cache:
        try:
            info = _get("conversations.info", {"channel": channel}, token=bot_token()).get("channel") or {}
        except SlackReadError as exc:
            if not any(e in str(exc) for e in NOT_A_MEMBER):
                raise
            cache[channel] = True
        else:
            cache[channel] = is_private(info) or not info.get("is_member")
    return cache[channel]


def read_thread(channel: str, root: str) -> list[dict[str, Any]]:
    """The whole thread, as Aura AI sees it.

    Bot token only. A user-token read would surface DMs and private channels Aura AI
    cannot post in. Cursor-paginated, because `conversations.replies` is oldest-first and
    a truncated read loses exactly the newest messages classification depends on.
    """
    token = bot_token()
    out: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    while True:
        params: dict[str, Any] = {"channel": channel, "ts": root, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        body = _get("conversations.replies", params, token=token)
        out.extend(body.get("messages", []))
        cursor = (body.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return out


FILE_FIELDS = ("id", "name", "title", "mimetype", "permalink")


def slim(msg: dict[str, Any]) -> dict[str, Any]:
    text = msg.get("text") or ""
    out = {
        "ts": msg["ts"],
        "user": msg.get("user"),
        "bot": bool(msg.get("bot_id")),
        "aura_ai": is_aura(msg),
        "text": text if len(text) <= TEXT_CAP else text[:TEXT_CAP] + " …[truncated]",
    }
    # A caption-less screenshot is only its file, so the replier has to be able to open it.
    if msg.get("files"):
        out["files"] = [{k: f.get(k) for k in FILE_FIELDS if f.get(k)} for f in msg["files"]]
    return out


def parse_since(value: Optional[str]) -> float:
    if not value:
        today = dt.datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        return today.timestamp()
    try:
        return float(value)
    except ValueError:
        day = dt.datetime.strptime(value, "%Y-%m-%d").astimezone()
        return day.timestamp()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", help="Reply window start: epoch seconds or YYYY-MM-DD (local). Default: today 00:00 local.")
    ap.add_argument("--lookback-days", type=int, default=7, help="How far back to discover threads that mention Aura AI (default 7).")
    ap.add_argument("--max-replies-per-thread", type=int, default=5, help="Loop guard: Aura AI replies per thread inside the window (default 5).")
    ap.add_argument("--threads", metavar="CHANNEL:THREAD_TS,...", help="Skip the search and classify these threads (the Slack MCP fallback when SLACK_USER_TOKEN is unset).")
    ap.add_argument("--check", metavar="CHANNEL:THREAD_TS", help="Re-check one thread just before posting. Exit 0 if still pending, 3 if not.")
    args = ap.parse_args()

    since = parse_since(args.since)
    if args.check:
        channel, root = args.check.split(":", 1)
        if channel in EXCLUDED_CHANNELS:
            print(EXCLUDED)
            sys.exit(3)
        bucket, _ = classify(read_thread(channel, root), since, args.max_replies_per_thread)
        print(bucket)
        sys.exit(0 if bucket == "pending" else 3)
    after_day = (dt.datetime.fromtimestamp(since) - dt.timedelta(days=args.lookback_days + 1)).strftime("%Y-%m-%d")
    query = f"<@{AURA_AI_USER}> after:{after_day}"

    # (channel, root) -> (display name, channel object from search or None)
    threads: dict[tuple[str, str], tuple[str, Optional[dict[str, Any]]]] = {}
    if args.threads:
        query = "(--threads)"
        for item in filter(None, args.threads.split(",")):
            channel, root = item.strip().split(":", 1)
            threads.setdefault((channel, root), (channel, None))
        matches, total = [], len(threads)
    else:
        resp = search_messages(query, count=SEARCH_COUNT)
        if not resp.get("ok"):
            sys.exit(f"search.messages failed: {resp.get('error')}")
        block = resp.get("messages") or {}
        matches = block.get("matches") or []
        total = (block.get("paging") or {}).get("total", len(matches))
        for m in matches:
            key = (m["channel"]["id"], thread_root(m))
            threads.setdefault(key, (m["channel"].get("name") or m["channel"]["id"], m["channel"]))

    out: dict[str, Any] = {b: [] for b in BUCKETS}
    ignored = excluded = 0
    membership: dict[str, bool] = {}
    for (channel, root), (name, known) in sorted(threads.items(), key=lambda kv: kv[0][1]):
        ref = {"channel": channel, "channel_name": name, "thread_ts": root}
        if channel in EXCLUDED_CHANNELS:
            excluded += 1
            continue
        try:
            if is_ignored(channel, known, membership):
                ignored += 1
                continue
            thread = read_thread(channel, root)
        except SlackReadError as exc:
            if any(e in str(exc) for e in NOT_A_MEMBER):
                ignored += 1
            else:
                out["failed"].append({**ref, "reason": str(exc)})
            continue
        bucket, directed = classify(thread, since, args.max_replies_per_thread)
        entry = {**ref, "awaiting": [m["ts"] for m in directed]}
        if bucket == "pending":
            entry["thread"] = [slim(m) for m in thread]
        out[bucket].append(entry)

    report = {
        "window": {
            "since": dt.datetime.fromtimestamp(since).astimezone().isoformat(),
            "lookback_days": args.lookback_days,
            "query": query,
        },
        "counts": {
            "mentions_found": len(matches),
            "mentions_total": total,
            "threads": len(threads),
            **{b: len(out[b]) for b in BUCKETS},
            IGNORED: ignored,
            EXCLUDED: excluded,
        },
        "truncated": not args.threads and total > len(matches),
        **out,
    }
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
