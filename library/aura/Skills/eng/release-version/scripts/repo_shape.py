"""Resolve everything about a repo that a release depends on.

Answers four questions that the release flow must not guess at:

  slug            the GitHub owner/repo, read from the git remote — NOT from the
                  folder name. The two diverge, and a remote may even be a redirect
                  that resolves without erroring, so only the remote is authoritative.
  default_branch  read from the GitHub API. Both `master` and `main` are in use
                  across the fleet, and a ruleset's *name* is not a signal: a
                  ruleset literally named "main" routinely targets
                  ~DEFAULT_BRANCH over an actual default branch of `master`.
  shape           which of the four release shapes this repo is (see below).
  version files   where the version actually lives. For Functions repos the root
                  package.json has no `version` field at all — it is in
                  functions/package.json. Each package also exposes the
                  `package-lock.json` beside it, which carries a second copy of
                  the same version.

Stdlib only. Read-only: nothing here mutates a repo.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# The four release shapes. `node` is the default; the others are detected by
# positive evidence so an unrecognised repo fails loudly rather than being
# mis-released as a plain node repo.
SHAPE_NODE = "node"            # root package.json, tag v<x.y.z>
SHAPE_FUNCTIONS = "functions"  # version in functions/package.json, tag-only CI
SHAPE_PYTHON = "python"        # node package.json + pyproject.toml kept in sync
SHAPE_LERNA = "lerna"          # independent versioning, tag <pkg>@<x.y.z>


class ShapeError(RuntimeError):
    """The repo could not be resolved into a releasable shape."""


def _run(args: list[str], cwd: Path, check: bool = True) -> str:
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and p.returncode:
        raise ShapeError(f"{' '.join(args)} failed: {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout.strip()


def _json_or_none(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


@dataclass
class Package:
    """One independently-versioned unit. Non-lerna repos have exactly one."""

    name: str
    version: str
    # Every file whose version string must move together, and the JSON key path
    # or regex used to move it. package.json files use the "version" key;
    # pyproject.toml is matched by regex.
    version_files: list[Path]
    tag_prefix: str  # "v" for node/functions/python, "<pkg>@" for lerna
    # The package's own directory. Version files are edited in place — nothing here
    # shells out to `npm version`, so this is a location, not a working directory
    # for a package manager.
    directory: Path

    def tag_for(self, version: str) -> str:
        return f"{self.tag_prefix}{version}"

    @property
    def lock_files(self) -> list[Path]:
        """The `package-lock.json` beside each `package.json` this package versions.

        npm records the package's own version inside the lock as well, in two
        places — the top-level `version` and `packages[""].version` — so a bump
        that moves only package.json leaves the lock reporting the previous
        release.

        Kept out of `version_files` on purpose. Nothing resolves from those
        fields and no CI job reads them, so a lock that cannot be moved must not
        be able to block a tag the way a stale package.json rightly does.
        """
        out: list[Path] = []
        for f in self.version_files:
            if f.name != "package.json":
                continue
            lock = f.with_name("package-lock.json")
            if lock.is_file():
                out.append(lock)
        return out


@dataclass
class RepoShape:
    root: Path
    slug: str
    default_branch: str
    shape: str
    packages: list[Package]
    # Where CI reports success. Job names differ by shape, so a verifier that
    # hardcodes one set reports a false green on the other.
    ci_jobs: list[str] = field(default_factory=list)
    # 4U-engine builds from the DEFAULT-BRANCH pipeline and inspects tags at
    # HEAD, so the tags must exist before that pipeline runs. Under a PR flow
    # they do not, so the pipeline needs re-triggering after the tags land.
    # Tag-only CI (Functions) needs no re-trigger.
    needs_pipeline_retrigger: bool = False
    # A commit subject matching this pattern trips a CI guard. 4U-engine's push
    # job exits 1 on a /^Publish/ commit with no tags at HEAD.
    forbidden_commit_subject: str | None = None

    @property
    def is_multi_package(self) -> bool:
        return self.shape == SHAPE_LERNA


def resolve_slug(root: Path) -> str:
    """owner/repo from the git remote. Never derived from the folder name."""
    url = _run(["git", "remote", "get-url", "origin"], root)
    m = re.search(r"(?:[:/])([^/:]+/[^/]+?)(?:\.git)?$", url)
    if not m:
        raise ShapeError(f"could not parse a GitHub slug from remote {url!r}")
    return m.group(1)


def resolve_default_branch(slug: str, root: Path) -> str:
    """Ask GitHub. Falls back to the local origin/HEAD symref when gh is unavailable."""
    p = subprocess.run(
        ["gh", "api", f"repos/{slug}", "--jq", ".default_branch"],
        capture_output=True, text=True,
    )
    if p.returncode == 0 and p.stdout.strip():
        return p.stdout.strip()
    ref = _run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], root, check=False)
    if ref.startswith("origin/"):
        return ref[len("origin/"):]
    raise ShapeError(
        f"could not resolve the default branch for {slug}: gh said "
        f"{p.stderr.strip()!r} and there is no origin/HEAD symref"
    )


def _lerna_packages(root: Path, cfg: dict) -> list[Package]:
    globs = cfg.get("packages") or ["packages/*"]
    out: list[Package] = []
    for pattern in globs:
        for d in sorted(root.glob(pattern)):
            pkg = _json_or_none(d / "package.json")
            if not pkg or not pkg.get("name") or not pkg.get("version"):
                continue
            out.append(Package(
                name=pkg["name"],
                version=pkg["version"],
                version_files=[d / "package.json"],
                tag_prefix=f"{pkg['name']}@",
                directory=d,
            ))
    if not out:
        raise ShapeError(f"lerna repo {root} matched no versioned packages in {globs}")
    return out


def detect(repo_path: str | Path, *, resolve_remote: bool = True) -> RepoShape:
    """Resolve a repo into a RepoShape. Raises ShapeError when it cannot."""
    p = Path(repo_path).expanduser().resolve()
    if not p.is_dir():
        raise ShapeError(f"{p} is not a directory")
    root = Path(_run(["git", "rev-parse", "--show-toplevel"], p))

    slug = resolve_slug(root)
    if resolve_remote:
        default_branch = resolve_default_branch(slug, root)
    else:
        # Offline fallback: the branch actually checked out here. Guessing a
        # literal "main" would be wrong on this fleet, where `master` dominates.
        default_branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root)

    lerna_cfg = _json_or_none(root / "lerna.json")
    root_pkg = _json_or_none(root / "package.json")
    fn_pkg = _json_or_none(root / "functions" / "package.json")

    # lerna first: a lerna repo's root package.json also has no version, so
    # checking Functions first would misclassify it.
    if lerna_cfg is not None:
        return RepoShape(
            root=root, slug=slug, default_branch=default_branch, shape=SHAPE_LERNA,
            packages=_lerna_packages(root, lerna_cfg),
            ci_jobs=["test", "push"],
            needs_pipeline_retrigger=True,
            forbidden_commit_subject=r"^Publish",
        )

    # Functions: the root carries a delegating "release" script and no version;
    # the real version is nested. Require the nested version to be present so a
    # repo that merely happens to have a functions/ dir is not misread.
    if fn_pkg and fn_pkg.get("version") and not (root_pkg or {}).get("version"):
        return RepoShape(
            root=root, slug=slug, default_branch=default_branch, shape=SHAPE_FUNCTIONS,
            packages=[Package(
                name=fn_pkg.get("name") or root.name,
                version=fn_pkg["version"],
                version_files=[root / "functions" / "package.json"],
                tag_prefix="v",
                directory=root / "functions",
            )],
            # Tag-only CI: deploy -> dev, promote -> prod, both gated on
            # tags /^v.*/ with branches ignored. Nothing runs on a branch push,
            # so no re-trigger is needed.
            ci_jobs=["test", "deploy", "promote"],
        )

    if not root_pkg or not root_pkg.get("version"):
        raise ShapeError(
            f"{root} has no releasable version: no lerna.json, no versioned "
            f"functions/package.json, and no version in package.json"
        )

    # pyproject.toml alongside package.json means a second file carries the same
    # version and must move in the same commit.
    pyproject = root / "pyproject.toml"
    version_files = [root / "package.json"]
    shape = SHAPE_NODE
    if pyproject.is_file() and re.search(r'(?m)^version\s*=\s*"', pyproject.read_text(encoding="utf-8")):
        version_files.append(pyproject)
        shape = SHAPE_PYTHON

    return RepoShape(
        root=root, slug=slug, default_branch=default_branch, shape=shape,
        packages=[Package(
            name=root_pkg.get("name") or root.name,
            version=root_pkg["version"],
            version_files=version_files,
            tag_prefix="v",
            directory=root,
        )],
        ci_jobs=["test", "push"],
    )
