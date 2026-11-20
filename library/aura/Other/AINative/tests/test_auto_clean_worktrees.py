"""eng:clean-up-worktrees auto_clean.py (ENG-2016): what it removes and what it must never remove.

Real git repos and worktrees in a temp dir; PR states, running processes and booted simulators are injected,
because those are the three inputs only the host can answer. The failure cases are the point: an open PR,
local commits past the merged head, ignored data, a process in the tree, a fresh touch, a primary checkout,
and a gh failure must each keep the worktree.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

SKILL = pathlib.Path(__file__).resolve().parents[1] / "plugins/eng/skills/clean-up-worktrees/scripts"
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("auto_clean", SKILL / "auto_clean.py")
ac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ac)

NOBODY = (set(), [], set())  # no cwd, no command line, no booted simulator


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class AutoCleanTest(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.repo = self.root / "Group" / "Repo"
        self.repo.mkdir(parents=True)
        sh(self.repo, "git", "init", "-q", "-b", "main")
        sh(self.repo, "git", "config", "user.email", "t@t")
        sh(self.repo, "git", "config", "user.name", "t")
        (self.repo / "a.txt").write_text("a")
        sh(self.repo, "git", "add", ".")
        sh(self.repo, "git", "commit", "-qm", "init")
        self.now = time.time() + 3 * 86400  # three days later: everything git wrote is idle

    def worktree(self, name, branch):
        wt = self.root / name
        sh(self.repo, "git", "worktree", "add", "-q", "-b", branch, str(wt))
        (wt / "f.txt").write_text(name)
        sh(wt, "git", "add", ".")
        sh(wt, "git", "commit", "-qm", name)
        return wt

    def plan(self, prs, busy=NOBODY, **kw):
        return ac.plan(str(self.root), now=self.now, prs=lambda repo: prs, busy=busy, **kw)

    def head(self, wt):
        return sh(wt, "git", "rev-parse", "HEAD")

    def reasons(self, p):
        return {pathlib.Path(s["path"]).name: s["reason"] for s in p["skipped"]}

    def test_merged_clean_idle_worktree_is_planned_and_dry_run_removes_nothing(self):
        wt = self.worktree("w1", "feat/one")
        p = self.plan({"feat/one": ("MERGED", self.head(wt))})
        self.assertEqual([x["path"] for x in p["remove"]], [str(wt)])
        self.assertTrue(wt.exists())

    def test_apply_removes_the_worktree_and_logs_it(self):
        wt = self.worktree("w1", "feat/one")
        p = self.plan({"feat/one": ("MERGED", self.head(wt))})
        log = self.root / "log" / "auto.jsonl"
        done = ac.apply(p, str(log))
        self.assertTrue(done[0]["ok"])
        self.assertFalse(wt.exists())
        self.assertEqual(json.loads(log.read_text().splitlines()[0])["action"], "remove-worktree")
        self.assertEqual(oct(log.stat().st_mode & 0o777), "0o600")
        self.assertIn("feat/one", sh(self.repo, "git", "branch"))  # the branch survives

    def test_open_pr_is_kept(self):
        wt = self.worktree("w1", "feat/one")
        p = self.plan({"feat/one": ("OPEN", self.head(wt))})
        self.assertEqual(p["remove"], [])
        self.assertEqual(self.reasons(p)["w1"], "PR OPEN")

    def test_no_pr_is_kept(self):
        self.worktree("w1", "feat/one")
        p = self.plan({})
        self.assertEqual(p["remove"], [])

    def test_unknown_pr_state_keeps_everything(self):
        self.worktree("w1", "feat/one")
        p = self.plan(None)
        self.assertEqual(p["remove"], [])
        self.assertIn("unknown", self.reasons(p)["w1"])

    def test_local_commits_past_the_merged_head_are_kept(self):
        wt = self.worktree("w1", "feat/one")
        merged = self.head(wt)
        (wt / "more.txt").write_text("later work")
        sh(wt, "git", "add", ".")
        sh(wt, "git", "commit", "-qm", "after the merge")
        p = self.plan({"feat/one": ("MERGED", merged)})
        self.assertEqual(p["remove"], [])
        self.assertIn("beyond the merged PR head", self.reasons(p)["w1"])

    def test_head_behind_the_merged_head_counts_as_merged(self):
        wt = self.worktree("w1", "feat/one")
        old = self.head(wt)
        (wt / "more.txt").write_text("x")
        sh(wt, "git", "add", ".")
        sh(wt, "git", "commit", "-qm", "merged too")
        newer = self.head(wt)
        sh(wt, "git", "reset", "-q", "--hard", old)
        p = self.plan({"feat/one": ("MERGED", newer)})
        self.assertEqual(len(p["remove"]), 1)

    def test_ignored_data_file_keeps_the_worktree(self):
        wt = self.worktree("w1", "feat/one")
        (wt / ".gitignore").write_text("users.json\n")
        sh(wt, "git", "add", ".")
        sh(wt, "git", "commit", "-qm", "ignore")
        (wt / "users.json").write_text("the only copy")
        p = self.plan({"feat/one": ("MERGED", self.head(wt))})
        self.assertEqual(p["remove"], [])
        self.assertIn("data at risk", self.reasons(p)["w1"])

    def test_uncommitted_change_keeps_the_worktree(self):
        wt = self.worktree("w1", "feat/one")
        (wt / "f.txt").write_text("edited")
        p = self.plan({"feat/one": ("MERGED", self.head(wt))})
        self.assertEqual(p["remove"], [])

    def test_process_in_the_tree_keeps_it(self):
        wt = self.worktree("w1", "feat/one")
        busy = ({str(wt / "sub")}, [], set())
        p = self.plan({"feat/one": ("MERGED", self.head(wt))}, busy=busy)
        self.assertEqual(p["remove"], [])
        self.assertEqual(self.reasons(p)["w1"], "a process runs in it")

    def test_command_naming_its_path_keeps_it(self):
        wt = self.worktree("w1", "feat/one")
        busy = (set(), [f"xcodebuild -derivedDataPath {wt}/DerivedData-CLI test"], set())
        p = self.plan({"feat/one": ("MERGED", self.head(wt))}, busy=busy)
        self.assertEqual(p["remove"], [])

    def test_booted_simulator_with_its_label_keeps_it(self):
        wt = self.worktree("w1", "feat/one")
        p = self.plan({"feat/one": ("MERGED", self.head(wt))}, busy=(set(), [], {"w1"}))
        self.assertEqual(p["remove"], [])
        self.assertEqual(self.reasons(p)["w1"], "booted simulator")

    def test_recent_git_activity_keeps_it_even_when_folder_mtime_is_old(self):
        wt = self.worktree("w1", "feat/one")
        old = time.time() - 30 * 86400
        os.utime(wt, (old, old))  # an old folder mtime says nothing about git's own files
        self.now = time.time()
        p = self.plan({"feat/one": ("MERGED", self.head(wt))})
        self.assertEqual(p["remove"], [])
        self.assertIn("touched within", self.reasons(p)["w1"])

    def test_folder_touched_by_a_script_does_not_hide_an_idle_worktree(self):
        wt = self.worktree("w1", "feat/one")
        os.utime(wt, (self.now, self.now))  # pull.sh-style touch: folder looks brand new
        p = self.plan({"feat/one": ("MERGED", self.head(wt))})
        self.assertEqual(len(p["remove"]), 1)

    def test_newest_pr_wins_when_a_merged_branch_got_a_new_open_pr(self):
        wt = self.worktree("w1", "feat/one")
        p = self.plan({"feat/one": ("OPEN", self.head(wt))})  # gh_pr_states keeps the highest number
        self.assertEqual(p["remove"], [])

    def test_detached_head_is_kept(self):
        wt = self.worktree("w1", "feat/one")
        sh(wt, "git", "checkout", "-q", "--detach")
        p = self.plan({"feat/one": ("MERGED", self.head(wt))})
        self.assertEqual(p["remove"], [])
        self.assertEqual(self.reasons(p)["w1"], "detached HEAD")

    def test_primary_checkout_is_never_removed(self):
        p = self.plan({"main": ("MERGED", self.head(self.repo))})
        self.assertEqual(p["remove"], [])
        self.assertTrue(self.repo.exists())

    def test_derived_data_idle_is_deleted_and_recent_is_kept(self):
        old_wt = self.worktree("old", "feat/old")
        new_wt = self.worktree("new", "feat/new")
        for wt, age in ((old_wt, 5 * 86400), (new_wt, 3600)):
            d = wt / "DerivedData-CLI" / "Build" / "Products"
            d.mkdir(parents=True)
            (d / "app").write_text("x" * 100)
            t = self.now - age
            for p in (wt / "DerivedData-CLI", wt / "DerivedData-CLI" / "Build", d):
                os.utime(p, (t, t))
        prs = {"feat/old": ("OPEN", ""), "feat/new": ("OPEN", "")}
        p = self.plan(prs)
        self.assertEqual([x["path"] for x in p["derived"]], [str(old_wt / "DerivedData-CLI")])
        ac.apply(p, str(self.root / "l.jsonl"))
        self.assertFalse((old_wt / "DerivedData-CLI").exists())
        self.assertTrue((new_wt / "DerivedData-CLI").exists())
        self.assertTrue(old_wt.exists())  # only the build output goes, never the worktree

    def test_derived_data_in_use_is_kept(self):
        wt = self.worktree("w1", "feat/one")
        d = wt / "DerivedData-CLI"
        d.mkdir()
        os.utime(d, (self.now - 9 * 86400,) * 2)
        p = self.plan({"feat/one": ("OPEN", "")}, busy=({str(wt)}, [], set()))
        self.assertEqual(p["derived"], [])

    def test_keep_marker_protects_derived_data(self):
        wt = self.worktree("w1", "feat/one")
        d = wt / "DerivedData-CLI"
        d.mkdir()
        os.utime(d, (self.now - 9 * 86400,) * 2)
        (wt / ".keep-derived-data").write_text("")
        p = self.plan({"feat/one": ("OPEN", "")})
        self.assertEqual(p["derived"], [])
        self.assertIn("marker", self.reasons(p)["DerivedData-CLI"])

    def test_a_fresh_index_does_not_make_an_idle_worktree_look_busy(self):
        wt = self.worktree("w1", "feat/one")
        gd = pathlib.Path(sh(wt, "git", "rev-parse", "--absolute-git-dir"))
        os.utime(gd / "index", (self.now, self.now))  # what `git status` does
        p = self.plan({"feat/one": ("MERGED", self.head(wt))})
        self.assertEqual(len(p["remove"]), 1)

    def test_nested_recent_file_keeps_derived_data_even_when_top_folders_are_old(self):
        wt = self.worktree("w1", "feat/one")
        d = wt / "DerivedData-CLI" / "Build" / "Products" / "Debug"
        d.mkdir(parents=True)
        f = d / "Aura.o"
        f.write_text("x")
        old = self.now - 9 * 86400
        for p in (wt / "DerivedData-CLI", wt / "DerivedData-CLI" / "Build",
                  wt / "DerivedData-CLI" / "Build" / "Products", d):
            os.utime(p, (old, old))
        os.utime(f, (self.now - 3600, self.now - 3600))  # an incremental compile this morning
        p = self.plan({"feat/one": ("OPEN", "")})
        self.assertEqual(p["derived"], [])
        self.assertIn("touched within", self.reasons(p)["DerivedData-CLI"])

    def test_gitignored_build_output_does_not_keep_a_merged_worktree(self):
        wt = self.worktree("w1", "feat/one")
        (wt / ".gitignore").write_text("DerivedData-CLI/\nTestResults/\n")
        sh(wt, "git", "add", ".")
        sh(wt, "git", "commit", "-qm", "ignore build output")
        for name in ("DerivedData-CLI", "TestResults"):
            (wt / name).mkdir()
            (wt / name / "out.bin").write_text("regenerable")
        p = self.plan({"feat/one": ("MERGED", self.head(wt))})
        self.assertEqual([x["path"] for x in p["remove"]], [str(wt)])

    def test_failed_process_check_removes_nothing(self):
        wt = self.worktree("w1", "feat/one")
        p = self.plan({"feat/one": ("MERGED", self.head(wt))}, busy=None)
        self.assertEqual(p["remove"] + p["derived"], [])
        self.assertEqual(self.reasons(p)["Repo"], "process check failed")

    def test_busy_state_fails_closed_when_a_tool_is_missing_or_empty(self):
        real = ac.subprocess.run
        try:
            def boom(*a, **k):
                raise FileNotFoundError("lsof")
            ac.subprocess.run = boom
            self.assertIsNone(ac.busy_state())

            class R:  # lsof answered with nothing: no machine has zero working directories
                returncode, stdout, stderr = 0, "", ""
            ac.subprocess.run = lambda *a, **k: R()
            self.assertIsNone(ac.busy_state())
        finally:
            ac.subprocess.run = real

    def test_simulator_label_is_sanitized_like_sim_test_does(self):
        wt = self.worktree("my wt", "feat/one")  # sim-test labels it "my-wt"
        p = self.plan({"feat/one": ("MERGED", self.head(wt))}, busy=(set(), [], {"my-wt"}))
        self.assertEqual(p["remove"], [])
        self.assertEqual(self.reasons(p)["my wt"], "booted simulator")

    def test_booted_main_simulator_protects_the_primary_checkouts_build_output(self):
        d = self.repo / "DerivedData-CLI"
        d.mkdir()
        os.utime(d, (self.now - 9 * 86400,) * 2)
        p = self.plan({}, busy=(set(), [], {"main"}))
        self.assertEqual(p["derived"], [])
        self.assertEqual(self.reasons(p)["DerivedData-CLI"], "booted simulator")

    def test_apply_deletes_simulators_of_removed_worktrees_only(self):
        gone = self.worktree("w1", "feat/one")
        kept = self.worktree("w2", "feat/two")
        p = self.plan({"feat/one": ("MERGED", self.head(gone)), "feat/two": ("OPEN", self.head(kept))})
        calls = []
        ac.apply(p, str(self.root / "l.jsonl"), sims_fn=lambda paths: calls.append(paths) or {})
        self.assertEqual(calls, [[str(gone)]])

    def test_simulator_delete_failure_is_surfaced_and_fails_the_run(self):
        gone = self.worktree("w1", "feat/one")
        p = self.plan({"feat/one": ("MERGED", self.head(gone))})
        ac.apply(p, str(self.root / "l.jsonl"),
                 sims_fn=lambda paths: {"simulatorsDeleted": 0, "simulatorsSkipped": 1})
        self.assertEqual(p["simulators"]["simulatorsSkipped"], 1)
        self.assertTrue(ac.has_failures(p))

    def test_clean_apply_is_not_a_failure(self):
        gone = self.worktree("w1", "feat/one")
        p = self.plan({"feat/one": ("MERGED", self.head(gone))})
        ac.apply(p, str(self.root / "l.jsonl"), sims_fn=lambda paths: {"simulatorsDeleted": 1, "simulatorsSkipped": 0})
        self.assertFalse(ac.has_failures(p))

    def test_root_is_required_without_the_environment_setting(self):
        saved = os.environ.pop("AURA_REPOS_DIR", None)
        try:
            with self.assertRaises(SystemExit):
                ac.main([])
        finally:
            if saved is not None:
                os.environ["AURA_REPOS_DIR"] = saved

    def test_derived_data_symlink_is_not_followed(self):
        wt = self.worktree("w1", "feat/one")
        target = self.root / "elsewhere"
        target.mkdir()
        (target / "keep.txt").write_text("k")
        os.symlink(target, wt / "DerivedData-CLI")
        p = self.plan({"feat/one": ("OPEN", "")})
        self.assertEqual(p["derived"], [])
        self.assertTrue((target / "keep.txt").exists())

    def test_primary_checkout_derived_data_is_cleared_when_idle(self):
        d = self.repo / "DerivedData-CLI"
        d.mkdir()
        os.utime(d, (self.now - 9 * 86400,) * 2)
        p = self.plan({})
        self.assertEqual([x["path"] for x in p["derived"]], [str(d)])

    def test_dangling_worktree_reference_is_not_planned(self):
        wt = self.worktree("w1", "feat/one")
        sh(self.root, "mv", str(wt), str(self.root / "moved"))
        p = self.plan({"feat/one": ("MERGED", "x")})
        self.assertEqual(p["remove"], [])


if __name__ == "__main__":
    unittest.main()
