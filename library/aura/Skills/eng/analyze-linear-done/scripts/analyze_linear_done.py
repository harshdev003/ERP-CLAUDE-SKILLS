#!/usr/bin/env python3
"""Analyze a team member's completed Linear issues over a time window.

Deterministic engine for the `analyze-linear-done` skill. Resolves a member,
fetches their issues completed in the window, rolls each up to its top-level
(no-parent) ancestor, computes in-progress durations, and emits a structured
report. The LLM layer writes the qualitative top-issue summaries on top of this.

Data source: Linear GraphQL API (LINEAR_API_KEY). See SKILL.md for the
MCP-fallback rationale (API-first by necessity for bulk/paginated queries).
"""
import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

LINEAR_GQL = "https://api.linear.app/graphql"

# Inline this many parent levels so we can resolve the top-level ancestor without
# extra round-trips. Aura hierarchies observed up to 3 deep; 6 is a safe margin.
PARENT_DEPTH = 6


def _parent_fragment(depth):
    if depth == 0:
        return ""
    inner = _parent_fragment(depth - 1)
    return "parent { identifier title estimate %s }" % inner


ISSUE_FIELDS = """
identifier
title
estimate
startedAt
completedAt
url
labels { nodes { name } }
project { name }
team { key }
%s
""" % _parent_fragment(PARENT_DEPTH)


def gql(query, variables, token):
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        LINEAR_GQL,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.stderr.write("Linear API HTTP %s: %s\n" % (e.code, e.read().decode("utf-8", "replace")))
        sys.exit(2)
    except urllib.error.URLError as e:
        sys.stderr.write("Linear API unreachable: %s\n" % e)
        sys.exit(2)
    if payload.get("errors"):
        sys.stderr.write("Linear GraphQL errors: %s\n" % json.dumps(payload["errors"]))
        sys.exit(2)
    return payload["data"]


# --------------------------------------------------------------------------- #
# Window parsing
# --------------------------------------------------------------------------- #
def parse_window(spec, week_start, now):
    """Return (start_dt, end_dt, label). All UTC. end is exclusive."""
    s = (spec or "previous-week").strip().lower()

    if s in ("previous-week", "prev-week", "last-week", "previous week", "last week"):
        if week_start == "sun":
            since = (now.weekday() + 1) % 7  # days since most recent Sunday
        else:
            since = now.weekday()            # days since most recent Monday
        this_week_start = (now - timedelta(days=since)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start = this_week_start - timedelta(days=7)
        end = this_week_start
        return start, end, "previous calendar week (%s-start): %s -> %s" % (
            week_start, start.date(), (end - timedelta(days=1)).date()
        )

    m = re.match(r"(?:last\s*)?(\d+)\s*[-\s]?d(?:ays?)?$", s)
    if m:
        n = int(m.group(1))
        return now - timedelta(days=n), now, "last %d days" % n

    m = re.match(r"(?:last\s*)?(\d+)\s*[-\s]?w(?:eeks?)?$", s)
    if m:
        n = int(m.group(1))
        return now - timedelta(weeks=n), now, "last %d weeks" % n

    raise ValueError(
        "Unrecognised --window %r. Use 'previous-week', 'last N days', or 'last N weeks'." % spec
    )


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# --------------------------------------------------------------------------- #
# Member resolution
# --------------------------------------------------------------------------- #
def resolve_member(query, token):
    q = """
    query($q: String!) {
      users(filter: { or: [
        { name: { containsIgnoreCase: $q } },
        { displayName: { containsIgnoreCase: $q } },
        { email: { containsIgnoreCase: $q } }
      ] }) {
        nodes { id name displayName email active }
      }
    }"""
    nodes = gql(q, {"q": query}, token)["users"]["nodes"]
    active = [n for n in nodes if n.get("active")]
    pool = active or nodes
    if not pool:
        sys.stderr.write("MEMBER_NOT_FOUND: no Linear user matches %r.\n" % query)
        sys.exit(3)
    if len(pool) > 1:
        sys.stderr.write("MEMBER_AMBIGUOUS: %d users match %r:\n" % (len(pool), query))
        for n in pool:
            sys.stderr.write("  - %s <%s>%s\n" % (
                n["name"], n.get("email", "?"), "" if n.get("active") else " (inactive)"))
        sys.stderr.write("Re-run with a more specific name or email.\n")
        sys.exit(3)
    return pool[0]


# --------------------------------------------------------------------------- #
# Issue fetch (paginated)
# --------------------------------------------------------------------------- #
def fetch_completed(uid, start, end, token):
    q = """
    query($uid: ID!, $start: DateTimeOrDuration!, $end: DateTimeOrDuration!, $after: String) {
      issues(
        first: 100
        after: $after
        filter: {
          assignee: { id: { eq: $uid } }
          completedAt: { gte: $start, lt: $end }
        }
      ) {
        nodes { %s }
        pageInfo { hasNextPage endCursor }
      }
    }""" % ISSUE_FIELDS
    out, after = [], None
    while True:
        data = gql(q, {"uid": uid, "start": iso(start), "end": iso(end), "after": after}, token)
        conn = data["issues"]
        out.extend(conn["nodes"])
        if not conn["pageInfo"]["hasNextPage"]:
            break
        after = conn["pageInfo"]["endCursor"]
    return out


# --------------------------------------------------------------------------- #
# Rollup + durations
# --------------------------------------------------------------------------- #
def root_of(issue):
    node = issue
    while node.get("parent"):
        node = node["parent"]
    return node


def days_between(a, b):
    if not a or not b:
        return None
    pa = datetime.fromisoformat(a.replace("Z", "+00:00"))
    pb = datetime.fromisoformat(b.replace("Z", "+00:00"))
    return round((pb - pa).total_seconds() / 86400, 2)


def build_report(member, window_label, issues, top_n):
    groups = {}  # root_id -> dict
    for it in issues:
        r = root_of(it)
        rid = r["identifier"]
        g = groups.setdefault(rid, {
            "root_id": rid, "root_title": r.get("title", rid),
            "points": 0.0, "count": 0, "children": [],
        })
        est = it.get("estimate") or 0
        g["points"] += est
        g["count"] += 1
        g["children"].append({
            "id": it["identifier"],
            "title": it["title"],
            "points": est,
            "in_progress_days": days_between(it.get("startedAt"), it.get("completedAt")),
            "is_self": it["identifier"] == rid,
        })

    parents = sorted(groups.values(), key=lambda x: (-x["points"], -x["count"]))
    for p in parents:
        p["points"] = round(p["points"], 1)
        p["children"].sort(key=lambda c: -c["points"])

    durs = [days_between(it.get("startedAt"), it.get("completedAt")) for it in issues]
    durs = [d for d in durs if d is not None]
    total_pts = round(sum(p["points"] for p in parents), 1)

    return {
        "member": {"name": member["name"], "email": member.get("email")},
        "window": window_label,
        "totals": {
            "initiatives": len(parents),
            "issues": len(issues),
            "points": total_pts,
            "issues_with_duration": len(durs),
            "median_in_progress_days": round(sorted(durs)[len(durs) // 2], 2) if durs else None,
        },
        "parents": parents,
        "top_n": top_n,
    }


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def render_md(rep):
    L = []
    m = rep["member"]
    t = rep["totals"]
    L.append("# Completed Linear work — %s" % m["name"])
    L.append("")
    L.append("**Window:** %s" % rep["window"])
    L.append("**Totals:** %d initiatives · %d issues · %s points"
             % (t["initiatives"], t["issues"], t["points"]))
    if t["median_in_progress_days"] is not None:
        L.append("**Median in-progress duration:** %s days (%d/%d issues had start+end timestamps)"
                 % (t["median_in_progress_days"], t["issues_with_duration"], t["issues"]))
    L.append("")

    if not rep["parents"]:
        L.append("_No completed issues found for this member in the window._")
        return "\n".join(L)

    L.append("## Top-level (no-parent) issues — points rolled up")
    L.append("")
    L.append("| # | Pts | Iss | ID | Initiative |")
    L.append("|--:|--:|--:|---|---|")
    for i, p in enumerate(rep["parents"], 1):
        L.append("| %d | %s | %d | %s | %s |"
                 % (i, p["points"], p["count"], p["root_id"], p["root_title"].replace("|", "\\|")))
    L.append("")

    top = rep["parents"][:rep["top_n"]]
    L.append("## Child breakdown for the top %d initiatives" % len(top))
    L.append("_(For the LLM to summarise — what kind of work made up each initiative.)_")
    L.append("")
    for p in top:
        L.append("### %s — %s · %s pts / %d issues"
                 % (p["root_id"], p["root_title"], p["points"], p["count"]))
        for c in p["children"]:
            d = "" if c["in_progress_days"] is None else "  [%.1fd]" % c["in_progress_days"]
            self_tag = " *(parent itself)*" if c["is_self"] else ""
            L.append("- %s pts — %s%s%s" % (c["points"], c["title"].replace("\n", " "), self_tag, d))
        L.append("")
    return "\n".join(L)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="Analyze a member's completed Linear issues by initiative.")
    ap.add_argument("--member", required=True, help="Team member name or email (e.g. 'Farhan').")
    ap.add_argument("--window", default="previous-week",
                    help="'previous-week' (default), 'last N days', or 'last N weeks'.")
    ap.add_argument("--week-start", choices=["mon", "sun"], default="mon",
                    help="Week boundary for 'previous-week' (default mon = ISO Monday-Sunday).")
    ap.add_argument("--top", type=int, default=6, help="How many top initiatives to detail (default 6).")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of Markdown.")
    args = ap.parse_args()

    token = os.environ.get("LINEAR_API_KEY")
    if not token:
        sys.stderr.write(
            "NO_LINEAR_API_KEY: set LINEAR_API_KEY, or fall back to the Linear MCP "
            "path described in SKILL.md.\n")
        sys.exit(4)

    now = datetime.now(timezone.utc)
    try:
        start, end, label = parse_window(args.window, args.week_start, now)
    except ValueError as e:
        sys.stderr.write("BAD_WINDOW: %s\n" % e)
        sys.exit(5)

    member = resolve_member(args.member, token)
    issues = fetch_completed(member["id"], start, end, token)
    rep = build_report(member, label, issues, args.top)

    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print(render_md(rep))


if __name__ == "__main__":
    main()
