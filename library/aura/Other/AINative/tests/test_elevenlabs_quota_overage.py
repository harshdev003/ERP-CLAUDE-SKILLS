"""report-elevenlabs-quota treats on-demand overage as normal and alerts on $ steps (ENG-1708).

ElevenLabs raises character_limit as usage reaches it, so overage is measured against the plan's
base grant, not the live limit (ENG-1726).

Aura pays ElevenLabs overage every month on purpose, so any live overage used to read CRITICAL
("already billing over the limit") and the limit read as a run-out date. The alert now warns per
`--overage-step-usd` step, the %/days thresholds are opt-in, and the report projects overage in $.
"""
import importlib.util
import io
import pathlib
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1] / "plugins/eng/skills"
SCRIPT = ROOT / "report-elevenlabs-quota/scripts/report.py"
QUOTA = ROOT / "report-quota/scripts/report.py"

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
RESET = int(datetime(2026, 11, 13, 11, 14, 4, tzinfo=timezone.utc).timestamp())
LIMIT = 24_495_216


def load(path, name):
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(path.parent))


def sub(used, overage="0", limit=LIMIT):
    # The live /v1/user/subscription shape: monthly period, overage amount as a string, the limit
    # raised in on-demand blocks above the plan's base grant, and the $990 base plan on next_invoice.
    return {"tier": "scale_2024_08_10", "status": "active", "character_count": used,
            "character_limit": limit, "next_character_count_reset_unix": RESET,
            "billing_period": "monthly_period", "character_refresh_period": "monthly_period",
            "current_overage": {"amount": overage, "currency": "usd"},
            "next_invoice": {"amount_due_cents": 99000, "subtotal_cents": 99000}}


RATE = 0.000165
BASE = 6_000_000
PREVIOUS = {"start": "2026-09-13", "end": "2026-10-13", "credits": 46_638_953.0, "usd": 7_694.97}
CURRENT = {"start": "2026-10-13", "end": "2026-10-30", "credits": 24_000_000.0, "usd": 24_000_000 * RATE}


class Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code, self.ok, self.text = body, status, status < 400, ""

    def json(self):
        return self.body


class OverageAgainstTheBaseGrant(unittest.TestCase):
    """ENG-1726: ElevenLabs raises character_limit as usage reaches it, so used never passes it and
    current_overage stays "0". Overage is measured against the plan's base grant instead."""

    def setUp(self):
        self.mod = load(SCRIPT, "elevenlabs_quota_under_test")

    def test_used_below_a_raised_limit_still_reports_overage(self):
        # The 11-02 reading: 27.84M used, live limit 28.13M, current_overage "0".
        p = self.mod.build_payload(sub(27_842_457, limit=28_131_576), NOW, None, current=CURRENT)
        self.assertEqual((p["base_credits"], p["base_source"]), (BASE, "plan_price_at_rate"))
        self.assertEqual(p["overage_credits"], 27_842_457 - BASE)
        self.assertAlmostEqual(p["overage_usd"], (27_842_457 - BASE) * RATE)
        self.assertGreater(p["overage_usd"], 3_000)
        self.assertEqual(p["api_overage_usd"], 0.0)
        self.assertEqual(p["extension_credits"], 28_131_576 - BASE)
        self.assertEqual(p["days_until_overage"], 0.0)
        self.assertEqual(self.mod.build_alert(p)["status"], "WARN")

    def test_projection_is_past_the_base_not_the_live_limit(self):
        p = self.mod.build_payload(sub(27_842_457, limit=28_131_576), NOW, None, current=CURRENT)
        self.assertAlmostEqual(p["projected_overage_usd"], (p["projected_at_reset"] - BASE) * RATE)

    def test_base_credits_flag_wins(self):
        p = self.mod.build_payload(sub(27_842_457, limit=28_131_576), NOW, None, current=CURRENT,
                                   base_credits=10_000_000)
        self.assertEqual((p["base_credits"], p["base_source"]), (10_000_000, "--base-credits"))
        self.assertAlmostEqual(p["overage_usd"], 17_842_457 * RATE)

    def test_the_rate_falls_back_to_the_previous_period(self):
        p = self.mod.build_payload(sub(27_842_457, limit=28_131_576), NOW, PREVIOUS)
        self.assertEqual(p["overage_rate_source"], "previous_period_spend")
        self.assertEqual(p["base_credits"], round(990 / (7_694.97 / 46_638_953.0), -3))

    def test_no_rate_falls_back_to_the_live_limit_and_says_so(self):
        p = self.mod.build_payload(sub(24_400_000), NOW, None)
        self.assertEqual((p["base_credits"], p["base_source"]), (LIMIT, "live_limit"))
        self.assertIn("overage understated", self.mod.render(p))


class OverageIsNormal(unittest.TestCase):
    def setUp(self):
        self.mod = load(SCRIPT, "elevenlabs_quota_under_test")

    def test_overage_below_a_step_is_ok(self):
        p = self.mod.build_payload(sub(BASE + 3_000_000), NOW, None, current=CURRENT)  # $495
        self.assertEqual(p["overage_step"], 0)
        self.assertEqual(self.mod.build_alert(p)["status"], "OK")

    def test_reaching_a_step_warns_never_critical(self):
        p = self.mod.build_payload(sub(BASE + 7_000_000), NOW, None, current=CURRENT)  # $1,155
        self.assertEqual(p["overage_step"], 2)
        alert = self.mod.build_alert(p)
        self.assertEqual(alert["status"], "WARN")
        self.assertIn("step 2 ($1,000)", alert["reasons"][0])

    def test_custom_step(self):
        p = self.mod.build_payload(sub(BASE + 7_000_000), NOW, None, 1000, CURRENT)
        self.assertEqual(p["overage_step"], 1)

    def test_percent_and_days_are_opt_in(self):
        p = self.mod.build_payload(sub(5_900_000), NOW, None, current=CURRENT)
        self.assertEqual(self.mod.build_alert(p)["status"], "OK")
        self.assertEqual(self.mod.build_alert(p, crit_pct=95)["status"], "CRITICAL")
        self.assertEqual(self.mod.build_alert(p, warn_days=30)["status"], "WARN")

    def test_past_the_base_overage_has_started(self):
        p = self.mod.build_payload(sub(BASE + 1), NOW, None, current=CURRENT)
        self.assertEqual(p["days_until_overage"], 0.0)
        self.assertIn("on-demand overage has started (normal on this plan)", self.mod.render(p))
        self.assertNotIn("runway", self.mod.render(p).lower())


class ProjectedOverage(unittest.TestCase):
    def setUp(self):
        self.mod = load(SCRIPT, "elevenlabs_quota_under_test")

    def test_rate_comes_from_this_periods_spend_first(self):
        p = self.mod.build_payload(sub(BASE + 1_000_000), NOW, PREVIOUS, current=CURRENT)
        self.assertEqual(p["overage_rate_source"], "current_period_spend")
        self.assertAlmostEqual(p["overage_rate_usd_per_credit"], RATE)
        self.assertIn("this period's spend per credit, 2026-10-13 to 2026-10-30", self.mod.render(p))

    def test_before_this_periods_spend_the_rate_is_the_previous_periods(self):
        p = self.mod.build_payload(sub(4_000_000), NOW, PREVIOUS)
        self.assertEqual(p["overage_rate_source"], "previous_period_spend")
        self.assertAlmostEqual(p["overage_rate_usd_per_credit"], 7_694.97 / 46_638_953.0)
        self.assertGreater(p["projected_overage_usd"], 0)
        text = self.mod.render(p)
        self.assertIn("(derived)", text)
        self.assertIn("2026-09-13 to 2026-10-13", text)

    def test_no_rate_leaves_the_projection_blank_not_the_report(self):
        p = self.mod.build_payload(sub(24_069_712), NOW, None)
        self.assertIsNone(p["projected_overage_usd"])
        self.assertIn("Projected overage $: n/a", self.mod.render(p))


class PreviousPeriodRead(unittest.TestCase):
    def setUp(self):
        self.mod = load(SCRIPT, "elevenlabs_quota_under_test")

    def test_reads_the_period_before_the_current_one_and_converts_cents(self):
        calls = []

        def get(url, headers=None, params=None, timeout=None):
            calls.append(params)
            value = 769_497.0 if params["metric"] == "fiat_units_spent" else 46_638_953.0
            return Resp({"time": [0, 1], "usage": {"All": [value / 2, value / 2]}})

        with mock.patch.object(self.mod.requests, "get", get):
            prev = self.mod.fetch_previous_period("k", sub(1))
        self.assertEqual((prev["start"], prev["end"]), ("2026-09-13", "2026-10-13"))
        self.assertAlmostEqual(prev["usd"], 7_694.97)
        self.assertEqual(prev["credits"], 46_638_953.0)
        self.assertEqual({c["metric"] for c in calls}, {"credits", "fiat_units_spent"})

    def test_a_failed_read_is_none(self):
        with mock.patch.object(self.mod.requests, "get", lambda *a, **k: Resp({}, 500)), \
                redirect_stderr(io.StringIO()):
            self.assertIsNone(self.mod.fetch_previous_period("k", sub(1)))

    def test_month_end_clamps(self):
        utc = lambda *a: datetime(*a, tzinfo=timezone.utc)
        self.assertEqual(self.mod.months_before(utc(2026, 3, 31), 1), utc(2026, 2, 28))
        self.assertEqual(self.mod.months_before(utc(2026, 1, 15), 1), utc(2025, 12, 15))


class MainExitCode(unittest.TestCase):
    def run_main(self, subscription, credits, usd_cents):
        mod = load(SCRIPT, "elevenlabs_quota_under_test")
        urls = []

        def get(url, headers=None, params=None, timeout=None):
            urls.append(url)
            if url.endswith("/user/subscription"):
                return Resp(subscription)
            return Resp({"usage": {"All": [usd_cents if params["metric"] == "fiat_units_spent" else credits]}})

        with mock.patch.object(mod.requests, "get", get), \
                mock.patch.dict("os.environ", {"ELEVENLABS_ADMIN_API_KEY": "k"}), \
                mock.patch.object(sys, "argv", ["report.py", "--alert"]), \
                redirect_stdout(io.TextIOWrapper(io.BytesIO())), redirect_stderr(io.StringIO()):
            code = mod.main()
        return code, urls

    def test_a_raised_limit_with_zero_api_overage_exits_warn(self):
        used = 27_842_457
        code, urls = self.run_main(sub(used, limit=28_131_576), used, used * RATE * 100)
        self.assertEqual(code, 10)
        self.assertEqual(len(urls), 3)  # subscription + this period's credits and spend

    def test_under_the_base_is_ok(self):
        code, urls = self.run_main(sub(4_000_000), 4_000_000, 4_000_000 * RATE * 100)
        self.assertEqual(code, 0)

    def test_no_spend_this_period_reads_the_previous_period(self):
        code, urls = self.run_main(sub(24_400_000), 0, 0)
        self.assertEqual(code, 0)
        self.assertEqual(len(urls), 5)


class ReportQuotaReadsThePayload(unittest.TestCase):
    def test_summary_uses_the_producers_keys(self):
        el = load(SCRIPT, "elevenlabs_quota_under_test")
        rq = load(QUOTA, "report_quota_under_test")
        p = el.build_payload(sub(BASE + 6_000_000, limit=12_060_000), NOW, None, current=CURRENT)
        line = rq._summ_elevenlabs(p)
        self.assertIn("overage $990", line)
        self.assertIn("12,000,000/6,000,000 credits (200%)", line)  # base grant, not the live limit
        self.assertIn("proj overage ~$", line)
        self.assertNotIn("runway", line)


if __name__ == "__main__":
    unittest.main()
