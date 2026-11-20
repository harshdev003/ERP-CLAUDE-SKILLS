#!/usr/bin/env python3
"""Cut an iOS release of the Aura app.

Bump the version, archive and upload to TestFlight from this host, land the bump
on master through a pull request, then tag the squash-merge commit.

The ordering is deliberate and is the whole reason this script exists: the build
runs BEFORE the PR, so an archive that fails never leaves a version bump merged
to master. The cost of that ordering is that the tagged commit is not the built
commit — a squash merge creates a new one — so the script asserts the two carry
an identical tree before it tags. See `assert_tree_match`.

No state file. Every phase is derived from git and GitHub, so a run that dies
half-way is resumed by running it again.
"""

from __future__ import annotations

import argparse
import base64
import glob
import hashlib
import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[4]
IOS_DIR = REPO_ROOT / "ios"
PBXPROJ_REL = "ios/Aura.xcodeproj/project.pbxproj"

DEFAULT_BRANCH = "master"
REMOTE = "origin"
APP_TARGET = "Aura"

# Bare `X.Y.Z-N`, no `v` prefix — matches every tag in this repo (3.19.148-1).
TAG_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)-(\d+)$")
# Older tags predate build numbers (3.19.122). Recognised for "last release"
# lookups so release notes span the right range; never produced.
LEGACY_TAG_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
BUILD_RE = re.compile(r"^[1-9]\d*$")

# GitHub reports a PR as mergeable in several ways. UNSTABLE means a
# non-required check is red, which the repo itself accepts — treating it as
# blocked would hang on a check nobody gates on.
MERGEABLE_STATES = {"CLEAN", "UNSTABLE", "HAS_HOOKS"}
MERGE_WAIT_SECONDS = 900
MERGE_POLL_SECONDS = 15

# GitHub also reports BLOCKED while a *required* check is merely still running,
# which is indistinguishable by status alone from a check that failed. Treating
# BLOCKED as terminal aborted the 3.20.0-1 release after the binary was already
# on TestFlight, over a check that went green two minutes later. `blocked_reason`
# tells the two apart; these are the check states that mean "not done yet".
PENDING_CHECK_STATES = {"PENDING", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED", "EXPECTED"}

# The three bundle IDs an Aura archive embeds. AuraWatch targets are dead — not
# in the `Aura` scheme, and their Embed Watch Content phase is empty — so they
# need no profile. See .claude/rules/native-ios.md § CI signing.
ARCHIVE_BUNDLE_IDS = (
    "com.AuraMobile",
    "com.AuraMobile.Aura-Widget",
    "com.AuraMobile.OneSignalNotificationServiceExtension",
)

# Xcode 16 moved provisioning profiles out of ~/Library/MobileDevice. Both are
# searched: a host that has been upgraded in place can still have profiles only
# in the old location, and finding none anywhere is a blocking problem rather
# than a reason to skip the check.
PROFILE_DIRS = (
    Path.home() / "Library/Developer/Xcode/UserData/Provisioning Profiles",
    Path.home() / "Library/MobileDevice/Provisioning Profiles",
)

# Warn when a profile that currently covers the build is close to lapsing —
# an expiry mid-release is the same outage as a missing profile, just later.
PROFILE_EXPIRY_WARN_DAYS = 30

# Markers that name the command xcodebuild actually failed on, most specific
# first. Scanned instead of the last "❌" line: `RCTDeprecation` (below) emits
# one on every single run and would otherwise always look like the cause.
BUILD_FAILURE_MARKERS = (
    "error: exportArchive",
    "Cloud signing permission error",
    "The following build commands failed:",
    "** ARCHIVE FAILED **",
    "** EXPORT FAILED **",
    "error: Node.js not found",
)

# Emitted by clang on every archive of this project and immediately recovered
# from — the module is rebuilt and the build continues. It is NOT a cause of
# failure, and deleting ModuleCache.noindex does not stop it (verified: it
# regenerates within the same build). Never report it as the reason.
BENIGN_DIAGNOSTIC = "module 'RCTDeprecation' in precompiled file"

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Read the version through the same library the Fastfile uses. project.pbxproj
# has three configurations per target and quotes identifiers containing "-", so
# a line-oriented parser picks the wrong number often enough to matter.
RUBY_READ_VERSION = """
require "xcodeproj"
proj = Xcodeproj::Project.open(ARGV[0])
target = proj.targets.find { |t| t.name == ARGV[1] }
abort("target #{ARGV[1]} not found") unless target
config = target.build_configurations.find { |c| c.name == "Release" }
abort("no Release configuration on #{ARGV[1]}") unless config
puts "#{config.build_settings['MARKETING_VERSION']}\\t#{config.build_settings['CURRENT_PROJECT_VERSION']}"
"""


class ReleaseError(Exception):
    """A condition the operator has to resolve. Exits 2."""


# ---------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------

# Prepended to PATH for every subprocess once the .nvmrc-pinned Node is located.
# See `resolve_node` — this is how the pinned version reaches npm, fastlane, and
# most importantly Xcode's "Bundle React Native code and images" phase.
NODE_BIN_DIR: Path | None = None


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = True,
    env: dict | None = None,
    tee: Path | None = None,
) -> subprocess.CompletedProcess:
    """Run a command. `capture=False` streams to this process's stdout, which is
    what the multi-minute fastlane archive needs so progress is visible.

    `tee` mirrors that stream into a file as it goes. Streaming and capturing are
    otherwise exclusive, and the archive needs both: live progress for whoever is
    watching, plus a log to scan when it fails (see `diagnose_build_failure`).
    """
    merged = {**os.environ, **(env or {})}
    if NODE_BIN_DIR:
        merged["PATH"] = f"{NODE_BIN_DIR}:{merged.get('PATH', '')}"
    if tee is not None and not capture:
        result = _run_teed(cmd, cwd=cwd, env=merged, tee=tee)
    else:
        result = subprocess.run(
            cmd,
            cwd=str(cwd or REPO_ROOT),
            capture_output=capture,
            text=True,
            env=merged,
        )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        raise ReleaseError(
            f"`{' '.join(cmd)}` failed (exit {result.returncode})"
            + (f":\n{detail}" if detail else "")
        )
    return result


def _run_teed(
    cmd: list[str], *, cwd: Path | None, env: dict, tee: Path
) -> subprocess.CompletedProcess:
    """Stream a command's output to stdout while mirroring it into `tee`."""
    tee.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd or REPO_ROOT),
        env=env,
        text=True,
        bufsize=1,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    with tee.open("w", encoding="utf-8", errors="replace") as fh:
        for line in proc.stdout:  # type: ignore[union-attr]
            sys.stdout.write(line)
            sys.stdout.flush()
            fh.write(line)
    return subprocess.CompletedProcess(cmd, proc.wait(), "", "")


def out(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> str:
    return run(cmd, cwd=cwd, check=check).stdout.strip()


def git(*args: str, check: bool = True) -> str:
    return out(["git", *args], check=check)


def gh_json(*args: str) -> object:
    raw = out(["gh", *args])
    return json.loads(raw) if raw else None


def repo_slug() -> str:
    """owner/repo from GitHub. Not the directory name — this runs from worktrees
    whose folder is named after a branch."""
    result = run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], check=False)
    return result.stdout.strip() or REPO_ROOT.name


# Under --json, stdout carries the payload and nothing else, so progress notes
# move to stderr. They are still emitted — a caller parsing JSON should not lose
# the "local tags disagree with origin" note, just not have it in the document.
JSON_MODE = False


def log(msg: str = "") -> None:
    print(msg, file=sys.stderr if JSON_MODE else sys.stdout, flush=True)


def header(msg: str) -> None:
    log("")
    log(f"── {msg} " + "─" * max(0, 68 - len(msg)))


# ---------------------------------------------------------------------------
# Version reading and arithmetic
# ---------------------------------------------------------------------------


def read_version_at(ref: str | None) -> tuple[str, str]:
    """Read (version, build) from the Aura target's Release config.

    `ref=None` reads the working tree. A ref reads that commit's pbxproj by
    materialising it into a throwaway `.xcodeproj` directory — Xcodeproj only
    needs the one file inside a correctly-named folder.
    """
    if ref is None:
        return _read_pbxproj(REPO_ROOT / "ios" / "Aura.xcodeproj")

    with tempfile.TemporaryDirectory() as tmp:
        proj_dir = Path(tmp) / "Aura.xcodeproj"
        proj_dir.mkdir()
        content = run(["git", "show", f"{ref}:{PBXPROJ_REL}"]).stdout
        (proj_dir / "project.pbxproj").write_text(content)
        return _read_pbxproj(proj_dir)


def _read_pbxproj(proj_dir: Path) -> tuple[str, str]:
    result = run(
        ["bundle", "exec", "ruby", "-e", RUBY_READ_VERSION, str(proj_dir), APP_TARGET],
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseError(
            "Could not read the project version via the xcodeproj gem.\n"
            f"  {(result.stderr or result.stdout).strip()}\n"
            "  Run `bundle install` in the repo root — the gem is pinned in the Gemfile\n"
            "  and has to be installed into the Ruby named by .ruby-version (3.3.11)."
        )
    version, _, build = result.stdout.strip().partition("\t")
    if not VERSION_RE.match(version) or not BUILD_RE.match(build):
        raise ReleaseError(
            f"Project reports version={version!r} build={build!r}, which is not X.Y.Z / N."
        )
    return version, build


def next_version(current: str, current_build: str, level: str) -> tuple[str, str]:
    """Apply a bump level. Every version bump resets the build to 1; `build`
    holds the version and increments, which is how you re-ship a version whose
    binary was rejected or superseded."""
    major, minor, patch = (int(p) for p in current.split("."))
    if level == "major":
        return f"{major + 1}.0.0", "1"
    if level == "minor":
        return f"{major}.{minor + 1}.0", "1"
    if level == "patch":
        return f"{major}.{minor}.{patch + 1}", "1"
    if level == "build":
        return current, str(int(current_build) + 1)
    raise ReleaseError(f"Unknown level {level!r} — expected major, minor, patch, or build.")


def sort_key(tag: str) -> tuple:
    m = TAG_RE.match(tag)
    if m:
        return (int(m[1]), int(m[2]), int(m[3]), int(m[4]))
    m = LEGACY_TAG_RE.match(tag)
    if m:
        return (int(m[1]), int(m[2]), int(m[3]), 0)
    return (0, 0, 0, 0)


def is_ancestor(ref: str, of: str) -> bool:
    return run(["git", "merge-base", "--is-ancestor", ref, of], check=False).returncode == 0


def latest_release_tag(exclude: str | None = None, ancestor_of: str | None = None) -> str | None:
    """The most recent release tag reachable from `ancestor_of` (default: the tip
    of the default branch).

    Ordered by tag creation date, NOT by version number. The tag namespace has a
    typo in it — `3.181.110-12` is a real tag on origin, and it sorts above every
    3.19.x release under any numeric comparison. Chronological order plus an
    ancestry check gives the tag the previous release actually cut, which is what
    a release-notes range needs.

    `ancestor_of` is what makes that true for a back-fill as well as for a fresh
    release. Anchored on master's tip it answers "the newest release", which is
    the wrong end of the range when the release being described is an older one:
    every tag cut since would be reachable, and the notes would list the work
    that shipped *after* it.
    """
    anchor = ancestor_of or f"{REMOTE}/{DEFAULT_BRANCH}"
    ordered = git(
        "for-each-ref", "--sort=-creatordate", "--format=%(refname:short)", "refs/tags"
    ).splitlines()
    for tag in ordered:
        if tag == exclude or not (TAG_RE.match(tag) or LEGACY_TAG_RE.match(tag)):
            continue
        if is_ancestor(tag, anchor):
            return tag
    return None


# ---------------------------------------------------------------------------
# Derived state
# ---------------------------------------------------------------------------


@dataclass
class State:
    env: str
    version: str
    build: str
    tag: str
    branch: str
    baseline_version: str
    baseline_build: str
    level: str | None
    local_branch_sha: str | None
    remote_branch_sha: str | None
    tag_local: bool
    tag_remote: bool
    github_release: bool
    pr: dict | None
    phase: str
    master_sha: str = ""
    last_tag: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def built_commit(self) -> str | None:
        """The commit the binary was archived from — the bump commit at the tip
        of the release branch. Prefer the remote copy: once pushed it is the
        durable record, and a local branch can be reset or deleted."""
        return self.remote_branch_sha or self.local_branch_sha


def fetch() -> None:
    """Refresh branches, then tags.

    The two are separate and the tag fetch is non-fatal on purpose. This repo
    carries local tags that disagree with origin for some older releases
    (3.18.x), and `git fetch --tags` exits non-zero on the whole operation
    rather than skipping just those refs. Failing the release over a stale
    legacy tag would be absurd — and nothing here trusts the local tag list for
    a decision that matters: whether the release tag already exists is answered
    by `git ls-remote`, which asks origin directly.
    """
    run(["git", "fetch", "--prune", REMOTE])
    result = run(["git", "fetch", "--tags", REMOTE], check=False)
    if result.returncode != 0:
        # ` ! [rejected]   3.18.32 -> 3.18.32  (would clobber existing tag)`
        clobbered = [
            line.split()[2]
            for line in (result.stderr or "").splitlines()
            if "would clobber existing tag" in line and len(line.split()) > 2
        ]
        if clobbered:
            log(
                "Note: local tags disagree with origin and were left alone: "
                + ", ".join(clobbered[:8])
                + (" …" if len(clobbered) > 8 else "")
            )


def rev_or_none(ref: str) -> str | None:
    result = run(["git", "rev-parse", "--verify", "--quiet", ref], check=False)
    return result.stdout.strip() or None


def remote_branch_sha(branch: str) -> str | None:
    line = git("ls-remote", "--heads", REMOTE, f"refs/heads/{branch}")
    return line.split()[0] if line else None


def remote_tag_exists(tag: str) -> bool:
    return bool(git("ls-remote", "--tags", REMOTE, f"refs/tags/{tag}"))


def github_release_exists(tag: str) -> bool:
    return run(["gh", "release", "view", tag], check=False).returncode == 0


def find_pr(branch: str) -> dict | None:
    prs = gh_json(
        "pr", "list",
        "--head", branch,
        "--state", "all",
        "--json", "number,state,url,mergeStateStatus,mergeCommit,headRefOid,title",
        "--limit", "10",
    )
    if not prs:
        return None
    # Newest first; a re-cut of the same tag would be an operator error anyway.
    return sorted(prs, key=lambda p: p["number"])[-1]


def derive_state(env: str, level: str | None, version: str | None, build: str | None) -> State:
    fetch()

    master_sha = rev_or_none(f"{REMOTE}/{DEFAULT_BRANCH}")
    if not master_sha:
        raise ReleaseError(
            f"{REMOTE}/{DEFAULT_BRANCH} is not resolvable. Run `git fetch {REMOTE}` and retry."
        )

    baseline_version, baseline_build = read_version_at(f"{REMOTE}/{DEFAULT_BRANCH}")

    if version:
        target_version = version
        target_build = build or "1"
    elif level:
        target_version, target_build = next_version(baseline_version, baseline_build, level)
        if build:
            target_build = build
    else:
        raise ReleaseError("Give either --level or --version.")

    if not VERSION_RE.match(target_version):
        raise ReleaseError(f"Invalid version {target_version!r} — expected X.Y.Z.")
    if not BUILD_RE.match(str(target_build)):
        raise ReleaseError(f"Invalid build {target_build!r} — expected a positive integer.")

    tag = f"{target_version}-{target_build}"
    branch = f"release/{tag}"
    tag_on_origin = remote_tag_exists(tag)

    state = State(
        env=env,
        version=target_version,
        build=str(target_build),
        tag=tag,
        branch=branch,
        baseline_version=baseline_version,
        baseline_build=baseline_build,
        level=level,
        local_branch_sha=rev_or_none(f"refs/heads/{branch}"),
        remote_branch_sha=remote_branch_sha(branch),
        tag_local=bool(rev_or_none(f"refs/tags/{tag}")),
        tag_remote=tag_on_origin,
        github_release=github_release_exists(tag) if tag_on_origin else False,
        pr=find_pr(branch),
        phase="",
        master_sha=master_sha,
        last_tag=latest_release_tag(exclude=tag),
    )

    state.phase = _phase(state)
    _add_warnings(state)
    return state


def _phase(s: State) -> str:
    if s.tag_remote:
        return "released" if s.github_release else "tagged-unreleased"
    if s.pr and s.pr["state"] == "MERGED":
        return "merged-untagged"
    if s.pr and s.pr["state"] == "OPEN":
        return "pr-open"
    if s.pr and s.pr["state"] == "CLOSED":
        return "pr-closed"
    if s.remote_branch_sha:
        return "branch-pushed"
    if s.local_branch_sha:
        return "bumped"
    return "fresh"


def phase_block_reason(s: State) -> str | None:
    """A phase that no amount of pre-flight fixing can proceed from.

    Returned as a pre-flight *problem* rather than a warning so `plan` and `run`
    agree: `plan` previously only warned about `pr-closed`, then printed the
    remaining steps and `Pre-flight OK` and exited 0, while `run` refused
    outright. A plan that green-lights a release `run` will not perform is worse
    than no plan.
    """
    if s.phase != "pr-closed":
        return None
    return (
        f"PR #{s.pr['number']} for {s.branch} was closed without merging, so this "
        "release cannot proceed.\n"
        f"      Reopen {s.pr['url']}, or delete the branch (local and remote) and "
        "re-run to cut a fresh build."
    )


def unfinished_release_tag(s: State) -> str | None:
    """A release that reached its tag and stopped before publishing.

    The version `master` declares IS the last release that landed, so its tag is
    the only one worth checking. A `--level` run computes its version from that
    same baseline, so it walks straight past the unfinished release into a new
    one — and the "just re-run the same command" resume never reaches it.
    """
    tag = f"{s.baseline_version}-{s.baseline_build}"
    if tag == s.tag:
        return None  # this run IS that release; the phase already says so
    if remote_tag_exists(tag) and not github_release_exists(tag):
        return tag
    return None


def _add_warnings(s: State) -> None:
    # Only when cutting a brand-new computed version: with an explicit --version
    # the operator is already naming a specific release, and the two extra
    # lookups would be paid on every invocation for nothing.
    if s.level and s.phase == "fresh":
        pending = unfinished_release_tag(s)
        if pending:
            version, _, build = pending.rpartition("-")
            s.warnings.append(
                f"{pending} is tagged on {REMOTE} with no GitHub release — an earlier "
                f"release stopped before publishing, and this run cuts {s.tag} past it "
                f"rather than finishing it. To publish it first:\n"
                f"           run --env {s.env} --version {version} --build {build}"
            )

    # Compared against what master currently declares, not against the newest
    # tag: the tag namespace contains a typo (3.181.110-12) that sorts above
    # every real release, so it cannot be used for an ordering judgement.
    target = sort_key(s.tag)
    baseline = sort_key(f"{s.baseline_version}-{s.baseline_build}")
    if target <= baseline:
        s.warnings.append(
            f"{s.tag} is not ahead of what {REMOTE}/{DEFAULT_BRANCH} declares "
            f"({s.baseline_version} ({s.baseline_build})). App Store Connect rejects a "
            "build number already used for a version."
        )
    if s.env == "dev":
        s.warnings.append(
            "env=dev still bumps master and cuts a release tag, same as prod — "
            "the tag namespace is shared."
        )
    # `pr-closed` is deliberately NOT a warning — see phase_block_reason.


# ---------------------------------------------------------------------------
# Node version
#
# `.nvmrc` is the single source of truth for the Node that builds the JS bundle
# (CLAUDE.md). It matters here for one reason that is easy to miss: Xcode's
# "Bundle React Native code and images" phase sources `ios/.xcode.env`, which
# resolves `NODE_BINARY=$(command -v node)` — so the archive bundles with
# whatever Node is on PATH in the shell that launched xcodebuild. That is this
# process. A release host sitting on an EOL Node would ship a bundle built by it
# and nothing would say so.
#
# So `nvm install` runs unconditionally (it reads .nvmrc and no-ops when that
# version is present), and the resulting bin directory is prepended to PATH for
# the subprocesses this script starts. Prepending, rather than invoking binaries
# by absolute path: `npm` shebangs `env node`, so an absolute npm would still
# pick the wrong Node back up off PATH.
# ---------------------------------------------------------------------------


def required_node_version() -> str | None:
    """The pinned version, or None when .nvmrc is absent or holds an alias such
    as `lts/*`. An alias is not a failure — nvm resolves it; it just means the
    post-install assertion below has nothing exact to compare against."""
    nvmrc = REPO_ROOT / ".nvmrc"
    if not nvmrc.is_file():
        return None
    raw = nvmrc.read_text().strip().lstrip("v")
    return raw if re.fullmatch(r"\d+\.\d+\.\d+", raw) else None


def active_node_version() -> str | None:
    result = run(["node", "--version"], check=False)
    return result.stdout.strip().lstrip("v") or None if result.returncode == 0 else None


def find_nvm_sh() -> Path | None:
    """Locate `nvm.sh`.

    `nvm` is a shell function, never an executable — `shutil.which("nvm")` returns
    None on a machine where nvm works perfectly, so the script has to find the
    script it is defined in and source that.
    """
    candidates = []
    if os.environ.get("NVM_DIR"):
        candidates.append(Path(os.environ["NVM_DIR"]) / "nvm.sh")
    candidates.append(Path.home() / ".nvm" / "nvm.sh")
    brew = run(["brew", "--prefix", "nvm"], check=False)
    if brew.returncode == 0 and brew.stdout.strip():
        candidates.append(Path(brew.stdout.strip()) / "nvm.sh")
    return next((c for c in candidates if c.is_file()), None)


def _nvm(nvm_sh: Path, command: str, *, capture: bool) -> subprocess.CompletedProcess:
    """Run one nvm command in a shell that has sourced nvm.sh.

    `bash -c` with an explicit source, NOT `bash -lc`: a login shell drags in the
    operator's whole profile, which is slower and can fail or print noise for
    reasons that have nothing to do with this release. `--no-use` stops nvm from
    switching to its default version on load, so the only version selection that
    happens is the one asked for here.

    cwd is the repo root so `nvm install` / `nvm use` find `.nvmrc`.
    """
    prelude = (
        f'export NVM_DIR={shlex.quote(str(nvm_sh.parent))}; '
        f'. {shlex.quote(str(nvm_sh))} --no-use; '
    )
    return run(["bash", "-c", prelude + command], cwd=REPO_ROOT, check=False, capture=capture)


# `plan` writes nothing, and installing a Node toolchain is a write — so only
# `run` is allowed to install. `plan` reports what `run` would do instead.
ALLOW_NODE_INSTALL = False

# Resolve NODE_BINARY exactly as Xcode's "Bundle React Native code and images"
# phase will, by running the same steps in the same order.
#
# Putting the pinned Node on PATH is NOT sufficient on its own, which is the
# trap this closes. The phase sources `with-environment.sh`, which sources
# `$PODS_ROOT/../.xcode.env` and then `.xcode.env.local` — and the phase's
# `command -v node` fallback only fires `if [ -z "$NODE_BINARY" ]`. So an
# absolute NODE_BINARY in the gitignored, per-developer `.xcode.env.local` wins
# outright over everything this script arranges, silently. `npm install` now
# regenerates that file rather than leaving it hand-maintained, which stops it
# rotting — but it does not stop it winning, so this still has to be resolved
# rather than assumed.
#
# Replicated rather than parsed: `.xcode.env.local` is shell, and a regex for
# `NODE_BINARY=` would miss `$(command -v node)`, conditionals, and anything
# else a developer legitimately put there.
#
# The two env files are sourced by absolute path rather than by invoking
# with-environment.sh, which resolves them as `$PODS_ROOT/../.xcode.env`. That
# form needs `ios/Pods/` to exist for the `..` to resolve — and pre-flight runs
# *before* `pod install`, so on a fresh checkout invoking the real script would
# source nothing and report a false all-clear. The order below is
# with-environment.sh's own (default → .xcode.env → .xcode.env.local), followed
# by the bundle phase's homebrew last resort from project.pbxproj.
BUNDLE_PHASE_PROBE = r"""
REPO="$1"
NODE_BINARY=$(command -v node || echo "")
[ -f "$REPO/ios/.xcode.env" ] && . "$REPO/ios/.xcode.env"
[ -f "$REPO/ios/.xcode.env.local" ] && . "$REPO/ios/.xcode.env.local"
if [ -z "$NODE_BINARY" ]; then
  export PATH=/opt/homebrew/bin:$PATH
  NODE_BINARY=$(command -v node)
fi
printf '%s' "$NODE_BINARY"
"""


# Does .xcode.env.local set NODE_BINARY at all, and to what?
#
# Asked by unsetting NODE_BINARY and sourcing ONLY that file — not by sourcing
# the chain and seeing whether the value changed at the .local step. The
# change-detection version has a false negative that matters: an override
# pointing at the same path the host's `node` currently resolves to looks like
# no override, right up until PATH moves and it starts winning.
XCODE_ENV_LOCAL_PROBE = r"""
REPO="$1"
unset NODE_BINARY
[ -f "$REPO/ios/.xcode.env.local" ] && . "$REPO/ios/.xcode.env.local"
printf '%s' "${NODE_BINARY:-}"
"""


def effective_node_binary() -> str | None:
    """The node the archive will actually bundle with, or None if unresolvable."""
    result = run(
        ["bash", "-c", BUNDLE_PHASE_PROBE, "probe", str(REPO_ROOT)], check=False
    )
    return result.stdout.strip() or None


def xcode_env_local_override() -> str | None:
    """What .xcode.env.local sets NODE_BINARY to, or None if it sets it at all."""
    result = run(
        ["bash", "-c", XCODE_ENV_LOCAL_PROBE, "probe", str(REPO_ROOT)], check=False
    )
    return result.stdout.strip() or None


def xcode_node_warning() -> str | None:
    """The most `plan` can honestly say while the pinned Node isn't installed.

    Two shapes of `.xcode.env.local` are indistinguishable here, and they have
    opposite verdicts: an absolute `NODE_BINARY=/path/to/node` wins over the
    install and is a real problem, while `NODE_BINARY=$(command -v node)` follows
    PATH and resolves correctly once `run` installs. Both read as "sets
    NODE_BINARY" right now, and PATH is not yet what the archive will see — so
    `plan` flags it and `run` decides it, rather than `plan` guessing.
    """
    if not NODE_PENDING_INSTALL:
        return None
    override = xcode_env_local_override()
    if not override:
        return None
    return (
        f"ios/.xcode.env.local sets NODE_BINARY ({override}), which wins over this "
        f"script's PATH.\n"
        f"           `plan` cannot tell whether it resolves to the pinned Node until "
        f"that Node is installed;\n"
        f"           `run` installs first and then refuses the archive if it does not."
    )


def xcode_node_problem() -> str | None:
    """Verify the bundle phase resolves to the .nvmrc Node, not just this PATH.

    Split on whether the pinned Node is installed yet, because the two cases can
    be checked to different depths and conflating them made `plan` block on a
    state `run` handles fine:

      resolved  PATH is final, so replay the whole bundle phase and assert the
                version it lands on.
      pending   `run` will `nvm install` first (`plan` deliberately won't — it
                writes nothing), so PATH is NOT yet what the archive will see.
                Asserting now would measure the host's current Node — the very
                EOL one this skill exists to route around — and report it as an
                `.xcode.env` fault, which is both wrong and misdirecting.

    A `.xcode.env.local` override is still checkable while pending: it is an
    absolute path, so it wins over the install too, and its version can be read
    directly.
    """
    want = required_node_version()
    if not want or NODE_PENDING_INSTALL:
        return None

    binary = effective_node_binary()
    if not binary or not Path(binary).is_file():
        return (
            "Xcode's bundle phase would resolve no usable NODE_BINARY, so the "
            "archive would fail at the JS bundle step.\n"
            "      Check ios/.xcode.env and ios/.xcode.env.local."
        )

    source = (
        "ios/.xcode.env.local (gitignored, per-developer)"
        if xcode_env_local_override()
        else "ios/.xcode.env"
    )
    got = run([binary, "--version"], check=False).stdout.strip().lstrip("v")
    if got == want:
        return None

    return (
        f"Xcode's bundle phase would build the JS bundle with Node v{got or '?'}, not "
        f"the v{want} that .nvmrc pins.\n"
        f"      NODE_BINARY resolves to: {binary}\n"
        f"      Set by {source}, which wins over this script's PATH.\n"
        f"      Fix: re-pin it under the right Node —\n"
        f"        nvm use && npm install --legacy-peer-deps\n"
        f"      (postinstall rewrites ios/.xcode.env.local, and refuses to write a\n"
        f"      pin for any Node other than the one .nvmrc names.)"
    )


# True once resolve_node finds the pinned Node absent and this invocation may not
# install it (i.e. `plan`). PATH is then NOT what the archive will see, so version
# assertions against it are meaningless — see xcode_node_problem.
NODE_PENDING_INSTALL = False

_NODE_RESULT: tuple[str | None, str] | None = None


def resolve_node() -> tuple[str | None, str]:
    """Put the .nvmrc Node on PATH for every subprocess. Returns (problem, status).

    `nvm install` with no arguments reads .nvmrc, is a no-op when that version is
    already installed, and selects it — so one command covers install and use, and
    resolves aliases that this script could not resolve on its own.

    A shell's `nvm use` dies with that shell, so it cannot simply be fired and
    forgotten. Instead the sourced shell is asked where `node` ended up, and that
    directory is prepended to PATH for the argv-list subprocesses that follow.
    The operator's own shell and `nvm alias default` are never touched.

    Cached, and it has to be: the first call sets NODE_BIN_DIR, after which
    `active_node_version()` reports the version this function just arranged for,
    and a second call would claim it had been active all along.
    """
    global NODE_BIN_DIR, _NODE_RESULT
    if _NODE_RESULT is not None:
        return _NODE_RESULT

    want = required_node_version()
    if not (REPO_ROOT / ".nvmrc").is_file():
        _NODE_RESULT = (None, ".nvmrc is absent — not checked")
        return _NODE_RESULT

    nvm_sh = find_nvm_sh()
    if nvm_sh:
        # `nvm use` selects without installing, and fails when the version is
        # absent — so it doubles as the "is it already here?" probe that lets
        # plan stay read-only.
        located = _nvm(nvm_sh, "nvm use >/dev/null 2>&1 && command -v node", capture=True)

        if located.returncode != 0 and not ALLOW_NODE_INSTALL:
            global NODE_PENDING_INSTALL
            NODE_PENDING_INSTALL = True
            _NODE_RESULT = (
                None,
                f"v{want or '(from .nvmrc)'} not installed — `run` will `nvm install` it",
            )
            return _NODE_RESULT

        if located.returncode != 0:
            # Streamed, not captured: a cold install downloads a toolchain and
            # goes quiet for a while, which reads as a hang otherwise. Under
            # --json it is captured instead, so stdout stays parseable.
            log(f"nvm install (reading .nvmrc) via {nvm_sh}")
            _nvm(nvm_sh, "nvm install", capture=JSON_MODE)
            located = _nvm(nvm_sh, "nvm use >/dev/null 2>&1 && command -v node", capture=True)

        node_path = located.stdout.strip()
        if located.returncode == 0 and node_path and Path(node_path).is_file():
            NODE_BIN_DIR = Path(node_path).parent
            got = run(["node", "--version"], check=False).stdout.strip().lstrip("v")
            # nvm reported success, so a mismatch here means .nvmrc says one thing
            # and nvm selected another — do not build on that.
            if want and got != want:
                _NODE_RESULT = (
                    f"nvm selected Node v{got} but .nvmrc pins v{want}.",
                    f"v{got} != .nvmrc v{want}",
                )
                NODE_BIN_DIR = None
                return _NODE_RESULT
            _NODE_RESULT = (None, f"v{got} via nvm ({NODE_BIN_DIR})")
            return _NODE_RESULT

    # No nvm. Acceptable only if the Node already on PATH is the right one.
    active = active_node_version()
    if want and active == want:
        _NODE_RESULT = (None, f"v{want} (already active; nvm not found, not needed)")
        return _NODE_RESULT

    _NODE_RESULT = (
        (
            f"Cannot get Node v{want or '(from .nvmrc)'}: nvm was not found "
            f"(looked for nvm.sh under $NVM_DIR, ~/.nvm, and brew), and the active Node "
            f"is v{active or 'none'}.\n"
            "      Xcode's bundle phase resolves NODE_BINARY from PATH, so the archive "
            "would build the JS bundle with the wrong Node.\n"
            "      Fix: install nvm, or switch this shell to the .nvmrc version yourself."
        ),
        f"UNAVAILABLE (active v{active or 'none'}, .nvmrc v{want or '?'})",
    )
    return _NODE_RESULT


# ---------------------------------------------------------------------------
# Dependencies
#
# The release branch is cut from origin/master, whose lockfiles may not be what
# is currently installed in this checkout. That matters asymmetrically:
#
#   Pods  — Xcode's `[CP] Check Pods Manifest.lock` phase compares Podfile.lock
#           against Pods/Manifest.lock and fails the build. Loud, but ~30s into
#           an archive rather than before it.
#   npm   — nothing checks. Metro bundles whatever is in node_modules, so a
#           stale tree ships a binary whose JS does not match the lockfile the
#           release tag points at, silently. This is the one that can actually
#           get a wrong build onto TestFlight.
# ---------------------------------------------------------------------------


def npm_install_reason() -> str | None:
    """How node_modules differs from the lockfile, or None if it matches.

    Reporting only — `run` reinstalls either way. This exists so `plan` can say
    whether the release is starting from a cold tree or a warm one, and so the
    log records what state the reinstall corrected.

    Compared against `node_modules/.package-lock.json` — npm's own record of what
    it installed — rather than a stamp file of our own, so it stays accurate when
    someone installs by hand.
    """
    node_modules = REPO_ROOT / "node_modules"
    hidden = node_modules / ".package-lock.json"
    lockfile = REPO_ROOT / "package-lock.json"

    if not node_modules.is_dir():
        return "node_modules/ is absent"
    if not hidden.is_file():
        return "node_modules/.package-lock.json is absent (installed state unknown)"
    if not lockfile.is_file():
        return None

    try:
        want = json.loads(lockfile.read_text()).get("packages", {})
        have = json.loads(hidden.read_text()).get("packages", {})
    except (json.JSONDecodeError, OSError) as e:
        return f"could not compare lockfiles ({e})"

    # Extras in the installed tree are fine; a package that is missing or at the
    # wrong version is not. Report the first few so the reason is actionable.
    drift = [
        path
        for path, spec in want.items()
        if path and spec.get("version") and have.get(path, {}).get("version") != spec["version"]
    ]
    if drift:
        shown = ", ".join(p.removeprefix("node_modules/") for p in drift[:4])
        return f"{len(drift)} package(s) differ from package-lock.json ({shown}…)"
    return None


def pods_install_reason() -> str | None:
    """How Pods/ differs from Podfile.lock, or None if it matches. This is the
    exact comparison Xcode's Check Pods Manifest.lock phase makes.

    Reporting only, like npm_install_reason — and note it cannot see a Pods/ that
    is corrupt but whose Manifest.lock still matches, which is one of the reasons
    the reinstall is unconditional rather than gated on this.
    """
    podfile_lock = IOS_DIR / "Podfile.lock"
    manifest = IOS_DIR / "Pods" / "Manifest.lock"

    if not (IOS_DIR / "Pods").is_dir():
        return "ios/Pods/ is absent"
    if not manifest.is_file():
        return "ios/Pods/Manifest.lock is absent"
    if not podfile_lock.is_file():
        return None
    if podfile_lock.read_bytes() != manifest.read_bytes():
        return "ios/Podfile.lock does not match ios/Pods/Manifest.lock"
    return None


def step_install_dependencies(s: State) -> None:
    """Reinstall node_modules and Pods from the release branch's lockfiles.

    Unconditional, and `npm ci` rather than `npm install`, deliberately. The two
    decisions go together: paying for a reinstall every release only buys
    something if the reinstall is exact.

      npm ci   wipes node_modules, installs precisely what package-lock.json
               says, refuses to run when package.json and the lock disagree, and
               never writes to the lock. `npm install` reconciles *toward* the
               lockfile, can amend it, and leaves extraneous packages in place —
               so running it every time costs the same and guarantees less.

    Measured at ~26s for this tree (1828 packages), which is noise against a
    20-45 minute archive.

    Runs after the branch checkout so the lockfiles are master's, and before
    set_version so the only tracked change afterwards is the version bump.
    """
    header("Install dependencies")
    log(f"npm:  {npm_install_reason() or 'in sync'} → reinstalling from the lockfile anyway")

    run(["npm", "ci", "--legacy-peer-deps"], capture=False)
    # npm ci cannot write the lockfile, so this asserts a guarantee rather than
    # checking a risk. Kept because a violation would mean npm did not behave as
    # documented, which is worth stopping a release over.
    _assert_lock_untouched(
        "package-lock.json",
        "npm ci is documented never to write the lockfile. Something else did.",
    )

    log(f"pods: {pods_install_reason() or 'in sync'} → reinstalling anyway")
    # CocoaPods 1.16.x aborts on a non-UTF-8 locale, which is the macOS default;
    # `npm run setup` sets the same variable for the same reason.
    #
    # No --repo-update: Podfile.lock pins exact versions, so the spec repo only
    # needs refreshing when a pin is newer than the local copy — and CocoaPods
    # says so explicitly when that happens. Paying for a network spec refresh on
    # every release to cover the rare case is the wrong trade.
    run(
        ["bundle", "exec", "pod", "install"],
        cwd=IOS_DIR,
        capture=False,
        env={"LC_ALL": "en_US.UTF-8", "LANG": "en_US.UTF-8"},
    )
    _assert_lock_untouched(
        "ios/Podfile.lock",
        "pod install changed Podfile.lock — the committed lockfile is not what "
        "this CocoaPods version resolves.\n"
        "  Land that change in its own PR first.",
    )
    # pod install rewrites project.pbxproj when pod integration changes, and that
    # is the same file set_version is about to edit. A diff here means the
    # committed project does not match what pod install produces — which
    # native-ios.md says to coordinate on, not to fold into a release.
    _assert_lock_untouched(
        PBXPROJ_REL,
        "pod install modified project.pbxproj. Spurious diffs here usually mean a "
        "different RN-CLI or CocoaPods version than whoever committed it last.\n"
        "  Coordinate before committing — see .claude/rules/native-ios.md.",
    )

    # Re-verify the bundle-phase Node, because both installs above WRITE
    # ios/.xcode.env.local, each with an *absolute* node path:
    #
    #   npm ci      postinstall runs scripts/write-xcode-env-local.js, which
    #               records process.execPath — the interpreter npm ran under,
    #               which is the PATH-pinned one by construction here. It
    #               overwrites unconditionally, so a stale pin is repaired.
    #   pod install RN's prepare_react_native_project! calls
    #               create_xcode_env_if_missing, which only writes the file when
    #               it is absent — so after npm ci it is a no-op:
    #
    #                 node_binary = `node --print "process.argv[0]"`.strip
    #                 system("echo 'export NODE_BINARY=#{node_binary}' > #{file_path}.local")
    #
    # Pre-flight ran before both, so it checked a state that no longer exists.
    # PATH is correct here by construction, so the written path should be correct
    # too — but "should be" is reasoning, and this is the last cheap moment to
    # make it a fact.
    problem = xcode_node_problem()
    if problem:
        raise ReleaseError(
            "After pod install, Xcode would not bundle with the pinned Node:\n"
            f"  {problem}"
        )


def _assert_lock_untouched(path: str, explanation: str) -> None:
    if not git("status", "--porcelain", "--", path):
        return
    raise ReleaseError(f"{path} changed during dependency install.\n  {explanation}")


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------


def preflight(s: State, *, needs_build: bool) -> list[str]:
    """Everything that can be checked in seconds, checked before the archive
    that takes 20-45 minutes. Returns blocking problems."""
    problems: list[str] = []

    blocked = phase_block_reason(s)
    if blocked:
        problems.append(blocked)

    if not shutil.which("gh"):
        problems.append("`gh` is not installed — needed for the PR and the merge.")
    elif run(["gh", "auth", "status"], check=False).returncode != 0:
        problems.append("`gh` is not authenticated. Run `gh auth login`.")

    # A `tagged-unreleased` resume only calls `gh release create`. It cuts no
    # branch and pushes no tag, so both guards below would be blocking a step
    # they cannot protect — and the tag one would block it by definition.
    if s.phase != "tagged-unreleased":
        dirty = git("status", "--porcelain")
        if dirty:
            problems.append(
                "Working tree is not clean. The release branch is cut from "
                f"{REMOTE}/{DEFAULT_BRANCH} in this checkout, and the fastlane lane refuses to "
                "build a tree that does not match a commit.\n"
                + "\n".join(f"      {line}" for line in dirty.splitlines()[:10])
            )

        if s.tag_remote or s.tag_local:
            where = "origin" if s.tag_remote else "locally"
            problems.append(f"Tag {s.tag} already exists {where}. Bump the build number.")

    if not needs_build:
        return problems

    # Resolved before the npm check: it is what puts the pinned Node's bin dir
    # (and therefore its npm) on PATH.
    node_problem, _ = resolve_node()
    if node_problem:
        problems.append(node_problem)
    else:
        # PATH is only half the story — the bundle phase can be pointed elsewhere
        # by .xcode.env.local. Checked after resolve_node so the probe runs with
        # the PATH the archive will actually inherit.
        xcode_problem = xcode_node_problem()
        if xcode_problem:
            problems.append(xcode_problem)
        xcode_warning = xcode_node_warning()
        if xcode_warning:
            s.warnings.append(xcode_warning)

    if not shutil.which("npm") and not NODE_BIN_DIR:
        problems.append("`npm` is not on PATH — needed to install the JS dependencies.")

    if run(["bundle", "exec", "fastlane", "--version"], cwd=IOS_DIR, check=False).returncode != 0:
        problems.append(
            "`bundle exec fastlane` does not run in ios/. Run `bundle install` in the repo root "
            "(gems must land in the Ruby named by .ruby-version)."
        )

    # An archive that gets 30 seconds in and dies on signing has still cost a
    # clean checkout and a bump commit, so check credentials up front.
    problems.extend(check_asc_partial())
    problems.extend(check_asc_token(s.warnings))
    problems.extend(check_signing(s.warnings))

    return problems


# ---------------------------------------------------------------------------
# Signing pre-flight
# ---------------------------------------------------------------------------


def distribution_identities() -> set[str]:
    """SHA-1 fingerprints of the `Apple Distribution` identities in the keychain.

    `security find-identity` prints that fingerprint, and it is the SHA-1 of the
    certificate's DER — the same value a provisioning profile's embedded certs
    hash to. So the two sides compare exactly, with no openssl round-trip.
    """
    identities = run(
        ["security", "find-identity", "-v", "-p", "codesigning"], check=False
    ).stdout
    return {
        sha
        for sha, name in re.findall(r'\)\s+([0-9A-F]{40})\s+"([^"]+)"', identities)
        if name.startswith("Apple Distribution")
    }


def installed_profiles() -> list[dict]:
    """Every installed provisioning profile, decoded."""
    paths: list[str] = []
    for directory in PROFILE_DIRS:
        paths.extend(sorted(glob.glob(str(directory / "*.mobileprovision"))))

    profiles = []
    for path in paths:
        # Read as bytes, not through `run` — plistlib wants the raw plist, and a
        # binary one would blow up on text-mode decoding. `security` needs none
        # of the PATH shaping `run` applies either.
        raw = subprocess.run(
            ["security", "cms", "-D", "-i", path], capture_output=True
        )
        if raw.returncode != 0:
            continue
        try:
            profiles.append(plistlib.loads(raw.stdout))
        except Exception:
            # A profile we cannot decode is one we cannot vouch for; skipping it
            # can only cost coverage, which surfaces as a blocking problem below.
            continue
    return profiles


def fastlane_env_value(name: str) -> str:
    """One value as the Fastfile will see it: real environment, then ios/fastlane/.env.

    Mirrors dotenv's precedence (a real env var wins) and its quote stripping, so a
    quoted empty value reads as unset here exactly as it does there.
    """
    from_env = os.environ.get(name, "").strip()
    if from_env:
        return from_env

    try:
        text = (IOS_DIR / "fastlane" / ".env").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == name:
            return value.strip().strip("'\"")
    return ""


ASC_VARS = (
    "APP_STORE_CONNECT_API_KEY",
    "APP_STORE_CONNECT_ISSUER_ID",
    "APP_STORE_CONNECT_KEY_ID",
)


def asc_route() -> tuple[str, list[str]]:
    """Which signing+upload route the lane will take, and what is missing.

    Mirrors `asc_configured?` in the Fastfile: all three App Store Connect values
    present means sign and upload through the API; anything missing means sign and
    upload through the Apple ID Xcode.app is signed into. Returns ("api", []) or
    ("xcode", [names that are unset]).
    """
    missing = [name for name in ASC_VARS if not fastlane_env_value(name)]
    return ("api", []) if not missing else ("xcode", missing)


def _es256_jwt(key_path: str, key_id: str, issuer_id: str) -> str:
    """An App Store Connect token, byte-for-byte the shape spaceship builds.

    Reproduced rather than imported: this script is stdlib-only, and the whole
    point is to mint what the lane will mint. See spaceship's
    connect_api/token.rb#refresh! -- an issuer id sets `iss`, and its absence sets
    `sub: "user"` instead, which is what makes the two key types incompatible.
    """
    def b64u(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    now = int(time.time())
    header = {"kid": key_id, "typ": "JWT", "alg": "ES256"}
    # iat is backdated by 60s exactly as spaceship does, so a host clock slightly
    # ahead of Apple's does not get a spurious rejection here that the lane would
    # not have hit.
    payload: dict = {"iat": now - 60, "exp": now + 600, "aud": "appstoreconnect-v1"}
    if issuer_id:
        payload["iss"] = issuer_id
    else:
        payload["sub"] = "user"

    signing_input = b64u(json.dumps(header).encode()) + b"." + b64u(json.dumps(payload).encode())
    signed = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", key_path],
        input=signing_input,
        capture_output=True,
    )
    if signed.returncode != 0:
        raise OSError(signed.stderr.decode(errors="replace")[:200])

    # openssl emits a DER SEQUENCE { r INTEGER, s INTEGER }; JWS wants the raw
    # 32-byte pair. Trailing garbage is impossible here, so a positional walk is
    # enough -- no ASN.1 library needed.
    der = signed.stdout
    if not der or der[0] != 0x30:
        raise OSError("openssl did not return a DER ECDSA signature")
    i = 2 if der[1] < 0x80 else 2 + (der[1] & 0x7F)
    raw = b""
    for _ in range(2):
        if der[i] != 0x02:
            raise OSError("malformed DER ECDSA signature")
        length = der[i + 1]
        # Strip DER's sign-padding zero, then left-pad to the fixed P-256 width.
        raw += der[i + 2 : i + 2 + length].lstrip(b"\x00").rjust(32, b"\x00")
        i += 2 + length

    return (signing_input + b"." + b64u(raw)).decode()


def check_asc_partial() -> list[str]:
    """Refuse a half-configured App Store Connect key.

    Absent credentials are not a problem — they select the Xcode route, which
    check_signing reports. A PARTIAL configuration is, because the lane refuses it
    outright rather than guessing which route was meant: xcode_asc_auth_args needs
    all three and would silently drop the API-key flags, so signing would succeed
    via Xcode and only the upload would break, 20-45 minutes in.

    Shared with release-feature-branch-ios, which runs the same lane and so must
    apply the same rule.
    """
    route, missing = asc_route()
    if route == "api" or len(missing) == len(ASC_VARS):
        return []
    present = [name for name in ASC_VARS if name not in missing]
    return [
        "App Store Connect API key is partially configured — the lane refuses this "
        "rather than guessing.\n"
        f"      set:   {', '.join(present)}\n"
        f"      unset: {', '.join(missing)}\n"
        f"      Fill all three in ios/fastlane/.env, or unset all three to "
        "sign and upload through Xcode.app's Apple ID."
    ]


def check_asc_token(warnings: list[str]) -> list[str]:
    """Authenticate to App Store Connect now, not 40 minutes from now.

    The two key types take mutually exclusive configurations and the mismatch is
    only ever reported as a bare 401:

      * a TEAM key needs an issuer id
      * an INDIVIDUAL key must not have one

    Getting it wrong the *upload* way is the expensive one. With no issuer id,
    xcode_asc_auth_args passes xcodebuild nothing, so signing quietly falls
    through to the Xcode account and succeeds -- archive and export both pass, and
    the 401 lands at upload_to_testflight, after the full 20-45 minute build. The
    signing pre-flight cannot catch it either: "no issuer id" is simultaneously the
    correct individual-key setup and the broken team-key one. Only asking Apple
    tells them apart.
    """
    route, _ = asc_route()
    if route != "api":
        # The Xcode route sends no token, so there is nothing to verify.
        return []

    key_path = fastlane_env_value("APP_STORE_CONNECT_API_KEY")
    key_id = fastlane_env_value("APP_STORE_CONNECT_KEY_ID")
    if os.environ.get("APP_STORE_CONNECT_API_KEY_BASE64"):
        # The CI shape, where the key is materialized by the lane at build time.
        return []

    key_path = os.path.expanduser(key_path)
    if not os.path.exists(key_path):
        return [
            f"App Store Connect key file not found: {key_path}\n"
            "      APP_STORE_CONNECT_API_KEY must be the path to the AuthKey_XXXXXXXXXX.p8."
        ]

    issuer_id = fastlane_env_value("APP_STORE_CONNECT_ISSUER_ID")

    try:
        token = _es256_jwt(key_path, key_id, issuer_id)
    except (OSError, ValueError, IndexError) as e:
        warnings.append(
            f"Could not mint an App Store Connect token to verify the key ({e}). "
            "Credentials are unverified; a bad key/issuer combination will surface as a "
            "401 at upload, after the archive."
        )
        return []

    request = urllib.request.Request(
        "https://api.appstoreconnect.apple.com/v1/apps?limit=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        urllib.request.urlopen(request, timeout=20).read(1)
        return []
    except urllib.error.HTTPError as e:
        if e.code != 401:
            # 403 and friends mean the token was accepted and the *scope* is narrow.
            # That may be fine for this lane, so it is not this check's call to make.
            warnings.append(
                f"App Store Connect returned HTTP {e.code} for the key check. The token "
                "authenticated, so the release can proceed, but the key's permissions may "
                "be narrower than the release needs."
            )
            return []
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        warnings.append(
            f"Could not reach App Store Connect to verify the key ({e}). Credentials are "
            "unverified; a bad key/issuer combination will surface as a 401 at upload."
        )
        return []

    return [
        "App Store Connect rejected the API key (401) — checked before the archive.\n"
        "      The key id, issuer id and .p8 in ios/fastlane/.env do not authenticate\n"
        "      together. Confirm all three came from the same key under Users and Access >\n"
        "      Integrations > App Store Connect API, and that it has not been revoked.\n"
        "      To release without a key at all, unset all three and sign through\n"
        "      Xcode.app's Apple ID instead."
    ]


def check_signing(warnings: list[str]) -> list[str]:
    """Assert the archive can actually be signed AND exported.

    A keychain identity alone is not enough, and checking only for one is what
    let the 3.20.0-1 release archive for ten minutes before dying at export:
    the host's certificate was in *none* of the installed profiles, so automatic
    signing had to mint a new profile — which an App Store Connect API key
    cannot do for cloud-managed assets at any role ("Cloud signing permission
    error"). Verifying cert-in-profile up front turns that into a 1-second fail.
    """
    route, missing = asc_route()
    if route != "api":
        # No API key, so xcodebuild authenticates as Xcode.app's Apple ID -- which is
        # also the only thing Apple permits to use its cloud-managed distribution
        # certificates. There may well be no certificate in this keychain at all, and
        # that is the normal case for this route rather than a fault, so requiring one
        # here would block exactly the people it exists for.
        warnings.append(
            "No App Store Connect API key ("
            + ", ".join(name.replace("APP_STORE_CONNECT_", "") for name in missing)
            + " unset), so signing and upload both go through the Apple ID Xcode.app is "
            "signed into. Make sure it is signed in to team 9C8CA87JCB. No local "
            "certificate is needed or checked on this route; the TestFlight upload also "
            "skips the build-processing wait, changelog and tester distribution."
        )
        return []

    dist = distribution_identities()
    if not dist:
        return [
            "No `Apple Distribution` identity in the keychain — the archive cannot be signed.\n"
            "      Import the Aura Health Inc. (9C8CA87JCB) distribution cert + private key\n"
            "      as a .p12 from someone who already holds one (Keychain Access > select the\n"
            "      cert AND its private key > Export > .p12; deliver via 1Password).\n"
            "      Do NOT expect Xcode to create one for you: most of this team has the\n"
            "      CLOUD_MANAGED_APP_DISTRIBUTION role, where Apple keeps the private key and\n"
            "      an Xcode archive+export leaves the keychain empty. fastlane authenticates\n"
            "      with an API key and Apple bars API keys from cloud signing, so the Xcode\n"
            "      path working says nothing about this lane. A new certificate is not an\n"
            "      option either — the team is at Apple's cap of 3.\n"
            "      See .claude/skills/release-ios/SKILL.md - Getting a distribution certificate."
        ]

    # A host with no profiles at all is the *worst* case, not an excuse to skip
    # the check — export needs a profile and there is none. It falls through to
    # the same blocking path as "profiles exist but none match", with an extra
    # line saying where we looked.
    profiles = installed_profiles()

    # bundle id -> soonest expiry among profiles that cover it with a local cert
    covered: dict[str, datetime] = {}
    for profile in profiles:
        app_id = profile.get("Entitlements", {}).get("application-identifier", "")
        bundle_id = app_id.split(".", 1)[1] if "." in app_id else ""
        if bundle_id not in ARCHIVE_BUNDLE_IDS:
            continue
        certs = profile.get("DeveloperCertificates") or []
        if not any(hashlib.sha1(bytes(c)).hexdigest().upper() in dist for c in certs):
            continue
        expiry = profile.get("ExpirationDate")
        if not isinstance(expiry, datetime):
            continue
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= datetime.now(timezone.utc):
            continue
        prev = covered.get(bundle_id)
        covered[bundle_id] = expiry if prev is None else min(prev, expiry)

    missing = [b for b in ARCHIVE_BUNDLE_IDS if b not in covered]
    if missing:
        listed = "\n".join(f"        - {b}" for b in missing)
        if not profiles:
            listed += "\n      No provisioning profiles were found at all. Searched:\n"
            listed += "\n".join(f"        {d}" for d in PROFILE_DIRS)
        return [
            "No installed provisioning profile covers these bundle IDs with a "
            "distribution certificate this Mac holds:\n"
            f"{listed}\n"
            "      The archive would succeed and `exportArchive` would then fail with\n"
            "      \"Cloud signing permission error / doesn't include signing certificate\".\n"
            "      An App Store Connect API key cannot mint cloud-managed signing assets at\n"
            "      any role, so automatic signing cannot repair this by itself. Either import\n"
            "      a .p12 for a cert the existing profiles already contain, or -- only if this\n"
            "      Apple ID holds a real (non cloud-managed) distribution cert -- archive+export\n"
            "      once from Xcode.app signed into an Apple ID with Admin on team 9C8CA87JCB\n"
            "      to regenerate the profiles around this cert.\n"
            "      See .claude/skills/release-ios/SKILL.md - Getting a distribution certificate."
        ]

    soonest_id = min(covered, key=lambda b: covered[b])
    soonest = covered[soonest_id]
    days = (soonest - datetime.now(timezone.utc)).days
    if days <= PROFILE_EXPIRY_WARN_DAYS:
        warnings.append(
            f"Provisioning profile for {soonest_id} expires in {days} day(s) "
            f"({soonest.date()}). Refresh it before it lapses mid-release."
        )
    return []


# ---------------------------------------------------------------------------
# Build failure diagnosis
# ---------------------------------------------------------------------------


def diagnose_build_failure(log_path: Path, returncode: int) -> str:
    """Name the command that actually failed.

    fastlane ends a failed run with ~60 lines of its own changelog and an
    "update fastlane" banner, so the real error scrolls hundreds of lines out of
    reach. Worse, the last "❌" line is almost always the benign RCTDeprecation
    diagnostic, which reads like the cause and is not.
    """
    try:
        lines = [
            ANSI_RE.sub("", ln).rstrip()
            for ln in log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        ]
    except OSError:
        return f"`fastlane` failed (exit {returncode}); its log was not readable."

    hits: list[str] = []
    for i, line in enumerate(lines):
        if BENIGN_DIAGNOSTIC in line:
            continue
        if not any(m in line for m in BUILD_FAILURE_MARKERS):
            continue
        hits.append(line.strip())
        # "The following build commands failed:" is a header — the command names
        # are on the lines under it, and they are the useful part.
        if "The following build commands failed:" in line:
            for follow in lines[i + 1 : i + 6]:
                text = follow.strip()
                if not text or text.startswith("("):
                    break
                hits.append(f"    {text}")

    seen, ordered = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            ordered.append(h)

    parts = [f"`bundle exec fastlane` failed (exit {returncode})."]
    if ordered:
        parts.append("\n  What actually failed:")
        parts.extend(f"    {h}" for h in ordered[:12])
    else:
        parts.append(
            "\n  No known failure marker in the log — read it directly; the cause is "
            "well above fastlane's changelog banner."
        )
    if any(BENIGN_DIAGNOSTIC in ln for ln in lines):
        parts.append(
            "\n  Ignore the `RCTDeprecation` fatal error in the log — clang emits it on "
            "every archive of this project and recovers. It is never the cause."
        )
    parts.append(f"\n  Full log: {log_path}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Release notes
# ---------------------------------------------------------------------------


def release_notes(s: State, upto: str) -> str:
    """Commits shipping in this release: everything on master since the last
    release tag. `upto` is the release commit, so its parent is the master tip
    the branch was cut from — excluding the bump keeps the notes to real work.

    `* %s (%h)` over `<from>...<to>` is release-it's default changelog, which is
    what `eng:release-version` renders for every backend repo. Same shape here on
    purpose: one recognisable release body across the fleet is worth more than a
    per-repo improvement on it.
    """
    # Resolved here rather than reusing `s.last_tag`, which is anchored on
    # master's tip and so names the newest release — right for a fresh release,
    # wrong for a back-fill of an older tag.
    since = latest_release_tag(exclude=s.tag, ancestor_of=f"{upto}^")
    rng = f"{since}...{upto}^" if since else f"{upto}^"
    entries = git(
        "log", rng, "--no-merges", "--pretty=format:* %s (%h)", check=False
    )
    return entries.strip() or "_No commits since the previous release._"


def release_body(s: State, merge_sha: str) -> str:
    """The GitHub release description.

    The notes range is anchored on the squash-merge commit rather than the bump
    commit the binary was archived from: the two have the same parent — the
    master tip the release branch was cut from, which `assert_tree_match` has
    already confirmed did not move — but the merge commit is on master and so
    still resolves once the release branch is deleted."""
    return f"""Version `{s.version}` (build `{s.build}`), archived from the `{s.env}` \
config and uploaded to TestFlight.

{release_notes(s, merge_sha)}
"""


def pr_body(s: State, built_sha: str) -> str:
    return f"""## Release {s.tag} ({s.env.upper()})

Version bump only — no product code. Cut by the `release-ios` skill.

**The binary is already built and uploaded.** `{s.tag}` was archived from
`{built_sha[:12]}` on this branch and uploaded to TestFlight before this PR was
opened, so a failed archive never lands a bump on `{DEFAULT_BRANCH}`. Merging
this PR is what makes the shipped version official; the skill then tags the
squash-merge commit after confirming its tree matches `{built_sha[:12]}`.

| | |
|---|---|
| Version | `{s.baseline_version} ({s.baseline_build})` → `{s.version} ({s.build})` |
| Tag | `{s.tag}` |
| Env | `{s.env}` |
| Built from | `{built_sha}` |

### Shipping in this release
{release_notes(s, built_sha)}

---
🤖 Generated with [Claude Code](https://claude.com/claude-code)
"""


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def step_branch(s: State) -> None:
    header(f"Branch {s.branch}")
    git("checkout", "-B", s.branch, f"{REMOTE}/{DEFAULT_BRANCH}")
    log(f"Branched from {REMOTE}/{DEFAULT_BRANCH} @ {s.master_sha[:12]}")


def step_bump(s: State) -> str:
    header(f"Bump {s.baseline_version} ({s.baseline_build}) → {s.version} ({s.build})")

    # set_version writes MARKETING_VERSION / CURRENT_PROJECT_VERSION across every
    # configuration of the target through the xcodeproj gem. Reusing it keeps one
    # writer for project.pbxproj rather than a second implementation here.
    run(
        ["bundle", "exec", "fastlane", "ios", "set_version",
         f"version:{s.version}", f"build:{s.build}"],
        cwd=IOS_DIR,
        capture=False,
    )

    if not git("status", "--porcelain"):
        raise ReleaseError(
            f"set_version wrote nothing — the project is already at {s.version} ({s.build}) "
            f"on {REMOTE}/{DEFAULT_BRANCH}. Pick a different version or build."
        )

    git("add", PBXPROJ_REL)
    git("commit", "-m", f"chore(ios): release {s.tag}\n\nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>")
    sha = git("rev-parse", "HEAD")
    log(f"Committed bump as {sha[:12]}")
    return sha


def step_build(s: State, allow_trial_fonts: bool = False) -> None:
    header(f"Archive and upload {s.tag} ({s.env.upper()})")
    log("fastlane switches the cask, archives Release, uploads to TestFlight,")
    log("then pushes Bugsnag dSYMs and the JS source map. 20-45 minutes.")
    if allow_trial_fonts:
        log("")
        log("OVERRIDE: --allow-trial-fonts — the asset-licensing gate is disabled for")
        log("this build. Trial-licensed faces are not licensed for distribution; this")
        log("ships only because their licensing was confirmed out of band.")
    # Recorded because it is otherwise invisible. Resolved the way the bundle
    # phase resolves it, NOT as `node --version` on this PATH — the two differ
    # whenever .xcode.env.local sets NODE_BINARY, and reporting the PATH version
    # there would put a false claim in the release log.
    binary = effective_node_binary()
    version = run([binary, "--version"], check=False).stdout.strip() if binary else "?"
    log(f"Bundling with {version} from {binary or 'unresolved NODE_BINARY'}")
    build_log = Path(tempfile.gettempdir()) / f"release-ios-{s.tag}.log"
    log(f"Log: {build_log}")
    log("")
    lane = ["bundle", "exec", "fastlane", "ios", f"release_{s.env}"]
    if allow_trial_fonts:
        lane.append("allow_trial_fonts:true")
    result = run(
        lane,
        cwd=IOS_DIR,
        capture=False,
        check=False,
        tee=build_log,
    )
    if result.returncode != 0:
        raise ReleaseError(diagnose_build_failure(build_log, result.returncode))
    log(f"\nUploaded {s.tag} to TestFlight.")


def step_push(s: State, sha: str) -> None:
    header(f"Push {s.branch}")
    # Pushed only after a successful upload, so the presence of the remote
    # branch is itself the record that the binary shipped. That is what makes
    # `status` able to tell "build succeeded, PR failed" from "never built".
    git("push", "--force-with-lease", REMOTE, f"{s.branch}:{s.branch}")
    log(f"Pushed {sha[:12]} to {REMOTE}/{s.branch}")


def step_open_pr(s: State, built_sha: str) -> dict:
    header("Open the version-bump PR")
    url = out([
        "gh", "pr", "create",
        "--base", DEFAULT_BRANCH,
        "--head", s.branch,
        "--title", f"chore(ios): release {s.tag}",
        "--body", pr_body(s, built_sha),
    ])
    log(url)
    pr = find_pr(s.branch)
    if not pr:
        raise ReleaseError(f"Created {url} but could not read it back with `gh pr list`.")
    return pr


def assert_no_drift(s: State, built_sha: str, allow_drift: bool) -> None:
    """The binary was archived from `built_sha`, whose parent was the master tip
    at branch time. If master has moved since, the squash-merge commit will carry
    a different tree than the one on TestFlight — so stop before merging, while
    the fix is still just "cut a new build"."""
    fetch()
    built_parent = git("rev-parse", f"{built_sha}^")
    master_tip = git("rev-parse", f"{REMOTE}/{DEFAULT_BRANCH}")
    if built_parent == master_tip:
        return

    behind = git("rev-list", "--count", f"{built_parent}..{master_tip}", check=False) or "?"
    msg = (
        f"{REMOTE}/{DEFAULT_BRANCH} has moved {behind} commit(s) since {s.tag} was built.\n"
        f"  built from : {built_sha[:12]} (parent {built_parent[:12]})\n"
        f"  master now : {master_tip[:12]}\n"
        "  Merging now would tag a tree that was never archived. Cut a new build "
        "(--level build) so the binary matches what lands."
    )
    if not allow_drift:
        raise ReleaseError(msg + "\n  Pass --allow-drift to merge and tag anyway.")
    log(f"WARNING (--allow-drift): {msg}")


def branch_requirements() -> tuple[set[str], int]:
    """(required status checks, required approving reviews) for the default branch.

    Read from the **rules** API, not `/branches/{b}/protection`. This repo gates
    master with a ruleset, and the protection endpoint reports it as having
    neither required checks nor required reviews — which is flatly wrong. Rules
    report `ci/circleci: test` and 0 approvals.
    """
    rules = gh_json("api", f"repos/{repo_slug()}/rules/branches/{DEFAULT_BRANCH}") or []
    contexts: set[str] = set()
    approvals = 0
    for rule in rules:
        params = rule.get("parameters") or {}
        if rule.get("type") == "required_status_checks":
            for check in params.get("required_status_checks") or []:
                if check.get("context"):
                    contexts.add(check["context"])
        elif rule.get("type") == "pull_request":
            approvals = max(approvals, params.get("required_approving_review_count") or 0)
    return contexts, approvals


def blocked_reason(number: int) -> str | None:
    """Why a BLOCKED PR cannot merge, or None if it is only waiting on a check.

    GitHub reports BLOCKED both for "a required check failed" and for "a required
    check has not finished", so the status alone cannot be acted on. Returning
    None means "transient — keep waiting"; the caller's deadline still bounds it.
    Unrecognised causes also return None deliberately: waiting out the deadline
    is cheap, and aborting a release whose binary is already on TestFlight is not.
    """
    required, approvals_needed = branch_requirements()
    pr = gh_json(
        "pr", "view", str(number),
        "--json", "statusCheckRollup,reviewDecision",
    ) or {}

    pending, failed = [], []
    for check in pr.get("statusCheckRollup") or []:
        # CheckRun carries name/status/conclusion; StatusContext (what CircleCI
        # posts) carries context/state. Normalise before comparing.
        name = check.get("name") or check.get("context") or ""
        if required and name not in required:
            continue
        state = (
            check.get("state")
            or check.get("conclusion")
            or check.get("status")
            or ""
        ).upper()
        if state in PENDING_CHECK_STATES or (
            check.get("status") == "IN_PROGRESS" and not check.get("conclusion")
        ):
            pending.append(name)
        elif state in {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED"}:
            failed.append(f"{name} ({state.lower()})")

    if failed:
        return "a required check failed: " + ", ".join(sorted(failed)) + "."
    if pending:
        return None
    if approvals_needed and pr.get("reviewDecision") != "APPROVED":
        return (
            f"it needs {approvals_needed} approving review(s) and has "
            f"{(pr.get('reviewDecision') or 'none').lower()}."
        )
    return None


def step_merge(s: State, pr: dict, allow_drift: bool) -> dict:
    header(f"Merge PR #{pr['number']}")

    deadline = time.time() + MERGE_WAIT_SECONDS
    while True:
        fresh = gh_json(
            "pr", "view", str(pr["number"]),
            "--json", "state,mergeStateStatus,mergeCommit,url,number,headRefOid",
        )
        state, merge_state = fresh["state"], fresh.get("mergeStateStatus")
        if state == "MERGED":
            return fresh
        if state == "CLOSED":
            raise ReleaseError(f"PR #{pr['number']} was closed without merging.")
        if merge_state in MERGEABLE_STATES:
            break
        if merge_state == "BLOCKED":
            reason = blocked_reason(pr["number"])
            if reason is not None:
                raise ReleaseError(
                    f"PR #{pr['number']} is BLOCKED — {reason} {fresh['url']}"
                )
            log("  BLOCKED — a required check is still running; waiting…")
        if time.time() > deadline:
            raise ReleaseError(
                f"PR #{pr['number']} was still {merge_state} after "
                f"{MERGE_WAIT_SECONDS // 60} minutes. {fresh['url']}"
            )
        log(f"  mergeStateStatus={merge_state} — waiting…")
        time.sleep(MERGE_POLL_SECONDS)

    assert_no_drift(s, s.built_commit or fresh["headRefOid"], allow_drift)

    # Squash is the only method this repo permits (allow_merge_commit and
    # allow_rebase_merge are both false), so there is nothing to detect.
    run(["gh", "pr", "merge", str(pr["number"]), "--squash", "--delete-branch"])
    log(f"Squash-merged #{pr['number']}")

    merged = gh_json(
        "pr", "view", str(pr["number"]), "--json", "state,mergeCommit,url,number,headRefOid"
    )
    if merged["state"] != "MERGED":
        raise ReleaseError(f"PR #{pr['number']} reports state={merged['state']} after merge.")
    return merged


def assert_tree_match(s: State, built_sha: str, merge_sha: str, allow_drift: bool) -> None:
    """The safety net for building before merging: the tag is only correct if the
    merge commit's tree is byte-identical to the one that was archived."""
    fetch()
    built_tree = git("rev-parse", f"{built_sha}^{{tree}}")
    merge_tree = git("rev-parse", f"{merge_sha}^{{tree}}")
    if built_tree == merge_tree:
        log(f"Tree check OK — {merge_sha[:12]} matches the archived {built_sha[:12]}")
        return

    msg = (
        f"Merge commit {merge_sha[:12]} carries a different tree than the archived "
        f"commit {built_sha[:12]}.\n"
        f"  built tree : {built_tree[:12]}\n"
        f"  merged tree: {merge_tree[:12]}\n"
        f"  The binary on TestFlight for {s.tag} is NOT what {s.tag} would point at."
    )
    if not allow_drift:
        raise ReleaseError(
            msg + "\n  Not tagging. Either cut a new build (--level build), or re-run "
            "with --allow-drift if you have confirmed the difference is immaterial."
        )
    log(f"WARNING (--allow-drift): {msg}")


def step_tag(s: State, merge_sha: str) -> None:
    header(f"Tag {s.tag}")
    git("fetch", REMOTE, DEFAULT_BRANCH)
    is_ancestor = run(
        ["git", "merge-base", "--is-ancestor", merge_sha, f"{REMOTE}/{DEFAULT_BRANCH}"],
        check=False,
    ).returncode == 0
    if not is_ancestor:
        raise ReleaseError(
            f"{merge_sha[:12]} is not an ancestor of {REMOTE}/{DEFAULT_BRANCH} — refusing to tag."
        )

    # Confirm the bump actually landed in the commit being tagged, rather than
    # trusting that the merge did what the PR said.
    landed_version, landed_build = read_version_at(merge_sha)
    if (landed_version, landed_build) != (s.version, s.build):
        raise ReleaseError(
            f"{merge_sha[:12]} reports {landed_version} ({landed_build}), expected "
            f"{s.version} ({s.build}). Not tagging."
        )

    git("tag", "-a", s.tag, merge_sha, "-m", f"Release {s.tag}")
    # Scoped push: a bare `--tags` would publish every stray local tag on this host.
    git("push", REMOTE, f"refs/tags/{s.tag}")
    log(f"Tagged {merge_sha[:12]} as {s.tag} and pushed")


def step_github_release(s: State, merge_sha: str) -> str | None:
    """Publish the tag as a GitHub release. Runs after the tag is pushed, so
    `--verify-tag` is a real guard: without it `gh` would happily create a tag of
    its own at the default branch's tip if the push had silently not landed."""
    header(f"GitHub release {s.tag}")
    if github_release_exists(s.tag):
        log("Release already exists — skipping")
        return None

    # Outside the repo, deliberately: a notes file left behind in REPO_ROOT by an
    # interrupted run would dirty the tree and block the next release's
    # clean-tree pre-flight.
    fd, notes_path = tempfile.mkstemp(prefix=f"release-notes-{s.tag}-", suffix=".md")
    notes = Path(notes_path)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(release_body(s, merge_sha))
    cmd = [
        "gh", "release", "create", s.tag,
        "--title", f"Release {s.tag}",
        "--verify-tag",
        "--notes-file", str(notes),
    ]
    # A dev build shares the tag namespace with prod. Marking it a prerelease is
    # what keeps "Latest release" pointing at the last thing users actually got.
    if s.env == "dev":
        cmd.append("--prerelease")

    # Same concern, different cause: back-filling a tag that a later release has
    # already superseded must not take "Latest" off it. gh's default here is
    # "automatic, by date and version" — which for an old version published today
    # is genuinely ambiguous, so say it outright rather than trust the heuristic.
    newest = latest_release_tag(exclude=s.tag)
    if newest and not is_ancestor(newest, merge_sha):
        cmd.append("--latest=false")
        log(f"Not marking latest — {newest} is newer")

    try:
        url = run(cmd).stdout.strip()
    finally:
        notes.unlink(missing_ok=True)
    log(f"Created GitHub release {s.tag}" + (f" — {url}" if url else ""))
    return url or None


def restore_branch(original: str, release_branch: str) -> None:
    """Put the checkout back where it started. If it started ON the release
    branch — a resume from the `bumped` phase — go to the default branch instead,
    since the release branch is about to be deleted."""
    target = DEFAULT_BRANCH if original in ("HEAD", "", release_branch) else original
    if run(["git", "checkout", target], check=False).returncode == 0:
        log(f"Returned to {target}")
    else:
        log(f"NOTE: could not check out {target}; the checkout is still on {release_branch}.")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

PHASE_NEXT = {
    "fresh": "bump → build → push → PR → merge → tag → release",
    "bumped": "build → push → PR → merge → tag → release (bump commit exists locally, not pushed)",
    "branch-pushed": "PR → merge → tag → release (binary already uploaded)",
    "pr-open": "merge → tag → release (binary already uploaded)",
    "merged-untagged": "tag → GitHub release",
    "tagged-unreleased": "GitHub release only (the tag is already on origin)",
    "pr-closed": "blocked — the PR was closed without merging",
    "released": "nothing — already released",
}


def cmd_plan(s: State, args) -> int:
    needs_build = s.phase in ("fresh", "bumped") and not args.skip_build
    problems = preflight(s, needs_build=needs_build)

    if args.json:
        print(json.dumps({**asdict(s), "problems": problems, "needsBuild": needs_build}, indent=2))
        return 2 if problems else 0

    log(f"Release plan — {repo_slug()}")
    log("")
    log(f"  env            {s.env}")
    log(f"  version        {s.baseline_version} ({s.baseline_build})  →  {s.version} ({s.build})")
    log(f"  level          {s.level or 'explicit'}")
    log(f"  tag            {s.tag}")
    log(f"  branch         {s.branch}")
    log(f"  base           {REMOTE}/{DEFAULT_BRANCH} @ {s.master_sha[:12]}")
    log(f"  last release   {s.last_tag or '(none)'}")
    log(f"  phase          {s.phase}")
    log(f"  next           {PHASE_NEXT[s.phase]}")

    if s.phase == "released":
        log("")
        log(f"  {s.tag} is already tagged on {REMOTE} and published. Nothing to do.")
        return 0

    if needs_build:
        _, node_status = resolve_node()
        log("")
        log("  Toolchain:")
        log(f"     node   {node_status}")
        if not args.skip_install:
            log(f"     npm    {npm_install_reason() or 'in sync'}")
            log(f"     pods   {pods_install_reason() or 'in sync'}")
            log("     → npm ci + pod install run regardless, so the archive builds")
            log("       from exactly what the lockfiles describe (~1-3 min)")

    log("")
    log("  Steps `run` would take:")
    steps = []
    if s.phase == "fresh":
        steps.append(f"branch {s.branch} off {REMOTE}/{DEFAULT_BRANCH}")
    if needs_build and not args.skip_install:
        steps.append("npm ci + pod install from the branch's lockfiles")
    if s.phase == "fresh":
        steps.append("set_version, commit the bump")
    if needs_build:
        lane_note = " allow_trial_fonts:true" if args.allow_trial_fonts else ""
        steps.append(
            f"bundle exec fastlane ios release_{s.env}{lane_note}"
            "  (archive + TestFlight + Bugsnag)"
        )
    if s.phase in ("fresh", "bumped"):
        steps.append(f"push {s.branch} — only after the upload succeeds")
    if s.phase in ("fresh", "bumped", "branch-pushed"):
        steps.append("gh pr create against master")
    if s.phase in ("fresh", "bumped", "branch-pushed", "pr-open"):
        steps.append("wait mergeable, check master has not moved, squash-merge")
    if not s.tag_remote:
        steps.append("verify the merge commit's tree matches the archived one, then tag and push")
    if args.github_release and not s.github_release:
        steps.append(f"gh release create {s.tag}")
    # Numbered here rather than inline, so a skipped step doesn't leave a gap.
    for n, step in enumerate(steps, 1):
        log(f"     {n}. {step}")
    if not steps:
        log("     (none — this release has nothing left to do)")

    if s.warnings:
        log("")
        for w in s.warnings:
            log(f"  WARNING  {w}")

    log("")
    if problems:
        log("  BLOCKED:")
        for p in problems:
            log(f"    - {p}")
        return 2

    log("  Pre-flight OK.")
    log("")
    log(f"  {plan_consequence(s, args)}")
    return 0


def plan_consequence(s: State, args) -> str:
    """The one sentence the operator reads before saying yes.

    Built from the phase rather than fixed, because a resume must not claim
    things it will not do — a publish-only resume that announces a TestFlight
    upload and a merge to master is asking for approval to the wrong question.
    """
    acts = []
    if s.phase in ("fresh", "bumped") and not args.skip_build:
        acts.append(f"uploads {s.tag} to TestFlight")
    if s.phase in ("fresh", "bumped", "branch-pushed", "pr-open"):
        acts.append(f"merges a version bump to {DEFAULT_BRANCH}")
    if not s.tag_remote:
        acts.append(f"tags {s.tag}")
    if args.github_release and not s.github_release:
        acts.append(f"publishes the {s.tag} GitHub release")
    if not acts:
        return "Nothing to do."
    joined = acts[0] if len(acts) == 1 else ", ".join(acts[:-1]) + " and " + acts[-1]
    return f"This {joined} on {repo_slug()}."


def cmd_status(s: State, args) -> int:
    if args.json:
        print(json.dumps(asdict(s), indent=2))
        return 0
    log(f"{s.tag} — phase: {s.phase}")
    log(f"  next            {PHASE_NEXT[s.phase]}")
    log(f"  local branch    {s.local_branch_sha[:12] if s.local_branch_sha else '—'}")
    log(f"  remote branch   {s.remote_branch_sha[:12] if s.remote_branch_sha else '—'}")
    log(f"  PR              " + (f"#{s.pr['number']} {s.pr['state']} {s.pr['url']}" if s.pr else "—"))
    log(f"  tag             local={'yes' if s.tag_local else 'no'} "
        f"remote={'yes' if s.tag_remote else 'no'}")
    log(f"  gh release      {'yes' if s.github_release else 'no'}")
    log(f"  master version  {s.baseline_version} ({s.baseline_build})")
    for w in s.warnings:
        log(f"  WARNING  {w}")
    return 0


def cmd_run(s: State, args) -> int:
    if s.phase == "released":
        log(f"{s.tag} is already released. Nothing to do.")
        return 0
    blocked = phase_block_reason(s)
    if blocked:
        raise ReleaseError(blocked)

    if s.phase == "tagged-unreleased":
        return cmd_run_release_only(s, args)

    original_branch = git("rev-parse", "--abbrev-ref", "HEAD")
    needs_build = s.phase in ("fresh", "bumped") and not args.skip_build

    problems = preflight(s, needs_build=needs_build)
    if problems:
        raise ReleaseError("Pre-flight failed:\n" + "\n".join(f"  - {p}" for p in problems))
    # Only `plan` and `status` used to show these, so a warning raised during a
    # run — a profile days from expiry, say — was gathered and never seen.
    for warning in s.warnings:
        log(f"WARNING: {warning}")

    built_sha = s.built_commit

    if s.phase == "fresh":
        step_branch(s)
        # Before the bump, so the only tracked change left afterwards is the
        # version — which is what lets the lockfile guards above be unambiguous.
        if needs_build and not args.skip_install:
            step_install_dependencies(s)
        built_sha = step_bump(s)
    elif s.phase == "bumped":
        git("checkout", s.branch)
        built_sha = s.local_branch_sha
        log(f"Resuming from the existing bump commit {built_sha[:12]} on {s.branch}")
        if needs_build and not args.skip_install:
            step_install_dependencies(s)

    if needs_build:
        step_build(s, args.allow_trial_fonts)
    elif s.phase in ("fresh", "bumped"):
        log("Skipping the build (--skip-build) — assuming the binary is already on TestFlight.")

    if s.phase in ("fresh", "bumped"):
        step_push(s, built_sha)

    pr = s.pr
    if pr is None:
        pr = step_open_pr(s, built_sha)

    if pr["state"] != "MERGED":
        pr = step_merge(s, pr, args.allow_drift)

    merge_sha = (pr.get("mergeCommit") or {}).get("oid")
    if not merge_sha:
        raise ReleaseError(
            f"PR #{pr['number']} is merged but GitHub reports no merge commit. "
            "Re-run once GitHub catches up."
        )

    if built_sha:
        assert_tree_match(s, built_sha, merge_sha, args.allow_drift)
    else:
        # Resuming a release whose branch was already deleted by the merge, on a
        # host that never had it. The archived commit is gone, so the tree check
        # cannot run — say so rather than letting the tag imply it passed.
        log(
            f"WARNING: the archived commit for {s.tag} is no longer available in this "
            "checkout, so the built-vs-merged tree check was SKIPPED. step_tag still "
            "confirms the merged commit declares the right version."
        )

    if not s.tag_remote:
        step_tag(s, merge_sha)

    # Before the release, not after: publishing is the one remaining step that
    # talks to a network, and a failure there should leave the checkout tidy and
    # the phase resumable into `gh release create` alone.
    restore_branch(original_branch, s.branch)
    run(["git", "branch", "-D", s.branch], check=False)

    release_url = step_github_release(s, merge_sha) if args.github_release else None

    header("Released")
    log(f"  tag      {s.tag}  →  {merge_sha[:12]}")
    log(f"  env      {s.env}")
    log(f"  built    {built_sha[:12] if built_sha else '(not built by this run)'}")
    log(f"  PR       {pr.get('url', '')}")
    if release_url:
        log(f"  release  {release_url}")
    log("")
    log("  The build is on TestFlight — confirm it finished processing in App Store Connect.")
    return 0


def cmd_run_release_only(s: State, args) -> int:
    """Resume a release whose tag is on origin but which has no GitHub release —
    either the publish step failed, or the tag predates this being a step at all.
    Nothing here touches git; the tag decides which commit is described."""
    if not args.github_release:
        log(f"{s.tag} is tagged on {REMOTE} but has no GitHub release, and "
            "--no-github-release was passed. Nothing to do.")
        return 0

    problems = preflight(s, needs_build=False)
    if problems:
        raise ReleaseError("Pre-flight failed:\n" + "\n".join(f"  - {p}" for p in problems))

    merge_sha = rev_or_none(f"refs/tags/{s.tag}^{{}}")
    if not merge_sha:
        raise ReleaseError(
            f"{s.tag} is on {REMOTE} but does not resolve in this checkout. "
            f"Run `git fetch --tags {REMOTE}` and retry."
        )

    release_url = step_github_release(s, merge_sha)

    header("Released")
    log(f"  tag      {s.tag}  →  {merge_sha[:12]}")
    log(f"  env      {s.env}")
    if release_url:
        log(f"  release  {release_url}")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Cut an iOS release of the Aura app.")
    parser.add_argument("command", choices=["plan", "run", "status"])
    parser.add_argument("--env", choices=["dev", "prod"], required=True)
    parser.add_argument(
        "--level",
        choices=["major", "minor", "patch", "build"],
        help="Bump level. major/minor/patch reset the build to 1; build keeps the version.",
    )
    parser.add_argument("--version", help="Explicit X.Y.Z, overriding --level.")
    parser.add_argument("--build", help="Explicit build number.")
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Do not archive. Use only when the binary is already on TestFlight.",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Do not run npm ci / pod install. Risks bundling JS from a dependency "
             "tree the release tag does not describe.",
    )
    parser.add_argument(
        "--allow-trial-fonts",
        action="store_true",
        help="Pass allow_trial_fonts:true to the lane, disabling the asset-licensing "
             "gate. Trial-licensed faces are not licensed for distribution — use only "
             "when their licensing has been confirmed out of band.",
    )
    parser.add_argument(
        "--allow-drift",
        action="store_true",
        help="Merge and tag even when master moved after the build, or the merged tree "
             "differs from the archived one. Tags a commit that was never built.",
    )
    parser.add_argument(
        "--github-release",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Publish the tag as a GitHub release after pushing it (default: on). "
             "--no-github-release cuts the tag without one.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if not args.level and not args.version:
        parser.error("give --level (major|minor|patch|build) or --version X.Y.Z")

    global JSON_MODE, ALLOW_NODE_INSTALL
    JSON_MODE = args.json
    ALLOW_NODE_INSTALL = args.command == "run"

    try:
        state = derive_state(args.env, args.level, args.version, args.build)
        return {"plan": cmd_plan, "run": cmd_run, "status": cmd_status}[args.command](state, args)
    except ReleaseError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted. Re-run the same command to resume.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
