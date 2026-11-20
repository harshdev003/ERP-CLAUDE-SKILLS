#!/usr/bin/env python3
"""Scan and bump the pinned Node.js version across Aura backend repos.

The Node version is pinned in a small set of files per repo:
  - `.nvmrc`            (bare version, e.g. `24.15.0`) — every repo on the track, root and nested
  - `Dockerfile`        (`FROM node:<ver>-alpine`)      — repos that ship a container
  - `functions/package.json` `engines.node` (MAJOR only, e.g. `"24"`) — Firebase Functions

Repos are grouped into the five phases of the "Update nodejs to version X.Y.Z"
Linear card. Within each phase the repos are auto-discovered by globbing for a
`.nvmrc`, so newly-added services/functions are picked up without editing this
script. Web and Mobile live on their own Node tracks and are intentionally
excluded (they are not under the globbed parents).

A local checkout says nothing about whether its GitHub repo is still live, so
each discovered repo's archived state is resolved via `gh repo view --json
isArchived` and reported as `archived: true|false|null` (null = the lookup could
not answer). Archived repos are excluded from `--apply` unless
`--include-archived` is passed, and bucketed separately by `--expect` so they
don't read as rollout failures forever. An *unknown* is never treated as
archived: it is still written and still counted as drift, because a false
"archived" would silently skip a live repo.

Usage:
  # Report current versions + drift (the "plan"):
  python3 scan_node_versions.py --root "$AURA_ROOT"

  # Apply a bump to one phase (idempotent — unchanged files are reported as such):
  python3 scan_node_versions.py --root "$AURA_ROOT" --apply \\
      --version 24.16.0 --phase Services

  # Apply to every phase at once:
  python3 scan_node_versions.py --root "$AURA_ROOT" --apply \\
      --version 24.16.0 --phase all

Output is JSON on stdout so the calling skill can parse it; a human-readable
table is printed to stderr.
"""

import argparse
import concurrent.futures
import glob
import json
import os
import re
import shutil
import subprocess
import sys

# Card phase -> parent dirs to glob for repos. A repo qualifies if it has a
# `.nvmrc`. Keep the five keys aligned with the Linear card checklist.
PHASE_PARENTS = {
    "Services": ["Services"],
    "Data": ["Data"],
    "Models": ["AI"],
    "CLI": ["Tools"],
    "Functions": ["Functions"],
}
PHASE_ORDER = ["Services", "Data", "Models", "CLI", "Functions"]

DOCKER_FROM_RE = re.compile(r"^(FROM\s+node:)([^\s-]+)(-\w+.*)$", re.MULTILINE)
# CircleCI executor image, e.g. `- image: cimg/node:24.15.0`. Full version.
CIMG_NODE_RE = re.compile(r"(cimg/node:)(\d+\.\d+\.\d+)")

# `git@github.com:Owner/Repo.git`, `https://github.com/Owner/Repo`, `…/Repo.git/`
GH_REMOTE_RE = re.compile(r"github\.com[:/]+([^/]+)/(.+?)(?:\.git)?/?$")

# slug -> True (archived) / False (live) / None (lookup could not answer)
_ARCHIVED_CACHE = {}


def _run(cmd, cwd=None, timeout=30):
    """Run `cmd`, returning stdout on success and None on any failure —
    missing binary, non-zero exit, timeout. Callers treat None as 'unknown'."""
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def repo_slug(repo):
    """`owner/name` from the repo's `origin` remote, or None if there is no
    origin / it isn't a GitHub URL."""
    out = _run(["git", "-C", repo, "remote", "get-url", "origin"])
    if not out:
        return None
    m = GH_REMOTE_RE.search(out.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def github_archived(slug):
    """True / False / None for a `owner/name` slug.

    None means "the lookup could not answer" — no slug, no `gh` on PATH, `gh`
    unauthenticated, an API error, or an unparseable response. It is
    deliberately distinct from False: reporting an unknown as live keeps the
    old (wrong) behaviour visible, whereas reporting it as archived would
    silently drop a live repo from the rollout.
    """
    if not slug:
        return None
    if slug in _ARCHIVED_CACHE:
        return _ARCHIVED_CACHE[slug]
    val = None
    if shutil.which("gh"):
        out = _run(["gh", "repo", "view", slug, "--json", "isArchived"])
        if out:
            try:
                raw = json.loads(out)["isArchived"]
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                raw = None
            # Must be a real JSON bool. `bool(None)` is False, which would report
            # a shapeless answer as *live* — the exact conflation this tri-state
            # exists to avoid.
            val = raw if isinstance(raw, bool) else None
    _ARCHIVED_CACHE[slug] = val
    return val


def resolve_archived(repos, check=True):
    """Map repo path -> archived state for every repo, resolved concurrently.

    With `check=False` every repo maps to None (unknown) and no subprocess runs
    — the offline/no-`gh` escape hatch. One `gh` call per repo is ~0.5s, so a
    26-repo workspace would add ~15s to an otherwise instant scan.
    """
    repos = list(repos)
    if not check or not repos:
        return {r: None for r in repos}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        states = pool.map(lambda r: github_archived(repo_slug(r)), repos)
    return dict(zip(repos, states))


def archived_label(state):
    """Human marker for an archived state: '' when live."""
    if state is True:
        return "ARCHIVED"
    if state is None:
        return "archived=unknown"
    return ""


def discover_root():
    """Find the Aura workspace root without hardcoding one developer's layout.
    Order: $AURA_ROOT, then common checkout locations that contain a Services/
    dir. Returns the path or None (caller then errors and asks for --root)."""
    env = os.environ.get("AURA_ROOT")
    if env and os.path.isdir(os.path.join(os.path.expanduser(env), "Services")):
        return os.path.expanduser(env)
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, "Development", "Aura"),
        os.path.join(home, "Development", "AuraHealth"),
        os.path.join(home, "AuraHealth"),
        os.path.join(home, "code", "Aura"),
        os.path.join(home, "src", "Aura"),
        os.path.join(home, "aura"),
    ]
    for c in candidates:
        if os.path.isdir(os.path.join(c, "Services")):
            return c
    return None


def discover_repos(root, phase, only=None):
    """Return sorted list of repo dirs in `phase` that pin a Node version.

    `only`, if given, is a set/list of repo basenames to filter to — used for
    the Prepare canary step, which bumps a single repo (Ingestor, then Feed)
    at a time rather than a whole phase.
    """
    repos = []
    for parent in PHASE_PARENTS[phase]:
        for nvmrc in glob.glob(os.path.join(root, parent, "*", ".nvmrc")):
            repos.append(os.path.dirname(nvmrc))
    repos = sorted(set(repos))
    if only:
        only = set(only)
        repos = [r for r in repos if os.path.basename(r) in only]
    return repos


def _nvmrc_files(repo):
    """Every `.nvmrc` under `repo` — root AND nested (e.g. 4U-engine's models/*/.nvmrc).
    `**` never enters hidden dirs (.git, .claude worktrees); node_modules is skipped here."""
    return sorted(p for p in glob.glob(os.path.join(repo, "**", ".nvmrc"), recursive=True)
                  if os.sep + "node_modules" + os.sep not in p)


def read_nvmrc(repo):
    """Version pinned in the repo's `.nvmrc` file(s), root or nested. Returns a single
    version, None if there is none, or 'MIXED:<v1>,<v2>' if they differ."""
    vers = set()
    for p in _nvmrc_files(repo):
        with open(p, encoding="utf-8") as f:
            v = f.read().strip()
        if v:
            vers.add(v)
    if not vers:
        return None
    return next(iter(vers)) if len(vers) == 1 else "MIXED:" + ",".join(sorted(vers))


def _node_dockerfiles(repo):
    """Every Dockerfile under `repo` whose base image is `node:` — root AND nested
    (e.g. 4U-engine's models/*/Dockerfile). Skips vendored trees."""
    out = []
    for df in glob.glob(os.path.join(repo, "**", "Dockerfile"), recursive=True):
        if os.sep + "node_modules" + os.sep in df or os.sep + ".git" + os.sep in df:
            continue
        try:
            with open(df, encoding="utf-8") as f:
                if DOCKER_FROM_RE.search(f.read()):
                    out.append(df)
        except OSError:
            continue
    return sorted(out)


def read_dockerfile_version(repo):
    """Version pinned in the repo's node Dockerfile(s), root or nested. Returns a
    single version, None if no node Dockerfile, or 'MIXED:<v1>,<v2>' if they differ."""
    vers = set()
    for df in _node_dockerfiles(repo):
        with open(df, encoding="utf-8") as f:
            m = DOCKER_FROM_RE.search(f.read())
        if m:
            vers.add(m.group(2))
    if not vers:
        return None
    return next(iter(vers)) if len(vers) == 1 else "MIXED:" + ",".join(sorted(vers))


def _circleci_path(repo):
    return os.path.join(repo, ".circleci", "config.yml")


def read_circleci_version(repo):
    """Return the (single, deduped) cimg/node version in .circleci/config.yml.

    Returns None if no config / no cimg/node pin. If the config pins more than
    one distinct version, returns the string 'MIXED:<v1>,<v2>' so the scan
    surfaces the inconsistency instead of hiding it.
    """
    p = _circleci_path(repo)
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        vers = {m.group(2) for m in CIMG_NODE_RE.finditer(f.read())}
    if not vers:
        return None
    if len(vers) == 1:
        return next(iter(vers))
    return "MIXED:" + ",".join(sorted(vers))


def write_circleci(repo, version):
    """Rewrite every cimg/node:<ver> in .circleci/config.yml to `version`."""
    p = _circleci_path(repo)
    if not os.path.isfile(p):
        return False
    with open(p, encoding="utf-8") as f:
        text = f.read()
    new = CIMG_NODE_RE.sub(lambda m: f"{m.group(1)}{version}", text)
    if new == text:
        return False
    with open(p, "w", encoding="utf-8") as f:
        f.write(new)
    return True


def read_functions_engine(repo):
    """Return (path, major) for functions/package.json engines.node, or (None, None)."""
    p = os.path.join(repo, "functions", "package.json")
    if not os.path.isfile(p):
        return None, None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return p, None
    node = str(data.get("engines", {}).get("node", "")).strip()
    return p, (node or None)


def inspect_repo(repo, archived=None):
    fn_path, fn_engine = read_functions_engine(repo)
    return {
        "repo": os.path.basename(repo),
        "path": repo,
        "nvmrc": read_nvmrc(repo),
        "dockerfile": read_dockerfile_version(repo),
        "circleci": read_circleci_version(repo),
        "functions_engine": fn_engine,
        "functions_pkg": fn_path,
        # True / False / None(unknown) — see github_archived()
        "archived": archived,
    }


def write_nvmrc(repo, version):
    """Rewrite every `.nvmrc` under `repo` (root AND nested). Returns True if any changed."""
    changed = False
    for p in _nvmrc_files(repo):
        with open(p, encoding="utf-8") as f:
            if f.read().strip() == version:
                continue
        with open(p, "w", encoding="utf-8") as f:
            f.write(version + "\n")
        changed = True
    return changed


def write_dockerfile(repo, version):
    """Rewrite `FROM node:<ver>-<variant>` in every node Dockerfile under `repo`
    (root AND nested, e.g. models/*/Dockerfile). Returns True if any file changed."""
    def repl(m):
        return f"{m.group(1)}{version}{m.group(3)}"

    changed = False
    for p in _node_dockerfiles(repo):
        with open(p, encoding="utf-8") as f:
            text = f.read()
        new = DOCKER_FROM_RE.sub(repl, text)
        if new != text:
            with open(p, "w", encoding="utf-8") as f:
                f.write(new)
            changed = True
    return changed


def write_functions_engine(repo, major):
    """Bump engines.node MAJOR in functions/package.json (only if it differs)."""
    p = os.path.join(repo, "functions", "package.json")
    if not os.path.isfile(p):
        return False
    with open(p, encoding="utf-8") as f:
        text = f.read()
    # Preserve formatting: regex-replace the node engine string rather than
    # round-tripping through json (which would reflow the whole file).
    pat = re.compile(r'("engines"\s*:\s*\{[^}]*?"node"\s*:\s*")([^"]+)(")', re.DOTALL)
    m = pat.search(text)
    if not m or m.group(2) == major:
        return False
    new = text[: m.start(2)] + major + text[m.end(2) :]
    with open(p, "w", encoding="utf-8") as f:
        f.write(new)
    return True


def scan(root, phases, check_archived=True):
    all_repos = []
    by_phase = {}
    for phase in phases:
        by_phase[phase] = discover_repos(root, phase)
        all_repos.extend(by_phase[phase])
    states = resolve_archived(all_repos, check=check_archived)
    return {p: [inspect_repo(r, states.get(r)) for r in repos] for p, repos in by_phase.items()}


def _off_pins(row, target):
    """Pins on `row` that are not on `target`. engines.node is major-only ('24'),
    so it is compared against the target's major — Functions deploys honour
    engines.node, so a stale major means the runtime is behind even at zero pin drift."""
    off = [v for v in (row["nvmrc"], row["dockerfile"], row["circleci"]) if v and v != target]
    eng = row.get("functions_engine")
    if eng and eng != target.split(".")[0]:
        off.append(f"engines.node={eng}")
    return off


def find_drift(report):
    """The dominant version across all .nvmrc values; repos not on it are drift.

    Archived repos are excluded from both the dominant-version vote and the
    drift list, and returned in their own bucket: they are frozen on whatever
    they were last released with and will never be bumped, so counting them as
    drift makes step 1 keep proposing a bump that cannot be pushed.
    """
    counts = {}
    for rows in report.values():
        for r in rows:
            # A MIXED read is drift, never a vote.
            if r["nvmrc"] and not r["nvmrc"].startswith("MIXED:") and r.get("archived") is not True:
                counts[r["nvmrc"]] = counts.get(r["nvmrc"], 0) + 1
    if not counts:
        return None, [], []
    dominant = max(counts, key=counts.get)
    drift, archived_drift = [], []
    for phase, rows in report.items():
        for r in rows:
            if not _off_pins(r, dominant):
                continue
            eng = r.get("functions_engine")
            entry = {
                "phase": phase,
                "repo": r["repo"],
                "pins": [r["nvmrc"], r["dockerfile"], r["circleci"]]
                + ([f"engines.node={eng}"] if eng else []),
                "expected": dominant,
            }
            if r.get("archived") is True:
                archived_drift.append(entry)
            else:
                drift.append(entry)
    return dominant, drift, archived_drift


def find_off_target(report, expect):
    """Split every repo not on `expect` into (off_target, archived_off_target).

    The finish/zero-drift check: "no internal drift" only proves the pins agree
    with each other — a workspace uniformly on the OLD release passes that.
    `--expect` asserts every pin is actually on the target.

    An archived repo can never reach the target, so it gets its own bucket
    rather than reading as a rollout failure on every future run. An *unknown*
    stays in `off_target` — it may be real drift.
    """
    off_target, archived_off_target = [], []
    for phase, rows in report.items():
        for r in rows:
            bad = _off_pins(r, expect)
            if not bad:
                continue
            entry = {"phase": phase, "repo": r["repo"], "off": bad}
            (archived_off_target if r.get("archived") is True else off_target).append(entry)
    return off_target, archived_off_target


def print_table(report, dominant, drift, archived_drift=()):
    w = sys.stderr
    print(f"\nCurrent dominant .nvmrc version: {dominant or '(none found)'}\n", file=w)
    archived, unknown = [], []
    for phase in PHASE_ORDER:
        if phase not in report:
            continue
        print(f"== {phase} ==", file=w)
        for r in report[phase]:
            extra = ""
            if r["functions_engine"] is not None:
                extra = f"  engines.node={r['functions_engine']}"
            mark = archived_label(r.get("archived"))
            if mark:
                extra += f"  {mark}"
                (archived if r.get("archived") is True else unknown).append(
                    f"{phase}/{r['repo']}")
            dv = r["dockerfile"] or "-"
            ci = r["circleci"] or "-"
            print(
                f"  {r['repo']:<24} nvmrc={r['nvmrc'] or '-':<10} docker={dv:<10} "
                f"circleci={ci:<10}{extra}",
                file=w,
            )
        print("", file=w)
    if archived:
        print("ARCHIVED on GitHub — excluded from --apply (pass --include-archived to "
              "override), and NOT counted as drift:", file=w)
        for a in archived:
            print(f"  - {a}", file=w)
        print("  A push to an archived repo fails with HTTP 403. That is the archive, "
              "not a credential problem.\n", file=w)
    if unknown:
        print("ARCHIVED STATE UNKNOWN (no gh / not authenticated / no GitHub origin) — "
              "treated as LIVE, so these are still bumped and still counted as drift:", file=w)
        for u in unknown:
            print(f"  ? {u}", file=w)
        print("", file=w)
    if drift:
        print("DRIFT (pins not matching dominant version):", file=w)
        for d in drift:
            print(f"  ! {d['phase']}/{d['repo']}: {d['pins']} (expected {d['expected']})", file=w)
        print("", file=w)
    if archived_drift:
        print("Drift in ARCHIVED repos (informational — frozen, do not bump):", file=w)
        for d in archived_drift:
            print(f"  - {d['phase']}/{d['repo']}: {d['pins']} (expected {d['expected']})", file=w)
        print("", file=w)


def apply_phase(root, phase, version, only=None, include_archived=False,
                check_archived=True):
    major = version.split(".")[0]
    repos = discover_repos(root, phase, only=only)
    states = resolve_archived(repos, check=check_archived)
    changes = []
    for repo in repos:
        name = os.path.basename(repo)
        state = states.get(repo)
        # Archived is the only state that blocks a write. An unknown is written:
        # a false "archived" would silently skip a live repo, which is worse than
        # the 403 this exclusion exists to prevent.
        if state is True and not include_archived:
            changes.append({"repo": name, "path": repo, "changed": [],
                            "archived": True, "skipped": "archived"})
            continue
        touched = []
        if write_nvmrc(repo, version):
            touched.append(".nvmrc")
        if write_dockerfile(repo, version):
            touched.append("Dockerfile")
        if write_circleci(repo, version):
            touched.append(".circleci/config.yml")
        if phase == "Functions" and write_functions_engine(repo, major):
            touched.append("functions/package.json(engines.node)")
        changes.append({"repo": name, "path": repo, "changed": touched,
                        "archived": state})
    return changes


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None,
                    help="Aura workspace root (auto-discovered via $AURA_ROOT or common "
                    "checkout locations if omitted)")
    ap.add_argument("--apply", action="store_true", help="apply a version bump")
    ap.add_argument("--version", help="target full version, e.g. 24.16.0 (required with --apply)")
    ap.add_argument("--expect", help="scan mode: fail if any pin is not on this exact version "
                    "(the finish/zero-drift check — internal consistency alone is not enough)")
    ap.add_argument("--phase", default="all", help="phase name or 'all'")
    ap.add_argument(
        "--repo",
        help="comma-separated repo basenames to limit --apply to (e.g. 'Ingestor,FeedService'); "
        "used for the Prepare canary step",
    )
    ap.add_argument("--include-archived", action="store_true",
                    help="also bump repos that are archived on GitHub (default: skip them — "
                         "the push would 403)")
    ap.add_argument("--no-archived-check", action="store_true",
                    help="skip the `gh repo view` archived lookups entirely; every repo is "
                         "reported as archived=unknown and treated as live")
    args = ap.parse_args()
    check_archived = not args.no_archived_check

    root = os.path.expanduser(args.root) if args.root else discover_root()
    if not root or not os.path.isdir(os.path.join(root, "Services")):
        print(json.dumps({"error": "Aura workspace root not found. Pass --root <path> "
                          "or set $AURA_ROOT (a dir containing Services/).",
                          "tried": root}))
        sys.exit(1)
    root = os.path.abspath(root)

    if args.phase == "all":
        phases = PHASE_ORDER
    elif args.phase in PHASE_PARENTS:
        phases = [args.phase]
    else:
        print(json.dumps({"error": f"unknown phase: {args.phase}"}))
        sys.exit(1)

    if args.apply:
        if not args.version:
            print(json.dumps({"error": "--version is required with --apply"}))
            sys.exit(1)
        if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
            print(json.dumps({"error": f"version must be MAJOR.MINOR.PATCH, got {args.version}"}))
            sys.exit(1)
        only = [r.strip() for r in args.repo.split(",")] if args.repo else None
        # With --repo, scan every phase for the named repos (a canary repo like
        # Ingestor lives in Data, FeedService in Services).
        scan_phases = PHASE_ORDER if only else phases
        if only:
            # Guard: a typo like --repo Feed (vs FeedService) must NOT look like a
            # successful no-op — a Prepare canary depends on the named repo existing.
            discovered = set()
            for p in PHASE_ORDER:
                discovered.update(os.path.basename(r) for r in discover_repos(root, p))
            missing = [n for n in only if n not in discovered]
            if missing:
                print(json.dumps({"error": f"--repo names matched no checkout: {missing}",
                                  "known": sorted(discovered)}))
                sys.exit(1)
        result = {"applied": args.version, "repo_filter": only,
                  "include_archived": args.include_archived,
                  "archived_checked": check_archived, "phases": {}, "skipped_archived": []}
        for phase in scan_phases:
            changes = apply_phase(root, phase, args.version, only=only,
                                  include_archived=args.include_archived,
                                  check_archived=check_archived)
            if changes:
                result["phases"][phase] = changes
        # human summary
        for phase, changes in result["phases"].items():
            for c in changes:
                if c.get("skipped") == "archived":
                    result["skipped_archived"].append(f"{phase}/{c['repo']}")
                    print(f"  {phase}/{c['repo']}: SKIPPED — archived on GitHub "
                          f"(--include-archived to override)", file=sys.stderr)
                elif c["changed"]:
                    note = " [archived]" if c.get("archived") is True else ""
                    print(f"  {phase}/{c['repo']}: updated {', '.join(c['changed'])}{note}",
                          file=sys.stderr)
                else:
                    print(f"  {phase}/{c['repo']}: already at {args.version}", file=sys.stderr)
        if result["skipped_archived"]:
            print("\nArchived repos were not touched. A push to one fails with HTTP 403 — "
                  "that is the archive, not a credential problem.", file=sys.stderr)
        print(json.dumps(result, indent=2))
        return

    report = scan(root, phases, check_archived=check_archived)
    dominant, drift, archived_drift = find_drift(report)
    print_table(report, dominant, drift, archived_drift)

    off_target, archived_off_target = [], []
    if args.expect:
        off_target, archived_off_target = find_off_target(report, args.expect)
        if off_target:
            print(f"\nNOT on target {args.expect}:", file=sys.stderr)
            for o in off_target:
                print(f"  ! {o['phase']}/{o['repo']}: {o['off']}", file=sys.stderr)
        if archived_off_target:
            print(f"\nArchived, so permanently off target {args.expect} "
                  f"(expected — not a rollout failure):", file=sys.stderr)
            for o in archived_off_target:
                print(f"  - {o['phase']}/{o['repo']}: {o['off']}", file=sys.stderr)

    print(json.dumps({"root": root, "dominant": dominant, "expect": args.expect,
                      "archived_checked": check_archived,
                      "report": report, "drift": drift, "archived_drift": archived_drift,
                      "off_target": off_target,
                      "archived_off_target": archived_off_target}, indent=2))
    if args.expect and off_target:
        sys.exit(2)


if __name__ == "__main__":
    main()
