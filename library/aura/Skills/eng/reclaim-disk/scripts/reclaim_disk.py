#!/usr/bin/env python3
"""Report and reclaim the disk an iOS build/test loop leaves behind.

Why this exists: on 2026-09-29 a Mac running the Aurie sim-test harness reached
235 MB free on a 926 GB volume. That is not merely slow — the Claude Code Bash
tool writes its output file before it executes, so at that point the agent could
not run *any* command, including the one that would have cleaned up. A manual
sweep recovered 233 GB.

Almost all of it was in places no script touched and most people do not know
exist. `~/Library/Developer/XCTestDevices` alone held 96 simulator clones and
124 GB: `xcodebuild test` clones a simulator per run and never deletes it.

A second sweep, on 2026-10-02, found ~190 GB on a Mac this tool called clean:
simulator runtimes nobody used (112 GB, plus 55 GB of dyld cache that goes with
them), simulators whose worktree was gone, screen recordings inside shut-down
simulators, and ordinary dev caches. A report that says "nothing" while the disk
is two-thirds build tooling is the failure, so every one of those is now listed.

Two rules shape the whole tool:

  * **Report before deleting.** `report` is the default verb and writes nothing.
    `reclaim` needs `--confirm`.
  * **Never remove something in use.** Every source has a liveness test that is
    evidence, not a guess — a device's own state from CoreSimulator, a workspace
    path that does exist, an `xcodebuild` whose argv names the directory.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

# The report prints em-dashes and middots. Under a non-UTF-8 locale — cron, CI, a
# bare ssh — encoding those raises and takes the whole run with it, which is a poor
# way for a disk-cleanup tool to fail.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - already fine, or not a tty
        pass

HOME = os.path.expanduser("~")
XCTEST_SET = os.path.join(HOME, "Library/Developer/XCTestDevices")
DERIVED_DATA = os.path.join(HOME, "Library/Developer/Xcode/DerivedData")
DEVICE_SUPPORT = os.path.join(HOME, "Library/Developer/Xcode/iOS DeviceSupport")
SIM_DEVICES = os.path.join(HOME, "Library/Developer/CoreSimulator/Devices")

# A runtime last used longer ago than this, with no simulator on it, is offered.
RUNTIME_DAYS = 30

# Below this a dev cache is not worth a line in the report.
DEV_CACHE_MIN_KB = 100 * 1024

PLATFORMS = {"iphonesimulator": "iOS", "watchsimulator": "watchOS",
             "xrsimulator": "visionOS", "appletvsimulator": "tvOS"}

# A target touched this recently is assumed to belong to a run in progress. Several
# sessions build on one Mac at once, so "nothing of mine is running" is not enough
# to go on — and the cost of waiting is one more sweep later, against deleting the
# output of someone else's 20-minute build.
FRESH_MINUTES = 60


# ---------------------------------------------------------------- helpers

def run_checked(*args: str, timeout: int = 120) -> tuple[bool, str]:
    """`(ok, output)` — output is stdout on success, stderr on failure.

    Deleting needs to know. `run()` below reports failure as an empty string, which
    is indistinguishable from a command that succeeded and printed nothing, so a
    `simctl delete` that failed — a wedged CoreSimulator on a nearly full disk is
    exactly when it would — was counted as reclaimed and the sweep exited 0.
    """
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    return (out.returncode == 0), (out.stdout if out.returncode == 0 else out.stderr)


def run(*args: str, timeout: int = 120) -> str:
    """Output, or empty on any failure. For reads, where empty is a fine answer."""
    ok, out = run_checked(*args, timeout=timeout)
    return out if ok else ""


def du_kb(path: str) -> int:
    """Size on disk in KB. `du` rather than a Python walk: it counts blocks, which
    is what the volume actually frees, and it is one process instead of 10^5 stats."""
    out = run("du", "-sk", path, timeout=600)
    try:
        return int(out.split()[0])
    except (IndexError, ValueError):
        return 0


def human(kb: int) -> str:
    v = float(kb)
    for unit in ("KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return f"{v:,.1f} {unit}" if unit != "KB" else f"{v:,.0f} KB"
        v /= 1024
    return f"{v:.1f} TB"


def free_kb(path: str = "/") -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize // 1024


def fresh(path: str, minutes: int = FRESH_MINUTES) -> bool:
    """Modified within the window — treat as a run in progress."""
    try:
        return (time.time() - os.path.getmtime(path)) < minutes * 60
    except OSError:
        return False


def days_since(stamp: str | None, now: float | None = None) -> float | None:
    """Days since a CoreSimulator `lastUsedAt` (`2026-10-02T03:31:34Z`), or None
    when it is absent — which for a runtime means it has never been used."""
    if not stamp:
        return None
    try:
        then = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return ((time.time() if now is None else now) - then.timestamp()) / 86400


def default_devices() -> dict:
    """`simctl list devices -j` for the default device set, keyed by runtime id."""
    try:
        return json.loads(run("xcrun", "simctl", "list", "devices", "-j")).get("devices", {})
    except ValueError:
        return {}


def device_type_names() -> dict[str, str]:
    """Device type identifier → its display name, from `simctl list devicetypes -j`."""
    try:
        types = json.loads(run("xcrun", "simctl", "list", "devicetypes", "-j")).get("devicetypes", [])
    except ValueError:
        return {}
    return {t["identifier"]: t["name"] for t in types if t.get("identifier") and t.get("name")}


def version_key(version: str) -> tuple:
    return tuple(int(p) for p in re.findall(r"\d+", version or ""))


def session_label(name: str) -> str:
    """The sim-test harness's label, sanitised the way `sim_common.sh` does it."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", name)


def live_build_roots() -> list[str]:
    """The working directory of every `xcodebuild` running on this machine.

    Several sessions share one Mac, and the paths they pass are *relative*:
    `-derivedDataPath DerivedData-CLI` resolves against the process's own cwd, not
    against anything visible in the argument. So the sound question is not "does
    this path appear in some argv" — matching on the basename would call every
    worktree's `DerivedData-CLI` live the moment any one of them built — but "is
    this path inside a tree something is currently building in".

    Found by running this while another session was mid-build in a sibling
    worktree, which the basename test could neither confirm nor place.
    """
    roots = []
    for line in run("ps", "-axo", "pid=,args=").splitlines():
        if "xcodebuild" not in line:
            continue
        pid = line.strip().split(None, 1)[0]
        for fd in run("lsof", "-p", pid, timeout=20).splitlines():
            parts = fd.split()
            if len(parts) > 8 and parts[3] == "cwd":
                roots.append(os.path.realpath(parts[-1]))
                break
    return sorted(set(roots))


def owning_tree(path: str) -> str:
    """The git worktree a path belongs to, by git's own reckoning.

    Plain containment is wrong here and quietly over-reaches: linked worktrees live
    at `<repo>/.claude/worktrees/<name>`, *inside* the main checkout, so "is this
    under the build's directory" makes one build in the main repo claim every
    worktree on the machine. Asking git instead costs one call and cannot make that
    mistake — it is the same boundary `git worktree` itself draws.
    """
    probe = path if os.path.isdir(path) else os.path.dirname(path)
    top = run("git", "-C", probe, "rev-parse", "--show-toplevel", timeout=20).strip()
    return os.path.realpath(top) if top else ""


def in_use_by_xcodebuild(path: str, roots: list[str] | None = None) -> bool:
    """Whether a live `xcodebuild` is working in the same worktree as this path."""
    real = os.path.realpath(path)
    mine = owning_tree(real)
    for root in (live_build_roots() if roots is None else roots):
        if mine and owning_tree(root) == mine:
            return True
        if not mine and (real == root or real.startswith(root + os.sep)):
            return True
    return False


class Candidate:
    """One reclaimable thing, and why it is or is not taken.

    `safe` and `blocked` are deliberately separate, because they answer different
    questions and only one of them is a preference:

      * `safe=False` is a **policy** hold — this needs a human to decide. `--only`
        is how that human says yes.
      * `blocked=True` is **evidence** that something is using it right now: a live
        `xcodebuild` in the same worktree, a booting simulator, a directory written
        minutes ago. No flag opts in, because there is nothing to opt in to — the
        answer is "wait", not "are you sure".

    Collapsing them makes `--only result-bundles --confirm` delete the output of a
    build in progress, which is the one thing this tool must never do.
    """

    def __init__(self, source: str, path: str, kb: int, why: str,
                 remove: str = "rmtree", safe: bool = True, note: str = "",
                 blocked: bool = False, requires: str = ""):
        self.source, self.path, self.kb = source, path, kb
        self.why, self.remove, self.note = why, remove, note
        self.blocked = blocked
        self.requires = requires
        self.safe = safe and not blocked

    @property
    def flag(self) -> str:
        """`x` in use, `-` reported only, `!` policy hold, blank takeable."""
        if self.blocked:
            return "x"
        if self.remove == "manual":
            return "-"
        return " " if self.safe else "!"

    def takeable(self, only: set[str], flags: set[str] = frozenset()) -> bool:
        if self.blocked or self.remove == "manual":
            return False
        # A second, narrower consent than `--only`: this one item carries a cost the
        # rest of its source does not, so saying yes to the source is not saying yes
        # to it.
        if self.requires and self.requires not in flags:
            return False
        return self.safe or self.source in only

    def as_dict(self) -> dict:
        return {"source": self.source, "path": self.path, "kb": self.kb,
                "why": self.why, "safe": self.safe, "blocked": self.blocked,
                "requires": self.requires, "note": self.note}


# ------------------------------------------------- machine-wide sources

def scan_xctest_clones() -> list[Candidate]:
    """Simulator clones `xcodebuild test` made and never deleted.

    The liveness test is CoreSimulator's own: the clones live in their own device
    *set*, so `simctl --set <path> list devices` reports each one's state. `Booted`
    is in use; `Shutdown` is not. That beats an `lsof` sweep on the directory —
    it is authoritative, it is instant, and it works on a set whose directories
    are already gone.

    **Removal is `simctl delete`, never `rm -rf`.** The set's `device_set.plist`
    registers every clone, so deleting the directories by hand reclaims the bytes
    and leaves the registry naming devices that no longer exist. That is not
    hypothetical: this machine was left with 27 such ghosts by exactly that sweep,
    which is what prompted the rule.
    """
    if not os.path.isdir(XCTEST_SET):
        return []
    out = run("xcrun", "simctl", "--set", XCTEST_SET, "list", "devices", "-j")
    try:
        devices = json.loads(out).get("devices", {})
    except ValueError:
        return []
    found = []
    for runtime, devs in devices.items():
        for d in devs:
            udid, state = d.get("udid"), d.get("state")
            if not udid:
                continue
            # Only a settled `Shutdown` is idle. CoreSimulator also reports
            # `Booting` and `Shutting Down`, and a clone in either is a test either
            # starting or finishing — taking it mid-transition is the same mistake
            # as taking a booted one, with worse timing.
            if state != "Shutdown":
                continue
            path = os.path.join(XCTEST_SET, udid)
            kb = du_kb(path) if os.path.isdir(path) else 0
            note = "" if kb else "registered but no directory — a stale entry to deregister"
            found.append(Candidate("xctest-clones", path, kb,
                                   f"{d.get('name') or 'clone'} · {state}",
                                   remove=f"simctl:{udid}", note=note))
    return found


def scan_derived_data() -> list[Candidate]:
    """Global DerivedData whose workspace no longer exists.

    Path-existence is the whole guard, and it is the right one: it cannot delete a
    live checkout's cache, including one belonging to another session or another
    repo entirely. A directory with no readable `info.plist` is left alone rather
    than guessed at — `ModuleCache.noindex` and friends live here too.
    """
    return [Candidate("derived-data", path, du_kb(path), f"workspace gone: {workspace}")
            for path, workspace in derived_data_entries() if workspace and not os.path.exists(workspace)]


def derived_data_entries() -> list[tuple[str, str]]:
    """`(path, WorkspacePath)` for every DerivedData entry; workspace is "" when the
    `info.plist` is missing or unreadable."""
    if not os.path.isdir(DERIVED_DATA):
        return []
    entries = []
    for name in sorted(os.listdir(DERIVED_DATA)):
        path = os.path.join(DERIVED_DATA, name)
        if not os.path.isdir(path):
            continue
        workspace = ""
        try:
            with open(os.path.join(path, "info.plist"), "rb") as fh:
                workspace = plistlib.load(fh).get("WorkspacePath") or ""
        except (OSError, ValueError, plistlib.InvalidFileException):
            pass
        entries.append((path, workspace))
    return entries


def scan_device_support() -> list[Candidate]:
    """Symbol caches for physical devices. Regenerated on demand the next time one
    is attached — which is why this is offered rather than taken by default: the
    regeneration is slow and happens when you are trying to debug something."""
    if not os.path.isdir(DEVICE_SUPPORT):
        return []
    found = []
    for name in sorted(os.listdir(DEVICE_SUPPORT)):
        path = os.path.join(DEVICE_SUPPORT, name)
        if os.path.isdir(path):
            found.append(Candidate("device-support", path, du_kb(path),
                                   "regenerated when the device is next attached",
                                   safe=False))
    return found


def scan_simulators() -> list[Candidate]:
    """Simulator devices whose runtime is gone. `simctl delete unavailable` is
    Apple's own verb for these and cannot touch a usable device.

    Named per-session devices on a usable runtime are `orphan-simulators`' question,
    not this one's.
    """
    found = []
    for runtime, devs in default_devices().items():
        if "unavailable" not in runtime.lower():
            continue
        for d in devs:
            udid = d.get("udid")
            if not udid or d.get("state") == "Booted":
                continue
            path = os.path.join(HOME, "Library/Developer/CoreSimulator/Devices", udid)
            found.append(Candidate("simulators", path, du_kb(path) if os.path.isdir(path) else 0,
                                   f"{d.get('name') or udid} · runtime unavailable",
                                   remove=f"simctl-default:{udid}", safe=False))
    return found


def scan_sim_runtimes(days: int = RUNTIME_DAYS) -> list[Candidate]:
    """Simulator runtimes (iOS, watchOS, visionOS disk images) nothing is using.

    Each is 4-9 GB, Xcode installs a new one per release and removes none, and each
    also has a dyld cache under `/Library/Developer/CoreSimulator/Caches/dyld` that
    CoreSimulator drops with it. On the 2026-10-02 Mac, 20 runtimes held 127 GB and
    2 were in use.

    CoreSimulator records `lastUsedAt` per runtime, so the test is evidence:

      * a runtime any simulator is created on is kept — deleting it strands the device;
      * a runtime used within `days` is kept;
      * the newest runtime of a platform that has ever been used is kept, because it
        is what Xcode builds against by default. A platform never used at all (a
        visionOS nobody targets) keeps nothing.

    A policy hold: a deleted runtime is a multi-GB re-download from Xcode → Settings
    → Components. Removal is `simctl runtime delete`, which finishes in the
    background, so free space catches up a minute or two after the sweep reports.
    """
    try:
        runtimes = json.loads(run("xcrun", "simctl", "runtime", "list", "-j"))
    except ValueError:
        return []
    if not isinstance(runtimes, dict):
        return []
    with_devices = {rid for rid, devs in default_devices().items() if devs}
    used_platforms, newest = set(), {}
    for r in runtimes.values():
        plat = r.get("platformIdentifier", "")
        if r.get("lastUsedAt"):
            used_platforms.add(plat)
        if plat not in newest or version_key(r.get("version")) > version_key(newest[plat].get("version")):
            newest[plat] = r
    found = []
    for rid, r in runtimes.items():
        if r.get("state") != "Ready" or r.get("deletable") is False:
            continue
        plat = r.get("platformIdentifier", "")
        if r.get("runtimeIdentifier") in with_devices:
            continue
        if plat in used_platforms and newest.get(plat) is r:
            continue
        age = days_since(r.get("lastUsedAt"))
        if age is not None and age < days:
            continue
        name = PLATFORMS.get(plat.rsplit(".", 1)[-1], plat)
        used = "never used" if age is None else f"last used {age:.0f}d ago"
        found.append(Candidate("sim-runtimes", r.get("runtimeBundlePath") or rid,
                               int(r.get("sizeBytes") or 0) // 1024,
                               f"{name} {r.get('version')} ({r.get('build')}) · {used} · no simulator on it",
                               remove=f"runtime:{r.get('identifier') or rid}", safe=False,
                               note="its dyld cache goes with it"))
    return found


def scan_sim_attachments() -> list[Candidate]:
    """Screen recordings and test attachments kept inside a simulator's data.

    They sit under `data/Containers/Data/InternalDaemon/*/Attachments` — QuickTime
    movies of 1 GB and up, plus half-written `.sb-*` temporaries from a recording
    that never finished. Emptying the folder leaves the simulator and its installed
    app alone, unlike `simctl erase`. A device that is not shut down may be
    recording into it, so it is held as in use.
    """
    found = []
    for devs in default_devices().values():
        for d in devs:
            udid = d.get("udid")
            if not udid:
                continue
            data = d.get("dataPath") or os.path.join(SIM_DEVICES, udid, "data")
            for att in sorted(glob.glob(os.path.join(data, "Containers/Data/InternalDaemon/*/Attachments"))):
                try:
                    if not os.listdir(att):
                        continue
                except OSError:
                    continue
                kb = du_kb(att)
                if kb < 10 * 1024:
                    continue
                idle = d.get("state") == "Shutdown"
                found.append(Candidate("sim-attachments", att, kb,
                                       f"{d.get('name') or udid} · recordings and test attachments",
                                       remove="empty", safe=False, blocked=not idle,
                                       note="" if idle else f"device is {d.get('state')} — shut it down first"))
    return found


def nvmrc_pins(root: str = HOME, depth: int = 4) -> set[str]:
    """Node versions pinned by any `.nvmrc` within `depth` directories of `root`."""
    pins = set()
    skip = {"Library", "node_modules", "Pods", "DerivedData", "build", "Applications",
            "Movies", "Music", "Pictures"}
    base = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath.count(os.sep) - base >= depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in skip]
        if ".nvmrc" in filenames:
            try:
                with open(os.path.join(dirpath, ".nvmrc")) as fh:
                    pins.add(fh.read().strip().lstrip("v"))
            except OSError:
                pass
    return pins


def scan_dev_caches() -> list[Candidate]:
    """Where the rest of a developer Mac's disk goes. Reported, never removed.

    Package-manager caches, unused Node versions, app updater leftovers, Docker's VM
    disk and live DerivedData are not build junk with a liveness test — each is
    somebody's tool, cleared with that tool's own command. Listing them with the
    command is what turns "nothing reclaimable" into an answer.
    """
    found = []

    def add(path: str, why: str, command: str):
        if os.path.exists(path):
            kb = du_kb(path)
            if kb >= DEV_CACHE_MIN_KB:
                found.append(Candidate("dev-caches", path, kb, why, remove="manual",
                                       safe=False, note=command))

    h = HOME
    add(f"{h}/.npm/_cacache", "npm download cache", "npm cache clean --force")
    add(f"{h}/.cache/uv", "uv (Python) cache", "uv cache clean")
    add(f"{h}/Library/Caches/pip", "pip cache", "pip cache purge")
    add(f"{h}/Library/Caches/Homebrew", "Homebrew downloads", "brew cleanup -s")
    add(f"{h}/.gradle/caches", "Gradle caches, rebuilt on the next build", f"rm -rf {h}/.gradle/caches")
    add(f"{h}/Library/Caches/CocoaPods", "CocoaPods pod cache", "pod cache clean --all")
    add(f"{h}/.cocoapods/repos", "CocoaPods spec repos; the CDN trunk replaces them unless a "
        "Podfile names the git specs source", f"rm -rf {h}/.cocoapods/repos/<name>")
    add(f"{h}/.nvm/.cache", "nvm download cache", "nvm cache clear")
    add(f"{h}/.cache/puppeteer", "Puppeteer browsers, re-downloaded on use", f"rm -rf {h}/.cache/puppeteer")
    add(f"{h}/.cache/codex-runtimes", "Codex runtimes, re-downloaded on use", f"rm -rf {h}/.cache/codex-runtimes")
    for shipit in sorted(glob.glob(f"{h}/Library/Caches/*.ShipIt")):
        add(shipit, "an app updater's staged download", f"rm -rf '{shipit}'")
    add(f"{h}/Library/Containers/com.docker.docker/Data/vms/0/Docker.raw",
        "Docker VM disk (size on disk, not its apparent size)", "docker system prune -a")

    node_dir = f"{h}/.nvm/versions/node"
    if os.path.isdir(node_dir):
        try:
            with open(f"{h}/.nvm/alias/default") as fh:
                default = fh.read().strip().lstrip("v")
        except OSError:
            default = ""
        pins = nvmrc_pins()
        versions = sorted(os.listdir(node_dir), key=version_key)
        if versions and not default[:1].isdigit():
            default = versions[-1].lstrip("v")  # `node` / `lts/*` — keep the newest
        for v in versions:
            bare = v.lstrip("v")
            if bare in pins or (default and (bare == default or bare.startswith(default + "."))):
                continue
            add(os.path.join(node_dir, v), "Node version: not nvm's default, pinned by no "
                ".nvmrc within 4 levels of ~", f"nvm uninstall {bare}")

    live = [p for p, ws in derived_data_entries() if not ws or os.path.exists(ws)]
    kb = sum(du_kb(p) for p in live)
    if kb >= DEV_CACHE_MIN_KB:
        found.append(Candidate("dev-caches", DERIVED_DATA, kb,
                               f"live Xcode DerivedData ({len(live)} projects that still exist)",
                               remove="manual", safe=False,
                               note=f"rm -rf {DERIVED_DATA}/* — costs a full rebuild of each"))
    return found


# ---------------------------------------------------- repo-local sources

def worktrees_of(repo: str) -> list[str]:
    """The repo and every linked worktree. Build junk accumulates per worktree, and
    the one holding the most is rarely the one you are standing in."""
    out = run("git", "-C", repo, "worktree", "list", "--porcelain")
    trees = [ln.split(" ", 1)[1] for ln in out.splitlines() if ln.startswith("worktree ")]
    return trees or [repo]


_LIVE_ROOTS: list[str] = []


def _repo_target(source: str, path: str, why: str, safe: bool = True) -> list[Candidate]:
    if not os.path.isdir(path):
        return []
    if in_use_by_xcodebuild(path, _LIVE_ROOTS):
        return [Candidate(source, path, du_kb(path), why, blocked=True,
                          note="an xcodebuild is running in this tree — leave it alone")]
    if fresh(path):
        return [Candidate(source, path, du_kb(path), why, blocked=True,
                          note=f"touched in the last {FRESH_MINUTES} min — assume a run is using it")]
    return [Candidate(source, path, du_kb(path), why, safe=safe)]


def scan_result_bundles(repo: str) -> list[Candidate]:
    """`.xcresult` bundles and the `TestResults/` they sit in.

    The largest repo-local source by far, and the least missed: a result bundle is
    the output of a test run that already reported its verdict. The Aurie repo's
    own instructions say to remove `TestResults/` after every run; nothing enforces
    it, so they accumulate one bundle per run.
    """
    found = []
    for tree in worktrees_of(repo):
        found += _repo_target("result-bundles", os.path.join(tree, "TestResults"),
                              "test output, regenerated by re-running the tests")
        for name in sorted(os.listdir(tree)) if os.path.isdir(tree) else []:
            if name.endswith(".xcresult"):
                found += _repo_target("result-bundles", os.path.join(tree, name),
                                      "loose result bundle")
    return found


def scan_derived_data_cli(repo: str, include_built: bool = False) -> list[Candidate]:
    """Per-worktree `DerivedData-CLI`, the path the repo's own test command uses.

    Held back when it contains a built `.app`, because that build is what lets
    `sim_install.sh --no-build` reinstall in seconds instead of a ~20 minute cold
    rebuild. Keeping one of these was worth more than the 5 GB it cost on the day
    every simulator had to be recreated. `--include-built` takes them anyway.
    """
    found = []
    for tree in worktrees_of(repo):
        path = os.path.join(tree, "DerivedData-CLI")
        if not os.path.isdir(path):
            continue
        # Liveness first, always. Testing for a built `.app` before this and
        # returning early would skip the check entirely, offering the cache of a
        # worktree with a running build — the opposite of what the hold is for.
        rows = _repo_target("derived-data-cli", path, "rebuilt on the next compile",
                            safe=False)
        if not rows:
            continue
        c = rows[0]
        if not c.blocked and run("find", os.path.join(path, "Build", "Products"),
                                 "-maxdepth", "2", "-name", "*.app", "-type", "d").strip():
            # Two separate holds, and `--only` clears only the first: every one of
            # these costs a rebuild, but this one costs a *cold* one. `--no-build`
            # reinstalls from it in seconds, which on the day every simulator had to
            # be recreated was worth far more than the 5 GB.
            c.why = "holds a built .app"
            c.requires = "include-built"
            c.note = ("kept: --no-build reinstalls from this in seconds "
                      "(--only derived-data-cli --include-built to take it)")
        found.append(c)
    return found


def scan_sim_sessions(repo: str, days: int = 7) -> list[Candidate]:
    """Per-run sim-test session directories: screenshots, hierarchy dumps, app logs.

    Kept for `days` because a session is the only place a capture's screenshot and
    dump exist — the screenshot store copies what it needs out of them, and a
    harvest reviewed the day after still wants the originals.
    """
    found, seen = [], set()
    cutoff = time.time() - days * 86400
    trees = [os.path.realpath(t) for t in worktrees_of(repo)]
    for tree in trees:
        for dirpath, dirnames, filenames in os.walk(tree):
            # Do not descend into another worktree, a build cache, or a package
            # checkout. Linked worktrees live under `.claude/worktrees` *inside* the
            # main checkout, so walking the main repo would otherwise reach every
            # one of them and report each session twice — and the second `rmtree`
            # then fails on a path the first already removed, failing the command.
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".")
                           and d not in {"DerivedData-CLI", "DerivedData", "build", "Pods",
                                         "node_modules", "SourcePackages"}
                           and os.path.realpath(os.path.join(dirpath, d)) not in trees
                           and not os.path.exists(os.path.join(dirpath, d, ".git"))]
            if os.path.basename(dirpath) != "Sessions":
                continue
            # "Sessions" is an ordinary word — Aura's own source is session-centric,
            # and a match on the name alone offered app source as reclaimable. The
            # sim-test harness puts its sessions under `<something>SimTests/`, and
            # each one carries the runner's own `config.json`. Require both.
            if not os.path.basename(os.path.dirname(dirpath)).endswith("SimTests"):
                continue
            dirnames[:] = []
            for name in sorted(os.listdir(dirpath)):
                path = os.path.realpath(os.path.join(dirpath, name))
                if not os.path.isdir(path) or path in seen:
                    continue
                if not os.path.exists(os.path.join(path, "config.json")):
                    continue
                if os.path.getmtime(path) > cutoff:
                    continue
                seen.add(path)
                found.append(Candidate("sim-sessions", path, du_kb(path),
                                       f"sim-test session, older than {days}d", safe=False))
    return found


def scan_merged_worktrees(repo: str) -> list[Candidate]:
    """Worktrees that did work, whose work is all in main, and whose tree is clean.

    Reported, never removed: `git worktree remove` is the only correct way to take
    one, and it belongs to whoever is working in it.

    All three conditions are load-bearing, and `git branch --merged` satisfies none
    of them reliably:

      * **It lies after a squash merge**, which is how these repos merge. The branch
        tip is a commit that never reached main, so `--merged` says no while every
        line of it is in main. The sound test is the *tree*: `git diff origin/main
        <branch>` empty means the branch's content is main's content.
      * **It fires on a branch that has done nothing.** A worktree created minutes
        ago, still at main's tip, is trivially "merged" — and is somebody's live
        workspace. Requiring at least one commit of its own excludes it; without
        that, this function reports the worktree it is being run from.
      * **Merged is not finished.** A worktree here was merged but held five
        uncommitted files including a new test, so a clean tree is required too.
    """
    found = []
    run("git", "-C", repo, "fetch", "-q", "origin", "main")
    for tree in worktrees_of(repo):
        if os.path.realpath(tree) == os.path.realpath(repo):
            continue
        branch = run("git", "-C", tree, "branch", "--show-current").strip()
        if not branch:
            continue
        own = run("git", "-C", tree, "rev-list", "--count", f"origin/main..{branch}").strip()
        if own in ("", "0"):
            continue  # no commits of its own — a fresh or untouched workspace
        same_tree = subprocess.run(["git", "-C", tree, "diff", "--quiet", "origin/main", branch],
                                   capture_output=True).returncode == 0
        if not same_tree or run("git", "-C", tree, "status", "--porcelain").strip():
            continue
        found.append(Candidate("merged-worktrees", tree, du_kb(tree),
                               f"{branch}: {own} commit(s), all of it in main, tree clean",
                               remove="manual", safe=False,
                               note=f"git -C {repo} worktree remove {tree}"))
    return found


def scan_orphan_simulators(repos: list[str]) -> list[Candidate]:
    """Per-session simulators whose worktree no longer exists.

    The sim-test harness names each session's device `"<model> (<label>)"`, the
    label being the worktree's directory name (`session_label` in `sim_common.sh`),
    `main` for the primary checkout, `ci`, or a `SIM_SESSION_NAME` override. Once
    the worktree is removed its simulator — 2-4 GB with the app installed — is
    never booted again and nothing deletes it.

    A label that is the name of a worktree of any `--repo` checkout, or that name
    plus a `-suffix`, is owned and not offered. Everything else is offered as a
    policy hold, never by default: a custom `SIM_SESSION_NAME` or a session from
    a repo that was not scanned looks exactly like an orphan from here. The last
    use date is in the report so the human can tell. A device that is not shut
    down is held as in use.
    """
    labels = {"main", "ci"}
    for repo in repos:
        labels |= {session_label(os.path.basename(t.rstrip(os.sep))) for t in worktrees_of(repo)}
    type_names = device_type_names()
    found = []
    for runtime, devs in default_devices().items():
        if "unavailable" in runtime.lower():
            continue
        for d in devs:
            # A session device is exactly `<its device type's name> (<label>)`. Matching
            # the shape alone would take Xcode's stock `iPad Pro 13-inch (M4)` and
            # `Apple Watch Series 11 (46mm)`, whose type names carry the parentheses.
            type_name = type_names.get(d.get("deviceTypeIdentifier", ""))
            m = re.fullmatch(re.escape(type_name) + r" \(([A-Za-z0-9._-]+)\)",
                             d.get("name") or "") if type_name else None
            udid = d.get("udid")
            if not m or not udid:
                continue
            label = m.group(1)
            if label in labels or any(label.startswith(owned + "-") for owned in labels):
                continue
            age = days_since(d.get("lastUsedAt"))
            idle = d.get("state") == "Shutdown"
            path = os.path.join(SIM_DEVICES, udid)
            found.append(Candidate(
                "orphan-simulators", path, du_kb(path) if os.path.isdir(path) else 0,
                f"{d.get('name')} · no worktree named {label!r} in the scanned checkouts · "
                + ("never used" if age is None else f"last used {age:.0f}d ago"),
                remove=f"simctl-default:{udid}", safe=False, blocked=not idle,
                note=("may be a SIM_SESSION_NAME or another repo's session — check before --only"
                      if idle else f"device is {d.get('state')} — a session is using it")))
    return found


# --------------------------------------------------------------- driving

# Ordered worst-offender first, which is also roughly safest first. Each takes the
# parsed args, so a source's knobs stay next to it.
MACHINE_SOURCES = {"xctest-clones": lambda a: scan_xctest_clones(),
                   "sim-runtimes": lambda a: scan_sim_runtimes(days=a.runtime_days),
                   "derived-data": lambda a: scan_derived_data(),
                   "simulators": lambda a: scan_simulators(),
                   "sim-attachments": lambda a: scan_sim_attachments(),
                   "device-support": lambda a: scan_device_support()}
REPO_SOURCES = {"result-bundles": lambda r, a: scan_result_bundles(r),
                "derived-data-cli": lambda r, a: scan_derived_data_cli(r, include_built=a.include_built),
                "sim-sessions": lambda r, a: scan_sim_sessions(r, days=a.days),
                "merged-worktrees": lambda r, a: scan_merged_worktrees(r)}
# Need every --repo at once: a simulator is an orphan only if no scanned checkout owns it.
ALL_REPO_SOURCES = {"orphan-simulators": lambda rs, a: scan_orphan_simulators(rs)}
# Reported only, and last: the space that is not build junk.
TAIL_SOURCES = {"dev-caches": lambda a: scan_dev_caches()}
ALL_SOURCES = list(MACHINE_SOURCES) + list(REPO_SOURCES) + list(ALL_REPO_SOURCES) + list(TAIL_SOURCES)


def collect(args) -> list[Candidate]:
    global _LIVE_ROOTS
    _LIVE_ROOTS = live_build_roots()
    if _LIVE_ROOTS:
        print("A build is running in:", file=sys.stderr)
        for r in _LIVE_ROOTS:
            print(f"  {r}", file=sys.stderr)
        print("  Anything inside those trees is held back.\n", file=sys.stderr)
    only = set(args.only or [])
    wanted = lambda name: not only or name in only  # noqa: E731
    found: list[Candidate] = []
    for name, fn in MACHINE_SOURCES.items():
        if wanted(name):
            found += fn(args)
    repos = []
    for repo in args.repo or []:
        if not os.path.exists(os.path.join(repo, ".git")):
            print(f"  ! {repo} is not a git checkout — skipped", file=sys.stderr)
            continue
        repos.append(repo)
        for name, fn in REPO_SOURCES.items():
            if wanted(name):
                found += fn(repo, args)
    for name, fn in ALL_REPO_SOURCES.items():
        if repos and wanted(name):
            found += fn(repos, args)
    for name, fn in TAIL_SOURCES.items():
        if wanted(name):
            found += fn(args)
    return found


def report(found: list[Candidate], args) -> int:
    if args.json:
        print(json.dumps({"freeKb": free_kb(),
                          "candidates": [c.as_dict() for c in found]}, indent=2))
        return 0
    print(f"Free now: {human(free_kb())}\n")
    if not found:
        print("Nothing reclaimable found.")
        return 0
    totals = {" ": 0, "!": 0, "x": 0, "-": 0}
    for source in ALL_SOURCES:
        rows = [c for c in found if c.source == source]
        if not rows:
            continue
        size = sum(c.kb for c in rows)
        takeable = sum(c.kb for c in rows if c.flag == " ")
        for c in rows:
            totals[c.flag] += c.kb
        print(f"{source}  —  {len(rows)} item(s), {human(size)}"
              f"{'' if takeable in (0, size) else f' ({human(takeable)} takeable)'}")
        for c in sorted(rows, key=lambda c: -c.kb)[: args.top]:
            print(f"  {c.flag} {human(c.kb):>12}  {c.path}")
            print(f"                 {c.why}{('  — ' + c.note) if c.note else ''}")
        if len(rows) > args.top:
            rest = sorted(rows, key=lambda c: -c.kb)[args.top:]
            print(f"    … {len(rest)} more, {human(sum(c.kb for c in rest))}")
        print()
    print(f"Reclaimable now: {human(totals[' '])}"
          + (f" · held for a decision: {human(totals['!'])} (!)" if totals["!"] else "")
          + (f" · in use: {human(totals['x'])} (x)" if totals["x"] else "")
          + (f" · reported only: {human(totals['-'])} (-)" if totals["-"] else ""))
    if totals["!"]:
        print("`!` needs a judgement this tool will not make — name its source with --only to say yes.")
    if totals["x"]:
        print("`x` is in use right now: no flag takes it, and the answer is to wait rather than to insist.")
    if totals["-"]:
        print("`-` is never removed by this tool — the note on each line is the command that clears it.")
    return 0


def reclaim(found: list[Candidate], args) -> int:
    only = set(args.only or [])
    flags = {"include-built"} if args.include_built else set()
    takeable = [c for c in found if c.takeable(only, flags)]
    blocked = [c for c in found if c.blocked]
    if blocked:
        print(f"{len(blocked)} item(s) are in use and will not be taken by any flag:")
        for c in blocked:
            print(f"  {c.path}\n      {c.note}")
        print()
    if not takeable:
        print("Nothing to reclaim. Run `report` to see what was held back and why.")
        return 0
    if not args.confirm:
        print(f"Would reclaim {human(sum(c.kb for c in takeable))} across "
              f"{len(takeable)} item(s). Re-run with --confirm.")
        return 0
    before, done, failed = free_kb(), 0, 0
    for c in takeable:
        ok, err = True, ""
        try:
            if c.remove.startswith("simctl:"):
                ok, err = run_checked("xcrun", "simctl", "--set", XCTEST_SET,
                                      "delete", c.remove.split(":", 1)[1])
            elif c.remove.startswith("simctl-default:"):
                ok, err = run_checked("xcrun", "simctl", "delete", c.remove.split(":", 1)[1])
            elif c.remove.startswith("runtime:"):
                ok, err = run_checked("xcrun", "simctl", "runtime", "delete",
                                      c.remove.split(":", 1)[1], timeout=600)
            elif c.remove == "empty":
                for name in os.listdir(c.path):
                    child = os.path.join(c.path, name)
                    if os.path.isdir(child) and not os.path.islink(child):
                        shutil.rmtree(child)
                    else:
                        os.remove(child)
            elif os.path.isdir(c.path):
                shutil.rmtree(c.path)
        except OSError as e:
            ok, err = False, str(e)
        if ok:
            done += 1
        else:
            # `run()` reports failure as an empty string, which a delete cannot act
            # on — hence `run_checked`. A wedged CoreSimulator on a nearly full disk
            # is precisely when a delete fails, and precisely when a false "clean
            # sweep" is most expensive.
            failed += 1
            print(f"  ! {c.path}: {err.strip() or 'delete failed'}", file=sys.stderr)
    actual = free_kb() - before
    print(f"Reclaimed {done} of {len(takeable)} item(s). "
          f"Free: {human(before)} -> {human(free_kb())} (+{human(max(0, actual))})")
    if any(c.remove.startswith("runtime:") for c in takeable):
        print("Runtime deletion finishes in the background — free space catches up over "
              "the next minute or two (`xcrun simctl runtime list` shows `Deleting`).")
    if failed:
        print(f"{failed} item(s) could not be removed — see above.", file=sys.stderr)
    return 1 if failed else 0


def check_space(args) -> int:
    """Pre-flight for a long job. One `df`, and it turns a dead session into a warning."""
    need = int(args.need_gb * 1024 * 1024)
    have = free_kb()
    ok = have >= need
    print(f"Free: {human(have)} · needed: {human(need)} · {'OK' if ok else 'LOW'}")
    if ok:
        return 0
    print("\nNot enough headroom to start. What is reclaimable:\n", file=sys.stderr)
    report(collect(args), argparse.Namespace(json=False, top=3))
    return 2


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="reclaim_disk",
        description=__doc__.split("\n\n")[0],
        epilog="report writes nothing. reclaim needs --confirm. Neither ever removes "
               "something a live device, workspace or xcodebuild is using.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    def common(sp):
        sp.add_argument("--repo", action="append", default=[],
                        help="a git checkout to scan for repo-local build junk (repeatable)")
        sp.add_argument("--only", action="append", default=[],
                        choices=ALL_SOURCES,
                        help="limit to these sources; also opts in to ones held back by default")
        sp.add_argument("--days", type=int, default=7,
                        help="keep sim-test sessions younger than this (default 7)")
        sp.add_argument("--runtime-days", type=int, default=RUNTIME_DAYS,
                        help="offer simulator runtimes unused for this long "
                             f"(default {RUNTIME_DAYS})")
        sp.add_argument("--include-built", action="store_true",
                        help="also take a DerivedData-CLI that holds a built .app")

    sp = sub.add_parser("report", help="list every reclaimable source with sizes (default)")
    common(sp)
    sp.add_argument("--top", type=int, default=5, help="items shown per source (default 5)")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("reclaim", help="delete what report marked takeable")
    common(sp)
    sp.add_argument("--confirm", action="store_true", help="actually delete")
    sp.add_argument("--top", type=int, default=5)
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("check-space", help="pre-flight a long job against a free-space floor")
    common(sp)
    sp.add_argument("--need-gb", type=float, default=60.0,
                    help="floor in GB (default 60 — a three-sky harvest exhausted 44)")
    sp.add_argument("--top", type=int, default=3)
    sp.add_argument("--json", action="store_true")

    # `report` is the default verb, so `reclaim_disk.py --repo X` must work. The
    # options live on the subparsers, so this has to be decided before parsing:
    # letting argparse see a leading `--repo` makes it exit on an unrecognized
    # argument, and the fallback after it could only ever catch a bare invocation.
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] not in sub.choices and argv[0] not in ("-h", "--help")):
        argv = ["report"] + argv
    args = p.parse_args(argv)
    if args.cmd == "check-space":
        return check_space(args)
    found = collect(args)
    return report(found, args) if args.cmd == "report" else reclaim(found, args)


if __name__ == "__main__":
    sys.exit(main())
