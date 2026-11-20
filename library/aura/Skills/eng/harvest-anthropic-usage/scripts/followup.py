#!/usr/bin/env python3
"""Raise one follow-up: a self-contained, role-neutral Slack post plus, by kind, a Linear card.

Where it goes and who acts on it come from ../references/routing.json, never from the caller.
One post per dedupe key: changed text is a reply in that post's thread, unchanged text does nothing.
Messages are never edited or deleted (tests/test_slack_never_edits.py).

    followup.py raise --kind go-needed --title ... --what ... --why ... --ask ... \\
        --go-line ... --go-session ... --go-type release --key ... [--pr URL] [--card ENG-1] [--dry-run]

Prints one JSON object: {"action", "permalink", "ts", "channel", "card", "card_action"}.
Exit codes: 0 done, 1 refused (the post breaks a rule; nothing was written), 2 a Slack or
Linear call failed (the JSON says how far it got; re-running with the same key is safe).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
import time
from typing import Any, Optional

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import slack_post  # noqa: E402
import slack_read  # noqa: E402

CONFIG = HERE.parent / "references" / "routing.json"
SKILL_ID = "shared:raise-followup"
LINEAR_URL = "https://api.linear.app/graphql"
TEXT_LIMIT = 3500
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._:/-]{2,80}$")

URL = re.compile(r"<?https?://[^\s>|]+(?:\|[^>]*)?>?")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
IPV6 = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){3,}[0-9A-Fa-f:]*\b")
TOKEN = re.compile(r"\b(?:xox[abposr]-[\w-]+|sk-[\w-]{20,}|gh[pousr]_\w{20,}|github_pat_\w+|ntn_\w{20,}"
                   r"|lin_api_\w+|AIza[\w-]{30,}|Bearer\s+\S{16,})")
FULL_ID = re.compile(r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{20,}\b")
VAGUE = re.compile(r"\b(as discussed|as mentioned|as agreed|see above|see below|as before|"
                   r"per our (?:chat|conversation|discussion)|like last time)\b", re.I)


def load_config(path: pathlib.Path = CONFIG) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def people(cfg: dict[str, Any]) -> list[str]:
    return sorted({p for r in cfg["roles"].values() for p in r.get("filled_by", [])})


def role_label(cfg: dict[str, Any], role: Optional[str]) -> str:
    return cfg["roles"][role]["label"] if role else ""


TEXT_FIELDS = ("title", "what", "why", "ask", "risk", "rollback", "go_line", "go_session", "session")


def problems(f: dict[str, Any], cfg: dict[str, Any]) -> list[str]:
    """Every rule the follow-up breaks. Empty means it may be posted."""
    out: list[str] = []
    kind = cfg["kinds"].get(f.get("kind") or "")
    if not kind:
        return [f"kind must be one of {', '.join(cfg['kinds'])}"]
    need = ["title", "what", "key", "session"] + ([] if f["kind"] == "fyi" else ["why", "ask"]) + kind["requires"]
    out += [f"{n} is required for {f['kind']}" for n in need if not (f.get(n) or "").strip()]
    if f.get("key") and not KEY_RE.match(f["key"]):
        out.append("key must be 3-81 chars of lowercase letters, digits and . _ : / -")
    if f.get("for") and f["for"] not in cfg["roles"]:
        out.append(f"for must be a role: {', '.join(cfg['roles'])}")
    go = [n for n in ("go_line", "go_session", "go_type") if (f.get(n) or "").strip()]
    if go and len(go) < 3:
        out.append("go_line, go_session and go_type go together: give all three or none")
    if f.get("go_type") and f["go_type"] not in cfg["authority"]:
        out.append(f"go_type must be one of {', '.join(cfg['authority'])}")
    names = [re.compile(rf"\b{re.escape(n)}\b", re.I) for n in people(cfg)]
    for name in TEXT_FIELDS:
        text = f.get(name) or ""
        if not text:
            continue
        if any(rx.search(text) for rx in names):
            out.append(f"{name} names a person; name the role instead (approver, reviewer, owner)")
        if VAGUE.search(text):
            out.append(f"{name} points at context the reader doesn't have ({VAGUE.search(text).group(0)!r}); say it")
        for label, rx in (("an email", EMAIL), ("a token", TOKEN)):
            if rx.search(text):
                out.append(f"{name} contains {label}")
        bare = URL.sub(" ", text)
        for label, rx in (("an IP address", IPV4), ("an IP address", IPV6), ("a full id (use a prefix)", FULL_ID)):
            if rx.search(bare):
                out.append(f"{name} contains {label}")
    for name in ("pr", "card_url"):
        if f.get(name) and not f[name].startswith("https://"):
            out.append(f"{name} must be an https link")
    return sorted(set(out))


def render(f: dict[str, Any], cfg: dict[str, Any]) -> str:
    """The post body, without the footer. The last line carries the key and a content hash."""
    kind = cfg["kinds"][f["kind"]]
    role = f.get("for") or kind["for"]
    lines = [f"*[{kind['header']}] {f['title']}*", f"*What:* {f['what']}"]
    if f.get("why"):
        lines.append(f"*Why:* {f['why']}")
    if f.get("ask"):
        lines.append((f"*Ask ({role_label(cfg, role)}):* " if role else "*Ask:* ") + f["ask"])
    if f.get("go_line") and f.get("go_type") in cfg["authority"]:
        who = role_label(cfg, cfg["authority"][f["go_type"]])
        lines.append(f"*Needs:* a go from {who} ({f['go_type']}), typed in the {f['go_session']} session:")
        lines.append(f"```{f['go_line']}```")
    if f.get("risk"):
        lines.append(f"*Risk:* {f['risk']}")
    if f.get("rollback"):
        lines.append(f"*Rollback:* {f['rollback']}")
    links = [f"<{f['pr']}|PR>" if f.get("pr") else "", f"<{f['card_url']}|card>" if f.get("card_url") else ""]
    if any(links):
        lines.append("*Links:* " + " · ".join(l for l in links if l))
    body = "\n".join(lines)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:8]
    return f"{body}\n_followup-key: {f['key']} · kind: {f['kind']} · from: {f['session']} · v:{digest}_"


def key_marker(key: str) -> re.Pattern:
    return re.compile(rf"followup-key: {re.escape(key)} ·")


def version_of(text: str) -> Optional[str]:
    m = re.search(r"· v:([0-9a-f]{8})_", text or "")
    return m.group(1) if m else None


def find_post(channel: str, key: str, window_days: int) -> Optional[dict[str, Any]]:
    """The newest post in the window carrying this key, or None."""
    rx = key_marker(key)
    for m in slack_read.history(channel, oldest=time.time() - window_days * 86400):
        if rx.search(m.get("text") or ""):
            return m
    return None


def latest_version(channel: str, post: dict[str, Any]) -> Optional[str]:
    """Version of the newest text for this post: the post itself, or a later reply carrying the key."""
    latest = version_of(post.get("text", ""))
    if post.get("reply_count"):
        for r in slack_read.replies(channel, post["ts"]):
            if r.get("ts") != post["ts"] and version_of(r.get("text", "")):
                latest = version_of(r["text"])
    return latest


def handled(msg: dict[str, Any]) -> bool:
    return any(r.get("name") == "white_check_mark" for r in msg.get("reactions") or [])


def slack(method: str, body: dict[str, Any]) -> dict[str, Any]:
    resp = slack_post._api(method, token=slack_post._token(None), body=body)
    if not resp.get("ok"):
        raise RuntimeError(f"Slack {method} failed: {resp.get('error')}")
    return resp


def linear(query: str, variables: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    import requests

    token = os.environ.get("LINEAR_API_KEY", "").strip()
    if not token:
        raise RuntimeError("LINEAR_API_KEY is not set")
    resp = requests.post(LINEAR_URL, json={"query": query, "variables": variables or {}},
                         headers={"Authorization": token, "Content-Type": "application/json"}, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"Linear HTTP {resp.status_code}")
    data = resp.json()
    if data.get("errors"):
        raise RuntimeError(f"Linear error: {data['errors'][0].get('message')}")
    return data["data"]


def card_for_key(key: str) -> Optional[dict[str, Any]]:
    """The open card that already carries this key, if any."""
    # `contains` is a prefix match on the key: ainative-pr-1 also finds ainative-pr-1587's card,
    # so read every page and keep the exact match.
    exact = re.compile(rf"followup-key: {re.escape(key)}(?![A-Za-z0-9._:/-])")
    after = None
    while True:
        d = linear("""query($m: String!, $after: String) { issues(first: 50, after: $after, filter: {
                     description: { contains: $m }, state: { type: { nin: ["completed", "canceled"] } } }) {
                     nodes { id identifier url description } pageInfo { hasNextPage endCursor } } }""",
                   {"m": f"followup-key: {key}", "after": after})["issues"]
        for n in d["nodes"]:
            if exact.search((n.get("description") or "").replace("\\", "")):
                return n
        if not d["pageInfo"]["hasNextPage"]:
            return None
        after = d["pageInfo"]["endCursor"]


def card_by_ref(ref: str) -> dict[str, Any]:
    d = linear("query($id: String!) { issue(id: $id) { id identifier url } }", {"id": ref})
    return d["issue"]


def file_card(f: dict[str, Any], cfg: dict[str, Any], body: str, permalink: str) -> dict[str, Any]:
    team_key = cfg["defaults"]["linear_team_key"]
    team = linear("query($k: String!) { teams(filter: { key: { eq: $k } }) { nodes { id } } }",
                  {"k": team_key})["teams"]["nodes"]
    if not team:
        raise RuntimeError(f"Linear team {team_key} not found")
    inp: dict[str, Any] = {"teamId": team[0]["id"], "title": f"[Follow-up] {f['title']}",
                           "description": f"{to_markdown(body)}\n\nPosted: {permalink}\n\nfollowup-key: {f['key']}"}
    parent = f.get("parent") or cfg["defaults"].get("linear_parent")
    if parent:
        inp["parentId"] = card_by_ref(parent)["id"]
    d = linear("mutation($i: IssueCreateInput!) { issueCreate(input: $i) { issue { id identifier url } } }",
               {"i": inp})
    return d["issueCreate"]["issue"]


def comment(card: dict[str, Any], text: str) -> None:
    linear("mutation($i: String!, $b: String!) { commentCreate(input: { issueId: $i, body: $b }) { success } }",
           {"i": card["id"], "b": text})


def to_markdown(body: str) -> str:
    """Slack mrkdwn to Linear markdown, for the card copy of the post."""
    body = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"[\2](\1)", body)
    return re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"**\1**", body)


def raise_followup(f: dict[str, Any], cfg: dict[str, Any], dry_run: bool = False) -> tuple[int, dict[str, Any]]:
    bad = problems(f, cfg)
    if bad:
        return 1, {"action": "refused", "problems": bad}
    body = render(f, cfg)
    text = f"{body}\n\n{slack_post.build_footer(SKILL_ID, runner='')}"
    if len(text) > TEXT_LIMIT:
        return 1, {"action": "refused", "problems": [f"post is {len(text)} chars; keep it under {TEXT_LIMIT}"]}
    channel = f.get("channel") or cfg["defaults"]["channel"]
    mode = cfg["kinds"][f["kind"]]["card"]
    if dry_run:
        return 0, {"action": "dry-run", "channel": channel, "card_mode": mode, "text": text}

    out: dict[str, Any] = {"channel": channel, "card": None, "card_action": "none"}
    try:
        old = find_post(channel, f["key"], cfg["defaults"]["dedupe_window_days"])
        if old and latest_version(channel, old) == version_of(body):
            out.update(action="unchanged", ts=old["ts"])
        elif old and not handled(old):
            # Posts in this channel are never edited: the new text is a reply in the post's thread.
            resp = slack("chat.postMessage", {"channel": channel, "thread_ts": old["ts"], "text": text,
                                              "unfurl_links": False, "unfurl_media": False})
            out.update(action="replied", ts=resp["ts"], thread_ts=old["ts"])
        else:
            resp = slack("chat.postMessage", {"channel": channel, "text": text, "unfurl_links": False,
                                              "unfurl_media": False})
            out.update(action="posted" if not old else "reposted", ts=resp["ts"])
        out["permalink"] = slack_post.get_permalink(channel, out["ts"]).get("permalink")
    except (RuntimeError, SystemExit, slack_read.SlackReadError) as exc:
        out.update(action="failed", error=str(exc))
        return 2, out

    if mode == "none":
        return 0, out
    note = f"Follow-up {out['action']} in {cfg['defaults']['channel_name']}: {out['permalink']}\n\nfollowup-key: {f['key']}"
    try:
        card = card_by_ref(f["card"]) if f.get("card") else (card_for_key(f["key"]) if mode == "file" else None)
        if out["action"] == "unchanged":
            # Nothing new to say; only file the card an earlier run failed to file.
            if card:
                out.update(card=card["identifier"], card_action="none")
            elif mode == "file":
                card = file_card(f, cfg, body, out["permalink"])
                out.update(card=card["identifier"], card_action="filed")
        elif card:
            comment(card, note)
            out.update(card=card["identifier"], card_action="commented")
        elif mode == "file":
            card = file_card(f, cfg, body, out["permalink"])
            out.update(card=card["identifier"], card_action="filed")
    except (RuntimeError, ImportError) as exc:
        out.update(card_action="failed", error=str(exc))
        return 2, out
    return 0, out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("raise", help="post (or update) one follow-up")
    r.add_argument("--kind", required=True)
    r.add_argument("--title", required=True)
    r.add_argument("--what", required=True)
    r.add_argument("--why", default="")
    r.add_argument("--ask", default="")
    r.add_argument("--for", dest="for_", default=None, help="role that must act (approver, reviewer, owner)")
    r.add_argument("--go-line", default="")
    r.add_argument("--go-session", default="")
    r.add_argument("--go-type", default="", help="release, prod-change, security or merge")
    r.add_argument("--risk", default="")
    r.add_argument("--rollback", default="")
    r.add_argument("--pr", default="", help="PR link")
    r.add_argument("--card", default="", help="Linear card id this follow-up is about (gets a comment)")
    r.add_argument("--parent", default="", help="parent card for a filed card (overrides the config)")
    r.add_argument("--key", required=True, help="dedupe key: one post per key")
    r.add_argument("--session", default=os.environ.get("AINATIVE_SESSION_NAME", ""),
                   help="the session or skill raising it (default AINATIVE_SESSION_NAME)")
    r.add_argument("--dry-run", action="store_true", help="print the post and exit; writes nothing")
    sub.add_parser("check-config", help="validate routing.json")
    return p


def check_config(cfg: dict[str, Any]) -> list[str]:
    out = [f"authority {k} -> unknown role {v}" for k, v in cfg["authority"].items() if v not in cfg["roles"]]
    for name, k in cfg["kinds"].items():
        if k["for"] is not None and k["for"] not in cfg["roles"]:
            out.append(f"kind {name} -> unknown role {k['for']}")
        if k["card"] not in ("none", "update", "file"):
            out.append(f"kind {name}: card must be none, update or file")
    return out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    a = build_parser().parse_args()
    cfg = load_config()
    if a.cmd == "check-config":
        bad = check_config(cfg)
        print(json.dumps({"ok": not bad, "problems": bad}))
        sys.exit(1 if bad else 0)
    f = {"kind": a.kind, "title": a.title, "what": a.what, "why": a.why, "ask": a.ask, "for": a.for_,
         "go_line": a.go_line, "go_session": a.go_session, "go_type": a.go_type, "risk": a.risk,
         "rollback": a.rollback, "pr": a.pr, "card": a.card, "parent": a.parent, "key": a.key,
         "session": a.session}
    if a.card:
        f["card_url"] = f"https://linear.app/aura-health/issue/{a.card}"
    code, out = raise_followup(f, cfg, a.dry_run)
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(code)


if __name__ == "__main__":
    main()
