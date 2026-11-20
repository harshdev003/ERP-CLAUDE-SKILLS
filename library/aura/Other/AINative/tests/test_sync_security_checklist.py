"""shared:sync-security-checklist carries the canonical checklist to every repo's copies.

The rules that matter: the renderer reproduces the copies' Markdown (so an edit's old text is
found), an edit lands only where its old text appears exactly once and never overwrites a file,
BUGBOT.md is edited and never replaced, drift is measured within a layer, the rollout stays
docs-only with no card id in branch or title, and a merge needs a clean docs-only PR, a hold
list, and stops at the first failure. Git runs on real temp repos; GitHub and Notion are stubbed.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/shared/skills/sync-security-checklist/scripts/sync.py"


def load():
    spec = importlib.util.spec_from_file_location("sync_security_checklist", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = load()


def rt(text, bold=False, italic=False, code=False, href=None):
    return {"plain_text": text, "href": href,
            "annotations": {"bold": bold, "italic": italic, "code": code}}


def block(kind, *runs, children=None, **extra):
    b = {"type": kind, kind: {"rich_text": list(runs), **extra}}
    if children:
        b["_children"] = children
    return b


def git(args, cwd):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if p.returncode:
        raise AssertionError(f"git {args}: {p.stderr}")
    return p.stdout.strip()


class Render(unittest.TestCase):
    def test_the_copies_layout(self):
        blocks = [
            block("paragraph", rt("Intro with "), rt("bold", bold=True), rt(".")),
            block("heading_2", rt("A. All repos")),
            block("heading_3", rt("A1. Secrets")),
            block("bulleted_list_item", rt("Never log:", bold=True), children=[
                block("bulleted_list_item", rt("tokens;")),
                block("paragraph", rt("Log ids instead.")),
            ]),
            block("bulleted_list_item", rt("The user is ", bold=True), rt("{id, role}", bold=True, code=True),
                  rt(" at most.", bold=True), rt(" See "), rt("the guide", href="https://x"), rt(" "),
                  rt("never", italic=True), rt(".")),
            block("numbered_list_item", rt("one")),
            block("numbered_list_item", rt("two")),
            block("heading_2", rt("Sources")),
            block("bulleted_list_item", rt("a card list that the copies do not carry")),
        ]
        self.assertEqual(S.render(blocks, "Security review checklist"), (
            "# Security review checklist\n\n"
            "Intro with **bold**.\n\n"
            "## A. All repos\n\n"
            "### A1. Secrets\n"
            "- **Never log:**\n"
            "  - tokens;\n\n"
            "  Log ids instead.\n"
            "- **The user is `{id, role}` at most.** See the guide *never*.\n"
            "1. one\n"
            "2. two\n"))

    def test_code_block(self):
        out = S.render([block("code", rt("a()\nb()"), language="javascript")], "T")
        self.assertIn("```javascript\na()\nb()\n```", out)
        self.assertIn("```\nx\n```", S.render([block("code", rt("x"), language="plain text")], "T"))

    def test_an_unknown_block_stops_rather_than_dropping_text(self):
        with self.assertRaises(SystemExit):
            S.render([block("toggle", rt("hidden rule"))], "T")


class Edits(unittest.TestCase):
    OLD = "# T\n\n- rule one\n- rule two\n- rule three\n"

    def test_a_changed_line_lands_where_it_appears_once(self):
        ops = S.edits(self.OLD, self.OLD.replace("rule two", "rule 2"))
        text, applied, missed, multi = S.apply_edits("intro\n- rule two\nend\n", ops)
        self.assertEqual((text, applied, missed, multi), ("intro\n- rule 2\nend\n", 1, [], []))

    def test_absent_old_text_is_a_miss_and_the_file_is_untouched(self):
        ops = S.edits(self.OLD, self.OLD.replace("rule two", "rule 2"))
        text, applied, missed, _ = S.apply_edits("- something else\n", ops)
        self.assertEqual(text, "- something else\n")
        self.assertEqual(applied, 0)
        self.assertEqual(missed, ["- rule two"])

    def test_repeated_old_text_is_ambiguous_not_applied(self):
        ops = S.edits(self.OLD, self.OLD.replace("rule two", "rule 2"))
        text, applied, _, multi = S.apply_edits("- rule two\n- rule two\n", ops)
        self.assertEqual(text, "- rule two\n- rule two\n")
        self.assertEqual(multi, ["- rule two"])

    def test_an_added_line_goes_after_its_anchor_once(self):
        ops = S.edits(self.OLD, self.OLD.replace("- rule two\n", "- rule two\n- rule new\n"))
        text, applied, _, _ = S.apply_edits("x\n- rule two\n- rule three\n", ops)
        self.assertEqual(text, "x\n- rule two\n- rule new\n- rule three\n")
        again, applied2, _, _ = S.apply_edits(text, ops)
        self.assertEqual((again, applied2), (text, 0))


class Layers(unittest.TestCase):
    def test_file_names_and_layers(self):
        self.assertEqual(S.layer("Services/UserService"), "service")
        self.assertEqual(S.layer("Data/Firebase"), "firebase")
        self.assertEqual(S.layer("Functions/FirebaseAuth"), "firebase")
        self.assertEqual(S.layer("Infra/AuraInfra"), "general")
        self.assertIn(".claude/rules/logging.md", S.files_for("Services/UserService"))
        self.assertNotIn(".claude/rules/logging.md", S.files_for("Web/UserWeb"))
        self.assertIn(".claude/rules/security-review.md", S.files_for("Mobile/Aura"))
        self.assertIn(".claude/rules/security.md", S.files_for("Web/UserWeb"))

    def test_the_repo_list_is_the_44(self):
        rs = S.repos()
        self.assertEqual(len(rs), 44)
        self.assertEqual(len(set(rs)), 44)
        self.assertFalse([r for r in rs if r.startswith("/") or "-wt-" in r])


class Fleet(unittest.TestCase):
    """Real git repos with a bare origin each, under a temp AURA_DEV_DIR."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dev = pathlib.Path(self.tmp.name) / "dev"
        self.assets = pathlib.Path(self.tmp.name) / "assets"
        self.assets.mkdir()
        self.env = mock.patch.dict(os.environ, {"AURA_DEV_DIR": str(self.dev),
                                               "SYNC_CHECKLIST_DIR": str(pathlib.Path(self.tmp.name) / "state")})
        self.env.start()
        self.addCleanup(self.env.stop)
        p = mock.patch.object(S, "ASSETS", self.assets)
        p.start()
        self.addCleanup(p.stop)

    def make(self, rel, files):
        bare = pathlib.Path(self.tmp.name) / "remotes" / f"{rel.replace('/', '_')}.git"
        bare.parent.mkdir(exist_ok=True)
        git(["init", "-q", "--bare", "-b", "main", str(bare)], self.tmp.name)
        root = self.dev / rel
        root.mkdir(parents=True)
        git(["init", "-q", "-b", "main"], root)
        git(["config", "user.email", "t@t.t"], root)
        git(["config", "user.name", "t"], root)
        for path, text in files.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(text)
        git(["add", "-A"], root)
        git(["commit", "-q", "-m", "base"], root)
        git(["remote", "add", "origin", str(bare)], root)
        git(["push", "-q", "-u", "origin", "main"], root)
        git(["remote", "set-head", "origin", "main"], root)
        with open(self.assets / "repos.txt", "a") as f:
            f.write(rel + "\n")
        return root

    def snapshots(self, old, new):
        d = pathlib.Path(self.tmp.name)
        for side, text in (("old", old), ("new", new)):
            (d / side).mkdir(exist_ok=True)
            (d / side / "checklist.md").write_text(text)
            (d / side / "logging.md").write_text("# Logging Standards\n\n- log ids\n")
        return d / "old", d / "new"

    def test_plan_edits_each_file_in_place_and_never_overwrites_bugbot(self):
        rule = "# Security review checklist\n\n### A1\n- rule two\n"
        bugbot = "# BugBot review rules: Thing\n\nOwn intro.\n\n---\n\n### A1\n- rule two\n"
        self.make("Web/Thing", {".claude/rules/security.md": rule, ".cursor/rules/security.mdc": "---\nfm\n---\n" + rule,
                                ".cursor/BUGBOT.md": bugbot})
        old, new = self.snapshots(rule, rule.replace("rule two", "rule 2"))
        out = pathlib.Path(self.tmp.name) / "plan"
        rows = S.plan(old, new, out)
        self.assertEqual(rows[0]["files"], {".claude/rules/security.md": 1, ".cursor/rules/security.mdc": 1,
                                            ".cursor/BUGBOT.md": 1})
        b = (out / "Thing/.cursor/BUGBOT.md").read_text()
        self.assertIn("Own intro.", b)
        self.assertIn("- rule 2", b)
        self.assertTrue((out / "Thing/.cursor/rules/security.mdc").read_text().startswith("---\nfm\n---\n"))

    def test_an_ambiguous_edit_leaves_that_file_out_of_the_plan(self):
        rule = "# T\n\n- rule two\n"
        self.make("Web/Thing", {".claude/rules/security.md": rule + "- rule two\n",
                                ".cursor/rules/security.mdc": rule, ".cursor/BUGBOT.md": rule})
        old, new = self.snapshots(rule, rule.replace("rule two", "rule 2"))
        rows = S.plan(old, new, pathlib.Path(self.tmp.name) / "plan")
        self.assertIn(".claude/rules/security.md", rows[0]["multi"])
        self.assertNotIn(".claude/rules/security.md", rows[0]["files"])

    def test_drift_names_the_repo_that_differs_from_its_layer(self):
        same = {".claude/rules/security.md": "same\n", ".cursor/rules/security.mdc": "same\n", ".cursor/BUGBOT.md": "a\n"}
        self.make("Web/One", same)
        self.make("Web/Two", {**same, ".cursor/BUGBOT.md": "own wording\n"})
        self.make("Web/Three", {**same, ".claude/rules/security.md": "old\n"})
        out = S.drift()
        self.assertEqual(out["differs"], {"Three": ["web:security.md"]})  # BUGBOT.md never compared
        self.assertEqual(out["missing"], {})


class RolloutWorktree(Fleet):
    def test_the_worktree_is_removed_even_when_the_repo_fails(self):
        root = self.make("Web/Thing", {".cursor/BUGBOT.md": "x\n"})
        real = S.sh

        def sh(args, cwd, check=True):
            return "" if args[0] == "gh" else real(args, cwd, check)

        row = {"repo": "Thing", "rel": "Web/Thing", "files": {".cursor/BUGBOT.md": 1}}
        with mock.patch.object(S, "sh", side_effect=sh), \
                mock.patch.object(S, "_commit_push_open", side_effect=RuntimeError("push refused")):
            with self.assertRaises(RuntimeError):
                S._rollout_one(row, pathlib.Path(self.tmp.name), "docs/x", "t", pathlib.Path("m"), pathlib.Path("b"))
        self.assertFalse((root / ".claude/worktrees/sync-checklist-docs-x").exists())
        self.assertEqual(len(git(["worktree", "list"], root).splitlines()), 1)


class Rollout(unittest.TestCase):
    def test_no_card_id_in_branch_or_title(self):
        for branch, title in (("docs/eng-1997-x", "t"), ("docs/x", "Security checklist [ENGBACK-12]"),
                              ("quality/prod-7795-checklist", "t")):
            with self.subTest(branch=branch), self.assertRaises(SystemExit):
                S.rollout(pathlib.Path("."), branch, title, pathlib.Path("m"), pathlib.Path("b"))

    def test_a_date_or_version_is_not_a_card_id(self):
        self.assertIsNone(S.CARD_ID.search("docs/security-checklist-v32 checklist role-ranking-2026"))


def fake_gh(pr=None, runs=None, threads=None):
    pr = pr or {"state": "OPEN", "isDraft": True, "headRefOid": "abc", "files": [{"path": ".cursor/BUGBOT.md"}]}
    runs = runs if runs is not None else [{"name": "Cursor Bugbot", "status": "completed", "conclusion": "success"},
                                          {"name": "test", "status": "completed", "conclusion": "success"}]
    threads = threads or []

    def gh(args):
        if args[:2] == ["pr", "view"]:
            return pr
        if args[0] == "api" and args[1] == "graphql":
            return {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": threads}}}}}
        return runs
    return gh


class Merge(unittest.TestCase):
    def test_a_clean_docs_only_pr_is_ready(self):
        self.assertEqual(S.merge_check("Thing", 1, fake_gh())[0], "ready")

    def test_a_code_file_stops(self):
        gh = fake_gh(pr={"state": "OPEN", "isDraft": False, "headRefOid": "a", "files": [{"path": "src/app.js"}]})
        self.assertEqual(S.merge_check("Thing", 1, gh)[0], "stop")

    def test_an_unresolved_thread_stops_outdated_or_not(self):
        verdict, why = S.merge_check("Thing", 1, fake_gh(threads=[{"isResolved": True}, {"isResolved": False}]))
        self.assertEqual((verdict, why), ("stop", "1 unresolved review threads"))

    def test_no_bugbot_run_or_running_checks_wait(self):
        self.assertEqual(S.merge_check("Thing", 1, fake_gh(runs=[]))[0], "wait")
        running = [{"name": "Cursor Bugbot", "status": "in_progress", "conclusion": None}]
        self.assertEqual(S.merge_check("Thing", 1, fake_gh(runs=running))[0], "wait")

    def test_a_failed_check_stops(self):
        runs = [{"name": "Cursor Bugbot", "status": "completed", "conclusion": "success"},
                {"name": "test", "status": "completed", "conclusion": "failure"}]
        self.assertEqual(S.merge_check("Thing", 1, fake_gh(runs=runs))[0], "stop")

    def test_a_missing_hold_list_stops_every_merge(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"SYNC_CHECKLIST_HOLDS": str(pathlib.Path(d) / "none.txt")}):
            with self.assertRaises(SystemExit):
                S.merge(["Thing#1"], gh=fake_gh())

    def test_a_held_repo_stops_and_nothing_after_it_runs(self):
        with tempfile.TemporaryDirectory() as d:
            holds = pathlib.Path(d) / "holds.txt"
            holds.write_text("# comment\nTwo\n")
            checked = []
            with mock.patch.dict(os.environ, {"SYNC_CHECKLIST_HOLDS": str(holds)}), \
                    mock.patch.object(S, "merge_check", side_effect=lambda r, n, gh: (checked.append(r), ("stop", "x"))[1]):
                out = S.merge(["Two#1", "One#2"], gh=fake_gh())
            self.assertEqual(out, [("Two#1", "stop", "repo is on release hold")])
            self.assertEqual(checked, [])

    def test_stops_at_the_first_unclean_pr(self):
        with tempfile.TemporaryDirectory() as d:
            holds = pathlib.Path(d) / "holds.txt"
            holds.write_text("")
            verdicts = iter([("stop", "1 unresolved review threads"), ("ready", "clean")])
            with mock.patch.dict(os.environ, {"SYNC_CHECKLIST_HOLDS": str(holds)}), \
                    mock.patch.object(S, "merge_check", side_effect=lambda r, n, gh: next(verdicts)), \
                    mock.patch.object(S.subprocess, "run", side_effect=AssertionError("merged")):
                out = S.merge(["A#1", "B#2"], gh=fake_gh())
            self.assertEqual(out, [("A#1", "stop", "1 unresolved review threads")])


class Cli(unittest.TestCase):
    def test_merge_target_shape(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(S.main(["merge", "Thing-12"]), 2)


if __name__ == "__main__":
    unittest.main()
