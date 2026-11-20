"""Turn conversation ratings into a conversation-quality eval dataset.

The `conversation-dataset` follow-up, as four subcommands run in order. Every
file they write holds conversation text, so it stays in the working directory;
only `rows` output is de-identified, and it is written for
`data:save-to-braintrust-dataset --bulk-insert` to load after a confirmation.

    gather      the exact production call behind each rated reply, plus production's tools/params
    review      a local text file to read while labelling: recent turns, the rated reply, the feedback
    deidentify  replace names, places, employers and contacts in the labelled ratings; check for residue
    rows        labelled, de-identified ratings as JSONL dataset rows, each with its judge rubric

A chat rating names the reply it rates (`dialogue_id`). The llmCall ledger keeps
one row per tool-call loop under its last call, which is the call that wrote the
reply, so the rated call is the last row of the rating's prompter with that
`dialogueId` (matched case-insensitively: call sites write the UUID in both
casings).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import pathlib
import re
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

import responses_reader as rr  # noqa: E402
from coach_calls import CONTEXT_MARKER, coach_parts, deidentify, exact_request, post_responses, production_config  # noqa: E402


def _ua_who() -> str:
    """Login name of whoever runs this, for the user agent: AINATIVE_UA_WHO, else the global git email's local part, git user.name, the OS account, or "unknown"."""
    who = re.sub(r"^\d+\+", "", os.environ.get("AINATIVE_UA_WHO", "").strip().partition("@")[0])
    if not who:
        if not hasattr(_ua_who, "found"):
            import getpass, subprocess
            found = ""
            for key in ("user.email", "user.name"):
                try:
                    val = subprocess.run(["git", "config", "--global", key], capture_output=True, encoding="utf-8", errors="replace", timeout=5).stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    val = ""
                found = re.sub(r"^\d+\+", "", val.partition("@")[0]) if key == "user.email" else val
                if found:
                    break
            if not found:
                try:
                    found = getpass.getuser()
                except Exception:
                    found = ""
            _ua_who.found = found
        who = _ua_who.found
    return re.sub(r"[^A-Za-z0-9._-]+", "-", who).strip("-.") or "unknown"


def user_agent() -> str:
    """`AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)` -- labels this skill's calls to RTDB, GCP and the Aura API.

    Same format as gcloud_sa.user_agent(). A label, not proof: anyone can send it.
    """
    skill = pathlib.Path(__file__).resolve().parents[1]
    root = skill.parents[1]
    try:
        plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError):
        plugin = root.name
    try:
        front = (skill / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        version = re.search(r'version:\s*"([^"]+)"', front).group(1)
    except (OSError, IndexError, AttributeError):
        version = "unknown"
    session = os.environ.get("AINATIVE_SESSION_NAME") or (os.environ.get("CLAUDE_CODE_SESSION_ID") or "")[:8]
    session = re.sub(r"[^A-Za-z0-9._-]+", "-", session).strip("-") or "none"
    return f"AuraClaude/{_ua_who()}/{plugin}:{skill.name}@{version} (session {session})"


CONVERSATION_PROJECT_ID = "proj_esFyd4eq2VIAjKUn69X1ZFfk"
AURA_API = "https://services.aurahealth.io"


# ------------------------------------------------------------------ pure parts


def rated_call(rows: list[dict[str, Any]], dialogue_id: str, prompter: str) -> dict[str, Any] | None:
    """The ledger row whose call wrote the rated reply: the last one of the prompter in that dialogue."""
    did = (dialogue_id or "").lower()
    calls = [r for r in rows if r.get("prompterName") == prompter and (r.get("dialogueId") or "").lower() == did]
    return max(calls, key=lambda r: r.get("createdAt") or "") if calls else None


def reply_of(resp: dict[str, Any]) -> tuple[str, list[str]]:
    """(reply text, tool names) from a stored response's output."""
    out = resp.get("output") or []
    text = "".join(c.get("text", "") for o in out if o.get("type") == "message" for c in o.get("content") or [])
    return text, [o.get("name") for o in out if o.get("type") == "function_call"]


def candidates(entries: list[dict[str, Any]], prompter: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Ratings that can become rows, and a count of every other one by reason."""
    keep, skipped = [], {}
    for e in entries:
        why = None
        if e.get("is_test"):
            why = "test_account"
        elif e.get("is_infra"):
            why = "infra"
        elif e.get("shape") != "chat":
            # A conversation rating rates a whole session: there is no one call behind it.
            why = "not_a_reply_rating"
        elif e.get("prompter_name") != prompter:
            why = "other_prompter"
        elif not e.get("dialogue_id"):
            why = "no_dialogue_id"
        elif not (e.get("conversation_id") or "").strip():
            why = "no_conversation_id"
        elif not (e.get("uid") or "").strip():
            # Without the user's id there is nothing to check residue against.
            why = "no_uid"
        if why:
            skipped[why] = skipped.get(why, 0) + 1
        else:
            keep.append(e)
    return keep, skipped


def conversation_for(g: dict[str, Any]) -> list[dict[str, Any]]:
    """The user/assistant messages of a gathered rating's request (no delimiters, no developer messages)."""
    return coach_parts(g["request"]["input"], g.get("context_marker", CONTEXT_MARKER))["conversation"]


def row_for(ts: str, g: dict[str, Any], deid: dict[str, Any], label: dict[str, Any], case_id: str) -> dict[str, Any]:
    md = {
        "case_id": case_id,
        "issue": label["issue"],
        "contextInstructions": deid["context"],
        "developerInstructions": g.get("developer") or None,
        "source_rating_ts": ts,
        "rating": g.get("polarity"),
        "deidentified": True,
        "prompter_version": g.get("version"),
        "request_source": "exact",
    }
    return {"input": deid["input"], "expected": label["rubric"], "metadata": md,
            "tags": ["conversation-feedback", label["issue"]]}


def select_rows(gathered: dict[str, Any], deid: dict[str, Any], labels: dict[str, Any],
                prefix: str) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Rows for labelled ratings that de-identified cleanly. Everything else is counted."""
    skipped: dict[str, list[str]] = {}
    rows = []
    for ts in sorted(labels):
        lab = labels[ts]
        why = None
        if ts not in gathered:
            why = "not_gathered"
        elif not lab.get("issue") or not lab.get("rubric"):
            why = "no_issue_or_rubric"
        elif ts not in deid:
            why = "not_deidentified"
        elif deid[ts].get("residue"):
            why = "pii_residue"
        if why:
            skipped.setdefault(why, []).append(ts)
            continue
        rows.append(row_for(ts, gathered[ts], deid[ts], lab, f"{prefix}-{len(rows) + 1:02d}"))
    return rows, skipped


# ------------------------------------------------------------------ network


def _post(body: dict[str, Any]) -> dict[str, Any]:
    key = os.environ.get("OPENAI_EVALUATIONS_API_KEY")
    if not key:
        raise SystemExit("OPENAI_EVALUATIONS_API_KEY is not set (~/.claude/settings.json `env`).")
    return post_responses(key, body)


def _account(uid: str) -> list[str] | None:
    """The user's own identifiers (given name, email) for the residue check, or None. Local only.

    None means the check would have nothing to look for, so `gather` skips the
    rating rather than let a row through on the PII model alone.
    """
    token = os.environ.get("AURA_API_TOKEN")
    # A blank id makes `/users/` answer for the token's owner, not the rated user.
    if not token or not (uid or "").strip():
        return None
    try:
        r = requests.get(f"{AURA_API}/users/{uid}", headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent()}, timeout=30)
        u = r.json() if r.status_code == 200 else None
    except (requests.RequestException, ValueError):
        return None
    if isinstance(u, dict):
        u = u.get("data", u)
    ids = [x for x in ((u or {}).get("givenName"), (u or {}).get("email")) if isinstance(x, str) and x.strip()]
    return ids or None


def cmd_gather(a: argparse.Namespace) -> int:
    key = os.environ.get("OPENAI_CONVERSATION_API_KEY")
    if not key:
        raise SystemExit("OPENAI_CONVERSATION_API_KEY is not set: stored production responses need it.")
    if not os.environ.get("AURA_API_TOKEN"):
        raise SystemExit("AURA_API_TOKEN is not set: the ledger and the residue check's account lookup need it.")
    entries = [e for p in a.ratings for e in json.loads(Path(p).read_text(encoding="utf-8")).get("entries") or []]
    keep, skipped = candidates(entries, a.prompter)
    out: dict[str, Any] = {}
    config = None
    for e in keep:
        try:
            row = rated_call(rr.fetch_ledger_rows_by_conversation(e["conversation_id"]), e["dialogue_id"], a.prompter)
        except rr.ResponsesReaderError:
            # The ledger read failed: not the same as a conversation with no matching call.
            skipped["ledger_error"] = skipped.get("ledger_error", 0) + 1
            continue
        if row is None:
            skipped["no_ledger_row"] = skipped.get("no_ledger_row", 0) + 1
            continue
        try:
            resp = rr.fetch_response(key, CONVERSATION_PROJECT_ID, row["openaiId"])
            items = list(reversed(rr.fetch_input_items(key, CONVERSATION_PROJECT_ID, row["openaiId"])))
            req, others = exact_request(resp, items, a.context_marker)
            parts = coach_parts(req["input"], a.context_marker)
        except (rr.ResponsesReaderError, ValueError) as x:
            reason = "unfetchable" if isinstance(x, rr.ResponsesReaderError) else "unparseable_call"
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        account = _account(e["uid"])
        if account is None:
            skipped["no_account"] = skipped.get("no_account", 0) + 1
            continue
        reply, tools = reply_of(resp)
        out[e["ts"]] = {"request": req, "context": parts["context"], "developer": parts["developer"],
                        "context_marker": a.context_marker, "reply": reply, "tool_calls": tools,
                        "feedback": e.get("feedback"), "polarity": e.get("polarity"), "version": row.get("version"),
                        "items_left_out": others, "account": account}
        config = config or production_config(resp)
    Path(a.out).write_text(json.dumps(out), encoding="utf-8")
    if config:
        Path(a.tools_out).write_text(json.dumps(config[0]), encoding="utf-8")
        Path(a.params_out).write_text(json.dumps(config[1]), encoding="utf-8")
    for ts, v in sorted(out.items(), reverse=True):
        print(ts, f"{v['polarity']} · {len(conversation_for(v))} turns · reply {len(v['reply'])} chars"
                  f" · {a.prompter} {v['version']}" + (f" · tools {v['tool_calls']}" if v["tool_calls"] else ""))
    print(json.dumps({"ratings": len(entries), "gathered": len(out), "skipped": skipped}))
    return 0


def cmd_review(a: argparse.Namespace) -> int:
    g = json.loads(Path(a.gathered).read_text(encoding="utf-8"))
    with open(a.out, "w", encoding="utf-8") as f:
        for ts, v in sorted(g.items()):
            f.write(f"===== {ts} · {v['polarity']} · feedback: {v.get('feedback') or '-'}\n")
            for m in conversation_for(v)[-a.turns:]:
                f.write(f"  [{m['role']}] {m['content'][:a.chars]}\n")
            f.write(f"  >>> RATED REPLY: {v['reply'][:a.chars * 2]}\n\n")
    print(json.dumps({"ratings": len(g), "out": a.out}))
    return 0


def cmd_deidentify(a: argparse.Namespace) -> int:
    g = json.loads(Path(a.gathered).read_text(encoding="utf-8"))
    keep = [ts for ts in json.loads(Path(a.labels).read_text(encoding="utf-8")) if ts in g]

    def one(ts: str) -> tuple[str, dict[str, Any]]:
        v = g[ts]
        return ts, deidentify(_post, v["context"], conversation_for(v), v.get("account") or [], a.model)

    with cf.ThreadPoolExecutor(a.concurrency) as pool:
        out = dict(pool.map(one, keep))
    Path(a.out).write_text(json.dumps(out), encoding="utf-8")
    for ts, v in out.items():
        print(ts, f"replaced {v['replaced']} · known-identifier residue {v['residue']}")
    return 0


def cmd_rows(a: argparse.Namespace) -> int:
    load = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))  # noqa: E731
    rows, skipped = select_rows(load(a.gathered), load(a.deid), load(a.labels), a.case_prefix)
    with open(a.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    issues: dict[str, int] = {}
    for r in rows:
        issues[r["metadata"]["issue"]] = issues.get(r["metadata"]["issue"], 0) + 1
    print(json.dumps({"rows": len(rows), "issues": issues, "skipped": {k: len(v) for k, v in skipped.items()}}))
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gather", help="Fetch the exact production call behind each rated reply.")
    g.add_argument("--ratings", required=True, action="append", help="fetch_ratings.py output (repeatable)")
    g.add_argument("--prompter", default="coach-prompter")
    g.add_argument("--out", required=True)
    g.add_argument("--tools-out", required=True, help="production tool definitions, for --tools-json")
    g.add_argument("--params-out", required=True, help="production runtime params, for --params-json")
    g.add_argument("--context-marker", default=CONTEXT_MARKER)
    g.set_defaults(fn=cmd_gather)
    r = sub.add_parser("review", help="Write a local file to read while labelling.")
    r.add_argument("--gathered", required=True)
    r.add_argument("--turns", type=int, default=4)
    r.add_argument("--chars", type=int, default=500)
    r.add_argument("--out", required=True)
    r.set_defaults(fn=cmd_review)
    d = sub.add_parser("deidentify", help="De-identify the labelled ratings.")
    d.add_argument("--gathered", required=True)
    d.add_argument("--labels", required=True)
    d.add_argument("--model", default="gpt-5.4")
    d.add_argument("--concurrency", type=int, default=6)
    d.add_argument("--out", required=True)
    d.set_defaults(fn=cmd_deidentify)
    w = sub.add_parser("rows", help="Write dataset rows for the labelled, de-identified ratings.")
    for flag in ("gathered", "deid", "labels", "out"):
        w.add_argument(f"--{flag}", required=True)
    w.add_argument("--case-prefix", default="cfb")
    w.set_defaults(fn=cmd_rows)
    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
