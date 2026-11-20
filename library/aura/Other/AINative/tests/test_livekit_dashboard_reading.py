"""An unread billing period must not render like a clean one.

Six of the seven LiveKit pools are not derivable from anything we collect, and LiveKit
exposes no billing API — the Cloud dashboard is the only source. So "watch the pools" was a
habit with nowhere to record its result: the one pool that actually blew (Krisp, August
2026, 955 minutes over, $1.14) was found by opening the dashboard, not by anything noticing,
and September's invoiced overage rendered as `$0.00` when nobody had looked at all.

`DASHBOARD_READINGS` gives the reading a home, which buys three properties this file pins
because each fails by looking like good news:

1. **Unread is distinguishable from zero.** `unbooked_overage` returns `None` for a period
   never read and `(0.0, False)` for one confirmed clean. Collapsing those is the defect.
2. **An actual outranks a lower bound.** `inference_credits_usd` sums to a LOWER BOUND
   across days the store could not measure; the dashboard carries the real figure. A rule
   that always prefers the derived value prints `≥ $0.00` beside a known `$15.35`.
3. **Below-threshold overage is reported, not dropped.** The threshold decides whether an
   amount is *booked*, never whether it is *shown* — a month that quietly reconciles to a
   total that is not the invoice is the silent shortfall the threshold exists to avoid.

Stdlib `unittest`, no network, no keys.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest
import uuid

REPO = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = REPO / "plugins"
CANONICAL = PLUGINS / "eng/skills/report-livekit-quota/scripts/livekit_plan.py"
COPIES = sorted(PLUGINS.glob("eng/skills/*/scripts/livekit_plan.py"))
QUOTA = PLUGINS / "eng/skills/report-livekit-quota/scripts/report.py"


def load(path: pathlib.Path):
    name = f"_lkplan_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    saved = list(sys.path)
    sys.path.insert(0, str(path.parent))
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = saved
        sys.modules.pop(name, None)
    return mod


class UnreadIsNotZero(unittest.TestCase):
    """Property 1 — the distinction the dashboard read exists to create."""

    def test_a_period_never_read_returns_none(self):
        for path in COPIES:
            with self.subTest(copy=path.parent.parent.name):
                plan = load(path)
                self.assertIsNone(plan.unbooked_overage("1999-01"))

    def test_a_period_read_and_clean_returns_zero_not_none(self):
        plan = load(CANONICAL)
        plan.DASHBOARD_READINGS["2099-01"] = {"read_on": "2099-01-15", "invoiced_overage_usd": 0.0}
        got = plan.unbooked_overage("2099-01")
        self.assertIsNotNone(got, "a read period must not report as unread")
        self.assertEqual(got[0], 0.0)

    def test_the_september_invoice_extras_are_recorded_and_book(self):
        # Krisp $56.85 + observability audio $56.35 off the closed statement ($915.84).
        plan = load(CANONICAL)
        amount, booked = plan.unbooked_overage("2026-09")
        self.assertAlmostEqual(amount, 113.20, places=2)
        self.assertTrue(booked)
        self.assertFalse(plan.dashboard_reading("2026-09")["partial"])

    def test_a_partial_reading_is_reported_but_never_booked(self):
        # October's 10-16 read was $506.36 to date, well over the threshold, but the
        # statement was open.
        plan = load(CANONICAL)
        plan.DASHBOARD_READINGS["2099-10"] = {"partial": True, "invoiced_overage_usd": 506.36}
        amount, booked = plan.unbooked_overage("2099-10")
        self.assertAlmostEqual(amount, 506.36, places=2)
        self.assertFalse(booked)

    def test_the_october_statement_is_closed_and_books(self):
        # Krisp $299.27 + observability audio $1,048.67 + spans $290.11 + transcripts $78.75.
        plan = load(CANONICAL)
        amount, booked = plan.unbooked_overage("2026-10")
        self.assertAlmostEqual(amount, 1716.80, places=2)
        self.assertTrue(booked)
        self.assertTrue(plan.dashboard_reading("2026-11")["partial"])

    def test_observability_is_one_pool_and_october_is_over_it(self):
        # The three "incl." lines on the October invoice sum to the 5M allotment exactly, and
        # the entries past it are the statement's spans + transcripts lines.
        plan = load(CANONICAL)
        self.assertEqual(2027330 + 1700733 + 1271937, plan.included("observability_entries", "scale"))
        used = plan.dashboard_reading("2026-10")["observability_entries"]
        self.assertAlmostEqual(plan.overage_usd("observability_entries", "scale", used),
                               290.11 + 78.75, delta=0.02)

    def test_the_august_overage_is_recorded(self):
        plan = load(CANONICAL)
        amount, booked = plan.unbooked_overage("2026-08")
        self.assertAlmostEqual(amount, 1.14, places=2)
        self.assertFalse(booked, "1.14 is far below the threshold and must not book")

    def test_every_copy_agrees_on_the_readings(self):
        # A plan fact that differs between copies is the exact drift that put store.py on
        # Scale and the quota report on Ship.
        answers = {path.parent.parent.name: load(path).unbooked_overage("2026-08")
                   for path in COPIES}
        self.assertEqual(len(set(answers.values())), 1, answers)


class TheThresholdDecidesBookingNotVisibility(unittest.TestCase):
    """Property 3 — a threshold that hides is a silent shortfall, not a simplification."""

    def test_an_amount_at_the_threshold_books(self):
        plan = load(CANONICAL)
        self.assertGreater(plan.OVERAGE_BOOKING_THRESHOLD_USD, 0)

    def test_the_renderer_states_an_unbooked_amount(self):
        src = QUOTA.read_text(encoding="utf-8")
        fn = src[src.index("def render_dashboard_read("):]
        fn = fn[:fn.index("\ndef ", 10)]
        self.assertIn("not booked", fn)
        self.assertIn("below the", fn)

    def test_the_renderer_warns_loudly_on_an_unread_period(self):
        src = QUOTA.read_text(encoding="utf-8")
        fn = src[src.index("def render_dashboard_read("):]
        fn = fn[:fn.index("\ndef ", 10)]
        self.assertIn("has not been read", fn)
        # The load-bearing word: an unread pool is UNKNOWN, and saying so is the whole
        # point — "no overage recorded" would read as a clean period.
        self.assertIn("not\u00a0zero".replace("\u00a0", " "), fn)

    def test_the_report_is_wired_to_render_it(self):
        # The #989 lesson: a renderer main() never calls ships as a no-op that looks like a
        # fix. Assert the call, not just the definition.
        src = QUOTA.read_text(encoding="utf-8")
        self.assertIn("render_dashboard_read(p)", src)
        self.assertIn('"dashboard_reading": plan_facts.dashboard_reading(', src)


class AnActualOutranksABound(unittest.TestCase):
    """Property 2 — derive the allocation, never the total."""

    def _build(self):
        name = f"_quota_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(name, QUOTA)
        mod = importlib.util.module_from_spec(spec)
        saved = list(sys.path)
        sys.path.insert(0, str(QUOTA.parent))
        sys.modules[name] = mod
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.path[:] = saved
            sys.modules.pop(name, None)
        return mod

    def test_a_bounded_derivation_is_replaced_by_the_dashboard_actual(self):
        q = self._build()
        rows = q.build_pool_report(
            "scale",
            {"inference_credits_usd": 0.0},
            coverage={"inference_credits_usd": {"days": 14, "unmeasured_days": 5}},
            reading={"read_on": "2026-09-15", "inference_credits_usd": 15.35},
        )
        infer = next(r for r in rows if r["pool"] == "inference_credits_usd")
        self.assertEqual(infer["source"], "dashboard")
        self.assertAlmostEqual(infer["measured"], 15.35, places=2)

    def test_a_whole_derivation_is_not_replaced(self):
        # The negative case: without it, "always prefer the dashboard" passes the test above
        # and silently discards a live per-period derivation in favour of a stale hand read.
        q = self._build()
        rows = q.build_pool_report(
            "scale",
            {"agent_session_minutes": 8451.0},
            coverage=None,
            reading={"read_on": "2026-09-15", "agent_session_minutes": 8037},
        )
        row = next(r for r in rows if r["pool"] == "agent_session_minutes")
        self.assertEqual(row["source"], "measured")
        self.assertAlmostEqual(row["measured"], 8451.0, places=1)

    def test_a_dashboard_row_does_not_inherit_the_stores_coverage_caveat(self):
        # The LOWER BOUND note describes the store's gap. Carrying it onto an actual would
        # caveat a figure that needs no caveat.
        q = self._build()
        rows = q.build_pool_report(
            "scale",
            {"inference_credits_usd": 0.0},
            coverage={"inference_credits_usd": {"days": 14, "unmeasured_days": 5}},
            reading={"read_on": "2026-09-15", "inference_credits_usd": 15.35},
        )
        infer = next(r for r in rows if r["pool"] == "inference_credits_usd")
        self.assertNotIn("coverage", infer)

    def test_a_mid_period_read_is_marked_partial(self):
        q = self._build()
        rows = q.build_pool_report(
            "scale", {}, coverage=None,
            reading={"read_on": "2026-09-15", "partial": True, "krisp_minutes": 3776},
        )
        krisp = next(r for r in rows if r["pool"] == "krisp_minutes")
        self.assertEqual(krisp["status"], "PARTIAL")

    def test_a_pool_with_neither_source_stays_unmeasured(self):
        q = self._build()
        rows = q.build_pool_report("scale", {}, coverage=None, reading=None)
        self.assertTrue(all(r["status"] == "UNMEASURED" for r in rows))


class TheAlertPathSurvivesADashboardPartial(unittest.TestCase):
    """`--alert` is the unattended surface, so a crash there is the worst placement.

    `build_alert` assumed every PARTIAL row carried the store's `coverage` key. A
    dashboard row read mid-period is PARTIAL without it, so the live September path raised
    KeyError before emitting a verdict or an exit code — and a monitoring caller reads a
    non-zero exit as an alerting condition, not as a broken report. The identical
    assumption had already been fixed in the pools footnote and not in its sibling.
    """

    QUOTA = PLUGINS / "eng/skills/report-livekit-quota/scripts/report.py"

    def _q(self):
        name = f"_quota_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(name, self.QUOTA)
        mod = importlib.util.module_from_spec(spec)
        saved = list(sys.path)
        sys.path.insert(0, str(self.QUOTA.parent))
        sys.modules[name] = mod
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.path[:] = saved
            sys.modules.pop(name, None)
        return mod

    def _payload(self, q):
        pools = q.build_pool_report(
            "scale", {}, coverage=None,
            reading={"read_on": "2026-09-15", "partial": True,
                     "krisp_minutes": 3776, "inference_credits_usd": 15.35},
        )
        return {"pools": pools, "plan": "scale", "consumed_minutes": 8451.0,
                "included_minutes": 50000, "pct_used": 16.9, "projected_minutes": 18109.0}

    def test_build_alert_does_not_raise_on_a_dashboard_partial(self):
        q = self._q()
        alert = q.build_alert(self._payload(q), 80.0, 95.0)
        self.assertIn(alert["status"], {"OK", "WARN", "CRITICAL"})

    def test_the_partial_reason_is_stated_once_not_per_pool(self):
        # Six identical sentences bury the reason that differs; on an alert surface that
        # is the only line a reader needs.
        q = self._q()
        alert = q.build_alert(self._payload(q), 80.0, 95.0)
        ptd = [r for r in alert["reasons"] if "period-to-date" in r]
        self.assertEqual(len(ptd), 1, ptd)

    def test_a_store_partial_still_reports_its_unmeasured_days(self):
        # The other cause must keep its own, more specific sentence.
        q = self._q()
        pools = q.build_pool_report(
            "scale", {"inference_credits_usd": 0.0},
            coverage={"inference_credits_usd": {"days": 14, "unmeasured_days": 5}},
            reading=None,
        )
        alert = q.build_alert({"pools": pools, "plan": "scale", "consumed_minutes": 0.0,
                               "included_minutes": 50000, "pct_used": 0.0,
                               "projected_minutes": 0.0}, 80.0, 95.0)
        self.assertTrue(any("unmeasured" in r for r in alert["reasons"]), alert["reasons"])


class ProvenanceIsAttributedToTheRightSource(unittest.TestCase):
    """A hand-read figure must not be credited to the cost store."""

    QUOTA = PLUGINS / "eng/skills/report-livekit-quota/scripts/report.py"

    def _q(self):
        name = f"_quota_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(name, self.QUOTA)
        mod = importlib.util.module_from_spec(spec)
        saved = list(sys.path)
        sys.path.insert(0, str(self.QUOTA.parent))
        sys.modules[name] = mod
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.path[:] = saved
            sys.modules.pop(name, None)
        return mod

    def test_dashboard_pools_are_named_as_dashboard(self):
        q = self._q()
        rows = q.build_pool_report(
            "scale", {}, coverage=None,
            reading={"read_on": "2026-09-15", "krisp_minutes": 3776},
        )
        text = "\n".join(q.render_pools(rows))
        self.assertIn("hand-read off the", text)
        self.assertNotIn("Krisp background minutes is read from the cost store", text)

    def test_provenance_prints_even_when_nothing_is_unmeasured(self):
        # It used to be gated on `if unmeasured:`, so a complete dashboard read removed the
        # "only N of M are derivable" caveat exactly when the table mixed three sources.
        q = self._q()
        reading = {"read_on": "2026-09-15"}
        for key in ("webrtc_participant_minutes", "downstream_data_gb", "krisp_minutes",
                    "observability_entries", "observability_audio_minutes",
                    "inference_credits_usd"):
            reading[key] = 1
        rows = q.build_pool_report("scale", {"agent_session_minutes": 8451.0},
                                   coverage=None, reading=reading)
        self.assertTrue(all(r["measured"] is not None for r in rows))
        text = "\n".join(q.render_pools(rows))
        self.assertIn("pools are derivable from the", text)


if __name__ == "__main__":
    unittest.main()
