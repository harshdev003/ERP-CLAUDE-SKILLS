"""LiveKit dollars are priced on BILLED minutes: logged minutes x LIVEKIT_LOG_TO_BILLED.

The forwarded GCP logs overcount the agent-session minutes LiveKit bills (3.2-3.6% on the
Sep and Oct 2026 invoices). The factor prices every LiveKit $ figure while the stored
`livekit_minutes` column stays as logged, so a later re-fit can rebook from raw minutes.
"""

import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/store-aurie-user-daily-cost/scripts"


def _load(name):
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(SCRIPTS))


class TestLogToBilled(unittest.TestCase):
    def setUp(self):
        self.store = _load("store")
        self.plan = "scale"
        self.f = self.store.LIVEKIT_LOG_TO_BILLED

    def test_the_factor_is_the_fitted_value(self):
        self.assertEqual(self.f, 0.966)

    def test_consumed_dollars_scale_by_the_factor(self):
        raw = self.store.livekit_day_dollars(self.plan, "2026-11-10", 5_000, 1_000, 1.0)
        billed = self.store.livekit_day_dollars(self.plan, "2026-11-10", 5_000, 1_000)
        self.assertAlmostEqual(billed[0], raw[0] * self.f, places=9)

    def test_per_user_voice_livekit_scales_and_totals_follow(self):
        minutes = {"a": 600.0, "b": 400.0}
        consumed, _unused, pools = self.store.livekit_day_dollars(
            self.plan, "2026-11-10", 5_000, 1_000)
        raw_c, _, raw_p = self.store.livekit_day_dollars(
            self.plan, "2026-11-10", 5_000, 1_000, 1.0)
        billed = self.store.allocate_by_minutes(consumed + pools, minutes, 1_000)
        logged = self.store.allocate_by_minutes(raw_c + raw_p, minutes, 1_000)
        llm = {"a": 0.5, "b": 0.25}
        for u in minutes:
            with self.subTest(user=u):
                # Inside the allotment the pools are 0, so the scale is exact.
                self.assertAlmostEqual(billed[u], logged[u] * self.f, places=9)
                voice_cost = billed[u]
                self.assertAlmostEqual(voice_cost + llm[u] - (logged[u] + llm[u]),
                                       (self.f - 1) * logged[u], places=9)
        # Minutes are not touched: the allocation reads them, never rewrites them.
        self.assertEqual(minutes, {"a": 600.0, "b": 400.0})

    def test_unused_capacity_moves_so_the_month_still_reconciles(self):
        # Inside the allotment, consumed + unused is the day's share of the subscription
        # whatever the minutes are; scaling consumed alone would drop 3.4% of it.
        c, u, _ = self.store.livekit_day_dollars(self.plan, "2026-11-10", 5_000, 1_000)
        rc, ru, _ = self.store.livekit_day_dollars(self.plan, "2026-11-10", 5_000, 1_000, 1.0)
        self.assertAlmostEqual(c + u, rc + ru, places=9)
        self.assertGreater(u, ru)

    def test_a_day_with_no_livekit_minutes_stays_zero(self):
        c, _u, p = self.store.livekit_day_dollars(self.plan, "2026-11-10", 5_000, 0.0)
        self.assertEqual((c, p), (0.0, 0.0))
        self.assertEqual(self.store.allocate_by_minutes(c + p, {}, 0.0), {})


if __name__ == "__main__":
    unittest.main()
