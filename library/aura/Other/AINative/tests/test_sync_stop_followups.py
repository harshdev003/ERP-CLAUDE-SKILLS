"""A stop in data:sync-screens or product:sync-design-system ends in a follow-up, never in silence.

Each script prints `FOLLOWUP stop <stage>: <why>` before it exits, and an unattended run raises
one `shared:raise-followup` per line. Forced stops run the real scripts with no network: a
missing screens directory or repo, and a Notion error stubbed at urlopen. The last class feeds a
real stop line to raise-followup with Slack and Linear stubbed: one post per key, a card for
`blocked`, a changed reason a thread reply on the same card.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCREENS = REPO / "plugins/data/skills/sync-screens/scripts/sync_screens.py"
DS = REPO / "plugins/product/skills/sync-design-system/scripts"
DB = "0123456789abcdef0123456789abcdef"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_raise_followup import CFG, Base, fu  # noqa: E402


def run_script(path, *argv):
    tmp = tempfile.mkdtemp()
    env = {"PATH": os.environ["PATH"], "HOME": tmp}  # no tokens, no settings
    # The scripts write UTF-8 (a stop line can hold an ellipsis): decode it as UTF-8 whatever the locale.
    return subprocess.run([sys.executable, str(path), *argv], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


def stop_lines(out):
    return [l for l in out.splitlines() if l.startswith("FOLLOWUP stop ")]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SyncScreensStops(unittest.TestCase):
    def test_a_missing_screens_directory_is_a_read_docs_stop(self):
        r = run_script(SCREENS, "check", "--repo", tempfile.mkdtemp())
        self.assertEqual(r.returncode, 2)
        lines = stop_lines(r.stdout)
        self.assertEqual(len(lines), 1, r.stdout)
        self.assertTrue(lines[0].startswith("FOLLOWUP stop read-docs: no screens directory"))

    def test_a_notion_error_is_a_read_notion_stop_with_the_id_cut(self):
        mod = load(SCREENS, "sync_screens_under_test")

        def refuse(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(b'{"message":"no"}'))
        out = io.StringIO()
        with mock.patch.object(mod, "extract", return_value=[]), \
                mock.patch.object(mod, "token", return_value="t"), \
                mock.patch.object(mod.urllib.request, "urlopen", refuse), \
                mock.patch.object(sys, "argv", ["sync_screens.py", "plan", "--database", DB]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit):
            mod.main()
        lines = stop_lines(out.getvalue())
        self.assertEqual(len(lines), 1, out.getvalue())
        self.assertTrue(lines[0].startswith("FOLLOWUP stop read-notion: Notion POST"), lines[0])
        self.assertIn("01234567…", lines[0])
        self.assertNotIn(DB, lines[0])

    def test_drift_is_a_result_not_a_stop(self):
        mod = load(SCREENS, "sync_screens_under_test")
        out = io.StringIO()
        rec = {"screen": "home_screen", "status": "captured", "domain": "home", "shots": []}
        with mock.patch.object(mod, "extract", return_value=[rec]), \
                mock.patch.object(mod, "fetch_rows", return_value={}), \
                mock.patch.object(sys, "argv", ["sync_screens.py", "check", "--database", DB]), \
                contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as e:
            mod.main()
        self.assertEqual(e.exception.code, 1)
        self.assertEqual(stop_lines(out.getvalue()), [])


class DesignSystemStops(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(DS))
        self.addCleanup(sys.path.remove, str(DS))
        sys.modules.pop("stops", None)
        self.stops = load(DS / "stops.py", "stops")

    def test_each_script_prints_one_stop_line(self):
        for script, stage in (("extract_tokens.py", "extract-tokens"), ("build_bundle.py", "build-bundle")):
            with self.subTest(script=script):
                r = run_script(DS / script, "--app", "aurie", "--out", tempfile.mkdtemp(),
                               "--repo", "/nonexistent-repo")
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(stop_lines(r.stdout), [f"FOLLOWUP stop {stage}: repo not found: /nonexistent-repo"])

    def test_run_reports_every_kind_of_stop_once(self):
        def raises(exc):
            def main():
                raise exc
            return main
        cases = [
            (raises(SystemExit("no baseline for page " + DB)), "FOLLOWUP stop s: no baseline for page 01234567…"),
            (raises(SystemExit(1)), "FOLLOWUP stop s: exit 1"),
            (raises(ValueError("bad\nsecond line")), "FOLLOWUP stop s: ValueError: bad"),
            (raises(SystemExit(0)), None),
            (raises(self.stops.Reported("child already said it")), None),
            (lambda: None, None),
        ]
        for main, want in cases:
            out = io.StringIO()
            with self.subTest(want=want), contextlib.redirect_stdout(out):
                with contextlib.suppress(BaseException):
                    self.stops.run(main, "s")
            self.assertEqual(stop_lines(out.getvalue()), [want] if want else [])

    def test_a_failed_token_extraction_is_reported_once(self):
        r = run_script(DS / "build_bundle.py", "--app", "aurie", "--out", tempfile.mkdtemp(),
                       "--repo", tempfile.mkdtemp())  # a repo with no theme files
        self.assertNotEqual(r.returncode, 0)
        lines = stop_lines(r.stdout)
        self.assertEqual(len(lines), 1, r.stdout)
        self.assertTrue(lines[0].startswith("FOLLOWUP stop extract-tokens: theme directory not found"))
        self.assertIn("token extraction failed", r.stderr)


class StopBecomesOneFollowUp(Base):
    """The skill's rule, end to end: a real stop line to raise-followup."""

    def raise_for(self, line):
        stage, why = line[len("FOLLOWUP stop "):].split(": ", 1)
        return fu.raise_followup({
            "kind": "blocked", "title": f"Weekly screens sync stopped at {stage}",
            "what": line, "why": "The Notion Screens database is not updated until the sync re-runs.",
            "ask": "Fix the cause and re-run the sync.",
            "key": f"sync-screens:aurie:stop:{stage}", "session": "aurie-screens-weekly"}, CFG)

    def forced_stop(self):
        r = run_script(SCREENS, "check", "--repo", "/nonexistent-checkout")
        return stop_lines(r.stdout)[0]

    def test_one_post_per_key_and_a_card_for_blocked(self):
        line = self.forced_stop()
        code, first = self.raise_for(line)
        self.assertEqual((code, first["action"], first["card_action"]), (0, "posted", "filed"))
        self.assertIn("followup-key: sync-screens:aurie:stop:read-docs", self.lin.cards[0]["description"])
        code, again = self.raise_for(line)
        self.assertEqual((code, again["action"], again["card_action"]), (0, "unchanged", "none"))
        self.assertEqual(len(self.slack.messages), 1)
        self.assertEqual(len(self.lin.cards), 1)

    def test_a_changed_reason_is_a_reply_on_the_same_post_and_card(self):
        self.raise_for(self.forced_stop())
        code, out = self.raise_for("FOLLOWUP stop read-docs: no screens directory at /another/checkout")
        self.assertEqual((code, out["action"]), (0, "replied"))
        self.assertEqual((len(self.slack.messages), len(self.lin.cards), len(self.lin.comments)), (1, 1, 1))


if __name__ == "__main__":
    unittest.main()
