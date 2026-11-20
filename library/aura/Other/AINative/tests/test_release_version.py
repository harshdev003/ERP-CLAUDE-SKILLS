"""Tests for eng:release-version.

Stdlib unittest, no network, no API keys. Real temp git repos are created so the
shape detection and tag/commit logic run against actual git plumbing rather than
mocks; anything needing GitHub is called with resolve_remote=False or has its
subprocess chokepoint stubbed.

The branches worth testing are the ones real data cannot reach: a repo with no
releasable version, a Functions layout whose version is nested, a commit that
does not carry the bump it is about to be tagged with, and the commit subject
that trips 4U-engine's CI guard.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins/eng/skills/release-version/scripts"
sys.path.insert(0, str(SCRIPTS))

import notes as N  # noqa: E402
import release as R  # noqa: E402
import repo_shape as S  # noqa: E402


def git(args: list[str], cwd: Path) -> str:
    p = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    if p.returncode:
        raise AssertionError(f"git {' '.join(args)} failed: {p.stderr}")
    return p.stdout.strip()


def make_repo(tmp: Path, remote: str = "git@github.com:AuraHealth/Thing.git") -> Path:
    root = tmp / "repo"
    root.mkdir()
    git(["init", "-q", "-b", "master"], root)
    git(["config", "user.email", "t@t.t"], root)
    git(["config", "user.name", "T"], root)
    git(["remote", "add", "origin", remote], root)
    return root


class SeededReleases(unittest.TestCase):
    """Base for tests that call build_plan.

    build_plan asks GitHub which tags already have a release. Seeding the cache
    keeps the suite offline and makes the released-vs-tagged distinction explicit
    per test — that distinction is the thing under test in several of them.
    """

    SLUG = "AuraHealth/Thing"
    RELEASED: tuple[str, ...] = ()

    def setUp(self):
        R._RELEASED_TAGS.clear()
        R._RELEASED_TAGS[self.SLUG] = set(self.RELEASED)
        self.addCleanup(R._RELEASED_TAGS.clear)

    def seed(self, *tags: str) -> None:
        R._RELEASED_TAGS[self.SLUG] = set(tags)


def commit(root: Path, msg: str, body: str = "") -> str:
    (root / "f.txt").write_text(msg + body, encoding="utf-8")
    git(["add", "-A"], root)
    full = msg if not body else f"{msg}\n\n{body}"
    m = root / ".msg"
    m.write_text(full, encoding="utf-8")
    git(["commit", "-q", "-F", str(m)], root)
    m.unlink()
    return git(["rev-parse", "HEAD"], root)


class TestBump(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(N.bump("1.2.3", "patch"), "1.2.4")
        self.assertEqual(N.bump("1.2.3", "minor"), "1.3.0")
        self.assertEqual(N.bump("1.2.3", "major"), "2.0.0")

    def test_prerelease_starts_and_increments(self):
        self.assertEqual(N.bump("1.2.3", "patch", "dev"), "1.2.4-dev.0")
        self.assertEqual(N.bump("1.2.4-dev.0", "patch", "dev"), "1.2.4-dev.1")
        self.assertEqual(N.bump("1.2.4-dev.9", "patch", "dev"), "1.2.4-dev.10")

    def test_promoting_a_prerelease_keeps_the_core_version(self):
        # 1.2.4-dev.3 promoting to a real release is 1.2.4, not 1.2.5.
        self.assertEqual(N.bump("1.2.4-dev.3", "patch"), "1.2.4")

    def test_rejects_non_semver(self):
        for bad in ("1.2", "v1.2.3", "abc", "1.2.x"):
            with self.assertRaises(ValueError):
                N.bump(bad, "patch")


class TestInferLevel(unittest.TestCase):
    def c(self, subject, body=""):
        return N.Commit(subject, body)

    def test_feat_is_minor(self):
        lvl, _ = N.infer_level([self.c("fix: a"), self.c("feat: b")])
        self.assertEqual(lvl, "minor")

    def test_bang_is_major(self):
        lvl, _ = N.infer_level([self.c("feat!: drop it")])
        self.assertEqual(lvl, "major")

    def test_breaking_footer_is_major(self):
        lvl, _ = N.infer_level([self.c("fix: a", "BREAKING CHANGE: gone")])
        self.assertEqual(lvl, "major")

    def test_breaking_beats_feat(self):
        lvl, _ = N.infer_level([self.c("feat: a"), self.c("chore!: b")])
        self.assertEqual(lvl, "major")

    def test_defaults_to_patch(self):
        # Untyped commits must not be read as features.
        for commits in ([], [self.c("some plain subject")], [self.c("chore: x")]):
            lvl, _ = N.infer_level(commits)
            self.assertEqual(lvl, "patch")

    def test_scoped_feat_still_minor(self):
        lvl, _ = N.infer_level([self.c("feat(config): x")])
        self.assertEqual(lvl, "minor")


class TestNotes(unittest.TestCase):
    def test_format_matches_release_it_default(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            commit(root, "chore: first")
            git(["tag", "v1.0.0"], root)
            commit(root, "fix: second")
            sha = git(["rev-parse", "--short", "HEAD"], root)
            out = N.render_notes(root, "v1.0.0", "HEAD")
            self.assertEqual(out, f"* fix: second ({sha})")

    def test_no_previous_tag_uses_full_history(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            commit(root, "feat: only")
            self.assertIn("feat: only", N.render_notes(root, None, "HEAD"))

    def test_last_tag_is_version_sorted_not_date_sorted(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            commit(root, "a")
            git(["tag", "v1.10.0"], root)
            commit(root, "b")
            git(["tag", "v1.9.0"], root)  # created later, lower version
            self.assertEqual(N.last_tag(root, "v"), "v1.10.0")

    def test_last_tag_respects_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            commit(root, "a")
            git(["tag", "v1.0.0"], root)
            git(["tag", "pkg@2.0.0"], root)
            self.assertEqual(N.last_tag(root, "v"), "v1.0.0")
            self.assertEqual(N.last_tag(root, "pkg@"), "pkg@2.0.0")
            self.assertIsNone(N.last_tag(root, "other@"))

    def test_last_tag_scoped_to_a_ref_skips_unreachable_tags(self):
        # A higher tag on a branch that never landed is not a release baseline.
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            commit(root, "a")
            git(["tag", "v1.0.0"], root)
            git(["checkout", "-q", "--orphan", "side"], root)
            commit(root, "feat: never merged")
            git(["tag", "v2.0.0"], root)
            git(["checkout", "-q", "master"], root)
            self.assertEqual(N.last_tag(root, "v"), "v2.0.0")            # unscoped
            self.assertEqual(N.last_tag(root, "v", ref="master"), "v1.0.0")

    def test_last_tag_keeps_a_reachable_prerelease(self):
        # A prerelease on this history really shipped from it, so it stays the
        # baseline — skipping it would re-announce everything it already carried,
        # and would break consecutive --pre-release builds.
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            commit(root, "a")
            git(["tag", "v1.0.0"], root)
            commit(root, "feat: b")
            git(["tag", "v1.1.0-dev.0"], root)
            self.assertEqual(N.last_tag(root, "v", ref="HEAD"), "v1.1.0-dev.0")


class TestShapeDetection(unittest.TestCase):
    def _detect(self, root):
        return S.detect(root, resolve_remote=False)

    def test_node_root_package(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.0.0"}))
            commit(root, "a")
            s = self._detect(root)
            self.assertEqual(s.shape, S.SHAPE_NODE)
            self.assertEqual(s.packages[0].tag_prefix, "v")
            self.assertFalse(s.needs_pipeline_retrigger)

    def test_functions_version_is_nested_and_root_has_none(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            # The real shape: root has a delegating release script and NO version.
            (root / "package.json").write_text(json.dumps(
                {"name": "n", "scripts": {"release": "cd functions && npm run release"}}))
            (root / "functions").mkdir()
            (root / "functions" / "package.json").write_text(
                json.dumps({"name": "n", "version": "2.11.5"}))
            commit(root, "a")
            s = self._detect(root)
            self.assertEqual(s.shape, S.SHAPE_FUNCTIONS)
            self.assertEqual(s.packages[0].version, "2.11.5")
            self.assertEqual(s.packages[0].version_files[0].name, "package.json")
            self.assertEqual(s.packages[0].version_files[0].parent.name, "functions")
            self.assertEqual(s.packages[0].directory.name, "functions")
            # Tag-only CI: nothing runs on a branch push, so no re-trigger.
            self.assertFalse(s.needs_pipeline_retrigger)
            self.assertIn("promote", s.ci_jobs)

    def test_python_shape_carries_both_version_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.4.2"}))
            (root / "pyproject.toml").write_text('[project]\nversion = "1.4.2"\n')
            commit(root, "a")
            s = self._detect(root)
            self.assertEqual(s.shape, S.SHAPE_PYTHON)
            self.assertEqual({f.name for f in s.packages[0].version_files},
                             {"package.json", "pyproject.toml"})

    def test_lerna_wins_over_functions_heuristic(self):
        # A lerna root also has no version, so ordering matters.
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "package.json").write_text(json.dumps({"name": "root", "private": True}))
            (root / "lerna.json").write_text(json.dumps(
                {"version": "independent", "packages": ["models/*"]}))
            (root / "functions").mkdir()
            (root / "functions" / "package.json").write_text(
                json.dumps({"name": "decoy", "version": "9.9.9"}))
            for name, ver in (("a-model", "1.2.3"), ("b-model", "4.5.6")):
                d = root / "models" / name
                d.mkdir(parents=True)
                (d / "package.json").write_text(json.dumps({"name": name, "version": ver}))
            commit(root, "a")
            s = self._detect(root)
            self.assertEqual(s.shape, S.SHAPE_LERNA)
            self.assertEqual([p.name for p in s.packages], ["a-model", "b-model"])
            self.assertEqual(s.packages[0].tag_prefix, "a-model@")
            self.assertTrue(s.needs_pipeline_retrigger)
            self.assertEqual(s.forbidden_commit_subject, r"^Publish")

    def test_lerna_skips_dirs_without_a_package_json(self):
        # 4U-engine has leftover __pycache__/venv dirs under models/.
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "package.json").write_text(json.dumps({"name": "root"}))
            (root / "lerna.json").write_text(json.dumps({"packages": ["models/*"]}))
            (root / "models" / "real").mkdir(parents=True)
            (root / "models" / "real" / "package.json").write_text(
                json.dumps({"name": "real", "version": "1.0.0"}))
            (root / "models" / "__pycache__").mkdir()
            commit(root, "a")
            s = self._detect(root)
            self.assertEqual([p.name for p in s.packages], ["real"])

    def test_unreleasable_repo_fails_loudly(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "package.json").write_text(json.dumps({"name": "n"}))  # no version
            commit(root, "a")
            with self.assertRaises(S.ShapeError):
                self._detect(root)

    def test_lerna_with_no_versioned_packages_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "lerna.json").write_text(json.dumps({"packages": ["models/*"]}))
            commit(root, "a")
            with self.assertRaises(S.ShapeError):
                self._detect(root)

    def test_slug_comes_from_the_remote_not_the_folder(self):
        # Data/DataJobs is AuraHealth/AuraDataJobs.
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td), remote="https://github.com/AuraHealth/AuraDataJobs.git")
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.0.0"}))
            commit(root, "a")
            self.assertEqual(self._detect(root).slug, "AuraHealth/AuraDataJobs")

    def test_slug_parses_ssh_and_https_and_no_suffix(self):
        for remote, want in (
            ("git@github.com:AuraHealth/X.git", "AuraHealth/X"),
            ("https://github.com/AuraHealth/X.git", "AuraHealth/X"),
            ("https://github.com/AuraHealth/X", "AuraHealth/X"),
        ):
            with tempfile.TemporaryDirectory() as td:
                root = make_repo(Path(td), remote=remote)
                (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.0.0"}))
                commit(root, "a")
                self.assertEqual(self._detect(root).slug, want, remote)


class TestWorksAfterReleaseItIsRemoved(SeededReleases):
    """The post-cleanup repo state: no release scripts, no release-it, no lerna.

    ENG-1074 strips those from all 36 repos. Detection must key on version fields
    and git facts only — never on a `release` script or a devDependency — or the
    skill breaks at the moment the old tooling is removed.
    """

    RELEASED = ("v1.0.0",)

    def test_node_repo_with_no_release_script_or_devdeps(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            # Exactly what a cleaned-up repo looks like: no scripts.release,
            # no release-it, no .release-it.json.
            (root / "package.json").write_text(json.dumps(
                {"name": "n", "version": "1.0.0",
                 "scripts": {"test": "jest", "lint": "eslint ."},
                 "devDependencies": {"eslint": "^9"}}))
            commit(root, "chore: base")
            git(["tag", "v1.0.0"], root)
            commit(root, "fix: work")
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            s = S.detect(root, resolve_remote=False)
            self.assertEqual(s.shape, S.SHAPE_NODE)
            plan = R.build_plan(s)
            self.assertEqual(plan.primary.proposed, "1.0.1")

    def test_functions_repo_with_the_delegating_shortcut_removed(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            # Root keeps no release shortcut at all — only the missing `version`
            # field distinguishes this shape, which is what detection uses.
            (root / "package.json").write_text(json.dumps(
                {"name": "n", "scripts": {"update": "ncu -u"}}))
            (root / "functions").mkdir()
            (root / "functions" / "package.json").write_text(json.dumps(
                {"name": "n", "version": "2.11.5", "scripts": {"lint": "eslint ."}}))
            commit(root, "chore: base")
            s = S.detect(root, resolve_remote=False)
            self.assertEqual(s.shape, S.SHAPE_FUNCTIONS)
            self.assertEqual(s.packages[0].version, "2.11.5")

    def test_lerna_shape_survives_removing_the_lerna_devdependency(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            # lerna.json stays; the lerna devDependency and release script are gone.
            (root / "package.json").write_text(json.dumps({"name": "root", "private": True}))
            (root / "lerna.json").write_text(json.dumps({"packages": ["m/*"]}))
            d = root / "m" / "a"
            d.mkdir(parents=True)
            (d / "package.json").write_text(json.dumps({"name": "a", "version": "1.0.0"}))
            commit(root, "chore: base")
            s = S.detect(root, resolve_remote=False)
            self.assertEqual(s.shape, S.SHAPE_LERNA)
            self.assertEqual([p.name for p in s.packages], ["a"])

    def test_deleting_lerna_json_breaks_detection_loudly_not_silently(self):
        # The one file the cleanup must keep. If it goes, the repo must fail to
        # resolve rather than be mis-released as a single-package repo.
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "package.json").write_text(json.dumps({"name": "root", "private": True}))
            d = root / "m" / "a"
            d.mkdir(parents=True)
            (d / "package.json").write_text(json.dumps({"name": "a", "version": "1.0.0"}))
            commit(root, "chore: base")
            with self.assertRaises(S.ShapeError):
                S.detect(root, resolve_remote=False)


class TestWriteVersion(unittest.TestCase):
    def test_package_json_formatting_survives(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "package.json"
            p.write_text('{\n  "name": "n",\n  "version": "1.0.0",\n  "z": 1\n}\n')
            R._write_version(p, "1.0.1")
            self.assertEqual(p.read_text(), '{\n  "name": "n",\n  "version": "1.0.1",\n  "z": 1\n}\n')

    def test_only_the_first_version_key_moves(self):
        # A nested dependency pinned to a version must not be rewritten.
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "package.json"
            p.write_text('{\n  "version": "1.0.0",\n  "d": {\n    "version": "9.9.9"\n  }\n}\n')
            R._write_version(p, "1.0.1")
            txt = p.read_text()
            self.assertIn('"version": "1.0.1"', txt)
            self.assertIn('"version": "9.9.9"', txt)

    def test_pyproject(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "pyproject.toml"
            p.write_text('[project]\nname = "x"\nversion = "1.4.2"\n')
            R._write_version(p, "1.4.3")
            self.assertIn('version = "1.4.3"', p.read_text())

    def test_missing_version_key_raises(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "package.json"
            p.write_text('{"name": "n"}')
            with self.assertRaises(R.ReleaseError):
                R._write_version(p, "1.0.1")

    def test_unknown_file_raises(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "Cargo.toml"
            p.write_text("version = \"1\"")
            with self.assertRaises(R.ReleaseError):
                R._write_version(p, "1.0.1")


LOCK = (
    '{\n'
    '  "name": "n",\n'
    '  "version": "1.0.0",\n'
    '  "lockfileVersion": 3,\n'
    '  "packages": {\n'
    '    "": {\n'
    '      "name": "n",\n'
    '      "version": "1.0.0",\n'
    '      "dependencies": {\n'
    '        "dep": "1.0.0"\n'
    '      }\n'
    '    },\n'
    '    "node_modules/dep": {\n'
    '      "version": "1.0.0"\n'
    '    }\n'
    '  }\n'
    '}\n'
)


class TestWriteLockVersion(unittest.TestCase):
    """The lock keeps its own copy of the version, which `npm version` moves for
    free. Editing package.json in place has to move it explicitly, or every
    release ships a lock reporting the version before it."""

    def lock(self, td: str, text: str = LOCK) -> Path:
        p = Path(td) / "package-lock.json"
        p.write_text(text, encoding="utf-8")
        return p

    def test_both_copies_of_the_version_move(self):
        with tempfile.TemporaryDirectory() as td:
            p = self.lock(td)
            self.assertIsNone(R._write_lock_version(p, "1.0.1"))
            d = json.loads(p.read_text())
            self.assertEqual(d["version"], "1.0.1")
            self.assertEqual(d["packages"][""]["version"], "1.0.1")

    def test_the_dependency_tree_is_untouched(self):
        # A release must ship the tree CI already tested, so only the two
        # version fields may differ.
        with tempfile.TemporaryDirectory() as td:
            p = self.lock(td)
            R._write_lock_version(p, "1.0.1")
            before = LOCK.splitlines()
            after = p.read_text().splitlines()
            self.assertEqual(len(before), len(after))
            moved = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
            self.assertEqual(len(moved), 2, [after[i] for i in moved])
            self.assertEqual(json.loads(p.read_text())["packages"]["node_modules/dep"],
                             {"version": "1.0.0"})

    def test_formatting_is_reproduced_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as td:
            p = self.lock(td)
            R._write_lock_version(p, "1.0.0")      # same version: a pure round-trip
            self.assertEqual(p.read_text(), LOCK)

    def test_a_lock_without_its_own_version_is_left_alone(self):
        # A Functions repo's *root* lock, whose package.json has no version.
        with tempfile.TemporaryDirectory() as td:
            text = '{\n  "name": "n",\n  "lockfileVersion": 2\n}\n'
            p = self.lock(td, text)
            self.assertIn("no version", R._write_lock_version(p, "1.0.1"))
            self.assertEqual(p.read_text(), text)

    def test_a_lock_npm_did_not_write_is_reported_not_reformatted(self):
        with tempfile.TemporaryDirectory() as td:
            text = '{"name": "n", "version": "1.0.0"}'
            p = self.lock(td, text)
            self.assertIn("reformat", R._write_lock_version(p, "1.0.1"))
            self.assertEqual(p.read_text(), text)

    def test_an_unreadable_lock_is_reported_not_raised(self):
        # Nothing resolves from these fields, so a broken lock must not be able
        # to take a release down with it.
        with tempfile.TemporaryDirectory() as td:
            p = self.lock(td, "{not json")
            self.assertIn("unreadable", R._write_lock_version(p, "1.0.1"))

    def test_a_missing_lock_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(
                R._write_lock_version(Path(td) / "package-lock.json", "1.0.1"),
                "not present")


class TestLockFilesAreNotVersionFiles(unittest.TestCase):
    """`lock_files` is deliberately separate from `version_files`: the tag-safety
    check reads version_files, and a lock must never be able to block a tag."""

    def make(self, td: str, with_lock: bool = True) -> S.RepoShape:
        root = make_repo(Path(td))
        (root / "package.json").write_text('{\n  "name": "n",\n  "version": "1.0.0"\n}\n')
        if with_lock:
            (root / "package-lock.json").write_text(LOCK, encoding="utf-8")
        commit(root, "init")
        return S.detect(root, resolve_remote=False)

    def test_the_lock_is_found_beside_the_package_json(self):
        with tempfile.TemporaryDirectory() as td:
            pkg = self.make(td).packages[0]
            self.assertEqual([f.name for f in pkg.version_files], ["package.json"])
            self.assertEqual([f.name for f in pkg.lock_files], ["package-lock.json"])

    def test_no_lock_means_no_lock_files(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(self.make(td, with_lock=False).packages[0].lock_files, [])

    def test_a_functions_repo_finds_the_nested_lock_not_the_root_one(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "package.json").write_text('{"name": "r", "scripts": {}}')
            (root / "package-lock.json").write_text('{"name": "r", "lockfileVersion": 2}')
            fn = root / "functions"
            fn.mkdir()
            (fn / "package.json").write_text('{\n  "name": "f",\n  "version": "2.0.0"\n}\n')
            (fn / "package-lock.json").write_text(LOCK, encoding="utf-8")
            commit(root, "init")
            shape = S.detect(root, resolve_remote=False)
            pkg = shape.packages[0]
            self.assertEqual([str(f.relative_to(shape.root)) for f in pkg.lock_files],
                             ["functions/package-lock.json"])


class TestTagPushHook(unittest.TestCase):
    """The tag push keeps the pre-push hook, and bypasses it only when the hook
    failed on files no commit can carry — untracked paths in the main checkout."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.remote = tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        self.root = make_repo(tmp, remote=str(self.remote))
        (self.root / ".gitignore").write_text("node_modules/\n")
        (self.root / "index.js").write_text("module.exports = 1;\n")
        git(["add", "."], self.root)
        git(["commit", "-qm", "init"], self.root)
        git(["tag", "-a", "v1.0.1", "-m", "Release 1.0.1"], self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _hook(self, *reported: str, code: int = 1):
        """A pre-push hook that prints husky-style lint output and exits `code`."""
        lines = "".join(f'echo "{self.root.resolve() / r}"\n'
                        f'echo "  1:1  error  something  no-undef"\n' for r in reported)
        hook = self.root / ".git/hooks/pre-push"
        hook.write_text(f"#!/bin/sh\n{lines}"
                        f'echo "husky - pre-push script failed (code {code})" >&2\n'
                        f"exit {code}\n")
        hook.chmod(0o755)

    def _remote_has(self, tag):
        return bool(git(["ls-remote", "--tags", str(self.remote), tag], self.root))

    def test_passing_hook_pushes_normally(self):
        self._hook(code=0)
        self.assertIsNone(R._push_tag(self.root, "v1.0.1"))
        self.assertTrue(self._remote_has("v1.0.1"))

    def test_failure_only_on_untracked_copy_is_bypassed_and_reported(self):
        stray = self.root / "Thing/.claude/worktrees/token-audit/test"
        stray.mkdir(parents=True)
        (stray / "a.spec.js").write_text("x\n")
        self._hook("Thing/.claude/worktrees/token-audit/test/a.spec.js")
        note = R._push_tag(self.root, "v1.0.1")
        self.assertTrue(self._remote_has("v1.0.1"))
        self.assertIn("--no-verify", note)
        self.assertIn("Thing/", note)

    def test_failure_on_a_tracked_file_stops_the_release(self):
        stray = self.root / "Thing"
        stray.mkdir()
        (stray / "a.js").write_text("x\n")
        self._hook("Thing/a.js", "index.js")
        with self.assertRaises(R.ReleaseError):
            R._push_tag(self.root, "v1.0.1")
        self.assertFalse(self._remote_has("v1.0.1"))

    def test_failure_naming_no_file_stops_the_release(self):
        (self.root / "Thing").mkdir()
        (self.root / "Thing/a.js").write_text("x\n")
        self._hook()
        with self.assertRaises(R.ReleaseError):
            R._push_tag(self.root, "v1.0.1")
        self.assertFalse(self._remote_has("v1.0.1"))


class TestTagSafety(unittest.TestCase):
    """The guard that makes 'tag only after the merge' real."""

    def _shape_and_plan(self, root, version="1.0.0"):
        shape = S.detect(root, resolve_remote=False)
        plan = R.Plan(
            shape=shape,
            packages=[R.PackagePlan(
                pkg=shape.packages[0], current=version, proposed="1.0.1",
                level="patch", reason="t", last_tag="v1.0.0", body="* x",
            )],
            branch="chore/release-1.0.1", commit_subject="Release 1.0.1",
        )
        return shape, plan

    def test_refuses_to_tag_a_commit_without_the_bump(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.0.0"}))
            sha = commit(root, "a")  # still 1.0.0 — the bump never landed
            shape, plan = self._shape_and_plan(root)
            with self.assertRaises(R.ReleaseError) as cm:
                R._verify_bump_in_commit(shape, sha, plan)
            self.assertIn("does not contain 1.0.1", str(cm.exception))

    def test_accepts_a_commit_carrying_the_bump(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.0.0"}))
            commit(root, "a")
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.0.1"}))
            sha = commit(root, "Release 1.0.1")
            shape, plan = self._shape_and_plan(root)
            R._verify_bump_in_commit(shape, sha, plan)  # must not raise

    def test_python_shape_checks_both_files(self):
        # pyproject left behind is the LivekitVoiceAgent failure: the image would
        # report a version it is not.
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.0.1"}))
            (root / "pyproject.toml").write_text('version = "1.0.0"\n')  # not bumped
            sha = commit(root, "Release 1.0.1")
            shape, plan = self._shape_and_plan(root)
            with self.assertRaises(R.ReleaseError) as cm:
                R._verify_bump_in_commit(shape, sha, plan)
            self.assertIn("pyproject.toml", str(cm.exception))


class TestBaselineComesFromTheRef(SeededReleases):
    RELEASED = ("v1.0.0", "v2.0.0")

    """The bump baseline must come from the ref being released, not the checkout.

    A stale or feature-branch checkout otherwise produces a baseline behind what
    landed, and the bump computed from it can downgrade past a shipped version.
    """

    def _repo(self, td):
        root = make_repo(Path(td))
        (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.0.0"}))
        commit(root, "chore: base")
        git(["tag", "v1.0.0"], root)
        return root

    def test_version_at_ref_reads_the_ref_not_the_worktree(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td)
            # default branch moves to 2.0.0
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "2.0.0"}))
            commit(root, "feat: bump to 2.0.0")
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            # local checkout is left behind at 1.0.0
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.0.0"}))
            shape = S.detect(root, resolve_remote=False)
            self.assertEqual(shape.packages[0].version, "1.0.0")  # worktree
            self.assertEqual(
                R._version_at_ref(shape, shape.packages[0], "origin/master"), "2.0.0")

    def test_stale_checkout_is_warned_about(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td)
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "2.0.0"}))
            commit(root, "feat: bump to 2.0.0")
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.0.0"}))
            plan = R.build_plan(S.detect(root, resolve_remote=False), level="patch")
            self.assertEqual(plan.primary.current, "2.0.0")  # from the ref, not 1.0.0
            self.assertTrue(any("out of date" in w for w in plan.warnings))
            # An untagged 2.0.0 on the ref is the in-flight shape, so the release
            # is v2.0.0 — bumping to 2.0.1 would skip 2.0.0 entirely.
            self.assertEqual(plan.primary.proposed, "2.0.0")

    def test_bump_uses_the_ref_baseline_not_the_stale_local_one(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td)
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "2.0.0"}))
            commit(root, "chore: release 2.0.0")
            git(["tag", "v2.0.0"], root)          # 2.0.0 is fully released
            commit(root, "fix: later work")
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.0.0"}))
            plan = R.build_plan(S.detect(root, resolve_remote=False), level="patch")
            # 2.0.1 from the ref baseline — NOT 1.0.1, which would downgrade past
            # a version already shipped.
            self.assertEqual(plan.primary.current, "2.0.0")
            self.assertEqual(plan.primary.proposed, "2.0.1")

    def test_missing_at_ref_falls_back_to_the_local_value(self):
        # A package added locally and never shipped has no version on the ref.
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "0.1.0"}))
            commit(root, "init")
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            shape = S.detect(root, resolve_remote=False)
            self.assertEqual(
                R._version_at_ref(shape, shape.packages[0], "origin/master"), "0.1.0")


class TestInFlightReleaseIsResumedNotSuperseded(SeededReleases):
    RELEASED = ("v1.7.8",)

    """The merge boundary: the ref carries the bump before the tag exists.

    Taking the baseline from the ref makes that state look like "1.7.9 is the
    current version, so release 1.7.10" — which would leave the merged 1.7.9
    forever untagged and undeployed, while a re-run silently started a new
    release. The unfinished release has to be finished instead.
    """

    def _repo_mid_release(self, td):
        root = make_repo(Path(td))
        (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.7.8"}))
        commit(root, "chore: base")
        git(["tag", "v1.7.8"], root)
        commit(root, "fix: a feature")
        # the release PR merged: ref now holds 1.7.9, but no v1.7.9 tag yet
        (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.7.9"}))
        commit(root, "Release 1.7.9")
        git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
        return root

    def test_resumes_the_untagged_version_instead_of_bumping_past_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_mid_release(td)
            plan = R.build_plan(S.detect(root, resolve_remote=False))
            self.assertEqual(plan.primary.proposed, "1.7.9")  # not 1.7.10
            self.assertEqual(plan.primary.level, "resume")
            self.assertEqual(plan.primary.tag, "v1.7.9")
            self.assertIn("finishing that release", plan.primary.reason)

    def test_branch_name_matches_the_resumed_version(self):
        # read_state keys off the target, so a recomputed target would miss the
        # in-flight PR, tags and releases entirely.
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_mid_release(td)
            plan = R.build_plan(S.detect(root, resolve_remote=False))
            self.assertEqual(plan.branch, "chore/release-1.7.9")

    def test_fully_released_is_no_longer_in_flight(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_mid_release(td)
            git(["tag", "v1.7.9"], root)
            self.seed("v1.7.8", "v1.7.9")   # tag AND GitHub release both present
            # Done, and no new commits since -> nothing to release, not 1.7.10.
            with self.assertRaises(R.NothingToRelease):
                R.build_plan(S.detect(root, resolve_remote=False))

    def test_tagged_but_release_missing_is_still_in_flight(self):
        # The half-finished case: the tag push landed, the GitHub release did not.
        # Keying in-flight on the tag alone made this unreachable — the range was
        # empty so it raised NothingToRelease and reported exit 3 over a release
        # that still needed finishing.
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_mid_release(td)
            git(["tag", "v1.7.9"], root)
            self.seed("v1.7.8")            # tag exists, release does NOT
            plan = R.build_plan(S.detect(root, resolve_remote=False))
            self.assertEqual(plan.primary.proposed, "1.7.9")
            self.assertEqual(plan.primary.level, "resume")

    def test_explicit_version_still_wins_over_resume(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo_mid_release(td)
            plan = R.build_plan(S.detect(root, resolve_remote=False),
                                explicit_version="2.0.0")
            self.assertEqual(plan.primary.proposed, "2.0.0")

    def test_a_never_tagged_repo_is_not_treated_as_in_flight(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "0.1.0"}))
            commit(root, "fix: first")
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            plan = R.build_plan(S.detect(root, resolve_remote=False))
            self.assertEqual(plan.primary.proposed, "0.1.1")  # a normal bump
            self.assertNotEqual(plan.primary.level, "resume")


class TestEmptyReleaseIsRefused(SeededReleases):
    RELEASED = ("v1.0.0",)

    """A second run on a finished release must not cut an empty next version."""

    def test_no_commits_since_the_tag_is_nothing_to_release(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.0.0"}))
            commit(root, "chore: base")
            git(["tag", "v1.0.0"], root)
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            with self.assertRaises(R.NothingToRelease) as cm:
                R.build_plan(S.detect(root, resolve_remote=False))
            self.assertIn("no commits", str(cm.exception))

    def test_commits_since_the_tag_do_release(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.0.0"}))
            commit(root, "chore: base")
            git(["tag", "v1.0.0"], root)
            commit(root, "fix: something")
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            plan = R.build_plan(S.detect(root, resolve_remote=False))
            self.assertEqual(plan.primary.proposed, "1.0.1")


class TestOrphanTagIsNotTheBaseline(SeededReleases):
    RELEASED = ("v1.0.0",)

    """An abandoned branch's tag must not anchor the next release.

    `--sort=-version:refname` ranks by semver alone, so a higher tag on a branch
    that never landed wins — and the range then spans the whole divergence. The
    repo looks like it has a release's worth of work when it has none, and the
    level is inferred from commits that already shipped.
    """

    def _repo(self, td):
        root = make_repo(Path(td))
        (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.0.0"}))
        commit(root, "chore: base")
        git(["tag", "v1.0.0"], root)
        return root

    def _orphan(self, root):
        """A higher tag on history that is not an ancestor of master."""
        here = git(["rev-parse", "HEAD"], root)
        git(["checkout", "-q", "--orphan", "side"], root)
        commit(root, "feat: never merged")
        git(["tag", "v2.0.0-dev.0"], root)
        git(["checkout", "-q", "--force", "master"], root)
        git(["reset", "-q", "--hard", here], root)

    def test_fully_released_repo_is_nothing_to_release(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td)
            self._orphan(root)
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            # Without the ancestry filter the baseline is v2.0.0-dev.0, whose
            # range is non-empty, so this proposes a release of already-shipped work.
            with self.assertRaises(R.NothingToRelease):
                R.build_plan(S.detect(root, resolve_remote=False))

    def test_range_and_level_come_from_the_reachable_tag(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td)
            self._orphan(root)
            commit(root, "fix: the one unreleased commit")
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            plan = R.build_plan(S.detect(root, resolve_remote=False))
            self.assertEqual(plan.primary.last_tag, "v1.0.0")
            self.assertEqual(plan.primary.proposed, "1.0.1")   # patch, not minor
            self.assertIn("fix: the one unreleased commit", plan.primary.body)
            self.assertNotIn("never merged", plan.primary.body)


class TestForceReleasesAnEmptyRange(SeededReleases):
    """`--force` is the one deliberate override of the nothing-to-release guard.

    The branch that matters is the interaction with `explicit_version`: the guard
    runs BEFORE version resolution, so an explicit version alone cannot reach it.
    That asymmetry is easy to break by reordering and impossible to notice from a
    happy-path run, since both paths look like a normal release once past it.
    """

    RELEASED = ("v1.7.8",)

    def _finished(self, td):
        root = make_repo(Path(td))
        (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.7.8"}))
        commit(root, "chore: base")
        git(["tag", "v1.7.8"], root)
        git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
        return root

    def test_explicit_version_alone_does_not_bypass_the_guard(self):
        with tempfile.TemporaryDirectory() as td:
            shape = S.detect(self._finished(td), resolve_remote=False)
            with self.assertRaises(R.NothingToRelease):
                R.build_plan(shape, explicit_version="1.8.0")

    def test_force_releases_and_honours_the_explicit_version(self):
        with tempfile.TemporaryDirectory() as td:
            shape = S.detect(self._finished(td), resolve_remote=False)
            plan = R.build_plan(shape, explicit_version="1.8.0", force=True)
            self.assertEqual(plan.primary.proposed, "1.8.0")
            self.assertEqual(plan.primary.current, "1.7.8")

    def test_force_without_a_version_bumps_patch(self):
        with tempfile.TemporaryDirectory() as td:
            shape = S.detect(self._finished(td), resolve_remote=False)
            plan = R.build_plan(shape, force=True)
            self.assertEqual(plan.primary.proposed, "1.7.9")

    def test_the_plan_says_it_was_forced(self):
        """A forced no-op must not be indistinguishable from a real release."""
        with tempfile.TemporaryDirectory() as td:
            shape = S.detect(self._finished(td), resolve_remote=False)
            plan = R.build_plan(shape, explicit_version="1.8.0", force=True)
            joined = " ".join(plan.warnings)
            self.assertIn("FORCED", joined)
            self.assertIn("identical", joined)

    def test_force_does_not_fire_the_warning_when_there_is_real_work(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._finished(td)
            commit(root, "fix: real work")
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            plan = R.build_plan(S.detect(root, resolve_remote=False), force=True)
            self.assertNotIn("FORCED", " ".join(plan.warnings))
            self.assertEqual(plan.primary.proposed, "1.7.9")

    def test_force_onto_an_already_released_version_is_refused(self):
        """The plan must not promise work that `run` will skip.

        `run` reads the same derived state, sees the tag and the GitHub release
        present, and returns `already-released` without bumping or tagging. A plan
        that showed the bump steps anyway would be a false success at the approval
        gate — so build_plan refuses rather than letting it through.
        """
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.8.0"}))
            commit(root, "chore: base")
            git(["tag", "v1.8.0"], root)
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            self.seed("v1.7.8", "v1.8.0")   # 1.8.0 shipped: tag AND release present
            shape = S.detect(root, resolve_remote=False)
            with self.assertRaises(R.ReleaseError) as cm:
                R.build_plan(shape, explicit_version="1.8.0", force=True)
            msg = str(cm.exception)
            self.assertIn("already fully released", msg)
            self.assertIn("rerun-circleci", msg)      # points at the tool that does work
            # and it must be a genuine error (exit 2), not nothing-to-release (exit 3)
            self.assertNotIsInstance(cm.exception, R.NothingToRelease)

    def test_force_onto_a_new_version_still_works_on_that_repo(self):
        """The refusal above must not block the legitimate forced release."""
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.8.0"}))
            commit(root, "chore: base")
            git(["tag", "v1.8.0"], root)
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            self.seed("v1.7.8", "v1.8.0")   # same shipped state as the test above
            plan = R.build_plan(S.detect(root, resolve_remote=False),
                                explicit_version="1.9.0", force=True)
            self.assertEqual(plan.primary.proposed, "1.9.0")

    def test_force_does_not_bypass_a_genuine_error(self):
        """It answers "nothing changed", not "something is wrong"."""
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "README.md").write_text("no version anywhere")
            commit(root, "chore: base")
            with self.assertRaises(S.ShapeError):
                S.detect(root, resolve_remote=False)


class TestResumeFromMirrorsRun(SeededReleases):
    RELEASED = ()

    """A reported resume step that disagrees with run() is worse than none."""

    def _plan(self, root):
        shape = S.detect(root, resolve_remote=False)
        return R.Plan(shape=shape, packages=[R.PackagePlan(
            pkg=shape.packages[0], current="1.0.0", proposed="1.0.1", level="patch",
            reason="t", last_tag="v1.0.0", body="* x")],
            branch="chore/release-1.0.1", commit_subject="Release 1.0.1")

    def test_pushed_branch_without_a_pr_resumes_from_the_pr_step(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.0.0"}))
            commit(root, "a")
            plan = self._plan(root)
            st = R.State(branch_pushed=True)
            self.assertEqual(R.describe(plan, st)["state"]["resume_from"], "pull request")

    def test_each_state_maps_to_its_step(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.0.0"}))
            commit(root, "a")
            plan = self._plan(root)
            for st, want in (
                (R.State(), "bump"),
                (R.State(branch_pushed=True), "pull request"),
                (R.State(branch_pushed=True, open_pr=7), "merge"),
                (R.State(merged_sha="abc", tags_missing=["v1.0.1"]), "tag"),
                (R.State(tags_pushed=["v1.0.1"]), "github release"),
                (R.State(tags_pushed=["v1.0.1"], releases_present=["v1.0.1"]),
                 "nothing — already released"),
            ):
                self.assertEqual(R.describe(plan, st)["state"]["resume_from"], want, want)


class TestLernaChangeDetectionUsesTheRef(SeededReleases):
    RELEASED = ("a@1.0.0",)

    def _lerna(self, td):
        root = make_repo(Path(td))
        (root / "lerna.json").write_text(json.dumps({"packages": ["m/*"]}))
        d = root / "m" / "a"
        d.mkdir(parents=True)
        (d / "package.json").write_text(json.dumps({"name": "a", "version": "1.0.0"}))
        (d / "index.js").write_text("//\n")
        commit(root, "init")
        git(["tag", "a@1.0.0"], root)
        git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
        return root, S.detect(root, resolve_remote=False)

    def test_a_change_only_on_local_head_does_not_count(self):
        # Committed locally but never landed on the default branch.
        with tempfile.TemporaryDirectory() as td:
            root, shape = self._lerna(td)
            (root / "m" / "a" / "index.js").write_text("// local only\n")
            commit(root, "fix: local only")
            self.assertFalse(
                R._package_changed(shape, shape.packages[0], "a@1.0.0", "origin/master"))

    def test_a_change_on_the_ref_does_count(self):
        with tempfile.TemporaryDirectory() as td:
            root, shape = self._lerna(td)
            (root / "m" / "a" / "index.js").write_text("// landed\n")
            commit(root, "fix: landed")
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            self.assertTrue(
                R._package_changed(shape, shape.packages[0], "a@1.0.0", "origin/master"))


class TestResumeCoversEveryStep(unittest.TestCase):
    def test_branch_has_bump_distinguishes_committed_from_noop(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.0.1"}))
            commit(root, "Release 1.0.1")
            shape = S.detect(root, resolve_remote=False)
            plan = R.Plan(
                shape=shape,
                packages=[R.PackagePlan(
                    pkg=shape.packages[0], current="1.0.0", proposed="1.0.1",
                    level="patch", reason="t", last_tag=None, body="* x")],
                branch="chore/release-1.0.1", commit_subject="Release 1.0.1")
            # The bump is committed on this branch -> resume, don't error.
            self.assertTrue(R._branch_has_bump(shape, plan, root))
            # A different target is genuinely absent.
            plan.packages[0].proposed = "9.9.9"
            self.assertFalse(R._branch_has_bump(shape, plan, root))

    def test_state_exposes_branch_pushed_so_resume_can_use_it(self):
        # The pushed-branch-but-no-PR gap: State must carry the signal.
        st = R.State(branch_pushed=True, open_pr=None, merged_sha=None)
        self.assertTrue(st.branch_pushed)
        self.assertFalse(st.done)


class TestReleaseLookupFailsClosed(SeededReleases):
    """An unknowable release state must never be read as "no release".

    Treating a failed listing as empty makes the current version look unreleased,
    so it is resumed rather than bumped — and the run then reports
    already-released while silently skipping commits that needed shipping.
    """

    def _shape(self, td):
        root = make_repo(Path(td))
        (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.0.0"}))
        commit(root, "a")
        return S.detect(root, resolve_remote=False)

    def test_failed_listing_is_not_an_empty_set(self):
        R._RELEASED_TAGS.clear()
        real = subprocess.run

        def boom(args, **kw):
            if args[:3] == ["gh", "release", "list"]:
                return subprocess.CompletedProcess(args, 1, "", "HTTP 500 upstream")
            return real(args, **kw)

        with tempfile.TemporaryDirectory() as td:
            shape = self._shape(td)
            subprocess.run = boom
            try:
                self.assertIsNone(R._released_tags(shape))
            finally:
                subprocess.run = real

    def test_saturated_listing_is_not_trustworthy(self):
        # At the limit, older releases may be missing, so absence is unprovable.
        R._RELEASED_TAGS.clear()
        real = subprocess.run
        rows = json.dumps([{"tagName": f"v0.0.{i}"} for i in range(R._LIST_LIMIT)])

        def saturated(args, **kw):
            if args[:3] == ["gh", "release", "list"]:
                return subprocess.CompletedProcess(args, 0, rows, "")
            return real(args, **kw)

        with tempfile.TemporaryDirectory() as td:
            shape = self._shape(td)
            subprocess.run = saturated
            try:
                self.assertIsNone(R._released_tags(shape))
            finally:
                subprocess.run = real

    def test_a_non_404_lookup_error_raises_rather_than_guessing(self):
        R._RELEASED_TAGS.clear()
        real = subprocess.run

        def failing(args, **kw):
            if args[:3] == ["gh", "release", "list"]:
                return subprocess.CompletedProcess(args, 1, "", "boom")
            if args[:3] == ["gh", "release", "view"]:
                return subprocess.CompletedProcess(args, 1, "", "HTTP 403 forbidden")
            return real(args, **kw)

        with tempfile.TemporaryDirectory() as td:
            shape = self._shape(td)
            subprocess.run = failing
            try:
                with self.assertRaises(R.ReleaseError) as cm:
                    R._release_exists(shape, "v1.0.0")
                self.assertIn("Refusing to guess", str(cm.exception))
            finally:
                subprocess.run = real

    def test_a_404_means_genuinely_no_release(self):
        R._RELEASED_TAGS.clear()
        real = subprocess.run

        def not_found(args, **kw):
            if args[:3] == ["gh", "release", "list"]:
                return subprocess.CompletedProcess(args, 1, "", "boom")
            if args[:3] == ["gh", "release", "view"]:
                return subprocess.CompletedProcess(args, 1, "", "release not found")
            return real(args, **kw)

        with tempfile.TemporaryDirectory() as td:
            shape = self._shape(td)
            subprocess.run = not_found
            try:
                self.assertFalse(R._release_exists(shape, "v1.0.0"))
            finally:
                subprocess.run = real


class TestLernaResumesAHalfDoneRelease(SeededReleases):
    """The in_flight rule must apply to lerna too, not only single-package repos."""

    RELEASED = ()

    def _lerna_mid_release(self, td):
        root = make_repo(Path(td))
        (root / "lerna.json").write_text(json.dumps({"packages": ["m/*"]}))
        d = root / "m" / "a"
        d.mkdir(parents=True)
        (d / "package.json").write_text(json.dumps({"name": "a", "version": "1.0.0"}))
        (d / "index.js").write_text("//\n")
        commit(root, "init")
        git(["tag", "a@1.0.0"], root)
        # work lands, then the release bump merges and the tag is pushed…
        (d / "index.js").write_text("// work\n")
        commit(root, "fix: work")
        (d / "package.json").write_text(json.dumps({"name": "a", "version": "1.0.1"}))
        commit(root, "Release a@1.0.1")
        git(["tag", "a@1.0.1"], root)
        git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
        return root

    def test_tags_landed_but_release_missing_still_resumes(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._lerna_mid_release(td)
            self.seed("a@1.0.0")          # a@1.0.1 tagged but NOT released
            plan = R.build_plan(S.detect(root, resolve_remote=False))
            self.assertEqual(plan.primary.proposed, "1.0.1")
            self.assertEqual(plan.primary.level, "resume")

    def test_fully_released_lerna_has_nothing_to_release(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._lerna_mid_release(td)
            self.seed("a@1.0.0", "a@1.0.1")
            with self.assertRaises(R.NothingToRelease):
                R.build_plan(S.detect(root, resolve_remote=False))

    def test_zero_release_repo_resumes_rather_than_being_treated_as_done(self):
        # One uniform rule: a tag without a release is unfinished, even on a repo
        # that has no releases at all. An empty listing cannot distinguish "never
        # used releases" from "first release interrupted before gh release create",
        # so neither reading is special-cased.
        with tempfile.TemporaryDirectory() as td:
            root = self._lerna_mid_release(td)
            self.seed()   # zero releases anywhere
            plan = R.build_plan(S.detect(root, resolve_remote=False))
            self.assertEqual(plan.primary.proposed, "1.0.1")
            self.assertEqual(plan.primary.level, "resume")

    def test_it_self_heals_once_the_release_exists(self):
        # After the resume creates the release, new work bumps normally — the
        # backfill is one run per package, not a permanent block.
        with tempfile.TemporaryDirectory() as td:
            root = self._lerna_mid_release(td)
            (root / "m" / "a" / "index.js").write_text("// more work\n")
            commit(root, "fix: more work")
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            self.seed("a@1.0.0", "a@1.0.1")
            plan = R.build_plan(S.detect(root, resolve_remote=False))
            self.assertEqual(plan.primary.proposed, "1.0.2")   # a real bump
            self.assertNotEqual(plan.primary.level, "resume")


class TestVerifyCannotFalseGreen(unittest.TestCase):
    """An unconfirmable deploy must never read as a passing one."""

    def _shape(self):
        return S.RepoShape(root=Path("."), slug="A/B", default_branch="master",
                           shape="node", packages=[], ci_jobs=["test", "push"])

    def test_missing_token_is_unverified_not_success(self):
        import os
        keys = ("CIRCLE_TOKEN", "CIRCLECI_TOKEN", "CIRCLE_CI_TOKEN", "CIRCLECI_API_TOKEN")
        saved = {k: os.environ.pop(k) for k in keys if k in os.environ}
        try:
            out = R.verify(self._shape(), "v1.0.0")
            self.assertEqual(out["status"], "unverified")
            self.assertNotEqual(out["status"], "success")
            self.assertIn("NOT a pass", out["detail"])
        finally:
            os.environ.update(saved)

    def test_http_error_is_unverified_not_success(self):
        import io
        import os
        import urllib.error
        import urllib.request
        import warnings
        os.environ.setdefault("CIRCLE_TOKEN", "x")
        real = urllib.request.urlopen
        errors = []

        def boom(*a, **k):
            err = urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b""))
            errors.append(err)
            raise err

        urllib.request.urlopen = boom
        try:
            # HTTPError is file-like; verify() discards it, so close it here to
            # keep a ResourceWarning out of the test output.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ResourceWarning)
                out = R.verify(self._shape(), "v1.0.0")
            self.assertEqual(out["status"], "unverified")
            self.assertIn("403", out["detail"])
        finally:
            urllib.request.urlopen = real
            for e in errors:
                e.close()


class TestVerifyCannotFalseRed(unittest.TestCase):
    """A pending pipeline must not report as a failed one.

    The inverse of false-green, and it bit the first real release: `push` sits at
    `blocked` for as long as `test` is running (normal fan-in), and a
    "not success => bad" classifier called that `failed`. A false red on a release
    sends the reader hunting a CI break that does not exist — the same phantom
    failure the skill's own failure-modes doc is about.
    """

    def _shape(self):
        return S.RepoShape(root=Path("."), slug="A/B", default_branch="master",
                           shape="node", packages=[], ci_jobs=["test", "push"])

    def _verify_with_jobs(self, jobs: dict[str, str]) -> dict:
        """Stub the CircleCI chokepoint and drive verify() off a job map."""
        import os
        import urllib.request
        os.environ.setdefault("CIRCLE_TOKEN", "x")
        real = urllib.request.urlopen

        class Resp:
            def __init__(self, payload):
                self._p = payload
            def read(self):  # noqa: E301
                return json.dumps(self._p).encode()
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        def fake(req, timeout=None):
            url = req.full_url
            if url.endswith("/pipeline"):
                return Resp({"items": [{"id": "p1", "number": 7,
                                        "vcs": {"tag": "v1.0.0"}}]})
            if "/workflow" in url and "/job" not in url:
                return Resp({"items": [{"id": "w1"}]})
            return Resp({"items": [{"name": n, "status": s} for n, s in jobs.items()]})

        urllib.request.urlopen = fake
        try:
            return R.verify(self._shape(), "v1.0.0")
        finally:
            urllib.request.urlopen = real

    def test_blocked_downstream_while_upstream_runs_is_running(self):
        out = self._verify_with_jobs({"test": "running", "push": "blocked"})
        self.assertEqual(out["status"], "running")
        self.assertEqual(out["problems"], {})

    def test_queued_is_running_not_failed(self):
        for pending in ("queued", "not_running", "on_hold", "blocked"):
            with self.subTest(status=pending):
                out = self._verify_with_jobs({"test": "success", "push": pending})
                self.assertEqual(out["status"], "running")

    def test_a_real_failure_is_still_failed(self):
        out = self._verify_with_jobs({"test": "failed", "push": "blocked"})
        self.assertEqual(out["status"], "failed")
        self.assertIn("test", out["problems"])

    def test_all_success_is_success(self):
        out = self._verify_with_jobs({"test": "success", "push": "success"})
        self.assertEqual(out["status"], "success")

    def test_canceled_is_failed_not_pending(self):
        out = self._verify_with_jobs({"test": "success", "push": "canceled"})
        self.assertEqual(out["status"], "failed")


class TestForbiddenCommitSubject(SeededReleases):
    RELEASED = ("a@1.0.0",)

    """4U-engine's push job exits 1 on a /^Publish/ commit with no tags at HEAD."""

    def test_publish_subject_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "lerna.json").write_text(json.dumps({"packages": ["m/*"]}))
            d = root / "m" / "a"
            d.mkdir(parents=True)
            (d / "package.json").write_text(json.dumps({"name": "a", "version": "1.0.0"}))
            commit(root, "a")
            # build_plan reads origin/<default-branch>; stand that ref up locally
            # so no network is needed.
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            shape = S.detect(root, resolve_remote=False)
            original = R.COMMIT_SUBJECT
            try:
                R.COMMIT_SUBJECT = "Publish {version}"
                with self.assertRaises(R.ReleaseError) as cm:
                    R.build_plan(shape)
                self.assertIn("forbidden", str(cm.exception))
            finally:
                R.COMMIT_SUBJECT = original

    def test_default_subject_clears_the_guard(self):
        self.assertFalse(R.COMMIT_SUBJECT.startswith("Publish"))


class TestPackageChanged(SeededReleases):
    RELEASED = ("a@1.0.0",)

    """lerna's ignoreChanges: a docs-only touch is not a release."""

    REF = "origin/master"

    def _lerna(self, td):
        root = make_repo(Path(td))
        (root / "lerna.json").write_text(json.dumps({"packages": ["m/*"]}))
        d = root / "m" / "a"
        d.mkdir(parents=True)
        (d / "package.json").write_text(json.dumps({"name": "a", "version": "1.0.0"}))
        (d / "index.js").write_text("//\n")
        commit(root, "init")
        git(["tag", "a@1.0.0"], root)
        self._land(root)
        return root, S.detect(root, resolve_remote=False)

    @staticmethod
    def _land(root):
        """Advance the remote-tracking ref, standing in for a merge."""
        git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)

    def test_markdown_only_change_is_not_a_release(self):
        with tempfile.TemporaryDirectory() as td:
            root, shape = self._lerna(td)
            (root / "m" / "a" / "README.md").write_text("docs\n")
            commit(root, "docs: readme")
            self._land(root)
            self.assertFalse(R._package_changed(shape, shape.packages[0], "a@1.0.0", self.REF))

    def test_source_change_is_a_release(self):
        with tempfile.TemporaryDirectory() as td:
            root, shape = self._lerna(td)
            (root / "m" / "a" / "index.js").write_text("// changed\n")
            commit(root, "fix: thing")
            self._land(root)
            self.assertTrue(R._package_changed(shape, shape.packages[0], "a@1.0.0", self.REF))

    def test_never_tagged_package_counts_as_changed(self):
        with tempfile.TemporaryDirectory() as td:
            _, shape = self._lerna(td)
            self.assertTrue(R._package_changed(shape, shape.packages[0], None, self.REF))

    def test_nothing_to_release_is_its_own_error_type(self):
        # A fleet sweep must be able to tell "nothing to release" apart from
        # "failed to release"; collapsing them lets a real failure read as a
        # no-op. NothingToRelease exits 3, a genuine error exits 2.
        with tempfile.TemporaryDirectory() as td:
            root, _ = self._lerna(td)
            self._land(root)
            shape = S.detect(root, resolve_remote=False)
            with self.assertRaises(R.NothingToRelease):
                R.build_plan(shape)
            # …and it is still a ReleaseError, so existing handlers keep working.
            self.assertTrue(issubclass(R.NothingToRelease, R.ReleaseError))

    def test_change_in_a_sibling_package_does_not_count(self):
        with tempfile.TemporaryDirectory() as td:
            root, _ = self._lerna(td)
            other = root / "m" / "b"
            other.mkdir(parents=True)
            (other / "package.json").write_text(json.dumps({"name": "b", "version": "1.0.0"}))
            commit(root, "feat: b only")
            self._land(root)
            shape = S.detect(root, resolve_remote=False)
            a = next(p for p in shape.packages if p.name == "a")
            self.assertFalse(R._package_changed(shape, a, "a@1.0.0", self.REF))


class TestPushBranch(unittest.TestCase):
    """The release-branch push must survive a pre-push hook it cannot satisfy.

    A worktree holds tracked files only, so a husky hook's generated
    `.husky/_/husky.sh` and the `node_modules` its body needs are both absent —
    the hook is guaranteed to fail there, and running it turns a working release
    into a half-done one (ENG-1082). Injecting a hook that always fails is the
    only way to cover that: a repo without one passes either way.
    """

    def _repo_with_failing_hook(self, tmp: Path) -> tuple[Path, Path]:
        bare = tmp / "remote.git"
        git(["init", "-q", "--bare", str(bare)], tmp)
        root = tmp / "work"
        root.mkdir()
        git(["init", "-q", "-b", "master"], root)
        git(["config", "user.email", "t@t.t"], root)
        git(["config", "user.name", "T"], root)
        git(["remote", "add", "origin", str(bare)], root)
        commit(root, "initial")

        # Exactly the 4U-engine shape: hook present, the file it sources absent.
        hooks = root / ".husky"
        hooks.mkdir()
        hook = hooks / "pre-push"
        hook.write_text('#!/bin/sh\n. "$(dirname "$0")/_/husky.sh"\n', encoding="utf-8")
        hook.chmod(0o755)
        git(["config", "core.hooksPath", ".husky"], root)
        return root, bare

    def test_push_succeeds_despite_a_failing_pre_push_hook(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root, bare = self._repo_with_failing_hook(tmp)
            git(["checkout", "-q", "-b", "chore/release-1.0.1"], root)
            commit(root, "Release 1.0.1")

            R._push_branch(root, "chore/release-1.0.1")

            self.assertIn("chore/release-1.0.1", git(["branch", "--list"], bare))

    def test_the_injected_hook_really_does_block_a_plain_push(self):
        """Guards the test above: if the hook stopped failing, that test proves nothing."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root, _ = self._repo_with_failing_hook(tmp)
            git(["checkout", "-q", "-b", "chore/release-1.0.1"], root)
            commit(root, "Release 1.0.1")

            p = subprocess.run(
                ["git", "push", "-u", "origin", "chore/release-1.0.1"],
                cwd=root, capture_output=True, text=True,
            )
            self.assertNotEqual(p.returncode, 0)

    def test_tag_push_is_not_routed_through_the_helper(self):
        """The tag push runs in the main checkout, where hooks work and still mean
        something. The branch push always skips hooks; the tag push goes through
        `_push_tag`, which runs them and bypasses only a failure on untracked
        files (TestTagPushHook)."""
        src = (SCRIPTS / "release.py").read_text(encoding="utf-8")
        self.assertIn("bypass = _push_tag(shape.root, p.tag)", src)
        self.assertNotIn("_push_branch(shape.root, p.tag", src)
        push_tag = src[src.index("def _push_tag("):]
        push_tag = push_tag[:push_tag.index("\ndef ", 1)]
        self.assertIn('["git", "push", "origin", tag]', push_tag)
        self.assertEqual(src.count('"--no-verify"'), 2)


class _Pkg:
    """Minimal stand-in for a PackagePlan, for branch-name shaping only."""

    def __init__(self, name: str, proposed: str):
        self.pkg = type("P", (), {"name": name})()
        self.proposed = proposed


class TestBranchSlug(unittest.TestCase):
    """A wide lerna repo must not build a branch name git will reject.

    4U-engine has 23 packages; under --force all of them release, and the old
    name concatenated every <pkg>-<version> pair into a ~700-char ref that git
    refused with `File name too long` (ENG-1090).
    """

    def test_single_package_keeps_the_bare_version(self):
        self.assertEqual(R._branch_slug([_Pkg("svc", "1.2.3")]), "1.2.3")

    def test_many_packages_do_not_enumerate(self):
        pkgs = [_Pkg(f"some-fairly-long-model-name-{i}", f"{i}.0.0") for i in range(23)]
        slug = R._branch_slug(pkgs)
        self.assertLessEqual(len(slug), R._BRANCH_SLUG_MAX)
        self.assertIn("plus-22-more", slug)
        # the giveaway of the old behaviour: a second package's name in the ref
        self.assertNotIn("some-fairly-long-model-name-1-1.0.0", slug)

    def test_the_full_ref_path_stays_within_the_filesystem_limit(self):
        """The failure was on the .lock path, not the branch string itself."""
        pkgs = [_Pkg(f"pkg-with-a-long-name-{i}", "10.20.30") for i in range(40)]
        ref = f".git/refs/heads/chore/release-{R._branch_slug(pkgs)}.lock"
        self.assertLess(len(ref.encode()), 255)

    def test_distinct_wide_plans_do_not_collide_on_one_branch(self):
        """read_state finds a release by branch name, so a collision would let a
        resume attach to the wrong one."""
        a = [_Pkg("x" * 70, "1.0.0"), _Pkg("b", "1.0.0")]
        b = [_Pkg("x" * 70, "1.0.0"), _Pkg("c", "2.0.0")]
        self.assertNotEqual(R._branch_slug(a), R._branch_slug(b))

    def test_slug_is_deterministic(self):
        pkgs = [_Pkg("a", "1.0.0"), _Pkg("b", "2.0.0")]
        self.assertEqual(R._branch_slug(pkgs), R._branch_slug(list(pkgs)))

    def test_build_plan_actually_uses_the_helper(self):
        """The wiring, which a unit test on the helper cannot cover.

        Reverting build_plan to the old inline expression leaves every test above
        passing, because they call _branch_slug directly. Only this notices.

        For the record, the expression this replaced produced a 600-char slug for 23
        packages — a 635-byte `.git/refs/heads/...lock` path against a 255-byte
        limit, which is the `File name too long` git reported.
        """
        src = (SCRIPTS / "release.py").read_text(encoding="utf-8")
        self.assertIn("branch_slug = _branch_slug(releasing)", src)
        self.assertNotIn('branch_slug = re.sub(r"[^a-z0-9.]+", "-", label.lower())', src)

    def test_merge_method_is_resolved_not_hardcoded(self):
        """Same wiring gap for ENG-1089."""
        src = (SCRIPTS / "release.py").read_text(encoding="utf-8")
        self.assertIn("method = _merge_method(shape.slug, shape.root)", src)
        self.assertIn("method, \"--delete-branch\"", src)


class TestMergeMethod(unittest.TestCase):
    """Assuming --squash stops the release on a repo that forbids it (ENG-1089)."""

    def setUp(self):
        self._real = R.gh_json
        self.addCleanup(lambda: setattr(R, "gh_json", self._real))

    def _stub(self, **allowed):
        R.gh_json = lambda *a, **k: {"allowSquashMerge": False, "allowMergeCommit": False,
                                     "allowRebaseMerge": False, **allowed}

    def test_prefers_squash_when_allowed(self):
        self._stub(allowSquashMerge=True, allowMergeCommit=True)
        self.assertEqual(R._merge_method("o/r", Path(".")), "--squash")

    def test_falls_back_to_merge_commit(self):
        """AuraDataJobs exactly: squash off, merge commit on."""
        self._stub(allowMergeCommit=True)
        self.assertEqual(R._merge_method("o/r", Path(".")), "--merge")

    def test_falls_back_to_rebase_last(self):
        self._stub(allowRebaseMerge=True)
        self.assertEqual(R._merge_method("o/r", Path(".")), "--rebase")

    def test_raises_when_no_method_is_permitted(self):
        self._stub()
        with self.assertRaises(R.ReleaseError) as e:
            R._merge_method("o/r", Path("."))
        self.assertIn("no merge method", str(e.exception))

    def _stub_gh_knowing(self, known: str, **allowed):
        """A `gh` that accepts exactly one spelling of the merge-method fields.

        `gh repo view --json <unknown-field>` exits non-zero listing every valid
        field, which `sh` surfaces as a ReleaseError — so an unrecognised spelling
        raises here rather than returning nulls.
        """
        def fake(args, *a, **k):
            fields = args[args.index("--json") + 1]
            if known not in fields:
                raise R.ReleaseError(
                    f'gh repo view failed:\nUnknown JSON field: "{fields.split(",")[0]}"'
                )
            base = dict.fromkeys(fields.split(","), False)
            return {**base, **{k2: v for k2, v in allowed.items() if k2 in base}}
        R.gh_json = fake

    def test_resolves_on_a_gh_using_the_current_spelling(self):
        """`squashMergeAllowed` — the spelling current gh builds accept."""
        self._stub_gh_knowing("Allowed", squashMergeAllowed=True)
        self.assertEqual(R._merge_method("o/r", Path(".")), "--squash")

    def test_resolves_on_a_gh_using_the_legacy_spelling(self):
        self._stub_gh_knowing("allow", allowMergeCommit=True)
        self.assertEqual(R._merge_method("o/r", Path(".")), "--merge")

    def test_unreadable_settings_are_not_reported_as_a_forbidding_repo(self):
        """A gh that knows neither spelling needs a gh fix, not a settings change.

        Reporting it as "permits no merge method" would send the caller into the
        repository's settings for a repo that in fact permits squash.
        """
        def fake(args, *a, **k):
            raise R.ReleaseError('gh repo view failed:\nUnknown JSON field: "x"')
        R.gh_json = fake
        with self.assertRaises(R.ReleaseError) as e:
            R._merge_method("o/r", Path("."))
        msg = str(e.exception)
        self.assertIn("could not read the merge settings", msg)
        self.assertNotIn("no merge method", msg)
        # gh's own stderr survives — without it the caller cannot tell which spelling
        # was refused, or that the refusal was about spelling at all.
        self.assertIn('Unknown JSON field: "x"', msg)

    def test_a_non_field_gh_failure_propagates_verbatim(self):
        """Auth, network, and missing-repo failures are not spelling problems.

        Folding them into the retry would report every one of them as "upgrade your
        gh" — the same misdiagnosis, one class over.
        """
        def fake(args, *a, **k):
            raise R.ReleaseError(
                "gh repo view failed:\ngh: To use GitHub CLI in a GitHub Actions "
                "workflow, set the GH_TOKEN environment variable."
            )
        R.gh_json = fake
        with self.assertRaises(R.ReleaseError) as e:
            R._merge_method("o/r", Path("."))
        msg = str(e.exception)
        self.assertIn("GH_TOKEN", msg)
        self.assertNotIn("could not read the merge settings", msg)
        self.assertNotIn("no merge method", msg)

    def test_a_non_field_failure_on_the_second_spelling_still_propagates(self):
        """The first spelling being unknown must not mask a real fault behind it."""
        seen: list[str] = []

        def fake(args, *a, **k):
            fields = args[args.index("--json") + 1]
            seen.append(fields)
            if len(seen) == 1:
                raise R.ReleaseError(
                    f'gh repo view failed:\nUnknown JSON field: "{fields.split(",")[0]}"'
                )
            raise R.ReleaseError("gh repo view failed:\nHTTP 404: Not Found")
        R.gh_json = fake
        with self.assertRaises(R.ReleaseError) as e:
            R._merge_method("o/r", Path("."))
        self.assertIn("HTTP 404", str(e.exception))
        self.assertEqual(len(seen), 2)


class TestWaitUntilMergeable(unittest.TestCase):
    """run must not merge while required checks are pending (ENG-1088)."""

    def setUp(self):
        self._gh, self._sleep = R.gh_json, R.time.sleep
        R.time.sleep = lambda s: None
        self.addCleanup(lambda: (setattr(R, "gh_json", self._gh),
                                 setattr(R.time, "sleep", self._sleep)))
        self.shape = type("S", (), {"slug": "o/r", "root": Path(".")})()

    def _states(self, *seq):
        it = iter(seq)
        def fake(args, cwd=None):
            if "mergeStateStatus" in args[-1]:
                return {"mergeStateStatus": next(it)}
            return {"statusCheckRollup": [{"name": "ci/test", "state": "PENDING"}]}
        R.gh_json = fake

    def test_waits_through_blocked_then_proceeds(self):
        self._states("BLOCKED", "BLOCKED", "CLEAN")
        R._wait_until_mergeable(self.shape, 5)   # returns without raising

    def test_unstable_is_mergeable_not_a_failure(self):
        """UNSTABLE = mergeable with a non-required check failing. Treating it as
        unmergeable would stall a release the repo would accept."""
        self._states("UNSTABLE")
        R._wait_until_mergeable(self.shape, 5)

    def test_dirty_fails_fast_rather_than_waiting_out_the_budget(self):
        self._states("DIRTY")
        with self.assertRaises(R.ReleaseError) as e:
            R._wait_until_mergeable(self.shape, 5)
        self.assertIn("DIRTY", str(e.exception))

    def test_timeout_names_the_check_states(self):
        self._states(*(["BLOCKED"] * 500))
        orig = R._CHECK_WAIT_SECONDS
        R._CHECK_WAIT_SECONDS = 0
        self.addCleanup(lambda: setattr(R, "_CHECK_WAIT_SECONDS", orig))
        with self.assertRaises(R.ReleaseError) as e:
            R._wait_until_mergeable(self.shape, 5)
        self.assertIn("ci/test", str(e.exception))

    def test_merge_is_gated_on_the_wait(self):
        """Source-level: the merge call must come after the wait, or the fix is
        decorative."""
        src = (SCRIPTS / "release.py").read_text(encoding="utf-8")
        wait_at = src.index("_wait_until_mergeable(shape, st.open_pr)")
        merge_at = src.index('sh(["gh", "pr", "merge"')
        self.assertLess(wait_at, merge_at)
        self.assertNotIn('"--squash", "--delete-branch"', src)


def _plan(levels: list[str], changed: bool = True) -> R.Plan:
    """A Plan with the given per-package levels, for is_resume only."""
    pkgs = []
    for i, lvl in enumerate(levels):
        p = R.PackagePlan(pkg=type("P", (), {"name": f"p{i}",
                                             "tag_for": staticmethod(lambda v: f"v{v}")})(),
                          current="1.0.0", proposed="1.0.1", level=lvl, reason="",
                          last_tag="v1.0.0", body="", changed=changed)
        pkgs.append(p)
    return R.Plan(shape=None, packages=pkgs, branch="b", commit_subject="s")


class TestIsResume(unittest.TestCase):
    """A resume ships no new version, so it must not report as a release (ENG-1081)."""

    def test_all_resume_is_a_resume(self):
        self.assertTrue(_plan(["resume"]).is_resume)
        self.assertTrue(_plan(["resume", "resume"]).is_resume)

    def test_a_fresh_release_is_not(self):
        self.assertFalse(_plan(["patch"]).is_resume)
        self.assertFalse(_plan(["minor", "major"]).is_resume)

    def test_mixed_is_not_a_resume(self):
        """Real work is shipping alongside the resume, so `released` is honest."""
        self.assertFalse(_plan(["resume", "patch"]).is_resume)

    def test_empty_is_not_a_resume(self):
        """all() over an empty list is True — guard against reporting a no-op plan
        as a resume."""
        self.assertFalse(_plan([]).is_resume)

    def test_unchanged_packages_are_ignored(self):
        self.assertFalse(_plan(["resume"], changed=False).is_resume)


class TestResumeReporting(unittest.TestCase):
    """The status word and the unreleased list are what make a resume legible."""

    def test_run_emits_resumed_not_released(self):
        src = (SCRIPTS / "release.py").read_text(encoding="utf-8")
        self.assertIn('status = "resumed" if plan.is_resume else "released"', src)
        # the pre-fix form, which reported every outcome as released
        self.assertNotIn('result = {"status": "released"', src)

    def test_run_reports_what_is_still_unreleased(self):
        src = (SCRIPTS / "release.py").read_text(encoding="utf-8")
        self.assertIn('result["unreleased"] = pending', src)

    def test_the_human_output_names_it_too(self):
        """The run that under-released 12 repos was read off the terminal, not --json."""
        src = (SCRIPTS / "release.py").read_text(encoding="utf-8")
        self.assertIn('if out["status"] == "resumed":', src)
        self.assertIn("FINISHED AN EXISTING release", src)

    def test_unreleased_after_lists_commit_subjects(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            commit(root, "shipped in the resumed version")
            git(["tag", "v1.0.1"], root)
            commit(root, "feat: still unreleased")
            commit(root, "fix: also still unreleased")
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)

            plan = _plan(["resume"])
            plan.shape = type("S", (), {"root": root, "default_branch": "master"})()
            plan.releasing[0].proposed = "1.0.1"

            out = R._unreleased_after(plan)
            subjects = out["p0"]
            self.assertIn("feat: still unreleased", subjects)
            self.assertIn("fix: also still unreleased", subjects)
            self.assertNotIn("shipped in the resumed version", subjects)

    def test_an_unreadable_tag_does_not_crash_a_finished_release(self):
        """The release already landed by this point; a reporting failure must not
        turn it into a traceback."""
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            commit(root, "only")
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            plan = _plan(["resume"])
            plan.shape = type("S", (), {"root": root, "default_branch": "master"})()
            plan.releasing[0].proposed = "9.9.9"      # tag v9.9.9 does not exist
            out = R._unreleased_after(plan)
            self.assertIn("could not read commits", out["p0"][0])



class TestStaleReleaseBranchIsRefused(SeededReleases):
    """A release branch left by a stopped run is resumed only if built on today's default.

    After a fix landed, a resumed `chore/release-x.y.z` pushed the old commit built
    on the pre-fix default (PROD-7794). The run must refuse, with the delete commands.
    """

    def _repo(self, td):
        root = make_repo(Path(td))
        (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.0.0"}))
        commit(root, "chore: base")
        git(["tag", "v1.0.0"], root)
        commit(root, "fix: first")
        git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
        self.seed("v1.0.0")
        plan = R.build_plan(S.detect(root, resolve_remote=False))
        # An empty local remote, so ls-remote answers offline; tests push to it as needed.
        bare = Path(td) / "empty.git"
        git(["init", "-q", "--bare", str(bare)], root)
        git(["remote", "set-url", "origin", str(bare)], root)
        return root, plan

    def test_a_branch_on_the_current_default_resumes(self):
        with tempfile.TemporaryDirectory() as td:
            root, plan = self._repo(td)
            git(["branch", plan.branch, "origin/master"], root)
            self.assertIsNone(R._stale_branch(plan.shape, plan))

    def test_no_branch_at_all_resumes(self):
        with tempfile.TemporaryDirectory() as td:
            _, plan = self._repo(td)
            self.assertIsNone(R._stale_branch(plan.shape, plan))

    def test_a_local_branch_behind_the_default_is_refused_with_the_delete_command(self):
        with tempfile.TemporaryDirectory() as td:
            root, plan = self._repo(td)
            git(["branch", plan.branch, "origin/master"], root)
            commit(root, "fix: the fix that landed after the stop")
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            why = R._stale_branch(plan.shape, plan)
            self.assertIn("not based on the current origin/master", why)
            self.assertIn(f"branch -D {plan.branch}", why)
            self.assertLess(why.index("worktree prune"), why.index("branch -D"))
            self.assertNotIn("push origin --delete", why)

    def _bare_remote(self, td, root):
        bare = Path(td) / "remote.git"
        git(["init", "-q", "--bare", str(bare)], root)
        git(["remote", "set-url", "origin", str(bare)], root)
        return bare

    def test_a_pushed_branch_behind_the_default_is_found_on_the_remote_unfetched(self):
        # Never fetched here: no refs/remotes/origin/<branch>. The remote itself is asked.
        with tempfile.TemporaryDirectory() as td:
            root, plan = self._repo(td)
            self._bare_remote(td, root)
            git(["push", "-q", "origin", f"origin/master:refs/heads/{plan.branch}"], root)
            git(["update-ref", "-d", f"refs/remotes/origin/{plan.branch}"], root)
            commit(root, "fix: later")
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            self.assertFalse(subprocess.run(["git", "rev-parse", "--verify", "--quiet",
                                             f"refs/remotes/origin/{plan.branch}"], cwd=root,
                                            capture_output=True).stdout.strip())
            why = R._stale_branch(plan.shape, plan)
            self.assertIn(f"push origin --delete {plan.branch}", why)
            self.assertNotIn("branch -D", why)

    def test_a_tracking_ref_left_after_the_remote_delete_does_not_block(self):
        with tempfile.TemporaryDirectory() as td:
            root, plan = self._repo(td)
            self._bare_remote(td, root)
            git(["update-ref", f"refs/remotes/origin/{plan.branch}", "origin/master"], root)
            commit(root, "fix: later")
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            self.assertIsNone(R._stale_branch(plan.shape, plan))

    def test_a_pushed_branch_on_the_current_default_resumes(self):
        with tempfile.TemporaryDirectory() as td:
            root, plan = self._repo(td)
            self._bare_remote(td, root)
            git(["push", "-q", "origin", f"origin/master:refs/heads/{plan.branch}"], root)
            self.assertIsNone(R._stale_branch(plan.shape, plan))

    def test_run_refuses_before_touching_a_worktree(self):
        import unittest.mock as m
        with tempfile.TemporaryDirectory() as td:
            _, plan = self._repo(td)
            with m.patch.object(R, "read_state", return_value=R.State(branch_pushed=True)), \
                    m.patch.object(R, "_stale_branch", return_value="stale: delete it"), \
                    m.patch.object(R, "_worktree", side_effect=AssertionError("worktree touched")), \
                    m.patch.object(R, "sh", side_effect=AssertionError("git or gh called")):
                with self.assertRaises(R.ReleaseError) as cm:
                    R.run(plan)
            self.assertIn("stale: delete it", str(cm.exception))

    def test_an_open_pr_is_left_alone(self):
        import unittest.mock as m
        with tempfile.TemporaryDirectory() as td:
            _, plan = self._repo(td)
            called = []
            with m.patch.object(R, "read_state", return_value=R.State(open_pr=5, branch_pushed=True)), \
                    m.patch.object(R, "_stale_branch", side_effect=lambda *a: called.append(1)), \
                    m.patch.object(R, "_wait_until_mergeable", side_effect=RuntimeError("stop here")):
                with self.assertRaises(RuntimeError):
                    R.run(plan)
            self.assertEqual(called, [])


class TestMissingTagPipeline(unittest.TestCase):
    """A tag whose GitHub event was lost has no pipeline; tag-built CI never deploys it."""

    def _shape(self, retrigger=False):
        return S.RepoShape(root=Path("."), slug="A/B", default_branch="master", shape="node",
                           packages=[], ci_jobs=["test", "push"], needs_pipeline_retrigger=retrigger)

    def _verify(self, shape, trigger, age=600):
        import io
        import os
        import unittest.mock as m
        import urllib.request
        posts = []

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(req, timeout=None):
            if req.get_method() == "POST":
                posts.append((req.full_url, json.loads(req.data)))
                return Resp(json.dumps({"number": 42}).encode())
            return Resp(json.dumps({"items": [{"id": "p", "vcs": {"tag": "v0.9.0"}}]}).encode())

        with m.patch.dict(os.environ, {"CIRCLE_TOKEN": "x"}), \
                m.patch.object(urllib.request, "urlopen", side_effect=fake), \
                m.patch.object(R, "_tag_age_s", return_value=age), \
                m.patch.object(R, "_tag_commit_time", return_value=None), \
                m.patch.object(R.rollout_mod, "rollout_check", return_value={}):
            out = R.verify(shape, "v1.0.0", trigger_missing=trigger)
        return out, posts

    def test_without_the_flag_it_reports_and_starts_nothing(self):
        out, posts = self._verify(self._shape(), trigger=False)
        self.assertEqual(out["status"], "no-pipeline")
        self.assertIn("--trigger-missing", out["detail"])
        self.assertEqual(posts, [])

    def test_with_the_flag_it_starts_the_tag_pipeline(self):
        out, posts = self._verify(self._shape(), trigger=True)
        self.assertEqual(posts, [("https://circleci.com/api/v2/project/gh/A/B/pipeline", {"tag": "v1.0.0"})])
        self.assertEqual(out["status"], "triggered")
        self.assertEqual(out["pipeline"], 42)
        self.assertEqual(R.verify_exit(out), 1)  # started is not deployed

    def test_a_young_tag_is_not_triggered(self):
        out, posts = self._verify(self._shape(), trigger=True, age=30)
        self.assertEqual(posts, [])
        self.assertIn("only 30 s old", out["detail"])

    def test_an_unknown_tag_age_is_not_triggered(self):
        out, posts = self._verify(self._shape(), trigger=True, age=None)
        self.assertEqual(posts, [])
        self.assertEqual(out["status"], "no-pipeline")

    def test_branch_built_ci_never_triggers_a_tag_pipeline(self):
        out, posts = self._verify(self._shape(retrigger=True), trigger=True)
        self.assertEqual(posts, [])
        self.assertIn("expected", out["detail"])

    def _paged(self, pages, created):
        """verify with CircleCI answering `pages` (lists of items) in order."""
        import io
        import os
        import unittest.mock as m
        import urllib.request
        posts, seen = [], []

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(req, timeout=None):
            if req.get_method() == "POST":
                posts.append(req.full_url)
                return Resp(b'{"number": 1}')
            url = req.full_url
            if "/pipeline" in url and "/workflow" not in url and "pipeline/" not in url.split("project/")[-1][-12:]:
                i = len(seen)
                seen.append(url)
                body = {"items": pages[i], "next_page_token": f"t{i + 1}" if i + 1 < len(pages) else None}
                return Resp(json.dumps(body).encode())
            if "/workflow" in url and "/job" not in url:
                return Resp(b'{"items": [{"id": "w"}]}')
            return Resp(b'{"items": [{"name": "test", "status": "success"}, {"name": "push", "status": "success"}]}')

        with m.patch.dict(os.environ, {"CIRCLE_TOKEN": "x"}), \
                m.patch.object(urllib.request, "urlopen", side_effect=fake), \
                m.patch.object(R, "_tag_age_s", return_value=3600), \
                m.patch.object(R, "_tag_commit_time", return_value=created), \
                m.patch.object(R, "serving_check", return_value={}), \
                m.patch.object(R.rollout_mod, "rollout_check", return_value={}):
            out = R.verify(self._shape(), "v1.0.0", trigger_missing=True)
        return out, posts, seen

    def test_a_tag_pipeline_past_the_first_page_is_found_not_retriggered(self):
        import datetime as d
        tag_at = d.datetime(2026, 11, 1, 12, tzinfo=d.timezone.utc)
        newer = [{"id": f"n{i}", "vcs": {"branch": "master"}, "created_at": "2026-11-02T00:00:00Z"} for i in range(20)]
        page2 = [{"id": "p", "number": 9, "vcs": {"tag": "v1.0.0"}, "created_at": "2026-11-01T12:00:05Z"}]
        out, posts, seen = self._paged([newer, page2], tag_at)
        self.assertEqual(posts, [])
        self.assertEqual(len(seen), 2)
        self.assertEqual(out["pipeline"], 9)

    def test_pages_that_never_reach_the_tag_are_unverified_and_trigger_nothing(self):
        import datetime as d
        tag_at = d.datetime(2026, 11, 1, 12, tzinfo=d.timezone.utc)
        newer = [{"id": "n", "vcs": {"branch": "master"}, "created_at": "2026-11-02T00:00:00Z"}] * 20
        out, posts, seen = self._paged([newer] * (R.PIPELINE_PAGES + 1), tag_at)
        self.assertEqual(posts, [])
        self.assertEqual(len(seen), R.PIPELINE_PAGES)
        self.assertEqual(out["status"], "unverified")

    def test_a_page_older_than_the_tag_proves_it_missing(self):
        import datetime as d
        tag_at = d.datetime(2026, 11, 1, 12, tzinfo=d.timezone.utc)
        older = [{"id": "o", "vcs": {"branch": "master"}, "created_at": "2026-10-31T00:00:00Z"}]
        out, posts, seen = self._paged([older, older], tag_at)
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(posts), 1)
        self.assertEqual(out["status"], "triggered")

    def test_the_cutoff_is_the_tagged_commit_not_the_release(self):
        # The release can be created hours after the tag push (a resumed run);
        # paging must still reach the pipeline the push started.
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            commit(root, "chore: base")
            git(["tag", "v1.0.0"], root)
            shape = S.RepoShape(root=root, slug="A/B", default_branch="master", shape="node",
                                packages=[], ci_jobs=["test", "push"])
            when = R._tag_commit_time(shape, "v1.0.0")
            head = git(["log", "-1", "--format=%cI", "HEAD"], root)
            self.assertEqual(when, R._iso(head))
            self.assertIsNone(R._tag_commit_time(shape, "v9.9.9"))

    def test_a_failed_trigger_is_reported_not_passed(self):
        import unittest.mock as m
        with m.patch.object(R, "_tag_age_s", return_value=600):
            out = R._trigger_tag_pipeline(self._shape(), "v1.0.0",
                                          lambda *a: (_ for _ in ()).throw(OSError("503")))
        self.assertEqual(out["status"], "no-pipeline")
        self.assertIn("503", out["detail"])

if __name__ == "__main__":
    unittest.main()


class TestTaggedRangeMatchesTheGo(SeededReleases):
    """A PR merged between the approved plan and the bump PR's merge lands under the tag
    (ReportService v1.34.17 and two 4U models shipped one PR the go never named). `run`
    must refuse to tag a commit carrying anything merged after the head the go approved."""

    RELEASED = ("v1.0.0", "a@1.0.0", "b@1.0.0")

    def _single(self, td):
        root = make_repo(Path(td))
        (root / "package.json").write_text(json.dumps({"name": "n", "version": "1.0.0"}))
        commit(root, "chore: base")
        git(["tag", "v1.0.0"], root)
        commit(root, "fix: the approved change")
        git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
        return root, S.detect(root, resolve_remote=False)

    def _bump(self, root, version):
        (root / "package.json").write_text(json.dumps({"name": "n", "version": version}))
        git(["add", "-A"], root)
        git(["commit", "-q", "-m", f"chore(release): {version}"], root)
        return git(["rev-parse", "HEAD"], root)

    def test_the_plan_records_the_head_it_was_computed_at(self):
        with tempfile.TemporaryDirectory() as td:
            root, shape = self._single(td)
            plan = R.build_plan(shape, level="patch")
            self.assertEqual(plan.head, git(["rev-parse", "origin/master"], root))
            self.assertEqual(R.describe(plan)["head"], plan.head)

    def test_a_pr_merged_after_the_go_is_caught(self):
        with tempfile.TemporaryDirectory() as td:
            root, shape = self._single(td)
            plan = R.build_plan(shape, level="patch")
            commit(root, "chore: package update merged a minute later")
            sha = self._bump(root, "1.0.1")
            extra = R._extra_commits(shape, plan, plan.head, sha)
            self.assertEqual(len(extra), 1)
            self.assertIn("package update merged a minute later", extra[0])

    def test_the_approved_range_alone_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root, shape = self._single(td)
            plan = R.build_plan(shape, level="patch")
            sha = self._bump(root, "1.0.1")
            self.assertEqual(R._extra_commits(shape, plan, plan.head, sha), [])
            self.assertEqual(R.tagged_range(shape, plan, sha), {"v1.0.1": 2})

    def test_re_running_with_the_tagged_sha_accepts_the_new_range(self):
        with tempfile.TemporaryDirectory() as td:
            root, shape = self._single(td)
            plan = R.build_plan(shape, level="patch")
            commit(root, "chore: late")
            sha = self._bump(root, "1.0.1")
            self.assertEqual(R._extra_commits(shape, plan, sha, sha), [])

    def test_a_resume_after_the_merge_needs_the_approved_head(self):
        # A retry plans on a tip that already holds the bump; its head must not approve
        # the commits the earlier run refused.
        with tempfile.TemporaryDirectory() as td:
            root, shape = self._single(td)
            commit(root, "chore: late")
            sha = self._bump(root, "1.0.1")
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            plan = R.build_plan(shape, level="patch")
            with self.assertRaises(R.ReleaseError) as cm:
                R._range_head(shape, plan, sha, None)
            self.assertIn("--expect-head", str(cm.exception))
            self.assertEqual(R._range_head(shape, plan, sha, sha), sha)

    def test_a_plan_made_before_the_merge_is_its_own_range_head(self):
        with tempfile.TemporaryDirectory() as td:
            root, shape = self._single(td)
            plan = R.build_plan(shape, level="patch")
            sha = self._bump(root, "1.0.1")
            self.assertEqual(R._range_head(shape, plan, sha, None), plan.head)

    def test_a_plan_without_a_head_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root, shape = self._single(td)
            plan = R.build_plan(shape, level="patch")
            plan.head = ""
            sha = self._bump(root, "1.0.1")
            with self.assertRaises(R.ReleaseError):
                R._range_head(shape, plan, sha, None)

    def test_an_unknown_expected_head_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root, shape = self._single(td)
            plan = R.build_plan(shape, level="patch")
            sha = self._bump(root, "1.0.1")
            with self.assertRaises(R.ReleaseError):
                R._extra_commits(shape, plan, "0" * 40, sha)

    def test_a_failed_log_fails_closed(self):
        # A tagged commit with no parent makes `<sha>^` unresolvable: the check must raise,
        # not report zero extra commits and let the tag through.
        with tempfile.TemporaryDirectory() as td:
            root, shape = self._single(td)
            plan = R.build_plan(shape, level="patch")
            root_sha = git(["rev-list", "--max-parents=0", "HEAD"], root)
            with self.assertRaises(R.ReleaseError):
                R._extra_commits(shape, plan, plan.head, root_sha)

    def test_lerna_counts_only_releasing_packages(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(Path(td))
            (root / "lerna.json").write_text(json.dumps({"packages": ["m/*"]}))
            for name in ("a", "b"):
                d = root / "m" / name
                d.mkdir(parents=True)
                (d / "package.json").write_text(json.dumps({"name": name, "version": "1.0.0"}))
                (d / "index.js").write_text("//\n")
            commit(root, "init")
            git(["tag", "a@1.0.0"], root)
            git(["tag", "b@1.0.0"], root)
            (root / "m" / "a" / "index.js").write_text("// approved\n")
            commit(root, "fix(a): approved")
            git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
            shape = S.detect(root, resolve_remote=False)
            plan = R.build_plan(shape, level="patch")
            self.assertEqual([p.pkg.name for p in plan.releasing], ["a"])
            (root / "m" / "b" / "index.js").write_text("// b only\n")
            commit(root, "fix(b): not released here")
            (root / "m" / "a" / "index.js").write_text("// late a\n")
            commit(root, "fix(a): late")
            (root / "m" / "a" / "package.json").write_text(json.dumps({"name": "a", "version": "1.0.1"}))
            git(["add", "-A"], root)
            git(["commit", "-q", "-m", "chore(release): a@1.0.1"], root)
            extra = R._extra_commits(shape, plan, plan.head, git(["rev-parse", "HEAD"], root))
            self.assertEqual(len(extra), 1)
            self.assertIn("fix(a): late", extra[0])


class TestReleaseOneLernaPackage(SeededReleases):
    """A go for one lerna package must tag that package alone, even when a merged commit
    also touched other packages (4U-engine#190 changed every model's .nvmrc)."""

    RELEASED = ("a@1.0.0", "b@1.0.0", "c@1.0.0")

    def _repo(self, td):
        root = make_repo(Path(td))
        (root / "lerna.json").write_text(json.dumps({"packages": ["m/*"]}))
        for name in ("a", "b", "c"):
            d = root / "m" / name
            d.mkdir(parents=True)
            (d / "package.json").write_text(json.dumps({"name": name, "version": "1.0.0"}))
            (d / ".nvmrc").write_text("24.18.0\n")
        commit(root, "init")
        for name in ("a", "b", "c"):
            git(["tag", f"{name}@1.0.0"], root)
        for name in ("a", "b"):
            (root / "m" / name / ".nvmrc").write_text("26.13.1\n")
        commit(root, "chore: node 26 for every model")
        git(["update-ref", "refs/remotes/origin/master", "HEAD"], root)
        return root, S.detect(root, resolve_remote=False)

    def test_without_package_every_changed_package_releases(self):
        with tempfile.TemporaryDirectory() as td:
            _, shape = self._repo(td)
            plan = R.build_plan(shape, level="patch")
            self.assertEqual(sorted(p.pkg.name for p in plan.releasing), ["a", "b"])

    def test_package_releases_only_the_named_one_and_warns_about_the_rest(self):
        with tempfile.TemporaryDirectory() as td:
            _, shape = self._repo(td)
            plan = R.build_plan(shape, level="patch", only=["a"])
            self.assertEqual([p.pkg.name for p in plan.releasing], ["a"])
            self.assertEqual([p.tag for p in plan.releasing], ["a@1.0.1"])
            self.assertTrue(any(w.startswith("b: changed") for w in plan.warnings))
            self.assertFalse(any(w.startswith("c:") for w in plan.warnings))

    def test_an_unknown_package_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            _, shape = self._repo(td)
            with self.assertRaises(R.ReleaseError):
                R.build_plan(shape, level="patch", only=["nope"])

    def test_a_selected_package_with_nothing_new_has_nothing_to_release(self):
        with tempfile.TemporaryDirectory() as td:
            _, shape = self._repo(td)
            with self.assertRaises(R.NothingToRelease):
                R.build_plan(shape, level="patch", only=["c"])

    def test_the_range_gate_answers_per_package(self):
        with tempfile.TemporaryDirectory() as td:
            root, shape = self._repo(td)
            plan = R.build_plan(shape, level="patch")
            (root / "m" / "b" / "index.js").write_text("// late b\n")
            commit(root, "fix(b): late")
            (root / "m" / "c" / "index.js").write_text("// late c\n")
            commit(root, "fix(c): late, c is not releasing")
            git(["commit", "-q", "--allow-empty", "-m", "chore(release)"], root)
            extra = R._extra_commits(shape, plan, plan.head, git(["rev-parse", "HEAD"], root))
            self.assertEqual(len(extra), 1)
            self.assertTrue(extra[0].startswith("b: "))
