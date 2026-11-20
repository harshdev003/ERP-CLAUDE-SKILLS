#!/usr/bin/env python3
"""Unattended worktree cleanup: remove merged worktrees and idle DerivedData-CLI. Dry run unless --apply.

Nothing removes a worktree when its PR merges, and every worktree that builds iOS keeps ~10 GB of
DerivedData-CLI. This does the two removals that are safe without a person:

  1. A worktree is removed only when ALL hold:
       - it is not a primary checkout and has a branch (not a detached HEAD);
       - the newest PR for that branch is MERGED, and the worktree's HEAD is that PR's head or an ancestor of it
         (so nothing local is lost; a squash merge leaves the commits on no remote, which is why the PR head is
         compared and "unpushed" is not);
       - worktree_data finds nothing at risk (no ignored or untracked data, no uncommitted change);
       - its HEAD last moved (commit, checkout, pull) at least --worktree-idle-days ago (git's own files: a script that
         touches every folder, as pull.sh does, cannot hide a stale worktree or mark a busy one idle);
       - no process runs in it and no booted simulator carries its label (sim-test's sanitized one, or main/ci
         for a primary checkout). Its simulators are deleted with it.
  2. A DerivedData-CLI folder directly under any checkout (primary included) is deleted when it has been
     untouched for --derived-idle-days and no process runs in the tree and no booted simulator carries its label.
     It is build output; the cost is a cold rebuild. An empty `.keep-derived-data` file in the checkout keeps it.

A gh failure for a repo skips that repo's removals: an unknown PR state is never "merged". Every action is
appended to the log. Only paths, sizes and times are printed or logged.

Usage:
  auto_clean.py --root DIR [--apply] [--log FILE]      (--root, or AURA_REPOS_DIR: the folder holding the clones)
                [--worktree-idle-days 1] [--derived-idle-days 2]
"""

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import worktree_data as wd  # noqa: E402  (same skill folder)
import worktree_sims as ws  # noqa: E402

DEFAULT_LOG = "~/.claude/clean-up-worktrees/auto.jsonl"
DERIVED = "DerivedData-CLI"
KEEP_MARKER = ".keep-derived-data"  # an empty file in a checkout: its build output is parked for a test
DAY = 86400


def run(*args, timeout=120):
    try:
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def find_repos(root):
    """Primary checkouts one or two levels under root (a `.git` directory, not a linked worktree's file)."""
    root = os.path.expanduser(root)
    out = []
    for first in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        p = os.path.join(root, first)
        if os.path.isdir(os.path.join(p, ".git")):
            out.append(p)
        elif os.path.isdir(p):
            try:
                kids = sorted(os.listdir(p))
            except OSError:
                continue
            out += [os.path.join(p, k) for k in kids if os.path.isdir(os.path.join(p, k, ".git"))]
    return out


def list_worktrees(repo):
    """Linked worktrees of a repo: [{path, branch|None, prunable}]. The first entry is the primary: skipped."""
    rc, out = run("git", "-C", repo, "worktree", "list", "--porcelain")
    if rc:
        return []
    blocks = [b for b in out.strip().split("\n\n") if b.strip()]
    res = []
    for b in blocks[1:]:
        d = {"path": None, "branch": None, "prunable": False}
        for line in b.splitlines():
            if line.startswith("worktree "):
                d["path"] = line[len("worktree "):]
            elif line.startswith("branch "):
                d["branch"] = line[len("branch "):].replace("refs/heads/", "", 1)
            elif line.startswith("prunable"):
                d["prunable"] = True
        if d["path"]:
            res.append(d)
    return res


def gh_pr_states(repo):
    """{branch: (state, headRefOid)} for the newest PR of each head branch, or None when gh fails."""
    rc, out = _gh(repo)
    if rc:
        return None
    try:
        prs = json.loads(out)
    except ValueError:
        return None
    newest = {}
    for p in prs:
        cur = newest.get(p["headRefName"])
        if cur is None or p["number"] > cur["number"]:
            newest[p["headRefName"]] = p
    return {b: (p["state"], p.get("headRefOid", "")) for b, p in newest.items()}


def _gh(repo):
    try:
        r = subprocess.run(["gh", "pr", "list", "--state", "all", "--limit", "1000",
                            "--json", "number,state,headRefName,headRefOid"],
                           cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=120)
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def busy_state():
    """(cwds, command lines, booted simulator labels), or None when any check could not answer.

    None is "unknown", and unknown is never "idle": plan() then removes nothing. A machine with no
    Xcode has no simulators, which is an answer; a failing simctl is not."""
    try:
        lsof = subprocess.run(["lsof", "-w", "-d", "cwd", "-Fn"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60).stdout
        ps = subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    cwds = {l[1:] for l in lsof.splitlines() if l.startswith("n")}
    if not cwds or ps.returncode or not ps.stdout.strip():
        return None  # a machine always has processes with a working directory
    labels = set()
    if shutil.which("xcrun"):
        try:
            r = subprocess.run(["xcrun", "simctl", "list", "devices", "booted"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode:
            return None
        labels = set(re.findall(r"\(([^()]+)\) \([0-9A-F-]{36}\) \(Booted\)", r.stdout))
    return cwds, ps.stdout.splitlines(), labels


def in_use(path, busy, primary=False):
    cwds, cmds, labels = busy
    base = path.rstrip("/")
    mine = {ws.label_for(base)}
    if primary:
        mine |= set(ws.CHECKOUT_LABELS)  # sim-test labels a primary checkout's simulator main or ci
    if mine & labels:
        return "booted simulator"
    for c in cwds:
        if c == base or c.startswith(base + "/"):
            return "a process runs in it"
    for line in cmds:
        if base + "/" in line or line.endswith(base) or (base + " ") in line:
            return "a process names its path"
    return None


def git_idle_seconds(wt, now):
    """Seconds since this worktree's branch last moved: newest of its HEAD and HEAD reflog.

    Not the index: `git status` (ours, an IDE's, a shell prompt's) rewrites it, so it would make every
    worktree look busy. Uncommitted edits are caught separately, as data at risk."""
    rc, gd = run("git", "-C", wt, "rev-parse", "--absolute-git-dir")
    if rc:
        return None
    gd = gd.strip()
    newest = 0
    for name in ("HEAD", "logs/HEAD"):
        try:
            newest = max(newest, os.stat(os.path.join(gd, name)).st_mtime)
        except OSError:
            pass
    return None if not newest else now - newest


def tree_size(path):
    rc, out = run("du", "-sk", path, timeout=600)
    try:
        return int(out.split()[0]) * 1024 if rc == 0 else 0
    except (ValueError, IndexError):
        return 0


def head_is_merged(wt, pr_oid):
    """The worktree's HEAD is the merged PR's head, or an ancestor of it: nothing local is unmerged."""
    rc, head = run("git", "-C", wt, "rev-parse", "HEAD")
    head = head.strip()
    if rc or not head or not pr_oid:
        return False
    if head == pr_oid:
        return True
    rc, _ = run("git", "-C", wt, "merge-base", "--is-ancestor", head, pr_oid)
    return rc == 0


def touched_since(path, cutoff):
    """True when anything under path (the folder, a subfolder or a file) changed after cutoff.

    Incremental compiles change nested files, not the top folders, so the top mtime alone would call a
    build from this morning idle. Stops at the first recent entry."""
    for root, dirs, files in os.walk(path):
        for name in [root] + [os.path.join(root, n) for n in dirs + files]:
            try:
                if os.lstat(name).st_mtime >= cutoff:
                    return True
            except OSError:
                continue
    return False


_UNSET = object()


def plan(root, worktree_idle_days=1, derived_idle_days=2, now=None, prs=gh_pr_states, busy=_UNSET,
         repos_fn=find_repos):
    """Decide every action. Returns {"remove": [...], "derived": [...], "skipped": [...]} without touching anything."""
    now = time.time() if now is None else now
    busy = busy_state() if busy is _UNSET else busy  # None: the check failed, so nothing is known idle
    out = {"remove": [], "derived": [], "skipped": []}

    def skip(path, why):
        out["skipped"].append({"path": path, "reason": why})

    for repo in repos_fn(root):
        states = prs(repo)
        if busy is None:  # a process or simulator check failed: nothing is known to be idle
            out["skipped"].append({"path": repo, "reason": "process check failed"})
            continue
        checkouts = [(repo, None, True)]
        for w in list_worktrees(repo):
            if w["prunable"] or not os.path.isdir(w["path"]):
                continue  # a dangling reference: `git worktree prune`, nothing on disk
            checkouts.append((w["path"], w["branch"], False))
        for path, branch, primary in checkouts:
            use = in_use(path, busy, primary)
            # --- the worktree itself
            if not primary:
                why = None
                if branch is None:
                    why = "detached HEAD"
                elif states is None:
                    why = "PR state unknown (gh failed)"
                elif states.get(branch, (None, ""))[0] != "MERGED":
                    why = f"PR {states.get(branch, ('none',))[0]}"
                elif not head_is_merged(path, states[branch][1]):
                    why = "local commits beyond the merged PR head"
                elif use:
                    why = use
                else:
                    idle = git_idle_seconds(path, now)
                    risk = wd.at_risk(path)
                    if risk.get("atRisk"):
                        why = "data at risk (" + (risk["error"] or "ignored/untracked/modified files") + ")"
                    elif idle is None or idle < worktree_idle_days * DAY:
                        why = "touched within the last %s day(s)" % worktree_idle_days
                if why:
                    skip(path, why)
                else:
                    out["remove"].append({"repo": repo, "path": path, "branch": branch,
                                          "bytes": tree_size(path)})
                    continue  # its DerivedData-CLI goes with it
            # --- DerivedData-CLI under this checkout
            d = os.path.join(path, DERIVED)
            if os.path.isdir(d) and not os.path.islink(d):
                if use:
                    skip(d, use)
                elif os.path.exists(os.path.join(path, KEEP_MARKER)):
                    skip(d, "%s marker" % KEEP_MARKER)
                elif touched_since(d, now - derived_idle_days * DAY):
                    skip(d, "touched within the last %s day(s)" % derived_idle_days)
                else:
                    out["derived"].append({"path": d, "bytes": tree_size(d)})
    return out


def delete_simulators(paths):
    """The sim-test simulators of removed worktrees (4-13 GB each), via the skill's own worktree_sims.py."""
    rc, out = run(sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "worktree_sims.py"),
                  "delete", *paths, "--confirm", timeout=600)
    try:
        d = json.loads(out)
        return {"simulatorsDeleted": len(d.get("deleted", [])), "simulatorsSkipped": len(d.get("skipped", []))}
    except ValueError:
        return {"simulatorsDeleted": 0, "simulatorError": rc != 0}


def apply(actions, log_path, sims_fn=delete_simulators):
    """Perform the plan. Returns the actions that succeeded, each with `ok` set."""
    done = []
    log = os.path.expanduser(log_path)
    os.makedirs(os.path.dirname(log) or ".", mode=0o700, exist_ok=True)
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        for a in actions["remove"]:
            rc, _ = run("git", "-C", a["repo"], "worktree", "remove", "--force", a["path"], timeout=600)
            a["ok"] = rc == 0 and not os.path.exists(a["path"])
            f.write(json.dumps({"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                "action": "remove-worktree", **a}) + "\n")
            done.append(a)
        for a in actions["derived"]:
            try:
                shutil.rmtree(a["path"])
                a["ok"] = True
            except OSError:
                a["ok"] = False
            f.write(json.dumps({"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                "action": "delete-derived-data", **a}) + "\n")
            done.append(a)
    for repo in {a["repo"] for a in actions["remove"]}:
        run("git", "-C", repo, "worktree", "prune")
    gone = [a["path"] for a in actions["remove"] if a["ok"]]
    if gone:
        sims = sims_fn(gone)
        actions["simulators"] = sims
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                "action": "delete-simulators", "worktrees": gone, **sims}) + "\n")
    return done


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.environ.get("AURA_REPOS_DIR"),
                    help="the folder holding the clones (or set AURA_REPOS_DIR)")
    ap.add_argument("--apply", action="store_true", help="remove for real (default is a dry run)")
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--worktree-idle-days", type=float, default=1)
    ap.add_argument("--derived-idle-days", type=float, default=2)
    a = ap.parse_args(argv)
    if not a.root:
        ap.error("--root is required (or set AURA_REPOS_DIR): the folder holding the clones")
    actions = plan(a.root, a.worktree_idle_days, a.derived_idle_days)
    freed = 0
    if a.apply:
        for x in apply(actions, a.log):
            freed += x["bytes"] if x.get("ok") else 0
    would = sum(x["bytes"] for x in actions["remove"] + actions["derived"])
    print(json.dumps({"mode": "apply" if a.apply else "dry-run",
                      "worktrees": [{k: x[k] for k in ("path", "branch", "bytes", "ok") if k in x}
                                    for x in actions["remove"]],
                      "derivedData": [{k: x[k] for k in ("path", "bytes", "ok") if k in x}
                                      for x in actions["derived"]],
                      "simulators": actions.get("simulators"),
                      "skipped": len(actions["skipped"]),
                      "skippedByReason": _count(actions["skipped"]),
                      "bytesFreed" if a.apply else "bytesReclaimable": freed if a.apply else would},
                     indent=2))
    return 1 if a.apply and has_failures(actions) else 0


def has_failures(actions):
    """A removal that failed, or a simulator delete that was skipped or errored (those worktrees are gone, so a
    later run will not see them again: `reclaim_disk.py --only orphan-simulators` is the retry)."""
    sims = actions.get("simulators", {})
    return (any(not x.get("ok") for x in actions["remove"] + actions["derived"])
            or bool(sims.get("simulatorsSkipped")) or bool(sims.get("simulatorError")))


def _count(skipped):
    c = {}
    for s in skipped:
        c[s["reason"]] = c.get(s["reason"], 0) + 1
    return c


if __name__ == "__main__":
    sys.exit(main())
