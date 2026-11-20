#!/usr/bin/env python3
"""Is a #guardrails-conversation alert thread still unanswered? Read-only.

The pre-post re-check for SKILL.md Step 6. The cadence's sweep snapshot is taken
minutes before the reply is posted (a whole-conversation read sits in between),
so two overlapping ticks can both see `reply_count == 0` and both post a draft —
and a page, and the account email — on the same crisis alert. Re-reading the
thread immediately before posting narrows that window to seconds; the post is
skipped when the analysis bot has already replied. A human reply (a reviewer asking
why nobody followed up) does not count — it must not hide the alert.

Prints `{"channel", "ts", "reply_count", "has_reply"}` — `has_reply` means a reply
from the analysis bot. Exit 0 when the read
worked (inspect `has_reply`), 1 when it did not — a failed re-check is NOT "no
reply": the caller must not post blind on an unreadable thread, it reports the
failure so the next tick retries.

Auth: SLACK_BOT_TOKEN. `#guardrails-conversation` is a private channel, so the
bot token needs `groups:history` (and `groups:read`) and the bot must be a member.
Transport prefers `requests`, falls back to `curl`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse


def _slack_get(method: str, params: dict, token: str) -> dict:
    base = "https://slack.com/api/" + method
    headers = {"Authorization": "Bearer " + token}
    try:
        import requests  # noqa: WPS433 (optional dependency)

        return requests.get(base, headers=headers, params=params, timeout=30).json()
    except ImportError:
        pass
    out = subprocess.run(
        ["curl", "-sS", "-H", "Authorization: Bearer " + token,
         base + "?" + urllib.parse.urlencode(params)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40,
    )
    if out.returncode != 0:
        raise RuntimeError("curl failed: " + out.stderr.strip())
    return json.loads(out.stdout)


def bot_id(token: str, get=_slack_get) -> str:
    """The analysis bot's user id. Raises RuntimeError when it cannot be resolved."""
    data = get("auth.test", {}, token)
    if not data.get("ok") or not data.get("user_id"):
        raise RuntimeError("auth.test failed: " + str(data.get("error")))
    return data["user_id"]


def thread_state(channel: str, ts: str, token: str, bot: str, get=_slack_get) -> dict:
    """Whether `bot` has replied on the thread rooted at `ts`. Raises RuntimeError on a Slack error."""
    data = get("conversations.replies", {"channel": channel, "ts": ts, "limit": 200}, token)
    if not data.get("ok"):
        raise RuntimeError("Slack error: " + str(data.get("error")))
    messages = data.get("messages") or []
    parent = next((m for m in messages if m.get("ts") == ts), messages[0] if messages else {})
    replies = [m for m in messages if m.get("ts") != ts]
    count = max(int(parent.get("reply_count") or 0), len(replies))
    bot_replied = bot in (parent.get("reply_users") or []) or any(m.get("user") == bot for m in replies)
    return {"channel": channel, "ts": ts, "reply_count": count, "has_reply": bot_replied}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Re-check an alert thread for replies before posting.")
    ap.add_argument("--channel", required=True, help="Slack channel ID")
    ap.add_argument("--ts", required=True, help="Alert message ts (thread root), e.g. 1779144885.524169")
    ap.add_argument("--bot-id", default=None, help="Analysis bot user id (default: resolved via auth.test)")
    args = ap.parse_args()
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        print(json.dumps({"error": "SLACK_BOT_TOKEN not set"}))
        return 1
    try:
        state = thread_state(args.channel, args.ts, token, args.bot_id or bot_id(token))
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"error": str(exc)[:200], "channel": args.channel, "ts": args.ts}))
        return 1
    print(json.dumps(state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
