#!/usr/bin/env python3
"""Build the saved Insights reports behind Mixpanel alerts, on one board, and verify each.

The Mixpanel service account (role "consumer" on project 963821) can save reports and boards but
cannot create alerts, so this script does everything up to the alert and prints a checklist for a
person to add each alert in the Mixpanel UI.

    python3 mixpanel_reports.py plan   --spec spec.json            # what would change; no writes
    python3 mixpanel_reports.py apply  --spec spec.json [--create-board]
    python3 mixpanel_reports.py verify --spec spec.json            # query each saved report
    python3 mixpanel_reports.py checklist --spec spec.json         # the one-line-per-alert list

Facts this encodes (learned on PROD-8892, 2026-11-20):

- A report is saved with ``POST /api/app/projects/{pid}/bookmarks/`` and must carry a
  ``dashboard_id``; without one Mixpanel answers "Dashboard doesn't exist". So every report goes
  on a board, and the board is created only with ``--create-board`` (a separate approval).
- A rolling window is ``displayOptions.analysis = "rolling"`` plus
  ``displayOptions.rollingWindowSize = N``. ``measurement.rolling`` must stay null: any key in it
  fails the query validator. Mixpanel's Rolling is an AVERAGE, so "no events in N periods" is an
  alert on "below 0.01": one event in the window averages 1/N (0.042 at N=24), so the threshold
  must sit under 1/N, and "below 1" fires whenever there are fewer than N events.
- ``GET /api/query/insights?project_id=…&bookmark_id=…`` runs a saved report and returns either
  its series or the validator's error, so it is the check that the saved params are valid.
- A create can return ``dashboard_id: null`` even when the body named the board, so ``apply``
  re-attaches with a PATCH and checks it. The board's ``layout`` cannot be set by this account
  (``INVALID_LAYOUT`` on every shape tried), so reports may not show as tiles; each row prints the
  report's own link.

Idempotent: a report whose name already exists on the board is updated in place, never duplicated.
Nothing is ever deleted. Counts only: filters on user ids or emails are refused.

Auth: MIXPANEL_SERVICE_ACCOUNT_USERNAME, MIXPANEL_SERVICE_ACCOUNT_SECRET, MIXPANEL_PROJECT_ID.
HTTPS goes through ``requests`` when importable, else ``curl``; never urllib (no macOS CA bundle).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

HOST = "https://mixpanel.com"
UNITS = ("hour", "day", "week")
# Properties that identify a person. A filter on one would put an id or email into a saved report.
PII_PROPS = re.compile(r"(?i)(e-?mail|user_?id|\$user_id|distinct_id|uid|phone|name)$")
ZERO_RULE = 0.01  # rolling average below this = no events; must stay under 1/N, the value one event gives


class SpecError(ValueError):
    """The spec is malformed or asks for something this skill refuses."""


# ------------------------------------------------------------------------------ spec


def load_spec(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    validate(spec)
    return spec


def validate(spec: dict) -> None:
    if not spec.get("board"):
        raise SpecError("spec.board (the board title) is required")
    alerts = spec.get("alerts") or []
    if not alerts:
        raise SpecError("spec.alerts is empty")
    names = set()
    for a in alerts:
        k = a.get("key") or a.get("name") or "?"
        for field in ("key", "name", "event", "unit", "condition", "check", "channels"):
            if not a.get(field):
                raise SpecError(f"{k}: '{field}' is required")
        if a["name"] in names:
            raise SpecError(f"{k}: duplicate report name '{a['name']}'")
        names.add(a["name"])
        if a["unit"] not in UNITS:
            raise SpecError(f"{k}: unit must be one of {UNITS}")
        for prop, val in (a.get("filters") or {}).items():
            if PII_PROPS.search(prop) or "@" in str(val):
                raise SpecError(f"{k}: filter on '{prop}' would put user identity in a report")
        roll = a.get("rolling")
        if roll is not None and (not isinstance(roll, int) or roll < 2):
            raise SpecError(f"{k}: rolling must be an integer of at least 2 periods")
        cond = parse_condition(a["condition"], k)
        if roll and cond[0] == "below" and cond[1] * roll >= 1:
            raise SpecError(f"{k}: rolling is an average and one event in {roll} {a['unit']}s averages "
                            f"{1 / roll:.3f}; 'no events' is 'below {ZERO_RULE}', not '{a['condition']}'")


def parse_condition(cond: str, key: str = "?") -> tuple[str, float]:
    m = re.fullmatch(r"\s*(above|below)\s+([0-9]*\.?[0-9]+)\s*", cond or "")
    if not m:
        raise SpecError(f"{key}: condition must read 'above N' or 'below N', got '{cond}'")
    return m.group(1), float(m.group(2))


def _filter(prop: str, val) -> dict:
    """One event-property filter: equals for strings, is true / is false for a JSON boolean."""
    f = {"dataset": "$mixpanel", "value": prop, "resourceType": "events", "profileType": None,
         "search": "", "dataGroupId": None, "propertyObjectKey": None, "determiner": "all"}
    if isinstance(val, bool):
        f.update({"filterType": "boolean", "defaultType": "boolean",
                  "filterOperator": "true" if val else "false", "filterValue": None})
    else:
        f.update({"filterType": "string", "defaultType": "string", "filterOperator": "equals",
                  "filterValue": val if isinstance(val, list) else [val]})
    return f


def params(a: dict) -> dict:
    """The saved-report params for one alert: an Insights line of Total Events."""
    filt = [_filter(prop, val) for prop, val in (a.get("filters") or {}).items()]
    ev = a["event"]
    days = a.get("days") or (7 if a["unit"] == "hour" else 30)
    p = {
        "sections": {
            "cohorts": [], "filter": [], "formula": [], "group": [],
            "show": [{
                "behavior": {
                    "type": "simple", "name": ev, "hasUnsavedChanges": False, "resourceType": "events",
                    "dataGroupId": None, "filters": filt,
                    "behaviors": [{"type": "event", "id": None, "name": ev, "filters": filt,
                                   "filtersDeterminer": None}],
                },
                "display": {},
                "measurement": {"math": "total", "perUserAggregation": None, "property": None,
                                "rolling": None, "cumulative": False, "multiAttribution": None},
                "type": "metric", "name": f"Total Events of {ev}",
            }],
            "time": [{"dateRangeType": "in the last", "window": {"unit": "day", "value": days},
                      "unit": a["unit"]}],
        },
        "displayOptions": {"chartType": "line", "plotStyle": "standard", "analysis": "linear",
                           "value": "absolute"},
    }
    if a.get("rolling"):
        p["displayOptions"]["analysis"] = "rolling"
        p["displayOptions"]["rollingWindowSize"] = a["rolling"]
    return p


def description(a: dict, ticket: str | None) -> str:
    bits = [f"{ticket} " if ticket else "", f"{a.get('severity', 'normal')} alert. {a['event']}"]
    for prop, val in (a.get("filters") or {}).items():
        bits.append(f", {prop} {val}")
    bits.append(f", by {a['unit']}")
    if a.get("rolling"):
        bits.append(f", rolling {a['rolling']}-{a['unit']} average (0 = no events in the window)")
    bits.append(f". Alert when {a['condition']}, checked {a['check']}, to {', '.join(a['channels'])}.")
    return "".join(bits)


# ------------------------------------------------------------------------------ transport


def _auth() -> tuple[str, str, str]:
    try:
        return (os.environ["MIXPANEL_SERVICE_ACCOUNT_USERNAME"],
                os.environ["MIXPANEL_SERVICE_ACCOUNT_SECRET"], os.environ["MIXPANEL_PROJECT_ID"])
    except KeyError as e:
        raise SystemExit(f"missing env var {e.args[0]} (names only are ever printed)") from None


def http(method: str, path: str, body: dict | None = None) -> dict:
    """One Mixpanel app/query API call. Returns the decoded JSON; raises on a non-JSON reply."""
    user, secret, _ = _auth()
    url = HOST + path
    try:
        import requests  # pylint: disable=import-outside-toplevel
        r = requests.request(method, url, auth=(user, secret), json=body, timeout=120)
        text = r.text
    except ImportError:
        cmd = ["curl", "-sS", "-X", method, "-u", f"{user}:{secret}", "-H",
               "Content-Type: application/json"]
        if body is not None:
            cmd += ["--data", json.dumps(body)]
        cmd.append(url)
        text = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=180,
                              check=False).stdout
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(f"{method} {path}: non-JSON reply: {text[:200]}") from None


def _results(d: dict):
    return d.get("results", d) if isinstance(d, dict) else d


# ------------------------------------------------------------------------------ actions


def find_board(title: str) -> dict | None:
    _, _, pid = _auth()
    boards = _results(http("GET", f"/api/app/projects/{pid}/dashboards/"))
    hits = [b for b in boards if b.get("title") == title]
    if len(hits) > 1:
        raise SystemExit(f"{len(hits)} boards are titled '{title}'; name the one to use with spec.board_id")
    return hits[0] if hits else None


def board_for(spec: dict) -> dict | None:
    if spec.get("board_id"):
        return {"id": spec["board_id"], "title": spec["board"]}
    return find_board(spec["board"])


def reports_on(board_id: int, ticket: str | None = None) -> dict[str, dict]:
    """Reports on the board by name. With a ticket, a same-named report with no board whose
    description starts with that ticket (one of ours whose attach did not stick) counts as existing,
    so a re-run updates it instead of duplicating. Someone else's same-named report is never adopted."""
    _, _, pid = _auth()
    allb = _results(http("GET", f"/api/app/projects/{pid}/bookmarks/"))
    found = {b["name"]: b for b in allb if b.get("dashboard_id") == board_id}
    if ticket:
        for b in allb:
            if (b.get("dashboard_id") is None and b.get("name") not in found
                    and (b.get("description") or "").startswith(f"{ticket} ")):
                found[b["name"]] = dict(b, stray=True)
    return found


def verify_one(bid: int) -> dict:
    _, _, pid = _auth()
    q = http("GET", f"/api/query/insights?project_id={pid}&bookmark_id={bid}")
    if "series" not in q:
        return {"ok": False, "error": str(q.get("error", q))[:300]}

    def leaf(s):
        for v in s.values():
            if isinstance(v, dict) and v and all(not isinstance(x, dict) for x in v.values()):
                return v
            if isinstance(v, dict):
                got = leaf(v)
                if got:
                    return got
        return None

    pts = sorted((leaf(q["series"]) or {}).items())
    if not pts:
        return {"ok": True, "points": 0}
    vals = [v for _, v in pts]
    return {"ok": True, "points": len(pts), "min": round(min(vals), 3), "max": round(max(vals), 3),
            "last": round(vals[-1], 3)}


def plan(spec: dict) -> list[dict]:
    board = board_for(spec)
    existing = reports_on(board["id"], spec.get("ticket")) if board else {}
    out = [{"board": spec["board"], "action": "exists" if board else "CREATE (needs --create-board)",
            "id": board and board["id"]}]
    for a in spec["alerts"]:
        e = existing.get(a["name"])
        out.append({"key": a["key"], "report": a["name"],
                    "action": (f"adopt stray {e['id']} and attach" if e.get("stray") else f"update {e['id']}")
                    if e else "create"})
    return out


def apply(spec: dict, create_board: bool) -> list[dict]:
    _, _, pid = _auth()
    board = board_for(spec)
    if not board:
        if not create_board:
            raise SystemExit(f"board '{spec['board']}' does not exist; creating it needs --create-board")
        board = _results(http("POST", f"/api/app/projects/{pid}/dashboards/",
                              {"title": spec["board"], "description": spec.get("board_description", "")}))
        if not board.get("id"):
            raise SystemExit(f"board create failed: {board}")
    existing = reports_on(board["id"], spec.get("ticket"))
    out = []
    for a in spec["alerts"]:
        body = {"name": a["name"], "type": "insights", "description": description(a, spec.get("ticket")),
                "params": json.dumps(params(a)), "dashboard_id": board["id"]}
        e = existing.get(a["name"])
        if e:
            r = _results(http("PATCH", f"/api/app/projects/{pid}/bookmarks/{e['id']}", body))
        else:
            r = _results(http("POST", f"/api/app/projects/{pid}/bookmarks/", body))
        bid = r.get("id") if isinstance(r, dict) else None
        if bid:
            # A create's reply can name the board while the stored report has dashboard_id null
            # (every create on PROD-8892). Attach with a PATCH, then trust only a fresh read.
            http("PATCH", f"/api/app/projects/{pid}/bookmarks/{bid}", {"dashboard_id": board["id"]})
            r = _results(http("GET", f"/api/app/projects/{pid}/bookmarks/{bid}"))
        row = {"key": a["key"], "report": a["name"], "id": bid, "action": "updated" if e else "created",
               "url": report_url(bid) if bid else None, "on_board": r.get("dashboard_id") == board["id"]}
        if not row["on_board"]:
            row.update({"ok": False, "error": "report saved but not attached to the board"})
            out.append(row)
            continue
        row.update(verify_one(bid) if bid else {"ok": False, "error": str(r)[:300]})
        out.append(row)
    out.insert(0, {"board": spec["board"], "id": board["id"], "url": board_url(board["id"])})
    return out


def verify(spec: dict) -> list[dict]:
    board = board_for(spec)
    if not board:
        raise SystemExit(f"board '{spec['board']}' does not exist")
    existing = reports_on(board["id"])
    out = []
    for a in spec["alerts"]:
        e = existing.get(a["name"])
        row = {"key": a["key"], "report": a["name"], "id": e and e["id"],
               "url": report_url(e["id"]) if e else None}
        row.update(verify_one(e["id"]) if e else {"ok": False, "error": "not on the board"})
        out.append(row)
    return out


def board_url(board_id: int) -> str:
    _, _, pid = _auth()
    ws = os.environ.get("MIXPANEL_WORKSPACE_ID", "30768")
    return f"{HOST}/project/{pid}/view/{ws}/app/boards#id={board_id}"


def report_url(bid: int) -> str:
    _, _, pid = _auth()
    ws = os.environ.get("MIXPANEL_WORKSPACE_ID", "30768")
    return f"{HOST}/project/{pid}/view/{ws}/app/insights#report/{bid}"


def checklist(spec: dict) -> str:
    lines = []
    for a in spec["alerts"]:
        lines.append(f"[ ] {a['key']} {a['name']} — {a['condition']} — {a['check']} — "
                     f"{', '.join(a['channels'])}")
    return "\n".join(lines)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Build and verify the saved reports behind Mixpanel alerts.")
    ap.add_argument("action", choices=("plan", "apply", "verify", "checklist"))
    ap.add_argument("--spec", required=True, help="JSON spec (see assets/spec-example.json)")
    ap.add_argument("--create-board", action="store_true", help="create the board if missing")
    args = ap.parse_args()
    try:
        spec = load_spec(args.spec)
    except SpecError as e:
        raise SystemExit(f"spec refused: {e}") from None
    if args.action == "checklist":
        print(checklist(spec))
        return
    rows = {"plan": plan, "verify": verify}.get(args.action)
    result = rows(spec) if rows else apply(spec, args.create_board)
    for r in result:
        print(json.dumps(r, ensure_ascii=False))
    if any(r.get("ok") is False for r in result):
        sys.exit(1)


if __name__ == "__main__":
    main()
