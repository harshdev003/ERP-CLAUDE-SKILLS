#!/usr/bin/env python3
"""
rerun.py — eng:rerun-circleci entry point.

Reruns a CircleCI workflow (from_failed=True) via the v2 API after user confirmation.
Requires a personal CircleCI token — project tokens receive 403.

Usage:
  python3 rerun.py --workflow <uuid>
  python3 rerun.py --pr 1234
  python3 rerun.py --url https://app.circleci.com/pipelines/...
  python3 rerun.py --workflow <uuid> --confirm   # skip interactive prompt
"""

import argparse
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(__file__))
from circleci_api import (
    DEFAULT_PROJECT,
    get_token,
    list_pipelines_for_pr,
    get_pipeline_workflows,
    get_workflow_jobs,
    rerun_workflow,
    parse_circleci_url,
    BASE_V2,
    _get,
)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Rerun a failed CircleCI workflow")
    parser.add_argument("--workflow", help="CircleCI workflow UUID")
    parser.add_argument("--pr", type=int, help="GitHub PR number — resolves latest failed workflow")
    parser.add_argument("--url", help="CircleCI pipeline/workflow URL")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="CircleCI project slug")
    parser.add_argument("--confirm", action="store_true", help="Skip interactive confirmation prompt")
    args = parser.parse_args()

    token = get_token()
    project = args.project
    workflow_id: str | None = args.workflow

    # -- Resolve workflow ID --------------------------------------------------

    if args.url:
        parsed = parse_circleci_url(args.url)
        if parsed["project"]:
            project = parsed["project"]
        if parsed["workflow_id"]:
            workflow_id = parsed["workflow_id"]

    if not workflow_id and args.pr:
        workflow_id = _resolve_workflow_for_pr(token, args.pr, project)
        if not workflow_id:
            return

    if not workflow_id:
        print("Provide --workflow, --pr, or --url.")
        sys.exit(1)

    # -- Fetch workflow info for confirmation ---------------------------------

    try:
        wf_data = _get(f"{BASE_V2}/workflow/{workflow_id}", token)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            print(f"Workflow {workflow_id} not found.")
        else:
            print(f"Failed to fetch workflow: {exc}")
        sys.exit(1)

    wf_name = wf_data.get("name", "?")
    wf_status = wf_data.get("status", "?")

    if wf_status == "success":
        print(f"Workflow '{wf_name}' ({workflow_id}) already succeeded — skipping rerun.")
        sys.exit(0)

    # Fetch failed job names for the summary
    try:
        jobs = get_workflow_jobs(token, workflow_id)
        failed_job_names = [j.get("name", "?") for j in jobs if j.get("status") in ("failed", "failing")]
    except Exception:
        failed_job_names = ["(could not fetch job list)"]

    failed_jobs_str = ", ".join(failed_job_names) if failed_job_names else "(none — rerun anyway?)"

    # -- Confirmation ---------------------------------------------------------

    print("Rerun summary")
    print("=============")
    print(f"Project:       {project}")
    print(f"Workflow:      {workflow_id}")
    print(f"Workflow name: {wf_name}  (status: {wf_status})")
    print(f"Jobs to retry: {failed_jobs_str}")
    print(f"Mode:          from_failed=true")

    if not args.confirm:
        print()
        answer = input("Confirm rerun? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print("Rerun cancelled.")
            sys.exit(0)

    # -- Post rerun -----------------------------------------------------------

    try:
        result = rerun_workflow(token, workflow_id, from_failed=True)
    except requests.HTTPError as exc:
        if exc.response is not None:
            status = exc.response.status_code
            body = ""
            try:
                body = exc.response.json().get("message", exc.response.text)
            except Exception:
                body = exc.response.text
            if status == 403:
                print(
                    "ERROR: 403 Forbidden — the rerun endpoint requires a personal token,\n"
                    "not a project token.\n"
                    "Create a personal token at https://app.circleci.com/settings/user/tokens\n"
                    "and update CIRCLE_TOKEN in ~/.claude/settings.json."
                )
            elif status == 409:
                print(f"ERROR: 409 Conflict — {body}\n(A rerun may already be in progress.)")
            else:
                print(f"ERROR: HTTP {status} — {body}")
        else:
            print(f"ERROR: {exc}")
        sys.exit(1)

    new_workflow_id = result.get("workflow_id", "?")
    print(f"\nRerun triggered — new workflow ID: {new_workflow_id}")
    print(f"-> Next step: investigate-circleci --workflow {new_workflow_id}   (check the result)")


def _resolve_workflow_for_pr(token: str, pr_number: int, project: str) -> str | None:
    """Return the workflow ID of the latest failed workflow for a PR."""
    pipelines = list_pipelines_for_pr(token, pr_number, project)
    if not pipelines:
        print(f"No pipelines found for PR #{pr_number} in {project}.")
        return None

    pipelines.sort(key=lambda p: p.get("number", 0), reverse=True)
    pipeline = pipelines[0]
    pipeline_id = pipeline["id"]

    workflows = get_pipeline_workflows(token, pipeline_id)
    failed = [w for w in workflows if w.get("status") in ("failed", "failing")]
    if not failed:
        statuses = ", ".join(w.get("status", "?") for w in workflows)
        print(f"No failed workflows in pipeline #{pipeline.get('number')} for PR #{pr_number}.")
        print(f"Workflow statuses: {statuses}")
        return None

    return failed[0]["id"]


if __name__ == "__main__":
    main()
