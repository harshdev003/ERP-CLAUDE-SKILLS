"""
circleci_api.py — CircleCI REST helper for investigate-circleci / rerun-circleci.

Canonical copy lives in:
  plugins/eng/skills/investigate-circleci/scripts/circleci_api.py

Consumers that ship their own copy (self-contained per Architecture Principle 1):
  plugins/eng/skills/rerun-circleci/scripts/circleci_api.py

Edit the canonical first, then propagate to every copy in the same PR.

Auth: Circle-Token header. Token resolution order:
  CIRCLE_TOKEN -> CIRCLECI_TOKEN -> CIRCLE_CI_TOKEN -> CIRCLECI_API_TOKEN

API versions used:
  v1.1  GET  /api/v1.1/project/{slug}/{build}          job steps + output URLs
  v2    GET  /api/v2/pipeline                           list pipelines for branch
  v2    GET  /api/v2/pipeline/{id}/workflow             list workflows in pipeline
  v2    GET  /api/v2/workflow/{id}/job                  list jobs in workflow
  v2    POST /api/v2/workflow/{id}/rerun                rerun (from_failed=true)

Stdlib only — no third-party imports except requests (required).
"""

import os
import subprocess
import sys
import time

try:
    import requests
except ImportError:
    print("ERROR: 'requests' is not installed. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

BASE_V1 = "https://circleci.com/api/v1.1"
BASE_V2 = "https://circleci.com/api/v2"
DEFAULT_PROJECT = "gh/AuraHealth/AuraConsumerApp"


def _resolve_token() -> str:
    """Resolve CIRCLE_TOKEN from env, trying fallback names."""
    for name in ("CIRCLE_TOKEN", "CIRCLECI_TOKEN", "CIRCLE_CI_TOKEN", "CIRCLECI_API_TOKEN"):
        val = os.environ.get(name, "").strip()
        if val:
            return val
    return ""


def _headers(token: str) -> dict:
    return {"Circle-Token": token, "Accept": "application/json"}


def _get(url: str, token: str, params: dict | None = None, retries: int = 3) -> dict:
    """GET with retry on 429/5xx."""
    for attempt in range(retries):
        resp = requests.get(url, headers=_headers(token), params=params, timeout=30)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 2 ** attempt))
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return {}


def _post(url: str, token: str, body: dict) -> dict:
    resp = requests.post(url, headers=_headers(token), json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def get_token(exit_on_missing: bool = True) -> str:
    token = _resolve_token()
    if not token and exit_on_missing:
        print(
            "ERROR: No CircleCI token found.\n"
            "Create a personal token at https://app.circleci.com/settings/user/tokens\n"
            "then add it to ~/.claude/settings.json under the 'env' block as CIRCLE_TOKEN.\n"
            "Restart Claude Code after adding it.\n"
            "Note: use a PERSONAL token — project tokens are rejected by the v2 rerun API.",
            file=sys.stderr,
        )
        sys.exit(1)
    return token


# ---------------------------------------------------------------------------
# v2 pipeline / workflow / job APIs
# ---------------------------------------------------------------------------

def list_pipelines_for_branch(token: str, branch: str, project: str = DEFAULT_PROJECT) -> list[dict]:
    """Return pipelines for a branch, most-recent first."""
    url = f"{BASE_V2}/project/{project}/pipeline"
    data = _get(url, token, params={"branch": branch})
    return data.get("items", [])


def _head_branch_for_pr(pr_number: int, project: str = DEFAULT_PROJECT) -> str | None:
    """Resolve a PR's head branch via the gh CLI.

    `project` is a CircleCI slug like 'gh/OWNER/REPO'; the GitHub repo is OWNER/REPO.
    Returns None if gh is unavailable, unauthenticated, or the PR can't be resolved.
    """
    parts = project.split("/")
    if len(parts) < 3:
        return None
    repo = f"{parts[1]}/{parts[2]}"
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--repo", repo,
             "--json", "headRefName", "-q", ".headRefName"],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None
    except Exception:
        return None


def list_pipelines_for_pr(token: str, pr_number: int, project: str = DEFAULT_PROJECT) -> list[dict]:
    """Return pipelines for a PR, most-recent first.

    A PR pipeline's `vcs.branch` is the actual head branch (e.g. 'feat/foo'), not
    'pull/<n>' — so we resolve the head branch via the gh CLI and list that branch's
    pipelines. `list_pipelines_for_branch` URL-encodes the branch, so slash-containing
    names work. Falls back to a paginated scan matching `vcs.pull_requests` / pull-branch
    patterns when gh can't resolve the branch (gh unavailable, or a fork PR).
    """
    branch = _head_branch_for_pr(pr_number, project)
    if branch:
        pipelines = list_pipelines_for_branch(token, branch, project)
        if pipelines:
            return pipelines

    # Fallback: scan recent pipelines and match the PR by pull_requests url or pull/N branch.
    url = f"{BASE_V2}/project/{project}/pipeline"
    all_items: list[dict] = []
    next_token = None
    for _ in range(10):  # max 10 pages
        params: dict = {}
        if next_token:
            params["page-token"] = next_token
        data = _get(url, token, params=params)
        all_items.extend(data.get("items", []))
        next_token = data.get("next_page_token")
        if not next_token:
            break

    matched = []
    for p in all_items:
        vcs = p.get("vcs", {})
        prs = vcs.get("pull_requests", []) or []
        for pr in prs:
            url_str = pr.get("url", "")
            if url_str.endswith(f"/pull/{pr_number}") or url_str.endswith(f"/pulls/{pr_number}"):
                matched.append(p)
                break
        b = vcs.get("branch", "")
        if b in (f"pull/{pr_number}", f"pull/{pr_number}/head") and p not in matched:
            matched.append(p)

    return matched


def get_pipeline_workflows(token: str, pipeline_id: str) -> list[dict]:
    url = f"{BASE_V2}/pipeline/{pipeline_id}/workflow"
    data = _get(url, token)
    return data.get("items", [])


def get_workflow_jobs(token: str, workflow_id: str) -> list[dict]:
    url = f"{BASE_V2}/workflow/{workflow_id}/job"
    data = _get(url, token)
    return data.get("items", [])


def rerun_workflow(token: str, workflow_id: str, from_failed: bool = True) -> dict:
    """POST /api/v2/workflow/{id}/rerun. Requires a personal token."""
    url = f"{BASE_V2}/workflow/{workflow_id}/rerun"
    return _post(url, token, {"from_failed": from_failed})


# ---------------------------------------------------------------------------
# v1.1 job log APIs
# ---------------------------------------------------------------------------

def get_build(token: str, build_number: int, project: str = DEFAULT_PROJECT) -> dict:
    url = f"{BASE_V1}/project/{project}/{build_number}"
    return _get(url, token)


def get_build_output(token: str, output_url: str) -> list[dict]:
    """Fetch a step's output (list of {action, message, ...} dicts)."""
    resp = requests.get(output_url, headers=_headers(token), timeout=60)
    resp.raise_for_status()
    return resp.json()


def get_job_log_tail(token: str, build_number: int, max_lines: int = 200,
                     project: str = DEFAULT_PROJECT) -> tuple[str, str]:
    """
    Return (log_tail, circleci_url) for a build.
    Concatenates all step outputs and returns the last max_lines lines.
    """
    build = get_build(token, build_number, project)
    circleci_url = build.get("build_url", f"https://app.circleci.com/pipelines/{project}/{build_number}")

    lines: list[str] = []
    for step in build.get("steps", []):
        for action in step.get("actions", []):
            output_url = action.get("output_url")
            if output_url:
                try:
                    outputs = get_build_output(token, output_url)
                    for out in outputs:
                        msg = out.get("message", "")
                        lines.extend(msg.splitlines())
                except Exception:
                    pass

    tail = "\n".join(lines[-max_lines:]) if lines else "(no output captured)"
    return tail, circleci_url


# ---------------------------------------------------------------------------
# URL parser
# ---------------------------------------------------------------------------

def parse_circleci_url(url: str) -> dict:
    """
    Parse a CircleCI URL into its components.
    Returns a dict with keys: project, pipeline_num, workflow_id, job_num, build_num.
    All values are None if not present in the URL.
    """
    import re
    result: dict = {
        "project": None,
        "pipeline_num": None,
        "workflow_id": None,
        "job_num": None,
        "build_num": None,
    }

    # https://app.circleci.com/pipelines/gh/AuraHealth/AuraConsumerApp/4597/workflows/fd392e6d-...
    m = re.search(
        r"pipelines/([^/]+/[^/]+/[^/]+)/(\d+)(?:/workflows/([0-9a-f-]+))?",
        url,
    )
    if m:
        result["project"] = m.group(1)
        result["pipeline_num"] = int(m.group(2))
        result["workflow_id"] = m.group(3)

    # https://app.circleci.com/jobs/gh/AuraHealth/AuraConsumerApp/1234
    m2 = re.search(r"jobs/([^/]+/[^/]+/[^/]+)/(\d+)", url)
    if m2:
        result["project"] = m2.group(1)
        result["build_num"] = int(m2.group(2))

    return result
