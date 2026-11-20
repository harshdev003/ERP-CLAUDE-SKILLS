"""Linear GraphQL helper for find-pending-decisions.

Fetches all non-Done/Cancelled parent issues in the active Product/Design cycle,
along with their full comment threads and those of their sub-issues (across all teams).

Output (stdout, JSON):
  {
    "cycle": { "id": "...", "name": "Cycle 12", "number": 12 },
    "issues": [
      {
        "identifier": "PROD-123",
        "title": "...",
        "url": "...",
        "state": "In Progress",
        "comments": [{ "body": "...", "createdAt": "...", "author": "Alice" }],
        "children": [
          { "identifier": "ENG-45", "title": "...", "url": "...", "state": "In Progress",
            "comments": [...] }
        ]
      }
    ]
  }

Error output:
  { "error": "no_key",          "message": "LINEAR_API_KEY not set ..." }
  { "error": "no_active_cycle", "message": "No active cycle found ..." }
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    sys.exit(
        "error: linear_graphql.py requires 'requests'. Install with: pip install requests"
    )

GRAPHQL_URL = "https://api.linear.app/graphql"
PRODUCT_DESIGN_TEAM_ID = "7e95fa4a-7510-447d-adc7-c663612e70c6"
EXCLUDED_STATES = ["Done", "Cancelled"]
EXCLUDED_LABELS = {"Support"}
BOT_NAMES = {"Linear", "GitHub", "Slack", "Cursor", "GitLab"}


def gql(token: str, query: str, variables: dict | None = None) -> dict:
    resp = requests.post(
        GRAPHQL_URL,
        json={"query": query, "variables": variables or {}},
        headers={"Authorization": token, "Content-Type": "application/json"},
        timeout=30,
    )
    if not resp.ok:
        sys.exit(f"HTTP {resp.status_code}: {resp.text}")
    data = resp.json()
    if "errors" in data:
        sys.exit(f"GraphQL error: {json.dumps(data['errors'], indent=2)}")
    return data["data"]


GET_ACTIVE_CYCLE = """
query GetActiveCycle($teamId: ID!) {
  cycles(
    filter: { team: { id: { eq: $teamId } }, isActive: { eq: true } }
    first: 1
  ) {
    nodes { id name number startsAt endsAt }
  }
}
"""

# Fetches parent issues in cycle (across all their sub-issues regardless of team).
# `children` on an Issue crosses all teams — not scoped to Product/Design.
GET_CYCLE_ISSUES = """
query GetCycleIssues(
  $cycleId: ID!
  $excludedStates: [String!]!
  $after: String
) {
  issues(
    filter: {
      cycle: { id: { eq: $cycleId } }
      parent: { null: true }
      state: { name: { nin: $excludedStates } }
    }
    first: 100
    after: $after
    orderBy: updatedAt
  ) {
    nodes {
      identifier title url
      state { name }
      labels { nodes { name } }
      comments(orderBy: createdAt, first: 100) {
        nodes { id body createdAt resolvedAt parent { resolvedAt } user { name } }
      }
      children(
        filter: { state: { name: { nin: $excludedStates } } }
        first: 100
        orderBy: updatedAt
      ) {
        nodes {
          identifier title url
          state { name }
          labels { nodes { name } }
          comments(orderBy: createdAt, first: 100) {
            nodes { id body createdAt resolvedAt parent { resolvedAt } user { name } }
          }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def clean_comments(raw: list[dict]) -> list[dict]:
    """Strip bot comments and Linear-resolved threads (including replies); return [{id, body, createdAt, author}]."""
    result = []
    for c in raw:
        if c.get("resolvedAt"):
            continue
        if (c.get("parent") or {}).get("resolvedAt"):
            continue
        user = c.get("user")
        if not user:
            continue
        name = user.get("name", "")
        if name in BOT_NAMES:
            continue
        result.append({"id": c["id"], "body": c["body"], "createdAt": c["createdAt"], "author": name})
    return result


def flatten_issue(raw: dict) -> dict | None:
    labels = {n["name"] for n in raw.get("labels", {}).get("nodes", [])}
    if labels & EXCLUDED_LABELS:
        return None
    return {
        "identifier": raw["identifier"],
        "title": raw["title"],
        "url": raw["url"],
        "state": (raw.get("state") or {}).get("name", "Unknown"),
        "comments": clean_comments(raw.get("comments", {}).get("nodes", [])),
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    token = os.environ.get("LINEAR_API_KEY", "").strip()
    if not token:
        print(json.dumps({
            "error": "no_key",
            "message": (
                "LINEAR_API_KEY is not set. "
                "Add it to ~/.claude/settings.json under the env block and restart Claude Code."
            ),
        }))
        sys.exit(1)

    # 1. Active cycle
    data = gql(token, GET_ACTIVE_CYCLE, {"teamId": PRODUCT_DESIGN_TEAM_ID})
    cycles = data["cycles"]["nodes"]
    if not cycles:
        print(json.dumps({
            "error": "no_active_cycle",
            "message": "No active cycle found for the Product/Design team.",
        }))
        sys.exit(0)

    raw_cycle = cycles[0]
    cycle = {
        "id": raw_cycle["id"],
        "name": raw_cycle.get("name") or f"Cycle {raw_cycle['number']}",
        "number": raw_cycle["number"],
    }

    # 2. All parent issues in cycle + their children (paginated)
    all_issues: list[dict] = []
    cursor: str | None = None

    while True:
        vars: dict = {"cycleId": cycle["id"], "excludedStates": EXCLUDED_STATES}
        if cursor:
            vars["after"] = cursor

        data = gql(token, GET_CYCLE_ISSUES, vars)
        page = data["issues"]

        for raw in page["nodes"]:
            issue = flatten_issue(raw)
            if issue is None:
                continue
            issue["children"] = [
                child for child in (
                    flatten_issue(c) for c in raw.get("children", {}).get("nodes", [])
                ) if child is not None
            ]
            all_issues.append(issue)

        page_info = page["pageInfo"]
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info["endCursor"]

    print(json.dumps({
        "cycle": cycle,
        "issues": all_issues,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }))


if __name__ == "__main__":
    main()
