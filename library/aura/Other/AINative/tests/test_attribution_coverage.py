#!/usr/bin/env python3
"""Attribution coverage — a walk can use the right source and still resolve nothing.

The defect this guards
----------------------
ENG-1114 gates the cost store on attribution *source*: `materiality
.degraded_share_pct`, the share of spend split by a stored-completions fallback
after a ledger read FAILED. That cannot see a collapse, for two reasons:

* a pre-epoch window legitimately attributes from stored completions (the ledger
  has no rows before 2026-07-23T03:25Z), so there is no `completions-fallback`
  marker to notice;
* if the walk resolves ZERO users there is no degraded *share* either — the
  denominator is source-classified dollars, not dollars that reached anyone.

So source and outcome fail independently, and both need a guard.

What it looked like in production
---------------------------------
Three consecutive days in `aura-main.aurie_cost.aurie_per_user_daily_cost`:

    date        users  users w/ llm   llm attributed   llm on `Other`
    2026-06-23    342           317          $151.38            $1.00
    2026-06-24     95             0            $0.00          $144.88
    2026-06-25     80             0            $0.00          $151.00

Neighbouring days run ~1,600–1,670 users at ~99.7% attributed. Voice attribution
was intact on all three, so this is the LLM per-user join specifically.

Nothing errored, and no total was wrong: the unattributed remainder lands on the
store's `Other` reconciling row, so the day still balances to the authoritative
bill. Only the distribution is empty — which is exactly what
`chart-aurie-cost-concentration`, `-cost-mix`, `-cohort-cost`, `-tenure-cost`,
`-cost-curve`, `-cumulative-cost`, `-lifetime-cost` and `-cohort-payback` read.

Why there are TWO guards and not one
------------------------------------
2026-06-23 is the case a coverage ratio cannot catch. Allocation is by token
share of the project's dollars, so when only SOME completions survive, the
survivors absorb 100% of the money: coverage reads 99.34% while 317 users carry
what ~1,630 should have, inflating each by roughly 5x. Only a count comparison
against the day's own history sees it. Both thresholds are calibrated on the 70
days held at 2026-08-10 — see the constants in `store.py` for the observed
ranges.

Failure injection is the point — a live window cannot produce a collapsed walk on
demand, so the per-user maps, dollar splits and BigQuery baselines here are
synthesised directly.
"""
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr

REPO = pathlib.Path(__file__).resolve().parents[1]
REPORT = REPO / "plugins/eng/skills/analyze-openai-cost-per-user/scripts/report.py"

# Same stubbed-google loader trap as tests/test_attribution_materiality.py:
# store.py imports livekit_minutes, which does `from google.cloud import logging`
# at module scope and sys.exit(2) when the SDK is missing (CI installs only
# requests). A loader that swallows SystemExit yields a HALF-INITIALISED module.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_el_caller_mapping import _load_store  # noqa: E402


def _load_report():
    """Load report.py. Deliberately does NOT catch SystemExit."""
    spec = importlib.util.spec_from_file_location("rep_cov", REPORT)
    mod = importlib.util.module_from_spec(spec)
    argv = sys.argv
    sys.argv = ["rep_cov"]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = argv
    return mod


rep = _load_report()
store = _load_store()

PRE = rep.LEDGER_EPOCH_UNIX - 40 * 86400           # June 2026 — the affected window
PRE_END = PRE + 86400
POST = rep.LEDGER_EPOCH_UNIX + 10 * 86400
POST_END = POST + 86400


def _users(*amounts):
    return {f"uid{i}": {"amount": a} for i, a in enumerate(amounts)}


class ModuleLoadedFully(unittest.TestCase):
    """Names the cause when the loader half-loads, instead of N AttributeErrors."""

    def test_report_loaded_fully(self):
        for name in ("summarize_attribution_coverage", "build_coverage_clause"):
            self.assertTrue(hasattr(rep, name), f"report.py missing {name}")

    def test_store_loaded_fully(self):
        for name in ("assert_attribution_coverage", "assert_user_count_plausible",
                     "MIN_ATTRIBUTION_COVERAGE_PCT", "MIN_USER_COUNT_RATIO"):
            self.assertTrue(hasattr(store, name), f"store.py missing {name}")


class CoverageSummary(unittest.TestCase):
    """`summarize_attribution_coverage` measures outcome, not source."""

    def test_healthy_day_is_near_complete(self):
        cov = rep.summarize_attribution_coverage(
            _users(90.0, 9.0), {"p1": 100.0}
        )
        self.assertAlmostEqual(cov["coverage_pct"], 99.0, places=4)
        self.assertEqual(cov["users_with_cost"], 2)
        self.assertAlmostEqual(cov["unreached_dollars"], 1.0, places=6)

    def test_total_collapse_reads_zero(self):
        """The 2026-06-24 shape: dollars walked, no user resolved."""
        cov = rep.summarize_attribution_coverage({}, {"p1": 144.88})
        self.assertEqual(cov["coverage_pct"], 0.0)
        self.assertEqual(cov["users_with_cost"], 0)
        self.assertAlmostEqual(cov["unreached_dollars"], 144.88, places=6)

    def test_no_walkable_spend_is_complete_not_collapsed(self):
        """A genuinely zero-LLM day must not read as a collapse."""
        cov = rep.summarize_attribution_coverage({}, {})
        self.assertEqual(cov["coverage_pct"], 100.0)
        self.assertEqual(cov["eligible_dollars"], 0.0)

    def test_skipped_projects_are_excluded_from_the_ratio(self):
        """Dev/internal spend is real, but is nobody's cost-to-serve.

        Counting it as uncovered would make a healthy day look broken and is
        exactly the false-refusal ENG-1114 was filed to stop."""
        cov = rep.summarize_attribution_coverage(
            _users(100.0), {"p1": 100.0}, skipped_dollars=74.0
        )
        self.assertEqual(cov["coverage_pct"], 100.0)
        self.assertEqual(cov["skipped_dollars"], 74.0)

    def test_share_never_exceeds_one_hundred_percent(self):
        cov = rep.summarize_attribution_coverage(
            _users(100.000000001), {"p1": 100.0}
        )
        self.assertLessEqual(cov["coverage_pct"], 100.0)


class MaterialityCannotSeeIt(unittest.TestCase):
    """The load-bearing negative: the ENG-1114 guard passes a collapsed day.

    If this ever fails, the two guards have stopped being independent and the
    argument for keeping both needs re-examining — not the test relaxing."""

    def test_pre_epoch_collapse_has_no_degraded_share(self):
        # A pre-epoch walk uses `completions` legitimately: no fallback marker.
        mat = rep.summarize_attribution_materiality({"completions-pre-epoch": 144.88})
        self.assertEqual(mat["degraded_share_pct"], 0.0)
        # ENG-1114's guard therefore accepts the day...
        store.assert_attribution_usable({"attribution": {"materiality": mat}})
        # ...while coverage refuses it.
        cov = rep.summarize_attribution_coverage({}, {"p1": 144.88})
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                store.assert_attribution_coverage(
                    {"attribution": {"materiality": mat, "coverage": cov}}
                )

    def test_zero_users_leaves_no_degraded_share_to_gate_on(self):
        """Nothing resolved ⇒ no *share* is degraded, only the outcome is empty."""
        mat = rep.summarize_attribution_materiality({})
        self.assertEqual(mat["degraded_share_pct"], 0.0)
        self.assertEqual(mat["attributed_dollars"], 0.0)


class CoverageClauseReachesEveryBranch(unittest.TestCase):
    """The clause must appear on a PRE-EPOCH note — where the failure lives.

    `build_attribution_note` returns early for a wholly pre-epoch window, so a
    clause wired into its post-epoch branches would have been invisible for
    exactly the days this ticket is about. That is why `main` appends it."""

    def _note(self, start, end, sources, cov):
        return rep.build_attribution_note(start, end, sources) + \
            rep.build_coverage_clause(cov)

    def test_collapse_is_announced_on_a_pre_epoch_window(self):
        cov = rep.summarize_attribution_coverage({}, {"p1": 144.88})
        note = self._note(PRE, PRE_END, {"completions"}, cov)
        self.assertIn("ATTRIBUTION COLLAPSE", note)
        self.assertIn("0.00%", note)

    def test_collapse_is_announced_on_a_post_epoch_window(self):
        cov = rep.summarize_attribution_coverage({}, {"p1": 144.88})
        note = self._note(POST, POST_END, {"ledger"}, cov)
        self.assertIn("ATTRIBUTION COLLAPSE", note)

    def test_healthy_day_reports_coverage_without_alarm(self):
        cov = rep.summarize_attribution_coverage(_users(99.7), {"p1": 100.0})
        note = self._note(POST, POST_END, {"ledger"}, cov)
        self.assertIn("per-user coverage", note)
        self.assertNotIn("ATTRIBUTION COLLAPSE", note)

    def test_zero_spend_day_says_nothing(self):
        cov = rep.summarize_attribution_coverage({}, {})
        self.assertEqual(rep.build_coverage_clause(cov), "")


class StoreRefusesACollapsedDay(unittest.TestCase):
    """`store.py` is the writer — it is what makes a collapse permanent."""

    def _json(self, per_user, walked, **kw):
        return {"attribution": {
            "coverage": rep.summarize_attribution_coverage(per_user, walked, **kw)
        }}

    def test_refuses_the_2026_06_24_shape(self):
        with self.assertRaises(SystemExit) as cm:
            with redirect_stderr(io.StringIO()):
                store.assert_attribution_coverage(self._json({}, {"p1": 144.88}))
        self.assertIn("REFUSING to store", str(cm.exception))
        self.assertIn("Other", str(cm.exception))

    def test_accepts_a_healthy_day(self):
        with redirect_stderr(io.StringIO()):
            store.assert_attribution_coverage(self._json(_users(99.7), {"p1": 100.0}))

    def test_accepts_a_day_with_no_walkable_llm_spend(self):
        store.assert_attribution_coverage(self._json({}, {}))

    def test_override_persists_deliberately(self):
        with redirect_stderr(io.StringIO()):
            store.assert_attribution_coverage(
                self._json({}, {"p1": 144.88}), allow_low_coverage=True
            )

    def test_pre_eng1120_json_warns_rather_than_reporting_a_pass(self):
        """A missing check is not a clean one — say so on stderr."""
        err = io.StringIO()
        with redirect_stderr(err):
            store.assert_attribution_coverage({"attribution": {"sources": ["ledger"]}})
        self.assertIn("NOT verified", err.getvalue())

    def test_threshold_sits_between_the_observed_ranges(self):
        """Healthy days ran 91.44%+; collapsed days ran 0.00%."""
        self.assertLess(store.MIN_ATTRIBUTION_COVERAGE_PCT, 91.44)
        self.assertGreater(store.MIN_ATTRIBUTION_COVERAGE_PCT, 0.0)

    def test_llm_costs_applies_the_guard(self):
        """Wiring check: the guard is useless if `llm_costs` skips it."""
        payload = self._json({}, {"p1": 144.88})
        payload["users_all"] = []
        payload["total"] = 144.88
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(payload, fh)
            path = fh.name
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                store.llm_costs(path)


class _FakeBQ(types.ModuleType):
    """Stands in for `bq` so the baseline query is injectable.

    Records the SQL so the parameter binding can be asserted — a stub cannot
    execute BigQuery, so the one thing worth pinning here is that the query does
    not depend on an implicit STRING→DATE coercion to run at all. A baseline
    query that errors would be caught by the caller and skip the check, leaving
    the guard permanently disabled while every run looked clean."""

    def __init__(self, counts, raises=None):
        super().__init__("bq")
        self._counts, self._raises = counts, raises
        self.last_sql = None

    def query(self, sql, project=None, **kw):
        self.last_sql = sql
        if self._raises:
            raise self._raises
        return [{"users": c} for c in self._counts]


class UserCountPlausibility(unittest.TestCase):
    """The partial-collapse guard — the case coverage provably cannot see."""

    def setUp(self):
        self._saved = sys.modules.get("bq")

    def tearDown(self):
        if self._saved is not None:
            sys.modules["bq"] = self._saved
        else:
            sys.modules.pop("bq", None)

    def _run(self, user_count, counts, **kw):
        sys.modules["bq"] = _FakeBQ(counts)
        with redirect_stderr(io.StringIO()):
            store.assert_user_count_plausible(
                "2026-06-23", user_count, "t", "p", **kw
            )

    def test_refuses_the_2026_06_23_shape(self):
        """317 users against a ~1,630 median — 19% of normal."""
        with self.assertRaises(SystemExit) as cm:
            self._run(317, [1626] * 14)
        msg = str(cm.exception)
        self.assertIn("REFUSING to store", msg)
        self.assertIn("19%", msg)

    def test_accepts_the_lowest_legitimate_day_observed(self):
        """2026-07-23 ran 1,737 against a 2,507 median (69%) and is a real day."""
        self._run(1737, [2507] * 14)

    def test_accepts_organic_growth(self):
        self._run(2836, [2408] * 14)

    def test_skips_when_history_is_too_thin_to_judge(self):
        """A first backfill has no baseline; refusing would block seeding."""
        self._run(5, [1600, 1600])

    def test_override_persists_deliberately(self):
        self._run(317, [1626] * 14, allow_low=True)

    def test_threshold_sits_in_the_observed_gap(self):
        """Affected days scored 0.00/0.00/0.19; lowest healthy scored 0.69."""
        self.assertGreater(store.MIN_USER_COUNT_RATIO, 0.194)
        self.assertLess(store.MIN_USER_COUNT_RATIO, 0.693)

    def test_zero_users_refuses_cleanly_instead_of_dividing_by_zero(self):
        """The 2026-06-24 end of this guard's own range.

        The refusal message quoted a `1/ratio` inflation factor, which is
        undefined at exactly the total collapse the guard exists to catch — so
        the worst case raised ZeroDivisionError instead of refusing. Reachable
        whenever the coverage check doesn't exit first: a walk JSON predating
        ENG-1120 carries no coverage block, so it only warns."""
        with self.assertRaises(SystemExit) as cm:
            self._run(0, [1626] * 14, eligible_dollars=144.88)
        msg = str(cm.exception)
        self.assertIn("REFUSING to store", msg)
        self.assertIn("Other", msg)
        self.assertNotIn("inflated", msg)

    def test_quiet_day_with_no_walkable_spend_is_not_a_collapse(self):
        """Zero users is the correct answer when there was nothing to attribute."""
        self._run(0, [1626] * 14, eligible_dollars=0.0)

    def test_skip_uses_walkable_spend_not_the_org_total(self):
        """The denominator must exclude skipped Dev/internal projects.

        `llm_costs`' `total` is the org-wide OpenAI figure and still counts the
        `[Development]` and internal-tooling projects the walk deliberately skips.
        A day whose only spend sat in those has zero walkable dollars and zero
        users legitimately — gating on the org total would refuse it."""
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"total": 74.0, "attribution": {"coverage":
                       rep.summarize_attribution_coverage({}, {}, skipped_dollars=74.0)}}, fh)
            path = fh.name
        self.assertEqual(store.walk_eligible_dollars(path), 0.0)
        self._run(0, [1626] * 14, eligible_dollars=store.walk_eligible_dollars(path))

    def test_pre_eng1120_json_falls_back_to_the_org_total(self):
        """No coverage block ⇒ use the org total, which is safe in one direction.

        Walkable spend is a subset of the org total, so `llm_total <= 0` does
        imply nothing was walkable. Without the fallback a legitimate zero-LLM
        day on an old walk JSON reaches the count check as 0 users and is
        refused."""
        self._run(0, [1626] * 14, eligible_dollars=None, llm_total=0.0)

    def test_org_total_never_overrides_a_known_eligible_figure(self):
        """The converse does not hold, so the fallback must stay a fallback."""
        with self.assertRaises(SystemExit):
            self._run(0, [1626] * 14, eligible_dollars=144.88, llm_total=0.0)

    def test_baseline_query_casts_the_day_parameter_explicitly(self):
        """`@day` binds as STRING; the DATE comparison must not rely on coercion.

        BigQuery does coerce it (both forms were verified to return identical
        rows against the live table), but the guard fails *open* — the caller
        skips the check when the baseline query errors — so a query that only
        works by implicit coercion would be a silent, permanent disablement."""
        fake = _FakeBQ([1626] * 14)
        sys.modules["bq"] = fake
        with redirect_stderr(io.StringIO()):
            store.assert_user_count_plausible(
                "2026-06-23", 1600, "t", "p", eligible_dollars=100.0
            )
        self.assertIn("DATE(@day)", fake.last_sql)
        self.assertNotIn("date < @day", fake.last_sql)

    def test_pre_eng1120_json_has_no_eligible_figure(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"total": 144.88, "attribution": {"sources": ["ledger"]}}, fh)
            path = fh.name
        self.assertIsNone(store.walk_eligible_dollars(path))


if __name__ == "__main__":
    unittest.main()


class PopulationShiftIsNotACollapse(unittest.TestCase):
    """ENG-1559: the engaged-user share tells a retired cron from a lost completion.

    The ambient feed/notification jobs retired on 2026-10-07 billed nearly every
    reached user, so users-with-cost fell from ~2,300 to 934 while every engaged
    user still carried cost. The count ratio alone refused every day after."""

    def setUp(self):
        self._saved = sys.modules.get("bq")

    def tearDown(self):
        if self._saved is not None:
            sys.modules["bq"] = self._saved
        else:
            sys.modules.pop("bq", None)

    def _run(self, user_count, counts, **kw):
        sys.modules["bq"] = _FakeBQ(counts)
        err = io.StringIO()
        with redirect_stderr(err):
            store.assert_user_count_plausible("2026-10-08", user_count, "t", "p",
                                              eligible_dollars=1681.67, **kw)
        return err.getvalue()

    @staticmethod
    def _cohort(n_engaged, n_engaged_with_cost, n_other_with_cost):
        """An `engagement.day_engagement` cohort (set of uids) plus the `llm_costs` map."""
        engaged = {f"e{i}" for i in range(n_engaged)}
        llm = {f"e{i}": 0.5 for i in range(n_engaged_with_cost)}
        llm.update({f"o{i}": 0.5 for i in range(n_other_with_cost)})
        return engaged, llm

    def test_stores_the_2026_10_08_shape(self):
        """934 users vs a 3,152 median (30%), 463 of 518 engaged with cost."""
        engaged, llm = self._cohort(518, 463, 934 - 463)
        share = store.engaged_cost_share(engaged, llm)
        self.assertAlmostEqual(share, 0.894, places=3)
        note = self._run(sum(1 for v in llm.values() if v > 0), [3152] * 14,
                         engaged_share=share)
        self.assertIn("population shift", note)

    def test_still_refuses_the_2026_06_23_shape(self):
        """317 users vs ~1,630, and only 60 of 83 engaged users carried cost."""
        engaged, llm = self._cohort(83, 60, 317 - 60)
        share = store.engaged_cost_share(engaged, llm)
        with self.assertRaises(SystemExit) as cm:
            self._run(317, [1626] * 14, engaged_share=share)
        self.assertIn("72.3% of engaged users", str(cm.exception))

    def test_still_refuses_a_total_collapse(self):
        """2026-06-24: engaged users exist, none carries cost."""
        engaged, llm = self._cohort(65, 0, 0)
        with self.assertRaises(SystemExit):
            self._run(0, [1626] * 14,
                      engaged_share=store.engaged_cost_share(engaged, llm))

    def test_unknown_share_never_exonerates(self):
        """Mixpanel down ⇒ no cohort ⇒ count-only behaviour, refusal says why."""
        self.assertIsNone(store.engaged_cost_share(set(), {"a": 1.0}))
        with self.assertRaises(SystemExit) as cm:
            self._run(934, [3152] * 14, engaged_share=None)
        self.assertIn("could not be measured", str(cm.exception))

    def test_tiny_cohort_is_too_noisy_to_vouch(self):
        engaged, llm = self._cohort(store.MIN_ENGAGED_FOR_SHARE - 1, 29, 0)
        self.assertIsNone(store.engaged_cost_share(engaged, llm))

    def test_healthy_count_is_unaffected_by_a_low_share(self):
        """The share only exonerates; it never refuses a day the count accepts."""
        self._run(3000, [3152] * 14, engaged_share=0.10)

    def test_threshold_sits_in_the_observed_gap(self):
        """Collapsed days 0.723/0/0; healthy minimum 0.858 over 128 stored days."""
        self.assertGreater(store.MIN_ENGAGED_COST_SHARE, 0.723)
        self.assertLess(store.MIN_ENGAGED_COST_SHARE, 0.858)
