#!/usr/bin/env python3
"""The cost readers see Inworld and Fish, and Inference-served TTS is flagged.

Why this needs a test
---------------------
#1304 added `voice_inworld` to the per-user store (and `voice_fish` before it), but the
readers enumerate vendors by hand. A column a reader does not name is not an error: the
LiveKit-path series and the vendor tables simply read low, and the total still renders,
because `voice` comes from `voice_cost`. On 2026-10-18 that dropped $7.39 of Inworld from
the LiveKit path. What is pinned:

- `chart-aurie-daily-cost` reads both columns into the day, into `voice_livekit_path`,
  and into LiveKit-path adoption; a row without them still loads; and the chart's two
  voice series add back to `voice`.
- `report-aurie-weekly-costs` carries both through the stored-day read, the vendor
  recompute for an unstored day, `--from-bigquery`, and both rendered vendor tables.
- A LiveKit session whose TTS ran through LiveKit Inference is flagged by the log parser
  and makes the store emit a `tts_via_livekit_inference` note — the credit leg is STT-only,
  so that spend would otherwise be missing from every vendor column.

Stdlib only, no network, no keys — `google.*` is stubbed the way the sibling suites do it.
"""
from __future__ import annotations

import contextlib
import importlib.abc
import importlib.util
import pathlib
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SKILLS = REPO / "plugins/eng/skills"
STORE_SCRIPTS = SKILLS / "store-aurie-user-daily-cost/scripts"


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


def _load(path, name, extra_path=None):
    if extra_path:
        sys.path.insert(0, str(extra_path))
    try:
        with _stubbed_google():
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        return mod
    finally:
        if extra_path:
            sys.path.remove(str(extra_path))


# The real 2026-10-18 vendor sums from aurie_per_user_daily_cost, folded into two rows.
def _row(user, **cols):
    base = {"date": "2026-10-18", "user_id": user, "llm_cost": "1.0",
            "voice_11labs_agent": "0", "voice_11labs_intelligence": "0",
            "voice_11labs_livekit": "0", "voice_cartesia": "0", "voice_fish": "0",
            "voice_inworld": "0", "voice_deepgram": "0", "voice_livekit": "0",
            "voice_livekit_platform": "0", "subscription_state": "paid",
            "active": "1", "engaged": "1"}
    base.update({k: str(v) for k, v in cols.items()})
    voice = sum(float(base[k]) for k in base if k.startswith("voice_"))
    base["voice_cost"] = str(voice)
    base["total_cost"] = str(voice + 1.0)
    return base


class TestDailyChartAggregate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agg = _load(SKILLS / "chart-aurie-daily-cost/scripts/aggregate.py",
                        "aggregate_under_inworld_test")

    def test_inworld_and_fish_reach_the_day_and_the_livekit_path(self):
        days, _ = self.agg.load([
            _row("u1", voice_inworld=7.39, voice_deepgram=2.0),
            _row("u2", voice_cartesia=40.34, voice_fish=0.5, voice_livekit=3.0),
        ])
        d = days["2026-10-18"]
        self.assertAlmostEqual(d["voice_inworld"], 7.39)
        self.assertAlmostEqual(d["voice_fish"], 0.5)
        self.assertAlmostEqual(self.agg.livekit_path(d), 7.39 + 2.0 + 40.34 + 0.5 + 3.0)
        # The chart draws ElevenLabs + LiveKit path; together they must be the voice total.
        self.assertAlmostEqual(d["voice_11labs"] + self.agg.livekit_path(d), d["voice"])

    def test_an_inworld_only_user_counts_as_a_livekit_voice_user(self):
        days, _ = self.agg.load([_row("u1", voice_inworld=0.2)])
        self.assertEqual(days["2026-10-18"]["livekit_voice_users"], 1)

    def test_rows_that_predate_the_columns_still_load(self):
        old = _row("u1", voice_cartesia=1.0)
        del old["voice_inworld"], old["voice_fish"]
        days, _ = self.agg.load([old])
        d = days["2026-10-18"]
        self.assertEqual((d["voice_inworld"], d["voice_fish"]), (0.0, 0.0))
        self.assertAlmostEqual(self.agg.livekit_path(d), 1.0)

    def test_every_non_elevenlabs_store_voice_column_is_on_the_livekit_path(self):
        # A new vendor column the reader doesn't name is the defect this file exists for.
        store = _load(STORE_SCRIPTS / "store.py", "store_under_reader_test", STORE_SCRIPTS)
        voice_cols = {c for c in store.COLS if c.startswith("voice_") and c != "voice_cost"}
        named = set(self.agg.LIVEKIT_PATH_VENDORS) | {
            "voice_11labs_agent", "voice_11labs_intelligence", "voice_11labs_livekit",
            "voice_livekit_platform"}  # platform is folded into voice_livekit by load()
        self.assertEqual(voice_cols - named, set())


W = _load(SKILLS / "report-aurie-weekly-costs/scripts/report.py", "weekly_under_inworld_test")
START = datetime(2026, 10, 17, tzinfo=timezone.utc)
END = datetime(2026, 10, 19, tzinfo=timezone.utc)  # 10-17, 10-18
RATES = {"cartesia": 38.0, "fish": 15.0, "inworld": 25.0, "deepgram": 0.348, "livekit": 0.01}


def _bq(fn):
    m = types.ModuleType("bq")
    m.query = fn
    m.credential_source = lambda: None
    return m


class _Store:
    def __init__(self):
        self.meta_seen = []

    def cartesia_day_total(self, d, r):
        return 4.0, ""

    def deepgram_day_total(self, d, r):
        return 1.0, ""

    def livekit_costs(self, d, r):
        return {}, 100.0, 16.7, "", {"marker": d}

    def fish_day_total(self, meta, r):
        self.meta_seen.append(("fish", meta))
        return 0.25, ""

    def inworld_day_total(self, d, meta, r):
        self.meta_seen.append(("inworld", meta))
        return 7.39, "inworld_derived: test"


class TestWeeklyReport(unittest.TestCase):

    def test_stored_days_carry_inworld_and_fish(self):
        seen = {}

        def query(sql, params=None, **_):
            seen["sql"] = sql
            return [{"d": "2026-10-17", "cartesia": 30.0, "fish": 0.0, "inworld": 6.0,
                     "deepgram": 1.0, "livekit": 50.0},
                    {"d": "2026-10-18", "cartesia": 40.34, "fish": 0.0, "inworld": 7.39,
                     "deepgram": 1.0, "livekit": 50.0}]
        with mock.patch.dict(sys.modules, {"bq": _bq(query)}), mock.patch.object(W, "_store", _Store()):
            totals, _ = W.fetch_livekit_path_week(START, END, RATES)
        self.assertIn("voice_inworld", seen["sql"])
        self.assertIn("voice_fish", seen["sql"])
        self.assertAlmostEqual(totals["inworld"], 13.39)
        self.assertAlmostEqual(totals["cartesia"], 70.34)

    def test_an_unstored_day_recomputes_inworld_and_fish_from_the_agent_logs(self):
        store = _Store()
        with mock.patch.dict(sys.modules, {"bq": _bq(lambda *a, **k: [])}), \
                mock.patch.object(W, "_store", store):
            totals, notes = W.fetch_livekit_path_week(START, END, RATES)
        self.assertAlmostEqual(totals["inworld"], 2 * 7.39)
        self.assertAlmostEqual(totals["fish"], 2 * 0.25)
        # Fish and the Inworld fallback read the same log meta livekit_costs parsed.
        self.assertIn(("inworld", {"marker": "2026-10-17"}), store.meta_seen)
        self.assertIn(("fish", {"marker": "2026-10-18"}), store.meta_seen)
        self.assertIn("inworld_derived: test", notes)

    def test_from_bigquery_vendor_split_ties_out_to_voice_cost(self):
        rows = [{"state": "paid", "llm": 10.0, "voice": 60.0, "total": 70.0,
                 "el_agent": 0.0, "el_intelligence": 1.0, "el_livekit": 5.0,
                 "cartesia": 40.34, "fish": 0.27, "inworld": 7.39, "deepgram": 2.0,
                 "livekit": 4.0, "active_users": 3}]

        def query(sql, params=None, **_):
            if "COUNT(DISTINCT date)" in sql:
                return [{"days": 2, "mn": "2026-10-17", "mx": "2026-10-18"}]
            self.assertIn("SUM(voice_inworld)", sql)
            self.assertIn("SUM(voice_fish)", sql)
            return rows
        with mock.patch.dict(sys.modules, {"bq": _bq(query)}):
            data = W.fetch_bigquery_week(START, END)
        self.assertAlmostEqual(data["vendor"]["inworld"], 7.39)
        self.assertAlmostEqual(sum(data["vendor"].values()), data["voice_usd"])
        md = W.render_bq_vendor_breakdown(data["vendor"])
        self.assertIn("| Inworld (TTS) | $7.39 |", md)
        self.assertIn("| Fish Audio (TTS) | $0.27 |", md)
        self.assertIn("**$60.00**", md)

    def test_live_vendor_table_puts_inworld_and_fish_on_the_livekit_path(self):
        totals = {"cartesia": 40.34, "fish": 0.27, "inworld": 7.39, "deepgram": 2.0, "livekit": 4.0}
        md = W.render_voice_vendor_breakdown(10.0, totals, RATES, [])
        self.assertIn("| Inworld (TTS) | $7.39 |", md)
        self.assertIn("| **LiveKit path (Cartesia+Fish+Inworld+Deepgram+LiveKit)** | **$54.00** |", md)
        self.assertIn("| **Total voice** | **$64.00** |", md)

    def test_a_failed_store_import_leaves_no_rate_and_still_renders(self):
        # The fish / inworld flags default to the store's constants, so they are None
        # when `store` didn't import; the table must still render rather than raise.
        rates = dict(RATES, fish=None, inworld=None)
        totals = {"cartesia": 1.0, "fish": 0.0, "inworld": 7.39, "deepgram": 0.0, "livekit": 0.0}
        md = W.render_voice_vendor_breakdown(0.0, totals, rates, [])
        self.assertIn("| Inworld (TTS) | $7.39 | as stored |", md)


def _entry(room, ts, **payload):
    return {"timestamp": ts, "jsonPayload": dict(room=room, **payload)}


T0, T10 = "2026-10-18T10:00:00Z", "2026-10-18T10:10:00Z"


class TestInferenceTtsGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lm = _load(STORE_SCRIPTS / "livekit_minutes.py", "livekit_minutes", STORE_SCRIPTS)
        cls.store = _load(STORE_SCRIPTS / "store.py", "store_under_guard_test", STORE_SCRIPTS)

    def _buckets(self, *created_payloads):
        entries = []
        for i, p in enumerate(created_payloads):
            entries += [_entry(f"r{i}", T0, userId=f"u{i}", **p),
                        _entry(f"r{i}", T10, userId=f"u{i}", message="Session usage totals")]
        _, _, b = self.lm._aggregate_sessions(self.lm._build_sessions(entries), "cartesia")
        return b

    def test_todays_direct_plugin_rows_are_not_flagged(self):
        b = self._buckets(
            {"message": "TTS provider created: inworld (voice: v1)", "tts_provider": "inworld",
             "tts_model": "inworld-tts-2", "voice_id": "v1"},
            {"message": "TTS provider created: cartesia (voice: v2)", "tts_provider": "cartesia",
             "tts_model": "sonic-3", "voice_id": "v2"},
            # STT on Inference is the normal case and must not be mistaken for TTS.
            {"message": "Session config resolved", "stt_provider": "deepgram-inference",
             "tts_provider": "elevenlabs", "tts_model": "eleven_flash_v2"})
        self.assertEqual(b["tts_inference_sessions_by_provider"], {})
        self.assertIsNone(self.store.inference_tts_note(b))

    def test_an_inference_provider_token_is_flagged_and_still_bucketed_under_its_vendor(self):
        b = self._buckets({"message": "TTS provider created: cartesia-inference (voice: v)",
                           "tts_provider": "cartesia-inference", "voice_id": "v"})
        self.assertEqual(b["tts_inference_sessions_by_provider"], {"cartesia": 1})
        self.assertAlmostEqual(b["minutes_by_provider"]["cartesia"], 10.0)

    def test_a_vendor_slash_model_id_is_flagged(self):
        b = self._buckets({"message": "TTS provider created: inworld (voice: v)",
                           "tts_provider": "inworld", "tts_model": "inworld/inworld-tts-1"})
        self.assertEqual(b["tts_inference_sessions_by_provider"], {"inworld": 1})

    def test_a_flagged_day_gets_a_loud_note(self):
        note = self.store.inference_tts_note(
            {"tts_inference_sessions_by_provider": {"cartesia": 2, "inworld": 1}})
        self.assertTrue(note.startswith("tts_via_livekit_inference: 3 session(s)"))
        self.assertIn("cartesia=2, inworld=1", note)
        self.assertIn("MISSING", note)

    def test_no_log_data_is_not_a_flag(self):
        self.assertIsNone(self.store.inference_tts_note({}))


if __name__ == "__main__":
    unittest.main()
