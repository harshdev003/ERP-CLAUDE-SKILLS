"""`data:sync-screens` archives an orphaned row only when git proves its doc was deleted on main.

The 2026-11-09 weekly run stopped on `paymentPending_screen`, whose doc #1965 had deleted, and the
row waited three days for a person. The rule now: archive (restorable) when the doc is gone from
origin/main AND origin/main has the deleting commit; otherwise print one FOLLOWUP line for a person.
The git checks run against a real throwaway repo; Notion is stubbed at `api` / `fetch_rows` / `extract`.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "plugins/data/skills/sync-screens/scripts/sync_screens.py"
spec = importlib.util.spec_from_file_location("sync_screens", SCRIPT)
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)

CFG = ss.APPS["aurie"]
REAL_FETCH = ss.fetch_main
DOCS = CFG["knowledge"] + "/screens"


def git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)


def make_repo() -> str:
    """origin/main history: add gone + kept + back; delete gone and back; recreate back."""
    repo = tempfile.mkdtemp()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    d = pathlib.Path(repo, DOCS)
    d.mkdir(parents=True)
    for n in ("gone_screen", "kept_screen", "back_screen"):
        (d / f"{n}.md").write_text(n)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "add")
    git(repo, "rm", "-q", f"{DOCS}/gone_screen.md", f"{DOCS}/back_screen.md")
    git(repo, "commit", "-qm", "delete")
    (d / "back_screen.md").write_text("again")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "recreate")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def row(name, url=None):
    return {"id": f"page-{name}", "properties": {
        "Screen": {"type": "title", "title": [{"plain_text": name}]},
        "Source doc": {"type": "url", "url": url if url is not None else CFG["github"] + f"{name}.md"}}}


class FetchedMain(unittest.TestCase):
    """The throwaway repo has no remote; its origin/main ref stands in for a successful fetch."""

    def setUp(self):
        p = mock.patch.object(ss, "fetch_main", lambda _repo: True)
        p.start()
        self.addCleanup(p.stop)


class OrphanPlan(FetchedMain):
    @classmethod
    def setUpClass(cls):
        cls.repo = make_repo()

    def plan(self, rows):
        return ss.orphan_plan(sorted(rows), rows, "aurie", self.repo)

    def test_only_a_doc_deleted_on_main_is_archived(self):
        rows = {n: row(n) for n in ("gone_screen", "kept_screen", "back_screen", "never_screen")}
        rows["handmade"] = row("handmade", url="https://example.com/x")
        archive, ask = self.plan(rows)
        self.assertEqual([n for n, _ in archive], ["gone_screen"])
        self.assertEqual(sorted(n for n, _ in ask), ["back_screen", "handmade", "kept_screen", "never_screen"])

    def test_a_failed_fetch_never_archives(self):
        with mock.patch.object(ss, "fetch_main", lambda _repo: False):
            archive, ask = self.plan({"gone_screen": row("gone_screen")})
        self.assertEqual(archive, [])
        self.assertIn("could not fetch", ask[0][1])

    def test_the_real_fetch_fails_without_a_remote(self):
        self.assertFalse(REAL_FETCH(self.repo))

    def test_unreadable_git_never_archives(self):
        archive, ask = ss.orphan_plan(["gone_screen"], {"gone_screen": row("gone_screen")}, "aurie",
                                      tempfile.mkdtemp())
        self.assertEqual((archive, [n for n, _ in ask]), ([], ["gone_screen"]))


class Apply(FetchedMain):
    """main() end to end: what is written to Notion, and the FOLLOWUP lines."""

    @classmethod
    def setUpClass(cls):
        cls.repo = make_repo()

    def run_main(self, *extra):
        rows = {n: row(n) for n in ("gone_screen", "kept_screen", "live_screen")}
        calls = []
        buf = io.StringIO()
        live = [{"screen": "live_screen", "status": "captured", "domain": "home"}]  # synced and unchanged
        with mock.patch.object(ss, "extract", lambda *_a: live), \
                mock.patch.object(ss, "row_matches", lambda *_a: True), \
                mock.patch.object(ss, "fetch_rows", lambda _db: rows), \
                mock.patch.object(ss, "api", lambda *a, **k: calls.append(a) or {}), \
                mock.patch.object(sys, "argv", ["sync_screens.py", *extra, "--repo", self.repo]), \
                contextlib.redirect_stdout(buf):
            try:
                ss.main()
            except SystemExit:
                pass
        return calls, buf.getvalue()

    def test_apply_archives_the_verified_orphan_and_asks_about_the_other(self):
        calls, out = self.run_main("apply", "--confirm")
        self.assertEqual(calls, [("PATCH", "/pages/page-gone_screen", {"archived": True})])
        self.assertIn("FOLLOWUP orphan kept_screen:", out)
        self.assertNotIn("FOLLOWUP orphan gone_screen", out)

    def test_keep_orphans_writes_nothing_and_asks_about_both(self):
        calls, out = self.run_main("apply", "--confirm", "--keep-orphans")
        self.assertEqual(calls, [])
        self.assertIn("FOLLOWUP orphan gone_screen:", out)
        self.assertIn("FOLLOWUP orphan kept_screen:", out)

    def test_only_never_touches_orphans(self):
        calls, out = self.run_main("apply", "--confirm", "--only", "live_screen")
        self.assertIn("orphaned 2", out)  # still counted against the full doc set
        self.assertEqual(calls, [])
        self.assertNotIn("FOLLOWUP orphan", out)

    def test_plan_and_unconfirmed_apply_write_nothing(self):
        for args in (("plan",), ("apply",), ("check",)):
            with self.subTest(args):
                calls, out = self.run_main(*args)
                self.assertEqual(calls, [])
        self.assertIn("archive: doc deleted on main", self.run_main("plan")[1])


if __name__ == "__main__":
    unittest.main()
