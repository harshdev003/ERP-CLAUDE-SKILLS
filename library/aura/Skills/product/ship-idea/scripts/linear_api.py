"""Linear GraphQL helper for update-linear-issues.

Standalone copy of the add-linear-issues helper (skills are self-contained — see
/CLAUDE.md), extended with two subcommands for editing existing issues:
`get-issue` (read current state) and `update-issue` (apply an IssueUpdateInput).
Replaces the Linear MCP tools with direct Linear GraphQL API calls, so the skill
works in sessions where the Linear MCP connector is unavailable (Cowork /
local-agent mode). See PROD-2216 / PROD-2253.

Auth: reads `LINEAR_API_KEY` from the environment (see /CLAUDE.md -> "Secrets and
environment"). The key is **never** printed — error output never echoes request
headers. On any failure the subcommand prints a JSON error to stdout with an
`"error"` field and `"fallback": "mcp"`, then exits 1, so the skill can warn the
user and fall back to the Linear MCP tools (or prompt the user). Error types:
  no_key        - LINEAR_API_KEY unset
  api_error     - non-2xx HTTP (e.g. 401 for an invalid / expired key)
  graphql_error - Linear returned a GraphQL `errors` array
  network_error - could not reach the API (timeout / connection). For a mutation
                  this is ambiguous (the write may have landed), so the payload
                  also sets "verify_before_retry": true.

All subcommands print JSON to stdout:

  whoami                                    -> {"id","name","email"}
  current-cycle --team <teamId>             -> {"id","name","number"}
                                               | {"error":"no_active_cycle"}
  milestones    --project <projectId>       -> [{"id","name","targetDate"}]
  states        --team <teamId>             -> [{"id","name","type","position"}]
  find-issue    --query <text|IDENTIFIER>   -> [{"id","identifier","title","url",
                                                 "labels":[{"id","name"}]}]
  get-issue     --id <id|IDENTIFIER>        -> {"id","identifier","title",
                                                 "description","url","priority",
                                                 "estimate","state","assignee",
                                                 "team","project","cycle",
                                                 "projectMilestone","parent",
                                                 "labels"} | {"error":"not_found"}
  create-issue  --input-file <file.json>    -> {"id","identifier","url"}
  update-issue  --input-file <file.json>    -> {"id","identifier","url"}
  comment       --issue <id> --body-file <f> -> {"id"}
  attach-url    --issue <id> --url <u> [--title <t>] -> {"id"}

`update-issue` reads an IssueUpdateInput JSON object from a file (keeps long
descriptions and the API key off the command line). It MUST include `id` (the
issue UUID, from get-issue / find-issue — mutations want the UUID, not the
PROD-123 identifier); `id` is sent as a separate GraphQL variable and every other
key is passed through as the IssueUpdateInput, e.g.:

  {
    "id": "<issue-uuid>",      // required — UUID, not the PROD-123 identifier
    "title": "...",
    "description": "...",      // markdown — REPLACES the existing body
    "assigneeId": "me",        // literal "me" is resolved to the viewer's id
    "priority": 3,             // 1 Urgent / 2 High / 3 Medium / 4 Low
    "projectId": "...",
    "cycleId": "...",
    "projectMilestoneId": "...",
    "parentId": "...",
    "stateId": "...",          // team-scoped (see SKILL.md note)
    "estimate": 5,
    "labelIds": ["..."],       // REPLACES the full label set
    "addedLabelIds": ["..."],  // OR add incrementally (keeps existing labels)
    "removedLabelIds": ["..."] // OR remove incrementally
  }

Only include the keys you want to change. For labels, use `labelIds` to replace
the whole set, or `addedLabelIds` / `removedLabelIds` for an incremental change —
do not mix `labelIds` with the incremental keys in the same call.

`create-issue` reads an IssueCreateInput JSON object from a file, e.g.:

  {
    "teamId": "...",          // required
    "title": "...",           // required
    "description": "...",     // markdown
    "assigneeId": "me",       // literal "me" is resolved to the viewer's id
    "priority": 3,
    "projectId": "...",
    "cycleId": "...",
    "projectMilestoneId": "...",
    "parentId": "...",
    "labelIds": ["..."],
    "estimate": 5,
    "stateId": "..."          // team-scoped (see SKILL.md note)
  }

Omit any field you can't resolve confidently.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

try:
    import requests
except ImportError:
    sys.exit(
        "error: linear_api.py requires 'requests'. Install with: pip install requests"
    )

GRAPHQL_URL = "https://api.linear.app/graphql"
IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d+$")


def _fail(error: str, message: str, **extra: object):
    """Print a structured JSON error to stdout and exit 1.

    Every failure carries `fallback: "mcp"` so the skill can warn the user and route
    to the Linear MCP tools. Output never includes request headers (the API key).
    """
    payload: dict = {"error": error, "message": message, "fallback": "mcp"}
    payload.update(extra)
    print(json.dumps(payload))
    sys.exit(1)


def _token() -> str:
    token = os.environ.get("LINEAR_API_KEY", "").strip()
    if not token:
        _fail(
            "no_key",
            "LINEAR_API_KEY is not set. Add it to ~/.claude/settings.json under the "
            "env block and restart Claude Code, or fall back to the Linear MCP tools.",
        )
    return token


def gql(query: str, variables: dict | None = None, soft: bool = False) -> dict | None:
    """POST a GraphQL request.

    A *GraphQL* error (e.g. entity not found) with `soft=True` returns None so the
    caller can try another path. Every other failure — missing key, network error,
    non-2xx HTTP, or a non-soft GraphQL error — prints a structured JSON error with
    `fallback: "mcp"` and exits 1, so the skill can warn the user and fall back to the
    Linear MCP tools. Error output never includes the request headers (the API key).
    """
    headers = {"Authorization": _token(), "Content-Type": "application/json"}
    try:
        resp = requests.post(
            GRAPHQL_URL,
            json={"query": query, "variables": variables or {}},
            headers=headers,
            timeout=30,
        )
    except requests.RequestException as exc:
        # Network / timeout: ambiguous for mutations (the write may have landed before
        # the connection dropped), so the skill must verify before retrying via MCP.
        _fail(
            "network_error",
            f"Could not reach the Linear API: {exc}",
            verify_before_retry=True,
        )
    if not resp.ok:
        # Non-2xx (401/403 invalid or expired key, 400, 429, 5xx). The request did not
        # take effect, so an MCP retry won't duplicate.
        _fail("api_error", f"Linear API returned HTTP {resp.status_code}: {resp.text}")
    data = resp.json()
    if "errors" in data:
        if soft:
            return None
        _fail("graphql_error", f"Linear GraphQL error: {json.dumps(data['errors'])}")
    return data["data"]


def _viewer_id() -> str:
    return gql("query { viewer { id } }")["viewer"]["id"]


def cmd_whoami(_args: argparse.Namespace) -> None:
    viewer = gql("query { viewer { id name email } }")["viewer"]
    print(json.dumps(viewer))


def cmd_current_cycle(args: argparse.Namespace) -> None:
    query = """
    query($teamId: ID!) {
      cycles(
        filter: { team: { id: { eq: $teamId } }, isActive: { eq: true } }
        first: 1
      ) { nodes { id name number startsAt endsAt } }
    }
    """
    nodes = gql(query, {"teamId": args.team})["cycles"]["nodes"]
    if not nodes:
        print(
            json.dumps(
                {
                    "error": "no_active_cycle",
                    "message": "No active cycle found for this team.",
                }
            )
        )
        return
    cycle = nodes[0]
    print(
        json.dumps(
            {
                "id": cycle["id"],
                "name": cycle.get("name") or f"Cycle {cycle['number']}",
                "number": cycle["number"],
            }
        )
    )


def cmd_milestones(args: argparse.Namespace) -> None:
    query = """
    query($id: String!) {
      project(id: $id) {
        projectMilestones(first: 50) { nodes { id name targetDate } }
      }
    }
    """
    project = gql(query, {"id": args.project})["project"]
    nodes = (project or {}).get("projectMilestones", {}).get("nodes", [])
    print(
        json.dumps(
            [
                {"id": m["id"], "name": m["name"], "targetDate": m.get("targetDate")}
                for m in nodes
            ]
        )
    )


def cmd_states(args: argparse.Namespace) -> None:
    """List a team's workflow states, so the skill can resolve "Done" per team.

    Workflow states are team-scoped, so the hardcoded Product/Design IDs in the
    SKILL.md don't apply to other teams. `type` is one of triage / backlog /
    unstarted / started / completed / canceled — pick the `completed` state to
    mark an issue done. Ordered by `position` (ascending) to mirror the team's
    board column order.
    """
    query = """
    query($teamId: ID!) {
      workflowStates(
        filter: { team: { id: { eq: $teamId } } }
      ) { nodes { id name type position } }
    }
    """
    nodes = gql(query, {"teamId": args.team})["workflowStates"]["nodes"]
    nodes.sort(key=lambda s: s.get("position", 0))
    print(
        json.dumps(
            [
                {
                    "id": s["id"],
                    "name": s["name"],
                    "type": s["type"],
                    "position": s.get("position"),
                }
                for s in nodes
            ]
        )
    )


def _normalize_issue(raw: dict) -> dict:
    return {
        "id": raw["id"],
        "identifier": raw["identifier"],
        "title": raw["title"],
        "url": raw["url"],
        "labels": (raw.get("labels") or {}).get("nodes", []),
    }


def cmd_find_issue(args: argparse.Namespace) -> None:
    term = args.query.strip()
    results: list[dict] = []

    # Exact identifier (e.g. PROD-2151) resolves directly; issue(id:) accepts the
    # human identifier as well as the UUID. Soft so a miss falls through to search.
    if IDENTIFIER_RE.match(term):
        by_id = gql(
            """
            query($id: String!) {
              issue(id: $id) {
                id identifier title url labels { nodes { id name } }
              }
            }
            """,
            {"id": term},
            soft=True,
        )
        issue = (by_id or {}).get("issue")
        if issue:
            results.append(issue)

    if not results:
        search = gql(
            """
            query($term: String!) {
              issues(
                filter: { title: { containsIgnoreCase: $term } }
                first: 10
                orderBy: updatedAt
              ) { nodes { id identifier title url labels { nodes { id name } } } }
            }
            """,
            {"term": term},
        )
        results = search["issues"]["nodes"]

    print(json.dumps([_normalize_issue(i) for i in results]))


def cmd_get_issue(args: argparse.Namespace) -> None:
    """Print the current state of one issue so the skill can show a before/after diff.

    Accepts a UUID or the human identifier (PROD-123). Prints
    {"error":"not_found"} (exit 0) when nothing matches, so the skill can tell
    "issue doesn't exist" apart from a transport failure (which exits 1).
    """
    issue = gql(
        """
        query($id: String!) {
          issue(id: $id) {
            id identifier title description url priority estimate
            state { id name type }
            assignee { id name }
            team { id name }
            project { id name }
            cycle { id name number }
            projectMilestone { id name }
            parent { id identifier title }
            labels { nodes { id name } }
          }
        }
        """,
        {"id": args.id.strip()},
        soft=True,
    )
    issue = (issue or {}).get("issue") if issue else None
    if not issue:
        print(
            json.dumps(
                {
                    "error": "not_found",
                    "message": f"No issue found for id '{args.id}'.",
                }
            )
        )
        return
    print(
        json.dumps(
            {
                "id": issue["id"],
                "identifier": issue["identifier"],
                "title": issue["title"],
                "description": issue.get("description"),
                "url": issue["url"],
                "priority": issue.get("priority"),
                "estimate": issue.get("estimate"),
                "state": issue.get("state"),
                "assignee": issue.get("assignee"),
                "team": issue.get("team"),
                "project": issue.get("project"),
                "cycle": issue.get("cycle"),
                "projectMilestone": issue.get("projectMilestone"),
                "parent": issue.get("parent"),
                "labels": (issue.get("labels") or {}).get("nodes", []),
            }
        )
    )


def cmd_create_issue(args: argparse.Namespace) -> None:
    with open(args.input_file, encoding="utf-8") as f:
        issue_input = json.load(f)

    if issue_input.get("assigneeId") == "me":
        issue_input["assigneeId"] = _viewer_id()

    res = gql(
        """
        mutation($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success issue { id identifier url }
          }
        }
        """,
        {"input": issue_input},
    )["issueCreate"]
    if not res.get("success"):
        sys.exit("issueCreate returned success=false")
    print(json.dumps(res["issue"]))


def cmd_update_issue(args: argparse.Namespace) -> None:
    """Apply an IssueUpdateInput to an existing issue.

    The JSON file must carry `id` (the issue UUID); it is popped out and sent as a
    separate GraphQL variable, while every remaining key becomes the
    IssueUpdateInput. `assigneeId: "me"` resolves to the viewer.
    """
    with open(args.input_file, encoding="utf-8") as f:
        payload = json.load(f)

    issue_id = payload.pop("id", None)
    if not issue_id:
        sys.exit("update-issue input JSON must include an 'id' field (the issue UUID).")

    if payload.get("assigneeId") == "me":
        payload["assigneeId"] = _viewer_id()

    if not payload:
        sys.exit("update-issue input JSON has no fields to change besides 'id'.")

    res = gql(
        """
        mutation($id: String!, $input: IssueUpdateInput!) {
          issueUpdate(id: $id, input: $input) {
            success issue { id identifier url }
          }
        }
        """,
        {"id": issue_id, "input": payload},
    )["issueUpdate"]
    if not res.get("success"):
        sys.exit("issueUpdate returned success=false")
    print(json.dumps(res["issue"]))


def cmd_comment(args: argparse.Namespace) -> None:
    with open(args.body_file, encoding="utf-8") as f:
        body = f.read()
    res = gql(
        """
        mutation($input: CommentCreateInput!) {
          commentCreate(input: $input) { success comment { id } }
        }
        """,
        {"input": {"issueId": args.issue, "body": body}},
    )["commentCreate"]
    if not res.get("success"):
        sys.exit("commentCreate returned success=false")
    print(json.dumps(res["comment"]))


def cmd_attach_url(args: argparse.Namespace) -> None:
    attach_input: dict = {"issueId": args.issue, "url": args.url}
    if args.title:
        attach_input["title"] = args.title
    res = gql(
        """
        mutation($input: AttachmentCreateInput!) {
          attachmentCreate(input: $input) { success attachment { id } }
        }
        """,
        {"input": attach_input},
    )["attachmentCreate"]
    if not res.get("success"):
        sys.exit("attachmentCreate returned success=false")
    print(json.dumps(res["attachment"]))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Linear GraphQL helper for update-linear-issues (PROD-2253)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("whoami", help="Print the authenticated viewer {id,name,email}.")

    p_cycle = sub.add_parser("current-cycle", help="Active cycle for a team.")
    p_cycle.add_argument("--team", required=True, help="Team id (UUID).")

    p_ms = sub.add_parser("milestones", help="Milestones for a project.")
    p_ms.add_argument("--project", required=True, help="Project id (UUID).")

    p_states = sub.add_parser("states", help="Workflow states for a team (resolve Done).")
    p_states.add_argument("--team", required=True, help="Team id (UUID).")

    p_find = sub.add_parser("find-issue", help="Find issue(s) by identifier or title.")
    p_find.add_argument("--query", required=True, help="Identifier (PROD-123) or title text.")

    p_get = sub.add_parser("get-issue", help="Get one issue's current state (for a diff).")
    p_get.add_argument("--id", required=True, help="Issue UUID or identifier (PROD-123).")

    p_create = sub.add_parser("create-issue", help="Create an issue from an IssueCreateInput JSON file.")
    p_create.add_argument("--input-file", required=True, help="Path to JSON file.")

    p_update = sub.add_parser("update-issue", help="Update an issue from an IssueUpdateInput JSON file (must include 'id').")
    p_update.add_argument("--input-file", required=True, help="Path to JSON file.")

    p_comment = sub.add_parser("comment", help="Add a comment to an issue.")
    p_comment.add_argument("--issue", required=True, help="Issue id (UUID).")
    p_comment.add_argument("--body-file", required=True, help="Path to markdown body file.")

    p_attach = sub.add_parser("attach-url", help="Attach a URL to an issue.")
    p_attach.add_argument("--issue", required=True, help="Issue id (UUID).")
    p_attach.add_argument("--url", required=True, help="URL to attach.")
    p_attach.add_argument("--title", default=None, help="Optional attachment title.")

    args = parser.parse_args()

    handlers = {
        "whoami": cmd_whoami,
        "current-cycle": cmd_current_cycle,
        "milestones": cmd_milestones,
        "states": cmd_states,
        "find-issue": cmd_find_issue,
        "get-issue": cmd_get_issue,
        "create-issue": cmd_create_issue,
        "update-issue": cmd_update_issue,
        "comment": cmd_comment,
        "attach-url": cmd_attach_url,
    }
    handlers[args.cmd](args)


if __name__ == "__main__":
    main()
