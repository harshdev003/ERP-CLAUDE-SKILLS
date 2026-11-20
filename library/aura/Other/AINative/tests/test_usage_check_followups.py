"""The weekly Claude usage check raises a follow-up for every stop, and nothing goes to #alerts-ai.

`weekly_check.py` (eng:harvest-anthropic-usage) replaces the launchd shell job that posted
needs-you alerts by curl and skipped silently when GCP_KEY was unset. Every stop is forced here
with `harvest.py` stubbed, and the follow-ups go through raise-followup with Slack and Linear
stubbed: one post per key, a card for `blocked`.
"""
from __future__ import annotations

import importlib.util
import io
import os
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_raise_followup import Base, fu  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
SKILL = REPO / "plugins/eng/skills/harvest-anthropic-usage"
RAISE = REPO / "plugins/shared/skills/raise-followup"
UUID = "0123abcd-0123-4567-89ab-0123456789ab"


def load():
    spec = importlib.util.spec_from_file_location("weekly_check", SKILL / "scripts/weekly_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Runner:
    """harvest.py stand-in: maps the first arg (--window or --check) to (exit code, output)."""

    def __init__(self, harvest=(0, "total: 12 rows"), check=(0, "fresh through 2026-11-16")):
        self.out = {"--window": harvest, "--check": check}
        self.calls = []

    def __call__(self, args):
        self.calls.append(args[0])
        return self.out[args[0]]


class Copies(unittest.TestCase):
    def test_the_bundled_raise_followup_files_match_their_canonicals(self):
        pairs = {"scripts/followup.py": RAISE / "scripts/followup.py",
                 "scripts/slack_post.py": REPO / "plugins/shared/skills/post-to-slack/scripts/slack_post.py",
                 "scripts/slack_read.py": REPO / "plugins/shared/skills/read-slack/scripts/slack_read.py",
                 "references/routing.json": RAISE / "references/routing.json"}
        for copy, canonical in pairs.items():
            with self.subTest(copy=copy):
                self.assertEqual((SKILL / copy).read_bytes(), canonical.read_bytes())


class WeeklyCheck(Base):
    def setUp(self):
        super().setUp()
        self.mod = load()
        tmp = pathlib.Path(tempfile.mkdtemp())
        for p in (mock.patch.object(self.mod, "LOG", tmp / "check.log"),
                  mock.patch.object(self.mod, "SETTINGS", tmp / "absent.json"),
                  mock.patch.dict(os.environ, {"GCP_KEY": "x", "CLAUDE_ADMIN_SESSION_KEY": "x"})):
            p.start()
            self.addCleanup(p.stop)
        self.log = tmp / "check.log"

    def main(self, runner, raise_one=None):
        with redirect_stdout(io.StringIO()):
            return self.mod.main([], runner=runner, raise_one=raise_one or fu.raise_followup)

    def test_a_clean_run_raises_nothing(self):
        self.assertEqual(self.main(Runner()), 0)
        self.assertEqual((len(self.slack.messages), len(self.lin.cards)), (0, 0))

    def test_gcp_key_unset_is_raised_not_skipped(self):
        os.environ.pop("GCP_KEY")
        runner = Runner()
        self.assertEqual(self.main(runner), 1)
        self.assertEqual(runner.calls, [])
        self.assertEqual((len(self.slack.messages), len(self.lin.cards)), (1, 1))
        self.assertIn("harvest-anthropic-usage:credentials:stop", self.slack.messages[0]["text"])

    def test_an_expired_session_key_is_one_blocked_follow_up_with_a_card(self):
        self.assertEqual(self.main(Runner(harvest=(2, "AUTH FAILED"))), 1)
        self.assertEqual((len(self.slack.messages), len(self.lin.cards)), (1, 1))
        self.assertIn("harvest-anthropic-usage:harvest:stop", self.slack.messages[0]["text"])
        self.assertIn("expired", self.slack.messages[0]["text"])
        # The same stop next Monday is the same post, not a second one.
        self.assertEqual(self.main(Runner(harvest=(2, "AUTH FAILED"))), 1)
        self.assertEqual((len(self.slack.messages), len(self.lin.cards)), (1, 1))

    def test_no_session_key_and_a_stale_warehouse_each_raise_one(self):
        os.environ.pop("CLAUDE_ADMIN_SESSION_KEY")
        runner = Runner(check=(1, "STALE: newest day 2026-11-09, 8 days old"))
        self.assertEqual(self.main(runner), 1)
        self.assertEqual(runner.calls, ["--check"])
        texts = [m["text"] for m in self.slack.messages]
        self.assertTrue(any("harvest-anthropic-usage:harvest:stop" in t for t in texts))
        self.assertTrue(any("harvest-anthropic-usage:check:stop" in t for t in texts))

    def test_tool_output_with_an_id_still_posts(self):
        self.assertEqual(self.main(Runner(harvest=(3, f"pre-flight failed for org {UUID} at 10.1.2.3"))), 1)
        text = self.slack.messages[0]["text"]
        self.assertNotIn(UUID, text)
        self.assertNotIn("10.1.2.3", text)
        self.assertIn("0123abcd…", text)

    def test_a_harvest_failure_reports_its_error_line_not_a_progress_line(self):
        progress = "\n".join(f"2026-11-{d:02d}  ok  40 rows" for d in range(1, 11))
        self.assertEqual(self.main(Runner(harvest=(1, progress + "\nFAILED: 2 day(s) failed: 2026-11-09 (HTTP 500)"))), 1)
        self.assertIn("FAILED: 2 day(s) failed", self.slack.messages[0]["text"])
        self.assertIn("FAILED: 2 day(s) failed", self.log.read_text())

    def test_a_check_that_cannot_read_bigquery_is_not_called_stale(self):
        self.assertEqual(self.main(Runner(check=(3, "freshness check failed: 403 Access Denied"))), 1)
        text = self.slack.messages[0]["text"]
        self.assertIn("could not read BigQuery", text)
        self.assertNotIn("is stale", text)

    def test_a_follow_up_that_cannot_post_is_exit_2_and_logged(self):
        code = self.main(Runner(harvest=(2, "AUTH FAILED")),
                         raise_one=lambda spec, cfg, dry: (2, {"action": "failed", "error": "Slack down"}))
        self.assertEqual(code, 2)
        self.assertIn("FOLLOW-UP harvest not posted", self.log.read_text())

    def test_nothing_posts_to_the_alerts_channel(self):
        seen = []

        def raise_one(spec, cfg, dry):
            code, res = fu.raise_followup(spec, cfg, dry)
            seen.append(res["channel"])
            return code, res

        self.main(Runner(harvest=(2, "AUTH FAILED"), check=(1, "STALE")), raise_one=raise_one)
        self.assertEqual(set(seen), {fu.load_config()["defaults"]["channel"]})
        self.assertEqual(len(seen), 2)
        self.assertNotIn("C0C9P5K069F", (SKILL / "scripts/weekly_check.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
