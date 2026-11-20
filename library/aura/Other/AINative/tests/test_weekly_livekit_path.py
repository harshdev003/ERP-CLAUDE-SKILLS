#!/usr/bin/env python3
"""report-aurie-weekly-costs reads LiveKit-path $ from the store (ENG-1586).

The live path summed per-day vendor recomputes: `livekit_costs(day, rate)` with no
month-to-date minutes (no overage accrual) and `deepgram_day_total` (Deepgram direct
only, no LiveKit Inference STT). After the 2026-09-24 LiveKit-only switch that read
~$75-120/day under what the daily cost store booked. What this pins:

  * a stored day uses the store's booked $ and never calls the vendor helpers;
  * an unstored day falls back to the vendor recompute and says it reads low;
  * a failed store read degrades to the recompute for every day, with a note.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/eng/skills/report-aurie-weekly-costs/scripts/report.py"


def _load():
    spec = importlib.util.spec_from_file_location("weekly_report_under_lk_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


W = _load()
START = datetime(2026, 10, 4, tzinfo=timezone.utc)
END = datetime(2026, 10, 7, tzinfo=timezone.utc)  # 10-04, 10-05, 10-06
RATES = {"cartesia": 1, "fish": 1, "inworld": 1, "deepgram": 1, "livekit": 1}
# Real per-user-store sums (aurie_per_user_daily_cost), 2026-10-05 / 10-06.
STORED = [{"d": "2026-10-05", "cartesia": 0.0, "deepgram": 25.38, "livekit": 67.68},
          {"d": "2026-10-06", "cartesia": 0.0, "deepgram": 23.82, "livekit": 59.56}]


class _Store:
    def __init__(self):
        self.days = []

    def cartesia_day_total(self, d, r):
        self.days.append(d)
        return 0.0, ""

    def deepgram_day_total(self, d, r):
        return 1.0, ""

    def fish_day_total(self, meta, r):
        return 0.0, ""

    def inworld_day_total(self, d, meta, r):
        return 0.0, ""

    def livekit_costs(self, d, r):
        return {}, 100.0, 16.7, "", {}


def _bq(rows=None, exc=None):
    m = types.ModuleType("bq")

    def query(sql, params=None, **_):
        if exc:
            raise exc
        return [r for r in rows if params["d0"] <= r["d"] <= params["d_last"]]
    m.query = query
    return m


class TestWeeklyLivekitPath(unittest.TestCase):

    def _run(self, bq_mod):
        store = _Store()
        with mock.patch.dict(sys.modules, {"bq": bq_mod}), mock.patch.object(W, "_store", store):
            totals, notes = W.fetch_livekit_path_week(START, END, RATES)
        return totals, notes, store.days

    def test_stored_days_use_the_booked_dollars(self):
        totals, notes, recomputed = self._run(_bq(STORED))
        self.assertEqual(recomputed, ["2026-10-04"])
        self.assertAlmostEqual(totals["livekit"], 67.68 + 59.56 + 16.7, places=2)
        self.assertAlmostEqual(totals["deepgram"], 25.38 + 23.82 + 1.0, places=2)
        self.assertTrue(any(n.startswith("livekit_path_source: 2 of 3") for n in notes))
        self.assertTrue(any("livekit_path_unstored: 1 day(s) (2026-10-04)" in n for n in notes))

    def test_a_fully_stored_week_never_calls_the_vendor_helpers(self):
        rows = STORED + [{"d": "2026-10-04", "cartesia": 0.0, "deepgram": 10.0, "livekit": 30.0}]
        totals, notes, recomputed = self._run(_bq(rows))
        self.assertEqual(recomputed, [])
        self.assertFalse(any(n.startswith("livekit_path_unstored") for n in notes))

    def test_a_failed_store_read_recomputes_every_day_and_says_so(self):
        totals, notes, recomputed = self._run(_bq(exc=RuntimeError("no creds")))
        self.assertEqual(len(recomputed), 3)
        self.assertTrue(any(n.startswith("livekit_store_unavailable") for n in notes))


if __name__ == "__main__":
    unittest.main()
