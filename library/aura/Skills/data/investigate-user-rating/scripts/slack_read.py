#!/usr/bin/env python3
"""Canonical Slack READ helper for AINative skills.

Companion to ``slack_post.py`` (posting). This is the ONE place the org's Slack
*read* mechanics live: windowed ``conversations.history`` (cursor-paginated), a
single message fetch by ts, and ``conversations.replies`` (thread fetch) — over a
shared 429-retry transport. Skills that read Slack history **copy this file into
their own ``scripts/`` dir** (skills are self-contained — AINative Architecture
Principle 1); the canonical copy lives at
``plugins/shared/skills/read-slack/scripts/slack_read.py``. Edit the canonical
first, then propagate to every copy in the same PR (see AINative ``CLAUDE.md`` →
"Canonical source for ``slack_read.py``").

Deliberately separate from ``slack_post.py``: reading and posting are different
jobs, and reads need cursor pagination + 429 retry that posting doesn't.

What this helper does NOT own: the **channel directory** — that's AI Knowledge →
``Tools/Slack``. It takes channel IDs as inputs.

Two ways to use it
------------------
- **As a library** — ``from slack_read import history, message_by_ts, replies,
  SlackReadError``. Functions return the raw Slack ``messages`` list and raise
  ``SlackReadError`` on a Slack-level error or persistent 429. Best-effort callers
  (e.g. ``eng:harvest-cost-changes``) catch ``SlackReadError`` and degrade; strict
  callers let it propagate.
- **As a CLI** — ``python3 slack_read.py <history|message|replies> …`` (prints the
  messages as JSON; converts ``SlackReadError`` to a clean non-zero exit).

MCP-first
---------
Per best-practices principle 11, prefer the Slack MCP connector when it's available
in the session; this script is the documented fallback for when MCP can't do the job
or isn't reachable (Cowork, cron, headless).

Transport
---------
HTTPS prefers ``requests`` (bundles certifi), falling back to ``curl`` when
``requests`` isn't importable. ``urllib`` is never used — stock macOS Python can't
find the system CA bundle through it (the bug that bit the read scripts originally).

Auth: ``SLACK_BOT_TOKEN`` (env, or ``token=``/``--token``). The bot must be a member
of the channel. Tokens are never printed. Forces UTF-8 stdout.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any, Optional

SLACK_API = "https://slack.com/api"

try:  # Preferred transport — bundles certifi, reuses connections.
    import requests  # type: ignore

    _HAVE_REQUESTS = True
except Exception:  # pragma: no cover - environment without requests
    requests = None  # type: ignore
    _HAVE_REQUESTS = False


class SlackReadError(Exception):
    """A Slack-level read error (ok:false, persistent 429, or transport failure).

    Raised by the public functions so callers can choose to degrade (catch it) or
    abort (let it propagate). The CLI converts it to a clean ``SystemExit``.
    """


# ---------- low-level transport ----------------------------------------------

def _token(explicit: Optional[str], env_var: str = "SLACK_BOT_TOKEN") -> str:
    tok = explicit or os.environ.get(env_var)
    if not tok:
        raise SlackReadError(f"{env_var} not set (pass --token or export it).")
    return tok


def _http_get(
    url: str, params: dict[str, Any], headers: dict[str, str]
) -> tuple[int, dict[str, Any], Optional[float]]:
    """One HTTPS GET. Returns (status_code, parsed_json, retry_after_seconds)."""
    if _HAVE_REQUESTS:
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        retry_after_hdr = resp.headers.get("Retry-After")
        retry_after = float(retry_after_hdr) if retry_after_hdr else None
        try:
            return resp.status_code, resp.json(), retry_after
        except ValueError:
            raise SlackReadError(
                f"Non-JSON response (HTTP {resp.status_code}): {resp.text[:200]!r}"
            )

    # curl fallback — append the HTTP status after the body so we can split it off.
    cmd = ["curl", "-sS", "-G", url]
    for key, value in headers.items():
        cmd += ["-H", f"{key}: {value}"]
    for key, value in (params or {}).items():
        cmd += ["--data-urlencode", f"{key}={value}"]
    cmd += ["-w", "\n%{http_code}"]
    try:
        result = subprocess.run(cmd, capture_output=True, check=True, timeout=30)
    except subprocess.CalledProcessError as exc:
        raise SlackReadError(f"curl failed for {url}: {exc.stderr.decode()}")
    out = result.stdout.decode()
    body_str, _, status_str = out.rpartition("\n")
    try:
        status = int(status_str.strip())
    except ValueError:
        raise SlackReadError(f"Could not parse curl status from {out[-60:]!r}")
    try:
        return status, json.loads(body_str), None
    except json.JSONDecodeError:
        raise SlackReadError(f"Non-JSON response (HTTP {status}): {body_str[:200]!r}")


def _get(
    method: str, params: dict[str, Any], *, token: str, max_attempts: int = 4
) -> dict[str, Any]:
    """A Slack Web API GET with 429 retry. Returns the body; raises on error.

    Mirrors the retry the read scripts hand-rolled: up to ``max_attempts``, backing
    off by ``Retry-After`` (capped at 5s, default 1s) on a 429.
    """
    url = f"{SLACK_API}/{method}"
    headers = {"Authorization": f"Bearer {token}"}
    for _ in range(max_attempts):
        status, body, retry_after = _http_get(url, params, headers)
        if status == 429:
            time.sleep(min(retry_after or 1.0, 5.0))
            continue
        if not body.get("ok"):
            raise SlackReadError(f"Slack {method} failed: {body.get('error')}")
        return body
    raise SlackReadError(f"Slack {method} kept returning HTTP 429.")


# ---------- public API (import these from a consuming skill) -----------------

def history(
    channel: str,
    *,
    token: Optional[str] = None,
    oldest: Optional[float] = None,
    latest: Optional[float] = None,
    limit: int = 200,
    newest: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Messages in a channel (newest first), cursor-paginated. Returns raw dicts.

    ``oldest`` / ``latest`` are epoch seconds (floats); omit either to leave that
    bound open. The caller does its own parsing and filtering.

    ``newest`` caps the result to the most recent N messages: pagination stops once N
    are collected and the result is truncated to N. Use it to faithfully replicate a
    "newest N messages" read (Slack's ``conversations.history limit=N`` semantic)
    instead of sweeping a whole time window — pass it alone (newest N in the channel)
    or with ``oldest`` (newest N within the window). Raises ``SlackReadError`` on a
    Slack error.
    """
    tok = _token(token)
    out: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    while True:
        page = min(limit, newest) if newest is not None else limit
        params: dict[str, Any] = {"channel": channel, "limit": page}
        if oldest is not None:
            params["oldest"] = f"{float(oldest):.6f}"
        if latest is not None:
            params["latest"] = f"{float(latest):.6f}"
        if cursor:
            params["cursor"] = cursor
        body = _get("conversations.history", params, token=tok)
        out.extend(body.get("messages", []))
        if newest is not None and len(out) >= newest:
            return out[:newest]
        cursor = (body.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return out


def message_by_ts(channel: str, ts: str, *, token: Optional[str] = None) -> list[dict[str, Any]]:
    """Fetch the single message at ``ts`` (inclusive). Returns a 0- or 1-element list."""
    tok = _token(token)
    body = _get(
        "conversations.history",
        {"channel": channel, "latest": ts, "oldest": ts, "inclusive": "true", "limit": 1},
        token=tok,
    )
    return body.get("messages", [])


def replies(channel: str, ts: str, *, token: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
    """Fetch a thread's messages (parent at index 0, then replies). No pagination."""
    tok = _token(token)
    body = _get(
        "conversations.replies",
        {"channel": channel, "ts": ts, "limit": limit},
        token=tok,
    )
    return body.get("messages", [])


# ---------- CLI --------------------------------------------------------------

def cmd_history(args: argparse.Namespace) -> int:
    msgs = history(
        args.channel, token=args.token,
        oldest=args.oldest, latest=args.latest, limit=args.limit, newest=args.newest,
    )
    print(json.dumps(msgs, indent=2, ensure_ascii=False))
    return 0


def cmd_message(args: argparse.Namespace) -> int:
    msgs = message_by_ts(args.channel, args.ts, token=args.token)
    print(json.dumps(msgs, indent=2, ensure_ascii=False))
    return 0


def cmd_replies(args: argparse.Namespace) -> int:
    msgs = replies(args.channel, args.ts, token=args.token, limit=args.limit)
    print(json.dumps(msgs, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Canonical Slack read helper.")
    parser.add_argument("--token", default=None, help="Override the token from env.")
    sub = parser.add_subparsers(dest="command", required=True)

    hist = sub.add_parser("history", help="Windowed conversations.history (paginated).")
    hist.add_argument("--channel", required=True, help="Channel ID (C…).")
    hist.add_argument("--oldest", type=float, default=None, help="Oldest epoch seconds (inclusive bound).")
    hist.add_argument("--latest", type=float, default=None, help="Latest epoch seconds.")
    hist.add_argument("--limit", type=int, default=200, help="Page size (default 200).")
    hist.add_argument("--newest", type=int, default=None, help="Cap to the most recent N messages (stops paginating — faithful 'newest N' read).")
    hist.set_defaults(func=cmd_history)

    msg = sub.add_parser("message", help="Fetch one message by ts (inclusive).")
    msg.add_argument("--channel", required=True, help="Channel ID (C…).")
    msg.add_argument("--ts", required=True, help="Message ts.")
    msg.set_defaults(func=cmd_message)

    rep = sub.add_parser("replies", help="Fetch a thread (conversations.replies).")
    rep.add_argument("--channel", required=True, help="Channel ID (C…).")
    rep.add_argument("--ts", required=True, help="Parent message ts.")
    rep.add_argument("--limit", type=int, default=100, help="Max replies (default 100).")
    rep.set_defaults(func=cmd_replies)

    return parser


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    try:
        raise SystemExit(args.func(args))
    except SlackReadError as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
