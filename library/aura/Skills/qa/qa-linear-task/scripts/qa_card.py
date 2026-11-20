"""Deterministic QA-card operations for qa-linear-task.

The QA card's checklist is the run's state: every execution mode reads it and
ticks it, so a subagent or a fresh session can resume from the card alone. This
script owns the parts that must not be done by hand — parsing the checklist and
flipping boxes — so a tick can never rewrite the rest of the body.

Subcommands (all print JSON to stdout):

  children  --issue <ID>               -> [{"id","identifier","title","state","labels"}]
  label     --name <label> [--team KEY] -> {"id","name"} | {"error":"not_found"}
  checklist --issue <ID> | --file <md> -> {"total","done","open","cases":[...]}
  tick      --issue <ID> --case 1.1 [--case 2.3 ...] [--untick]
                                       -> {"identifier","changed":[...],"missing":[...],
                                           "total","done","open"}
  move      --issue <ID> --case 3.3 [...] --to <ID>
                                       -> same shape as tick; open cases become
                                          `- [ ] ~~3.3 ...~~ → moved to <ID>`

A case is a Markdown task line whose text starts with a dotted id:
`- [ ] 2.3 Mute → ...`. Linear serialises checked boxes as `[X]` or `[x]`; both
read as checked. A struck-through case (`- [ ] ~~3.2 ...~~ → moved`) reads as
`moved` and does not count as open.

Transport, auth and error shape come from the sibling linear_api.py (a copy of
shared:update-linear-issues' helper).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from linear_api import gql  # noqa: E402

CASE_RE = re.compile(
    r"^(?P<lead>\s*[-*]\s+\[)(?P<mark>[ xX])(?P<close>\]\s+)(?P<strike>~~)?"
    r"(?P<id>\d+(?:\.\d+)+|\d+)\b(?P<rest>.*)$"
)
SECTION_RE = re.compile(r"^#{2,4}\s+(?P<title>.+?)\s*$")
MAX_EDIT_ATTEMPTS = 3


def parse_cases(markdown: str) -> list[dict]:
    cases, section = [], None
    for n, line in enumerate(markdown.split("\n")):
        s = SECTION_RE.match(line)
        if s:
            section = s.group("title")
            continue
        m = CASE_RE.match(line)
        if not m:
            continue
        if m.group("strike"):
            status = "moved"
        else:
            status = "done" if m.group("mark") in "xX" else "open"
        cases.append(
            {
                "id": m.group("id"),
                "status": status,
                "section": section,
                "text": (m.group("strike") or "") + m.group("id") + m.group("rest"),
                "line": n,
            }
        )
    return cases


def summarise(cases: list[dict]) -> dict:
    return {
        "total": len(cases),
        "done": sum(c["status"] == "done" for c in cases),
        "open": sum(c["status"] == "open" for c in cases),
        "moved": sum(c["status"] == "moved" for c in cases),
    }


def _issue(ident: str, fields: str) -> dict:
    res = gql(f'query($id: String!) {{ issue(id: $id) {{ {fields} }} }}', {"id": ident})
    issue = (res or {}).get("issue")
    if not issue:
        print(json.dumps({"error": "not_found", "message": f"No issue '{ident}'."}))
        sys.exit(1)
    return issue


def _paginate(query: str, variables: dict, path: list[str]) -> list[dict]:
    """Follow `pageInfo.endCursor` to the end; one page is never assumed complete."""
    nodes, after = [], None
    while True:
        res = gql(query, {**variables, "after": after})
        conn = res
        for key in path:
            conn = (conn or {}).get(key)
        if conn is None:
            print(json.dumps({"error": "not_found", "message": f"No result for {variables}."}))
            sys.exit(1)
        nodes += conn["nodes"]
        if not conn["pageInfo"]["hasNextPage"]:
            return nodes
        after = conn["pageInfo"]["endCursor"]


def cmd_children(args: argparse.Namespace) -> None:
    nodes = _paginate(
        "query($id: String!, $after: String) { issue(id: $id) {"
        " children(first: 100, after: $after) { nodes { id identifier title url"
        " state { name type } labels { nodes { name } } }"
        " pageInfo { hasNextPage endCursor } } } }",
        {"id": args.issue},
        ["issue", "children"],
    )
    out = [
        {
            "id": c["id"],
            "identifier": c["identifier"],
            "title": c["title"],
            "url": c["url"],
            "state": c["state"],
            "labels": [l["name"] for l in c["labels"]["nodes"]],
        }
        for c in nodes
    ]
    print(json.dumps(out))


def pick_label(nodes: list[dict], team_key: str | None) -> dict | None:
    """A workspace label applies to every team; a team label only to its own team.

    `QA` is also a *team* name, but only issueLabels ids are valid labelIds, so
    this never looks at teams. A team-scoped label for another team would make
    issueCreate reject the whole card, so it is never picked.
    """
    workspace = [n for n in nodes if not n.get("team")]
    if workspace:
        return workspace[0]
    own = [n for n in nodes if team_key and (n.get("team") or {}).get("key") == team_key]
    return own[0] if own else None


def cmd_label(args: argparse.Namespace) -> None:
    nodes = _paginate(
        "query($n: String!, $after: String) { issueLabels("
        " filter: { name: { eqIgnoreCase: $n } }, first: 100, after: $after)"
        " { nodes { id name team { key } } pageInfo { hasNextPage endCursor } } }",
        {"n": args.name},
        ["issueLabels"],
    )
    pick = pick_label(nodes, args.team)
    print(json.dumps({"id": pick["id"], "name": pick["name"]} if pick else {"error": "not_found"}))


def cmd_checklist(args: argparse.Namespace) -> None:
    if args.file:
        body = Path(args.file).read_text(encoding="utf-8")
    else:
        body = _issue(args.issue, "description")["description"] or ""
    cases = parse_cases(body)
    for c in cases:
        c.pop("line")
    print(json.dumps({**summarise(cases), "cases": cases}))


def tick_lines(lines: list[str], want: set[str], untick: bool) -> list[str]:
    mark = " " if untick else "x"
    changed = []
    for i, line in enumerate(lines):
        m = CASE_RE.match(line)
        if not m or m.group("id") not in want or m.group("strike"):
            continue
        if (m.group("mark") in "xX") != (not untick):
            lines[i] = m.group("lead") + mark + line[m.end("mark"):]
            changed.append(m.group("id"))
    return changed


def move_lines(lines: list[str], want: set[str], to: str) -> list[str]:
    changed = []
    for i, line in enumerate(lines):
        m = CASE_RE.match(line)
        if not m or m.group("id") not in want or m.group("strike") or m.group("mark") in "xX":
            continue
        text = (m.group("id") + m.group("rest")).rstrip()
        lines[i] = f"{m.group('lead')} {m.group('close')}~~{text}~~ → moved to {to}"
        changed.append(m.group("id"))
    return changed


def edit_checklist(ident: str, cases: list[str], apply) -> None:
    """Read-modify-write with a stale-read check, retried.

    Linear has no conditional update, so two runners ticking one card can each
    write back a body missing the other's tick. Re-reading `updatedAt` right
    before the write and re-applying on a change narrows that window to the
    round-trip of one mutation. It is not a lock — execution-modes.md keeps one
    runner per card at a time.
    """
    want = set(cases)
    for _ in range(MAX_EDIT_ATTEMPTS):
        issue = _issue(ident, "id identifier description updatedAt")
        lines = (issue["description"] or "").split("\n")
        changed = apply(lines, want)
        body = "\n".join(lines)
        if changed:
            if _issue(ident, "updatedAt")["updatedAt"] != issue["updatedAt"]:
                continue
            res = gql(
                "mutation($id: String!, $d: String!) { issueUpdate(id: $id,"
                " input: { description: $d }) { success } }",
                {"id": issue["id"], "d": body},
            )
            if not res["issueUpdate"]["success"]:
                sys.exit("issueUpdate returned success=false")
        present = {c["id"] for c in parse_cases(body)}
        print(
            json.dumps(
                {
                    "identifier": issue["identifier"],
                    "changed": changed,
                    "missing": sorted(want - present),
                    **summarise(parse_cases(body)),
                }
            )
        )
        return
    print(json.dumps({"error": "concurrent_edit", "message": f"{ident} kept changing; retry."}))
    sys.exit(1)


def cmd_tick(args: argparse.Namespace) -> None:
    edit_checklist(args.issue, args.case, lambda lines, want: tick_lines(lines, want, args.untick))


def cmd_move(args: argparse.Namespace) -> None:
    edit_checklist(args.issue, args.case, lambda lines, want: move_lines(lines, want, args.to))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("children", help="Sub-issues of a card (find an existing QA card).")
    s.add_argument("--issue", required=True)
    s.set_defaults(fn=cmd_children)

    s = sub.add_parser("label", help="Resolve an issue-label id by name (never a team id).")
    s.add_argument("--name", required=True)
    s.add_argument("--team", help="Team key (e.g. ENGMOB); allows that team's own label.")
    s.set_defaults(fn=cmd_label)

    s = sub.add_parser("checklist", help="Parse the QA checklist into cases + counts.")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--issue")
    g.add_argument("--file")
    s.set_defaults(fn=cmd_checklist)

    s = sub.add_parser("tick", help="Tick (or --untick) cases by id on the card.")
    s.add_argument("--issue", required=True)
    s.add_argument("--case", action="append", required=True)
    s.add_argument("--untick", action="store_true")
    s.set_defaults(fn=cmd_tick)

    s = sub.add_parser("move", help="Strike open cases and point them at a follow-up card.")
    s.add_argument("--issue", required=True)
    s.add_argument("--case", action="append", required=True)
    s.add_argument("--to", required=True, help="Follow-up card identifier, e.g. ENGMOB-2916.")
    s.set_defaults(fn=cmd_move)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
