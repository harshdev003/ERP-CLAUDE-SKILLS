"""`eng:livekit-loop` RECOMMEND leg — the deterministic half in `scripts/recommend.py`.

What has to hold, because each failure is silent:

- the daily step fires once per PT day, from 09:00 PT, and not before;
- a day's counts stay that day's (never pooled), and every rate carries its n;
- in-flight, on-hold and recently declined items reach the model, so it can leave them out; a card
  closed long ago does not, and an on-hold title is caught whatever its case;
- a decline writes a Recommendations log entry the next read recognises.

Linear is stubbed at `_linear`, its single chokepoint; no gcloud call is made.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import sys
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/eng/skills/livekit-loop/scripts/recommend.py"

spec = importlib.util.spec_from_file_location("livekit_loop_recommend_under_test", SCRIPT)
rec = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(SCRIPT.parent))
spec.loader.exec_module(rec)

TODAY = dt.date(2026, 10, 26)


class Due(unittest.TestCase):
    def at(self, hour):
        return dt.datetime(2026, 10, 26, hour, 5, tzinfo=rec.PT)

    def test_not_before_nine(self):
        self.assertFalse(rec.is_due(self.at(8), None))

    def test_first_tick_after_nine(self):
        self.assertTrue(rec.is_due(self.at(9), "2026-10-25"))

    def test_once_per_day(self):
        self.assertFalse(rec.is_due(self.at(15), "2026-10-26"))


class Day(unittest.TestCase):
    def test_counts_and_rates_carry_n(self):
        started = [{"serviceContext": {"version": "1.14.6"}}] * 3 + [{"serviceContext": {"version": "1.14.5"}}]
        ended = [{"reason": "client left"}] * 3 + [{"reason": "reconnect timeout"}]
        interrupts = [{"barge_in_ms": 400, "premature": True}, {"barge_in_ms": 600, "premature": False}]
        out = rec.summarize_day(TODAY, started, ended, interrupts, [{"line_type": "stt_stall_reprompt"}])
        self.assertEqual(out["date"], "2026-10-26")
        self.assertEqual(out["sessions"], 4)
        self.assertEqual(out["releases"], {"1.14.6": 3, "1.14.5": 1})
        self.assertEqual(out["ending_reasons"], {"client left": 3, "reconnect timeout": 1})
        self.assertEqual(out["interrupts"]["n"], 2)
        self.assertEqual(out["interrupts"]["premature_share"], 0.5)
        self.assertEqual(out["fallback_lines"], {"stt_stall_reprompt": 1})

    def test_empty_day_has_no_rate(self):
        out = rec.summarize_day(TODAY, [], [], [], [])
        self.assertIsNone(out["interrupts"]["premature_share"])
        self.assertIsNone(out["interrupts"]["barge_in_ms_p50"])


def node(ident, title, state_type="started", closed=None, comment=None):
    return {"identifier": ident, "title": title, "state": {"name": state_type, "type": state_type},
            "completedAt": closed if state_type == "completed" else None,
            "canceledAt": closed if state_type == "canceled" else None,
            "comments": {"nodes": [{"body": comment, "createdAt": "2026-10-25T10:00:00Z"}] if comment else []}}


class Cards(unittest.TestCase):
    def read(self, children, comments=(), page=2):
        """Serve each connection `page` nodes at a time, so every read crosses a page boundary."""
        nodes = {"children": list(children),
                 "comments": [{"body": b, "createdAt": at} for b, at in comments]}

        def fake(query, variables):
            field = "children" if "children(" in query else "comments"
            start = int(variables.get("after") or 0)
            chunk = nodes[field][start:start + page]
            more = start + page < len(nodes[field])
            return {"issue": {field: {"nodes": chunk,
                                      "pageInfo": {"hasNextPage": more, "endCursor": str(start + page)}}}}

        with mock.patch.object(rec, "_linear", side_effect=fake):
            return rec.cards(TODAY)

    def test_open_on_hold_and_recently_closed_are_kept(self):
        out = self.read([
            node("ENG-1", "Voice: in flight", comment="PR open"),
            node("ENG-2", "[On Hold] Voice: backgrounding"),
            node("ENG-3", "[Livekit Support] upstream report"),
            node("ENG-4", "Voice: fixed last week", "completed", "2026-10-20T00:00:00Z"),
            node("ENG-5", "Voice: rejected last week", "canceled", "2026-10-19T00:00:00Z"),
            node("ENG-6", "Voice: fixed long ago", "completed", "2026-09-01T00:00:00Z"),
        ])
        ids = {c["id"]: c for c in out["children"]}
        self.assertEqual(sorted(ids), ["ENG-1", "ENG-2", "ENG-3", "ENG-4", "ENG-5"])
        self.assertTrue(ids["ENG-2"]["on_hold"] and ids["ENG-3"]["on_hold"])
        self.assertFalse(ids["ENG-1"]["on_hold"])
        self.assertEqual(ids["ENG-1"]["last_comment"], "PR open")

    def test_decline_round_trips_into_the_log(self):
        body = rec.decline_body("Post the LiveKit report", "not now", TODAY)
        out = self.read([], [(body, "2026-10-26T18:00:00Z"), ("unrelated", "2026-10-26T18:00:00Z"),
                             (rec.decline_body("old", "x", dt.date(2026, 9, 1)), "2026-09-01T00:00:00Z")])
        self.assertEqual(len(out["recommendations_log"]), 1)
        self.assertIn("Post the LiveKit report", out["recommendations_log"][0]["entry"])
        self.assertIn("2026-11-09", body)


def reprompts(sessions: int, each: int, line="stt_stall_reprompt") -> list[dict]:
    return [{"transaction": f"tx{i}", "line_type": line} for i in range(sessions) for _ in range(each)]


def week(sig: dict, end: dt.date = TODAY) -> list[dict]:
    return [{"date": (end - dt.timedelta(days=b)).isoformat(), "signals": sig} for b in range(rec.BASELINE_DAYS, 0, -1)]


QUIET_WEEK = week(rec.signals([], reprompts(1, rec.BURST_PER_SESSION)))  # 1 burst a day


class Bar(unittest.TestCase):
    """Lead's verify list: raised in the tick it clears the bar, digest-only below it, never twice."""

    def tick(self, now, items=None, baseline=QUIET_WEEK, today=TODAY):
        return rec.evaluate(now, baseline, items or {}, today)

    def keys(self, out, bucket):
        return [r["key"] for r in out[bucket]]

    def test_three_burst_sessions_raise_in_the_same_tick(self):
        # Two sessions alone are watching; the third with the same symptom crosses the bar.
        out = self.tick(rec.signals([], reprompts(2, 18)))
        self.assertNotIn("burst:stt_stall_reprompt", self.keys(out, "raise"))
        self.assertIn("burst:stt_stall_reprompt", self.keys(out, "watching"))
        out = self.tick(rec.signals([], reprompts(3, 6)))
        self.assertIn("burst:stt_stall_reprompt", self.keys(out, "raise"))
        row = out["raise"][0]
        self.assertEqual((row["n"], row["bar"], row["trailing_max"], row["trailing_days"]), (3, 3, 1, 7))

    def test_over_the_count_but_inside_the_week_is_watching(self):
        busy = week(rec.signals([], reprompts(4, rec.BURST_PER_SESSION)))
        out = self.tick(rec.signals([], reprompts(3, 6)), baseline=busy)
        self.assertIn("burst:stt_stall_reprompt", self.keys(out, "watching"))

    def test_no_baseline_never_raises(self):
        out = self.tick(rec.signals([], reprompts(9, 9)), baseline=[])
        self.assertEqual(out["raise"], [])

    def test_volume_bar_needs_twenty_sessions(self):
        ended = lambda n: [{"transaction": f"t{i}", "reason": "no participant joined"} for i in range(n)]
        wk = week(rec.signals(ended(10), []))
        self.assertIn("ending:no participant joined", self.keys(self.tick(rec.signals(ended(19), []), baseline=wk), "watching"))
        self.assertIn("ending:no participant joined", self.keys(self.tick(rec.signals(ended(20), []), baseline=wk), "raise"))
        self.assertNotIn("ending:client left", rec.signals([{"transaction": "x", "reason": "client left"}], []))

    def test_a_raised_item_is_not_raised_again(self):
        now = rec.signals([], reprompts(3, 6))
        items = {"burst:stt_stall_reprompt": {"status": "pending", "n": 3, "at": TODAY.isoformat()}}
        out = self.tick(now, items)
        self.assertEqual(self.keys(out, "raise"), [])
        self.assertEqual(out["held"][0]["status"], "pending")

    def test_material_change_revives_an_on_hold_item(self):
        items = {"burst:stt_stall_reprompt": {"status": "on_hold", "n": 3, "at": "2026-09-01"}}
        self.assertEqual(self.keys(self.tick(rec.signals([], reprompts(4, 6)), items), "raise"), [])
        self.assertIn("burst:stt_stall_reprompt", self.keys(self.tick(rec.signals([], reprompts(5, 6)), items), "raise"))

    def test_decline_holds_for_fourteen_days(self):
        now = rec.signals([], reprompts(3, 6))
        recent = {"burst:stt_stall_reprompt": {"status": "declined", "n": 3, "at": "2026-10-20"}}
        old = {"burst:stt_stall_reprompt": {"status": "declined", "n": 3, "at": "2026-10-01"}}
        self.assertEqual(self.keys(self.tick(now, recent), "raise"), [])
        self.assertIn("burst:stt_stall_reprompt", self.keys(self.tick(now, old), "raise"))


class BaselineSince(unittest.TestCase):
    """2026-10-27: a config change that switches a line on is not a regression."""

    def test_a_signal_switched_on_by_config_is_not_raised_against_its_old_zero(self):
        with mock.patch.dict(rec.BASELINE_SINCE, {"away_check_in": "2026-10-26T21:07:00"}):
            now = rec.signals([], reprompts(67, 1, line="away_check_in"))
            out = rec.evaluate(now, week({}, end=dt.date(2026, 10, 27)), {}, dt.date(2026, 10, 27))
            row = next(r for r in out["watching"] if r["key"] == "fallback:away_check_in")
            self.assertEqual(row["trailing_days"], 0)
            # once full days after the change exist, it is judged against them
            later = rec.evaluate(now, week(rec.signals([], reprompts(60, 1, line="away_check_in")),
                                           end=dt.date(2026, 11, 3)), {}, dt.date(2026, 11, 3))
            self.assertIn("fallback:away_check_in", [r["key"] for r in later["raise"]])

    def test_agc_off_resets_only_the_stt_stall_reprompt_fallback(self):
        # the shipped table: fallback:stt_stall_reprompt from 2026-11-02T17:12:02Z, its burst untouched
        now = rec.signals([], reprompts(80, rec.BURST_PER_SESSION))
        self.assertEqual(sorted(now), ["burst:stt_stall_reprompt", "fallback:stt_stall_reprompt"])
        out = rec.evaluate(now, week(rec.signals([], reprompts(1, rec.BURST_PER_SESSION)), end=dt.date(2026, 11, 3)),
                           {}, dt.date(2026, 11, 3))
        rows = {r["key"]: r for r in out["raise"] + out["watching"]}
        self.assertEqual(rows["fallback:stt_stall_reprompt"]["trailing_days"], 0)
        self.assertEqual(rows["burst:stt_stall_reprompt"]["trailing_days"], rec.BASELINE_DAYS)

    def test_the_back_off_release_resets_only_the_away_check_in_burst(self):
        # the shipped table: burst:away_check_in from 2026-10-29T20:20:51Z, the fallback still from 10-26
        now = rec.signals([], reprompts(6, rec.BURST_PER_SESSION, line="away_check_in"))
        self.assertEqual(sorted(now), ["burst:away_check_in", "fallback:away_check_in"])
        out = rec.evaluate(now, week(now, end=dt.date(2026, 10, 30)), {}, dt.date(2026, 10, 30))
        days = {r["key"]: r["trailing_days"] for r in out["watching"] + out["raise"]}
        self.assertEqual(days, {"burst:away_check_in": 0, "fallback:away_check_in": 3})

    def test_the_away_grace_change_resets_the_user_away_ending(self):
        # the shipped table, not a patched one: the ending's subject is the reason after the colon
        ended = [{"reason": "user away", "transaction": i} for i in range(30)]
        now = rec.signals(ended, [])
        out = rec.evaluate(now, week({}, end=dt.date(2026, 10, 28)), {}, dt.date(2026, 10, 28))
        row = next(r for r in out["watching"] if r["key"] == "ending:user away")
        self.assertEqual(row["trailing_days"], 0)

    def test_other_signals_keep_the_full_week(self):
        out = rec.evaluate(rec.signals([], reprompts(3, 6)), QUIET_WEEK, {}, TODAY)
        self.assertEqual(out["raise"][0]["trailing_days"], 7)


class Store(unittest.TestCase):
    def test_raised_then_resolved_round_trips(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d, mock.patch.object(rec, "ITEMS", pathlib.Path(d, "items.json")):
            for argv in (["raised", "--key", "burst:x", "--n", "3"], ["resolve", "--key", "burst:x", "--status", "declined"]):
                with mock.patch.object(sys, "argv", ["recommend.py", *argv]), mock.patch("sys.stdout"):
                    self.assertEqual(rec.main(), 0)
            item = rec._load_items()["burst:x"]
        self.assertEqual((item["status"], item["n"]), ("declined", 3))

    def cli(self, d, *argv):
        with mock.patch.object(rec, "ITEMS", pathlib.Path(d, "items.json")), \
                mock.patch.object(sys, "argv", ["recommend.py", *argv]), mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            return rec.main(), rec._load_items()

    def test_resolve_on_a_never_raised_key_holds_it_on_the_next_tick(self):
        """2026-10-27: picked without `raised` stored n=0, so the next tick raised it again."""
        import tempfile
        ended = [{"transaction": f"t{i}", "reason": "reconnect timeout"} for i in range(127)]
        now = rec.signals(ended, [])
        week_ = week(rec.signals(ended[:100], []))
        with tempfile.TemporaryDirectory() as d:
            code, items = self.cli(d, "resolve", "--key", "ending:reconnect timeout", "--status", "picked", "--n", "127")
            self.assertEqual(code, 0)
            out = rec.evaluate(now, week_, items, TODAY)
        self.assertEqual([r["key"] for r in out["raise"]], [])
        self.assertEqual(out["held"][0]["status"], "picked")

    def test_resolve_without_n_on_a_never_raised_key_fails_loudly(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            code, items = self.cli(d, "resolve", "--key", "ending:reconnect timeout", "--status", "picked")
        self.assertEqual(code, 2)
        self.assertEqual(items, {})


if __name__ == "__main__":
    unittest.main()
