#!/usr/bin/env python3
"""Find what removing a worktree would lose before `git worktree remove --force` drops it.

`git status` doesn't show ignored files, and `--force` removes untracked and ignored files without a word.
A "clean" worktree can still hold data a run needs: a 7.4M-uid users.json and its sample, gitignored and
mode 600, were lost this way (2026-11-06). So, per worktree:

  - ignored and untracked files, except regenerable folders (node_modules, build output, caches, venvs);
  - uncommitted changes to tracked files;
  - commits on no branch and no remote (a detached HEAD): removing the worktree loses them. Commits on the
    worktree's branch but on no remote are reported too; they survive on the branch until it's deleted.

Usage:
  worktree_data.py report <worktree_path>...               # JSON: what each worktree holds at risk
  worktree_data.py log <worktree_path>... --log <file>     # append a manifest line per at-risk file

Only paths, sizes and times are printed or logged, never a file's content.
"""

import argparse
import datetime as dt
import json
import os
import subprocess
import sys

# Folders a build or install recreates. A file under one of these is never data at risk.
REGENERABLE = {
    "node_modules", "build", "dist", "out", ".next", ".nuxt", ".turbo", ".cache", ".parcel-cache",
    "coverage", ".nyc_output", "target", "DerivedData", "DerivedData-CLI", "TestResults", "Pods", ".gradle", ".expo", ".venv", "venv",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".idea", ".vscode",
}
NOISE = {".DS_Store"}
MAX_LISTED = 20


def git(wt, *args):
    r = subprocess.run(["git", "-C", wt, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, r.stdout


def regenerable(rel: str) -> bool:
    parts = rel.rstrip("/").split("/")
    return any(p in REGENERABLE for p in parts) or parts[-1] in NOISE


def files_under(wt: str, rel: str):
    """A porcelain entry can be a whole folder ("data/"): walk it, skipping regenerable folders."""
    full = os.path.join(wt, rel)
    if os.path.isfile(full) or os.path.islink(full):
        yield rel.rstrip("/")
        return
    for root, dirs, files in os.walk(full):
        dirs[:] = [d for d in dirs if d not in REGENERABLE]
        for f in files:
            if f not in NOISE:
                yield os.path.relpath(os.path.join(root, f), wt)


def at_risk(wt: str) -> dict:
    """What `git worktree remove --force` would lose from this worktree."""
    out = {"worktree": wt, "ignored": [], "untracked": [], "modified": 0, "unpushed": 0, "orphaned": 0,
           "bytes": 0, "error": None}
    if not os.path.isdir(wt):
        out["error"] = "missing"  # a dangling reference: `git worktree prune`, nothing on disk to lose
        out["atRisk"] = False
        return out
    # -z: NUL-separated, no C-quoting of special or non-ASCII names (which lstat would never find)
    rc, status = git(wt, "status", "--porcelain", "-z", "--ignored", "--untracked-files=all")
    if rc:
        out["error"] = "git status failed"
        out["atRisk"] = True  # couldn't look inside: keep it, never assume it's empty
        return out
    entries = status.split("\0")
    i = 0
    while i < len(entries):
        line = entries[i]
        i += 1
        if len(line) < 4:
            continue
        code, rel = line[:2], line[3:]
        if code[0] in "RC":
            i += 1  # a rename or copy carries its source path as the next entry
        if code == "!!" or code == "??":
            if regenerable(rel):
                continue
            for f in files_under(wt, rel):
                if regenerable(f):
                    continue
                try:
                    st = os.lstat(os.path.join(wt, f))
                except OSError:
                    continue
                entry = {"path": f, "bytes": st.st_size,
                         "modified": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc)
                         .isoformat(timespec="seconds")}
                (out["ignored"] if code == "!!" else out["untracked"]).append(entry)
                out["bytes"] += st.st_size
        elif code.strip():
            out["modified"] += 1
    # Removing a worktree keeps its branch, so commits on no remote survive on the branch (reported, for a
    # later branch cleanup). Commits reachable from no branch and no remote (a detached HEAD) are lost.
    rc, cnt = git(wt, "rev-list", "--count", "HEAD", "--not", "--remotes")
    out["unpushed"] = int(cnt.strip() or 0) if rc == 0 else 0
    rc, cnt = git(wt, "rev-list", "--count", "HEAD", "--not", "--remotes", "--branches")
    out["orphaned"] = int(cnt.strip() or 0) if rc == 0 else 0
    out["atRisk"] = bool(out["ignored"] or out["untracked"] or out["modified"] or out["orphaned"])
    return out


def summary(r: dict) -> dict:
    """The report's view: counts, total size and the largest files, never content."""
    files = sorted(r["ignored"] + r["untracked"], key=lambda e: -e["bytes"])
    return {"worktree": r["worktree"], "atRisk": r.get("atRisk", True), "error": r["error"],
            "ignoredFiles": len(r["ignored"]), "untrackedFiles": len(r["untracked"]),
            "modifiedTracked": r["modified"], "commitsOnNoBranch": r["orphaned"],
            "unpushedBranchCommits": r["unpushed"], "bytes": r["bytes"],
            "largest": files[:MAX_LISTED]}


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("report", "log"))
    ap.add_argument("worktrees", nargs="+")
    ap.add_argument("--log", help="manifest file to append to (log action)")
    a = ap.parse_args(argv)
    results = [at_risk(os.path.abspath(w)) for w in a.worktrees]
    if a.action == "report":
        print(json.dumps({"worktrees": [summary(r) for r in results],
                          "atRisk": sum(1 for r in results if r.get("atRisk"))}, indent=2))
        return 0
    if not a.log:
        ap.error("log needs --log <file>")
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    log = os.path.expanduser(a.log)
    os.makedirs(os.path.dirname(log) or ".", mode=0o700, exist_ok=True)
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    n = 0
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        for r in results:
            for kind in ("ignored", "untracked"):
                for e in r[kind]:
                    f.write(json.dumps({"loggedAt": now, "worktree": r["worktree"], "kind": kind, **e}) + "\n")
                    n += 1
            f.write(json.dumps({"loggedAt": now, "worktree": r["worktree"], "modifiedTracked": r["modified"],
                                "commitsOnNoBranch": r["orphaned"], "unpushedBranchCommits": r["unpushed"]}) + "\n")
    print(json.dumps({"logged": n, "log": a.log}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
