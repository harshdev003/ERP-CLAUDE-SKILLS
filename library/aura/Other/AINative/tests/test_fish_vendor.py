#!/usr/bin/env python3
"""Fish Audio as a vendor column in the per-user cost store (ENG-1306).

Why this needs a test
---------------------
`store.py` books voice across per-vendor columns. A vendor with no column still
reconciles the day total — its spend lands in the `Other` row's remainder — so the
Fish arm of PROD-6068 would have read as roughly free rather than cheap, with no error
anywhere. Every assertion below covers a failure that yields a plausible number.

Fish also has no usage API (only a credit-balance snapshot), so the day total is
DERIVED from the agent's own synthesised-character count on `Session usage totals`.
A Fish session that never logged that row must be counted and noted, never folded in
as zero: a partial day is not a cheap one.

Stdlib only, no network, no keys — `google.*` is stubbed the way the sibling suites do it.
"""
from __future__ import annotations

import contextlib
import csv
import importlib.abc
import importlib.util
import os
import pathlib
import sys
import tempfile
import types
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/store-aurie-user-daily-cost/scripts"


class _StubModule(types.ModuleType):
    def __getattr__(self, name):
        cls = type(name, (Exception,), {"__module__": self.__name__})
        setattr(self, name, cls)
        return cls


class _GoogleStubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "google" or fullname.startswith("google."):
            return importlib.util.spec_from_loader(fullname, self, is_package=True)
        return None

    def create_module(self, spec):
        mod = _StubModule(spec.name)
        mod.__path__ = []
        return mod

    def exec_module(self, module):
        pass


@contextlib.contextmanager
def _stubbed_google():
    saved = {k: v for k, v in sys.modules.items() if k == "google" or k.startswith("google.")}
    finder = _GoogleStubFinder()
    for k in saved:
        del sys.modules[k]
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        for k in [k for k in sys.modules if k == "google" or k.startswith("google.")]:
            del sys.modules[k]
        sys.modules.update(saved)


def _load(name):
    sys.path.insert(0, str(SCRIPTS))
    try:
        with _stubbed_google():
            spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(SCRIPTS))


def _entry(room, ts, **payload):
    return {"timestamp": ts, "jsonPayload": dict(room=room, **payload)}


T0, T10 = "2026-09-18T10:00:00Z", "2026-09-18T10:10:00Z"


class TestFishIsAVendorEverywhereTheOthersAre(unittest.TestCase):
    """The column has to exist in every schema copy, or one consumer silently drops it."""

    @classmethod
    def setUpClass(cls):
        cls.store = _load("store")
        cls.lm = _load("livekit_minutes")
        cls.bq_sink = _load("bq_sink")
        cls.unit_costs = _load("unit_costs")
        cls.rebook = _load("rebook_livekit")

    def test_voice_fish_sits_next_to_cartesia_in_the_csv_schema(self):
        cols = self.store.COLS
        self.assertIn("voice_fish", cols)
        self.assertEqual(cols.index("voice_fish"), cols.index("voice_cartesia") + 1)

    def test_bigquery_schema_matches_the_csv_header_exactly(self):
        bq_cols = [n for n, _t in self.bq_sink.STORES["per_user"]["schema"]]
        self.assertEqual(bq_cols, self.store.COLS)

    def test_rebook_synthesizes_the_column_at_zero_too(self):
        self.assertIn("voice_fish", self.rebook._NUMERIC_COLS)

    def test_fish_marks_the_livekit_voice_path_for_adoption_counts(self):
        self.assertIn("voice_fish", self.unit_costs._LIVEKIT_PATH_COLS)

    def test_fish_is_a_known_tts_provider_so_its_minutes_are_not_folded_into_the_default(self):
        """An unknown provider token books under DEFAULT_TTS_PROVIDER — Fish $ would then be
        allocated over Cartesia sessions, a silent misattribution."""
        self.assertIn("fish", self.lm.TTS_PROVIDERS)
        for raw in ("fish", "Fish", "fishaudio", "fish-audio", "FISH"):
            with self.subTest(raw=raw):
                self.assertEqual(self.lm._normalize_tts_provider(raw), "fish")

    def test_the_rate_and_its_provenance_are_pinned(self):
        """$15 per 1M UTF-8 bytes for s2-pro (fish.audio/developers, checked 2026-09-15)."""
        self.assertEqual(self.store.DEFAULT_FISH_RATE_PER_MILLION, 15.00)


class TestCharacterCapture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lm = _load("livekit_minutes")

    def test_tts_characters_are_read_structurally_from_the_usage_row(self):
        s = self.lm._build_sessions([
            _entry("r1", T0, userId="u1", message="TTS provider created: fish (voice: v)", tts_provider="fish"),
            _entry("r1", T10, userId="u1", message="Session usage totals", tts_characters=1234, tts_seconds=70.0),
        ])["r1"]
        self.assertEqual((s.tts_provider, s.tts_characters), ("fish", 1234))

    def test_a_malformed_count_is_never_logged_not_zero(self):
        s = self.lm._build_sessions([
            _entry("r1", T0, userId="u1", message="Session usage totals", tts_characters="n/a"),
        ])["r1"]
        self.assertIsNone(s.tts_characters)

    def test_characters_roll_up_per_provider_with_a_measured_count(self):
        sessions = self.lm._build_sessions([
            _entry("r1", T0, userId="u1", message="TTS provider created: fish", tts_provider="fish"),
            _entry("r1", T10, userId="u1", message="Session usage totals", tts_characters=1000),
            _entry("r2", T0, userId="u2", message="TTS provider created: fish", tts_provider="fish"),
            # r2 never logs the usage row → counted, not measured
            _entry("r2", T10, userId="u2", message="Agent turn interrupted"),
            _entry("r3", T0, userId="u3", message="TTS provider created: cartesia", tts_provider="cartesia"),
            _entry("r3", T10, userId="u3", message="Session usage totals", tts_characters=500),
        ])
        _, _, b = self.lm._aggregate_sessions(sessions)
        self.assertEqual(b["tts_characters_by_provider"]["fish"], 1000)
        self.assertEqual(b["tts_sessions_by_provider"]["fish"], 2)
        self.assertEqual(b["tts_sessions_measured_by_provider"]["fish"], 1)
        self.assertEqual(b["tts_characters_by_provider"]["cartesia"], 500)


class TestFishDayTotal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = _load("store")

    def _meta(self, chars, sessions, measured):
        return {"tts_characters_by_provider": {"fish": chars},
                "tts_sessions_by_provider": {"fish": sessions},
                "tts_sessions_measured_by_provider": {"fish": measured}}

    def test_dollars_are_characters_times_the_rate(self):
        dollars, note = self.store.fish_day_total(self._meta(2_000_000, 3, 3), 15.00)
        self.assertAlmostEqual(dollars, 30.0)
        self.assertEqual(note, "")

    def test_a_quiet_day_is_a_plain_zero(self):
        self.assertEqual(self.store.fish_day_total(self._meta(0, 0, 0), 15.00), (0.0, ""))

    def test_an_unmeasured_session_is_counted_and_noted_never_folded_in_as_zero(self):
        dollars, note = self.store.fish_day_total(self._meta(1_000_000, 4, 3), 15.00)
        self.assertAlmostEqual(dollars, 15.0)  # covers the 3 measured sessions only
        self.assertIn("fish_partial", note)
        self.assertIn("1 of 4", note)
        self.assertIn("UNMEASURED", note)

    def test_no_log_data_is_a_note_not_a_zero(self):
        dollars, note = self.store.fish_day_total({}, 15.00)
        self.assertEqual(dollars, 0.0)
        self.assertIn("fish_pending", note)


class TestAllocationAndReconciliation(unittest.TestCase):
    """The Cartesia shape: vendor $ over the minute share of the sessions that used it."""

    @classmethod
    def setUpClass(cls):
        cls.store = _load("store")

    def test_fish_dollars_reach_only_fish_sessions_and_the_remainder_is_the_userless_share(self):
        day_fish = 12.0
        fish_by_user = {"u1": 6.0, "u2": 2.0}   # 8 of 10 Fish minutes carry a user
        fish_min = 10.0                          # the other 2 are userId-less
        alloc = self.store.allocate_by_minutes(day_fish, fish_by_user, fish_min)
        self.assertAlmostEqual(alloc["u1"], 7.2)
        self.assertAlmostEqual(alloc["u2"], 2.4)
        other = max(round(day_fish - sum(alloc.values()), 4), 0.0)
        self.assertAlmostEqual(other, 2.4)
        self.assertAlmostEqual(sum(alloc.values()) + other, day_fish)  # reconciles EXACTLY

    def test_fish_is_a_coverage_path_of_its_own(self):
        cov = self.store.summarize_voice_coverage({"fish": (12.0, {"u1": 12.0}), "cartesia": (0.0, {})})
        self.assertEqual(cov["paths"]["fish"]["coverage_pct"], 100.0)
        self.assertEqual(cov["collapsed_paths"], [])


class TestSchemaMigration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = _load("store")

    def test_a_pre_fish_store_gains_the_column_at_zero_and_keeps_its_totals(self):
        pre = self.store.PRE_FISH_COLS
        row = {c: "0" for c in pre}
        row.update({"date": "2026-09-01", "user_id": "u1", "llm_cost": "1.5", "voice_cartesia": "0.25",
                    "voice_cost": "0.25", "total_cost": "1.75", "subscription_state": "paid",
                    "engaged": "1", "active": "1", "activity_source": "engaged"})
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "per_user.csv")
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=pre, lineterminator="\n")
                w.writeheader(); w.writerow(row)
            self.assertTrue(self.store._migrate_old_csv(path))
            with open(path, newline="") as f:
                header = next(csv.reader(f))
                rows = list(csv.DictReader(f, fieldnames=header))
        self.assertEqual(header, self.store.COLS)
        self.assertEqual(rows[0]["voice_fish"], "0.0")
        self.assertEqual((rows[0]["voice_cartesia"], rows[0]["voice_cost"], rows[0]["total_cost"]),
                         ("0.25", "0.25", "1.75"))

    def test_the_previous_schema_is_todays_minus_the_new_column(self):
        # Same lineage property the drawdown and platform migrations pin: PRE_FISH_COLS is
        # exactly today's schema minus the one column that postdates it, in order.
        added_since = {"voice_fish", "voice_inworld"}
        self.assertEqual(self.store.PRE_FISH_COLS, [c for c in self.store.COLS if c not in added_since])
        self.assertNotIn("voice_fish", self.store.PRE_FISH_COLS)

    def test_a_current_store_is_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "per_user.csv")
            with open(path, "w", newline="") as f:
                csv.writer(f, lineterminator="\n").writerow(self.store.COLS)
            self.assertTrue(self.store._migrate_old_csv(path))
            with open(path, newline="") as f:
                self.assertEqual(next(csv.reader(f)), self.store.COLS)


if __name__ == "__main__":
    unittest.main()
