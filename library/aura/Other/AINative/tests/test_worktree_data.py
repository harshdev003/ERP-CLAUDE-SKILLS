"""eng:clean-up-worktrees (ENG-1828): what removing a worktree would lose.

2026-11-06: a cleanup removed a CLI worktree as "clean" while it held gitignored, mode-600 input data
(users.full.json, a 50k sample, mixpanel.jsonl). `git status` doesn't show ignored files and
`git worktree remove --force` drops them. These tests build real repos and worktrees in that shape.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
import unittest

SCRIPT = (pathlib.Path(__file__).resolve().parents[1]
          / "plugins/eng/skills/clean-up-worktrees/scripts/worktree_data.py")
spec = importlib.util.spec_from_file_location("worktree_data", SCRIPT)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)


def sh(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


class WorktreeDataTest(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.repo = self.root / "repo"
        self.repo.mkdir()
        sh(self.repo, "git", "init", "-q", "-b", "main")
        sh(self.repo, "git", "config", "user.email", "t@example.com")
        sh(self.repo, "git", "config", "user.name", "t")
        (self.repo / ".gitignore").write_text("data/*\nnode_modules/\nbuild/\n")
        (self.repo / "README.md").write_text("x\n")
        sh(self.repo, "git", "add", ".")
        sh(self.repo, "git", "commit", "-q", "-m", "init")
        self.wt = self.root / "wt"
        sh(self.repo, "git", "worktree", "add", "-q", str(self.wt), "-b", "feature")

    def test_a_worktree_git_calls_clean_can_hold_ignored_data(self):
        data = self.wt / "data" / "cleanup-users"
        data.mkdir(parents=True)
        (data / "users.full.json").write_text("[" + ",".join(['"u"'] * 1000) + "]")
        os.chmod(data / "users.full.json", 0o600)
        (data / "mixpanel.jsonl").write_text("{}\n")
        out = subprocess.run(["git", "-C", str(self.wt), "status", "--porcelain"], capture_output=True, text=True)
        self.assertEqual(out.stdout, "")  # what made it look clean
        r = wd.at_risk(str(self.wt))
        self.assertTrue(r["atRisk"])
        self.assertEqual(sorted(e["path"] for e in r["ignored"]),
                         ["data/cleanup-users/mixpanel.jsonl", "data/cleanup-users/users.full.json"])
        self.assertGreater(r["bytes"], 4000)

    def test_regenerable_folders_are_not_data(self):
        (self.wt / "node_modules" / "x").mkdir(parents=True)
        (self.wt / "node_modules" / "x" / "index.js").write_text("1")
        (self.wt / "build").mkdir()
        (self.wt / "build" / "out.js").write_text("1")
        (self.wt / ".DS_Store").write_text("1")
        r = wd.at_risk(str(self.wt))
        self.assertFalse(r["atRisk"], r)

    def test_untracked_modified_and_unpushed_are_at_risk(self):
        (self.wt / "notes.txt").write_text("draft")
        (self.wt / "README.md").write_text("changed\n")
        r = wd.at_risk(str(self.wt))
        self.assertEqual([e["path"] for e in r["untracked"]], ["notes.txt"])
        self.assertEqual(r["modified"], 1)
        sh(self.wt, "git", "commit", "-q", "-am", "local only")
        r = wd.at_risk(str(self.wt))
        self.assertGreaterEqual(r["unpushed"], 1)  # kept on the branch: reported, not at risk by itself
        self.assertEqual(r["orphaned"], 0)
        os.remove(self.wt / "notes.txt")
        self.assertFalse(wd.at_risk(str(self.wt))["atRisk"])

    def test_commits_on_a_detached_head_are_at_risk(self):
        sh(self.wt, "git", "checkout", "-q", "--detach")
        (self.wt / "f.txt").write_text("1")
        sh(self.wt, "git", "add", "f.txt")
        sh(self.wt, "git", "commit", "-q", "-m", "detached work")
        r = wd.at_risk(str(self.wt))
        self.assertEqual(r["orphaned"], 1)
        self.assertTrue(r["atRisk"])

    def test_report_and_log_carry_paths_and_sizes_never_content(self):
        (self.wt / "data").mkdir()
        (self.wt / "data" / "secret.json").write_text('{"uid": "SECRETVALUE"}')
        res = subprocess.run(["python3", str(SCRIPT), "report", str(self.wt)], capture_output=True, text=True)
        rep = json.loads(res.stdout)
        self.assertEqual(rep["atRisk"], 1)
        self.assertEqual(rep["worktrees"][0]["largest"][0]["path"], "data/secret.json")
        self.assertNotIn("SECRETVALUE", res.stdout)
        log = self.root / "removed.jsonl"
        subprocess.run(["python3", str(SCRIPT), "log", str(self.wt), "--log", str(log)], check=True,
                       capture_output=True)
        text = log.read_text()
        self.assertIn("data/secret.json", text)
        self.assertNotIn("SECRETVALUE", text)
        self.assertEqual(os.stat(log).st_mode & 0o777, 0o600)

    def test_a_missing_worktree_is_reported_not_crashed(self):
        r = wd.at_risk(str(self.root / "gone"))
        self.assertEqual((r["error"], r["atRisk"]), ("missing", False))

    def test_a_worktree_that_cant_be_inspected_is_kept(self):
        broken = self.root / "notgit"
        broken.mkdir()
        (broken / ".git").write_text("gitdir: /nowhere\n")
        r = wd.at_risk(str(broken))
        self.assertEqual((r["error"], r["atRisk"]), ("git status failed", True))
        self.assertTrue(wd.summary(r)["atRisk"])

    def test_special_and_non_ascii_names_are_found(self):
        (self.wt / "data").mkdir()
        for name in ("données été.json", 'quote"d name.txt', "tab\tname.txt"):
            (self.wt / "data" / name).write_text("x")
        r = wd.at_risk(str(self.wt))
        self.assertEqual(sorted(e["path"] for e in r["ignored"]),
                         sorted(f"data/{n}" for n in ("données été.json", 'quote"d name.txt', "tab\tname.txt")))

    def test_log_creates_its_folder(self):
        (self.wt / "data").mkdir()
        (self.wt / "data" / "x.json").write_text("1")
        log = self.root / "new" / "dir" / "removed.jsonl"
        subprocess.run(["python3", str(SCRIPT), "log", str(self.wt), "--log", str(log)], check=True,
                       capture_output=True)
        self.assertTrue(log.exists())
        self.assertEqual(os.stat(log.parent).st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
