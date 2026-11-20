#!/usr/bin/env python3
"""
investigate.py — eng:investigate-circleci entry point.

Usage:
  python3 investigate.py --pr 1234
  python3 investigate.py --workflow <uuid>
  python3 investigate.py --build 4597
  python3 investigate.py --url https://app.circleci.com/pipelines/...
  python3 investigate.py --pr 1234 --project gh/AuraHealth/SomeOtherRepo
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from circleci_api import (
    DEFAULT_PROJECT,
    get_token,
    list_pipelines_for_pr,
    list_pipelines_for_branch,
    get_pipeline_workflows,
    get_workflow_jobs,
    get_build,
    get_job_log_tail,
    parse_circleci_url,
)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Investigate a CircleCI CI failure")
    parser.add_argument("--pr", type=int, help="GitHub PR number")
    parser.add_argument("--workflow", help="CircleCI workflow UUID")
    parser.add_argument("--build", type=int, help="CircleCI v1.1 build number")
    parser.add_argument("--url", help="CircleCI pipeline/workflow URL")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="CircleCI project slug")
    parser.add_argument("--max-lines", type=int, default=200, help="Log tail length")
    args = parser.parse_args()

    token = get_token()

    # -- Resolve inputs -------------------------------------------------------

    workflow_id: str | None = args.workflow
    build_number: int | None = args.build
    project = args.project
    pr_num: int | None = args.pr
    pipeline_num: int | None = None

    if args.url:
        parsed = parse_circleci_url(args.url)
        if parsed["project"]:
            project = parsed["project"]
        if parsed["workflow_id"]:
            workflow_id = parsed["workflow_id"]
        if parsed["build_num"]:
            build_number = parsed["build_num"]
        if parsed["pipeline_num"]:
            pipeline_num = parsed["pipeline_num"]

    # -- Fetch pipeline/workflow -----------------------------------------------

    if build_number and not workflow_id:
        _investigate_build(token, build_number, project, args.max_lines)
        return

    if not workflow_id:
        pipeline = _resolve_pipeline(token, pr_num, pipeline_num, project)
        if not pipeline:
            return
        pipeline_num = pipeline.get("number", pipeline_num)
        pipeline_id = pipeline["id"]
        branch = pipeline.get("vcs", {}).get("branch", "unknown")
        print(f"Pipeline: #{pipeline_num}  Branch: {branch}")
        workflows = get_pipeline_workflows(token, pipeline_id)
        failed = [w for w in workflows if w.get("status") in ("failed", "failing")]
        if not failed:
            statuses = ", ".join(w.get("status", "?") for w in workflows) or "none"
            print(f"\nNo failed workflows found. Pipeline statuses: {statuses}")
            print("(Workflow may have already been rerun or is still running.)")
            return
        # Use the first failed workflow (most recent)
        workflow = failed[0]
        workflow_id = workflow["id"]
        print(f"Workflow: {workflow.get('name', '?')}  Status: {workflow.get('status', '?')}")

    # -- Inspect workflow jobs -------------------------------------------------

    jobs = get_workflow_jobs(token, workflow_id)
    failed_jobs = [j for j in jobs if j.get("status") in ("failed", "failing")]
    if not failed_jobs:
        all_statuses = ", ".join(f"{j.get('name','?')}:{j.get('status','?')}" for j in jobs)
        print(f"\nWorkflow {workflow_id} — no failed jobs found.")
        print(f"Job statuses: {all_statuses}")
        return

    print(f"\nFailed jobs ({len(failed_jobs)}):")
    for j in failed_jobs:
        duration_s = j.get("duration_seconds") or j.get("duration")
        dur_str = f"  Duration: {duration_s}s" if duration_s is not None else ""
        print(f"  - {j.get('name', '?')}  (#{j.get('job_number', '?')}){dur_str}")

    # Investigate the first failed job
    job = failed_jobs[0]
    job_num: int | None = job.get("job_number")
    job_name = job.get("name", "?")
    duration_s = job.get("duration_seconds") or job.get("duration")

    print(f"\n--- Job: {job_name} ---")

    if job_num:
        _investigate_build(token, job_num, project, args.max_lines,
                           job_name=job_name, duration_s=duration_s,
                           workflow_id=workflow_id)
    else:
        print("(No build number available — cannot fetch log.)")
        print(f"\nClassification: UNKNOWN")
        print(f"Reason: job_number missing from API response")
        print(f"\n-> Next step: open https://app.circleci.com/pipelines/{project} and inspect manually")


def _resolve_pipeline(token: str, pr_num: int | None, pipeline_num: int | None,
                      project: str) -> dict | None:
    if pr_num:
        pipelines = list_pipelines_for_pr(token, pr_num, project)
        if not pipelines:
            print(f"No pipelines found for PR #{pr_num} in {project}.")
            print("Check that CI ran for this PR, or try --workflow <id> directly.")
            return None
        # Most recent first (highest pipeline number)
        pipelines.sort(key=lambda p: p.get("number", 0), reverse=True)
        return pipelines[0]

    if pipeline_num:
        # No direct "get pipeline by number" endpoint — list and filter
        from circleci_api import BASE_V2, _get
        data = _get(f"{BASE_V2}/project/{project}/pipeline", token)
        for p in data.get("items", []):
            if p.get("number") == pipeline_num:
                return p
        print(f"Pipeline #{pipeline_num} not found in {project}.")
        return None

    print("Provide --pr, --workflow, --build, or --url.")
    return None


def _investigate_build(token: str, build_number: int, project: str, max_lines: int,
                       job_name: str = "", duration_s: float | None = None,
                       workflow_id: str | None = None) -> None:
    try:
        log_tail, circleci_url = get_job_log_tail(token, build_number, max_lines, project)
    except Exception as exc:
        print(f"Could not fetch log for build #{build_number}: {exc}")
        print(f"\nClassification: UNKNOWN")
        print(f"Reason: log fetch failed")
        if workflow_id:
            print(f"\n-> Next step: open {circleci_url_for_workflow(project, workflow_id)}")
        return

    classification, reason = _classify(log_tail, duration_s)

    print(f"\nFailure output (last {max_lines} lines):")
    print("  " + "\n  ".join(log_tail.splitlines()[-50:]))

    print(f"\nClassification: {classification}")
    print(f"Reason: {reason}")

    if classification == "INFRA":
        if workflow_id:
            print(f"\n-> Next step: rerun-circleci --workflow {workflow_id}")
        else:
            print(f"\n-> Next step: rerun-circleci --build {build_number}")
    elif classification == "REAL":
        print(f"\n-> Next step: fix the issue described above, then push a new commit")
    else:
        print(f"\n-> Next step: review full log at {circleci_url}")


def circleci_url_for_workflow(project: str, workflow_id: str) -> str:
    return f"https://app.circleci.com/pipelines/{project}?filter=all"


def _classify(log: str, duration_s: float | None) -> tuple[str, str]:
    """Return (classification, reason). Classification: INFRA | REAL | UNKNOWN."""
    lower = log.lower()

    # Duration heuristic — zero or very short duration on a normally-long job
    if duration_s is not None and duration_s <= 5:
        return "INFRA", f"job completed in {duration_s}s — likely infra/runner issue (normal run takes much longer)"

    # Network / infra error strings
    infra_signals = [
        ("context deadline exceeded", "context deadline exceeded — network/timeout infra issue"),
        ("i/o timeout", "i/o timeout — network/infra issue"),
        ("connection reset by peer", "connection reset — network/infra issue"),
        ("connection refused", "connection refused — infra/service unavailability"),
        ("resource temporarily unavailable", "resource temporarily unavailable — infra issue"),
        ("no space left on device", "out of disk — runner infra issue"),
        ("error: exit status 70", "simulator boot failure (exit 70) — xcode infra issue"),
        ("sigkill", "process killed (SIGKILL) — likely OOM or runner issue"),
        ("xcode license", "Xcode license not accepted on runner — infra issue"),
        # Git checkout / VCS-fetch failures — the runner couldn't reach GitHub, so
        # the job dies before building. Transient network/infra, safe to rerun.
        ("no route to host", "git checkout couldn't reach the remote (No route to host) — network/infra issue"),
        ("could not read from remote repository", "git checkout failed to read the remote — network/infra issue"),
        ("from promisor remote", "promisor remote fetch failed during checkout — network/infra issue"),
        ("failed to checkout: exit status 128", "git checkout failed (exit 128) — network/infra issue"),
        # A dependency the job downloads — a tool installer, a package index — answered with a 5xx or
        # a rate limit. The server refused service; nothing about the commit is at fault, which is
        # exactly what a rerun is for. Keyed on the HTTP status rather than on curl's exit 22, which
        # also covers a 404: a wrong URL or a version that no longer exists is a REAL defect, and
        # calling it infra would send someone rerunning a job that can never pass.
        ("returned error: 500", "download got HTTP 500 — transient upstream/CDN failure"),
        ("returned error: 502", "download got HTTP 502 — transient upstream/CDN failure"),
        ("returned error: 503", "download got HTTP 503 — transient upstream/CDN failure"),
        ("returned error: 504", "download got HTTP 504 — transient upstream/CDN failure"),
        ("returned error: 429", "download was rate-limited (HTTP 429) — retry later"),
        ("500 internal server error", "HTTP 500 from an upstream service — transient"),
        ("502 bad gateway", "HTTP 502 Bad Gateway — transient upstream failure"),
        ("503 service unavailable", "HTTP 503 Service Unavailable — transient upstream failure"),
        ("504 gateway time-out", "HTTP 504 Gateway Timeout — transient upstream failure"),
        ("504 gateway timeout", "HTTP 504 Gateway Timeout — transient upstream failure"),
        ("429 too many requests", "rate-limited by an upstream service (HTTP 429) — retry later"),
        # curl transport failures: no HTTP response arrived at all, so the repo cannot be the cause.
        ("curl: (6)", "curl could not resolve host (6) — DNS/network infra issue"),
        ("curl: (7)", "curl could not connect (7) — network/infra issue"),
        ("curl: (28)", "curl timed out (28) — network/infra issue"),
        ("curl: (35)", "curl TLS connect error (35) — network/infra issue"),
        ("curl: (52)", "curl got an empty reply from the server (52) — upstream flake"),
        ("curl: (55)", "curl send failure (55) — network/infra issue"),
        ("curl: (56)", "curl receive failure (56) — network/infra issue"),
        ("temporary failure in name resolution", "DNS resolution failed — network/infra issue"),
    ]
    for signal, reason in infra_signals:
        if signal in lower:
            return "INFRA", reason

    # Real failure signals
    real_signals = [
        ("swiftlint", "SwiftLint violation — code quality issue"),
        ("build failed", "xcodebuild reported Build FAILED"),
        ("error: ", "compile or configuration error"),
        ("test failure", "unit test failure"),
        ("assertion failed", "assertion failure in tests"),
        ("fatal error", "fatal error in build or tests"),
    ]
    for signal, reason in real_signals:
        if signal in lower:
            return "REAL", reason

    if not log.strip() or log == "(no output captured)":
        return "UNKNOWN", "no log output captured"

    return "UNKNOWN", "could not classify from log content — review manually"


if __name__ == "__main__":
    main()
