"""The Inference credit pool stops being UNMEASURED — without becoming a wrong number.

`eng:report-livekit-quota` listed `inference_credits_usd` as UNMEASURED because the
forwarded agent logs cannot see it. `eng:store-aurie-user-daily-cost` now STORES the
drawdown, so the pool can be reported — but the month it reports spans days the store
could not measure, and summing across those yields a LOWER BOUND, not a figure.

Reporting that bound as the value is worse than the honest UNMEASURED it replaces: a
quota report exists to say how close a block is to full, and a confident understatement
is the one answer that defeats it. So the pool carries a PARTIAL status, `≥` on the
figure and the percentage, and the count of days nothing measured.

Every assertion here covers a failure that produces a plausible number rather than an
error — including the one that shipped in the first cut of this file's subject (see
`TestNullIsNotZero`).
"""

import importlib.util
import pathlib
import sys
import types
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/report-livekit-quota/scripts"


def _load():
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("quota_report", SCRIPTS / "report.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["quota_report"] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(SCRIPTS))


class TestNullIsNotZero(unittest.TestCase):
    """THE regression guard, and it shipped broken in the first cut.

    `deepgram_inference_drawdown` is NULL for every row written before the column
    existed. `SUM(NULL)` is NULL, and `NULL = 0` evaluates to NULL rather than TRUE — so
    a bare `dd = 0` in the COUNTIF matches none of those days. The pool then reported
    "$0.00, 0%, OK" for a month whose drawdown nothing had ever recorded: a comparison
    that silently never fires, which is the same shape the column exists to remove.
    Verified live against BigQuery: the bare form gave `unmeasured_days=0`, the
    COALESCE'd form gave 4 — the four days between the Inference cutover and the agent
    deploy.
    """

    def setUp(self):
        self.src = (SCRIPTS / "report.py").read_text()

    def test_both_operands_are_null_guarded(self):
        self.assertIn("COUNTIF(lk > 0 AND COALESCE(dg, 0) = 0 AND COALESCE(dd, 0) = 0)",
                      self.src)

    def test_the_bare_comparison_is_gone(self):
        # The form that counted nothing. Named explicitly so a "simplification" that
        # drops the COALESCE fails here rather than in a month's quota report.
        self.assertNotIn("lk > 0 AND dg = 0 AND dd = 0", self.src)

    def test_the_sum_itself_is_coalesced(self):
        # SUM over an all-NULL column is NULL, and float(None) raises — a crash rather
        # than a wrong number, but still a break.
        self.assertIn("ROUND(COALESCE(SUM(dd), 0), 4)", self.src)


class TestCoverageDrivesStatus(unittest.TestCase):
    def setUp(self):
        self.rep = _load()

    def _rows(self, usd, days, unmeasured):
        return self.rep.build_pool_report(
            "scale", {self.rep.CREDITS_POOL: usd},
            {self.rep.CREDITS_POOL: {"days": days, "unmeasured_days": unmeasured}})

    def _credits(self, rows):
        return next(r for r in rows if r["pool"] == self.rep.CREDITS_POOL)

    def test_a_fully_measured_month_is_ok(self):
        self.assertEqual(self._credits(self._rows(12.65, 30, 0))["status"], "OK")

    def test_any_unmeasured_day_makes_it_partial(self):
        # Not OK: with days nothing could measure, the sum is a floor.
        self.assertEqual(self._credits(self._rows(12.65, 13, 4))["status"], "PARTIAL")

    def test_past_the_block_is_over_even_when_partial(self):
        # A lower bound that already exceeds the allotment is over regardless of what
        # the unmeasured days would have added — OVER must not be softened to PARTIAL.
        self.assertEqual(self._credits(self._rows(60.0, 13, 4))["status"], "OVER")

    def test_the_pool_is_still_not_derivable_from_logs(self):
        # It is reported because it is STORED, not because the logs gained the ability
        # to see it. A `derivable` flag flipped here would misdescribe the source.
        self.assertFalse(self._credits(self._rows(12.65, 30, 0))["derivable"])


class TestRendering(unittest.TestCase):
    def setUp(self):
        self.rep = _load()

    def _render(self, usd, days, unmeasured):
        rows = self.rep.build_pool_report(
            "scale", {self.rep.CREDITS_POOL: usd},
            {self.rep.CREDITS_POOL: {"days": days, "unmeasured_days": unmeasured}})
        return "\n".join(self.rep.render_pools(rows))

    def test_a_partial_figure_is_marked_as_a_bound(self):
        out = self._render(12.65, 13, 4)
        self.assertIn("≥ $12.65", out)
        self.assertIn("4 of 13 days unmeasured", out)
        self.assertIn("≥ 25%", out)  # 12.65 of the $50 block

    def test_a_partial_month_says_it_may_be_fuller(self):
        # The reader's actual question is "how close am I to the block?" — a bound with
        # no direction is not an answer.
        self.assertIn("may be fuller", self._render(12.65, 13, 4))

    def test_a_complete_month_carries_no_bound_markers(self):
        out = self._render(12.65, 30, 0)
        self.assertIn("$12.65", out)
        self.assertNotIn("≥", out)
        self.assertNotIn("may be fuller", out)


class TestUnreachableStoreDegradesToUnmeasured(unittest.TestCase):
    def setUp(self):
        self.rep = _load()

    def _with_query(self, fn):
        stub = types.ModuleType("bq")
        stub.query = fn
        saved = sys.modules.get("bq")
        sys.modules["bq"] = stub
        try:
            return self.rep.stored_inference_drawdown("2026-09-15", "p", "p.d.t")
        finally:
            if saved is not None:
                sys.modules["bq"] = saved
            else:
                del sys.modules["bq"]

    def test_a_failure_returns_none_never_zero(self):
        # None -> the pool reports UNMEASURED. A 0 would read as "block untouched",
        # which is the single worst answer an unreachable store can give here.
        def boom(sql, project, params=None):
            raise RuntimeError("bigquery unavailable")
        self.assertIsNone(self._with_query(boom))

    def test_an_empty_result_returns_none(self):
        self.assertIsNone(self._with_query(lambda s, p, params=None: []))

    def test_an_unmeasured_pool_renders_as_not_derivable(self):
        rows = self.rep.build_pool_report("scale", {})
        credits = next(r for r in rows if r["pool"] == self.rep.CREDITS_POOL)
        self.assertEqual(credits["status"], "UNMEASURED")
        self.assertIsNone(credits["pct_used"])


class TestWarehouseProjectNotLoggingProject(unittest.TestCase):
    """The BigQuery job bills to the WAREHOUSE project (Bugbot High).

    The first cut passed `args.project or auth.project` — the Cloud LOGGING project
    (`firebase-auratech16`), with `--project` a log override. The SA holds
    `bigquery.jobUser` on `aura-main` only, so the job would be refused, and
    `stored_inference_drawdown` swallows any failure into `None` → UNMEASURED. The pool
    would have stayed silently blank on every default run.

    It survived a *live* check because that check called the function directly with
    "aura-main" — verifying the function while leaving the wiring untested, which is the
    same gap that let a hardcoded `0.0` ship in PROD-6008.
    """

    def setUp(self):
        self.src = (SCRIPTS / "report.py").read_text()

    def test_the_call_uses_the_warehouse_project(self):
        self.assertIn("stored_inference_drawdown(\n"
                      "        now.strftime(\"%Y-%m-%d\"), bq_project(), COST_TABLE)", self.src)

    def test_it_never_passes_a_logging_project(self):
        # The two spellings that name the log project. Either one silently blanks the pool.
        self.assertNotIn("args.project or auth.project", self.src)
        call = self.src[self.src.index("drawdown = stored_inference_drawdown"):][:200]
        self.assertNotIn("auth.project", call)
        self.assertNotIn("args.project", call)

    def test_the_accessor_reads_the_bundled_helper(self):
        rep = _load()
        self.assertEqual(rep.bq_project(), "aura-main")
        self.assertTrue(rep.COST_TABLE.startswith("aura-main."))


class TestAlertCoversMeasuredPools(unittest.TestCase):
    """A measured pool joins the verdict, or the OK is unqualified (Bugbot Medium).

    A pool left `unmeasured_pools` the moment it gained a value but never entered the
    OK/WARN/CRITICAL check — so `--alert` could print OK while that pool was OVER. That is
    the shape the Krisp comment exists to prevent, reintroduced by the change that made
    the pool measurable in the first place.
    """

    def setUp(self):
        self.rep = _load()

    def _alert(self, credits_usd, unmeasured_days=0, minutes=7000.0):
        pools = self.rep.build_pool_report(
            "scale",
            {self.rep.MINUTES_POOL: minutes, self.rep.CREDITS_POOL: credits_usd},
            {self.rep.CREDITS_POOL: {"days": 30, "unmeasured_days": unmeasured_days}})
        payload = {"pools": pools, "pct_used": minutes / 50000 * 100,
                   "projected_minutes": minutes, "included_minutes": 50000,
                   "overage_rate": 0.01}
        return self.rep.build_alert(payload, warn_pct=80.0, crit_pct=100.0)

    def test_a_pool_over_its_allotment_makes_the_verdict_critical(self):
        a = self._alert(60.0)
        self.assertEqual(a["status"], "CRITICAL")
        self.assertTrue(any("Inference credits" in r for r in a["reasons"]), a["reasons"])

    def test_a_pool_near_its_allotment_warns(self):
        self.assertEqual(self._alert(45.0)["status"], "WARN")

    def test_a_quiet_pool_leaves_the_verdict_ok(self):
        self.assertEqual(self._alert(5.0)["status"], "OK")

    def test_the_scope_names_every_pool_the_verdict_covers(self):
        # An OK whose scope says "agent-session minutes only" while silently covering more
        # (or less) is the claim the data does not support.
        self.assertIn("Inference credits", self._alert(5.0)["scope"])

    def test_a_lower_bound_is_said_out_loud_even_when_ok(self):
        # A floor can only understate, so a quiet verdict on one is not a clean bill.
        a = self._alert(5.0, unmeasured_days=4)
        self.assertTrue(any("lower bound" in r for r in a["reasons"]), a["reasons"])


class TestFootnoteCountsDerivableNotMeasured(unittest.TestCase):
    """"Derivable from the forwarded agent logs" must not count a stored pool (Medium)."""

    def setUp(self):
        self.rep = _load()

    def _render(self, with_credits):
        m = {self.rep.MINUTES_POOL: 7000.0}
        c = {}
        if with_credits:
            m[self.rep.CREDITS_POOL] = 12.65
            c[self.rep.CREDITS_POOL] = {"days": 30, "unmeasured_days": 0}
        return "\n".join(self.rep.render_pools(self.rep.build_pool_report("scale", m, c)))

    def test_a_stored_pool_does_not_inflate_the_derivable_count(self):
        # Both runs derive exactly ONE pool from the logs; only the source of the second
        # differs. A count of "rows with a value" would read 1 then 2.
        self.assertIn("**1 of 7** pools are derivable", self._render(False))
        self.assertIn("**1 of 7** pools are derivable", self._render(True))

    def test_the_stored_pool_is_named_as_stored(self):
        self.assertIn("Inference credits is read from the cost store", self._render(True))


class TestDependencyIsDeclared(unittest.TestCase):
    def test_bigquery_is_in_requirements(self):
        # bq.py imports google-cloud-bigquery; without it BQUnavailable becomes a silent
        # UNMEASURED on a fresh env, with no install hint anywhere.
        req = (SCRIPTS / "requirements.txt").read_text()
        self.assertIn("google-cloud-bigquery", req)


if __name__ == "__main__":
    unittest.main()
