"""eng:monitor-coach-replies (ENG-1826): the daily detector against the real history.

The fixture is IntelligenceService's daily series for 2026-09-25 to 2026-11-05 (counts only:
tests/fixtures/coach_daily_fixture.csv, from its onset analysis, ENGBACK-2365). Its emoji column is the
BigQuery share (llm_call.emojiCount, one row per turn), within 0.2 points of the Postgres share it replaced. On that history the
detector must raise, as new flags, the steps people found by hand:
- 10-08: the emoji step (it began at about 20Z on 10-07, so the 10-07 daily row is only +8 points);
- 11-03: the length step, chat and voice;
- 11-04: the emoji drop from our own release, the incomplete onset, and the first double failure;
and keep a step's following days as continuing, so one step is posted once.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins/eng/skills/monitor-coach-replies/scripts/monitor.py"
spec = importlib.util.spec_from_file_location("monitor_coach_replies", SCRIPT)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)
FIXTURE = ROOT / "tests/fixtures/coach_daily_fixture.csv"


def history() -> dict:
    h = {}
    with open(FIXTURE, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            h[r["date"]] = {
                "chat": {"n": int(r["chat_n"]), "out_med": int(r["chat_med"]), "out_p90": int(r["chat_p90"])},
                "voice": {"n": int(r["voice_n"]), "out_med": int(r["voice_med"]), "out_p90": int(r["voice_p90"])},
                "emoji_pct": float(r["emoji_pct_all_coach"]),
                "incomplete": int(r["incomplete_max_output_attempt1"]),
                "double": int(r["incomplete_max_output_attempt2_user_failed"]),
                "empty": int(r["empty_completed_logs"]),
            }
    return h


H = history()
TH = monitor.thresholds()


def new(d):
    return {(f["metric"], f["mode"]) for f in monitor.detect_new(H, d, TH)["flags"] if f["new"]}


def flagged(d):
    return {(f["metric"], f["mode"]) for f in monitor.detect(H, d, TH)["flags"]}


class HistoryTest(unittest.TestCase):
    def test_the_fixture_is_counts_only(self):
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("@", text)
        self.assertEqual(text.splitlines()[0].count(","), 12)
        self.assertEqual(len(H), 42)

    def test_the_10_07_emoji_step_flags_on_10_08(self):
        self.assertNotIn(("replies with emoji", "all"), flagged("2026-10-07"))  # +8 points: onset at ~20Z
        self.assertIn(("replies with emoji", "all"), new("2026-10-08"))

    def test_the_11_03_length_step_flags_chat_and_voice(self):
        for mode in ("chat", "voice"):
            self.assertIn(("output tokens median", mode), new("2026-11-03"))
            self.assertIn(("output tokens p90", mode), new("2026-11-03"))
        chat = next(f for f in monitor.detect(H, "2026-11-03", TH)["flags"]
                    if f["metric"] == "output tokens median" and f["mode"] == "chat")
        self.assertEqual((chat["value"], chat["baseline"], chat["change"]), (182, 160, "+14%"))

    def test_11_04_emoji_drop_incompletes_and_double_failure(self):
        got = new("2026-11-04")
        self.assertIn(("replies with emoji", "all"), got)
        self.assertIn(("incomplete responses (retried)", "all"), got)
        self.assertIn(("double failures (user saw a failure)", "all"), got)

    def test_a_step_is_new_once_then_continuing(self):
        # The 11-03 length step still flags on 11-04 against the old median, but it isn't news.
        self.assertIn(("output tokens median", "chat"), flagged("2026-11-04"))
        self.assertNotIn(("output tokens median", "chat"), new("2026-11-04"))
        self.assertIn(("replies with emoji", "all"), flagged("2026-10-09"))
        self.assertNotIn(("replies with emoji", "all"), new("2026-10-09"))

    def test_every_double_failure_is_new(self):
        self.assertIn(("double failures (user saw a failure)", "all"), new("2026-11-05"))

    def test_a_quiet_baseline_week_raises_nothing(self):
        for d in ("2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05", "2026-10-06"):
            self.assertEqual(flagged(d), set(), d)

    def test_missing_data_is_unmeasured_never_normal(self):
        h = {d: {k: v for k, v in r.items() if k != "emoji_pct"} for d, r in H.items()}
        res = monitor.detect(h, "2026-10-08", TH)
        self.assertIn("emoji share", res["unmeasured"])
        res = monitor.detect({}, "2026-10-08", TH)
        self.assertEqual(res["flags"], [])
        self.assertTrue(any("length chat" in u for u in res["unmeasured"]))


class ReportTest(unittest.TestCase):
    def test_report_carries_counts_and_context_only(self):
        res = monitor.detect_new(H, "2026-11-04", TH)
        text = monitor.render(res, ["2026-11-03T20:40Z release v1.47.0"])
        self.assertIn("v1.47.0", text)
        self.assertIn("(continuing)", text)
        self.assertIn("replies with emoji", text)
        text = monitor.render(res, [])
        self.assertIn("likely model drift", text)

    def test_check_posts_only_on_a_new_flag(self):
        tmp = pathlib.Path(tempfile.mkdtemp()) / "history.json"
        tmp.write_text(json.dumps(H), encoding="utf-8")
        posted = []
        fake_slack = mock.Mock(post_message=lambda ch, text, **kw: posted.append((ch, text, kw)) or {"ok": True})
        with mock.patch.object(monitor, "HISTORY", tmp), mock.patch.object(monitor, "changes", return_value=[]), \
                mock.patch.dict("sys.modules", {"slack_post": fake_slack}), \
                mock.patch("sys.stdout", new_callable=__import__("io").StringIO):
            monitor.main(["check", "--date", "2026-10-05", "--post"])
            self.assertEqual(posted, [])
            monitor.main(["check", "--date", "2026-11-03", "--post"])
            self.assertEqual(len(posted), 1)
            self.assertEqual(posted[0][0], monitor.DEFAULT_CHANNEL)
            self.assertEqual(posted[0][2]["footer_skill"], "eng:monitor-coach-replies")
            monitor.main(["check", "--date", "2026-10-06", "--post", "--weekly"])
            self.assertIn("all clear", posted[-1][1])

    def run_check(self, h, *argv, ok=True, env=None):
        tmp = pathlib.Path(tempfile.mkdtemp()) / "history.json"
        tmp.write_text(json.dumps(h), encoding="utf-8")
        posted = []
        def post(ch, text, **kw):
            posted.append(text)
            return {"ok": ok}
        with mock.patch.object(monitor, "HISTORY", tmp), mock.patch.object(monitor, "changes", return_value=[]), \
                mock.patch.dict("sys.modules", {"slack_post": mock.Mock(post_message=post)}), \
                mock.patch.dict(os.environ, env or {}, clear=False), \
                mock.patch("sys.stdout", new_callable=__import__("io").StringIO), \
                mock.patch("sys.stderr", new_callable=__import__("io").StringIO):
            rc = monitor.main(["check", *argv, "--post"])
        return rc, posted, json.loads(tmp.read_text(encoding="utf-8"))

    def test_a_failed_post_exits_1_and_is_posted_again_next_day(self):
        rc, posted, h = self.run_check(H, "--date", "2026-11-03", ok=False)
        self.assertEqual((rc, len(posted)), (1, 1))
        self.assertNotIn("posted", h["2026-11-03"])
        # 11-04's length flags are continuing only against what 11-03 actually posted: nothing.
        rc, posted, _ = self.run_check(h, "--date", "2026-11-04")
        self.assertIn("output tokens median (chat)", posted[0])
        self.assertNotIn("median (chat): 191 vs 160 (+19%) (continuing)", posted[0])

    def test_a_post_that_exits_is_a_failed_post(self):
        # slack_post exits (SystemExit) on a missing token or a transport error.
        tmp = pathlib.Path(tempfile.mkdtemp()) / "history.json"
        tmp.write_text(json.dumps(H), encoding="utf-8")
        def boom(*a, **k):
            raise SystemExit("SLACK_BOT_TOKEN not set")
        with mock.patch.object(monitor, "HISTORY", tmp), mock.patch.object(monitor, "changes", return_value=[]), \
                mock.patch.dict("sys.modules", {"slack_post": mock.Mock(post_message=boom)}), \
                mock.patch("sys.stdout", new_callable=__import__("io").StringIO), \
                mock.patch("sys.stderr", new_callable=__import__("io").StringIO):
            rc = monitor.main(["check", "--date", "2026-11-03", "--post"])
        self.assertEqual(rc, 1)
        self.assertNotIn("posted", json.loads(tmp.read_text(encoding="utf-8"))["2026-11-03"])

    def test_a_successful_post_is_recorded_and_not_repeated(self):
        rc, _, h = self.run_check(H, "--date", "2026-11-03")
        self.assertEqual(rc, 0)
        self.assertTrue(h["2026-11-03"]["posted"])
        rc, posted, _ = self.run_check(h, "--date", "2026-11-04")
        self.assertIn("(continuing)", posted[0])

    def test_weekly_all_clear_needs_a_full_measured_week(self):
        rc, posted, _ = self.run_check(H, "--date", "2026-10-06", "--weekly")
        self.assertIn("all clear", posted[-1])
        partial = {d: r for d, r in H.items() if d != "2026-10-04"}
        rc, posted, _ = self.run_check(partial, "--date", "2026-10-06", "--weekly")
        self.assertEqual(posted, [])
        no_logs = {d: {k: v for k, v in r.items() if k != "incomplete"} for d, r in H.items()}
        rc, posted, _ = self.run_check(no_logs, "--date", "2026-10-06", "--weekly")
        self.assertEqual(posted, [])

    def test_ratio_flags_keep_their_direction(self):
        f = {"metric": "incomplete responses (retried)", "mode": "all", "change": "10x", "dir": "+"}
        self.assertEqual(monitor.key(f), monitor.key(dict(f, change="9x")))

    def test_collect_rereads_a_day_a_failed_read_left_empty(self):
        row = dict(H["2026-11-04"])
        self.assertFalse(monitor.incomplete_row(row))
        self.assertTrue(monitor.incomplete_row(dict(row, incomplete=None)))
        self.assertTrue(monitor.incomplete_row({k: v for k, v in row.items() if k != "voice"}))
        # The emoji share comes from BigQuery now, so an empty one is always re-read.
        self.assertTrue(monitor.incomplete_row(dict(row, emoji_pct=None)))

    def test_collect_takes_emoji_from_bigquery_and_postgres_only_as_fallback(self):
        tmp = pathlib.Path(tempfile.mkdtemp()) / "history.json"
        length = {d: {"chat": {"n": 600, "out_med": 150, "out_p90": 280},
                      "voice": {"n": 600, "out_med": 130, "out_p90": 250}}
                  for d in monitor.days_back(monitor.day("2026-11-05"), 7) + ["2026-11-05"]}
        bq_emoji = {d: 20.0 for d in length if d != "2026-11-01"}  # one day BigQuery has no emojiCount for
        pg = mock.Mock(return_value=19.0)
        with mock.patch.object(monitor, "HISTORY", tmp), \
                mock.patch.object(monitor, "collect_length", return_value=length), \
                mock.patch.object(monitor, "collect_emoji_bq", return_value=bq_emoji), \
                mock.patch.object(monitor, "collect_emoji", pg), \
                mock.patch.object(monitor, "collect_log_count", return_value=0), \
                mock.patch("sys.stdout", new_callable=__import__("io").StringIO):
            monitor.main(["collect", "--date", "2026-11-05"])
        h = json.loads(tmp.read_text(encoding="utf-8"))
        self.assertEqual(h["2026-11-05"]["emoji_pct"], 20.0)
        self.assertEqual(h["2026-11-01"]["emoji_pct"], 19.0)
        pg.assert_called_once_with("2026-11-01")

    def test_the_bigquery_emoji_query_counts_one_row_per_turn(self):
        sql = monitor.EMOJI_BQ_SQL
        self.assertIn("PARTITION BY dialogueId ORDER BY createdAt DESC", sql)
        self.assertIn("replyChars IS NOT NULL", sql)
        self.assertIn("COUNTIF(e > 0)", sql)
        self.assertNotIn("message", sql)

    def test_history_file_is_private(self):
        tmp = pathlib.Path(tempfile.mkdtemp()) / "sub" / "history.json"
        with mock.patch.object(monitor, "HISTORY", tmp):
            monitor.save_history({"2026-11-05": {"incomplete": 1}})
        self.assertEqual(os.stat(tmp).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
