#!/usr/bin/env python3
"""Bump Kubernetes image tags under a k8s manifests tree to the latest semver
tag published in Google Artifact Registry.

For every manifest (Deployment, Job, CronJob, ...) that references an image in the
target registry, this resolves the highest ``vX.Y.Z`` tag for that package and
rewrites the image tag in place — touching only the tag, never surrounding
formatting. Each package (including loaders/trainers, which have their own
packages) resolves independently.

Runs ``gcloud`` through the bundled ``gcloud_sa.py``: as the GCP_KEY service
account (which needs Artifact Registry read), or the user's login when GCP_KEY is
unset.
"""

import argparse
import concurrent.futures
import glob
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcloud_sa  # noqa: E402  (GCP_KEY service account, see docs/canonical-sources.md)

DEFAULT_REGISTRY = "us-central1-docker.pkg.dev/aura-build/aura-repository"
DEFAULT_K8S_PATH = "k8s/apps"
SEMVER_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def log(msg: str) -> None:
    print(msg, flush=True)


def parse_registry(registry: str):
    """Split a registry image prefix into (host_project_repo, location, project, repo)."""
    # e.g. us-central1-docker.pkg.dev/aura-build/aura-repository
    m = re.match(r"^([a-z0-9-]+)-docker\.pkg\.dev/([^/]+)/([^/]+)/?$", registry)
    if not m:
        raise SystemExit(
            f"Could not parse Artifact Registry location/project/repo from '{registry}'.\n"
            "Expected form: <location>-docker.pkg.dev/<project>/<repo>"
        )
    location, project, repo = m.group(1), m.group(2), m.group(3)
    return location, project, repo


def image_tag_pattern(registry: str) -> re.Pattern:
    # Matches: <registry>/<package>:vX.Y.Z  (capturing package + version)
    esc = re.escape(registry.rstrip("/"))
    return re.compile(esc + r"/([a-z0-9-]+):(v\d+\.\d+\.\d+)")


def in_scope(path: str, scope: str) -> bool:
    is_deployment = os.path.basename(path).startswith("deployment")
    if scope == "deployments":
        return is_deployment
    if scope == "jobs":
        return not is_deployment
    return True  # all


def discover(k8s_path: str, scope: str, tagpat: re.Pattern):
    """Return (files_in_scope, {package: current_versions_set}) across all yaml."""
    files = sorted(glob.glob(os.path.join(k8s_path, "**", "*.yaml"), recursive=True))
    files += sorted(glob.glob(os.path.join(k8s_path, "**", "*.yml"), recursive=True))
    scoped = []
    packages = set()
    for fp in sorted(set(files)):
        if not in_scope(fp, scope):
            continue
        with open(fp, encoding="utf-8") as f:
            txt = f.read()
        matches = tagpat.findall(txt)
        if not matches:
            continue
        scoped.append(fp)
        for pkg, _ver in matches:
            packages.add(pkg)
    return scoped, packages


def latest_tag(package: str, location: str, project: str, repo: str):
    """Query Artifact Registry and return the highest semver tag (with 'v'), or None."""
    try:
        out = gcloud_sa.run(
            [
                "artifacts", "tags", "list",
                f"--package={package}",
                f"--repository={repo}",
                f"--location={location}",
                f"--project={project}",
                "--format=value(name)",
            ],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return None, f"query failed: {e}"
    if out.returncode != 0:
        lines = [ln.strip() for ln in out.stderr.splitlines() if ln.strip()]
        # An HttpError spans many lines; its JSON "message" is the useful one.
        detail = (next((ln for ln in lines if ln.startswith('"message"')), None)
                  or (lines[-1] if lines else f"exit {out.returncode}"))[:200]
        return None, f"gcloud as {gcloud_sa.credential()}: {detail}"
    best = None
    for line in out.stdout.splitlines():
        tag = line.rsplit("/tags/", 1)[-1].strip()
        m = SEMVER_TAG.match(tag)
        if not m:
            continue
        key = tuple(int(x) for x in m.groups())
        if best is None or key > best[0]:
            best = (key, tag)
    if best is None:
        return None, "no semver tags found"
    return best[1], None


def resolve_all(packages, location, project, repo, workers: int):
    latest = {}
    errors = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(latest_tag, p, location, project, repo): p for p in sorted(packages)}
        for fut in concurrent.futures.as_completed(futs):
            pkg = futs[fut]
            tag, err = fut.result()
            if tag:
                latest[pkg] = tag
            else:
                errors[pkg] = err
    return latest, errors


def apply_updates(files, latest, tagpat, registry, dry_run: bool):
    prefix = registry.rstrip("/")
    changed_files = 0
    changes = []  # (pkg, from_versions, to, filepath)
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            txt = f.read()
        new_txt = txt
        file_touched = False
        pkgs_here = sorted(set(m.group(1) for m in tagpat.finditer(txt)))
        for pkg in pkgs_here:
            new = latest.get(pkg)
            if not new:
                continue
            cur_versions = sorted(set(
                m.group(2) for m in tagpat.finditer(new_txt) if m.group(1) == pkg
            ))
            sub = re.sub(
                re.escape(prefix) + r"/" + re.escape(pkg) + r":v\d+\.\d+\.\d+",
                prefix + "/" + pkg + ":" + new,
                new_txt,
            )
            if sub != new_txt:
                new_txt = sub
                file_touched = True
                changes.append((pkg, cur_versions, new, fp))
        if file_touched:
            changed_files += 1
            if not dry_run:
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(new_txt)
    return changed_files, changes


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="Bump k8s image tags to latest Artifact Registry semver.")
    ap.add_argument("--repo-root", default=".", help="Path to the infra repo root (default: cwd).")
    ap.add_argument("--k8s-path", default=DEFAULT_K8S_PATH,
                    help=f"Manifest tree, relative to repo root (default: {DEFAULT_K8S_PATH}).")
    ap.add_argument("--registry", default=DEFAULT_REGISTRY,
                    help=f"Artifact Registry image prefix (default: {DEFAULT_REGISTRY}).")
    ap.add_argument("--scope", choices=["deployments", "jobs", "all"], default="all",
                    help="Which manifests to update. 'deployments' = deployment*.yaml only; "
                         "'jobs' = everything else (Jobs/CronJobs); 'all' = both (default).")
    ap.add_argument("--only", default=None,
                    help="Comma-separated package allowlist (e.g. 'content-service,coach-service').")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would change without writing any files.")
    ap.add_argument("--workers", type=int, default=8, help="Parallel gcloud queries (default: 8).")
    args = ap.parse_args()

    os.chdir(args.repo_root)
    if not os.path.isdir(args.k8s_path):
        raise SystemExit(f"k8s path not found: {os.path.join(args.repo_root, args.k8s_path)}")

    location, project, repo = parse_registry(args.registry)
    tagpat = image_tag_pattern(args.registry)

    log(f"Scanning {args.k8s_path} (scope={args.scope}) against {args.registry}")
    files, packages = discover(args.k8s_path, args.scope, tagpat)
    if args.only:
        allow = set(p.strip() for p in args.only.split(",") if p.strip())
        packages = {p for p in packages if p in allow}
    if not packages:
        log("No matching images found. Nothing to do.")
        return
    log(f"Found {len(files)} in-scope manifest(s) referencing {len(packages)} package(s). "
        f"Resolving latest tags...")

    latest, errors = resolve_all(packages, location, project, repo, args.workers)
    for pkg, err in sorted(errors.items()):
        log(f"  ! {pkg}: {err} (left unchanged)")

    changed_files, changes = apply_updates(files, latest, tagpat, args.registry, args.dry_run)

    verb = "Would update" if args.dry_run else "Updated"
    if not changes:
        if errors:
            log(f"\nNo tags changed, but {len(errors)} package(s) could NOT be resolved "
                f"(see '!' lines above) and were left unchanged — this is not a clean "
                f"'all up to date' result. Resolve those before trusting the outcome.")
            raise SystemExit(1)
        log("\nEverything already at the latest tag. No changes.")
        return

    log("")
    for pkg, cur, new, fp in sorted(changes, key=lambda c: (c[3], c[0])):
        cur_s = ",".join(cur)
        if cur_s == new:
            continue
        log(f"  ~ {pkg:34s} {cur_s:14s} -> {new:14s}  {fp}")

    bumped_pkgs = sorted(set(c[0] for c in changes if ",".join(c[1]) != c[2]))
    log(f"\n{verb} {changed_files} file(s) across {len(bumped_pkgs)} package(s).")
    if errors:
        log(f"WARNING: {len(errors)} package(s) could not be resolved (see '!' lines above) "
            f"and were left unchanged.")
    if args.dry_run:
        log("(dry run — no files written; re-run without --dry-run to apply)")
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
