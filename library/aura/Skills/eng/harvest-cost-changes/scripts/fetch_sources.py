#!/usr/bin/env python3
"""Fetch raw cost-change candidates from Linear, GitHub, and Slack, plus the
current state of the Learnings/Aurie Cost Engineering page (ledger rows, block
map, harvest watermark).

Deterministic side of eng:harvest-cost-changes — no classification happens
here. Emits one JSON file the skill reads to decide what (if anything) goes on
the page. The companion script apply_updates.py performs the writes.

Sources (each independently degradable — a missing token or unreachable
service adds a note to `degraded` instead of failing the run):

- Notion  (NOTION_TOKEN)       — page block map + Cost Changes ledger rows
- Linear  (LINEAR_API_KEY)     — "AI Cost Review and Changes [Cycle N]" issue,
                                 children, comments
- GitHub  (gh CLI)             — merged PRs in IntelligenceService and
                                 AuraConsumerApp since the watermark
- Slack   (SLACK_BOT_TOKEN)    — #ai-prompt-engineering-cost messages + thread
                                 replies since the watermark

Usage:
  python3 fetch_sources.py [--since YYYY-MM-DD] [--issue PROD-XXXX]
                           [--sources linear,github,slack,page,ledger]
                           [--json-out /tmp/cost_harvest.json]

Without --since, the watermark is read from the page footer's
"Last harvested: YYYY-MM-DD" (falling back to "Compiled YYYY-MM-DD", then to
7 days ago).
"""

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notion_rest import notion  # noqa: E402
from slack_read import SlackReadError, history, replies  # noqa: E402

PAGE_ID = "37b75e23af208189b4e2de18e56546e0"          # Learnings/Aurie Cost Engineering
COST_CHANGES_DB_ID = "37b75e23af2081e2b6cbc58e36eaa69c"  # child database "Cost Changes"
SLACK_CHANNEL = "C0B3GN41MDX"                          # #ai-prompt-engineering-cost
SLACK_WORKSPACE_URL = "https://auramobile.slack.com"
LINEAR_CYCLE_ISSUE_TITLE = "AI Cost Review and Changes"
GITHUB_REPOS = ["AuraHealth/IntelligenceService", "AuraHealth/AuraConsumerApp"]

WATERMARK_RE = re.compile(r"Last harvested: (\d{4}-\d{2}-\d{2})")
COMPILED_RE = re.compile(r"Compiled (\d{4}-\d{2}-\d{2})")

ALL_SOURCES = ["page", "ledger", "linear", "github", "slack"]


def _block_text(block: dict) -> str:
    payload = block.get(block["type"], {})
    return "".join(t.get("plain_text", "") for t in payload.get("rich_text", []))


def fetch_page_map(degraded: list) -> dict:
    """Block map of the page: headline bullets, narrative headings, footer."""
    blocks = notion.get_block_children(PAGE_ID)
    out = {
        "page_id": PAGE_ID,
        "headline_bullets": [],
        "narrative_headings": [],
        "footer": None,
        "watermark": None,
    }
    section = None
    for b in blocks:
        btype = b["type"]
        text = _block_text(b) if btype != "child_database" else ""
        if btype == "heading_2":
            section = text
            continue
        if btype == "bulleted_list_item" and section == "Headline metrics":
            out["headline_bullets"].append({"block_id": b["id"], "text": text})
        elif btype == "heading_3":
            out["narrative_headings"].append({"block_id": b["id"], "text": text})
        elif btype == "paragraph" and (
            WATERMARK_RE.search(text) or COMPILED_RE.search(text)
        ):
            out["footer"] = {"block_id": b["id"], "text": text}
            m = WATERMARK_RE.search(text) or COMPILED_RE.search(text)
            out["watermark"] = m.group(1)
    return out


def fetch_ledger(degraded: list) -> dict:
    """All Cost Changes rows + the data_source_id apply_updates.py needs."""
    db = notion._request("GET", f"/databases/{COST_CHANGES_DB_ID}")
    ds_id = db["data_sources"][0]["id"]
    rows = []
    for r in notion.query_data_source(ds_id, page_size=100):
        props = r["properties"]

        def text_of(name, kind):
            v = props.get(name, {})
            if kind == "title":
                return "".join(t["plain_text"] for t in v.get("title", []))
            if kind == "rich_text":
                return "".join(t["plain_text"] for t in v.get("rich_text", []))
            if kind == "select":
                return (v.get("select") or {}).get("name")
            if kind == "date":
                return (v.get("date") or {}).get("start")
            return None

        rows.append({
            "row_id": r["id"],
            "change": text_of("Change", "title"),
            "area": text_of("Area", "select"),
            "status": text_of("Status", "select"),
            "date": text_of("Date", "date"),
            "refs": text_of("Refs", "rich_text"),
            "impact": text_of("Impact", "rich_text"),
        })
    return {"data_source_id": ds_id, "rows": rows}


def fetch_linear(since: str, issue_override: str, degraded: list) -> dict:
    import requests

    key = os.environ.get("LINEAR_API_KEY")
    if not key:
        degraded.append("linear: LINEAR_API_KEY not set — try Linear MCP instead")
        return {}

    def gql(query, variables=None):
        resp = requests.post(
            "https://api.linear.app/graphql",
            json={"query": query, "variables": variables or {}},
            headers={"Authorization": key, "Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("errors"):
            raise RuntimeError(f"Linear GraphQL: {data['errors']}")
        return data["data"]

    issue_fields = """
        identifier title url description updatedAt
        state { name } cycle { number startsAt endsAt }
        children(first: 50) { nodes {
            identifier title url updatedAt state { name }
            assignee { name } description
        } }
        comments(first: 50) { nodes { body createdAt url user { name } } }
    """

    if issue_override:
        data = gql(
            "query($id: String!) { issue(id: $id) { %s } }" % issue_fields,
            {"id": issue_override},
        )
        issue = data["issue"]
    else:
        # Most recently updated cycle issue with the canonical title.
        data = gql(
            """query($title: String!) {
                 issues(filter: {title: {contains: $title}},
                        orderBy: updatedAt, first: 5) {
                   nodes { %s }
                 }
               }""" % issue_fields,
            {"title": LINEAR_CYCLE_ISSUE_TITLE},
        )
        nodes = data["issues"]["nodes"]
        if not nodes:
            degraded.append(
                f"linear: no issue titled '{LINEAR_CYCLE_ISSUE_TITLE}' found"
            )
            return {}
        issue = nodes[0]

    for child in issue["children"]["nodes"]:
        child["description"] = (child.get("description") or "")[:800]
        child["updated_since_watermark"] = child["updatedAt"][:10] >= since
    issue["description"] = (issue.get("description") or "")[:1500]
    issue["comments"]["nodes"] = [
        c for c in issue["comments"]["nodes"] if c["createdAt"][:10] >= since
    ]
    return {"cycle_issue": issue}


def fetch_github(since: str, degraded: list) -> dict:
    out = {}
    for repo in GITHUB_REPOS:
        try:
            raw = subprocess.run(
                [
                    "gh", "pr", "list", "--repo", repo, "--state", "merged",
                    "--search", f"merged:>={since}", "--limit", "100",
                    "--json", "number,title,url,mergedAt,author,body",
                ],
                capture_output=True, text=True, timeout=60, check=True,
            ).stdout
            prs = json.loads(raw)
            for pr in prs:
                pr["body"] = (pr.get("body") or "")[:600]
                pr["author"] = (pr.get("author") or {}).get("login")
            out[repo] = prs
        except FileNotFoundError:
            degraded.append("github: gh CLI not installed")
            break
        except subprocess.CalledProcessError as exc:
            degraded.append(f"github: gh failed for {repo}: {exc.stderr.strip()[:200]}")
    return out


def fetch_slack(since: str, degraded: list) -> dict:
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        degraded.append("slack: SLACK_BOT_TOKEN not set — try Slack MCP instead")
        return {}
    oldest = dt.datetime.strptime(since, "%Y-%m-%d").replace(
        tzinfo=dt.timezone.utc
    ).timestamp()

    def permalink(ts):
        return f"{SLACK_WORKSPACE_URL}/archives/{SLACK_CHANNEL}/p{ts.replace('.', '')}"

    # Best-effort: a Slack read failure degrades the run rather than aborting it
    # (the shared slack_read helper raises SlackReadError, which we catch here).
    try:
        messages = history(SLACK_CHANNEL, token=token, oldest=oldest)
    except SlackReadError as exc:
        degraded.append(f"slack: conversations.history error: {exc}")
        return {}

    threads = []
    for m in sorted(messages, key=lambda x: x["ts"]):
        if m.get("subtype") in ("channel_join", "channel_leave"):
            continue
        entry = {
            "ts": m["ts"],
            "date": dt.datetime.fromtimestamp(
                float(m["ts"]), dt.timezone.utc
            ).strftime("%Y-%m-%d"),
            "user": m.get("user") or m.get("bot_id"),
            "text": (m.get("text") or "")[:1500],
            "permalink": permalink(m["ts"]),
            "replies": [],
        }
        if m.get("reply_count"):
            try:
                thread_msgs = replies(SLACK_CHANNEL, m["ts"], token=token, limit=100)
            except SlackReadError:
                thread_msgs = []
            entry["replies"] = [
                {
                    "user": r.get("user") or r.get("bot_id"),
                    "text": (r.get("text") or "")[:1200],
                }
                for r in thread_msgs[1:]
            ]
        threads.append(entry)
    return {"channel": SLACK_CHANNEL, "threads": threads}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", help="Override watermark (YYYY-MM-DD)")
    ap.add_argument("--issue", help="Linear cycle issue override (e.g. PROD-2274)")
    ap.add_argument(
        "--sources", default=",".join(ALL_SOURCES),
        help=f"Comma list from: {','.join(ALL_SOURCES)} (default: all)",
    )
    ap.add_argument("--json-out", default="/tmp/cost_harvest.json")
    args = ap.parse_args()
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    degraded: list = []
    result: dict = {
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "degraded": degraded,
    }

    page = {}
    if "page" in sources or "ledger" in sources:
        if not notion.available:
            degraded.append("notion: NOTION_TOKEN not set — page/ledger skipped")
        else:
            if "page" in sources:
                try:
                    page = fetch_page_map(degraded)
                    result["page"] = page
                except Exception as exc:  # noqa: BLE001
                    degraded.append(f"notion page: {exc}")
            if "ledger" in sources:
                try:
                    result["ledger"] = fetch_ledger(degraded)
                except Exception as exc:  # noqa: BLE001
                    degraded.append(f"notion ledger: {exc}")

    since = args.since or page.get("watermark") or (
        dt.date.today() - dt.timedelta(days=7)
    ).isoformat()
    result["since"] = since

    if "linear" in sources:
        try:
            result["linear"] = fetch_linear(since, args.issue, degraded)
        except Exception as exc:  # noqa: BLE001
            degraded.append(f"linear: {exc}")
    if "github" in sources:
        result["github"] = fetch_github(since, degraded)
    if "slack" in sources:
        try:
            result["slack"] = fetch_slack(since, degraded)
        except Exception as exc:  # noqa: BLE001
            degraded.append(f"slack: {exc}")

    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    print(f"since: {since}  (watermark found: {bool(page.get('watermark'))})")
    print(f"ledger rows: {len(result.get('ledger', {}).get('rows', []))}")
    cyc = result.get("linear", {}).get("cycle_issue") or {}
    if cyc:
        kids = cyc.get("children", {}).get("nodes", [])
        fresh = sum(1 for k in kids if k.get("updated_since_watermark"))
        print(f"linear: {cyc.get('identifier')} '{cyc.get('title')}' — "
              f"{len(kids)} children ({fresh} updated since watermark), "
              f"{len(cyc.get('comments', {}).get('nodes', []))} new comments")
    for repo, prs in result.get("github", {}).items():
        print(f"github {repo}: {len(prs)} merged PRs since {since}")
    if result.get("slack", {}).get("threads") is not None:
        print(f"slack #ai-prompt-engineering-cost: "
              f"{len(result['slack']['threads'])} messages since {since}")
    if degraded:
        print("DEGRADED: " + "; ".join(degraded))
    print(f"wrote {args.json_out}")


if __name__ == "__main__":
    main()
