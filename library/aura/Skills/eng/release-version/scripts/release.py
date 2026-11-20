#!/usr/bin/env python3
"""Release an Aura repo through a pull request.

    plan    resolve the repo and print exactly what a release would do. No writes.
    run     execute: bump -> branch -> PR -> merge -> tag -> GitHub release.
    status  report where a partially-completed release currently stands.
    verify  check the CI pipeline for a tag, then the Spinnaker rollout it triggers.

Why a PR and not a push: the default branch requires a pull request with no
bypass actors, so a PR is the only route onto it. This is the flow the branch
hardening was designed to require, not a workaround for it.

Three ordering rules the flow depends on:

  1. Tag AFTER the merge. CI builds the image from the tagged commit and
     services report their own version from the file bumped in it, so a tag
     placed before the merge deploys a wrong version. `run` verifies the bump
     is actually present in the commit it is about to tag.
  2. Tag the merge commit by SHA. A squash merge creates a NEW commit, so the
     branch head is not what landed on the default branch.
  3. Push the branch and the tags as separate steps. One
     `git push --follow-tags` is not atomic: the tag can land while the branch
     is rejected, which starts a pipeline for a tag that is then deleted on
     rollback, and CI dies on a vanished ref. That produces a red "test job
     failed" alert for what is really one blocked push.

State is derived from the repo and GitHub, never from a local file, so a run
interrupted anywhere resumes correctly and a second run on a finished release
is a no-op.

Stdlib only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
import os
import pathlib
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import notes as notes_mod  # noqa: E402
import rollout as rollout_mod  # noqa: E402
from repo_shape import (  # noqa: E402
    SHAPE_LERNA,
    Package,
    RepoShape,
    ShapeError,
    detect,
)


def _ua_who() -> str:
    """Login name of whoever runs this, for the user agent: AINATIVE_UA_WHO, else the global git email's local part, git user.name, the OS account, or "unknown"."""
    who = re.sub(r"^\d+\+", "", os.environ.get("AINATIVE_UA_WHO", "").strip().partition("@")[0])
    if not who:
        if not hasattr(_ua_who, "found"):
            import getpass, subprocess
            found = ""
            for key in ("user.email", "user.name"):
                try:
                    val = subprocess.run(["git", "config", "--global", key], capture_output=True, encoding="utf-8", errors="replace", timeout=5).stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    val = ""
                found = re.sub(r"^\d+\+", "", val.partition("@")[0]) if key == "user.email" else val
                if found:
                    break
            if not found:
                try:
                    found = getpass.getuser()
                except Exception:
                    found = ""
            _ua_who.found = found
        who = _ua_who.found
    return re.sub(r"[^A-Za-z0-9._-]+", "-", who).strip("-.") or "unknown"


def user_agent() -> str:
    """`AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)` -- labels this skill's calls to RTDB, GCP and the Aura API.

    Same format as gcloud_sa.user_agent(). A label, not proof: anyone can send it.
    """
    skill = pathlib.Path(__file__).resolve().parents[1]
    root = skill.parents[1]
    try:
        plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError):
        plugin = root.name
    try:
        front = (skill / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        version = re.search(r'version:\s*"([^"]+)"', front).group(1)
    except (OSError, IndexError, AttributeError):
        version = "unknown"
    session = os.environ.get("AINATIVE_SESSION_NAME") or (os.environ.get("CLAUDE_CODE_SESSION_ID") or "")[:8]
    session = re.sub(r"[^A-Za-z0-9._-]+", "-", session).strip("-") or "none"
    return f"AuraClaude/{_ua_who()}/{plugin}:{skill.name}@{version} (session {session})"


COMMIT_SUBJECT = "Release {version}"
RELEASE_TITLE = "Release {version}"


class ReleaseError(RuntimeError):
    pass


class NothingToRelease(ReleaseError):
    """No package changed since its last tag.

    Distinct from ReleaseError because the two must not look alike: in a fleet
    sweep, a repo with nothing to release is a normal outcome, while a repo that
    failed to release needs attention. Collapsing them into one exit code lets a
    real failure read as "nothing to do". Exit 3, versus 2 for a genuine error.
    """


def sh(args: list[str], cwd: Path | None = None, check: bool = True) -> str:
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and p.returncode:
        raise ReleaseError(f"{' '.join(args)} failed:\n{p.stderr.strip() or p.stdout.strip()}")
    return p.stdout.strip()


def gh_json(args: list[str], cwd: Path | None = None) -> object:
    out = sh(["gh"] + args, cwd=cwd)
    return json.loads(out) if out else None


# ---------------------------------------------------------------- planning


@dataclass
class PackagePlan:
    pkg: Package
    current: str
    proposed: str
    level: str
    reason: str
    last_tag: str | None
    body: str
    changed: bool = True

    @property
    def tag(self) -> str:
        return self.pkg.tag_for(self.proposed)


@dataclass
class Plan:
    shape: RepoShape
    packages: list[PackagePlan]
    branch: str
    commit_subject: str
    warnings: list[str] = field(default_factory=list)
    # The default-branch commit this plan was computed at: the range a go approves.
    head: str = ""

    @property
    def releasing(self) -> list[PackagePlan]:
        return [p for p in self.packages if p.changed]

    @property
    def is_resume(self) -> bool:
        """Is every releasing package finishing a version that already exists?

        A resume completes an earlier release: the version is already on the default
        branch and already tagged, and what is missing is a later step (usually the
        GitHub release). It creates no new version, so it ships **none of the caller's
        changes** — whatever they wanted released is still on the default branch,
        unreleased. That makes it the opposite outcome from a release in the only way
        that matters, so it carries its own status and its own report (ENG-1081).

        A mixed plan — some packages resuming, others cutting new versions — is not a
        resume: real work is shipping, so `released` is the honest word for it.

        Note `all()` over an empty list is True, so an empty `releasing` has to be
        excluded explicitly or a plan releasing nothing reads as a resume.
        """
        rel = self.releasing
        return bool(rel) and all(p.level == "resume" for p in rel)

    @property
    def primary(self) -> PackagePlan:
        return self.releasing[0]

    @property
    def label(self) -> str:
        """Version label for the branch / commit / release title."""
        rel = self.releasing
        if len(rel) == 1:
            return rel[0].proposed
        return ", ".join(f"{p.pkg.name}@{p.proposed}" for p in rel)


def _version_at_ref(shape: RepoShape, pkg: Package, ref: str) -> str | None:
    """The version recorded in this package's version file *at a ref*.

    The release baseline has to come from the ref being released, not from the
    working tree. A stale or feature-branch checkout otherwise yields a baseline
    that is behind (or ahead of) what actually landed, and the bump computed from
    it can silently downgrade past a version already on the default branch.
    """
    for f in pkg.version_files:
        rel = f.relative_to(shape.root)
        blob = sh(["git", "show", f"{ref}:{rel}"], cwd=shape.root, check=False)
        if not blob:
            continue
        if f.name == "package.json":
            try:
                v = json.loads(blob).get("version")
            except json.JSONDecodeError:
                v = None
            if v:
                return v
        elif f.name == "pyproject.toml":
            m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', blob)
            if m:
                return m.group(1)
    return None


def _tag_exists(shape: RepoShape, tag: str) -> bool:
    return bool(sh(["git", "tag", "--list", tag], cwd=shape.root, check=False).strip())


_LIST_LIMIT = 1000
# slug -> set of tags with a release, or None when the listing is not trustworthy
# (the call failed, or it saturated the limit so older releases may be missing).
_RELEASED_TAGS: dict[str, set[str] | None] = {}


def _released_tags(shape: RepoShape) -> set[str] | None:
    """Tags with a GitHub release, or None when that cannot be established.

    None is NOT "no releases" — the distinction is load-bearing. Treating a failed
    or truncated listing as an empty set makes the current version look unreleased,
    which reads as an in-flight release to finish rather than a new one to cut, and
    the run then reports already-released while quietly skipping commits that
    needed shipping. A transient API error must never silently swallow a release.
    """
    if shape.slug not in _RELEASED_TAGS:
        p = subprocess.run(
            ["gh", "release", "list", "--repo", shape.slug, "--limit", str(_LIST_LIMIT),
             "--json", "tagName"],
            cwd=shape.root, capture_output=True, text=True,
        )
        if p.returncode:
            _RELEASED_TAGS[shape.slug] = None
        else:
            try:
                rows = json.loads(p.stdout or "[]")
            except json.JSONDecodeError:
                rows = None
            # Saturating the limit means older releases may be missing, so the set
            # cannot answer "absent" — only "present".
            _RELEASED_TAGS[shape.slug] = (
                None if rows is None or len(rows) >= _LIST_LIMIT
                else {r["tagName"] for r in rows}
            )
    return _RELEASED_TAGS[shape.slug]


def _release_exists(shape: RepoShape, tag: str) -> bool:
    """Does this tag have a GitHub release? Fails closed rather than guessing.

    Prefers the cached listing (one call for a whole lerna repo) and falls back to
    a per-tag lookup when the listing is untrustworthy. A lookup error that is not
    a plain 404 raises, because the alternative is a wrong answer that silently
    skips or repeats a release.
    """
    listed = _released_tags(shape)
    if listed is not None:
        return tag in listed
    p = subprocess.run(
        ["gh", "release", "view", tag, "--repo", shape.slug, "--json", "tagName"],
        cwd=shape.root, capture_output=True, text=True,
    )
    if p.returncode == 0:
        return True
    err = (p.stderr or "").lower()
    if "not found" in err or "404" in err:
        return False
    raise ReleaseError(
        f"cannot determine whether {tag} has a GitHub release on {shape.slug}: "
        f"{(p.stderr or p.stdout).strip()}. Refusing to guess — guessing either "
        f"skips a release or repeats one."
    )


def _fully_released(shape: RepoShape, tag: str) -> bool:
    """A version is done only when its tag exists AND it has a GitHub release.

    One rule, every repo. A tag with no release is a half-finished release that
    stays resumable, never something to bump past.

    There is deliberately no "this repo does not use GitHub releases" exemption.
    An empty release listing cannot distinguish a repo that never used releases
    (historical `lerna version`, which only tags) from a first release interrupted
    before `gh release create`. Special-casing either reading breaks the other, and
    trying to infer it produced a bug in both directions. Requiring both artifacts
    everywhere removes the ambiguity and matches `State.done`.

    The cost is bounded and visible: the first run on a repo carrying tags without
    releases proposes finishing those versions (plan level `resume`), creating the
    missing GitHub release without touching code. It self-heals — every run after
    that bumps normally — and the proposal passes through the approval gate like
    any other.
    """
    return _tag_exists(shape, tag) and _release_exists(shape, tag)


def _package_changed(shape: RepoShape, pkg: Package, last: str | None, to_ref: str) -> bool:
    """Has this package changed since its own last tag, on the ref being released?

    Mirrors lerna's `ignoreChanges: ["*.md", ".git*"]` — a docs-only touch is
    not a release. Without a previous tag the package has never shipped, so it
    counts as changed.

    Diffed against `to_ref`, not local HEAD: on a feature branch or a stale
    checkout, HEAD would mark packages changed that did not move on the default
    branch, and miss ones that did.
    """
    if last is None:
        return True
    rel = pkg.directory.relative_to(shape.root)
    files = sh(["git", "diff", "--name-only", f"{last}..{to_ref}", "--", str(rel)],
               cwd=shape.root, check=False).splitlines()
    for f in files:
        base = Path(f).name
        if base.endswith(".md") or base.startswith(".git"):
            continue
        return True
    return False


def build_plan(
    shape: RepoShape,
    *,
    level: str | None = None,
    explicit_version: str | None = None,
    pre_release: str | None = None,
    force: bool = False,
    only: list[str] | None = None,
) -> Plan:
    """Compute the release without touching anything. `only` names the packages to
    release (lerna: `--package`); every other package is left out of the plan's work,
    however much it changed, so a go for one package tags that package alone."""
    to_ref = f"origin/{shape.default_branch}"
    warnings: list[str] = []
    plans: list[PackagePlan] = []
    if only:
        unknown = sorted(set(only) - {p.name for p in shape.packages})
        if unknown:
            raise ReleaseError(f"--package {', '.join(unknown)}: no such package in {shape.root} "
                               f"(packages: {', '.join(p.name for p in shape.packages)})")

    # Plan against the remote's default branch, not the local checkout — a stale
    # local branch would compute notes over the wrong range and propose a version
    # from an old baseline. Fetch once, then fail with something actionable.
    sh(["git", "fetch", "origin", shape.default_branch, "--tags", "--quiet"],
       cwd=shape.root, check=False)
    if subprocess.run(["git", "rev-parse", "--verify", "--quiet", to_ref],
                      cwd=shape.root, capture_output=True).returncode:
        raise ReleaseError(
            f"{to_ref} does not resolve in {shape.root}. Fetch it first "
            f"(`git -C {shape.root} fetch origin {shape.default_branch}`)."
        )

    for pkg in shape.packages:
        last = notes_mod.last_tag(shape.root, pkg.tag_prefix, ref=to_ref)
        if only and pkg.name not in only:
            if shape.is_multi_package and _package_changed(shape, pkg, last, to_ref):
                warnings.append(f"{pkg.name}: changed since {last or 'the beginning'} but not "
                                f"selected with --package — not released, not tagged")
            plans.append(PackagePlan(pkg=pkg, current=pkg.version, proposed=pkg.version,
                                     level="skip", reason="not selected (--package)",
                                     last_tag=last, body="", changed=False))
            continue
        commits = notes_mod.read_commits(shape.root, last, to_ref)
        inferred, reason = notes_mod.infer_level(commits)

        # Baseline from the ref being released, not the working tree. Falling back
        # to the local value only when the package does not exist on that ref yet
        # (a package added locally and never shipped).
        at_ref = _version_at_ref(shape, pkg, to_ref)
        current = at_ref or pkg.version
        if at_ref and at_ref != pkg.version:
            warnings.append(
                f"{pkg.name}: local checkout says {pkg.version} but {to_ref} says "
                f"{at_ref} — planning from {at_ref}. Your checkout is out of date."
            )

        # An in-flight release: the ref carries a version that is not fully
        # released. Target THAT version rather than bumping on top of it —
        # otherwise the merged release never gets its tag, never deploys, and a
        # re-run silently starts the next version instead of finishing this one.
        #
        # "Not fully released" deliberately covers BOTH a missing tag and a tag
        # whose GitHub release is missing. Keying on the tag alone made the
        # documented tags-pushed-but-release-missing resume unreachable: once the
        # tag existed the range was empty, so this raised NothingToRelease before
        # the state could even be read, reporting exit 3 over a half-done release.
        #
        # Guarded on `last`: a repo that has never released has no baseline to
        # resume, so its untagged version is a first release, not an in-flight one.
        in_flight = bool(last and at_ref and not _fully_released(shape, pkg.tag_for(at_ref)))

        if explicit_version:
            proposed, lvl, why = explicit_version, "explicit", "version given explicitly"
        elif in_flight:
            proposed, lvl = at_ref, "resume"
            # Name the part that is actually missing. "no tag" when the tag exists
            # and only the GitHub release is absent sends the reader looking in the
            # wrong place.
            missing = ("no tag" if not _tag_exists(shape, pkg.tag_for(at_ref))
                       else "the tag but no GitHub release")
            why = (f"{to_ref} already carries {at_ref} with {missing} "
                   f"({pkg.tag_for(at_ref)}) — finishing that release rather than "
                   f"starting a new one")
        else:
            lvl = level or inferred
            why = reason if level is None else f"{level} requested (inference said {inferred}: {reason})"
            proposed = notes_mod.bump(current, lvl, pre_release)

        # --force onto a version that is ALREADY fully released cannot do anything:
        # `run` reads the same state, finds the tag and the release present, and
        # returns `already-released` without bumping or tagging. Letting the plan
        # through would promise work at the approval gate that never happens — a
        # false success, which is the failure mode this whole skill is built to
        # avoid. Refuse instead, and name the tool that does rebuild a live tag.
        if force and _fully_released(shape, pkg.tag_for(proposed)):
            raise ReleaseError(
                f"{pkg.name}: {proposed} is already fully released "
                f"({pkg.tag_for(proposed)} exists and has a GitHub release). "
                f"--force re-cuts a NEW version; it cannot re-cut an existing tag, "
                f"which would need the tag deleted first. Either pick a higher "
                f"version, or to rebuild this exact tag re-run its pipeline directly "
                f"(eng:rerun-circleci)."
            )

        body = notes_mod.render_notes(shape.root, last, to_ref)
        # One rule for every shape: a package is releasable if it has NEW work, or
        # if it has UNFINISHED work. Only the "new work" test differs by shape —
        # lerna scopes it to the package directory, everything else to the repo.
        #
        # Keeping these as two separate branches is what let the lerna path miss
        # `in_flight`: after its tags landed the range went empty, so a half-done
        # lerna release could never be finished. The `or in_flight` has to apply to
        # both, so it lives outside the shape split.
        #
        # `force` is a third, deliberately operator-supplied reason: re-cut the ref
        # under a new version with no new work. Real cases are a forced image rebuild
        # and bumping past a version that shipped broken. It only ever widens what
        # counts as releasable — every downstream guarantee (bump verified in the
        # tagged commit, tag after the merge, release created) is untouched, so a
        # forced release is a normal release over an empty commit range.
        has_new_work = (_package_changed(shape, pkg, last, to_ref)
                        if shape.is_multi_package else bool(commits))
        changed = has_new_work or in_flight or force
        plans.append(PackagePlan(
            pkg=pkg, current=current, proposed=proposed, level=lvl,
            reason=why, last_tag=last, body=body, changed=changed,
        ))
        if not body.strip() and changed:
            warnings.append(
                f"{pkg.name}: no commits since {last or 'the beginning'} — "
                f"the release notes would be empty"
            )
        # Say so when --force is carrying the release on its own. Otherwise a forced
        # no-op is indistinguishable in the plan from a genuine one, and the whole
        # point of the approval gate is that the operator can see what they approved.
        if force and not has_new_work and not in_flight:
            warnings.append(
                f"{pkg.name}: FORCED — nothing changed since {last or 'the beginning'}. "
                f"{current} → {proposed} re-releases the same tree, so the image is "
                f"identical and the notes are empty."
            )

    releasing = [p for p in plans if p.changed]
    if not releasing:
        detail = ("no package changed since its last tag (ignoring *.md and .git* "
                  "per the lerna ignoreChanges convention)" if shape.is_multi_package
                  else f"no commits on {to_ref} since {plans[0].last_tag or 'the beginning'}")
        raise NothingToRelease(f"nothing to release: {detail}")

    label = (releasing[0].proposed if len(releasing) == 1
             else ", ".join(f"{p.pkg.name}@{p.proposed}" for p in releasing))
    branch_slug = _branch_slug(releasing)
    subject = COMMIT_SUBJECT.format(version=label)

    if shape.forbidden_commit_subject and re.search(shape.forbidden_commit_subject, subject):
        raise ReleaseError(
            f"commit subject {subject!r} matches this repo's forbidden pattern "
            f"{shape.forbidden_commit_subject!r}. Its CI fails a commit with that "
            f"subject when no tags point at HEAD, which is exactly the state a "
            f"squash merge creates before the tags are pushed."
        )

    return Plan(
        shape=shape, packages=plans,
        branch=f"chore/release-{branch_slug}",
        commit_subject=subject, warnings=warnings,
        head=sh(["git", "rev-parse", to_ref], cwd=shape.root).strip(),
    )


# ---------------------------------------------------------------- state


@dataclass
class State:
    """Where a release currently stands, derived from the repo and GitHub."""

    tags_pushed: list[str] = field(default_factory=list)
    tags_missing: list[str] = field(default_factory=list)
    releases_present: list[str] = field(default_factory=list)
    merged_sha: str | None = None
    open_pr: int | None = None
    branch_pushed: bool = False

    @property
    def done(self) -> bool:
        return bool(self.tags_pushed) and not self.tags_missing and not self.releases_missing

    @property
    def releases_missing(self) -> list[str]:
        return [t for t in self.tags_pushed if t not in self.releases_present]


def read_state(plan: Plan) -> State:
    shape, st = plan.shape, State()
    sh(["git", "fetch", "origin", "--tags", "--quiet"], cwd=shape.root, check=False)

    remote_tags = set()
    for line in sh(["git", "ls-remote", "--tags", "origin"], cwd=shape.root, check=False).splitlines():
        m = re.search(r"refs/tags/(\S+?)(?:\^\{\})?$", line)
        if m:
            remote_tags.add(m.group(1))

    for p in plan.releasing:
        (st.tags_pushed if p.tag in remote_tags else st.tags_missing).append(p.tag)

    for tag in st.tags_pushed:
        rc = subprocess.run(["gh", "release", "view", tag, "--repo", shape.slug, "--json", "tagName"],
                            capture_output=True, text=True, cwd=shape.root)
        if rc.returncode == 0:
            st.releases_present.append(tag)

    st.branch_pushed = bool(sh(["git", "ls-remote", "--heads", "origin", plan.branch],
                               cwd=shape.root, check=False).strip())

    prs = gh_json(["pr", "list", "--repo", shape.slug, "--head", plan.branch,
                   "--state", "all", "--json", "number,state,mergeCommit"], cwd=shape.root) or []
    for pr in prs:
        if pr["state"] == "MERGED":
            st.merged_sha = (pr.get("mergeCommit") or {}).get("oid")
        elif pr["state"] == "OPEN":
            st.open_pr = pr["number"]

    if st.merged_sha is None and not st.open_pr:
        # The target version may already be on the default branch with no PR that
        # this plan's branch name matches — a hand-edited version file, or an
        # earlier release whose branch was named differently. The bump, PR and
        # merge are all done in that case, so treat the ref as the merge commit
        # and resume at the tag step. Without this, run() would open a worktree,
        # write versions that produce no change, and try to open a PR with no
        # commits — which fails.
        ref = f"origin/{shape.default_branch}"
        if all(_version_at_ref(shape, p.pkg, ref) == p.proposed for p in plan.releasing):
            st.merged_sha = sh(["git", "rev-parse", ref], cwd=shape.root, check=False) or None
    return st


# ---------------------------------------------------------------- execution


def _worktree(shape: RepoShape, plan: Plan) -> Path:
    """An isolated checkout so the user's working tree is never touched.

    Worktrees live **outside** the repo, under the system temp dir keyed by repo
    name. A worktree is a full second copy of the tree with no node_modules, so a
    copy inside the repo sits in the path of every repo-wide tool: a husky
    pre-push hook that lints `.` walks it, fails on rules whose plugins cannot
    resolve there, and blocks the tag push mid-release. Nothing here removes a
    worktree when a release finishes, so one left inside the repo also breaks the
    *next* release.
    """
    base = Path(tempfile.gettempdir()) / "aura-release-worktrees" / shape.root.name
    wt = base / f"release-{plan.branch.replace('/', '-')}"
    if wt.exists():
        return wt
    base.mkdir(parents=True, exist_ok=True)
    # The temp dir can be cleared by the OS while git still has the worktree
    # registered, leaving `worktree add` refusing a path git believes is live.
    sh(["git", "worktree", "prune"], cwd=shape.root, check=False)
    existing = sh(["git", "branch", "--list", plan.branch], cwd=shape.root, check=False).strip()
    args = ["git", "worktree", "add", str(wt)]
    args += [plan.branch] if existing else ["-b", plan.branch, f"origin/{shape.default_branch}"]
    sh(args, cwd=shape.root)
    return wt


def _stale_branch(shape: RepoShape, plan: Plan) -> str | None:
    """Why an existing release branch must not be resumed, or None when it can be.

    A stopped run leaves `chore/release-x.y.z` behind, locally and maybe on the
    remote. Resuming it as-is ships whatever it was built on: after a fix landed
    on the default branch, the next run pushed the old commit built on the
    pre-fix default. So a branch with no open PR is resumed only when the current
    default branch is an ancestor of it.
    """
    default = f"origin/{shape.default_branch}"

    def behind(sha: str) -> bool:
        return subprocess.run(["git", "merge-base", "--is-ancestor", default, sha],
                              cwd=shape.root, capture_output=True).returncode != 0

    local = sh(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{plan.branch}"],
               cwd=shape.root, check=False).strip()
    # The remote itself, never refs/remotes/origin/<branch>: that tracking ref
    # outlives a `push --delete` and can be missing for a branch never fetched.
    line = sh(["git", "ls-remote", "--heads", "origin", plan.branch], cwd=shape.root, check=False).strip()
    remote = line.split()[0] if line else ""
    if remote:
        # Its commit has to be local before ancestry can be asked; one that will
        # not fetch cannot be shown fresh, so it counts as stale.
        sh(["git", "fetch", "-q", "origin", f"refs/heads/{plan.branch}"], cwd=shape.root, check=False)
    stale_local = bool(local) and behind(local)
    stale_remote = bool(remote) and behind(remote)
    if not (stale_local or stale_remote):
        return None
    wt = Path(tempfile.gettempdir()) / "aura-release-worktrees" / shape.root.name / \
        f"release-{plan.branch.replace('/', '-')}"
    cmds = [f"git -C {shape.root} worktree remove --force {wt}"] if wt.exists() else []
    if local:
        # The OS can clear the temp worktree while git still has it registered,
        # and `branch -D` refuses a branch checked out in a registered worktree.
        cmds.append(f"git -C {shape.root} worktree prune")
        cmds.append(f"git -C {shape.root} branch -D {plan.branch}")
    if remote:
        cmds.append(f"git -C {shape.root} push origin --delete {plan.branch}")
    return (f"{plan.branch} is left over from a stopped run and is not based on the "
            f"current {default}, so resuming it would ship the old commit. Nothing was "
            f"pushed. Delete it, then re-run:\n  " + "\n  ".join(cmds))


def _push_branch(cwd: Path, branch: str) -> None:
    """Push the release branch, with pre-push hooks disabled.

    Not a convenience: a pre-push hook **cannot** succeed from a release worktree,
    so running it only converts a working release into a failed one.

    A worktree is a checkout of tracked files and nothing else. Two things every
    husky hook needs are absent there:

      - `.husky/_/husky.sh`, which older hooks source on line 1. It is generated by
        husky's install and gitignored (`.husky/_/.gitignore` is `*`), so the hook
        exists in the worktree and the file it sources does not.
      - `node_modules`, so the `npm run lint` / `npm test` the hook body actually
        runs has nothing to run with.

    4U-engine hits the first and would hit the second: the push failed *after* the
    version files were already committed, leaving a half-done release (ENG-1082).
    Placing the worktree outside the repo (PROD-4288) fixed a different symptom —
    a hook linting `.` walking the copy — and cannot fix this one, because a
    gitignored file is absent from a worktree wherever that worktree lives.

    What is skipped is covered elsewhere, so this trades nothing away: the branch
    changes version files only, and its PR runs the repo's real CI before the merge
    this release depends on. The tag push is deliberately NOT routed through here —
    it runs in the main checkout, where hooks have what they need and still mean
    something.
    """
    sh(["git", "push", "--no-verify", "-u", "origin", branch], cwd=cwd)


def _untracked_paths(root: Path) -> list[str]:
    """Top-level untracked and ignored entries in the main checkout, as git reports
    them (directories end in `/`). These are files no commit — and so no tag —
    carries."""
    out = sh(["git", "status", "--porcelain", "--ignored", "--untracked-files=normal"],
             cwd=root, check=False)
    return [line[3:] for line in out.splitlines() if line[:3] in ("?? ", "!! ")]


def _hook_failure_paths(root: Path, output: str) -> list[str]:
    """The files a failed pre-push hook reported, relative to the repo root.

    Linters print each offending file as an absolute path on its own line (the
    ESLint stylish format every repo's `lint` script uses). Anything else in the
    output is ignored.
    """
    base = root.resolve()
    found = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("/"):
            continue
        try:
            rel = Path(line).resolve().relative_to(base)
        except (ValueError, OSError):
            continue
        found.append(rel.as_posix())
    return found


def _push_tag(root: Path, tag: str) -> str | None:
    """Push one release tag from the main checkout, with its pre-push hook.

    The hook runs here because, unlike in the release worktree, it has what it
    needs: `node_modules` and the generated husky files. Its usual job is a lint
    of `.`, which walks the **working tree** — untracked files included. A stray
    untracked copy in the checkout (an abandoned nested worktree such as
    `<Repo>/<Repo>/.claude/worktrees/…`, which the root `.claude/**` ignore does
    not match) makes that lint fail for a tag whose commit cannot contain it.

    So the hook is bypassed only when its failure is provably about such files:
    the output names a pre-push failure, it reports at least one file, and
    **every** reported file lies under an untracked or ignored path. The tagged
    commit is the merge commit whose PR already passed the repo's required
    checks, which run the same lint, so nothing the commit carries goes unchecked.
    Any failure on a tracked file, or one that names no file, still stops the
    release.

    Returns a note naming the untracked paths when the bypass was used, so the
    caller can report it and the user can clean them up; `None` otherwise.
    """
    p = subprocess.run(["git", "push", "origin", tag], cwd=root, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if p.returncode == 0:
        return None
    output = (p.stderr or "") + "\n" + (p.stdout or "")
    failure = ReleaseError(f"git push origin {tag} failed:\n{output.strip()}")
    if "pre-push" not in output.lower():
        raise failure
    reported = _hook_failure_paths(root, output)
    untracked = _untracked_paths(root)
    if not reported or not untracked:
        raise failure

    def under_untracked(rel: str) -> str | None:
        for u in untracked:
            if rel == u.rstrip("/") or (u.endswith("/") and rel.startswith(u)):
                return u
        return None

    culprits = {under_untracked(r) for r in reported}
    if None in culprits:
        raise failure
    sh(["git", "push", "--no-verify", "origin", tag], cwd=root)
    return (f"{tag} pushed with --no-verify: the pre-push hook failed only on "
            f"untracked files under {', '.join(sorted(culprits))} — remove them "
            f"(a stray nested worktree goes with `git worktree remove <path>`)")


_BRANCH_SLUG_MAX = 60


def _branch_slug(releasing: list[PackagePlan]) -> str:
    """The branch name's variable part — bounded, whatever the package count.

    A single-package repo keeps the bare version (`chore/release-1.2.3`).

    A multi-package repo does NOT enumerate its packages. Composing the name from
    every releasing package and version is what broke 4U-engine: 23 packages
    produced a ~700-character ref, and git refused to create the lock file for it
    with `File name too long` — before the worktree existed, so `--force` (which
    marks every package changed) could not run at all on the repo where forcing is
    most useful (ENG-1090).

    The name carries no information a reader needs: `plan` prints every package and
    version, and so does the PR body. So it names the first package and counts the
    rest.

    Determinism matters more than prettiness here — `read_state` finds an
    in-progress release by looking its branch up by name, so the same plan must
    always produce the same slug. `releasing` follows `shape.packages` order, which
    is stable. The length guard appends a short digest of the full label rather than
    truncating bare, so two long names that share a prefix cannot collide onto one
    branch and have a resume attach to the wrong release.
    """
    def slug(s: str) -> str:
        return re.sub(r"[^a-z0-9.]+", "-", s.lower()).strip("-")

    if len(releasing) == 1:
        out = slug(releasing[0].proposed)
    else:
        head = f"{releasing[0].pkg.name}-{releasing[0].proposed}"
        out = slug(f"{head}-plus-{len(releasing) - 1}-more")

    if len(out) > _BRANCH_SLUG_MAX:
        full = ",".join(f"{p.pkg.name}@{p.proposed}" for p in releasing)
        digest = hashlib.sha1(full.encode()).hexdigest()[:8]
        out = f"{out[:_BRANCH_SLUG_MAX - 9].rstrip('-')}-{digest}"
    return out


# Merge methods in preference order. Squash is the fleet norm and keeps the release
# on a single commit; the fallbacks exist because a repo may forbid it.
#
# Each method carries both spellings of its `gh repo view` field, because the name
# depends on the installed `gh`: current builds expose `squashMergeAllowed` /
# `mergeCommitAllowed` / `rebaseMergeAllowed`, older ones the `allow*` form. Asking
# for a field a given `gh` does not know is a hard error listing every valid field,
# not a null — so the spellings are tried in turn and either key is honoured.
_MERGE_METHODS = (("--squash", ("squashMergeAllowed", "allowSquashMerge")),
                  ("--merge", ("mergeCommitAllowed", "allowMergeCommit")),
                  ("--rebase", ("rebaseMergeAllowed", "allowRebaseMerge")))

_MERGE_FIELD_SETS = ("squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed",
                     "allowSquashMerge,allowMergeCommit,allowRebaseMerge")


def _is_unknown_json_field(exc: ReleaseError) -> bool:
    """Did `gh` reject the request because it does not know the field names?

    Only that failure means "try the other spelling". Everything else `gh repo view`
    can fail with — not authenticated, no network, repo not found — is a different
    fault with a different fix, and swallowing it into the spelling retry would make
    every one of them read as "upgrade your gh".
    """
    return "unknown json field" in str(exc).lower()


def _merge_method(slug: str, cwd: Path) -> str:
    """Which merge flag this repo actually permits.

    Assuming `--squash` fails outright on a repo that forbids it:

        GraphQL: Squash merges are not allowed on this repository. (mergePullRequest)

    and the release stops with the branch pushed and nothing tagged. The fleet is
    squash-only today, so this guards an exception rather than the norm — but the
    setting is per-repo, and a repo that forbids squash fails this way only *after* the
    bump commit exists, which is why the method is read and never assumed (ENG-1089).

    The ordering discipline survives any of these: `run` reads the merge commit SHA
    back from the PR and verifies it carries the bump before tagging, and all three
    methods land a new commit on the default branch. Rebase is last because it lands
    the branch's commits individually rather than one, so the SHA to tag is whatever
    the PR reports as its merge commit — still read back, never assumed.

    The `--json` field names are version-dependent (see `_MERGE_FIELD_SETS`), so each
    spelling is attempted until one is accepted. A `gh` that rejects every spelling is
    reported as its own failure rather than as a repo that forbids all three methods:
    the two need different fixes, and reading "permits no merge method" off a repo that
    permits squash sends the caller into its settings for no reason. Only an unknown-
    field rejection is treated that way — any other `gh` failure (unauthenticated, no
    network, repo not found) propagates verbatim, carrying its own stderr, so it is not
    misread as a third thing again.
    """
    allowed: dict = {}
    rejected: list[str] = []
    for fields in _MERGE_FIELD_SETS:
        try:
            info = gh_json(["repo", "view", slug, "--json", fields], cwd=cwd)
        except ReleaseError as exc:
            if not _is_unknown_json_field(exc):
                raise
            rejected.append(str(exc).replace("\n", "\n  "))
            continue
        if isinstance(info, dict) and info:
            allowed = info
            break
    if not allowed:
        detail = "\n  ".join(rejected) if rejected else "gh returned no fields"
        raise ReleaseError(
            f"could not read the merge settings for {slug} — no known spelling of the "
            f"merge-method fields was accepted by this gh, so which method the repo "
            f"permits is unknown and nothing was merged or tagged. Upgrade or check "
            f"`gh`, then re-run — state is derived, so nothing is lost.\n  {detail}"
        )
    for flag, keys in _MERGE_METHODS:
        if any(allowed.get(k) for k in keys):
            return flag
    raise ReleaseError(
        f"{slug} permits no merge method (squash / merge commit / rebase are all "
        f"disabled in its settings), so this release cannot land. Enable one in the "
        f"repository's settings, then re-run — state is derived, so nothing is lost."
    )


# Poll budget for required checks. Sized for a CI run, not a deploy: the branch
# carries only version files, so its checks are the repo's lint/test pass.
_CHECK_WAIT_SECONDS = 900
_CHECK_POLL_SECONDS = 15

# mergeStateStatus values, classified explicitly. Never "not CLEAN": UNSTABLE means
# mergeable with a non-required check failing, and treating it as unmergeable would
# stall a release the repo would happily accept.
_MERGE_READY = ("CLEAN", "UNSTABLE", "HAS_HOOKS")
_MERGE_WAITING = ("BLOCKED", "UNKNOWN")


def _check_summary(slug: str, pr: int, cwd: Path) -> str:
    """Per-check states, for the message only — never for the merge decision."""
    data = gh_json(["pr", "view", str(pr), "--repo", slug,
                    "--json", "statusCheckRollup"], cwd=cwd)
    rows = (data or {}).get("statusCheckRollup") or [] if isinstance(data, dict) else []
    parts = []
    for r in rows:
        name = r.get("name") or r.get("context") or "?"
        # check-runs carry conclusion/status; legacy commit statuses carry state
        parts.append(f"{name}={r.get('conclusion') or r.get('status') or r.get('state') or '?'}")
    return ", ".join(parts) or "no checks reported"


def _wait_until_mergeable(shape: RepoShape, pr: int) -> None:
    """Block until GitHub says the PR can merge.

    `run` used to call `gh pr merge` the moment the PR existed. On a repo whose
    default branch requires a status check, that check is still PENDING at that
    instant and GitHub refuses:

        X Pull request #5 is not mergeable: the base branch policy prohibits the merge.

    The release then stopped with the version files committed and pushed but nothing
    tagged, and printed a red ERROR — indistinguishable from a real failure, though
    the only correct response was to re-run once CI went green. It hit both repos in
    the fleet that have required checks (ENG-1088).

    Asking `mergeStateStatus` rather than re-deriving the branch rules keeps this
    honest: GitHub already knows what is required, so there is no second copy of the
    policy here to drift.

    A wait that cannot succeed still ends in a raise — a required review, for
    instance, will never clear on its own — but the message names the check states so
    the reason is visible rather than guessed at.
    """
    deadline = time.monotonic() + _CHECK_WAIT_SECONDS
    announced = False
    while True:
        data = gh_json(["pr", "view", str(pr), "--repo", shape.slug,
                        "--json", "mergeStateStatus"], cwd=shape.root)
        state = (data or {}).get("mergeStateStatus") if isinstance(data, dict) else None
        if state in _MERGE_READY:
            return
        if state not in _MERGE_WAITING:
            raise ReleaseError(
                f"PR #{pr} is {state} — refusing to merge. "
                f"{'Rebase it on the default branch' if state in ('BEHIND', 'DIRTY') else 'Resolve it'} "
                f"and re-run; state is derived, so nothing is lost. "
                f"Checks: {_check_summary(shape.slug, pr, shape.root)}"
            )
        if time.monotonic() >= deadline:
            raise ReleaseError(
                f"PR #{pr} was still {state} after {_CHECK_WAIT_SECONDS // 60} minutes. "
                f"Checks: {_check_summary(shape.slug, pr, shape.root)}. "
                f"If a required review is what is missing, get it and re-run — the "
                f"release resumes from here and nothing is lost."
            )
        if not announced:
            # Printed, not recorded as a step: waiting is progress, not an action
            # taken, and `run`'s completed-step list is what a resume reports.
            print(f"  … waiting for PR #{pr} to become mergeable "
                  f"({_check_summary(shape.slug, pr, shape.root)})", flush=True)
            announced = True
        time.sleep(_CHECK_POLL_SECONDS)


def _branch_has_bump(shape: RepoShape, plan: Plan, wt: Path) -> bool:
    """Does the release branch already carry every target version?

    Distinguishes "a previous run committed the bump and died before pushing"
    from "the bump is a genuine no-op", which otherwise look identical: writing
    the same values into an already-bumped tree leaves it clean either way.
    """
    for p in plan.releasing:
        for f in p.pkg.version_files:
            blob = sh(["git", "show", f"HEAD:{f.relative_to(shape.root)}"], cwd=wt, check=False)
            if p.proposed not in blob:
                return False
    return True


def _write_version(path: Path, version: str) -> None:
    text = path.read_text(encoding="utf-8")
    if path.name == "package.json":
        # Textual replace of the first top-level "version" key, so key order and
        # formatting survive. json.dump would reformat the whole file.
        new, n = re.subn(r'(?m)^(\s*"version"\s*:\s*")[^"]*(")', rf'\g<1>{version}\g<2>', text, count=1)
        if not n:
            raise ReleaseError(f"no \"version\" key found in {path}")
    elif path.name == "pyproject.toml":
        new, n = re.subn(r'(?m)^(version\s*=\s*")[^"]*(")', rf'\g<1>{version}\g<2>', text, count=1)
        if not n:
            raise ReleaseError(f"no version field found in {path}")
    else:
        raise ReleaseError(f"do not know how to write a version into {path.name}")
    path.write_text(new, encoding="utf-8")


def _lock_text(data: dict) -> str:
    """npm's own serialisation of a lock: `JSON.stringify(lock, null, 2)` + newline."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _write_lock_version(path: Path, version: str) -> str | None:
    """Move the version npm records inside a package-lock.json.

    The lock holds the package's own version twice — the top-level `version` and
    `packages[""].version` — and npm writes the whole file with
    `JSON.stringify(lock, null, 2)`. Reserialising it therefore reproduces the
    file byte for byte, so the release commit carries a two-line diff and no
    reformatting.

    Returns None once the lock is moved, or the reason it was left alone. Nothing
    resolves from these fields, so an unexpected lock is reported and skipped
    rather than raised: a cosmetic mismatch must not stop a release.
    """
    if not path.is_file():
        return "not present"
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        return f"unreadable ({exc})"
    if not isinstance(data, dict):
        return "not a JSON object"
    packages = data.get("packages")
    root_entry = packages.get("") if isinstance(packages, dict) else None
    targets = [d for d in (data, root_entry) if isinstance(d, dict) and "version" in d]
    if not targets:
        return "carries no version of its own"
    if _lock_text(data) != text:
        return "not in npm's format — rewriting it would reformat the whole file"
    for d in targets:
        d["version"] = version
    path.write_text(_lock_text(data), encoding="utf-8")
    return None


def _verify_bump_in_commit(shape: RepoShape, sha: str, plan: Plan) -> None:
    """Confirm the commit about to be tagged actually contains the bump.

    This is the check that makes rule 1 real rather than aspirational: a tag on
    a commit without the bump deploys an image that misreports its own version.
    """
    for p in plan.releasing:
        for f in p.pkg.version_files:
            rel = f.relative_to(shape.root)
            blob = sh(["git", "show", f"{sha}:{rel}"], cwd=shape.root)
            if p.proposed not in blob:
                raise ReleaseError(
                    f"refusing to tag {sha[:12]}: {rel} does not contain "
                    f"{p.proposed}. The tagged commit must carry the bump."
                )


def _extra_commits(shape: RepoShape, plan: Plan, expected: str, sha: str) -> list[str]:
    """Commits the tagged commit carries that the approved range did not: merged onto the
    default branch after `expected` and before the bump PR. The bump commit itself is not
    one. On a lerna repo only commits touching a releasing package count, since only those
    packages are tagged."""
    if subprocess.run(["git", "cat-file", "-e", f"{expected}^{{commit}}"],
                      cwd=shape.root, capture_output=True).returncode:
        raise ReleaseError(f"--expect-head {expected[:12]} is not a commit in {shape.root}. "
                           f"Fetch {shape.default_branch}, or pass the head the plan printed.")
    cmd = ["git", "log", "--format=%h %s", f"{expected}..{sha}^"]
    # check=True: a failed log must stop the tag, never read as "no extra commits".
    if not shape.is_multi_package:
        return sh(cmd, cwd=shape.root).splitlines()
    # Per package: each tag answers only for its own directory.
    out = []
    for p in plan.releasing:
        rel = str(p.pkg.directory.relative_to(shape.root))
        out += [f"{p.pkg.name}: {line}"
                for line in sh(cmd + ["--", rel], cwd=shape.root).splitlines()]
    return out


def _range_head(shape: RepoShape, plan: Plan, sha: str, expect_head: str | None) -> str:
    """The head the go approved. Without --expect-head that is this plan's own head, but only
    when the plan was computed before the bump merged: a resumed run plans on a tip that
    already contains the bump, so its head would approve whatever an earlier run refused."""
    if expect_head:
        return expect_head
    if plan.head and subprocess.run(["git", "merge-base", "--is-ancestor", sha, plan.head],
                                    cwd=shape.root, capture_output=True).returncode:
        return plan.head
    raise ReleaseError(
        f"refusing to tag {sha[:12]}: the bump merged before this run planned, so this plan's "
        f"head cannot stand for the range the go approved. Re-run with --expect-head <sha>: "
        f"the Range head the approved plan printed, or the sha a refusal printed once a go "
        f"covers its commits.")


def tagged_range(shape: RepoShape, plan: Plan, sha: str) -> dict[str, int]:
    """Per releasing package, how many commits its tag covers (last tag..sha)."""
    out = {}
    for p in plan.releasing:
        rng = f"{p.last_tag}..{sha}" if p.last_tag else sha
        out[p.tag] = int(sh(["git", "rev-list", "--count", rng], cwd=shape.root, check=False).strip() or 0)
    return out


def run(plan: Plan, *, dry_run: bool = False, wait_ci: bool = True,
        expect_head: str | None = None) -> dict:
    shape = plan.shape
    st = read_state(plan)
    done: list[str] = []

    def step(msg: str) -> None:
        done.append(msg)
        print(f"  → {msg}", flush=True)

    if dry_run:
        return {"status": "dry-run", "would": describe(plan, st)}

    if st.done:
        # Tags and releases exist — but the deploy may still not have happened.
        # On shapes whose CI reads tags at HEAD, the re-trigger is the step that
        # actually ships, and it is the one most likely to have been missed: it
        # runs last, and with no CircleCI token it only prints ACTION NEEDED.
        # Returning here without it would report success over an undeployed
        # release.
        out = {"status": "already-released", "tags": st.tags_pushed, "steps": done}
        if shape.needs_pipeline_retrigger:
            step(_retrigger(shape))
            out["steps"] = done
        return out

    # ---- 1..4  bump, commit, push, PR — each sub-step skipped if already done,
    #            so an interrupt between any two of them resumes rather than
    #            re-bumping or stranding a pushed branch.
    if st.merged_sha is None:
        if st.open_pr is None:
            why = _stale_branch(shape, plan)
            if why:
                raise ReleaseError(why)
        if st.open_pr is None and not st.branch_pushed:
            wt = _worktree(shape, plan)
            for p in plan.releasing:
                for f in p.pkg.version_files:
                    _write_version(wt / f.relative_to(shape.root), p.proposed)
                for f in p.pkg.lock_files:
                    rel = f.relative_to(shape.root)
                    left = _write_lock_version(wt / rel, p.proposed)
                    if left:
                        print(f"  ⚠ {rel} keeps its old version: {left}", flush=True)

            if sh(["git", "status", "--porcelain"], cwd=wt).strip():
                sh(["git", "add", "-A"], cwd=wt)
                msg = wt / ".git-release-msg"
                msg.write_text(plan.commit_subject + "\n", encoding="utf-8")
                sh(["git", "commit", "-F", str(msg)], cwd=wt)
                msg.unlink(missing_ok=True)
                for p in plan.releasing:
                    step(f"bumped {p.pkg.name} {p.current} → {p.proposed}")
            elif _branch_has_bump(shape, plan, wt):
                # A previous run already committed the bump here and died before
                # pushing. Writing the same values left the tree clean.
                step("bump already committed locally — reusing it")
            else:
                raise ReleaseError(
                    f"bump produced no change in {wt} and the branch does not "
                    f"carry {plan.label}. Inspect that worktree."
                )
            _push_branch(wt, plan.branch)
            step(f"pushed {plan.branch}")
            st = read_state(plan)

        if st.open_pr is None:
            # The branch is on the remote but has no PR — either a fresh push
            # above, or a previous run that died between push and PR creation.
            body = Path(tempfile.gettempdir()) / f"rv-pr-{plan.branch.replace('/', '-')}.md"
            body.write_text(_pr_body(plan), encoding="utf-8")
            sh(["gh", "pr", "create", "--repo", shape.slug, "--base", shape.default_branch,
                "--head", plan.branch, "--title", plan.commit_subject,
                "--body-file", str(body)], cwd=shape.root)
            body.unlink(missing_ok=True)
            st = read_state(plan)
            if st.open_pr is None:
                raise ReleaseError(
                    f"opened a PR for {plan.branch} but could not read its number "
                    f"back. Re-run to resume — state is derived, so nothing is lost."
                )
            step(f"opened PR #{st.open_pr}")

        # ---- 5  merge. Not --auto: allow_auto_merge is false on these repos, and
        #         `run` needs the merge SHA in hand to tag, so it waits here rather
        #         than handing the merge to GitHub and losing track of it.
        #
        #         Two things are asked rather than assumed, because assuming either
        #         one stopped a release mid-flight: whether the PR may merge yet
        #         (ENG-1088) and which merge method the repo permits (ENG-1089).
        _wait_until_mergeable(shape, st.open_pr)
        method = _merge_method(shape.slug, shape.root)
        sh(["gh", "pr", "merge", str(st.open_pr), "--repo", shape.slug,
            method, "--delete-branch"], cwd=shape.root)
        st = read_state(plan)
        if not st.merged_sha:
            raise ReleaseError(f"PR #{st.open_pr} reported merged but exposed no merge commit")
        step(f"merged PR as {st.merged_sha[:12]}")

    sha = st.merged_sha
    sh(["git", "fetch", "origin", shape.default_branch, "--quiet"], cwd=shape.root)
    if subprocess.run(["git", "merge-base", "--is-ancestor", sha, f"origin/{shape.default_branch}"],
                      cwd=shape.root).returncode:
        raise ReleaseError(
            f"{sha[:12]} is not an ancestor of origin/{shape.default_branch} — "
            f"refusing to tag a commit that is not on the default branch"
        )
    _verify_bump_in_commit(shape, sha, plan)

    # The go approves the range the plan showed. A PR merged after that, while the bump
    # PR waited for CI, lands under the tag too. Stop before tagging, with the merge
    # already done: a re-run resumes at the tag once a go covers the new range.
    expected = _range_head(shape, plan, sha, expect_head)
    extra = _extra_commits(shape, plan, expected, sha)
    if extra:
        raise ReleaseError(
            f"refusing to tag {sha[:12]}: it carries {len(extra)} commit(s) merged after "
            f"{expected[:12]}, the head the go approved:\n  "
            + "\n  ".join(extra[:20])
            + f"\nThe bump is merged and nothing is tagged. Get a go that covers them, then "
              f"re-run with --expect-head {sha}.")
    step(f"tagged range checked against {expected[:12]}: "
         + ", ".join(f"{t} covers {n} commit(s)" for t, n in tagged_range(shape, plan, sha).items()))

    # ---- 6  tag the merge commit, one tag per released package, pushed
    #         separately from the branch (rule 3)
    for p in plan.releasing:
        if p.tag in st.tags_pushed:
            continue
        # A local tag may already exist from an interrupted run. Reuse it only if
        # it points at the same commit; a tag on a different commit is a real
        # conflict and must not be pushed silently.
        existing = sh(["git", "rev-list", "-n", "1", p.tag], cwd=shape.root, check=False).strip()
        if existing and existing != sha:
            raise ReleaseError(
                f"local tag {p.tag} points at {existing[:12]} but this release "
                f"targets {sha[:12]}. Delete the stale tag "
                f"(`git -C {shape.root} tag -d {p.tag}`) and re-run."
            )
        if not existing:
            sh(["git", "tag", "-a", p.tag, "-m", f"Release {p.proposed}", sha], cwd=shape.root)
        bypass = _push_tag(shape.root, p.tag)
        step(f"tagged {p.tag} at {sha[:12]}")
        if bypass:
            step(bypass)

    # ---- 7  GitHub release per tag
    st = read_state(plan)
    for p in plan.releasing:
        if p.tag in st.releases_present:
            continue
        nf = shape.root / f".git-notes-{p.pkg.name.replace('/', '-')}"
        nf.write_text(p.body + "\n", encoding="utf-8")
        sh(["gh", "release", "create", p.tag, "--repo", shape.slug,
            "--title", RELEASE_TITLE.format(version=p.proposed),
            "--notes-file", str(nf), "--target", sha], cwd=shape.root)
        nf.unlink(missing_ok=True)
        step(f"created GitHub release {p.tag}")

    # ---- 8  re-trigger the default-branch pipeline where CI reads tags at HEAD.
    #         The branch pipeline already ran, before the tags existed.
    if shape.needs_pipeline_retrigger:
        step(_retrigger(shape))

    status = "resumed" if plan.is_resume else "released"
    result = {"status": status, "tags": [p.tag for p in plan.releasing],
              "sha": sha, "steps": done}

    # A resume ships no new version, so anything on the default branch past the
    # resumed tag remains unreleased. Naming it is the point: without it, the caller
    # cannot tell a finished older release from one carrying their own work.
    if plan.is_resume:
        pending = _unreleased_after(plan)
        result["unreleased"] = pending
        result["next"] = ("this finished an existing release and shipped no new "
                          "version — re-run to release the commits listed under "
                          "`unreleased`" if any(pending.values()) else
                          "nothing further to release")
    if wait_ci:
        result["ci"] = "run `verify` to check the pipeline"
    return result


def _unreleased_after(plan: Plan) -> dict[str, list[str]]:
    """Per package, the commit subjects on the default branch past its resumed tag.

    These are the commits a resume leaves unreleased. Both inputs are already in
    hand — the resumed tag and the ref being released — so this reads git rather
    than deriving anything.
    """
    to_ref = f"origin/{plan.shape.default_branch}"
    out: dict[str, list[str]] = {}
    for p in plan.releasing:
        try:
            commits = notes_mod.read_commits(plan.shape.root, p.tag, to_ref)
        except RuntimeError:
            # A tag that will not resolve is not worth failing a finished release
            # over; report it as unknown rather than crashing after the work landed.
            out[p.pkg.name] = ["(could not read commits past this tag)"]
            continue
        out[p.pkg.name] = [c.subject for c in commits]
    return out


def _retrigger(shape: RepoShape) -> str:
    import os
    token = next((os.environ[k] for k in
                  ("CIRCLE_TOKEN", "CIRCLECI_TOKEN", "CIRCLE_CI_TOKEN", "CIRCLECI_API_TOKEN")
                  if os.environ.get(k)), None)
    if not token:
        return (f"ACTION NEEDED: re-trigger the {shape.default_branch} pipeline for "
                f"{shape.slug} — its CI builds from tags at HEAD and the branch "
                f"pipeline ran before the tags existed. No CircleCI token in env.")
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        f"https://circleci.com/api/v2/project/gh/{shape.slug}/pipeline",
        data=json.dumps({"branch": shape.default_branch}).encode(),
        headers={"Circle-Token": token, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            num = json.load(r).get("number")
        return f"re-triggered the {shape.default_branch} pipeline (#{num}) so it sees the tags"
    except urllib.error.HTTPError as e:
        return (f"ACTION NEEDED: re-trigger the {shape.default_branch} pipeline manually — "
                f"CircleCI returned {e.code}")


def _pr_body(plan: Plan) -> str:
    lines = ["## Release", ""]
    for p in plan.releasing:
        lines.append(f"- **{p.pkg.name}** `{p.current}` → `{p.proposed}` "
                     f"({p.level} — {p.reason})")
    lines += ["", "Version bump only; no functional change. The tag is created after this",
              "merges, pointing at the squashed commit, and CI deploys from that tag.", ""]
    lines += ["## Notes", "", "```", plan.primary.body or "(no commits)", "```"]
    return "\n".join(lines)


# ---------------------------------------------------------------- output


def describe(plan: Plan, st: State | None = None) -> dict:
    shape = plan.shape
    d = {
        "repo": str(shape.root), "slug": shape.slug, "shape": shape.shape,
        "default_branch": shape.default_branch, "branch": plan.branch,
        "commit_subject": plan.commit_subject,
        "ci_jobs": shape.ci_jobs,
        "needs_pipeline_retrigger": shape.needs_pipeline_retrigger,
        "packages": [{
            "name": p.pkg.name, "current": p.current, "proposed": p.proposed,
            "level": p.level, "reason": p.reason, "last_tag": p.last_tag,
            "tag": p.tag, "changed": p.changed,
            "version_files": [str(f.relative_to(shape.root)) for f in p.pkg.version_files],
            "lock_files": [str(f.relative_to(shape.root)) for f in p.pkg.lock_files],
            "notes": p.body,
        } for p in plan.packages],
        "warnings": plan.warnings,
        "head": plan.head,
    }
    if st:
        d["state"] = {
            "tags_pushed": st.tags_pushed, "tags_missing": st.tags_missing,
            "releases_missing": st.releases_missing, "merged_sha": st.merged_sha,
            "open_pr": st.open_pr, "branch_pushed": st.branch_pushed,
            # Mirrors run()'s branching exactly, including the pushed-branch-but-
            # no-PR gap — a status that disagrees with what run() will do is worse
            # than no status at all.
            "resume_from": ("nothing — already released" if st.done
                            else "github release" if st.tags_pushed and st.releases_missing
                            else "tag" if st.merged_sha
                            else "merge" if st.open_pr
                            else "pull request" if st.branch_pushed
                            else "bump"),
        }
    return d


def print_plan(plan: Plan, st: State | None) -> None:
    shape = plan.shape
    print(f"\nRepo    {shape.root}")
    print(f"GitHub  {shape.slug}  (default branch: {shape.default_branch})")
    print(f"Shape   {shape.shape}")
    for p in plan.packages:
        if not p.changed:
            why = ("not selected (--package)" if p.level == "skip"
                   else f"unchanged since {p.last_tag}")
            print(f"\n  {p.pkg.name}: {why} — skipping")
            continue
        files = ", ".join(str(f.relative_to(shape.root)) for f in p.pkg.version_files)
        print(f"\n  {p.pkg.name}")
        print(f"    {p.current} → {p.proposed}   ({p.level}: {p.reason})")
        print(f"    version file(s): {files}")
        locks = ", ".join(str(f.relative_to(shape.root)) for f in p.pkg.lock_files)
        if locks:
            print(f"    lock file(s):    {locks}")
        print(f"    tag: {p.tag}   (last: {p.last_tag or 'none'})")
        body = p.body or "(no commits)"
        print("    notes:")
        for line in body.splitlines()[:12]:
            print(f"      {line}")
        extra = len(body.splitlines()) - 12
        if extra > 0:
            print(f"      … {extra} more")

    print(f"\nWill do:")
    print(f"  1. bump on branch {plan.branch}, commit \"{plan.commit_subject}\"")
    print(f"  2. push the branch and open a PR against {shape.default_branch}")
    print(f"  3. squash-merge it (the only accepted route onto {shape.default_branch})")
    print(f"  4. tag the SQUASHED merge commit, after verifying it carries the bump")
    print(f"  5. create the GitHub release(s)")
    if shape.needs_pipeline_retrigger:
        print(f"  6. re-trigger the {shape.default_branch} pipeline — this repo's CI "
              f"builds from tags at HEAD")
    print(f"\n  CI to verify afterwards: {', '.join(shape.ci_jobs)}")
    if plan.head:
        print(f"\n  Range head: {plan.head}  (the go covers this; pass it to run as "
              f"--expect-head so a PR merged after it is not tagged)")

    for w in plan.warnings:
        print(f"\n  WARNING: {w}")
    if st and (st.merged_sha or st.open_pr or st.tags_pushed):
        print(f"\n  Partially complete — resume from: {describe(plan, st)['state']['resume_from']}")


def latest_job_statuses(workflows: list, jobs_of) -> dict:
    """Latest known status per job name across a pipeline's workflows.

    A rerun adds a NEW workflow to the same pipeline and leaves the superseded one
    in place, so a pipeline routinely carries both a failed and a successful run of
    the same job. CircleCI lists workflows newest-first, so collecting them in API
    order lets the OLDEST status win and reports a recovered deploy as still broken.

    Walking oldest-first instead makes the newest status win. Per JOB rather than
    per workflow, because `rerun --from-failed` re-runs only the failed subtree: the
    earlier workflow holds the sole record of the jobs that already passed, and
    dropping it would lose them.
    """
    ordered = sorted(workflows, key=lambda w: w.get("created_at") or "")
    jobs: dict[str, str] = {}
    for wf in ordered:
        for j in jobs_of(wf):
            jobs[j["name"]] = j["status"]
    return jobs


# Repos that can report the version production is actually SERVING. CI green
# means the image was built and pushed; the rollout lands afterwards, and the
# gap is a minute or more. `verify` would otherwise say `success` while every
# pod still runs the previous version -- which reads as "deployed" and is the
# single easiest way to test a release that is not live yet.
SERVING_PROBES = {
    "AuraHealth/IntelligenceService": "aura_llm_ledger",
}


def _serving_versions_aura_ledger() -> tuple[set, str | None]:
    """Versions stamped on the most recent Aura llmCall rows.

    Every IntelligenceService LLM call records the service `version` that
    handled it, so the ledger is a direct read of what is serving traffic --
    not an inference from CI, and not a claim about what was deployed. Read from
    BigQuery `llm.llm_call` (ENG-1657): IntelligenceService streams rows through
    the Storage Write API default stream, readable within seconds, so the newest
    rows are current.
    """
    import os
    import sys
    from datetime import datetime, timezone

    if not os.environ.get("GCP_KEY"):
        return set(), "GCP_KEY not set"
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import llm_ledger  # noqa: E402  (bundled copy — canonical at data:analyze-user-conversations)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        rows = llm_ledger.rows({"from": today, "limit": 100})
    except llm_ledger.LedgerError as e:
        return set(), f"ledger unreachable: {e}"
    if not rows:
        return set(), "ledger returned no rows for today"
    return {r.get("version") for r in rows if r.get("version")}, None


def serving_check(shape: RepoShape, tag: str) -> dict:
    """Is the released version actually handling traffic?

    Returns `deployed`: `confirmed` when the tag's version appears on live
    traffic, `not-serving` when it does not, `unknown` when no probe exists or
    it could not run. `unknown` is NOT a pass.

    `not-serving` deliberately does not say "not yet": the same answer covers a
    rollout still in flight and a version already superseded by a later one,
    and the probe cannot tell those apart from a single read. Both mean the
    same thing to a caller — this tag is not what production is running.
    """
    probe = SERVING_PROBES.get(shape.slug)
    if not probe:
        return {"deployed": "unknown", "serving_probe": False,
                "deploy_detail": "no serving probe for this repo — CI green does "
                                 "not confirm the rollout; check manually."}
    versions, err = _serving_versions_aura_ledger()
    if err:
        return {"deployed": "unknown", "deploy_detail": err}
    want = tag.lstrip("v")
    return {"deployed": "confirmed" if want in versions else "not-serving",
            "serving": sorted(v for v in versions if v)}


def verify(shape: RepoShape, tag: str, *, deployment_log: str | None = None,
           trigger_missing: bool = False) -> dict:
    """Report the CI pipeline for a tag, then the rollout it triggers.

    The tag is the trigger, not the outcome: CI green is the image pushed, and the
    push starts the Spinnaker pipeline that actually patches the cluster.
    """
    out = _verify_ci(shape, tag, trigger_missing=trigger_missing)
    # Spinnaker only starts once `push` has pushed the image, so asking while CI
    # runs is noise and asking after it failed is moot. On `unverified` (no
    # CircleCI token) the rollout is the only signal there is, and an execution
    # after the tag means the push did land.
    if out["status"] in ("success", "unverified"):
        roll = rollout_mod.rollout_check(shape, tag, log_text=deployment_log)
        assert "detail" not in roll, "rollout_check must not shadow verify's detail"
        out.update(roll)
    return out


# A tag's pipeline normally exists within seconds of the push. Before this age a
# missing one may just be late, and triggering it would build and deploy twice.
MISSING_PIPELINE_MIN_AGE_S = 300


def _iso(value: str | None) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _tag_created(shape: RepoShape, tag: str) -> dt.datetime | None:
    """When the tag's GitHub release was created, or None when unknown."""
    rc = subprocess.run(["gh", "release", "view", tag, "--repo", shape.slug, "--json", "createdAt"],
                        capture_output=True, text=True, cwd=shape.root)
    if rc.returncode:
        return None
    try:
        return _iso(json.loads(rc.stdout)["createdAt"])
    except (ValueError, KeyError, TypeError):
        return None


def _tag_commit_time(shape: RepoShape, tag: str) -> dt.datetime | None:
    """The tagged commit's time: no pipeline for the tag can be older than it.

    Not the GitHub release's time, which can come hours after the tag push (and
    so after its pipeline) when a stopped run resumes.
    """
    out = sh(["git", "log", "-1", "--format=%cI", f"refs/tags/{tag}"], cwd=shape.root, check=False).strip()
    return _iso(out) if out else None


def _tag_age_s(shape: RepoShape, tag: str) -> float | None:
    """Seconds since the tag's GitHub release was created, or None when unknown."""
    created = _tag_created(shape, tag)
    return None if created is None else (dt.datetime.now(dt.timezone.utc) - created).total_seconds()


PIPELINE_PAGES = 10


def _find_tag_pipeline(api, slug: str, tag: str, since) -> tuple[dict | None, bool]:
    """(the tag's pipeline or None, whether the search reached back before the tag).

    CircleCI lists 20 pipelines per page, newest first, so a tag pipeline on a busy
    repo ages off the first page. Page back until the tag's pipeline turns up, or
    a pipeline created before the tagged commit proves it is not further back.
    `since` is a callable giving that commit's time, asked only when the first
    page misses.
    """
    path, token, when = f"project/gh/{slug}/pipeline", None, None
    for _ in range(PIPELINE_PAGES):
        page = api(path + (f"?page-token={token}" if token else ""))
        items = page.get("items") or []
        match = next((p for p in items if (p.get("vcs") or {}).get("tag") == tag), None)
        if match:
            return match, True
        if when is None:
            when = since() or False
        oldest = _iso(items[-1].get("created_at")) if items else None
        if when and oldest is not None and oldest < when:
            return None, True
        token = page.get("next_page_token")
        if not token:
            return None, True  # the whole history was read
    return None, False


def _trigger_tag_pipeline(shape: RepoShape, tag: str, api_post) -> dict:
    """Start the missing pipeline for a tag, once the tag is old enough to be sure."""
    age = _tag_age_s(shape, tag)
    if age is None:
        return {"status": "no-pipeline",
                "detail": f"no pipeline for {tag}, and the tag's age could not be read "
                          f"(no GitHub release?), so nothing was triggered. Check CircleCI, "
                          f"then trigger it by hand if it is really missing."}
    if age < MISSING_PIPELINE_MIN_AGE_S:
        return {"status": "no-pipeline",
                "detail": f"no pipeline for {tag} yet, but the tag is only {int(age)} s old: "
                          f"it may still be on its way. Re-run in a few minutes."}
    try:
        num = api_post(f"project/gh/{shape.slug}/pipeline", {"tag": tag}).get("number")
    except Exception as e:  # noqa: BLE001 — any failure is reported, never a pass
        return {"status": "no-pipeline",
                "detail": f"no pipeline for {tag}; triggering one failed ({e}). Trigger it by hand."}
    return {"status": "triggered", "pipeline": num,
            "detail": f"no pipeline existed for {tag} (the tag event was lost); started "
                      f"pipeline #{num} for it. Re-run verify to follow it."}


def _verify_ci(shape: RepoShape, tag: str, *, trigger_missing: bool = False) -> dict:
    """The CircleCI half of `verify`, plus the serving probe once CI is green."""
    import os
    token = next((os.environ[k] for k in
                  ("CIRCLE_TOKEN", "CIRCLECI_TOKEN", "CIRCLE_CI_TOKEN", "CIRCLECI_API_TOKEN")
                  if os.environ.get(k)), None)
    if not token:
        return {"status": "unverified",
                "detail": "no CircleCI token in env — cannot confirm the deploy. "
                          "This is NOT a pass; check the pipeline manually."}
    import urllib.error
    import urllib.request

    def api(path: str) -> dict:
        req = urllib.request.Request(f"https://circleci.com/api/v2/{path}",
                                     headers={"Circle-Token": token})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    def since():
        return None if shape.needs_pipeline_retrigger else _tag_commit_time(shape, tag)
    try:
        match, covered = _find_tag_pipeline(api, shape.slug, tag, since)
    except urllib.error.HTTPError as e:
        return {"status": "unverified", "detail": f"CircleCI returned {e.code}"}

    if not match and not covered and not shape.needs_pipeline_retrigger:
        return {"status": "unverified",
                "detail": f"no pipeline for {tag} in the newest {PIPELINE_PAGES} pages, and they "
                          f"do not reach back to the tag, so it may exist further back. Nothing "
                          f"was triggered; check CircleCI."}
    if not match:
        if shape.needs_pipeline_retrigger:
            return {"status": "no-pipeline",
                    "detail": f"no pipeline found for tag {tag}. This repo's CI builds "
                              f"from the default-branch pipeline, so this is expected — "
                              f"check that pipeline instead."}
        if trigger_missing:
            def api_post(path: str, body: dict) -> dict:
                req = urllib.request.Request(
                    f"https://circleci.com/api/v2/{path}", data=json.dumps(body).encode(),
                    headers={"Circle-Token": token, "Content-Type": "application/json"},
                    method="POST")
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.load(r)
            return _trigger_tag_pipeline(shape, tag, api_post)
        return {"status": "no-pipeline",
                "detail": f"no pipeline found for tag {tag}, and this repo's CI builds "
                          f"from tags, so nothing has built or deployed it (a lost GitHub "
                          f"event does this). Starting it deploys: ask the user, then re-run verify with "
                          f"--trigger-missing."}
    wfs = api(f"pipeline/{match['id']}/workflow")["items"]
    jobs = latest_job_statuses(wfs, lambda wf: api(f"workflow/{wf['id']}/job")["items"])
    relevant = {k: v for k, v in jobs.items() if k in shape.ci_jobs}
    # Classify by explicit sets, never by "not success". CircleCI has several
    # pending statuses beyond `running`, and treating them as failures reports a
    # healthy in-flight pipeline as a broken one — a false red that sends the
    # reader hunting a CI break that does not exist. `blocked` in particular is
    # the normal state of a fan-in job (test -> push) for as long as its upstream
    # is still going, so it shows up on essentially every release.
    PENDING = ("running", "blocked", "queued", "not_running", "on_hold")
    SKIPPED = ("not_run",)          # filtered out by config; not a failure
    bad = {k: v for k, v in relevant.items()
           if v not in ("success",) + PENDING + SKIPPED}
    status = ("failed" if bad
              else "running" if any(v in PENDING for v in relevant.values())
              else "success" if relevant and all(v == "success" for v in relevant.values())
              else "unknown")
    out = {"status": status, "pipeline": match.get("number"), "jobs": jobs,
           "problems": bad}
    # A green pipeline is the image being built and pushed, not the rollout.
    # Only ask once CI is actually green: before that "not-yet" is just noise.
    if status == "success":
        probe = serving_check(shape, tag)
        # `detail` belongs to verify's own first output line; a probe note must
        # never displace the CI job summary there.
        assert "detail" not in probe, "serving_check must not shadow verify's detail"
        out.update(probe)
    return out


def verify_lines(out: dict, tag: str) -> str:
    """The terminal rendering of `verify`: CI first, then rollout, then serving."""
    line = f"{out['status']}: {out.get('detail') or out.get('jobs')}"
    roll = out.get("rollout")
    if roll:
        where = f" — {out['execution']}" if out.get("execution") else ""
        label = {"complete": "rolled out", "failed": "ROLLOUT FAILED",
                 "running": "rolling out", "not-started": "rollout NOT STARTED",
                 }.get(roll, "rollout UNCONFIRMED")
        line += f"\n  {label}: {out.get('rollout_detail')}{where}"
        if out.get("rollout_note"):
            line += f"\n    note: {out['rollout_note']}"
    dep = out.get("deployed")
    if dep == "confirmed":
        line += f"\n  deployed: {tag} is serving traffic"
    elif dep == "not-serving":
        line += (f"\n  NOT SERVING: CI is green but traffic is on "
                 f"{out.get('serving')} — either the rollout has not "
                 f"landed, or this tag was superseded")
    elif dep == "unknown" and not (roll == "complete"
                                   and out.get("serving_probe") is False):
        # With a completed rollout, a repo that has no serving probe has said all
        # it can; repeating "unconfirmed" under it would contradict the line above.
        line += f"\n  deploy UNCONFIRMED: {out.get('deploy_detail')}"
    return line


def verify_exit(out: dict) -> int:
    """0 only for a healthy or in-flight CI with no failed rollout."""
    if out.get("rollout") == "failed":
        return 1
    return 0 if out["status"] in ("success", "running") else 1


# ---------------------------------------------------------------- cli


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "run", "status"):
        s = sub.add_parser(name)
        s.add_argument("repo")
        s.add_argument("--level", choices=["patch", "minor", "major"])
        s.add_argument("--version", dest="explicit_version")
        s.add_argument("--pre-release", dest="pre_release", metavar="ID")
        s.add_argument("--force", action="store_true",
                       help="release even with no new work since the last tag — "
                            "re-cuts the ref under a new version (identical image, "
                            "empty notes). The plan says so explicitly.")
        s.add_argument("--package", dest="only", action="append", metavar="NAME",
                       help="release only this package (repeatable). On a lerna repo every "
                            "other package is left untagged however much it changed")
        s.add_argument("--json", action="store_true")
        if name == "run":
            s.add_argument("--dry-run", action="store_true")
            s.add_argument("--expect-head", metavar="SHA",
                           help="the default-branch head the go approved (printed by plan). "
                                "run refuses to tag a commit carrying anything merged after it; "
                                "without it, run checks against its own plan's head")
    v = sub.add_parser("verify")
    v.add_argument("repo")
    v.add_argument("--tag", required=True)
    v.add_argument("--deployment-log", metavar="FILE",
                   help="a saved read of #deployment (Slack MCP output or "
                        "conversations.history JSON) to use instead of SLACK_BOT_TOKEN")
    v.add_argument("--trigger-missing", action="store_true",
                   help="when a tag-built repo has no pipeline for the tag, start one "
                        "(only once the tag is 5 minutes old)")
    v.add_argument("--json", action="store_true")

    a = ap.parse_args()
    try:
        shape = detect(a.repo)
        if a.cmd == "verify":
            log = (Path(a.deployment_log).read_text(encoding="utf-8")
                   if a.deployment_log else None)
            out = verify(shape, a.tag, deployment_log=log, trigger_missing=a.trigger_missing)
            if a.json:
                print(json.dumps(out, indent=2))
            else:
                print(verify_lines(out, a.tag))
            sys.exit(verify_exit(out))

        plan = build_plan(shape, level=a.level, explicit_version=a.explicit_version,
                          pre_release=a.pre_release, force=a.force, only=a.only)
        if a.cmd in ("plan", "status"):
            st = read_state(plan) if a.cmd == "status" else None
            if a.json:
                print(json.dumps(describe(plan, st), indent=2))
            else:
                print_plan(plan, st)
            sys.exit(0)

        out = run(plan, dry_run=a.dry_run, expect_head=a.expect_head)
        if a.json:
            print(json.dumps(out, indent=2))
        elif out["status"] == "dry-run":
            # A dry-run result carries no `tags`, so the generic line below rendered
            # as a bare "dry-run: " — nothing at all, in the one mode a human uses
            # --dry-run in. Print the plan it computed instead.
            print_plan(plan, read_state(plan))
            print("\ndry-run: nothing was written")
        else:
            print(f"\n{out['status']}: {', '.join(out.get('tags', []))}")
            # A `resumed` line alone still invites the reading that the caller's work
            # shipped, so name what did not. Printed on the terminal and not only in
            # --json, because the terminal is where a release is usually read.
            if out["status"] == "resumed":
                pending = out.get("unreleased") or {}
                print("  this FINISHED AN EXISTING release and shipped no new version")
                for name, subjects in pending.items():
                    if not subjects:
                        continue
                    print(f"  still unreleased in {name}:")
                    for s in subjects[:10]:
                        print(f"    - {s}")
                    if len(subjects) > 10:
                        print(f"    … and {len(subjects) - 10} more")
                if any(pending.values()):
                    print("  re-run to release them")
    except NothingToRelease as e:
        # Exit 3, not 2: a normal outcome, so a fleet sweep can tell it apart
        # from a repo that actually failed.
        if getattr(a, "json", False):
            print(json.dumps({"status": "nothing-to-release", "detail": str(e)}, indent=2))
        else:
            print(str(e))
        sys.exit(3)
    except (ReleaseError, ShapeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
