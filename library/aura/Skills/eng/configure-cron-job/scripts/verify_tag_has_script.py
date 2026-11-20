#!/usr/bin/env python3
"""Verify a source repo's release tag contains a required npm script before it's used
to pin a new AuraInfra CronJob image.

Checks `package.json` at the given tag via `gh api
repos/<org>/<repo>/contents/package.json?ref=<tag>`. If the script is missing, walks
forward through the repo's releases (ordered by `published_at`, oldest to newest,
starting just after the target tag) and reports the first one that has it.

This is the exact check that caught intelligence-service `v0.273.0` predating the
`job:feedScheduleReminders` / `job:feedInactivityNudge` scripts before it shipped to
prod in PROD-3406 / PROD-3461 — done by hand there, scripted here.

Exit codes:
  0  — success (script found at target tag, or a later tag was located)
  1  — gh CLI error (repo/tag unreachable, not authenticated)
  2  — script not found at the target tag AND not found in any later release either

Stdlib only (subprocess + gh CLI does the network I/O; no third-party deps).
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys


def gh_api(path: str) -> tuple[int, str]:
    result = subprocess.run(
        ["gh", "api", path],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout if result.returncode == 0 else result.stderr


def fetch_package_json(repo: str, ref: str) -> dict | None:
    """Return the parsed package.json at `ref`, or None if unreachable."""
    code, out = gh_api(f"repos/{repo}/contents/package.json?ref={ref}")
    if code != 0:
        return None
    try:
        payload = json.loads(out)
        content = base64.b64decode(payload["content"]).decode("utf-8")
        return json.loads(content)
    except (KeyError, ValueError, UnicodeDecodeError):
        return None


def has_script(package_json: dict | None, script_name: str) -> bool:
    if not package_json:
        return False
    return script_name in package_json.get("scripts", {})


def fetch_releases(repo: str) -> list[dict]:
    """All non-draft, non-prerelease releases, oldest first."""
    code, out = gh_api(f"repos/{repo}/releases?per_page=100")
    if code != 0:
        return []
    try:
        releases = json.loads(out)
    except ValueError:
        return []
    releases = [r for r in releases if not r.get("draft") and not r.get("prerelease")]
    releases.sort(key=lambda r: r.get("published_at") or "")
    return releases


def find_first_tag_with_script(repo: str, script_name: str, after_tag: str | None) -> tuple[str | None, list[str]]:
    releases = fetch_releases(repo)
    if not releases:
        return None, []

    tags_in_order = [r["tag_name"] for r in releases]
    start_index = 0
    if after_tag and after_tag in tags_in_order:
        start_index = tags_in_order.index(after_tag) + 1

    checked = []
    for tag in tags_in_order[start_index:]:
        checked.append(tag)
        pkg = fetch_package_json(repo, tag)
        if has_script(pkg, script_name):
            return tag, checked
    return None, checked


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="org/repo, e.g. AuraHealth/IntelligenceService")
    parser.add_argument("--script", required=True, help="npm script name, e.g. job:feedScheduleReminders")
    parser.add_argument("--tag", required=True, help="target/pinned tag to check first, e.g. v0.273.0")
    args = parser.parse_args()

    target_pkg = fetch_package_json(args.repo, args.tag)
    if target_pkg is None:
        print(json.dumps({
            "error": "gh_api_error",
            "message": f"Could not fetch package.json at {args.repo}@{args.tag} — check repo/tag/auth.",
        }))
        sys.exit(1)

    target_has_script = has_script(target_pkg, args.script)

    result = {
        "repo": args.repo,
        "script": args.script,
        "target_tag": args.tag,
        "target_tag_has_script": target_has_script,
        "first_tag_with_script": args.tag if target_has_script else None,
        "checked_tags": [],
    }

    if not target_has_script:
        found_tag, checked = find_first_tag_with_script(args.repo, args.script, args.tag)
        result["checked_tags"] = checked
        result["first_tag_with_script"] = found_tag

    print(json.dumps(result, indent=2))

    if not result["first_tag_with_script"]:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
