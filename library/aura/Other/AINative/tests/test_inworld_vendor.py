#!/usr/bin/env python3
"""Inworld TTS as a vendor column in the per-user cost store.

Why this needs a test
---------------------
Inworld served LiveKit sessions — and from Aurie 1.33.0 (2026-10-15) it is arm `a` of the
aiVoiceProvider experiment — but no column booked its dollars, so the arm read as free.
Not even the `Other` row carried it: that row holds remainders of totals the store
booked, and Inworld's was never booked. Every case below fails by producing a plausible
number, never an error.

Inworld HAS a usage API (`GET /usage/v1/records`), so the day total is ACTUAL characters
× the rate; the agent's own `tts_characters` is only the fallback when the key is missing
or the API fails. The transport is stubbed at `requests.get`, the one chokepoint, and the
200 fixture is the response the live API returned for 2026-10-18.

Also pins the TTS default the store applies to a session that never logged a provider:
the agent's default moved cartesia -> elevenlabs on 2026-07-31 (PROD-4079).

Stdlib only, no network, no keys.
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


from unittest import mock


def _entry(room, ts, **payload):
    return {"timestamp": ts, "jsonPayload": dict(room=room, **payload)}


T0, T10 = "2026-10-18T10:00:00Z", "2026-10-18T10:10:00Z"

# What the live API returned for 2026-10-18 (services=tts, groupBy model).
LIVE_2026_10_18 = {"usageRecords": [{
    "startTime": "2026-10-18T00:00:00Z", "endTime": "2026-10-19T00:00:00Z",
    "group": {"model": "inworld-tts-2"},
    "metrics": {"characters": {"value": "295464", "unit": "characters"},
                "characters_est_cost": {"value": "7386600", "unit": "usd_micros"}}}],
    "nextPageToken": "", "readTime": "2026-10-19T16:49:58.399482594Z"}


class _Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


def _meta(chars, sessions, measured):
    return {"tts_characters_by_provider": {"inworld": chars},
            "tts_sessions_by_provider": {"inworld": sessions},
            "tts_sessions_measured_by_provider": {"inworld": measured}}


class TestInworldIsAVendorEverywhereTheOthersAre(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = _load("store")
        cls.bq_sink = _load("bq_sink")
        cls.unit_costs = _load("unit_costs")
        cls.rebook = _load("rebook_livekit")

    def test_voice_inworld_sits_next_to_fish_in_the_csv_schema(self):
        cols = self.store.COLS
        self.assertEqual(cols.index("voice_inworld"), cols.index("voice_fish") + 1)

    def test_bigquery_schema_matches_the_csv_header_exactly(self):
        self.assertEqual([n for n, _t in self.bq_sink.STORES["per_user"]["schema"]], self.store.COLS)

    def test_rebook_and_unit_costs_carry_the_column(self):
        self.assertIn("voice_inworld", self.rebook._NUMERIC_COLS)
        self.assertIn("voice_inworld", self.unit_costs._LIVEKIT_PATH_COLS)
        self.assertIn("voice_inworld", self.unit_costs._STORE_LK_COST_COLS)

    def test_the_row_sum_check_includes_the_column(self):
        # voice_cost == Σ columns is the check that would catch a column left out of the
        # per-row sum; it only can if the column is in the check.
        src = (SCRIPTS / "store.py").read_text()
        check = src[src.index("sum_ok = all("):src.index("notes = [n for n in")]
        self.assertIn('r["voice_inworld"]', check)


class TestInworldDayTotal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = _load("store")

    def _call(self, get, meta=None, key="k", rate=25.0):
        env = {"INWORLD_API_KEY": key} if key else {}
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(self.store.requests, "get", get):
            if not key:
                os.environ.pop("INWORLD_API_KEY", None)
            return self.store.inworld_day_total("2026-10-18", meta or {}, rate)

    def test_actual_characters_from_the_usage_api_times_the_rate(self):
        dollars, note = self._call(lambda *a, **k: _Resp(200, LIVE_2026_10_18))
        self.assertAlmostEqual(dollars, 7.3866)  # = Inworld's own characters_est_cost
        self.assertEqual(note, "")

    def test_the_request_is_one_utc_day_of_tts(self):
        seen = {}

        def get(url, headers=None, params=None, timeout=None):
            seen.update(url=url, auth=headers["Authorization"], **params)
            return _Resp(200, LIVE_2026_10_18)
        self._call(get, key="abc")
        self.assertTrue(seen["url"].endswith("/usage/v1/records"))
        self.assertEqual(seen["auth"], "Basic abc")
        self.assertEqual((seen["startTime"], seen["endTime"], seen["services"]),
                         ("2026-10-18T00:00:00Z", "2026-10-19T00:00:00Z", "tts"))

    def test_every_page_is_summed(self):
        pages = [{"usageRecords": [{"metrics": {"characters": {"value": "1000000"}}}],
                  "nextPageToken": "p2"},
                 {"usageRecords": [{"metrics": {"characters": {"value": "500000"}}}],
                  "nextPageToken": ""}]
        dollars, _ = self._call(lambda *a, **k: _Resp(200, pages.pop(0)))
        self.assertAlmostEqual(dollars, 37.5)

    def test_a_failed_api_falls_back_to_agent_characters_with_a_note(self):
        dollars, note = self._call(lambda *a, **k: _Resp(500), meta=_meta(400_000, 4, 4))
        self.assertAlmostEqual(dollars, 10.0)
        self.assertIn("inworld_derived", note)
        self.assertIn("usage API failed", note)

    def test_a_persistent_429_falls_back_rather_than_booking_zero(self):
        with mock.patch.object(self.store.time, "sleep", lambda *_: None):
            dollars, note = self._call(lambda *a, **k: _Resp(429, {}), meta=_meta(400_000, 4, 4))
        self.assertAlmostEqual(dollars, 10.0)
        self.assertIn("inworld_derived", note)

    def test_a_persistent_429_on_cartesia_is_pending_not_zero(self):
        with mock.patch.dict(os.environ, {"CARTESIA_ADMIN_API_KEY": "k"}), \
             mock.patch.object(self.store.time, "sleep", lambda *_: None), \
             mock.patch.object(self.store.requests, "get", lambda *a, **k: _Resp(429, {})):
            dollars, note = self.store.cartesia_day_total("2026-10-18", 38.0)
        self.assertEqual(dollars, 0.0)
        self.assertIn("cartesia_pending", note)

    def test_no_key_falls_back_and_counts_unmeasured_sessions(self):
        def boom(*a, **k):
            raise AssertionError("no key must mean no call")
        dollars, note = self._call(boom, meta=_meta(400_000, 4, 3), key=None)
        self.assertAlmostEqual(dollars, 10.0)
        self.assertIn("INWORLD_API_KEY not set", note)
        self.assertIn("1 of 4", note)
        self.assertIn("UNMEASURED", note)

    def test_no_key_and_no_inworld_traffic_is_a_plain_zero(self):
        self.assertEqual(self._call(None, meta=_meta(0, 0, 0), key=None), (0.0, ""))

    def test_no_key_and_no_logs_is_pending_not_zero(self):
        dollars, note = self._call(None, meta={}, key=None)
        self.assertEqual(dollars, 0.0)
        self.assertTrue(note.startswith("inworld_pending"))


class TestAllocation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = _load("store")

    def test_inworld_dollars_reach_only_inworld_sessions_and_reconcile(self):
        alloc = self.store.allocate_by_minutes(7.3866, {"u1": 3.0, "u2": 1.0}, 5.0)
        other = max(round(7.3866 - sum(alloc.values()), 4), 0.0)
        self.assertAlmostEqual(sum(alloc.values()) + other, 7.3866, places=4)
        self.assertAlmostEqual(alloc["u1"], 7.3866 * 3 / 5)


class TestSchemaMigration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = _load("store")

    def test_the_previous_schema_is_todays_minus_the_new_column(self):
        self.assertEqual(self.store.PRE_INWORLD_COLS,
                         [c for c in self.store.COLS if c != "voice_inworld"])

    def test_a_pre_inworld_store_gains_the_column_at_zero_and_keeps_its_totals(self):
        pre = self.store.PRE_INWORLD_COLS
        row = {c: "0" for c in pre}
        row.update({"date": "2026-10-10", "user_id": "u1", "llm_cost": "1.5", "voice_fish": "0.25",
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
        self.assertEqual(rows[0]["voice_inworld"], "0.0")
        self.assertEqual((rows[0]["voice_fish"], rows[0]["total_cost"]), ("0.25", "1.75"))


class TestDefaultTtsProvider(unittest.TestCase):
    """A session with no provider line books under the agent's default ON THAT DAY."""

    @classmethod
    def setUpClass(cls):
        cls.lm = _load("livekit_minutes")

    def test_the_default_follows_the_agent(self):
        self.assertEqual(self.lm.default_tts_provider("2026-10-18"), "elevenlabs")
        self.assertEqual(self.lm.default_tts_provider("2026-08-01"), "elevenlabs")
        self.assertEqual(self.lm.default_tts_provider(), "elevenlabs")

    def test_a_day_before_the_switch_keeps_the_old_default(self):
        self.assertEqual(self.lm.default_tts_provider("2026-07-31"), "cartesia")

    def test_an_unlabelled_session_books_under_the_given_default(self):
        sessions = self.lm._build_sessions([
            _entry("r1", T0, userId="u1", message="hello"),
            _entry("r1", T10, userId="u1", message="bye"),
        ])
        _, _, b = self.lm._aggregate_sessions(sessions, "elevenlabs")
        self.assertAlmostEqual(b["minutes_by_provider"]["elevenlabs"], 10.0)
        self.assertEqual(b["minutes_by_provider"]["cartesia"], 0.0)
        self.assertEqual(b["default_tts_provider"], "elevenlabs")
        _, _, b = self.lm._aggregate_sessions(sessions, "cartesia")
        self.assertAlmostEqual(b["minutes_by_provider"]["cartesia"], 10.0)


if __name__ == "__main__":
    unittest.main()
