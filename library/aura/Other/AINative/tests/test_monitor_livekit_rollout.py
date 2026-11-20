"""`eng:monitor-livekit` — a planned rollout is not a LOOK, a stuck drain still is.

After a deploy LiveKit Cloud drains old sessions for up to ~1h, so a window holding two releases is
expected. The `window` verdict suppresses "more than one release serving" only while the newest
version in the window is the latest LivekitVoiceAgent release and that release is under 90 min old.
The failures worth pinning are the silent ones: a stuck drain or an unknown version read as a
rollout, and a `gh` failure read as permission to suppress.

`gh` is stubbed at `subprocess.run`, the module's only call into it.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/eng/skills/monitor-livekit/scripts/monitor_livekit.py"

spec = importlib.util.spec_from_file_location("monitor_livekit_under_test", SCRIPT)
ml = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(SCRIPT.parent))
spec.loader.exec_module(ml)

END = dt.datetime(2026, 10, 26, 22, 30)
MIXED = {"1.14.6": 29, "1.14.5": 16}


def window(versions: dict) -> dict:
    return {"sessions": sum(versions.values()), "versions": versions, "groups": {}, "signals": {},
            "errors": 0}


def gh(tag="v1.14.6", published="2026-10-26T21:35:12Z", returncode=0):
    out = json.dumps({"tagName": tag, "publishedAt": published})
    return mock.patch.object(ml.subprocess, "run", return_value=subprocess.CompletedProcess([], returncode, out, ""))


class LatestRelease(unittest.TestCase):
    def test_parses_tag_and_time(self):
        with gh():
            self.assertEqual(ml.latest_release(), ("1.14.6", dt.datetime(2026, 10, 26, 21, 35, 12)))

    def test_gh_failure_is_none(self):
        with gh(returncode=1):
            self.assertIsNone(ml.latest_release())

    def test_gh_missing_is_none(self):
        with mock.patch.object(ml.subprocess, "run", side_effect=FileNotFoundError("gh")):
            self.assertIsNone(ml.latest_release())


class Verdict(unittest.TestCase):
    def reasons(self, versions, end=END, **kw):
        with gh(**kw):
            info = ml.rollout(versions, ml.latest_release(), end)
        return info, ml.verdict(window(versions), window({"1.14.5": 30}), info)

    def test_rollout_within_90_min_is_normal_with_info(self):
        info, reasons = self.reasons(MIXED)
        self.assertEqual(reasons, [])
        self.assertTrue(info.startswith("rollout in progress: "))

    def test_old_release_after_90_min_is_a_stuck_drain(self):
        info, reasons = self.reasons(MIXED, end=END + dt.timedelta(minutes=40))
        self.assertIsNone(info)
        self.assertTrue(any(r.startswith("more than one release serving") for r in reasons))

    def test_newest_in_window_not_the_latest_release(self):
        info, reasons = self.reasons({"1.14.5": 10, "1.14.4": 3})
        self.assertIsNone(info)
        self.assertTrue(any(r.startswith("more than one release serving") for r in reasons))

    def test_gh_failure_keeps_the_look(self):
        info, reasons = self.reasons(MIXED, returncode=1)
        self.assertIsNone(info)
        self.assertTrue(any(r.startswith("more than one release serving") for r in reasons))

    def test_newest_is_by_semver_not_string(self):
        info, _ = self.reasons({"1.14.10": 5, "1.14.9": 5}, tag="v1.14.10")
        self.assertIsNotNone(info)

    def test_single_release_needs_no_lookup(self):
        self.assertIsNone(ml.rollout({"1.14.6": 30}, ("1.14.6", END), END))
        self.assertEqual(ml.verdict(window({"1.14.6": 30}), window({"1.14.6": 30})), [])


class AnsweredTurns(unittest.TestCase):
    """ENG-1767 / ENG-1787: the verdict reads the median session's answered-turn ratio.

    On 2026-11-03 from 03:44Z the LLM failed silently (median 0% over 11 sessions, no error row). On
    2026-11-04 06:26Z the window total was 35% (74/210) with nothing wrong: one user talked over every
    reply (83 turns, 5 answered); the median session was 56% over 9.
    """

    @staticmethod
    def rows(sessions):
        """(user turns, answered) per room -> the two row lists `collect` reads."""
        turns, answered = [], []
        for i, (n, a) in enumerate(sessions):
            turns += [{"jsonPayload": {"room": f"r{i}"}}] * n
            answered += [{"jsonPayload": {"room": f"r{i}"}}] * a
        return turns, answered

    def look(self, sessions):
        turns, answered = self.rows(sessions)
        now = {**window({"1.17.2": 30}), "session_ratios": ml.session_ratios(turns, answered),
               "user_turns": len(turns), "answered": len(answered)}
        return [r for r in ml.verdict(now, window({"1.17.2": 30})) if "answered-turn" in r]

    def test_the_outage_is_a_look(self):
        (r,) = self.look([(20, 2), (18, 0), (25, 1), (15, 0), (30, 3), (12, 0), (22, 0), (19, 1), (17, 0), (16, 0), (19, 0)])
        self.assertIn("median session 0% over 11 sessions", r)

    def test_one_monologue_does_not_flag_a_healthy_window(self):
        # 83 turns with 5 answered plus two long-turn users: the total is under 40%, the median is not.
        sessions = [(83, 5), (40, 9), (30, 8), (10, 6), (9, 5), (12, 7), (8, 5), (6, 4), (12, 8)]
        turns, answered = self.rows(sessions)
        self.assertLess(len(answered) / len(turns), 0.40)
        self.assertEqual(self.look(sessions), [])

    def test_the_boundaries(self):
        self.assertEqual(self.look([(10, 4)] * 4), [])              # exactly 40%: not under
        self.assertEqual(len(self.look([(10, 3)] * 4)), 1)          # 30% over 4 sessions
        self.assertEqual(self.look([(10, 0)] * 3), [])              # too few sessions to call
        self.assertEqual(self.look([(4, 0)] * 10 + [(10, 9)]), [])  # short sessions don't count

    def test_rows_without_a_room_are_not_one_session(self):
        turns = [{"jsonPayload": {}}] * 50
        self.assertEqual(ml.session_ratios(turns, []), [])

    def test_the_agent_job_stands_in_for_room(self):
        turns = [{"jsonPayload": {"transactionSlice": "j1"}}] * 5
        self.assertEqual(ml.session_ratios(turns, turns[:2]), [0.4])

    def test_conversation_id_is_not_a_session_key(self):
        # One conversation can span several rooms when the app rejoins.
        turns = [{"jsonPayload": {"conversationId": "c1"}}] * 10
        self.assertEqual(ml.session_ratios(turns, []), [])

    def test_an_older_window_without_the_counts_is_not_a_look(self):
        self.assertEqual([r for r in ml.verdict(window({"1.17.2": 30}), window({"1.17.2": 30})) if "answered" in r], [])

    def test_both_rows_are_read_on_the_agents_log(self):
        reads = []
        with mock.patch.object(ml, "read", lambda flt, limit=20000: reads.append(flt) or []):
            w = ml.collect(dt.datetime(2026, 11, 3, 3, 44), dt.datetime(2026, 11, 3, 4, 15))
        self.assertEqual((w["user_turns"], w["answered"], w["session_ratios"]), (0, 0, []))
        turn = [f for f in reads if 'jsonPayload.message="User turn shape"' in f]
        ttft = [f for f in reads if 'jsonPayload.message="Turn latency" AND jsonPayload.metric="llm_ttft_ms"' in f]
        self.assertEqual((len(turn), len(ttft)), (1, 1))
        self.assertIn('logName="projects/firebase-auratech16/logs/aurie-voice-agent"', ttft[0])


if __name__ == "__main__":
    unittest.main()
