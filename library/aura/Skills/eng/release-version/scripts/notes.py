"""Release notes and version selection.

Two jobs, both deterministic:

  notes    render the commit list for a release, in release-it's own default
           format so release history stays consistent across the tooling change:
             git log --pretty=format:"* %s (%h)" <from>...<to>
           Reproduced verbatim rather than improved on. A release body that
           suddenly changes shape makes every prior release harder to read.

  version  propose the next version from conventional commits. A proposal only —
           the caller prints it and asks. Inferring silently would let a
           mislabelled commit ship a wrong major.

Stdlib only. Read-only.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

MAJOR, MINOR, PATCH = "major", "minor", "patch"

# A conventional-commit subject: type(optional scope)optional-! : description
_CC = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s")
# BREAKING CHANGE may appear as a footer token in the body.
_BREAKING = re.compile(r"(?m)^BREAKING[ -]CHANGE:")


def _run(args: list[str], cwd: Path) -> str:
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"{' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


def last_tag(cwd: Path, prefix: str = "v", ref: str | None = None) -> str | None:
    """The highest existing tag for a prefix, by version sort.

    Version sort, not creation date: a re-tagged or back-filled release would
    otherwise make an older version look like the latest, and the notes would
    then span the wrong range.

    `ref` scopes candidates to tags reachable from that ref. A tag on a branch
    that never landed is not a baseline for "commits since the last release" —
    the range then spans the whole divergence, so a fully-released repo looks
    like it has a release's worth of work. Prereleases are deliberately NOT
    filtered: an unreachable one is already excluded here, and a reachable one
    really did ship from this history.
    """
    args = ["git", "tag", "--list", f"{prefix}*", "--sort=-version:refname"]
    if ref:
        args += ["--merged", ref]
    out = _run(args, cwd)
    tags = [t for t in out.splitlines() if t.strip()]
    return tags[0] if tags else None


def render_notes(cwd: Path, from_ref: str | None, to_ref: str) -> str:
    """release-it's default changelog, byte-for-byte.

    With no previous tag, falls back to the full history to `to_ref` — the same
    thing release-it does on a first release.
    """
    fmt = "--pretty=format:* %s (%h)"
    rng = f"{from_ref}...{to_ref}" if from_ref else to_ref
    return _run(["git", "log", fmt, rng], cwd).strip()


@dataclass
class Commit:
    subject: str
    body: str

    @property
    def cc_type(self) -> str | None:
        m = _CC.match(self.subject)
        return m.group("type") if m else None

    @property
    def is_breaking(self) -> bool:
        m = _CC.match(self.subject)
        return bool(m and m.group("bang")) or bool(_BREAKING.search(self.body))


def read_commits(cwd: Path, from_ref: str | None, to_ref: str) -> list[Commit]:
    """Commits in the release range, subject + body, for level inference."""
    sep, end = "\x1f", "\x1e"
    rng = f"{from_ref}...{to_ref}" if from_ref else to_ref
    out = _run(["git", "log", f"--pretty=format:%s{sep}%b{end}", rng], cwd)
    commits = []
    for chunk in out.split(end):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        subject, _, body = chunk.partition(sep)
        commits.append(Commit(subject.strip(), body))
    return commits


def infer_level(commits: list[Commit]) -> tuple[str, str]:
    """(level, one-line reason). Defaults to patch — the safe direction.

    An under-called bump ships a correct artifact under a modest number; an
    over-called one claims a breaking change that did not happen.
    """
    if not commits:
        return PATCH, "no commits since the last tag"
    breaking = [c for c in commits if c.is_breaking]
    if breaking:
        return MAJOR, f"{len(breaking)} breaking commit(s), e.g. {breaking[0].subject!r}"
    feats = [c for c in commits if c.cc_type == "feat"]
    if feats:
        return MINOR, f"{len(feats)} feat commit(s), e.g. {feats[0].subject!r}"
    typed = [c for c in commits if c.cc_type]
    if typed:
        kinds = sorted({c.cc_type for c in typed})
        return PATCH, f"only {', '.join(kinds)} commit(s)"
    return PATCH, f"{len(commits)} commit(s), none conventional-commit typed"


def bump(version: str, level: str, pre_release: str | None = None) -> str:
    """Apply a level to a semver string.

    A pre_release identifier produces `<next>-<id>.0`, or increments the counter
    when already on a prerelease of the same identifier — matching what
    `release-it --preRelease=dev` produces.
    """
    core, _, pre = version.partition("-")
    parts = core.split(".")
    if len(parts) != 3 or not all(x.isdigit() for x in parts):
        raise ValueError(f"{version!r} is not a semver x.y.z")
    major, minor, patch = (int(x) for x in parts)

    if pre_release and pre.startswith(f"{pre_release}."):
        # Already on a prerelease of this identifier: increment its counter and
        # keep the core version, which is what release-it does.
        n = pre[len(pre_release) + 1:]
        return f"{core}-{pre_release}.{int(n) + 1}" if n.isdigit() else f"{core}-{pre_release}.0"

    if level == MAJOR:
        major, minor, patch = major + 1, 0, 0
    elif level == MINOR:
        minor, patch = minor + 1, 0
    elif level == PATCH:
        # A prerelease promoting to a release keeps its core version.
        if not pre:
            patch += 1
    else:
        raise ValueError(f"unknown level {level!r}")

    nxt = f"{major}.{minor}.{patch}"
    return f"{nxt}-{pre_release}.0" if pre_release else nxt
