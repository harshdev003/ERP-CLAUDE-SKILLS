#!/usr/bin/env python3
"""Patch a Spinnaker pipeline JSON to add a new cronjob's dev+prod deploy stages.

Given a pipeline JSON (fetched via `spin pipeline get --name <pipeline> --application
<app>`, or exported by hand from the Spinnaker UI), computes:

  - a new dev `patchManifest` stage for `cronJob <cronjob-name>`, depending on the
    app's existing "deploy to dev" stage
  - a new prod `patchManifest` stage, depending on the app's existing "promote to
    prod" stage
  - the updated `requisiteStageRefIds` for the "check tag version" precondition
    stage, gaining the new dev stage's refId

and writes the patched pipeline JSON back out — ready for `spin pipeline save
--file <path>`, or for hand-pasting into the Spinnaker UI's JSON editor if `spin`
isn't set up (see references/spinnaker-stage-template.md).

Stage-name matching is heuristic (case-insensitive substring), since pipeline JSON
has no stable machine-readable "role" field for these stages. Pass explicit
--dev-deploy-refid / --promote-prod-refid / --precondition-refid to override
when the heuristic can't find a unique match.

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import sys


def find_stage_by_name_substring(stages: list[dict], substrings: list[str]) -> list[dict]:
    matches = []
    for stage in stages:
        name = (stage.get("name") or "").lower()
        if any(s in name for s in substrings):
            matches.append(stage)
    return matches


def find_precondition_stage(stages: list[dict]) -> list[dict]:
    matches = []
    for stage in stages:
        if stage.get("type") == "checkPreconditions" and "tag version" in (stage.get("name") or "").lower():
            matches.append(stage)
    return matches


def resolve_single_match(matches: list[dict], label: str, override: str | None) -> str:
    if override:
        return override
    if len(matches) == 1:
        return matches[0]["refId"]
    if not matches:
        print(json.dumps({
            "error": "stage_not_found",
            "message": f"Could not find a unique '{label}' stage by name heuristic. Pass an explicit refid override.",
        }))
        sys.exit(1)
    print(json.dumps({
        "error": "ambiguous_stage_match",
        "message": f"Found {len(matches)} candidate stages for '{label}': {[m['name'] for m in matches]}. Pass an explicit refid override.",
    }))
    sys.exit(1)


def next_refids(stages: list[dict], count: int) -> list[str]:
    numeric_refids = [int(s["refId"]) for s in stages if str(s.get("refId", "")).isdigit()]
    start = (max(numeric_refids) + 1) if numeric_refids else 1
    return [str(start + i) for i in range(count)]


def build_stage(
    *,
    account: str,
    app: str,
    cronjob_name: str,
    container_name: str,
    human_job_name: str,
    ref_id: str,
    requisite_ref_id: str,
) -> dict:
    return {
        "account": account,
        "app": app,
        "cloudProvider": "kubernetes",
        "location": "aura-services",
        "manifestName": f"cronJob {cronjob_name}",
        "mode": "static",
        "name": f"Deploy {human_job_name} job to {'dev' if 'dev' in account else 'prod'}",
        "options": {"mergeStrategy": "strategic", "record": False},
        "patchBody": [
            {
                "spec": {
                    "jobTemplate": {
                        "spec": {
                            "template": {
                                "spec": {
                                    "containers": [
                                        {"image": "${ trigger['payload']['tag']}", "name": container_name}
                                    ]
                                }
                            }
                        }
                    }
                }
            }
        ],
        "refId": ref_id,
        "requisiteStageRefIds": [requisite_ref_id],
        "source": "text",
        "type": "patchManifest",
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipeline-file", required=True, help="path to the pipeline JSON (from `spin pipeline get` or UI export)")
    parser.add_argument("--app", required=True, help='Spinnaker application name, e.g. "intelligence"')
    parser.add_argument("--cronjob-name", required=True, help="CronJob metadata.name, e.g. intelligence-service-feed-schedule-reminders")
    parser.add_argument("--container-name", help="defaults to --cronjob-name (matches every existing example)")
    parser.add_argument("--human-job-name", required=True, help='human label for stage names, e.g. "feed schedule reminders"')
    parser.add_argument("--dev-deploy-refid", default=None)
    parser.add_argument("--promote-prod-refid", default=None)
    parser.add_argument("--precondition-refid", default=None)
    parser.add_argument("--write", help="write patched pipeline JSON to this path (default: stdout)")
    args = parser.parse_args()

    with open(args.pipeline_file) as f:
        pipeline = json.load(f)

    stages = pipeline.get("stages", [])
    container_name = args.container_name or args.cronjob_name

    dev_deploy_refid = resolve_single_match(
        find_stage_by_name_substring(stages, ["deploy to dev"]),
        "deploy to dev", args.dev_deploy_refid,
    )
    promote_prod_refid = resolve_single_match(
        find_stage_by_name_substring(stages, ["promote to prod"]),
        "promote to prod", args.promote_prod_refid,
    )
    precondition_matches = find_precondition_stage(stages)
    precondition_refid = resolve_single_match(precondition_matches, "check tag version", args.precondition_refid)

    next_dev_refid, next_prod_refid = next_refids(stages, 2)

    dev_stage = build_stage(
        account="kubernetes-dev-account", app=args.app, cronjob_name=args.cronjob_name,
        container_name=container_name, human_job_name=args.human_job_name,
        ref_id=next_dev_refid, requisite_ref_id=dev_deploy_refid,
    )
    prod_stage = build_stage(
        account="kubernetes-prod-account", app=args.app, cronjob_name=args.cronjob_name,
        container_name=container_name, human_job_name=args.human_job_name,
        ref_id=next_prod_refid, requisite_ref_id=promote_prod_refid,
    )

    for stage in stages:
        if stage.get("refId") == precondition_refid:
            requisites = stage.setdefault("requisiteStageRefIds", [])
            if next_dev_refid not in requisites:
                requisites.append(next_dev_refid)

    stages.append(dev_stage)
    stages.append(prod_stage)
    pipeline["stages"] = stages

    output = json.dumps(pipeline, indent=2)
    if args.write:
        with open(args.write, "w") as f:
            f.write(output)
        print(json.dumps({
            "written": args.write,
            "dev_stage_refid": next_dev_refid,
            "prod_stage_refid": next_prod_refid,
            "precondition_refid": precondition_refid,
        }, indent=2))
    else:
        print(output)


if __name__ == "__main__":
    main()
