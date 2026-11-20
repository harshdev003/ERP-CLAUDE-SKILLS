"""The marks the loop leaves on an idea's Slack thread as its Notion row moves.

A reaction on the parent post says at a glance where the idea stands, without opening
the thread: a build mark once Gate 1 has said yes, a cross once the idea is closed. The
loop adds them itself when the row's Status changes — a person never has to remember
to — and reads its own marks back as marks, never as decisions.

Skill-agnostic on purpose: this file is copied byte-for-byte into every skill that
writes a verdict (see docs/canonical-sources.md), so nothing in it names a skill.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Optional

try:  # Preferred transport — bundles certifi, reuses connections.
    import requests  # type: ignore
    _HAVE_REQUESTS = True
except Exception:  # pragma: no cover - environment without requests
    requests = None  # type: ignore
    _HAVE_REQUESTS = False

# Status -> the reaction that marks it. One mark at a time: landing one takes the
# others off, so a row that moves approved -> rejected shows a cross and nothing else.
MARKS: dict[str, str] = {
    "approved": "rocket",   # 🚀 Gate 1 said build it
    "done": "white_check_mark",  # ✅ its feature card is Done (a TestFlight build carries it)
    "experiment_running": "white_check_mark",  # ✅ live, so its card is long Done
    "rejected": "x",        # ❌ closed at Gate 1
    "abandoned": "x",       # ❌ closed later, without an answer
}
ALL_MARKS = sorted(set(MARKS.values()))

# The loop's own Slack identity (`aura_ai`). A reaction from this user is a mark the
# loop left, and a reader must not take it for a person's signal.
LOOP_BOT_USER = "U0ATMPESR37"

# `…/archives/C0C1JTK3YCC/p1789694236837849` → `("C0C1JTK3YCC", "1789694236.837849")`.
# The `p` form is the ts with the dot removed; the last six digits are the fraction.
_PERMALINK_RE = re.compile(r"/archives/([A-Z0-9]+)/p(\d{10})(\d{6})")


def thread_from_permalink(url: str) -> Optional[tuple[str, str]]:
    """`(channel_id, ts)` from a Slack permalink, or None when it is not one."""
    m = _PERMALINK_RE.search(url or "")
    if not m:
        return None
    return m.group(1), f"{m.group(2)}.{m.group(3)}"


def _call(method: str, token: str, **data: Any) -> dict[str, Any]:
    """One Slack Web API POST. A transport failure comes back as `{"ok": False,
    "error": …}` so the caller has one shape to read.

    HTTPS goes through `requests` (which bundles certifi) when it is importable and
    falls back to `curl` otherwise — the same rule as the Slack readers and posters
    beside this file. `urllib` is never used: on this machine it cannot find the
    macOS CA bundle, and a mark that silently failed on every run would look exactly
    like one nobody asked for.
    """
    url = f"https://slack.com/api/{method}"
    try:
        if _HAVE_REQUESTS:
            resp = requests.post(url, data=data, headers={"Authorization": f"Bearer {token}"},
                                 timeout=20)
            return resp.json()
        cmd = ["curl", "-sS", "-X", "POST", url, "-H", f"Authorization: Bearer {token}"]
        for k, v in data.items():
            cmd += ["--data-urlencode", f"{k}={v}"]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        if out.returncode != 0:
            return {"ok": False, "error": f"transport: curl exit {out.returncode}"}
        return json.loads(out.stdout)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"transport: {exc}"}
    except Exception as exc:  # requests' own exception family
        return {"ok": False, "error": f"transport: {exc}"}


def mark_thread(permalink: str, status: str, token: Optional[str] = None) -> str:
    """Put the mark for `status` on the thread's parent and take the other marks off.

    Returns one line for the log. Never raises: the row's Status is already written
    by the time this runs, and a missing token or scope must not turn a recorded
    decision into a failed command. `already_reacted` and `no_reaction` are the
    idempotent cases — a re-run changes nothing and says so.
    """
    name = MARKS.get(status)
    if not name:
        return f"no mark for '{status}'"
    parsed = thread_from_permalink(permalink)
    if not parsed:
        return "not marked — the row has no Slack thread"
    tok = token or os.environ.get("SLACK_BOT_TOKEN")
    if not tok:
        return "not marked — SLACK_BOT_TOKEN is not set"
    channel, ts = parsed
    out = []
    r = _call("reactions.add", tok, channel=channel, timestamp=ts, name=name)
    if r.get("ok") or r.get("error") == "already_reacted":
        out.append(f":{name}: on")
    else:
        out.append(f":{name}: not added ({r.get('error')})")
    for other in ALL_MARKS:
        if other == name:
            continue
        r = _call("reactions.remove", tok, channel=channel, timestamp=ts, name=other)
        if r.get("ok"):
            out.append(f":{other}: off")
        elif r.get("error") not in (None, "no_reaction"):
            out.append(f":{other}: not removed ({r.get('error')})")
    return ", ".join(out)
