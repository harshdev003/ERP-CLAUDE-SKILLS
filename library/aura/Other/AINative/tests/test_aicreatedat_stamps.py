#!/usr/bin/env python3
"""aiCreatedAt batch stamps, and the AI-onboarding rescue rung.

**The defect this pins.** `backfill-aurie-user-ai-created` hardcoded
`2026-02-11T12:00:00` as *the* aiCreatedAt sentinel. It is one of a family: Postgres
carries several exact-noon batch stamps, so the skill refused that one value and stored
the other thirteen as `source=postgres` — a fake join date accepted as genuine. 631 users
(3.3% of the dimension, $703.37 of prod cost) were assigned to the wrong cohort in every
`chart-aurie-*` cohort/tenure/retention/payback chart. Nothing raised; the charts were
simply wrong for those users. PROD-6109.

**Why a test and not a wider constant.** Replacing one hardcoded value with fourteen
hardcoded values fails again the next time the upstream batch runs, and fails the same
silent way. The stamp is therefore detected by SHAPE — exact noon UTC *and* shared by
several users — and what needs pinning is that the shape rule keeps both halves:

- Drop the cluster half and every genuine noon join date is discarded as a stamp.
- Drop the noon half and ordinary timestamps start matching.

Both failures are silent, so `test_lone_noon_timestamp_is_not_a_stamp` is the
load-bearing negative: a per-construct test of "sentinel is detected" passes against a
detector that flags *everything* at noon.

**The rung-ordering property.** `consumerAIOnboarding` rescues a user whose Postgres
value is unusable; it must never override a real one. Measured on 592 users holding both,
the two agree to the same calendar day 97.3% of the time (median 0s) — but 10 of the 13
disagreements have onboarding LATER. So a rung order that let onboarding win would move
those users' cohorts later, which is the same class of silent cohort corruption the whole
card exists to remove. `test_real_aicreatedat_beats_onboarding` pins it.

Stdlib unittest, no network, no API keys.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/backfill-aurie-user-ai-created/scripts"


def _load():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("backfill_under_test", SCRIPTS / "backfill.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


backfill = _load()

REAL = "2026-03-14T09:22:41.000Z"
SENTINEL = "2026-02-11T12:00:00.000Z"
CLUSTERED_NOON = "2025-12-08T12:00:00.000Z"
LONE_NOON = "2026-06-30T12:00:00.000Z"
STAMPS = {backfill.SENTINEL_PREFIX, "2025-12-08T12:00:00", "2026-01-19T12:00:00"}


class TestStampDetection(unittest.TestCase):
    def test_sentinel_is_a_stamp_in_every_wire_format(self):
        for v in (SENTINEL, "2026-02-11T12:00:00Z", "2026-02-11 12:00:00+00:00",
                  "2026-02-11T12:00:00"):
            with self.subTest(v=v):
                self.assertTrue(backfill.is_batch_stamp(v, STAMPS))

    def test_clustered_noon_value_is_a_stamp(self):
        self.assertTrue(backfill.is_batch_stamp(CLUSTERED_NOON, STAMPS))

    def test_lone_noon_timestamp_is_not_a_stamp(self):
        """THE load-bearing negative. A real join date can land at exactly noon; only a
        noon value SHARED by several users is a batch stamp. A detector that flags all
        noon values passes every other test in this class and discards real join dates."""
        self.assertFalse(backfill.is_batch_stamp(LONE_NOON, STAMPS))

    def test_ordinary_timestamp_is_not_a_stamp(self):
        self.assertFalse(backfill.is_batch_stamp(REAL, STAMPS))

    def test_empty_and_none_are_not_stamps(self):
        for v in (None, "", "   "):
            with self.subTest(v=v):
                self.assertFalse(backfill.is_batch_stamp(v, STAMPS))

    def test_norm_ts_makes_postgres_and_bigquery_comparable(self):
        import datetime as dt
        pg = backfill._norm_ts("2025-12-08T12:00:00.000Z")
        bq_dt = backfill._norm_ts(dt.datetime(2025, 12, 8, 12, 0, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(pg, bq_dt)
        self.assertEqual(pg, "2025-12-08T12:00:00")


class TestOnboardingExtraction(unittest.TestCase):
    def test_reads_the_nested_key(self):
        rec = {"onboarding": {"shown": {"consumerAIOnboarding": "2025-09-16T19:01:14Z"}}}
        self.assertEqual(backfill._onboarding_ai_ts(rec), "2025-09-16T19:01:14Z")

    def test_missing_or_malformed_shapes_return_none(self):
        for rec in ({}, {"onboarding": None}, {"onboarding": {}}, {"onboarding": "str"},
                    {"onboarding": {"shown": None}}, {"onboarding": {"shown": {}}},
                    {"onboarding": {"shown": {"consumerAIOnboarding": ""}}}):
            with self.subTest(rec=rec):
                self.assertIsNone(backfill._onboarding_ai_ts(rec))


class TestResolutionLadder(unittest.TestCase):
    """fetch_aura with the transport stubbed at its single chokepoint (`_get_user`)."""

    def setUp(self):
        self._orig = backfill._get_user

    def tearDown(self):
        backfill._get_user = self._orig

    def _stub(self, prod_rec, status=200):
        def fake(base, uid):
            if base == backfill.PROD_BASE:
                return (status, prod_rec)
            return (404, None)
        backfill._get_user = fake

    def test_real_aicreatedat_beats_onboarding(self):
        """Onboarding rescues, never overrides — otherwise the 10-of-13 cases where
        onboarding is LATER would push those users into a later cohort."""
        self._stub({"aiCreatedAt": REAL, "createdAt": "2026-01-01T00:00:00Z",
                    "onboarding": {"shown": {"consumerAIOnboarding": "2026-08-01T00:00:00Z"}}})
        aic, _created, outcome, ob = backfill.fetch_aura("u1", STAMPS)
        self.assertEqual(outcome, "postgres")
        self.assertEqual(aic, REAL)
        self.assertEqual(ob, "2026-08-01T00:00:00Z")  # carried, but not used

    def test_stamped_aicreatedat_routes_to_fallback_with_onboarding(self):
        self._stub({"aiCreatedAt": CLUSTERED_NOON, "createdAt": "2025-12-01T00:00:00Z",
                    "onboarding": {"shown": {"consumerAIOnboarding": "2025-09-16T19:01:14Z"}}})
        aic, _created, outcome, ob = backfill.fetch_aura("u2", STAMPS)
        self.assertIsNone(aic)
        self.assertEqual(outcome, "fallback")
        self.assertEqual(ob, "2025-09-16T19:01:14Z")

    def test_sentinel_still_routes_to_fallback(self):
        self._stub({"aiCreatedAt": SENTINEL, "createdAt": "2026-02-16T17:43:16Z",
                    "onboarding": {"shown": {"consumerAIOnboarding": "2026-02-16T17:43:16Z"}}})
        _aic, _c, outcome, ob = backfill.fetch_aura("u3", STAMPS)
        self.assertEqual(outcome, "fallback")
        self.assertEqual(ob, "2026-02-16T17:43:16Z")

    def test_null_aicreatedat_routes_to_fallback(self):
        self._stub({"aiCreatedAt": None, "createdAt": "2025-09-16T19:01:14Z",
                    "onboarding": {"shown": {"consumerAIOnboarding": "2025-09-16T19:01:14Z"}}})
        _aic, _c, outcome, ob = backfill.fetch_aura("u4", STAMPS)
        self.assertEqual(outcome, "fallback")
        self.assertEqual(ob, "2025-09-16T19:01:14Z")

    def test_lone_noon_join_date_is_kept_as_postgres(self):
        """The negative, end to end: a genuine noon join date must survive the ladder."""
        self._stub({"aiCreatedAt": LONE_NOON, "createdAt": "2026-06-30T11:00:00Z",
                    "onboarding": {"shown": {"consumerAIOnboarding": "2026-06-30T12:00:00Z"}}})
        aic, _c, outcome, _ob = backfill.fetch_aura("u5", STAMPS)
        self.assertEqual(outcome, "postgres")
        self.assertEqual(aic, LONE_NOON)

    def test_transport_error_never_falls_back(self):
        self._stub(None, status=500)
        aic, _c, outcome, ob = backfill.fetch_aura("u6", STAMPS)
        self.assertEqual(outcome, "error")
        self.assertIsNone(aic)
        self.assertIsNone(ob)

    def test_arity_is_four_so_callers_cannot_silently_unpack_three(self):
        self._stub({"aiCreatedAt": REAL, "createdAt": None, "onboarding": {}})
        self.assertEqual(len(backfill.fetch_aura("u7", STAMPS)), 4)


class TestKnownBatchStamps(unittest.TestCase):
    def setUp(self):
        self._exists, self._query = backfill.bq.table_exists, backfill.bq.query

    def tearDown(self):
        backfill.bq.table_exists, backfill.bq.query = self._exists, self._query

    def test_always_includes_the_sentinel_even_with_no_table(self):
        backfill.bq.table_exists = lambda *_a, **_k: False
        self.assertEqual(backfill.known_batch_stamps(), {backfill.SENTINEL_PREFIX})

    def test_sentinel_survives_a_query_failure(self):
        """The sentinel is never STORED (the skill refuses it), so it can never be
        rediscovered from the table — it has to be unconditional."""
        backfill.bq.table_exists = lambda *_a, **_k: True

        def boom(*_a, **_k):
            raise backfill.BQError("permission denied")
        backfill.bq.query = boom
        self.assertEqual(backfill.known_batch_stamps(), {backfill.SENTINEL_PREFIX})

    def test_discovered_stamps_are_normalised(self):
        import datetime as dt
        backfill.bq.table_exists = lambda *_a, **_k: True
        backfill.bq.query = lambda *_a, **_k: [
            {"ai_created_at": dt.datetime(2025, 12, 8, 12, 0, tzinfo=dt.timezone.utc), "n": 189},
            {"ai_created_at": dt.datetime(2026, 1, 19, 12, 0, tzinfo=dt.timezone.utc), "n": 106},
        ]
        got = backfill.known_batch_stamps()
        self.assertIn("2025-12-08T12:00:00", got)
        self.assertIn("2026-01-19T12:00:00", got)
        self.assertIn(backfill.SENTINEL_PREFIX, got)

    def test_query_filters_on_noon_and_a_user_threshold(self):
        """Both halves of the shape must reach SQL. A query missing either one is the
        silent-failure mode this card exists to remove."""
        seen = {}
        backfill.bq.table_exists = lambda *_a, **_k: True

        def cap(sql, *_a, **_k):
            seen["sql"] = sql
            return []
        backfill.bq.query = cap
        backfill.known_batch_stamps()
        self.assertIn(backfill.BATCH_STAMP_TIME, seen["sql"])
        self.assertIn(str(backfill.BATCH_STAMP_MIN_USERS), seen["sql"])
        self.assertIn("COUNT(DISTINCT user_id)", seen["sql"])


class TestColdStartGuards(unittest.TestCase):
    """A brand-new upstream batch has no rows in the dimension, so `known_batch_stamps`
    is blind to it — and the run only fetches UIDs missing from the table, so its first
    wave would be stored as `source=postgres` and never retried. That is PROD-6109
    repeating for every new batch (found by Bugbot on the first cut of this change).

    Two independent guards close it, because each alone has a hole: the per-user rule
    needs an onboarding timestamp, and the in-run cluster needs several users at once.
    """

    def setUp(self):
        self._orig = backfill._get_user

    def tearDown(self):
        backfill._get_user = self._orig

    # --- per-user guard ---
    def test_noon_value_disagreeing_with_onboarding_is_a_stamp(self):
        self.assertTrue(backfill.stamp_by_onboarding(
            "2026-07-04T12:00:00.000Z", "2025-09-16T19:01:14Z"))

    def test_noon_value_agreeing_with_onboarding_is_kept(self):
        """The negative: a genuine noon join date agrees with onboarding on the day
        (97.3% of measured users) and must survive."""
        self.assertFalse(backfill.stamp_by_onboarding(
            "2026-06-30T12:00:00.000Z", "2026-06-30T12:00:00Z"))

    def test_rule_only_applies_at_noon(self):
        """Without the noon gate this would reclassify ordinary values whenever
        onboarding merely differs by a day."""
        self.assertFalse(backfill.stamp_by_onboarding(
            "2026-03-14T09:22:41.000Z", "2025-09-16T19:01:14Z"))

    def test_no_onboarding_means_no_verdict(self):
        for ob in (None, "", "   "):
            with self.subTest(ob=ob):
                self.assertFalse(backfill.stamp_by_onboarding("2026-07-04T12:00:00Z", ob))

    def test_unseen_stamp_routes_to_fallback_end_to_end(self):
        """The exact case Bugbot found: a noon stamp absent from `stamps` (never stored,
        so undiscoverable) must NOT be accepted as a real join date."""
        backfill._get_user = lambda base, uid: (200, {
            "aiCreatedAt": "2026-11-02T12:00:00.000Z",          # a batch nobody has seen
            "createdAt": "2026-10-30T08:00:00Z",
            "onboarding": {"shown": {"consumerAIOnboarding": "2026-10-30T08:04:11Z"}},
        }) if base == backfill.PROD_BASE else (404, None)
        aic, _c, outcome, ob = backfill.fetch_aura("u", {backfill.SENTINEL_PREFIX})
        self.assertEqual(outcome, "fallback")
        self.assertIsNone(aic)
        self.assertEqual(ob, "2026-10-30T08:04:11Z")

    # --- in-run cluster guard ---
    def _resolved(self, n, ts, source="postgres"):
        return {f"u{i}": (ts, None, source) for i in range(n)}

    def test_in_run_cluster_is_detected(self):
        r = self._resolved(backfill.BATCH_STAMP_MIN_USERS, "2026-11-02T12:00:00.000Z")
        self.assertEqual(backfill.fresh_batch_stamps(r), {"2026-11-02T12:00:00"})

    def test_below_threshold_is_not_a_cluster(self):
        """The negative that stops every lone noon join date being discarded."""
        r = self._resolved(backfill.BATCH_STAMP_MIN_USERS - 1, "2026-11-02T12:00:00.000Z")
        self.assertEqual(backfill.fresh_batch_stamps(r), set())

    def test_non_noon_values_never_cluster(self):
        r = self._resolved(50, "2026-11-02T09:15:00.000Z")
        self.assertEqual(backfill.fresh_batch_stamps(r), set())

    def test_proxy_resolved_rows_are_not_counted(self):
        """Only values that came from Postgres can be Postgres batch stamps."""
        r = self._resolved(50, "2026-11-02T12:00:00.000Z", source="first_ai_login")
        self.assertEqual(backfill.fresh_batch_stamps(r), set())

    # --- re-route keeps prod/dev provenance ---
    def test_reroute_keeps_dev_provenance(self):
        """A dev user re-routed by the in-run net must become `fallback_dev`. The proxy
        reads the PROD Mixpanel project and cannot answer for dev users at all, so
        collapsing to `fallback` reports them as `no_ai_activity` — a residue reason that
        is wrong about which environment was searched."""
        n = backfill.BATCH_STAMP_MIN_USERS
        ts = "2026-11-02T12:00:00.000Z"
        resolved = {f"p{i}": (ts, None, "postgres") for i in range(n)}
        resolved.update({f"d{i}": (ts, None, "postgres_dev") for i in range(n)})
        outcomes, need = {}, []
        fresh, moved = backfill.reroute_fresh_stamps(resolved, outcomes, need)
        self.assertEqual(fresh, {"2026-11-02T12:00:00"})
        self.assertEqual(len(moved), 2 * n)
        self.assertEqual(resolved, {})
        self.assertCountEqual(need, moved)
        for i in range(n):
            self.assertEqual(outcomes[f"p{i}"], "fallback")
            self.assertEqual(outcomes[f"d{i}"], "fallback_dev")

    def test_reroute_is_a_noop_without_a_cluster(self):
        resolved = {"u0": ("2026-11-02T12:00:00.000Z", None, "postgres")}
        outcomes, need = {}, []
        fresh, moved = backfill.reroute_fresh_stamps(resolved, outcomes, need)
        self.assertEqual((fresh, moved, outcomes, need), (set(), [], {}, []))
        self.assertEqual(len(resolved), 1)


if __name__ == "__main__":
    unittest.main()
