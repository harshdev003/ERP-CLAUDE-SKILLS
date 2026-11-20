#!/usr/bin/env python3
"""The per-user cost store's split from our own data (ENG-1859).

(a) TTS $ is split by llmCall-ledger reply characters, not session minutes: every TTS vendor
    bills characters, and characters per minute moved from ~713 to ~1,050 over October, so a
    minute split moved 10-13% of daily TTS $ between users. A failed ledger read keeps the
    minute split and says so.
(b) A LiveKit log read that comes back short is refused: on 2026-10-04 it found 147 users
    against the ledger's 228 LiveKit coach users (a re-read found 263).
(c) A --force re-store past a source's retention keeps the stored per-user split: ElevenLabs
    ConvAI keeps conversations 10 days, and re-storing 10-04 moved $10.97 to `Other`.

The main() tests drive store.main() to a written CSV with every vendor read stubbed at its
chokepoint, so they pin the wiring as well as the helpers. Stdlib only, no network, no keys.
"""
from __future__ import annotations

import contextlib
import csv
import datetime
import io
import os
import re
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_vendor_read_failure import store  # noqa: E402  (loads store.py with google stubbed)

OLD_DAY = "2026-10-04"


def _meta(by_user_tts):
    by_provider = {p: sum(m.values()) for p, m in by_user_tts.items()}
    total = sum(by_provider.values())
    return {"minutes_by_provider": by_provider, "by_user_tts": by_user_tts, "sessions": 2,
            "stt_minutes": total, "stt_sessions_measured": 2,
            "stt_minutes_by_transport": {"deepgram": total}}


class CharsSplit(unittest.TestCase):
    def test_a_provider_total_splits_by_reply_characters(self):
        out, missing, usd = store.allocate_tts_by_chars(
            {"elevenlabs": 4.0}, {"elevenlabs": {"a": 10.0, "b": 10.0}}, {"a": 1000, "b": 3000})
        self.assertAlmostEqual(out["elevenlabs"]["a"], 1.0)
        self.assertAlmostEqual(out["elevenlabs"]["b"], 3.0)
        self.assertEqual((missing, usd), (set(), 0.0))

    def test_a_mixed_provider_user_splits_their_characters_by_minute_share(self):
        # u: 10 min ElevenLabs + 30 min Cartesia, 4,000 chars -> 1,000 + 3,000. v: Cartesia only.
        out, _, _ = store.allocate_tts_by_chars(
            {"elevenlabs": 1.0, "cartesia": 3.0},
            {"elevenlabs": {"u": 10.0}, "cartesia": {"u": 30.0, "v": 10.0}},
            {"u": 4000, "v": 1000})
        self.assertAlmostEqual(out["elevenlabs"]["u"], 1.0)
        self.assertAlmostEqual(out["cartesia"]["u"], 2.25)
        self.assertAlmostEqual(out["cartesia"]["v"], 0.75)

    def test_a_user_without_ledger_chars_keeps_their_minute_share(self):
        out, missing, usd = store.allocate_tts_by_chars(
            {"fish": 3.0}, {"fish": {"a": 10.0, "b": 10.0, "c": 10.0}}, {"a": 1000, "b": 1000})
        self.assertAlmostEqual(out["fish"]["c"], 1.0)
        self.assertAlmostEqual(out["fish"]["a"], 1.0)
        self.assertEqual(missing, {"c"})
        self.assertAlmostEqual(usd, 1.0)

    def test_every_provider_total_reconciles(self):
        totals = {"elevenlabs": 37.41, "cartesia": 12.07, "fish": 0.0, "inworld": 3.33}
        by_user = {"elevenlabs": {"a": 3.1, "b": 7.7, "c": 0.4},
                   "cartesia": {"a": 1.0, "d": 9.0}, "inworld": {"e": 2.0}}
        out, _, _ = store.allocate_tts_by_chars(totals, by_user, {"a": 917, "b": 5003, "d": 77})
        for p, total in totals.items():
            self.assertAlmostEqual(sum(out[p].values()), total, places=6, msg=p)


class LedgerUserGuard(unittest.TestCase):
    def test_the_2026_10_04_short_read_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            store.assert_livekit_users_match_ledger(OLD_DAY, 147, 228)
        self.assertIn("147 users", str(cm.exception.code))
        self.assertIn("228 LiveKit coach users", str(cm.exception.code))

    def test_a_normal_day_and_the_override_pass(self):
        store.assert_livekit_users_match_ledger(OLD_DAY, 263, 228)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            store.assert_livekit_users_match_ledger(OLD_DAY, 147, 228, allow_short=True)
        self.assertIn("--allow-short-livekit-log", err.getvalue())


class KeepStoredSplit(unittest.TestCase):
    def test_kept_when_the_totals_match_scaled_when_not_other_when_none(self):
        split, note = store.keep_stored_split("voice_11labs_agent", "ElevenLabs ConvAI",
                                              {"a": 6.0, "b": 4.0}, 10.97, 10.97)
        self.assertEqual(split, {"a": 6.0, "b": 4.0})
        self.assertIn("kept", note)
        split, note = store.keep_stored_split("voice_11labs_agent", "ElevenLabs ConvAI",
                                              {"a": 6.0, "b": 4.0}, 10.0, 20.0)
        self.assertEqual(split, {"a": 12.0, "b": 8.0})
        self.assertIn("scaled", note)
        split, note = store.keep_stored_split("voice_11labs_agent", "ElevenLabs ConvAI", {}, 0.0, 5.0)
        self.assertIsNone(split)
        self.assertIn("`Other`", note)

    def test_a_day_is_expired_once_any_of_it_is_past_retention(self):
        now = datetime.datetime(2026, 11, 9, 15, 0, tzinfo=datetime.timezone.utc)
        self.assertTrue(store.source_expired(OLD_DAY, 10, now))
        # Exactly 10 days old: the cutoff (10-30 15:00Z) falls inside it, so part is purged.
        self.assertTrue(store.source_expired("2026-10-30", 10, now))
        self.assertFalse(store.source_expired("2026-10-31", 10, now))


class MainRun(unittest.TestCase):
    """store.main() run to a written CSV."""

    BQ_ERROR = type("BQError", (Exception,), {})
    LK = {"elevenlabs": {"u1": 10.0, "u2": 10.0}, "cartesia": {"u2": 20.0}}

    def _run(self, *, chars=None, ledger_fails=False, lk_users=None, force=False, day="2026-11-08",
             stored=None, extra=()):
        tmp = tempfile.mkdtemp()
        self.csv = os.path.join(tmp, "per_user.csv")
        bq = types.ModuleType("bq")
        bq.partition_exists = lambda *a, **k: False
        bq.BQUnavailable = type("BQUnavailable", (Exception,), {})
        bq.BQError = self.BQ_ERROR
        bq.PROJECT, bq.DATASET = "p", "d"
        bq_sink = types.ModuleType("bq_sink")
        bq_sink.STORES = {"per_user": {"table": "t"}}
        lk_min = lk_users or {"u1": 10.0, "u2": 30.0}

        def ledger(day, project):
            if ledger_fails:
                raise self.BQ_ERROR("403 bigquery.jobs.create")
            return chars if chars is not None else {"u1": 1000.0, "u2": 3000.0}

        patches = [
            mock.patch.dict(sys.modules, {"bq": bq, "bq_sink": bq_sink}),
            mock.patch.dict(os.environ, {"ELEVENLABS_ADMIN_API_KEY": "x", "AURA_API_TOKEN": "x"}),
            mock.patch.object(store, "llm_costs", return_value=({"u1": 1.0}, 1.0, {})),
            mock.patch.object(store.engagement, "day_engagement", return_value=({}, {}, "off")),
            mock.patch.object(store, "assert_user_count_plausible", return_value=None),
            mock.patch.object(store, "assert_voice_user_count_plausible", return_value=None),
            mock.patch.object(store, "walk_eligible_dollars", return_value=1.0),
            # ConvAI $10.97, ElevenLabs LiveKit TTS $4.00.
            mock.patch.object(store, "elevenlabs_caller_split", return_value=(10.97, 0.0, 4.0, "")),
            # A day past ConvAI retention: the walk finds no conversations.
            mock.patch.object(store, "voice_costs",
                              side_effect=lambda d, k, total: (({} if force else {"u1": total}), total)),
            mock.patch.object(store, "livekit_minutes_for_day",
                              return_value=(lk_min, sum(lk_min.values()), _meta(self.LK))),
            mock.patch.object(store, "mtd_livekit_minutes_before", return_value=0.0),
            mock.patch.object(store, "mtd_inference_drawdown_before", return_value=0.0),
            mock.patch.object(store, "cartesia_day_total", return_value=(3.0, "")),
            mock.patch.object(store, "fish_day_total", return_value=(0.0, "")),
            mock.patch.object(store, "inworld_day_total", return_value=(0.0, "")),
            mock.patch.object(store, "deepgram_day_total", return_value=(1.0, "")),
            mock.patch.object(store, "sub_state", return_value="paid"),
            mock.patch.object(store, "ledger_reply_chars", side_effect=ledger),
            mock.patch.object(store, "stored_split", return_value=stored or ({}, 0.0)),
            mock.patch.object(sys, "argv", ["store.py", "--date", day, "--llm-json", "x.json",
                                            "--csv", self.csv, *(["--force"] if force else []),
                                            *extra]),
        ]
        out = tempfile.TemporaryFile("w+")
        with contextlib.ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            st.enter_context(contextlib.redirect_stdout(out))
            st.enter_context(contextlib.redirect_stderr(out))
            store.main()
        out.seek(0)
        self.output = out.read()
        with open(self.csv, newline="") as f:
            return {r["user_id"]: r for r in csv.DictReader(f)}

    def assertReconciles(self, rows):
        day = float(re.search(r"\) = \$([0-9.]+)", self.output).group(1))
        self.assertLess(abs(sum(float(r["total_cost"]) for r in rows.values()) - day), 0.01)

    def col(self, rows, c):
        return {u: float(r[c]) for u, r in rows.items() if float(r[c])}

    def test_chars_split_the_tts_columns_and_the_day_reconciles_to_the_cent(self):
        rows = self._run()
        # ElevenLabs $4: u1 10 min/1,000 chars; u2 10 of 30 min on ElevenLabs -> 1,000 of its 3,000.
        self.assertEqual(self.col(rows, "voice_11labs_livekit"), {"u1": 2.0, "u2": 2.0})
        self.assertEqual(self.col(rows, "voice_cartesia"), {"u2": 3.0})
        self.assertIn("tts_alloc_basis: chars", self.output)
        self.assertReconciles(rows)

    def test_a_failed_ledger_read_falls_back_to_minutes_and_is_tagged(self):
        rows = self._run(ledger_fails=True)
        self.assertEqual(self.col(rows, "voice_11labs_livekit"), {"u1": 2.0, "u2": 2.0})
        self.assertIn("tts_alloc_basis: minutes", self.output)
        self.assertIn("ledger_pending", self.output)
        self.assertReconciles(rows)

    def test_minutes_and_chars_disagree_where_chars_per_minute_differ(self):
        rows = self._run(chars={"u1": 3000.0, "u2": 1000.0})
        # u1 has 3x u2's ElevenLabs chars over the same minutes.
        el = self.col(rows, "voice_11labs_livekit")
        self.assertAlmostEqual(el["u1"], 3.6)
        self.assertAlmostEqual(el["u2"], 0.4)

    def test_a_short_livekit_log_read_is_refused_and_the_override_stores(self):
        ledger = {f"u{i}": 100.0 for i in range(10)}
        with self.assertRaises(SystemExit) as cm:
            self._run(chars=ledger, lk_users={"u1": 10.0, "u2": 30.0})
        self.assertIn("REFUSING to store", str(cm.exception.code))
        self.assertFalse(os.path.exists(self.csv))
        self._run(chars=ledger, lk_users={"u1": 10.0, "u2": 30.0},
                  extra=["--allow-short-livekit-log"])

    def test_a_failed_ledger_read_skips_the_user_check(self):
        self._run(ledger_fails=True, lk_users={"u1": 40.0})

    def test_a_forced_re_store_past_retention_keeps_the_stored_agent_split(self):
        rows = self._run(force=True, day=OLD_DAY,
                         stored=({"u1": 6.97, "u2": 4.0}, 10.97))
        self.assertEqual(self.col(rows, "voice_11labs_agent"), {"u1": 6.97, "u2": 4.0})
        self.assertEqual(float(rows["Other"]["voice_11labs_agent"]), 0.0)
        self.assertIn("kept the stored per-user split", self.output)
        self.assertReconciles(rows)

    def test_a_forced_re_store_with_no_stored_split_lands_on_other_and_says_so(self):
        # The voice-coverage guard refuses this day on its own; the override is how it is stored.
        rows = self._run(force=True, day=OLD_DAY, extra=["--allow-low-coverage"])
        self.assertEqual(float(rows["Other"]["voice_11labs_agent"]), 10.97)
        self.assertIn("no stored per-user split", self.output)


if __name__ == "__main__":
    unittest.main()
