#!/usr/bin/env python3
"""Scaffold a new AuraInfra CronJob manifest from a service's existing sibling cronjobs.

Reads every top-level `*.yaml` CronJob manifest directly under a service directory
(e.g. `k8s/apps/services/intelligence-service/*.yaml` — NOT its `dev/`/`prod/`
subfolders, which are separate per-environment overlays) and infers conventions:
slug style, `resources.requests`, whether `activeDeadlineSeconds` is used, the
`envFrom` secret name, and the image reference currently in use. Renders a new
manifest matching those conventions.

Text/regex-based, not a YAML library — matches this plugin's stdlib-only convention
and avoids a PyYAML dependency for what is, across 107 existing manifests, a highly
regular and line-oriented format (see ENG-905 exploration notes).

Modes:
  --discover-only   print the discovered conventions as JSON, do not render anything
  (default)         render a new manifest to stdout
  --write           also write the rendered manifest to
                    <service-dir>/<slug>cronjob.yaml
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

NAME_RE = re.compile(r"^\s*name:\s*(\S+)\s*$", re.MULTILINE)
NAMESPACE_RE = re.compile(r"^\s*namespace:\s*(\S+)\s*$", re.MULTILINE)
SCHEDULE_RE = re.compile(r'schedule:\s*"([^"]+)"\s*(#.*)?')
ACTIVE_DEADLINE_RE = re.compile(r"activeDeadlineSeconds:\s*(\d+)")
MEMORY_RE = re.compile(r'memory:\s*"([^"]+)"')
CPU_RE = re.compile(r'cpu:\s*"([^"]+)"')
SECRET_NAME_RE = re.compile(r"secretRef:\s*\n\s*name:\s*(\S+)", re.MULTILINE)
IMAGE_RE = re.compile(r"image:\s*(\S+)")
ARGS_JOB_RE = re.compile(r'args:\s*\["run",\s*"(job:\S+?)"\]')


def discover(service_dir: Path) -> dict:
    manifests = sorted(service_dir.glob("*.yaml"))
    if not manifests:
        return {"service_dir": str(service_dir), "sibling_count": 0}

    names, namespaces, memories, cpus, secrets, images, job_slugs = [], [], [], [], [], [], []
    active_deadline_count = 0

    for path in manifests:
        text = path.read_text()
        if name_match := NAME_RE.search(text):
            names.append(name_match.group(1))
        if ns_match := NAMESPACE_RE.search(text):
            namespaces.append(ns_match.group(1))
        if ACTIVE_DEADLINE_RE.search(text):
            active_deadline_count += 1
        for m in MEMORY_RE.finditer(text):
            memories.append(m.group(1))
        for m in CPU_RE.finditer(text):
            cpus.append(m.group(1))
        if secret_match := SECRET_NAME_RE.search(text):
            secrets.append(secret_match.group(1))
        if image_match := IMAGE_RE.search(text):
            images.append(image_match.group(1))
        if job_match := ARGS_JOB_RE.search(text):
            job_slugs.append(job_match.group(1))

    def most_common(items: list[str]) -> str | None:
        return Counter(items).most_common(1)[0][0] if items else None

    # Hyphenated slugs (e.g. feed-schedule-reminders) vs camelCase (e.g. sendcoachautomatedmessage).
    hyphenated = sum(1 for n in names if "-" in n.split(service_dir.name + "-", 1)[-1])

    return {
        "service_dir": str(service_dir),
        "sibling_count": len(manifests),
        "namespace": most_common(namespaces) or "aura-services",
        "uses_active_deadline_seconds": active_deadline_count > len(manifests) / 2,
        "memory": most_common(memories) or "1000Mi",
        "cpu": most_common(cpus) or "500m",
        "secret_name": most_common(secrets) or f"{service_dir.name}-secret",
        "current_image": most_common(images),
        "slug_style": "hyphenated" if hyphenated >= len(names) / 2 else "camelCase",
    }


def slugify(job_script: str, style: str) -> str:
    # job_script like "job:feedScheduleReminders" -> "feedScheduleReminders"
    name = job_script.split(":", 1)[-1]
    if style != "hyphenated":
        return name.lower()
    # camelCase -> hyphen-case
    hyphenated = re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()
    return hyphenated


def render_manifest(
    service: str,
    job_script: str,
    schedule: str,
    schedule_comment: str,
    image_tag: str,
    conventions: dict,
    active_deadline_seconds: int | None,
) -> tuple[str, str]:
    slug = slugify(job_script, conventions.get("slug_style", "hyphenated"))
    cronjob_name = f"{service}-{slug}"
    namespace = conventions.get("namespace", "aura-services")
    memory = conventions.get("memory", "1000Mi")
    cpu = conventions.get("cpu", "500m")
    secret_name = conventions.get("secret_name", f"{service}-secret")
    image_repo = (conventions.get("current_image") or f"us-central1-docker.pkg.dev/aura-build/aura-repository/{service}").split(":")[0].split("@")[0]

    deadline_block = ""
    if active_deadline_seconds:
        deadline_block = f"      activeDeadlineSeconds: {active_deadline_seconds} # This needs to be shorter than the cron interval\n"
        pod_deadline = f"          activeDeadlineSeconds: {active_deadline_seconds + 30}\n"
    else:
        pod_deadline = ""

    manifest = f"""apiVersion: batch/v1
kind: CronJob
metadata:
  name: {cronjob_name}
  namespace: {namespace}
spec:
  schedule: "{schedule}" # {schedule_comment}
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 1
  failedJobsHistoryLimit: 1
  jobTemplate:
    spec:
{deadline_block}      parallelism: 1
      template:
        metadata:
          annotations:
            "cluster-autoscaler.kubernetes.io/safe-to-evict": "false"
        spec:
{pod_deadline}          terminationGracePeriodSeconds: 30
          containers:
          - name: {cronjob_name}
            image: {image_repo}:{image_tag}
            imagePullPolicy: IfNotPresent #Always
            resources:
              requests:
                memory: "{memory}"
                cpu: "{cpu}"
            envFrom:
              - secretRef:
                  name: {secret_name}
            command: ["npm"]
            args: ["run", "{job_script}"]
          restartPolicy: Never
"""
    return cronjob_name, manifest


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-dir", required=True, help="Path to k8s/apps/services/<service>")
    parser.add_argument("--discover-only", action="store_true")
    parser.add_argument("--job-script", help='npm script, e.g. "job:feedScheduleReminders"')
    parser.add_argument("--schedule", help='cron expression, e.g. "*/8 * * * *"')
    parser.add_argument("--schedule-comment", default="", help="human-readable comment for the schedule line")
    parser.add_argument("--image-tag", help="verified image tag to pin to (see verify_tag_has_script.py)")
    parser.add_argument("--active-deadline-seconds", type=int, default=None)
    parser.add_argument("--write", action="store_true", help="write the manifest into --service-dir")
    args = parser.parse_args()

    service_dir = Path(args.service_dir)
    if not service_dir.is_dir():
        print(json.dumps({"error": "not_a_directory", "path": str(service_dir)}))
        sys.exit(1)

    conventions = discover(service_dir)

    if args.discover_only:
        print(json.dumps(conventions, indent=2))
        return

    missing = [f for f in ("job_script", "schedule", "image_tag") if getattr(args, f) is None]
    if missing:
        print(json.dumps({"error": "missing_required_args", "missing": missing}))
        sys.exit(1)

    active_deadline = args.active_deadline_seconds
    if active_deadline is None and conventions.get("uses_active_deadline_seconds"):
        # Caller didn't specify — flag that this service's convention expects one.
        print(json.dumps({
            "warning": "service_uses_active_deadline_seconds_but_none_provided",
            "hint": "pass --active-deadline-seconds sized under the schedule interval",
        }), file=sys.stderr)

    cronjob_name, manifest = render_manifest(
        service=service_dir.name,
        job_script=args.job_script,
        schedule=args.schedule,
        schedule_comment=args.schedule_comment or "TODO: describe this schedule",
        image_tag=args.image_tag,
        conventions=conventions,
        active_deadline_seconds=active_deadline,
    )

    slug = cronjob_name[len(service_dir.name) + 1:]
    out_path = service_dir / f"{slug}cronjob.yaml"

    if args.write:
        out_path.write_text(manifest)
        print(json.dumps({"written": str(out_path), "cronjob_name": cronjob_name}, indent=2))
    else:
        print(json.dumps({"would_write": str(out_path), "cronjob_name": cronjob_name}, indent=2))
        print("---")
        print(manifest)


if __name__ == "__main__":
    main()
