"""Tests for `eng:update-nodejs`'s scanner — the archived-repo handling.

The defect being guarded (ENG-1080): the scanner globs for `.nvmrc` and had no
notion of a repo being archived on GitHub, so an archived repo was listed as a
normal rollout target, bumped, committed, and only failed at `git push` with an
HTTP 403 — which reads exactly like the `GITHUB_TOKEN`-shadowing credential
fault in CLAUDE.md (ENG-971), i.e. the wrong diagnosis.

Every branch that matters here is one real data cannot reach: `gh` missing from
PATH, `gh` exiting non-zero, a response that will not parse, a checkout with no
GitHub origin. So the single subprocess chokepoint (`_run`) and `shutil.which`
are stubbed, and no test lets a process spawn.

The load-bearing assertions are the ones about **unknown**: a lookup that could
not answer must never collapse into either `True` or `False`. Reported as
archived, a live repo silently drops out of the rollout; reported as live, the
403 comes back. Only a distinct third state is honest.
"""

import importlib.util
import json
import os
import pathlib
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCANNER = REPO_ROOT / "plugins/eng/skills/update-nodejs/scripts/scan_node_versions.py"


def load_scanner():
    spec = importlib.util.spec_from_file_location("scan_node_versions", SCANNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


scan_mod = load_scanner()


def row(repo="Svc", nvmrc="24.19.0", dockerfile=None, circleci=None,
        functions_engine=None, archived=False):
    return {"repo": repo, "path": f"/ws/Services/{repo}", "nvmrc": nvmrc,
            "dockerfile": dockerfile, "circleci": circleci,
            "functions_engine": functions_engine, "functions_pkg": None,
            "archived": archived}


class ScannerTestCase(unittest.TestCase):
    """Isolates the module-level archived cache and restores the stub points."""

    def setUp(self):
        scan_mod._ARCHIVED_CACHE.clear()
        self._saved = (scan_mod._run, scan_mod.shutil.which)
        self.addCleanup(self._restore)
        # Default: `gh` exists, but any call must be explicitly stubbed. An
        # unstubbed call raises rather than spawning a real process.
        scan_mod.shutil.which = lambda name: f"/usr/bin/{name}"
        scan_mod._run = self._forbidden

    def _restore(self):
        scan_mod._run, scan_mod.shutil.which = self._saved
        scan_mod._ARCHIVED_CACHE.clear()

    def _forbidden(self, cmd, cwd=None, timeout=30):
        raise AssertionError(f"unstubbed subprocess call: {cmd}")

    def stub_run(self, handler):
        """`handler(cmd) -> stdout str | None`, None meaning the command failed."""
        scan_mod._run = lambda cmd, cwd=None, timeout=30: handler(cmd)

    def stub_gh(self, remotes, archived_json):
        """`remotes`: repo path -> origin URL (or None). `archived_json`: slug ->
        raw stdout for `gh repo view` (or None for a failed call)."""
        def handler(cmd):
            if cmd[0] == "git":
                return remotes.get(cmd[2])
            if cmd[0] == "gh":
                return archived_json.get(cmd[3])
            raise AssertionError(f"unexpected command: {cmd}")
        self.stub_run(handler)


class TestRepoSlug(ScannerTestCase):
    def test_parses_every_remote_shape(self):
        cases = {
            "git@github.com:AuraHealth/KlaviyoService.git": "AuraHealth/KlaviyoService",
            "https://github.com/AuraHealth/KlaviyoService.git": "AuraHealth/KlaviyoService",
            "https://github.com/AuraHealth/KlaviyoService": "AuraHealth/KlaviyoService",
            "https://github.com/AuraHealth/KlaviyoService/": "AuraHealth/KlaviyoService",
            "ssh://git@github.com/AuraHealth/Repo.git\n": "AuraHealth/Repo",
        }
        for url, want in cases.items():
            with self.subTest(url=url):
                self.stub_run(lambda cmd, u=url: u)
                self.assertEqual(scan_mod.repo_slug("/ws/Services/X"), want)

    def test_non_github_remote_is_unknown(self):
        self.stub_run(lambda cmd: "git@gitlab.com:team/thing.git")
        self.assertIsNone(scan_mod.repo_slug("/ws/Services/X"))

    def test_no_origin_is_unknown(self):
        # `git remote get-url origin` exits non-zero -> _run returns None
        self.stub_run(lambda cmd: None)
        self.assertIsNone(scan_mod.repo_slug("/ws/Services/X"))


class TestGithubArchived(ScannerTestCase):
    def test_true_and_false_are_read_from_the_response(self):
        for raw, want in (('{"isArchived":true}', True), ('{"isArchived":false}', False)):
            with self.subTest(raw=raw):
                scan_mod._ARCHIVED_CACHE.clear()
                self.stub_run(lambda cmd, r=raw: r)
                self.assertIs(scan_mod.github_archived("AuraHealth/Svc"), want)

    def test_missing_gh_binary_is_unknown_not_live(self):
        scan_mod.shutil.which = lambda name: None
        # _run stays forbidden: no subprocess may be attempted at all.
        self.assertIsNone(scan_mod.github_archived("AuraHealth/Svc"))

    def test_failed_call_is_unknown_not_live(self):
        # gh present but unauthenticated / API error / timeout -> non-zero exit.
        self.stub_run(lambda cmd: None)
        self.assertIsNone(scan_mod.github_archived("AuraHealth/Svc"))

    def test_unparseable_or_shapeless_response_is_unknown(self):
        for raw in ("not json", "", "[]", "{}", '{"isArchived":null}', "null"):
            with self.subTest(raw=raw):
                scan_mod._ARCHIVED_CACHE.clear()
                self.stub_run(lambda cmd, r=raw: r)
                self.assertIsNone(scan_mod.github_archived("AuraHealth/Svc"))

    def test_no_slug_is_unknown_and_makes_no_call(self):
        self.assertIsNone(scan_mod.github_archived(None))
        self.assertIsNone(scan_mod.github_archived(""))

    def test_result_is_cached_per_slug(self):
        calls = []

        def handler(cmd):
            calls.append(cmd)
            return '{"isArchived":true}'

        self.stub_run(handler)
        self.assertIs(scan_mod.github_archived("AuraHealth/Svc"), True)
        self.assertIs(scan_mod.github_archived("AuraHealth/Svc"), True)
        self.assertEqual(len(calls), 1)

    def test_unknown_is_cached_too(self):
        calls = []

        def handler(cmd):
            calls.append(cmd)
            return None

        self.stub_run(handler)
        self.assertIsNone(scan_mod.github_archived("AuraHealth/Svc"))
        self.assertIsNone(scan_mod.github_archived("AuraHealth/Svc"))
        self.assertEqual(len(calls), 1)


class TestResolveArchived(ScannerTestCase):
    def test_maps_each_repo_to_its_state(self):
        self.stub_gh(
            {"/ws/Services/Klaviyo": "git@github.com:AuraHealth/Klaviyo.git",
             "/ws/Services/User": "git@github.com:AuraHealth/User.git",
             "/ws/Services/Local": None},
            {"AuraHealth/Klaviyo": '{"isArchived":true}',
             "AuraHealth/User": '{"isArchived":false}'},
        )
        got = scan_mod.resolve_archived(
            ["/ws/Services/Klaviyo", "/ws/Services/User", "/ws/Services/Local"])
        self.assertEqual(got, {"/ws/Services/Klaviyo": True,
                               "/ws/Services/User": False,
                               "/ws/Services/Local": None})

    def test_check_false_makes_no_call_and_reports_unknown(self):
        # _run stays forbidden — --no-archived-check must not spawn anything.
        got = scan_mod.resolve_archived(["/ws/a", "/ws/b"], check=False)
        self.assertEqual(got, {"/ws/a": None, "/ws/b": None})

    def test_empty_repo_list(self):
        self.assertEqual(scan_mod.resolve_archived([]), {})


class TestArchivedLabel(unittest.TestCase):
    def test_three_distinct_labels(self):
        self.assertEqual(scan_mod.archived_label(True), "ARCHIVED")
        self.assertEqual(scan_mod.archived_label(False), "")
        # The unknown must be visible in the table, not silently blank like live.
        self.assertEqual(scan_mod.archived_label(None), "archived=unknown")


class TestFindDrift(unittest.TestCase):
    def test_archived_repo_is_bucketed_not_reported_as_drift(self):
        report = {"Services": [
            row("A", "24.19.0"), row("B", "24.19.0"),
            row("Klaviyo", "24.18.0", archived=True),
        ]}
        dominant, drift, archived_drift = scan_mod.find_drift(report)
        self.assertEqual(dominant, "24.19.0")
        self.assertEqual(drift, [])
        self.assertEqual([d["repo"] for d in archived_drift], ["Klaviyo"])

    def test_archived_repo_does_not_vote_for_dominant(self):
        # Two live on the new release, three archived stragglers on the old one:
        # the archived majority must not drag the dominant version backwards.
        report = {"Services": [
            row("A", "24.19.0"), row("B", "24.19.0"),
            row("X", "24.18.0", archived=True),
            row("Y", "24.18.0", archived=True),
            row("Z", "24.18.0", archived=True),
        ]}
        dominant, drift, _ = scan_mod.find_drift(report)
        self.assertEqual(dominant, "24.19.0")
        self.assertEqual(drift, [])

    def test_unknown_still_counts_as_drift(self):
        report = {"Services": [
            row("A", "24.19.0"), row("B", "24.19.0"),
            row("Maybe", "24.18.0", archived=None),
        ]}
        dominant, drift, archived_drift = scan_mod.find_drift(report)
        self.assertEqual(dominant, "24.19.0")
        self.assertEqual([d["repo"] for d in drift], ["Maybe"])
        self.assertEqual(archived_drift, [])

    def test_live_drift_is_unchanged(self):
        report = {"Services": [
            row("A", "24.19.0", dockerfile="24.19.0"),
            row("B", "24.19.0", dockerfile="24.19.0"),
            row("Stale", "24.19.0", dockerfile="24.18.0"),
        ]}
        dominant, drift, archived_drift = scan_mod.find_drift(report)
        self.assertEqual(dominant, "24.19.0")
        self.assertEqual([d["repo"] for d in drift], ["Stale"])
        # `pins` keeps the full pin list, not just the offending ones.
        self.assertEqual(drift[0]["pins"], ["24.19.0", "24.18.0", None])
        self.assertEqual(archived_drift, [])

    def test_all_archived_yields_no_dominant(self):
        report = {"Services": [row("X", "24.18.0", archived=True)]}
        self.assertEqual(scan_mod.find_drift(report), (None, [], []))

    def test_functions_engine_major_drift(self):
        report = {"Functions": [
            row("A", "24.19.0", functions_engine="24"),
            row("B", "24.19.0", functions_engine="22"),
        ]}
        _, drift, _ = scan_mod.find_drift(report)
        self.assertEqual([d["repo"] for d in drift], ["B"])


class TestFindOffTarget(unittest.TestCase):
    def test_archived_gets_its_own_bucket(self):
        report = {"Services": [
            row("A", "24.19.0", dockerfile="24.19.0", circleci="24.19.0"),
            row("Klaviyo", "24.18.0", dockerfile="24.18.0", archived=True),
        ]}
        off, archived_off = scan_mod.find_off_target(report, "24.19.0")
        # This is the assertion that makes the rollout finishable: an archived
        # repo must not keep --expect exiting non-zero forever.
        self.assertEqual(off, [])
        self.assertEqual([o["repo"] for o in archived_off], ["Klaviyo"])
        self.assertEqual(archived_off[0]["off"], ["24.18.0", "24.18.0"])

    def test_unknown_stays_in_off_target(self):
        report = {"Services": [row("Maybe", "24.18.0", archived=None)]}
        off, archived_off = scan_mod.find_off_target(report, "24.19.0")
        self.assertEqual([o["repo"] for o in off], ["Maybe"])
        self.assertEqual(archived_off, [])

    def test_on_target_repo_is_in_neither_bucket(self):
        report = {"Services": [
            row("A", "24.19.0", dockerfile="24.19.0", circleci="24.19.0",
                functions_engine="24"),
            row("Klaviyo", "24.19.0", archived=True),
        ]}
        self.assertEqual(scan_mod.find_off_target(report, "24.19.0"), ([], []))


class TestApplyPhase(ScannerTestCase):
    """`--apply` write behaviour, against a real temp workspace."""

    def setUp(self):
        super().setUp()
        import tempfile
        self.root = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.root, True)

    def make_repo(self, name, version="24.18.0", phase="Services"):
        path = os.path.join(self.root, phase, name)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, ".nvmrc"), "w", encoding="utf-8") as f:
            f.write(version + "\n")
        with open(os.path.join(path, "Dockerfile"), "w", encoding="utf-8") as f:
            f.write(f"FROM node:{version}-alpine\nRUN true\n")
        return path

    def stub_states(self, states):
        """states: repo basename -> archived value. Bypasses the gh lookup so the
        write behaviour is tested against a known state rather than a live repo."""
        # Capture the original BEFORE overwriting — restoring the stub instead
        # would leak it into every later test in the module.
        original = scan_mod.resolve_archived
        self.addCleanup(setattr, scan_mod, "resolve_archived", original)
        scan_mod.resolve_archived = lambda repos, check=True: {
            r: states.get(os.path.basename(r)) for r in repos}

    def test_archived_repo_is_skipped_and_reported(self):
        live = self.make_repo("UserService")
        arch = self.make_repo("KlaviyoService")
        self.stub_states({"UserService": False, "KlaviyoService": True})
        changes = scan_mod.apply_phase(self.root, "Services", "24.19.0")
        by_repo = {c["repo"]: c for c in changes}

        self.assertEqual(by_repo["KlaviyoService"]["skipped"], "archived")
        self.assertEqual(by_repo["KlaviyoService"]["changed"], [])
        # Reported, not silently dropped from the output.
        self.assertIn("KlaviyoService", by_repo)
        # And genuinely not written — this is the 403 that never happens now.
        self.assertEqual(scan_mod.read_nvmrc(arch), "24.18.0")

        self.assertEqual(by_repo["UserService"]["changed"], [".nvmrc", "Dockerfile"])
        self.assertEqual(scan_mod.read_nvmrc(live), "24.19.0")

    def test_include_archived_writes_it(self):
        arch = self.make_repo("KlaviyoService")
        self.stub_states({"KlaviyoService": True})
        changes = scan_mod.apply_phase(self.root, "Services", "24.19.0",
                                       include_archived=True)
        self.assertNotIn("skipped", changes[0])
        self.assertEqual(changes[0]["changed"], [".nvmrc", "Dockerfile"])
        self.assertEqual(scan_mod.read_nvmrc(arch), "24.19.0")

    def test_unknown_is_written_fail_open(self):
        # A false "archived" would silently skip a live repo — worse than a 403.
        maybe = self.make_repo("MaybeService")
        self.stub_states({"MaybeService": None})
        changes = scan_mod.apply_phase(self.root, "Services", "24.19.0")
        self.assertNotIn("skipped", changes[0])
        self.assertIsNone(changes[0]["archived"])
        self.assertEqual(scan_mod.read_nvmrc(maybe), "24.19.0")

    def test_no_archived_check_writes_everything(self):
        arch = self.make_repo("KlaviyoService")
        # check_archived=False must not consult gh at all (_run is forbidden),
        # and everything reads as unknown, hence written.
        changes = scan_mod.apply_phase(self.root, "Services", "24.19.0",
                                       check_archived=False)
        self.assertEqual(changes[0]["changed"], [".nvmrc", "Dockerfile"])
        self.assertEqual(scan_mod.read_nvmrc(arch), "24.19.0")

    def test_idempotent_reapply_on_live_repo(self):
        self.make_repo("UserService", version="24.19.0")
        self.stub_states({"UserService": False})
        changes = scan_mod.apply_phase(self.root, "Services", "24.19.0")
        self.assertEqual(changes[0]["changed"], [])


class TestScanIncludesArchived(ScannerTestCase):
    def test_scan_annotates_each_row(self):
        import tempfile
        root = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, root, True)
        for name in ("UserService", "KlaviyoService"):
            path = os.path.join(root, "Services", name)
            os.makedirs(path)
            with open(os.path.join(path, ".nvmrc"), "w", encoding="utf-8") as f:
                f.write("24.19.0\n")
        self.stub_gh(
            {os.path.join(root, "Services", "UserService"):
                "git@github.com:AuraHealth/UserService.git",
             os.path.join(root, "Services", "KlaviyoService"):
                "git@github.com:AuraHealth/KlaviyoService.git"},
            {"AuraHealth/UserService": '{"isArchived":false}',
             "AuraHealth/KlaviyoService": '{"isArchived":true}'},
        )
        report = scan_mod.scan(root, ["Services"])
        states = {r["repo"]: r["archived"] for r in report["Services"]}
        self.assertEqual(states, {"UserService": False, "KlaviyoService": True})
        # The field must be present and JSON-serialisable for the calling skill.
        json.dumps(report)


class TestNestedNvmrc(unittest.TestCase):
    """4U-engine keeps a `.nvmrc` per model (models/*/.nvmrc). Reading only the root one let two
    models' pins drift from their images unseen (ENGBACK-3235)."""

    def repo(self, files):
        import tempfile
        root = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, root)
        for rel, text in files.items():
            p = os.path.join(root, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
        return root

    def test_a_nested_pin_off_the_root_reads_as_mixed(self):
        r = self.repo({".nvmrc": "26.13.1\n", "models/a/.nvmrc": "24.18.0\n", "models/b/.nvmrc": "26.13.1\n"})
        self.assertEqual(scan_mod.read_nvmrc(r), "MIXED:24.18.0,26.13.1")

    def test_agreeing_pins_read_as_one_version(self):
        r = self.repo({".nvmrc": "26.13.1\n", "models/a/.nvmrc": "26.13.1\n"})
        self.assertEqual(scan_mod.read_nvmrc(r), "26.13.1")

    def test_vendored_and_hidden_trees_are_skipped(self):
        r = self.repo({".nvmrc": "26.13.1\n", "node_modules/x/.nvmrc": "18.0.0\n",
                       ".claude/worktrees/wt/.nvmrc": "24.18.0\n"})
        self.assertEqual(scan_mod.read_nvmrc(r), "26.13.1")

    def test_no_nvmrc_is_none(self):
        self.assertIsNone(scan_mod.read_nvmrc(self.repo({"README.md": "x"})))

    def test_apply_rewrites_every_nvmrc(self):
        r = self.repo({".nvmrc": "24.18.0\n", "models/a/.nvmrc": "24.18.0\n", "node_modules/x/.nvmrc": "18.0.0\n"})
        self.assertTrue(scan_mod.write_nvmrc(r, "26.13.1"))
        self.assertEqual(scan_mod.read_nvmrc(r), "26.13.1")
        with open(os.path.join(r, "node_modules/x/.nvmrc"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "18.0.0\n")
        self.assertFalse(scan_mod.write_nvmrc(r, "26.13.1"))

    def test_mixed_is_drift_and_does_not_vote(self):
        report = {"Models": [row("4U-engine", nvmrc="MIXED:24.18.0,26.13.1")],
                  "Services": [row("A", nvmrc="26.13.1"), row("B", nvmrc="26.13.1")]}
        dominant, drift, _ = scan_mod.find_drift(report)
        self.assertEqual(dominant, "26.13.1")
        self.assertEqual([d["repo"] for d in drift], ["4U-engine"])
        off, _ = scan_mod.find_off_target(report, "26.13.1")
        self.assertEqual([o["repo"] for o in off], ["4U-engine"])


if __name__ == "__main__":
    unittest.main()
