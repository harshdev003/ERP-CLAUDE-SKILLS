#!/usr/bin/env python3
"""Has this user tripped a guardrail before? Read-only, across BOTH guardrail channels.

Aurie has two guardrail alert streams, each triaged by its own skill and each able
to produce a CS outreach draft:

* `#guardrails-memory-ingestion` — "Memory ingestion guardrail triggered"
* `#guardrails-conversation`     — "Conversation guardrail triggered"

The same person can trip both, or one repeatedly, and every alert gets its own
draft. Without a look across both channels a reviewer cannot see that, and CS can
email the same person twice in a day. This script lists the user's earlier alerts
in a trailing window, reads each thread to recover what happened to it, and
renders the lines the triage reply carries.

What an earlier alert's outcome is read from:

* the first **bot** reply on its thread — the triage skill's verdict line
  (`🚨` active-risk draft, `☑️` resources in place, `✅` no active risk,
  `⚠️ … not analysable`), whose emoji Slack returns as shortcodes;
* any reply containing `HIGH PRIORITY` — the @channel page;
* the thread itself — every line that names an earlier draft links to it, so the
  reviewer can read the email that already went out before sending another;
* a `white_check_mark` reaction on the **alert itself** from a human — the
  reviewers' mark that the alert was handled; on an active-risk draft it is the
  mark that the CS email went out.

"Earlier" means strictly older than the alert being triaged (`--before-ts`), so
the alert never counts itself and a later alert is never presented as history.

Duplicates: given `--conversation`, an earlier alert whose body names the same
conversation is the same incident, not history — Intelligence Service can post one
alert per handoff. It is left out of every line and returned as `duplicate_of`, so
the caller can point at the first thread instead of drafting and paging twice.

The once-a-day rule: when an active-risk draft was posted for this user in either
channel within `--email-window-hours` (24 by default), `email_window_hit` is true
and `email_line` names it. The rule is a reviewer note — the caller still
produces its draft. Withholding a draft on a safety path is a decision a person
makes, not this script.

The last-email line: CS's ✅ on an active-risk draft is the only record that an
email went out, so `last_email_line` surfaces the newest one on its own line —
with the date the draft was posted, looked back `--email-days` (90 by default,
longer than the alert list). Slack does not timestamp reactions, so the send
time itself is unknown; the date is the draft's. When the user has earlier alerts
but none marked sent, the line says so rather than disappearing.

Prints one JSON object. Exit 0 when at least one channel was read (inspect
`incomplete` and `errors` — a channel that could not be read is never reported
as "no prior guardrails"), 1 when none could be read or the token is missing.

Auth: SLACK_BOT_TOKEN, a member of both channels; both are private, so the token
needs `groups:history`. Transport prefers `requests`, falls back to `curl`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone

CHANNELS = {
    "C0B1D3JEHLP": "memory-ingestion",  # #guardrails-memory-ingestion
    "C0C7HEZ7CEA": "conversation",      # #guardrails-conversation
}
SLACK_WORKSPACE = "https://auramobile.slack.com"
ALERT_MARKER = "guardrail triggered"
# Whole value up to whitespace: a character class would truncate a `google:…` uid
# and match the wrong account.
UID_RE = re.compile(r"\*User:\*[ \t]*(\S+)")
CONV_RE = re.compile(r"\*Conversation:\*[ \t]*(\S+)")

ACTIVE = "active risk draft"
IN_PLACE = "active risk, resources in place"
DUPLICATE = "duplicate alert"
NO_RISK = "no active risk"
NOT_ANALYSABLE = "not analysable"
UNANSWERED = "not yet triaged"
UNREADABLE = "thread unreadable"
OTHER = "replied (no triage verdict)"


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


def _call(get, method, params, token):
    data = get(method, params, token)
    if not data.get("ok"):
        raise RuntimeError(str(data.get("error")))
    return data


def _history(get, channel, oldest, latest, token):
    messages, cursor = [], None
    while True:
        params = {"channel": channel, "oldest": f"{oldest:.6f}", "limit": 200}
        if latest is not None:
            params["latest"] = latest
        if cursor:
            params["cursor"] = cursor
        data = _call(get, "conversations.history", params, token)
        messages.extend(data.get("messages") or [])
        cursor = (data.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return messages


def classify(replies: list) -> tuple:
    """(outcome, high_priority) from a thread's replies, parent excluded."""
    high = any("HIGH PRIORITY" in (r.get("text") or "") for r in replies)
    verdicts = [r for r in replies if r.get("bot_id") and "HIGH PRIORITY" not in (r.get("text") or "")]
    if not verdicts:
        return (OTHER if replies else UNANSWERED), high
    first = ((verdicts[0].get("text") or "").strip().splitlines() or [""])[0]
    if ":rotating_light:" in first or "🚨" in first:
        return ACTIVE, high
    if ":ballot_box_with_check:" in first or "☑" in first:
        return IN_PLACE, high
    if "duplicate alert" in first:
        return DUPLICATE, high
    if ":white_check_mark:" in first or "✅" in first:
        return NO_RISK, high
    if "not analysable" in first.lower():
        return NOT_ANALYSABLE, high
    return OTHER, high


def marked_handled(parent: dict, bot_users: set) -> bool:
    for reaction in parent.get("reactions") or []:
        if reaction.get("name") == "white_check_mark":
            if any(u not in bot_users for u in reaction.get("users") or []):
                return True
    return False


def find_priors(user, token, *, before_ts=None, days=30.0, now=None,
                channels=None, get=_slack_get, conversation=None):
    now = time.time() if now is None else now
    channels = channels or CHANNELS
    oldest = now - days * 86400
    priors, errors = [], {}
    for channel, label in channels.items():
        try:
            history = _history(get, channel, oldest, before_ts, token)
        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
            errors[label] = str(exc)[:200]
            continue
        for msg in history:
            text = msg.get("text") or ""
            match = UID_RE.search(text)
            if ALERT_MARKER not in text or not match or match.group(1) != user:
                continue
            ts = msg.get("ts") or ""
            if before_ts is not None and float(ts) >= float(before_ts):
                continue
            replies, unreadable = [], False
            if msg.get("reply_count"):
                try:
                    thread = _call(get, "conversations.replies",
                                   {"channel": channel, "ts": ts, "limit": 50}, token)
                    replies = [m for m in thread.get("messages") or [] if m.get("ts") != ts]
                except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
                    errors[f"{label}:{ts}"] = str(exc)[:200]
                    unreadable = True
            outcome, high = (UNREADABLE, False) if unreadable else classify(replies)
            conv = CONV_RE.search(text)
            bot_users = {r.get("user") for r in replies if r.get("bot_id")}
            priors.append({
                "channel": label,
                "channel_id": channel,
                "ts": ts,
                "permalink_ts": "p" + ts.replace(".", ""),
                "permalink": f"{SLACK_WORKSPACE}/archives/{channel}/p{ts.replace('.', '')}",
                "age_hours": round((now - float(ts)) / 3600, 1),
                "outcome": outcome,
                "high_priority": high,
                "marked_handled": marked_handled(msg, bot_users),
                "same_conversation": bool(conversation and conv and conv.group(1) == conversation),
            })
    priors.sort(key=lambda p: float(p["ts"]), reverse=True)
    return priors, errors


def split_duplicates(priors, current_ts=None):
    """(history, duplicate_of).

    Only the OLDEST alert of a conversation is triaged; every later one is a
    duplicate of it, whatever order a sweep reaches them in. So `priors` must be
    read without the `before_ts` cut-off (a sweep walks newest-first: the later
    alert is reached while the oldest is still unanswered, and the oldest is then
    reached with the later one hidden behind `before_ts` — both drafted and paged).

    history: other conversations' alerts strictly older than `current_ts`.
    duplicate_of: the oldest same-conversation alert, when it is older than
    `current_ts`; None when this alert is itself the oldest, or when there is no
    `current_ts` (Mode B has no alert to be a duplicate)."""
    def older(p):
        return current_ts is None or float(p["ts"]) < float(current_ts)
    same = [p for p in priors if p["same_conversation"] and p["ts"] != current_ts]
    first = min(same, key=lambda p: float(p["ts"])) if same else None
    if first is not None and (current_ts is None or not older(first)):
        first = None  # Mode B (no alert ts): nothing to be a duplicate of
    return [p for p in priors if not p["same_conversation"] and older(p)], first


def _age(hours: float) -> str:
    if hours < 1:
        return "<1h ago"
    if hours < 48:
        return f"{int(hours)}h ago"
    return f"{int(hours // 24)}d ago"


def _window(days: float) -> str:
    return f"{int(days)}d" if float(days).is_integer() else f"{days}d"


def _thread(p: dict) -> str:
    """Slack mrkdwn link to an earlier alert's thread, where its draft lives."""
    return f"<{p['permalink']}|open thread>"


def _when(ts: str) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _scoped(errors: dict, in_window: list) -> dict:
    """Errors that bear on a window: every unreadable channel, and only the
    unreadable threads of alerts inside it. The lookup reads the longer of the two
    windows, so a thread failure on a 60-day-old alert must not mark the 30-day
    list incomplete."""
    tss = {p["ts"] for p in in_window}
    return {k: v for k, v in errors.items() if ":" not in k or k.split(":", 1)[1] in tss}


def render_last_email(priors, errors, *, email_days):
    """The standalone `📧 Last CS email` line, or None when there is no history at all."""
    window = _window(email_days)
    in_window = [p for p in priors if p["age_hours"] <= email_days * 24]
    errors = _scoped(errors, in_window)
    sent = [p for p in in_window if p["outcome"] == ACTIVE and p["marked_handled"]]
    incomplete = " · ⚠️ lookup incomplete — check by hand before sending" if errors else ""
    if sent:
        last = sent[0]
        line = (f"📧 *Last CS email:* draft posted {_when(last['ts'])} ({_age(last['age_hours'])}) "
                f"in #guardrails-{last['channel']} — marked sent ✅ ({_thread(last)})")
        if len(sent) > 1:
            line += f" · {len(sent)} emails in {window}"
        pending = [p for p in in_window if p["outcome"] == ACTIVE and not p["marked_handled"]
                   and float(p["ts"]) > float(last["ts"])]
        if pending:
            line += (f" · a newer draft ({_when(pending[0]['ts'])}, "
                     f"#guardrails-{pending[0]['channel']}, {_thread(pending[0])}) is not marked sent")
        return line + incomplete
    if in_window:
        return f"📧 *Last CS email:* none marked sent in {window}" + incomplete
    if errors:
        return "📧 *Last CS email:* unknown — lookup incomplete, check by hand before sending"
    return None


def render(priors, errors, *, days, email_window_hours):
    """The two reply lines, Slack mrkdwn. Categorical only — no content, no full UID."""
    window = _window(days)
    priors = [p for p in priors if p["age_hours"] <= days * 24]
    errors = _scoped(errors, priors)
    if not priors:
        prior_line = f"Prior guardrails ({window}, both channels): none"
    else:
        parts = []
        for p in priors[:5]:
            part = f"{p['channel']} {_age(p['age_hours'])} — {p['outcome']}"
            if p["high_priority"]:
                part += ", paged"
            if p["marked_handled"]:
                part += ", ✅ marked handled" + (" (email sent)" if p["outcome"] == ACTIVE else "")
            parts.append(part)
        more = f" (+{len(priors) - 5} older)" if len(priors) > 5 else ""
        prior_line = (f"Prior guardrails ({window}, both channels): {len(priors)} — "
                      + "; ".join(parts) + more)
    if errors:
        unread = sorted(k for k in errors if ":" not in k)
        detail = (", ".join(unread) + " unreadable") if unread else "some threads unreadable"
        prior_line += f" · ⚠️ lookup incomplete ({detail}) — check by hand before sending"
    recent = [p for p in priors if p["outcome"] == ACTIVE and p["age_hours"] < email_window_hours]
    email_line = None
    if recent:
        last = recent[0]
        status = "marked sent" if last["marked_handled"] else "not yet marked sent"
        email_line = (
            f"⚠️ One CS email per user per day: an active-risk draft for this user was posted "
            f"{_age(last['age_hours'])} in #guardrails-{last['channel']} ({status}, {_thread(last)}). "
            "Send at most one email in 24h — fold this one into it or hold it. "
            "A high-priority case is the reviewer's call: never withhold help in an imminent case."
        )
    return prior_line, email_line, bool(recent)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="List a user's earlier alerts across both guardrail channels.")
    ap.add_argument("--user", required=True, help="Firebase UID from the alert")
    ap.add_argument("--before-ts", default=None,
                    help="ts of the alert being triaged; only strictly older alerts count")
    ap.add_argument("--days", type=float, default=30.0, help="trailing window in days (default 30)")
    ap.add_argument("--email-days", type=float, default=90.0,
                    help="look-back for the last CS email line, in days (default 90)")
    ap.add_argument("--email-window-hours", type=float, default=24.0,
                    help="once-a-day window for CS email (default 24)")
    ap.add_argument("--conversation", default=None,
                    help="conversation id of the alert being triaged; same-conversation alerts are duplicates")
    ap.add_argument("--now", type=float, default=None, help="override current epoch (testing only)")
    args = ap.parse_args()

    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        print(json.dumps({"error": "SLACK_BOT_TOKEN not set"}))
        return 1
    # With --conversation, read past --before-ts too: a newer alert for the same
    # conversation decides nothing, but the oldest one must be visible from both.
    dedupe = bool(args.conversation and args.before_ts)
    priors, errors = find_priors(args.user, token, before_ts=None if dedupe else args.before_ts,
                                 days=max(args.days, args.email_days), now=args.now,
                                 conversation=args.conversation)
    priors, duplicate_of = split_duplicates(priors, args.before_ts if dedupe else None)
    unread = [k for k in errors if ":" not in k]
    channels_read = [label for label in CHANNELS.values() if label not in unread]
    prior_line, email_line, hit = render(priors, errors, days=args.days,
                                         email_window_hours=args.email_window_hours)
    last_email_line = render_last_email(priors, errors, email_days=args.email_days)
    listed = [p for p in priors if p["age_hours"] <= args.days * 24]
    print(json.dumps({
        "user": args.user[:8] + "…",
        "window_days": args.days,
        "channels_read": channels_read,
        "incomplete": bool(errors),
        "errors": errors,
        "email_days": args.email_days,
        "prior_count": len(listed),
        "priors": priors,
        "email_window_hit": hit,
        "duplicate_of": duplicate_of and {
            "permalink": duplicate_of["permalink"],
            "outcome": duplicate_of["outcome"],
            "triaged": duplicate_of["outcome"] not in (UNANSWERED, UNREADABLE, OTHER),
        },
        "last_email_line": last_email_line,
        "prior_line": prior_line,
        "email_line": email_line,
    }, ensure_ascii=False, indent=2))
    return 0 if channels_read else 1


if __name__ == "__main__":
    sys.exit(main())
