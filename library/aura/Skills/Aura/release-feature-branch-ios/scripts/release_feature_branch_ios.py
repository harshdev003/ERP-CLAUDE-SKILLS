#!/usr/bin/env python3
"""Build the current branch and upload it to TestFlight. No git history work.

Sibling to `release-ios`, which is the only way a *release* is cut: from master,
through a PR, tagged, published. This one exists for the other case — getting a
feature branch in front of QA — where every one of those steps is wrong. A tag
on unmerged work claims a release that never happened.

So the shapes differ on purpose:

                        release-ios              release-feature-branch-ios
  builds                origin/master            your local branch, as it is
  version baseline      origin/master            the branch itself
  after upload          PR → merge → tag → release   one commit on the branch
  plan mode             yes                      no

The heavy lifting is imported from release_ios.py rather than copied: node
resolution, the npm/pod reinstall, and the build-failure diagnosis are the same
problems here, and a second copy would drift from the original silently.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SIBLING = REPO_ROOT / ".claude" / "skills" / "release-ios" / "scripts" / "release_ios.py"


def _load_sibling():
    """Import release_ios.py by path — it is not on sys.path and has no package."""
    if not SIBLING.exists():
        sys.exit(
            f"ERROR: {SIBLING.relative_to(REPO_ROOT)} is missing.\n"
            "  This skill reuses its node/dependency/diagnosis helpers."
        )
    spec = importlib.util.spec_from_file_location("release_ios", SIBLING)
    mod = importlib.util.module_from_spec(spec)
    # Registered before exec: @dataclass resolves annotations through
    # sys.modules[cls.__module__], which is absent for a module loaded by path.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


R = _load_sibling()

IOS_DIR = R.IOS_DIR
PBXPROJ = REPO_ROOT / R.PBXPROJ_REL
DEFAULT_BRANCH = R.DEFAULT_BRANCH


class BuildError(Exception):
    pass


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------


def current_version() -> tuple[str, str]:
    """The version the working tree declares, via the sibling's xcodeproj read."""
    return R.read_version_at(None)


def master_version() -> tuple[str, str] | None:
    try:
        return R.read_version_at(f"{R.REMOTE}/{DEFAULT_BRANCH}")
    except Exception:
        return None


def version_sort_key(v: str) -> tuple:
    return tuple(int(p) for p in re.findall(r"\d+", v)[:3])


def resolve_target(level: str | None, version: str | None, build: str | None) -> tuple[str, str]:
    cur_v, cur_b = current_version()
    if version:
        return version, (build or "1")
    if level:
        v, b = R.next_version(cur_v, cur_b, level)
        return v, (build or b)
    if build:
        return cur_v, build
    raise BuildError("give --level (major|minor|patch|build), --version X.Y.Z, or --build N")


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------


def preflight(
    env: str, target: tuple[str, str], *, allow_master: bool
) -> None:
    problems: list[str] = []

    branch = R.git("rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        problems.append("Detached HEAD — check out the branch you want to build.")
    elif branch == DEFAULT_BRANCH and not allow_master:
        problems.append(
            f"On {DEFAULT_BRANCH}. A release from {DEFAULT_BRANCH} belongs to the `release-ios`\n"
            "      skill, which tags it and publishes a GitHub release. Use that instead, or\n"
            "      pass --allow-master if you really want an untagged build off master."
        )

    # The bump is committed before the archive (fastlane's ensure_git_status_clean
    # demands a clean tree), so anything already uncommitted would be swept into
    # that commit. Refuse rather than silently widen it.
    dirty = R.git("status", "--porcelain")
    if dirty:
        listed = "\n".join(f"        {line}" for line in dirty.splitlines()[:10])
        problems.append(
            "Working tree is not clean. The version bump is committed before the archive,\n"
            "      so these would be swept into that commit:\n" + listed
        )

    if run_ok(["npm", "--version"]) is False:
        problems.append("`npm` is not on PATH — needed to install the JS dependencies.")

    if (
        subprocess.run(
            ["bundle", "exec", "fastlane", "--version"],
            cwd=IOS_DIR,
            capture_output=True,
        ).returncode
        != 0
    ):
        problems.append(
            "`bundle exec fastlane` does not run in ios/. Run `bundle install` in the repo\n"
            "      root — gems must land in the Ruby named by .ruby-version."
        )

    # Signing and upload route, decided exactly as release-ios and the Fastfile's
    # asc_configured? decide it: all three App Store Connect values, or none. This
    # runs the same `deploy` lane, so it must apply the same rule.
    #
    # The two checks this replaced were both wrong once the lane gained the Xcode
    # route:
    #
    #   * "No App Store Connect credentials" treated absent credentials as a
    #     missing prerequisite and told you to fill in .env. Absence is now a
    #     ROUTE — sign and upload as the Apple ID Xcode.app is signed into — and
    #     blocking it locked out precisely the cloud-managed accounts that cannot
    #     hold a certificate at all. It also only fired when ios/fastlane/.env was
    #     absent, so a .env with everything commented out sailed past it.
    #   * The keychain check ran only under the old --skip-upload flag, i.e. it
    #     demanded a certificate when NOT uploading and never checked when
    #     uploading — the archive needs signing either way. Its advice ("one Xcode
    #     archive with automatic signing creates it") is also false for a
    #     cloud-managed account: Apple keeps the private key and the keychain stays
    #     empty. (That flag is gone; the no-key route replaces it.)
    #
    # check_signing now covers both: it requires a cert-in-profile on the api route
    # and requires nothing on the xcode route, warning what that route skips.
    warnings: list[str] = []
    problems.extend(R.check_asc_partial())
    problems.extend(R.check_asc_token(warnings))
    problems.extend(R.check_signing(warnings))

    tag = f"{target[0]}-{target[1]}"
    if R.git("tag", "-l", tag).strip():
        problems.append(
            f"Tag {tag} exists — that version+build already shipped. App Store Connect\n"
            "      rejects a re-used build number; pick another with --build or --version."
        )

    for warning in warnings:
        print(f"  WARNING: {warning}")

    if problems:
        raise BuildError(
            "Pre-flight failed:\n" + "\n".join(f"    - {p}" for p in problems)
        )


def checkout_local(branch: str) -> None:
    """Switch to a LOCAL branch — never a remote-tracking one.

    The whole point of this skill is building what is on your machine, so a name
    that only resolves as `origin/<branch>` is refused rather than silently
    checked out as a detached head of the pushed tip — which is exactly the
    surprise release-ios delivers when local commits are not pushed.
    """
    if R.git("rev-parse", "--abbrev-ref", "HEAD") == branch:
        return
    # Existence before cleanliness: a typo'd branch name should say so, not send
    # you off tidying a working tree for a checkout that was never going to work.
    exists = (
        subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=REPO_ROOT,
        ).returncode
        == 0
    )
    if not exists:
        remote_only = (
            subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", f"refs/remotes/{R.REMOTE}/{branch}"],
                cwd=REPO_ROOT,
            ).returncode
            == 0
        )
        hint = (
            f"\n  It exists as {R.REMOTE}/{branch}. This skill builds local branches;"
            f"\n  `git checkout -b {branch} {R.REMOTE}/{branch}` first if that is what you want."
            if remote_only
            else ""
        )
        raise BuildError(f"No local branch {branch!r}.{hint}")
    if R.git("status", "--porcelain"):
        raise BuildError(
            f"Working tree is not clean — cannot switch to {branch}. Commit or stash first."
        )
    R.git("checkout", branch)
    R.log(f"Checked out {branch}")


def restore_branch(started_on: str | None) -> None:
    """Put the checkout back where --branch found it, on any early exit."""
    if not started_on:
        return
    if R.git("status", "--porcelain"):
        R.log(f"\nLeaving you on {R.git('rev-parse', '--abbrev-ref', 'HEAD')} — the tree")
        R.log(f"is not clean, so switching back to {started_on} would fail.")
        return
    R.git("checkout", started_on)
    R.log(f"\nRestored {started_on}.")


def run_ok(cmd: list[str]) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def warn_version_below_master(target_version: str) -> None:
    """A branch's version can trail master, which App Store Connect will reject.

    Not fatal: the branch may legitimately be on its own line. But it is the
    mistake this workflow invites, because the baseline is the branch rather
    than master, so it is worth saying out loud.
    """
    mv = master_version()
    if not mv:
        return
    if version_sort_key(target_version) < version_sort_key(mv[0]):
        R.log("")
        R.log(
            f"  WARNING: {target_version} is below {DEFAULT_BRANCH}'s {mv[0]}. App Store Connect\n"
            f"           rejects a build whose version trails what already shipped."
        )


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def step_install() -> None:
    R.header("Install dependencies")
    R.log(f"npm:  {R.npm_install_reason() or 'in sync'} → reinstalling from the lockfile anyway")
    R.run(["npm", "ci", "--legacy-peer-deps"], capture=False)
    R.log(f"pods: {R.pods_install_reason() or 'in sync'} → reinstalling anyway")
    R.run(
        ["bundle", "exec", "pod", "install"],
        cwd=IOS_DIR,
        capture=False,
        env={**os.environ, "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"},
    )


def step_bump(version: str, build: str) -> str:
    """Set the version, then commit it — before the archive, not after.

    fastlane's `ensure_git_status_clean` means the bump cannot sit uncommitted
    while the build runs. So it is committed first and rolled back if the build
    fails, which lands at the same place as "commit after a successful upload"
    without fighting the lane.
    """
    R.header(f"Set version {version} ({build})")
    R.run(
        ["bundle", "exec", "fastlane", "ios", "set_version", f"version:{version}", f"build:{build}"],
        cwd=IOS_DIR,
        capture=False,
    )
    if not R.git("status", "--porcelain"):
        raise BuildError(
            f"set_version wrote nothing — the project already declares {version} ({build})."
        )
    R.git("add", R.PBXPROJ_REL)
    # Lockfiles too: `pod install` above can regenerate Podfile.lock with
    # locally-resolved SPEC CHECKSUMS, and fastlane's ensure_git_status_clean
    # refuses the archive over it. They describe what this binary was built
    # from, so they belong in the same commit rather than stranded beside it.
    for lock in ("ios/Podfile.lock", "package-lock.json"):
        if R.git("status", "--porcelain", "--", lock):
            R.git("add", lock)
            R.log(f"  including {lock} — regenerated by the install step")
    R.git("commit", "-m", f"chore(ios): {version}-{build}")

    leftover = R.git("status", "--porcelain")
    if leftover:
        raise BuildError(
            "Working tree still dirty after the bump commit — fastlane will refuse "
            "the archive:\n" + "\n".join(f"      {x}" for x in leftover.splitlines()[:10])
        )
    sha = R.git("rev-parse", "HEAD")
    R.log(f"Committed {sha[:12]}")
    return sha


def step_build(env: str, allow_trial_fonts: bool) -> None:
    R.header("Build and upload to TestFlight")
    cmd = ["bundle", "exec", "fastlane", "ios", f"release_{env}"]
    if allow_trial_fonts:
        cmd.append("allow_trial_fonts:true")
    log_path = Path(tempfile.gettempdir()) / "release-feature-branch-ios.log"
    result = R.run(cmd, cwd=IOS_DIR, capture=False, check=False, tee=log_path)
    if result.returncode != 0:
        raise BuildError(R.diagnose_build_failure(log_path, result.returncode))


def rollback(sha_before: str) -> None:
    """Undo the bump commit so a failed build leaves the branch as it was."""
    R.log("")
    R.log(f"Rolling the bump commit back to {sha_before[:12]} — the upload did not succeed.")
    R.git("reset", "--hard", sha_before)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(
        description="Build the current branch and upload it to TestFlight (no tag, no PR).",
    )
    p.add_argument("--env", choices=["dev", "prod"], required=True)
    p.add_argument(
        "--branch",
        help="Local branch to build. Checked out first; must already exist locally. "
        "Defaults to whatever is currently checked out. This is a LOCAL ref — "
        "unlike release-ios, nothing is read from origin.",
    )
    p.add_argument(
        "--level",
        choices=["major", "minor", "patch", "build"],
        help="Bump level relative to what this branch declares.",
    )
    p.add_argument("--version", help="Explicit X.Y.Z, overriding --level.")
    p.add_argument("--build", help="Explicit build number.")
    p.add_argument(
        "--allow-trial-fonts",
        action="store_true",
        help="Pass allow_trial_fonts:true to fastlane. Trial fonts are not licensed for "
        "distribution — internal builds only.",
    )
    p.add_argument("--skip-install", action="store_true", help="Skip npm ci / pod install.")
    p.add_argument(
        "--allow-master",
        action="store_true",
        help=f"Permit building from {DEFAULT_BRANCH}. Prefer the release-ios skill.",
    )
    args = p.parse_args()

    R.ALLOW_NODE_INSTALL = True

    started_on: str | None = None
    try:
        # The checkout has to precede everything, because the version baseline is
        # read from the working tree. But it is also the one mutation that happens
        # before the build, so the original branch is remembered and restored if
        # anything downstream refuses — a failed pre-flight must not leave you
        # standing on a branch you did not ask to be on.
        if args.branch:
            started_on = R.git("rev-parse", "--abbrev-ref", "HEAD")
            checkout_local(args.branch)
            if started_on == args.branch:
                started_on = None

        version, build = resolve_target(args.level, args.version, args.build)
        preflight(
            args.env,
            (version, build),
            allow_master=args.allow_master,
        )

        branch = R.git("rev-parse", "--abbrev-ref", "HEAD")
        cur_v, cur_b = current_version()

        R.log(f"Branch build — {branch}")
        R.log("")
        R.log(f"  env        {args.env}")
        R.log(f"  version    {cur_v} ({cur_b})  →  {version} ({build})")
        R.log(f"  commit     {R.git('rev-parse', 'HEAD')[:12]}")
        R.log("  upload     TestFlight, via fastlane")
        R.log(f"  after      one commit on {branch}. No tag, no PR, no GitHub release.")
        warn_version_below_master(version)
        R.log("")

        node_bin, node_note = R.resolve_node()
        R.log(f"  node       {node_note}")

        if not args.skip_install:
            step_install()

        sha_before = R.git("rev-parse", "HEAD")
        step_bump(version, build)
        try:
            step_build(args.env, args.allow_trial_fonts)
        except BuildError:
            rollback(sha_before)
            raise

        R.header("Uploaded")
        R.log(f"  {version} ({build}) → TestFlight, archived from the {args.env} cask")
        R.log("")
        R.log(f"  branch   {branch} — bump commit kept, nothing pushed")
        R.log("  Push it yourself if the team needs the bump on the remote:")
        R.log(f"    git push origin {branch}")
        return 0

    except BuildError as e:
        restore_branch(started_on)
        print(f"\nERROR: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        restore_branch(started_on)
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
