#!/usr/bin/env python3
"""Every reader of `aurie_user_ai_created` must resolve it to one row per user.

**Why this has to be a test.** The dimension is append-only with no unique key, so a
correction is an APPENDED row with a later `fetched_at` — that is how PROD-6110 restates
the 631 batch-stamped users. A reader that does not dedup then sees *two* rows for every
restated user and double-counts them in a cohort join. Nothing raises; the chart is just
wrong, in the direction of over-counting the corrected population.

`SKILL.md` says consumers dedup "defensively", and when audited all 8 did. But that is a
convention, and a convention is a description of the present, not a guarantee about the
next reader. An audit answers "is it true today"; this answers "is it still true".

**The check is by shape, in both directions.** Readers are DISCOVERED by sweeping the
scripts for ones that query the table, then compared against an explicitly declared list:
a discovered reader that is not declared fails (a new consumer must be classified), and a
declared one the sweep can no longer see fails too (so a reader that silently stops
matching is caught rather than dropping out of coverage). The declared list is deliberately
not derived from the sweep — deriving it makes the check vacuous.

**A near-miss worth recording.** The first audit for PROD-6110 grepped for `QUALIFY` and
reported 0 of 8 readers deduping. They all do — via an explicit
`ROW_NUMBER() … WHERE rn = 1` subquery, which is semantically identical. Probing for the
syntax named in the docs instead of the behaviour is the same error as grepping for a
filename instead of a rule. So this test accepts EITHER spelling.

Stdlib unittest, no network.
"""
from __future__ import annotations

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = REPO / "plugins"
TABLE = "aurie_user_ai_created"

# Readers that must dedup: they SELECT columns of the dimension per user.
DEDUPING_READERS = {
    "eng/skills/chart-aurie-cohort-cost/scripts/cohort.py",
    "eng/skills/chart-aurie-cost-curve/scripts/curve.py",
    "eng/skills/chart-aurie-cost-mix/scripts/mix.py",
    "eng/skills/chart-aurie-cumulative-cost/scripts/cumulative.py",
    "eng/skills/chart-aurie-free-user-cost/scripts/free_cost.py",
    "eng/skills/chart-aurie-lifetime-cost/scripts/lifetime.py",
    "eng/skills/chart-aurie-retention/scripts/retention.py",
    "eng/skills/chart-aurie-tenure-cost/scripts/tenure.py",
    "eng/skills/backfill-aurie-user-ai-created/scripts/restate_stamps.py",
}

# Exempt, each with the reason it cannot be affected by a duplicate row.
EXEMPT = {
    # Reads only `SELECT DISTINCT user_id` — presence, never a value, so a second row
    # for the same user changes nothing it computes.
    "eng/skills/backfill-aurie-user-ai-created/scripts/backfill.py":
        "membership only (SELECT DISTINCT user_id)",
}

# Either spelling of "latest row per user wins".
_QUALIFY = re.compile(
    r"QUALIFY\s+ROW_NUMBER\(\)\s*OVER\s*\(\s*PARTITION BY user_id\s+ORDER BY fetched_at DESC",
    re.I | re.S)
_SUBQUERY = re.compile(
    r"ROW_NUMBER\(\)\s*OVER\s*\(\s*PARTITION BY user_id\s+ORDER BY fetched_at DESC\s*\)"
    r".{0,400}?\brn\s*=\s*1", re.I | re.S)


def _dedups(text: str) -> bool:
    return bool(_QUALIFY.search(text) or _SUBQUERY.search(text))


def _discover() -> set:
    """Scripts that query the dimension (mention the table AND issue SQL against it)."""
    found = set()
    for p in PLUGINS.rglob("*.py"):
        t = p.read_text(encoding="utf-8", errors="replace")
        if TABLE in t and re.search(r"\bFROM\b|\bJOIN\b", t, re.I):
            found.add(str(p.relative_to(PLUGINS)))
    return found


class TestReaderCoverage(unittest.TestCase):
    def test_every_discovered_reader_is_declared(self):
        """A new consumer must be classified, not silently uncovered."""
        undeclared = _discover() - DEDUPING_READERS - set(EXEMPT)
        self.assertEqual(undeclared, set(),
                         f"new reader(s) of {TABLE} — add to DEDUPING_READERS, or to "
                         f"EXEMPT with the reason a duplicate row cannot affect them: "
                         f"{sorted(undeclared)}")

    def test_every_declared_reader_is_still_discovered(self):
        """Catches a reader that stops matching the sweep and would drop out of cover."""
        missing = (DEDUPING_READERS | set(EXEMPT)) - _discover()
        self.assertEqual(missing, set(),
                         f"declared reader(s) no longer found by the sweep: {sorted(missing)}")


class TestDedup(unittest.TestCase):
    def test_all_readers_dedup_by_latest_fetched_at(self):
        offenders = []
        for rel in sorted(DEDUPING_READERS):
            t = (PLUGINS / rel).read_text(encoding="utf-8", errors="replace")
            if not _dedups(t):
                offenders.append(rel)
        self.assertEqual(offenders, [],
                         "reader(s) missing the latest-fetched_at dedup; an appended "
                         "correction would double-count those users: " + str(offenders))

    def test_exempt_readers_state_a_reason(self):
        for rel, why in EXEMPT.items():
            self.assertTrue(why and len(why) > 10, f"{rel} exempt with no real reason")

    def test_the_matcher_rejects_a_reader_with_no_dedup(self):
        """Negative: without this the matcher could accept anything and pass vacuously."""
        self.assertFalse(_dedups("SELECT user_id, ai_created_at FROM `x.y.z`"))

    def test_the_matcher_accepts_both_spellings(self):
        self.assertTrue(_dedups(
            "QUALIFY ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY fetched_at DESC)=1"))
        self.assertTrue(_dedups(
            "SELECT user_id FROM ( SELECT user_id, "
            "ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY fetched_at DESC) AS rn "
            "FROM `t` ) WHERE rn = 1"))

    def test_the_matcher_rejects_a_dedup_on_the_wrong_key(self):
        """Partitioning by anything but user_id, or ordering by anything but fetched_at,
        does not make the latest row per user win."""
        self.assertFalse(_dedups(
            "QUALIFY ROW_NUMBER() OVER (PARTITION BY date ORDER BY fetched_at DESC)=1"))
        self.assertFalse(_dedups(
            "QUALIFY ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY ai_created_at DESC)=1"))


class TestRestatementSafety(unittest.TestCase):
    """Two properties of `restate_stamps.py` whose breakage is silent and permanent.

    A correction is written ONCE — the corrected row is by construction no longer a batch
    stamp, so the pass never revisits that user. Anything wrong at write time is therefore
    cached forever, which is what makes both of these worth pinning rather than reviewing.
    """

    def setUp(self):
        import os as _os
        self._tok = _os.environ.get("AURA_API_TOKEN")
        _os.environ["AURA_API_TOKEN"] = "test-token-not-real"
        import importlib.util
        SCRIPTS = REPO / "plugins/eng/skills/backfill-aurie-user-ai-created/scripts"
        import sys
        sys.path.insert(0, str(SCRIPTS))
        spec = importlib.util.spec_from_file_location("restate_ut", SCRIPTS / "restate_stamps.py")
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.bf = self.mod.backfill
        self._get = self.bf._get_user
        self._fb = self.bf.fallback_first_ai

    def tearDown(self):
        import os as _os
        if self._tok is None:
            _os.environ.pop("AURA_API_TOKEN", None)
        else:
            _os.environ["AURA_API_TOKEN"] = self._tok
        self.bf._get_user = self._get
        self.bf.fallback_first_ai = self._fb

    def test_transient_failure_never_falls_through_to_the_proxy(self):
        """A 5xx is not evidence the user has no onboarding timestamp. Falling through
        would cache a weaker proxy date over an available one, permanently."""
        self.bf._get_user = lambda base, uid: (503, None)
        called = []
        self.bf.fallback_first_ai = lambda uids, **k: (called.append(list(uids)) or ({}, set()))
        resolved, unresolved, transient = self.mod.resolve(["u1", "u2"], 2)
        self.assertEqual(resolved, {})
        self.assertCountEqual(transient, ["u1", "u2"])
        self.assertEqual(unresolved, [])
        self.assertEqual(called, [], "the proxy must not be consulted for a transient failure")

    def test_a_prod_transient_does_not_even_try_dev(self):
        """Mirrors `fetch_aura`: only a 404 routes to dev. A 5xx on prod says nothing
        about the user, so consulting dev (and then the proxy) would be acting on a
        failure as if it were a verdict.

        Pinned separately because a stub that fails on BOTH hosts cannot distinguish
        this: the dev-side guard catches that case regardless, so removing the prod-side
        guard is a no-op against it. Found by a mutation that failed to fail.
        """
        hosts = []

        def fake(base, uid):
            hosts.append(base)
            return (503, None)
        self.bf._get_user = fake
        self.bf.fallback_first_ai = lambda uids, **k: ({}, set())
        resolved, unresolved, transient = self.mod.resolve(["u1"], 1)
        self.assertEqual(transient, ["u1"])
        self.assertEqual(resolved, {})
        self.assertEqual(hosts, [self.bf.PROD_BASE],
                         "a prod-side transient must not fall through to the dev host")

    def test_genuine_404_on_both_still_reaches_the_proxy(self):
        """The negative that stops the guard degenerating into 'never use the proxy'."""
        self.bf._get_user = lambda base, uid: (404, None)
        self.bf.fallback_first_ai = lambda uids, **k: ({"u1": "2025-09-16T19:01:14Z"}, set(uids))
        resolved, unresolved, transient = self.mod.resolve(["u1"], 1)
        self.assertEqual(transient, [])
        self.assertEqual(resolved, {"u1": "2025-09-16T19:01:14Z"})

    def test_proxy_chunk_failure_is_transient_not_unresolved(self):
        """`fallback_first_ai` returns (map, answered). A UID absent from `answered` was
        never asked — its chunk or the table discovery failed. Calling that "unresolved"
        reports a BigQuery blip as a stable user left untouched, and a one-shot --confirm
        would finish while their winning row is still a batch stamp."""
        self.bf._get_user = lambda base, uid: (404, None)
        # u1 answered with no event (genuinely nothing); u2 never asked (chunk failed).
        self.bf.fallback_first_ai = lambda uids, **k: ({}, {"u1"})
        resolved, unresolved, transient = self.mod.resolve(["u1", "u2"], 1)
        self.assertEqual(resolved, {})
        self.assertEqual(unresolved, ["u1"], "u1 was asked and had no event — stable")
        self.assertEqual(transient, ["u2"], "u2 was never asked — retry next run")

    def test_a_real_200_is_preferred_over_the_proxy(self):
        self.bf._get_user = lambda base, uid: (200, {
            "onboarding": {"shown": {"consumerAIOnboarding": "2026-02-12T21:52:06Z"}}})
        self.bf.fallback_first_ai = lambda uids, **k: ({"u1": "1999-01-01T00:00:00Z"}, set(uids))
        resolved, _u, _t = self.mod.resolve(["u1"], 1)
        self.assertEqual(resolved["u1"], "2026-02-12T21:52:06Z")

    def test_degraded_stamp_discovery_is_not_a_clean_run(self):
        """`known_batch_stamps` degrades to the sentinel alone on a BQError. For this
        pass that is indistinguishable from success — the sentinel is never stored, so
        nothing is selected and it would print 'nothing to restate' and exit 0 while
        leaving every wrong row in place. Under --confirm that is a silently skipped
        correction."""
        self.bf.known_batch_stamps = lambda: {self.bf.SENTINEL_PREFIX}
        self.mod.bq.table_exists = lambda *a, **k: True
        self.mod.bq.query = lambda *a, **k: [{"n": 18931}]
        import sys as _s
        argv = _s.argv
        _s.argv = ["restate_stamps.py"]
        try:
            rc = self.mod.main()
        finally:
            _s.argv = argv
        self.assertEqual(rc, 1, "a failed discovery must not exit 0")

    def test_genuinely_empty_table_is_not_treated_as_a_failure(self):
        """The negative that stops the guard firing on a legitimately empty store."""
        self.bf.known_batch_stamps = lambda: {self.bf.SENTINEL_PREFIX}
        self.mod.bq.table_exists = lambda *a, **k: True
        # Route by SQL: the row-count probe answers 0, every other query answers empty.
        self.mod.bq.query = lambda sql, *a, **k: ([{"n": 0}] if "COUNT(*)" in sql else [])
        import sys as _s
        argv = _s.argv
        _s.argv = ["restate_stamps.py"]
        try:
            rc = self.mod.main()
        finally:
            _s.argv = argv
        self.assertEqual(rc, 0)

    def test_selection_uses_the_known_stamp_set(self):
        """Selecting by a re-cluster of WINNING rows loses stragglers after a partial
        write: the table is append-only so the historical cluster holds, but the winning
        one shrinks below the threshold and those users stop being recognised at all."""
        seen = {}
        self.mod.bq.query = lambda sql, *a, **k: (seen.__setitem__("sql", sql) or [])
        self.mod.stamped_rows("p.d.t", {"2025-12-08T12:00:00", "2026-01-19T12:00:00"})
        self.assertIn("2025-12-08 12:00:00", seen["sql"])
        self.assertIn("2026-01-19 12:00:00", seen["sql"])
        self.assertNotIn("COUNT(DISTINCT user_id)", seen["sql"],
                         "must not re-cluster the winning rows")
        self.assertIn("ORDER BY fetched_at DESC", seen["sql"])

    def test_empty_stamp_set_selects_nothing(self):
        self.mod.bq.query = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("must not query with an empty stamp set"))
        self.assertEqual(self.mod.stamped_rows("p.d.t", set()), {})


if __name__ == "__main__":
    unittest.main()
