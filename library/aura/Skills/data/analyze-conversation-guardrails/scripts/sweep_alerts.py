#!/usr/bin/env python3
"""List every conversation-guardrail alert in a window, answered or not.

Deterministic, read-only. Reads `#guardrails-conversation` history, parses each k8bot
`*Conversation guardrail triggered*` alert, and — for alerts with a thread — reads the
triage reply posted by `eng:investigate-conversation-guardrail` and lifts its
`Trigger reason:` line, its severity (the verdict on the reply's first line) and the
alert's handoff category (the first entry of its `handoffs:` list — the alert fires on the
conversation's first handoff).

Output (stdout, or --out): {"channel", "window": {"oldest", "latest"}, "counts": {...},
"alerts": [{"ts", "user", "transaction", "conversation", "reply_count", "severity", "category",
"reason_line": {"reason", "subject", "asked_for", "in_care"} | null,
"prompt": "<prompt file>" | null}]}.

`prompt` is the alert's `*Prompt:*` value: the experiment-arm prompt file that ran (e.g.
`coach.coachPrompterGuardrails.a.txt`). Intelligence Service prints the line only when an arm
swapped the prompt body, so null means the prompter's base file ran.

Auth: SLACK_BOT_TOKEN; the bot must be a member of the private channel and hold
`groups:history`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

DEFAULT_CHANNEL = "C0C7HEZ7CEA"  # #guardrails-conversation (private)
ALERT_MARKER = "Conversation guardrail triggered"
# Each id runs to the next whitespace: a character class truncates ids with `:`/`-`/`_`/`.`.
UID_RE = re.compile(r"\*User:\*[ \t]*(\S+)")
TX_RE = re.compile(r"\*Transaction:\*[ \t]*(\S+)")
CONV_RE = re.compile(r"\*Conversation:\*[ \t]*(\S+)")
PROMPT_RE = re.compile(r"\*Prompt:\*[ \t]*(\S+)")
REASON_RE = re.compile(
    r"^Trigger reason:[ \t]*(?P<reason>[a-z-]+)"
    r"[ \t]*·[ \t]*subject:[ \t]*(?P<subject>[a-z-]+)"
    r"[ \t]*·[ \t]*asked for:[ \t]*(?P<asked_for>[a-z-]+)"
    r"(?P<in_care>[ \t]*·[ \t]*already in care)?[ \t]*$",
    re.M,
)
# Slack returns emoji in message text as shortcodes; the literal glyph covers a raw post.
HANDOFFS_RE = re.compile(r"handoffs:[ \t]*#\d+[ \t]+([a-z_]+)")
VERDICTS = (("🚨", "crisis"), (":rotating_light:", "crisis"),
            ("☑️", "crisis"), (":ballot_box_with_check:", "crisis"),
            ("✅", "no-active-risk"), (":white_check_mark:", "no-active-risk"),
            ("⚠️", "unknown"), (":warning:", "unknown"),
            ("↩️", "duplicate"), (":leftwards_arrow_with_hook:", "duplicate"))


class SlackError(RuntimeError):
    pass


def _get(method: str, params: dict, token: str) -> dict:
    for attempt in range(5):
        r = requests.get(f"https://slack.com/api/{method}", params=params, timeout=30,
                         headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 429:
            time.sleep(min(int(r.headers.get("Retry-After", "2")), 10))
            continue
        data = r.json()
        if not data.get("ok"):
            raise SlackError(f"{method}: {data.get('error')}")
        return data
    raise SlackError(f"{method}: rate limited")


def history(channel: str, oldest: float, latest: float, token: str) -> list[dict]:
    out, cursor = [], None
    while True:
        params = {"channel": channel, "oldest": f"{oldest:.6f}", "latest": f"{latest:.6f}", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = _get("conversations.history", params, token)
        out.extend(data.get("messages", []))
        cursor = (data.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return out


def parse_alert(msg: dict) -> dict | None:
    text = msg.get("text") or ""
    if ALERT_MARKER not in text:
        return None
    def grab(rx):
        m = rx.search(text)
        return m.group(1) if m else None
    return {"ts": msg["ts"], "user": grab(UID_RE), "transaction": grab(TX_RE),
            "conversation": grab(CONV_RE), "reply_count": msg.get("reply_count", 0),
            "severity": None, "category": None, "reason_line": None,
            "prompt": grab(PROMPT_RE)}


def parse_category(text: str) -> str | None:
    """The alert's handoff category: the first entry of the reply's `handoffs:` list."""
    m = HANDOFFS_RE.search(text)
    return m.group(1) if m else None


def parse_reply(text: str) -> tuple[str | None, dict | None]:
    """The triage severity and the `Trigger reason:` line of one reply, if present.

    Severity is the triage verdict: `crisis` (active risk), `non-crisis`, `false-positive`
    or `unknown` (not analysable). It is judged on the whole conversation.
    """
    first = text.lstrip().split("\n", 1)[0]
    severity = next((v for mark, v in VERDICTS if first.startswith(mark)), None)
    if severity == "no-active-risk":
        severity = "false-positive" if "false positive" in first else "non-crisis"
    m = REASON_RE.search(text)
    line = None
    if m:
        line = {"reason": m["reason"], "subject": m["subject"], "asked_for": m["asked_for"],
                "in_care": bool(m["in_care"])}
    return severity, line


def inherit_duplicate_severity(alerts: list[dict]) -> None:
    """A duplicate pointer (a later alert for an already-alerted conversation) has no
    verdict of its own: the triage lives on the conversation's oldest alert. Copy that
    verdict across so the rollup counts the incident's severity, not `unclassified`.
    Left as `duplicate` when the first thread has no verdict yet."""
    first = {}
    for a in sorted(alerts, key=lambda a: float(a["ts"])):
        if a.get("conversation") and a.get("severity") not in (None, "duplicate"):
            first.setdefault(a["conversation"], a)
    for a in alerts:
        src = first.get(a.get("conversation"))
        if a.get("severity") == "duplicate" and src is not None:
            a["severity"] = src["severity"]
            a["category"] = a["category"] or src.get("category")


def read_thread(channel: str, alert: dict, token: str) -> None:
    data = _get("conversations.replies", {"channel": channel, "ts": alert["ts"], "limit": 50}, token)
    for reply in data.get("messages", [])[1:]:
        text = reply.get("text") or ""
        severity, line = parse_reply(text)
        alert["severity"] = alert["severity"] or severity
        alert["category"] = alert["category"] or parse_category(text)
        alert["reason_line"] = alert["reason_line"] or line


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--channel", default=DEFAULT_CHANNEL)
    ap.add_argument("--days", type=float, default=7.0, help="Trailing window in days (default 7)")
    ap.add_argument("--oldest", type=float, help="Window start, epoch seconds (overrides --days)")
    ap.add_argument("--latest", type=float, help="Window end, epoch seconds (default now)")
    ap.add_argument("--no-replies", action="store_true", help="Skip reading triage replies")
    ap.add_argument("--out", help="Write the JSON here instead of stdout")
    args = ap.parse_args()

    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        print("SLACK_BOT_TOKEN is not set", file=sys.stderr)
        return 2
    latest = args.latest or time.time()
    oldest = args.oldest if args.oldest is not None else latest - args.days * 86400
    try:
        alerts = [a for a in (parse_alert(m) for m in history(args.channel, oldest, latest, token)) if a]
        if not args.no_replies:
            threaded = [a for a in alerts if a["reply_count"]]
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda a: read_thread(args.channel, a, token), threaded))
    except SlackError as exc:
        print(f"Slack read failed: {exc}", file=sys.stderr)
        return 1
    alerts.sort(key=lambda a: float(a["ts"]))
    inherit_duplicate_severity(alerts)
    result = {
        "channel": args.channel,
        "window": {"oldest": oldest, "latest": latest},
        "counts": {
            "alerts": len(alerts),
            "users": len({a["user"] for a in alerts}),
            "conversations": len({a["conversation"] for a in alerts}),
            "unparsed": sum(1 for a in alerts if not a["user"] or not a["conversation"]),
            "with_triage_reply": sum(1 for a in alerts if a["severity"]),
            "with_reason_line": sum(1 for a in alerts if a["reason_line"]),
        },
        "alerts": alerts,
    }
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(json.dumps(result["counts"]))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
