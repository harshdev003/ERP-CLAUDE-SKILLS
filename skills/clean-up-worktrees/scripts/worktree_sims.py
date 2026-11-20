#!/usr/bin/env python3
"""Find and delete the iOS simulators that sim-test created for git checkouts.

iOS sim-test scripts give each session its own simulator, named
"<device type> (<label>)" (sim_common.sh session_label). The label is:

  - a worktree's directory name, with every character outside [A-Za-z0-9._-]
    replaced by "-" — the simulator outlives the worktree, so it is removed
    together with it;
  - `main` for a repo's primary checkout, or `ci` on CI — these belong to no
    worktree and are cleaned up separately, on their own confirmation.

Usage:
  worktree_sims.py report   <worktree_path>...            # worktree sims, delete nothing
  worktree_sims.py delete   <worktree_path>... --confirm  # delete them
  worktree_sims.py report   --checkout                    # main/ci sims, delete nothing
  worktree_sims.py delete   --checkout --confirm          # delete them

Worktree simulators are deleted even when booted (shut down first): their
worktree is going away. A booted main/ci simulator may be in use by a session in
the primary checkout, so it is reported and skipped.

Prints a JSON object to stdout. Exits 0 with an empty result on a machine
without a working `simctl` (no Xcode, or only the Command Line Tools).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

DEVICES_DIR = os.path.expanduser("~/Library/Developer/CoreSimulator/Devices")
# Labels sim-test uses outside worktrees. A worktree with one of these names
# would match simulators that are not its own, so worktree lookups skip them.
CHECKOUT_LABELS = ("main", "ci")


def label_for(worktree_path):
    name = os.path.basename(os.path.normpath(worktree_path))
    return re.sub(r"[^A-Za-z0-9._-]", "-", name)


def run(*args, timeout=120):
    """(ok, output) — stdout on success, stderr on failure. Never raises."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    return r.returncode == 0, (r.stdout if r.returncode == 0 else r.stderr)


def simctl_json(*args):
    """Parsed `simctl list ... -j`, or {} when simctl is unavailable (e.g. only
    the Command Line Tools are installed) or its output is not JSON."""
    ok, out = run("xcrun", "simctl", "list", *args, "-j")
    if not ok:
        return {}
    try:
        return json.loads(out)
    except ValueError:
        return {}


def list_simulators():
    type_names = {t["identifier"]: t["name"]
                  for t in simctl_json("devicetypes").get("devicetypes", [])
                  if t.get("identifier") and t.get("name")}
    sims = []
    for runtime, devices in simctl_json("devices").get("devices", {}).items():
        for d in devices:
            if not d.get("udid") or not d.get("name"):
                continue
            sims.append({
                "udid": d["udid"],
                "name": d["name"],
                "type_name": type_names.get(d.get("deviceTypeIdentifier", "")),
                "state": d.get("state", ""),
                "runtime": runtime.rsplit(".", 1)[-1],
            })
    return sims


def size_bytes(udid):
    path = os.path.join(DEVICES_DIR, udid)
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_blocks * 512
            except OSError:
                pass
    return total


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} GB"


def find_matches(targets, sims):
    """targets: list of (worktree_path_or_None, label)."""
    matches = []
    for wt, label in targets:
        for s in sims:
            # A session device is exactly `<its device type's name> (<label>)`.
            # Matching the ` (<label>)` ending alone would also take Xcode's stock
            # `iPad Pro 13-inch (M4)`, whose type name carries the parentheses.
            if s["type_name"] and s["name"] == f"{s['type_name']} ({label})":
                b = size_bytes(s["udid"])
                matches.append({**s, "worktree": wt, "label": label,
                                "bytes": b, "size": human(b)})
    return matches


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("action", choices=["report", "delete"])
    p.add_argument("worktrees", nargs="*")
    p.add_argument("--checkout", action="store_true",
                   help="target the main/ci simulators instead of worktree paths")
    p.add_argument("--confirm", action="store_true",
                   help="required with delete; without it, delete only reports")
    args = p.parse_args()
    if args.checkout == bool(args.worktrees):
        p.error("pass worktree paths or --checkout, not both or neither")

    if not shutil.which("xcrun"):
        print(json.dumps({"simulators": [], "note": "xcrun not found; no simulators checked"}))
        return

    if args.checkout:
        targets = [(None, label) for label in CHECKOUT_LABELS]
    else:
        targets = [(wt, label_for(wt)) for wt in args.worktrees
                   if label_for(wt) not in CHECKOUT_LABELS]

    matches = find_matches(targets, list_simulators())
    deleted, skipped = [], []
    if args.action == "delete" and args.confirm:
        for m in matches:
            if m["state"] == "Booted":
                if args.checkout:
                    skipped.append({**m, "reason": "booted (may be in use by the primary checkout)"})
                    continue
                run("xcrun", "simctl", "shutdown", m["udid"])
            ok, err = run("xcrun", "simctl", "delete", m["udid"])
            if ok:
                deleted.append(m)
            else:
                skipped.append({**m, "reason": err.strip() or "simctl delete failed"})

    print(json.dumps({
        "simulators": matches,
        "total": human(sum(m["bytes"] for m in matches)),
        "deleted": deleted,
        "freed": human(sum(m["bytes"] for m in deleted)),
        "skipped": skipped,
    }, indent=2))


if __name__ == "__main__":
    main()
