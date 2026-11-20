#!/usr/bin/env python3
"""Krisp and observability-audio overage accrue daily from session minutes (ENG-1589).

Both pools are metered per minute of an agent session (Sep 2026 invoice: 0.93x and 0.99x
agent minutes) and both ran past their allotments once voice went LiveKit-only, yet the
store booked neither until someone hand-read an invoice. What this pins:

  * the accrual is a month-to-date delta: a month's days sum to the month's overage
    whatever the daily split, and a day inside both allotments books $0;
  * September's real minutes give ~$121, against $113.20 invoiced (Krisp proxy runs high);
  * a pool without a published overage rate books nothing rather than a guess;
  * store.py allocates the accrual per user by LiveKit minute share (with the consumed
    credits) and carries it in the day total; `Other` keeps only an unallocatable remainder.
"""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
COPIES = sorted((REPO / "plugins").glob("eng/skills/*/scripts/livekit_plan.py"))
STORE = REPO / "plugins/eng/skills/store-aurie-user-daily-cost/scripts/store.py"


def _load(path):
    spec = importlib.util.spec_from_file_location(f"lkplan_{path.parent.parent.name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSessionProxyOverage(unittest.TestCase):

    def test_days_sum_to_the_month_whatever_the_split(self):
        for path in COPIES:
            with self.subTest(copy=path.parent.parent.name):
                L = _load(path)
                days = [500.0] * 10 + [5000.0] * 10 + [1838.0] * 10   # 73,380 min
                mtd, booked = 0.0, 0.0
                for m in days:
                    booked += L.session_proxy_overage_usd("scale", mtd, m)
                    mtd += m
                whole = L.session_proxy_overage_usd("scale", 0.0, mtd)
                self.assertAlmostEqual(booked, whole, places=6)

    def test_inside_both_allotments_books_nothing(self):
        L = _load(COPIES[0])
        self.assertEqual(L.session_proxy_overage_usd("scale", 0.0, 9_999.0), 0.0)

    def test_september_minutes_land_near_the_invoice(self):
        L = _load(COPIES[0])
        got = L.session_proxy_overage_usd("scale", 0.0, 61_838.0)
        # (61,838 - 10,000) x $0.0012 + (61,838 - 50,000) x $0.005
        self.assertAlmostEqual(got, 62.2056 + 59.19, places=2)
        self.assertLess(abs(got - 113.20) / 113.20, 0.10)

    def test_a_pool_with_no_published_rate_books_nothing(self):
        L = _load(COPIES[0])
        # Ship publishes no observability-audio allotment or rate: only Krisp accrues.
        self.assertAlmostEqual(L.session_proxy_overage_usd("ship", 0.0, 2_000.0),
                               (2_000 - 1_000) * 0.0012, places=6)


    def test_observability_audio_stops_on_its_recording_off_day_and_krisp_continues(self):
        # ENG-1861: recording off since 2026-11-04 20:03Z; November's observability audio sat
        # inside its 50k allotment while session minutes passed it on 11-05.
        krisp_only = (60_000 - 10_000) * 0.0012
        for path in COPIES:
            with self.subTest(copy=path.parent.parent.name):
                L = _load(path)
                self.assertAlmostEqual(L.session_proxy_overage_usd("scale", 0.0, 60_000.0, "2026-11-05"),
                                       krisp_only, places=6)
                self.assertAlmostEqual(L.session_proxy_overage_usd("scale", 0.0, 60_000.0, "2026-12-01"),
                                       krisp_only, places=6)
                # Earlier days keep their accrual: October still books observability audio.
                self.assertAlmostEqual(L.session_proxy_overage_usd("scale", 0.0, 60_000.0, "2026-10-20"),
                                       krisp_only + 10_000 * 0.005, places=6)
                self.assertAlmostEqual(L.session_proxy_overage_usd("scale", 0.0, 60_000.0, "2026-11-04"),
                                       krisp_only + 10_000 * 0.005, places=6)

    def test_the_store_passes_the_day(self):
        src = STORE.read_text()
        self.assertIn("livekit_plan.session_proxy_overage_usd(plan, before, today, day))", src)
        self.assertIn("args.livekit_plan, day, lk_minutes_before, lk_total_min)", src)
        self.assertIn("session_proxy_overage_usd(plan, before, billed, day)", src)


class TestStoreBooksTheAccrual(unittest.TestCase):

    def test_users_and_the_day_total_carry_it(self):
        src = STORE.read_text()
        self.assertIn("day_livekit, day_livekit_unused, day_livekit_pools = livekit_day_dollars(", src)
        # Allocated by minute share with the consumed credits; `Other` keeps only the
        # remainder (a day with no minutes to allocate on).
        self.assertIn("livekit = allocate_by_minutes(day_livekit + day_livekit_pools, "
                      "lk_min_by_user, lk_total_min)", src)
        self.assertIn("olk = round(max(day_livekit + day_livekit_pools - sum(livekit.values()), 0.0), 4)", src)
        self.assertIn("+ day_livekit_pools)", src)

    def test_minute_share_allocation_reconciles(self):
        src = STORE.read_text()
        start = src.index("def allocate_by_minutes(")
        ns = {}
        exec(src[start:src.index("\ndef ", start + 1)], ns)
        got = ns["allocate_by_minutes"](10.0 + 6.2, {"a": 600.0, "b": 400.0}, 1000.0)
        self.assertAlmostEqual(got["a"], 9.72, places=6)
        self.assertAlmostEqual(got["b"], 6.48, places=6)
        self.assertAlmostEqual(sum(got.values()), 16.2, places=6)
        # No minutes: nothing attributed, so the caller's remainder lands on `Other`.
        self.assertEqual(ns["allocate_by_minutes"](6.2, {}, 0.0), {})


if __name__ == "__main__":
    unittest.main()
