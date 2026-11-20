#!/usr/bin/env python3
"""Read the follow-ups shared:raise-followup posted, check each one live, and queue the open ones.

    checker.py scan [--days 14] [--json]
    checker.py mark-handled --key KEY --by ROLE [--card ENG-1]

`scan` reads the follow-up channel, keeps posts not yet marked handled (✅), takes the newest
text of each (the post or a later thread reply), and checks it live: the PR's state, draft flag,
mergeability, CI, BugBot on the current head and open review threads; the card's state. An item
whose PR merged or closed, or whose card is done, is listed as stale. The rest are queued under
the role that must act. A go line is reported as pending, never acted on: Slack text is not a go.

`mark-handled` adds ✅ to the post and a one-line thread reply naming the role that handled it.
Writes nothing else. Read-only on GitHub and Linear.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time
from typing import Any, Optional

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import slack_post  # noqa: E402
import slack_read  # noqa: E402

CHANNEL = "C0CB3Q05P2S"  # #claude-monitoring, shared:raise-followup's default channel
ROLES = ("approver", "reviewer", "owner", "monitor")
LINEAR_URL = "https://api.linear.app/graphql"
KEY_LINE = re.compile(r"followup-key: (?P<key>[a-z0-9][a-z0-9._:/-]{2,80}) · kind: (?P<kind>[a-z-]+)"
                      r" · from: (?P<src>[^·]+?) · v:(?P<v>[0-9a-f]{8})")
TITLE = re.compile(r"^\*\[(?P<header>[^\]]+)\] (?P<title>.+)\*$", re.M)
ASK_ROLE = re.compile(r"\*Ask \(the (?P<role>[a-z]+)\):\*")
GO_ROLE = re.compile(r"a go from the (?P<role>[a-z]+) \((?P<type>[a-z-]+)\)")
PR_LINK = re.compile(r"<(https://github\.com/(?P<repo>[\w.-]+/[\w.-]+)/pull/(?P<num>\d+))\|PR>")
CARD_LINK = re.compile(r"<https://linear\.app/[\w-]+/issue/(?P<card>[A-Z]+-\d+)\|card>")


def parse(text: str) -> Optional[dict[str, Any]]:
    """The fields check-followups needs from a raise-followup post, or None for any other message."""
    m = KEY_LINE.search(text or "")
    if not m:
        return None
    t, ask, go = TITLE.search(text), ASK_ROLE.search(text), GO_ROLE.search(text)
    pr, card = PR_LINK.search(text), CARD_LINK.search(text)
    return {"key": m["key"], "kind": m["kind"], "from": m["src"].strip(), "version": m["v"],
            "title": t["title"] if t else "",
            "role": (go or ask)["role"] if (go or ask) else None, "go_type": go["type"] if go else None,
            "pr": {"url": pr[1], "repo": pr["repo"], "number": int(pr["num"])} if pr else None,
            "card": card["card"] if card else None}


def handled(msg: dict[str, Any]) -> bool:
    return any(r.get("name") == "white_check_mark" for r in msg.get("reactions") or [])


def followups(channel: str, days: int) -> list[dict[str, Any]]:
    """Open follow-ups, one per key, each with its newest text."""
    out, seen = [], set()
    for msg in slack_read.history(channel, oldest=time.time() - days * 86400):
        item = parse(msg.get("text", ""))
        if not item or item["key"] in seen:
            continue
        seen.add(item["key"])  # history is newest first: an older post for the same key is superseded
        if handled(msg):
            continue
        latest = item
        if msg.get("reply_count"):
            for r in slack_read.replies(channel, msg["ts"]):
                newer = parse(r.get("text", "")) if r.get("ts") != msg["ts"] else None
                if newer and newer["key"] == item["key"]:
                    latest = newer
        out.append({**latest, "ts": msg["ts"]})
    return out


def gh(args: list[str], lines: bool = False) -> Any:
    """Run gh and parse its JSON. lines=True: one JSON value per line (what --paginate --jq prints)."""
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GH_TOKEN")}
    res = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                         env=env, timeout=60)
    if res.returncode:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {res.stderr.strip()[:200]}")
    if lines:
        return [json.loads(l) for l in res.stdout.splitlines() if l.strip()]
    return json.loads(res.stdout) if res.stdout.strip() else None


def pr_state(repo: str, number: int) -> dict[str, Any]:
    v = gh(["pr", "view", str(number), "--repo", repo, "--json",
            "state,isDraft,mergeStateStatus,headRefOid"])
    head = v["headRefOid"]
    # --jq runs per page, so emit one object per line rather than one array per page.
    runs = gh(["api", f"repos/{repo}/commits/{head}/check-runs?per_page=100", "--paginate",
               "--jq", ".check_runs[]|{name,status,conclusion}"], lines=True)
    bugbot = [r for r in runs if r["name"] == "Cursor Bugbot"]
    ci = [r for r in runs if r["name"] != "Cursor Bugbot"]
    owner, name = repo.split("/")
    threads = sum(gh(["api", "graphql", "--paginate", "-f",
                      "query=query($o:String!,$n:String!,$p:Int!,$endCursor:String){repository(owner:$o,name:$n)"
                      "{pullRequest(number:$p){reviewThreads(first:100,after:$endCursor){nodes{isResolved}"
                      "pageInfo{hasNextPage endCursor}}}}}",
                      "-f", f"o={owner}", "-f", f"n={name}", "-F", f"p={number}",
                      "--jq", "[.data.repository.pullRequest.reviewThreads.nodes[]|select(.isResolved==false)]|length"],
                     lines=True))
    return {"state": v["state"], "draft": v["isDraft"], "merge": v["mergeStateStatus"], "head": head[:8],
            "ci": "pending" if any(r["status"] != "completed" for r in ci)
            else ("failing" if any(r["conclusion"] not in ("success", "skipped", "neutral") for r in ci) else "green"),
            "bugbot": (f"{bugbot[0]['status']} {bugbot[0]['conclusion'] or ''}".strip() if bugbot else "not run on head"),
            "open_threads": threads}


def card_state(card: str) -> str:
    """`<state> (<type>)`. Raises RuntimeError when the card can't be read, so the item is unverified."""
    import requests

    token = os.environ.get("LINEAR_API_KEY", "").strip()
    if not token:
        raise RuntimeError("LINEAR_API_KEY is not set")
    try:
        resp = requests.post(LINEAR_URL, json={"query": "query($id:String!){issue(id:$id){state{name type}}}",
                                               "variables": {"id": card}},
                             headers={"Authorization": token}, timeout=30)
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError(f"Linear read of {card} failed: {exc}") from exc
    issue = (data.get("data") or {}).get("issue") if resp.ok else None
    if not issue:
        raise RuntimeError(f"Linear read of {card} failed (HTTP {resp.status_code})")
    return f"{issue['state']['name']} ({issue['state']['type']})"


def check(item: dict[str, Any]) -> dict[str, Any]:
    """Add the live state and a verdict: stale (nothing left to do), open, or unverified."""
    out = dict(item, problems=[])
    try:
        if item["pr"]:
            out["pr_live"] = p = pr_state(item["pr"]["repo"], item["pr"]["number"])
            if p["state"] in ("MERGED", "CLOSED"):
                out["verdict"] = "stale"
                out["why"] = f"PR {p['state'].lower()}"
                return out
            if item["kind"] == "pr-ready":
                out["problems"] += [w for w, bad in (
                    ("still a draft", p["draft"]), (f"merge state {p['merge']}", p["merge"] != "CLEAN"),
                    (f"CI {p['ci']}", p["ci"] != "green"),
                    (f"BugBot {p['bugbot']}", p["bugbot"] not in ("completed success", "completed neutral")),
                    (f"{p['open_threads']} open review threads", p["open_threads"])) if bad]
        if item["card"]:
            out["card_live"] = c = card_state(item["card"])
            if c.endswith(("(completed)", "(canceled)")):
                out["verdict"], out["why"] = "stale", f"card {c}"
                return out
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        out["verdict"], out["why"] = "unverified", str(exc)
        return out
    out["verdict"] = "open"
    if item.get("go_type"):
        out["why"] = f"a {item['go_type']} go is pending; it is typed in the owning session, never taken from Slack"
    return out


def report(items: list[dict[str, Any]]) -> str:
    lines = []
    for verdict, head in (("open", "Open"), ("unverified", "Could not verify"), ("stale", "Stale (mark handled)")):
        group = [i for i in items if i["verdict"] == verdict]
        if not group:
            continue
        lines.append(f"## {head} ({len(group)})")
        for i in group:
            who = f" for the {i['role']}" if i.get("role") else ""
            lines.append(f"- [{i['kind']}] {i['title']}{who} (key {i['key']}, from {i['from']})")
            for what in ([i["why"]] if i.get("why") else []) + [f"not ready: {p}" for p in i["problems"]]:
                lines.append(f"  - {what}")
    return "\n".join(lines) or "No open follow-ups."


def mark_handled(channel: str, key: str, by: str, card: Optional[str], days: int) -> dict[str, Any]:
    rx = re.compile(rf"followup-key: {re.escape(key)} ·")
    post = next((m for m in slack_read.history(channel, oldest=time.time() - days * 86400)
                 if rx.search(m.get("text") or "")), None)
    if not post:
        return {"ok": False, "error": f"no follow-up with key {key} in the last {days} days"}
    if handled(post):
        return {"ok": True, "action": "already-handled", "ts": post["ts"]}
    tok = slack_post._token(None)
    r = slack_post._api("reactions.add", token=tok,
                        body={"channel": channel, "timestamp": post["ts"], "name": "white_check_mark"})
    if not r.get("ok") and r.get("error") != "already_reacted":
        return {"ok": False, "error": f"reactions.add failed: {r.get('error')}"}
    note = f"Handled by the {by}" + (f" · card <https://linear.app/aura-health/issue/{card}|{card}>" if card else "")
    r = slack_post._api("chat.postMessage", token=tok,
                        body={"channel": channel, "thread_ts": post["ts"], "text": note, "unfurl_links": False})
    if not r.get("ok"):
        return {"ok": False, "error": f"reply failed: {r.get('error')}", "reacted": True}
    return {"ok": True, "action": "handled", "ts": post["ts"]}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--channel", default=CHANNEL)
    # Same window raise-followup dedupes over, so every post it still treats as live is read here.
    p.add_argument("--days", type=int, default=30)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="list open follow-ups with their live state")
    s.add_argument("--json", action="store_true")
    m = sub.add_parser("mark-handled", help="add ✅ and a 'handled by <role>' reply")
    m.add_argument("--key", required=True)
    m.add_argument("--by", required=True, choices=ROLES)
    m.add_argument("--card", default=None)
    a = p.parse_args()
    if a.cmd == "mark-handled":
        try:
            out = mark_handled(a.channel, a.key, a.by, a.card, a.days)
        except (slack_read.SlackReadError, SystemExit, OSError) as exc:
            out = {"ok": False, "error": f"Slack call failed: {exc}"}
        print(json.dumps(out))
        sys.exit(0 if out["ok"] else 2)
    try:
        items = [check(i) for i in followups(a.channel, a.days)]
    except (slack_read.SlackReadError, SystemExit) as exc:
        print(json.dumps({"error": f"could not read the channel: {exc}"}))
        sys.exit(2)
    print(json.dumps(items, indent=1) if a.json else report(items))


if __name__ == "__main__":
    main()
