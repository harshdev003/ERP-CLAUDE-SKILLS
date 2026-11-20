"""The LiveKit plan of record, its allotments, and the properties that fail silently.

Three jobs, in increasing order of what a `diff -q` cannot do for us:

1. **Copies agree.** Every `livekit_plan.py` is byte-identical to the canonical.
2. **Consumers agree.** The plan fact each consumer actually resolves is the same one —
   the defect this file exists for. `store.py`'s comment said Scale while
   `report-livekit-quota` defaulted to Ship, and nothing compared them, so the two skills
   answered "is LiveKit costing us anything at the margin" differently depending on which
   one you ran.
3. **The dashboard facts are pinned.** Nothing in this repo can refresh them, and every one
   of them is wrong in a way that produces a confident number rather than an error.

Membership is by shape, not filename: any module defining `POOLS` + `effective_rate_per_min`
joins the family automatically, so a fourth copy cannot quietly drop out of coverage.
"""

import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = REPO / "plugins"
CANONICAL = PLUGINS / "eng/skills/report-livekit-quota/scripts/livekit_plan.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _discover():
    """Every module that IS a LiveKit plan table, found by shape rather than by name."""
    found = []
    for path in sorted(PLUGINS.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "POOLS = {" in text and "def effective_rate_per_min" in text:
            found.append(path)
    return found


class TestCopiesAgree(unittest.TestCase):
    def test_canonical_exists(self):
        self.assertTrue(CANONICAL.is_file(), f"canonical missing: {CANONICAL}")

    def test_every_copy_is_byte_identical(self):
        canon = CANONICAL.read_bytes()
        copies = [p for p in _discover() if p != CANONICAL]
        self.assertTrue(copies, "expected at least one sibling copy")
        for path in copies:
            with self.subTest(copy=str(path.relative_to(REPO))):
                self.assertEqual(canon, path.read_bytes(), "diverged from the canonical")

    def test_discovery_finds_every_file_named_livekit_plan(self):
        # Shape-discovery and filename must not disagree: a file named livekit_plan.py that
        # the sweep cannot see would silently sit outside the byte-identical check.
        by_name = set(PLUGINS.rglob("livekit_plan.py"))
        self.assertEqual(by_name, set(_discover()))


class TestPlanOfRecord(unittest.TestCase):
    """The reconcile itself — every consumer resolves the SAME plan."""

    def setUp(self):
        self.plan = _load(CANONICAL, "lk_plan_canonical")

    def test_plan_of_record_is_scale_renewing_on_the_first(self):
        # Read off the LiveKit Cloud dashboard (project p_5bw0of3laoy) on 2026-09-14:
        # CURRENT PLAN Scale, NEXT BILLING CYCLE October 1, 2026.
        self.assertEqual(self.plan.DEFAULT_PLAN, "scale")
        self.assertEqual(self.plan.DEFAULT_RENEWAL_DAY, 1)

    def test_quota_skill_defaults_to_the_plan_of_record(self):
        src = (PLUGINS / "eng/skills/report-livekit-quota/scripts/report.py").read_text()
        self.assertIn("DEFAULT_PLAN = plan_facts.DEFAULT_PLAN", src)
        self.assertIn("DEFAULT_RENEWAL_DAY = plan_facts.DEFAULT_RENEWAL_DAY", src)
        # The literal that used to disagree must be gone, not merely overridden elsewhere.
        self.assertNotIn('DEFAULT_PLAN = "ship"', src)

    def test_every_consumer_resolves_the_rate_from_the_table(self):
        # The point of the reconcile: no consumer may carry its own LiveKit plan literal.
        consumers = {
            "eng/skills/store-aurie-user-daily-cost/scripts/store.py": "DEFAULT_LIVEKIT_RATE_PER_MIN",
            "eng/skills/analyze-livekit-cost/scripts/report.py": "LIVEKIT_OVERAGE_PER_MIN",
        }
        for rel, const in consumers.items():
            with self.subTest(consumer=rel):
                src = (PLUGINS / rel).read_text()
                self.assertRegex(
                    src,
                    rf"{const} = livekit_plan\.overage_per_unit\(",
                    f"{const} must come from livekit_plan, not a local literal",
                )


class TestDashboardFacts(unittest.TestCase):
    """Pinned against the 2026-09-14 dashboard + pricing pages. A wrong value here does not
    raise — it prices a minute and reports a number nobody can tell is wrong."""

    def setUp(self):
        self.plan = _load(CANONICAL, "lk_plan_facts")

    def test_scale_subscription_and_allotments(self):
        p = self.plan
        self.assertEqual(p.subscription_usd("scale"), 500.0)
        self.assertEqual(p.included("agent_session_minutes", "scale"), 50_000)
        self.assertEqual(p.included("krisp_minutes", "scale"), 10_000)
        self.assertEqual(p.included("inference_credits_usd", "scale"), 50.00)

    def test_stt_rate_matches_the_invoice_line(self):
        # The billing line reads "STT deepgram - nova_3 incl. Inference credits ($0.252 per
        # hour)". That is the MONOLINGUAL Scale rate; multi mode bills more, so the language
        # band is load-bearing and a rate picked without it is wrong in silence.
        p = self.plan
        per_min = p.INFERENCE_RATES["deepgram/nova-3"]["monolingual"]["scale"]
        self.assertAlmostEqual(per_min * 60, 0.252, places=6)
        self.assertGreater(
            p.INFERENCE_RATES["deepgram/nova-3"]["multilingual"]["scale"], per_min,
            "multilingual must cost more than monolingual, or the bands are swapped",
        )

    def test_august_invoice_reconciles(self):
        # August 2026 billed $501.14 = $500 subscription + Krisp overage on 10,955 minutes
        # against a 10,000 allotment. The only variable dollar ever paid to LiveKit.
        p = self.plan
        krisp = p.overage_usd("krisp_minutes", "scale", 10_955)
        self.assertAlmostEqual(500.0 + krisp, 501.14, places=1)

    def test_september_stt_drawdown_is_inside_the_credit_balance(self):
        # 3,013 STT minutes at the Scale monolingual rate = $12.65 drawn against $50 of
        # included credits, so nothing is billed. The store must not charge this twice.
        p = self.plan
        drawdown = p.inference_usd("deepgram/nova-3", "scale", 3_013)
        self.assertAlmostEqual(drawdown, 12.65, places=2)
        self.assertEqual(p.overage_usd("inference_credits_usd", "scale", drawdown), 0.0)

    def test_the_facts_carry_a_verification_date(self):
        self.assertRegex(self.plan.PLAN_FACTS_VERIFIED, r"^\d{4}-\d{2}-\d{2}$")


class TestSilentProperties(unittest.TestCase):
    """The three behaviours whose breakage produces a plausible number, not an error."""

    def setUp(self):
        self.plan = _load(CANONICAL, "lk_plan_props")

    def test_inside_the_allotment_costs_nothing(self):
        # The defect this whole table exists for: pricing an included minute at the overage
        # rate books a charge that does not exist.
        self.assertEqual(self.plan.overage_usd("agent_session_minutes", "scale", 7_344), 0.0)
        self.assertEqual(self.plan.overage_usd("krisp_minutes", "scale", 3_776), 0.0)

    def test_effective_rate_is_never_below_the_overage_rate(self):
        # LiveKit prices each plan so subscription/allotment equals the overage rate exactly
        # at 100% utilization. So the overage rate is the FLOOR of what a minute can cost,
        # and quoting it as the cost books the theoretical minimum. Our own store did: Aug
        # 2026 booked $119.90 against a $501.14 invoice.
        for plan in ("ship", "scale"):
            rate = self.plan.overage_per_unit("agent_session_minutes", plan)
            allot = self.plan.included("agent_session_minutes", plan)
            with self.subTest(plan=plan):
                self.assertAlmostEqual(
                    self.plan.effective_rate_per_min(plan, allot), rate, places=6,
                    msg="effective rate should meet the overage rate at full utilization",
                )
                for used in (allot * 0.1, allot * 0.5, allot * 0.9):
                    self.assertGreater(self.plan.effective_rate_per_min(plan, used), rate)

    def test_the_floor_property_does_not_hold_on_a_zero_dollar_plan(self):
        # Build has no subscription, so subscription/allotment is $0 rather than the overage
        # rate: its effective rate starts at $0 and RISES. The floor claim is a property of the
        # PAID plans, and the report must not assert it on Build — a rendered sentence the
        # numbers beside it contradict is worse than no sentence.
        self.assertEqual(self.plan.subscription_usd("build"), 0.0)
        eff = self.plan.effective_rate_per_min(
            "build", 1_400, extra_overage_usd=self.plan.overage_usd("agent_session_minutes", "build", 1_400))
        self.assertLess(eff, self.plan.overage_per_unit("agent_session_minutes", "build"))
        src = (PLUGINS / "eng/skills/report-livekit-quota/scripts/report.py").read_text()
        self.assertIn("if sub > 0:", src, "the floor sentence must be gated on a paid plan")

    def test_effective_rate_stays_above_the_floor_past_full_utilization(self):
        # Past the allotment the subscription is spread over MORE minutes, so
        # subscription/minutes alone dips below the overage rate — the report would then quote a
        # $/min beneath the floor it calls a guaranteed minimum, precisely when it is alerting
        # CRITICAL. Overage is part of the invoice and must be in the numerator.
        plan, pool = "scale", "agent_session_minutes"
        rate = self.plan.overage_per_unit(pool, plan)
        allot = self.plan.included(pool, plan)
        for used in (allot * 1.2, allot * 2, allot * 10):
            with self.subTest(minutes=used):
                naive = self.plan.effective_rate_per_min(plan, used)
                self.assertLess(naive, rate, "precondition: the naive form dips below the floor")
                corrected = self.plan.effective_rate_per_min(
                    plan, used, extra_overage_usd=self.plan.overage_usd(pool, plan, used))
                self.assertGreaterEqual(round(corrected, 6), rate)

    def test_effective_rate_honours_the_cli_overrides(self):
        # --included-minutes / --overage-rate-per-min must reach the effective rate too,
        # or the minutes section and the effective-rate numerator quote different
        # overages for the same run — and a non-default rate could push the $/min back
        # below the floor the report asserts.
        scripts = PLUGINS / "eng/skills/report-livekit-quota/scripts"
        sys.path.insert(0, str(scripts))
        self.addCleanup(sys.path.remove, str(scripts))
        report = _load(scripts / "report.py", "lk_quota_overrides")
        # a tighter allotment than the plan's, at a custom rate
        self.assertAlmostEqual(report.measured_overage_usd(1_000, 1_400, 0.02), 8.00, places=6)
        self.assertEqual(report.measured_overage_usd(1_000, 900, 0.02), 0.0)
        src = (scripts / "report.py").read_text()
        self.assertNotIn("extra_overage_usd=plan_facts.overage_usd(MINUTES_POOL", src,
                         "the effective rate must use the run's own allotment/rate")

    def test_quota_report_includes_overage_in_the_effective_rate(self):
        # The property above only holds if the CALLER passes the overage — the defect was a
        # missing argument, not missing arithmetic.
        src = (PLUGINS / "eng/skills/report-livekit-quota/scripts/report.py").read_text()
        self.assertEqual(
            src.count("extra_overage_usd=measured_overage_usd("), 2,
            "both the current and projected effective rates must include measured overage",
        )

    def test_effective_rate_of_a_silent_month_is_none_not_zero(self):
        # Zero would read as "free" for a month that still cost $500.
        self.assertIsNone(self.plan.effective_rate_per_min("scale", 0))

    def test_august_effective_rate(self):
        self.assertAlmostEqual(
            self.plan.effective_rate_per_min("scale", 11_381, extra_overage_usd=1.14),
            0.0440, places=4,
        )


class TestUnmeasuredPoolsAreListed(unittest.TestCase):
    """A pool the logs cannot see must be reported as unmeasured, never dropped."""

    def setUp(self):
        scripts = PLUGINS / "eng/skills/report-livekit-quota/scripts"
        sys.path.insert(0, str(scripts))
        self.addCleanup(sys.path.remove, str(scripts))
        self.report = _load(scripts / "report.py", "lk_quota_report")

    def test_krisp_is_present_with_its_allotment(self):
        # Krisp is not derivable from the forwarded logs, and it is the pool that actually
        # went into overage while session minutes sat at 23%. Dropping it from the table
        # would make an unwatched pool indistinguishable from a healthy one.
        rows = self.report.build_pool_report("scale", {"agent_session_minutes": 7_344})
        krisp = next(r for r in rows if r["pool"] == "krisp_minutes")
        self.assertEqual(krisp["status"], "UNMEASURED")
        self.assertIsNone(krisp["measured"])
        self.assertEqual(krisp["included"], 10_000)

    def test_measured_pool_is_scored(self):
        rows = self.report.build_pool_report("scale", {"agent_session_minutes": 7_344})
        mins = next(r for r in rows if r["pool"] == "agent_session_minutes")
        self.assertEqual(mins["status"], "OK")
        self.assertAlmostEqual(mins["pct_used"], 14.688, places=2)
        self.assertEqual(mins["overage_usd"], 0.0)

    def test_alert_states_what_it_did_not_check(self):
        # An unqualified OK is a claim the data does not support.
        payload = {
            "pct_used": 14.7, "projected_minutes": 15_737, "included_minutes": 50_000,
            "overage_rate": 0.01,
            "pools": self.report.build_pool_report("scale", {"agent_session_minutes": 7_344}),
        }
        alert = self.report.build_alert(payload, warn_pct=80.0, crit_pct=100.0)
        self.assertEqual(alert["status"], "OK")
        self.assertIn("Krisp background minutes", alert["unmeasured_pools"])
        rendered = self.report.render_alert({**payload, "plan": "scale"}, alert)
        self.assertIn("SCOPE", rendered)


if __name__ == "__main__":
    unittest.main()
