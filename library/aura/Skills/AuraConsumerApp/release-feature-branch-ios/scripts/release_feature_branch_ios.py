#!/usr/bin/env python3
"""Build the current branch and upload it to TestFlight. No git history work.

Sibling to `release-ios`, which is the only way a *release* is cut: from main,
through a PR, tagged, published. This one exists for the other case — getting a
feature branch in front of QA — where every one of those steps is wrong. A tag
on unmerged work claims a release that never happened, and it lands in the same
namespace the real releases use.

So the shapes differ on purpose:

                        release-ios                  release-feature-branch-ios
  builds                origin/main                  your local branch, as it is
  version baseline      origin/main                  the branch itself
  after upload          PR -> merge -> tag -> release   one commit on the branch
  plan mode             yes                          no

The pre-flight, signing, config and diagnosis helpers are imported from
release_ios.py rather than copied: they are the same problems here, and a second
copy would drift from the original silently.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import shutil
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
            "  This skill reuses its pre-flight, signing and diagnosis helpers."
        )
    spec = importlib.util.spec_from_file_location("release_ios", SIBLING)
    mod = importlib.util.module_from_spec(spec)
    # Registered before exec: @dataclass resolves annotations through
    # sys.modules[cls.__module__], which is absent for a module loaded by path.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


R = _load_sibling()

FASTLANE_DIR = R.FASTLANE_DIR
PROJECT_YML_REL = R.PROJECT_YML_REL
DEFAULT_BRANCH = R.DEFAULT_BRANCH
REMOTE = R.REMOTE

# `make proj` runs at the head of the build and resolves Swift packages, which
# can rewrite Package.resolved. The .xcodeproj is gitignored but this one file
# inside it is tracked (`!**/Package.resolved`), so a resolve shows up as a diff.
# It describes what this binary was built from, so it belongs in the bump commit
# rather than stranded beside it.
PACKAGE_RESOLVED_REL = (
    "AuraConsumerApp.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved"
)
BUMP_PATHS = (PROJECT_YML_REL, PACKAGE_RESOLVED_REL)


class BuildError(Exception):
    pass


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------


def current_version() -> tuple[str, str]:
    """The version the working tree declares.

    Delegated so the every-target assertion comes with it: project.yml carries
    MARKETING_VERSION and CURRENT_PROJECT_VERSION for AuraConsumerApp and each
    extension (notification service, Home Screen widget), and App Store Connect
    rejects an upload whose app and extensions disagree.
    """
    return R.read_version_at(None)


def main_version() -> tuple[str, str] | None:
    try:
        return R.read_version_at(f"{REMOTE}/{DEFAULT_BRANCH}")
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


def tag_forms(version: str, build: str) -> tuple[str, str]:
    """Both tag shapes this repo carries.

    `release-ios` cuts the bare `X.Y.Z-N`; every tag before it carries a `v`
    prefix, and there are 158 of them. Either one means that version+build has
    already shipped, and App Store Connect rejects a re-used build number no
    matter what the tag is called.
    """
    return (f"{version}-{build}", f"v{version}-{build}")


def existing_tags(version: str, build: str, warnings: list[str]) -> list[str]:
    """Where this version+build is already tagged, local and on origin.

    Origin is asked directly with `ls-remote` rather than trusted from the local
    tag list, which can be arbitrarily stale on a feature branch nobody has
    fetched on in weeks. If origin cannot be reached that is a warning, not a
    block — the network is not a release prerequisite, and the upload itself will
    reject a duplicate build number anyway.
    """
    found: list[str] = []
    remote_reachable = True
    for tag in tag_forms(version, build):
        if R.git("tag", "-l", tag).strip():
            found.append(f"{tag} (local)")
            continue
        if not remote_reachable:
            continue
        try:
            if R.remote_tag_exists(tag):
                found.append(f"{tag} (on {REMOTE})")
        except Exception:
            remote_reachable = False
            warnings.append(
                f"Could not reach {REMOTE} to check for an existing tag — only the local "
                "tag list was consulted. A build number already used on App Store Connect "
                "is rejected at the upload, after the archive."
            )
    return found


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------


def preflight(env: str, target: tuple[str, str], *, allow_main: bool) -> None:
    """Everything checkable in seconds, before an archive that takes tens of
    minutes."""
    problems: list[str] = []
    warnings: list[str] = []

    branch = R.git("rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        problems.append("Detached HEAD — check out the branch you want to build.")
    elif branch == DEFAULT_BRANCH and not allow_main:
        problems.append(
            f"On {DEFAULT_BRANCH}. A build from {DEFAULT_BRANCH} belongs to the `release-ios`\n"
            "      skill, which lands the bump through a PR, tags the merge commit and\n"
            "      publishes a GitHub release. Use that instead, or pass --allow-main if you\n"
            f"      really want an untagged, unrecorded build off {DEFAULT_BRANCH}."
        )

    # The build writes project.yml (the lane bumps the version itself) and the
    # commit afterwards picks that up. Pre-existing changes would be swept into
    # that commit, and the failure path — restoring project.yml and
    # Package.resolved — would clobber a genuine local edit to either.
    dirty = R.git("status", "--porcelain")
    if dirty:
        problems.append(
            "Working tree is not clean. The build writes project.yml and the commit "
            "afterwards\n"
            "      picks it up, so these would be swept into that commit — and the "
            "failure path\n"
            "      restores project.yml, which would discard an edit of your own:\n"
            + "\n".join(f"        {line}" for line in dirty.splitlines()[:10])
        )

    # The .xcodeproj is generated from project.yml and gitignored, so `make proj`
    # runs at the head of the build. Without xcodegen it fails inside the lane.
    if not shutil.which("xcodegen"):
        problems.append(
            "`xcodegen` is not on PATH. The .xcodeproj is generated from project.yml, so "
            "`make proj`\n"
            "      runs at the start of every build. Install it with `make install-xcodegen`."
        )

    if (
        R.run(["bundle", "exec", "fastlane", "--version"], cwd=FASTLANE_DIR, check=False).returncode
        != 0
    ):
        problems.append(
            "`bundle exec fastlane` does not run in fastlane/. Run `bundle install` there —\n"
            "      gems must land in the Ruby named by .ruby-version."
        )

    # `make env-<env> release` is step 0 of the lane and aborts if either source
    # is missing, which would be a failure well into the run. Both destinations
    # it writes are gitignored, so the swap cannot dirty the tree.
    problems.extend(R.check_env_config(env, warnings))

    # Signing and upload route, decided exactly as release-ios and the Fastfile's
    # asc_configured? decide it: all three App Store Connect values, or none.
    # This runs the same lanes, so it must apply the same rule — and on the API
    # route both the token and the certificate are verified, because an archive
    # that dies at export has still cost tens of minutes.
    # In a linked worktree the gitignored fastlane/.env is absent, which flips
    # that fork to the Xcode route without anyone choosing it — and the failure
    # lands at the upload, after the archive. Settled before the route-specific
    # checks below, which only make sense once the route is right.
    worktree_env = R.check_worktree_asc_env(warnings)
    problems.extend(worktree_env)
    problems.extend(R.check_asc_partial())
    problems.extend(R.check_asc_token(warnings))
    problems.extend(R.check_signing(warnings, announce_xcode_route=not worktree_env))

    tagged = existing_tags(target[0], target[1], warnings)
    if tagged:
        problems.append(
            f"Already tagged as {', '.join(tagged)} — that version+build shipped as a "
            "release.\n"
            "      App Store Connect rejects a re-used build number; pick another with "
            "--build or --version."
        )

    for warning in warnings:
        R.log(f"  WARNING: {warning}")

    if problems:
        raise BuildError("Pre-flight failed:\n" + "\n".join(f"    - {p}" for p in problems))


def warn_version_below_main(target_version: str) -> None:
    """A branch's version can trail main's, which App Store Connect rejects.

    Not fatal: a branch may legitimately be on its own version line, and giving a
    long-running branch its own line rather than a build bump on main's version
    also keeps its Bugsnag crash reports from merging with the shipped release's.
    But it is the mistake this workflow invites, because the baseline is the
    branch rather than main, so it is worth saying out loud.
    """
    mv = main_version()
    if not mv:
        return
    if version_sort_key(target_version) < version_sort_key(mv[0]):
        R.log("")
        R.log(
            f"  WARNING: {target_version} is below {DEFAULT_BRANCH}'s {mv[0]}. App Store "
            "Connect rejects\n"
            "           a build whose version trails what already shipped."
        )


# ---------------------------------------------------------------------------
# Branch handling
# ---------------------------------------------------------------------------


def checkout_local(branch: str) -> None:
    """Switch to a LOCAL branch — never a remote-tracking one.

    The whole point of this skill is building what is on your machine, so a name
    that only resolves as `origin/<branch>` is refused rather than silently
    checked out as a detached head of the pushed tip.
    """
    if R.git("rev-parse", "--abbrev-ref", "HEAD") == branch:
        return
    # Existence before cleanliness: a typo'd branch name should say so, not send
    # you off tidying a working tree for a checkout that was never going to work.
    exists = (
        subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=REPO_ROOT
        ).returncode
        == 0
    )
    if not exists:
        remote_only = (
            subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", f"refs/remotes/{REMOTE}/{branch}"],
                cwd=REPO_ROOT,
            ).returncode
            == 0
        )
        hint = (
            f"\n  It exists as {REMOTE}/{branch}. This skill builds local branches;"
            f"\n  `git checkout -b {branch} {REMOTE}/{branch}` first if that is what you want."
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
    # The checkout itself can be what failed — a typo'd name, or a tree that went
    # dirty between the two calls. Nothing moved, so there is nothing to restore,
    # and the dirty-tree branch below would otherwise announce it was "leaving"
    # you on the branch you never left.
    if R.git("rev-parse", "--abbrev-ref", "HEAD") == started_on:
        return
    if R.git("status", "--porcelain"):
        R.log(f"\nLeaving you on {R.git('rev-parse', '--abbrev-ref', 'HEAD')} — the tree is not")
        R.log(f"clean, so switching back to {started_on} would fail.")
        return
    R.git("checkout", started_on)
    R.log(f"\nRestored {started_on}.")


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def default_changelog(branch: str, sha: str, env: str, version: str, build: str) -> str:
    """What QA sees as "What to Test".

    The lane falls back to CHANGELOG.md, which never has an entry for a branch
    version, so without this every branch build arrives with a blank changelog
    and nothing distinguishing it from the others in the list.

    Colons are avoided deliberately: fastlane parses each argv token as
    `key:value`, so a colon inside the value is asking for trouble.
    """
    return (
        f"Branch build {version} ({build}) - {env} - {branch} @ {sha[:12]}. "
        "Unmerged work, not a release - no tag, no GitHub release."
    )


def step_build(env: str, version: str, build: str, changelog: str) -> None:
    """Archive and upload — before anything is committed.

    Unlike Aura's equivalent, the bump is NOT committed first. That ordering
    exists there only because its Fastfile calls `ensure_git_status_clean`, which
    will not run against a dirty tree; this Fastfile has no such guard, and the
    `release` lane bumps the version itself as its step 1. So the build runs
    first and a failure costs nothing — no commit is ever created, and the tree
    is restored to what it was.
    """
    R.header(f"Archive and upload {version} ({build}) ({env.upper()})")
    R.log(f"fastlane switches the build configuration to {env}, writes the version into")
    R.log("project.yml, regenerates the project, archives Release, uploads to TestFlight,")
    R.log("then pushes the Bugsnag dSYM.")
    route, _ = R.asc_route()
    R.log(f"Signing and upload route: {route}")
    if route != "api":
        R.log("  Note: the Xcode route skips the changelog, so TestFlight shows no What to Test.")
    log_path = Path(tempfile.gettempdir()) / f"release-feature-branch-ios-{version}-{build}.log"
    R.log(f"Log: {log_path}")
    R.log("")

    cmd = [
        "bundle", "exec", "fastlane", "release",
        f"version:{version}",
        f"build:{build}",
        f"changelog:{changelog}",
        # The lane prompts interactively otherwise, which would hang a
        # backgrounded build forever.
        "skip_confirmation:true",
        "skip_waiting:true",
    ]
    if env == "dev":
        cmd.append("dev:true")

    result = R.run(cmd, cwd=FASTLANE_DIR, capture=False, check=False, tee=log_path)
    if result.returncode != 0:
        raise BuildError(R.diagnose_build_failure(log_path, result.returncode))


def step_commit(version: str, build: str) -> str | None:
    """Record what shipped, after it has shipped.

    Anything the build left outside project.yml / Package.resolved is reported
    and left in the tree rather than raising: the binary is already on
    TestFlight, so failing here would strand a successful upload with no record
    of it. `release-ios` does raise in the equivalent spot, and correctly — there
    the check runs *before* the archive, where stopping costs nothing.
    """
    R.header(f"Commit the bump {version} ({build})")

    changed = R.git("status", "--porcelain")
    if not changed:
        R.log(f"Nothing to commit — the branch already declared {version} ({build}).")
        R.log("The build shipped; there is simply no bump to record.")
        return None

    # R.porcelain_path, not a fixed slice: `git()` strips the payload, so the
    # first line loses the leading space an unstaged change carries and a
    # `line[3:]` slice would eat a character off exactly that one path — warning
    # that project.yml is "not in the commit" moments before committing it.
    unexpected = [
        line for line in changed.splitlines() if R.porcelain_path(line) not in BUMP_PATHS
    ]

    staged: list[str] = []
    for path in BUMP_PATHS:
        if R.git("status", "--porcelain", "--", path):
            R.git("add", path)
            staged.append(path)

    if not staged:
        R.log("Nothing to commit — the build changed no version-bearing file.")
        if unexpected:
            R.log("  It did leave these, which are not this skill's to commit:")
            for line in unexpected[:10]:
                R.log(f"    {line}")
        return None

    if unexpected:
        R.log("  WARNING: the build also left these, which are NOT in the commit:")
        for line in unexpected[:10]:
            R.log(f"           {line}")

    for path in staged:
        R.log(f"  including {path}")
    R.git(
        "commit", "-m",
        f"chore(release): branch build {version}-{build}\n\n"
        "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>",
    )
    sha = R.git("rev-parse", "HEAD")
    R.log(f"Committed {sha[:12]}")
    return sha


def restore_tree(reason: str) -> None:
    """Undo what the build wrote, so the branch is exactly as it was.

    Cheaper and safer than Aura's `git reset --hard`: nothing was committed, so
    there is no commit to unwind — only the lane's writes to restore.

    Silent when there is nothing to restore, which is what lets the interrupt
    path call it unconditionally: after a successful commit the tree is already
    clean and this must not print as though it undid something.
    """
    if not R.git("status", "--porcelain"):
        return
    R.log("")
    R.log(f"Restoring the working tree — {reason}, so nothing is committed.")
    for path in BUMP_PATHS:
        if R.git("status", "--porcelain", "--", path):
            R.run(["git", "checkout", "--", path], check=False)
    leftover = R.git("status", "--porcelain")
    if leftover:
        R.log("  The build left changes outside project.yml / Package.resolved:")
        for line in leftover.splitlines()[:10]:
            R.log(f"    {line}")


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
        "--changelog",
        help="TestFlight 'What to Test' text. Defaults to branch + commit + env. "
        "Ignored on the Xcode signing route, which uploads without one.",
    )
    p.add_argument(
        "--allow-main",
        action="store_true",
        help=f"Permit building from {DEFAULT_BRANCH}. Prefer the release-ios skill.",
    )
    args = p.parse_args()

    started_on: str | None = None
    try:
        # The checkout precedes everything, because the version baseline is read
        # from the working tree. It is also the one mutation that happens before
        # the build, so the original branch is remembered and restored if
        # anything downstream refuses — a failed pre-flight must not leave you
        # standing on a branch you did not ask to be on.
        if args.branch:
            started_on = R.git("rev-parse", "--abbrev-ref", "HEAD")
            checkout_local(args.branch)
            if started_on == args.branch:
                started_on = None

        # Best-effort: the trails-main warning reads origin/main's project.yml,
        # which on a branch nobody has fetched on can be months old. Failing here
        # would make the network a prerequisite for a purely local build.
        R.run(["git", "fetch", "--quiet", REMOTE, DEFAULT_BRANCH], check=False)

        version, build = resolve_target(args.level, args.version, args.build)
        preflight(args.env, (version, build), allow_main=args.allow_main)

        branch = R.git("rev-parse", "--abbrev-ref", "HEAD")
        sha = R.git("rev-parse", "HEAD")
        cur_v, cur_b = current_version()
        changelog = args.changelog or default_changelog(branch, sha, args.env, version, build)

        R.log(f"Branch build — {branch}")
        R.log("")
        R.log(f"  env        {args.env}")
        R.log(f"  version    {cur_v} ({cur_b})  ->  {version} ({build})")
        R.log(f"  commit     {sha[:12]}")
        R.log("  upload     TestFlight, via fastlane")
        R.log(f"  after      one commit on {branch}. No tag, no PR, no GitHub release.")
        warn_version_below_main(version)
        R.log("")

        try:
            step_build(args.env, version, build, changelog)
        except BuildError:
            restore_tree("the upload did not succeed")
            raise

        step_commit(version, build)

        R.header("Uploaded")
        R.log(f"  {version} ({build}) -> TestFlight, archived against the {args.env} config")
        R.log("")
        R.log(f"  branch   {branch} — bump commit kept locally, nothing pushed")
        R.log("  Push it yourself if the team needs the bump on the remote:")
        R.log(f"    git push {REMOTE} {branch}")
        R.log("")
        R.log("  App Store Connect still has to finish processing before the build is")
        R.log("  installable — check TestFlight rather than assuming it is ready.")
        return 0

    except (BuildError, R.ReleaseError) as e:
        restore_branch(started_on)
        print(f"\nERROR: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        # The lane writes the version into project.yml as its step 1, long before
        # the archive finishes — so an interrupt mid-build leaves an unshipped
        # bump in the tree, which both breaks the "a non-successful run leaves
        # the branch untouched" guarantee and blocks the --branch restore below,
        # since that refuses to switch away from a dirty tree.
        restore_tree("interrupted")
        restore_branch(started_on)
        print("\nInterrupted.", file=sys.stderr)
        print(
            "  If the upload had already completed when you interrupted, the bump is no\n"
            "  longer recorded — check TestFlight, and re-run with the explicit\n"
            "  --version/--build if the build is there.",
            file=sys.stderr,
        )
        return 130


if __name__ == "__main__":
    sys.exit(main())
