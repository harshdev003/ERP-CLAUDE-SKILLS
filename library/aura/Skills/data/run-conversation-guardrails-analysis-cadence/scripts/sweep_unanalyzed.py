#!/usr/bin/env python3
"""Sweep #guardrails-conversation for unanalyzed conversation guardrail alerts.

Deterministic, read-only. Lists k8bot "Conversation guardrail triggered" alerts in a
trailing window with no reply from the analysis bot yet — the dedup is Slack thread
state, not a local watermark. A human reply does not count as handled. Emits JSON on stdout for the orchestrating
`data:run-conversation-guardrails-analysis-cadence` skill to feed into
`eng:investigate-conversation-guardrail`.

Adapted from `data:run-guardrails-analysis-cadence`'s sweep (memory-ingestion alerts): same
transport and dedup, plus the `*Conversation:*` field and a structured `not_in_channel` error.

Auth: SLACK_BOT_TOKEN (bot must be a member of the channel). Transport prefers `requests`,
falls back to `curl` (never urllib) — same convention as the shared Slack helpers.

Stdlib + requests/curl only. No cross-skill imports (self-contained per Architecture principle 1).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

DEFAULT_CHANNEL = "C0C7HEZ7CEA"  # #guardrails-conversation (workspace auramobile)
ALERT_MARKER = "Conversation guardrail triggered"
# Each value runs to the next whitespace / end of line. A character class such as
# `[A-Za-z0-9]+` silently truncates an id containing `:`, `-`, `_` or `.` (a
# provider-prefixed `google:…` uid, a non-UUID transaction), and every later lookup
# then runs against the wrong account.
UID_RE = re.compile(r"\*User:\*[ \t]*(\S+)")
TX_RE = re.compile(r"\*Transaction:\*[ \t]*(\S+)")
CONV_RE = re.compile(r"\*Conversation:\*[ \t]*(\S+)")


def _slack_get(method, params, token):
    """GET a Slack Web API method. Prefer requests, fall back to curl."""
    base = "https://slack.com/api/" + method
    headers = {"Authorization": "Bearer " + token}
    try:
        import requests  # noqa: WPS433 (optional dependency)

        resp = requests.get(base, headers=headers, params=params, timeout=30)
        return resp.json()
    except ImportError:
        pass

    # curl fallback
    import urllib.parse

    url = base + "?" + urllib.parse.urlencode(params)
    out = subprocess.run(
        ["curl", "-sS", "-H", "Authorization: Bearer " + token, url],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=40,
    )
    if out.returncode != 0:
        raise RuntimeError("curl failed: " + out.stderr.strip())
    return json.loads(out.stdout)


def _bot_id(token, override=None):
    """The analysis bot's user id — the only reply that marks an alert handled."""
    if override:
        return override
    data = _slack_get("auth.test", {}, token)
    if not data.get("ok") or not data.get("user_id"):
        raise RuntimeError("auth.test failed: " + str(data.get("error")))
    return data["user_id"]


def _bot_replied(message, channel, bot, token):
    """True when the analysis bot has replied on this alert's thread.

    Any other reply — a reviewer asking why nobody followed up — must not hide the
    alert. `reply_users` on the parent answers it without an extra call, but Slack
    caps that list at five users (`reply_users_count` holds the true total), so a
    list that omits the bot is only trusted when it is complete; otherwise read the
    thread.
    """
    if not message.get("reply_count"):
        return False
    users = message.get("reply_users")
    if users is not None:
        if bot in users:
            return True
        if len(users) >= int(message.get("reply_users_count") or len(users)):
            return False
    data = _slack_get("conversations.replies", {"channel": channel, "ts": message["ts"], "limit": 200}, token)
    if not data.get("ok"):
        raise RuntimeError("Slack error: " + str(data.get("error")))
    return any(m.get("user") == bot and m.get("ts") != message["ts"] for m in data.get("messages", []))


def _history(channel, oldest, token):
    """Cursor-paginate conversations.history from `oldest` to now."""
    messages = []
    cursor = None
    while True:
        params = {"channel": channel, "oldest": f"{oldest:.6f}", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = _slack_get("conversations.history", params, token)
        if not data.get("ok"):
            raise RuntimeError("Slack error: " + str(data.get("error")))
        messages.extend(data.get("messages", []))
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return messages


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--channel", default=DEFAULT_CHANNEL, help="Slack channel ID (default: #guardrails-conversation)")
    ap.add_argument("--days", type=float, default=7.0, help="Trailing window in days (default: 7)")
    ap.add_argument("--now", type=float, default=None, help="Override current epoch (testing only)")
    ap.add_argument("--bot-id", default=None, help="Analysis bot user id (default: resolved via auth.test)")
    args = ap.parse_args()

    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        print(json.dumps({"error": "SLACK_BOT_TOKEN not set"}))
        sys.exit(1)

    now = args.now if args.now is not None else time.time()
    oldest = now - args.days * 86400

    try:
        # Without the bot id the check is blind: every thread would read as unhandled.
        bot = _bot_id(token, args.bot_id)
        messages = _history(args.channel, oldest, token)
    except RuntimeError as exc:
        # not_in_channel is the expected first-run failure: the aura_ai bot has to be
        # invited to the channel by a human. Report it as data, not a traceback.
        print(json.dumps({"error": str(exc), "channel": args.channel}))
        sys.exit(1)
    alerts = [m for m in messages if ALERT_MARKER in m.get("text", "")]

    unanalyzed = []
    for m in alerts:
        if _bot_replied(m, args.channel, bot, token):
            continue
        text = m.get("text", "")
        uid_match = UID_RE.search(text)
        tx_match = TX_RE.search(text)
        conv_match = CONV_RE.search(text)
        ts = m.get("ts", "")
        unanalyzed.append(
            {
                "ts": ts,
                "human_replies": bool(m.get("reply_count")),
                "user": uid_match.group(1) if uid_match else None,
                "transaction": tx_match.group(1) if tx_match else None,
                "conversation": conv_match.group(1) if conv_match else None,
                # p-slug form for building a Slack permalink (thread_ts == ts)
                "permalink_ts": "p" + ts.replace(".", "") if ts else None,
            }
        )

    print(
        json.dumps(
            {
                "channel": args.channel,
                "window_days": args.days,
                "total_messages": len(messages),
                "alerts": len(alerts),
                "unanalyzed": unanalyzed,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
