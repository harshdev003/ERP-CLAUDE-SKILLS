#!/usr/bin/env python3
"""Window-wide ledger reads for `data:report-aurie-conversations` (ENG-1113).

The defect this guards
----------------------
The report walked OpenAI's stored **chat-completions** list, which cannot
enumerate Responses-API traffic. When Aurie finished migrating to Responses on
2026-08-07 the report kept succeeding while seeing ~42% of calls: it printed
`insight-card 0` against a true 1,482, never tracked journal at all, and showed
a 48% week-over-week "collapse" that was entirely lost visibility. Cost, read
from the billed-truth Costs API, rose 17% over the same window.

Nothing errored. That is the whole problem — and it is why the assertions below
that matter most are about **coverage reporting** and **failure**, not about
happy-path counts.

Why these branches need injection
---------------------------------
Every defect worth catching here lives somewhere live data cannot go:

* a ledger that raises, which must **fail** rather than yield zero
  conversations — indistinguishable from a quiet week;
* a window starting before the ledger's recorded history;
* a call landing exactly on the window end.

Every read goes through one `getter` object (`rows` / `stats`; `llm_ledger` in
production, BigQuery `llm.llm_call` since ENG-1657), so stubbing that one
object reaches all of them with no credentials.

Stdlib `unittest` only.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from reader_harness import load_reader, script_paths  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
REPORT_SKILL = REPO / "plugins/data/skills/report-aurie-conversations/scripts"


def _load(name: str):
    """Load a script from the report skill with no sibling-cache leakage."""
    return load_reader(REPORT_SKILL / name, siblings=("ledger_reader", "llm_ledger", "bq"))


def row(rid, *, created, api="responses", prompter="coach-prompter",
        conversation="C1", user="u1", project="conversation"):
    """One ledger row, shaped like an `llm_ledger.rows` row."""
    return {
        "id": rid,
        "openaiId": f"resp_{rid}",
        "createdAt": created,
        "openaiAPI": api,
        "prompterName": prompter,
        "conversationId": conversation,
        "userId": user,
        "project": project,
    }


class FakeLedger:
    """Serves `rows` and `stats` from canned rows, like `llm_ledger`: `from` inclusive, `to` exclusive."""

    def __init__(self, rows, *, stats_calls=None, fail=False):
        self.rows_ = rows
        self.stats_calls = stats_calls
        self.fail = fail
        self.calls = []

    def _select(self, params):
        self.calls.append(dict(params))
        if self.fail:
            raise self.error("injected ledger failure")
        rows = [r for r in self.rows_ if r.get("project") == params.get("project")] \
            if params.get("project") else list(self.rows_)
        lo, hi = params.get("from"), params.get("to")
        return [r for r in rows if (not lo or r["createdAt"] >= lo) and (not hi or r["createdAt"] < hi)]

    def rows(self, params):
        return sorted(self._select(params), key=lambda r: r["createdAt"], reverse=True)

    def stats(self, params):
        agg: dict[str, int] = {}
        for r in self._select(params):
            agg[r["openaiAPI"]] = agg.get(r["openaiAPI"], 0) + 1
        if self.stats_calls is not None:
            agg = dict(self.stats_calls)
        return {k: {"calls": v} for k, v in agg.items()}


class LedgerWalkTest(unittest.TestCase):
    def setUp(self):
        self.mod = _load("ledger_reader.py")
        FakeLedger.error = self.mod.llm_ledger.LedgerError
        self.start, self.end = 1785000000, 1785600000

    def _ts(self, offset):
        return self.mod._iso(self.start + offset)

    def test_one_query_returns_the_whole_window(self):
        rows = [row(i, created=self._ts(i)) for i in range(2507)]
        fake = FakeLedger(rows)
        got = self.mod.walk_ledger(self.start, self.end, getter=fake)
        self.assertEqual(len({r["id"] for r in got}), 2507)
        self.assertEqual(len(fake.calls), 1)

    def test_the_window_end_is_exclusive_like_every_other_bound(self):
        """`[start, end)` — a call exactly on the end belongs to the next window.

        Everything else in this report treats the end as exclusive: `--until`
        says so, and the OpenAI Costs API's `end_time` behaves that way. A call
        landing exactly on the boundary would otherwise be counted in the
        activity numbers but not in the cost they are divided by.
        """
        boundary = row(1, created=self.mod._iso(self.end))
        inside = row(2, created=self.mod._iso(self.end - 5))
        fake = FakeLedger([boundary, inside])
        got = self.mod.walk_ledger(self.start, self.end, getter=fake)
        self.assertEqual({r["id"] for r in got}, {2})
        stats = self.mod.ledger_stats(self.start, self.end, getter=fake)
        self.assertEqual(self.mod.stats_calls(stats), 1)

    def test_ledger_failure_raises_rather_than_returning_empty(self):
        """A failed ledger read must not look like a week with no conversations."""
        fake = FakeLedger([row(1, created=self._ts(0))], fail=True)
        with self.assertRaises(self.mod.LedgerError):
            self.mod.walk_ledger(self.start, self.end, getter=fake)
        with self.assertRaises(self.mod.LedgerError):
            self.mod.ledger_stats(self.start, self.end, getter=fake)


class FilterAllowlistTest(unittest.TestCase):
    """An unrecognised filter must raise, not silently widen the query.

    The ledger endpoint drops params it does not recognise and answers with the
    UNFILTERED total. Measured on 2026-08-04→08-11:

        prompterName=coach-prompter  ->  44,549 / 61,562   (correct)
        prompter=coach-prompter      ->  45,356 / 191,591  (whole window)
        projekt=conversation         ->  45,356 / 191,591  (whole window)

    So a wrong-but-plausible name (`prompter` for `prompterName`) and an
    ordinary typo both return a number that looks entirely reasonable. This is
    the same silent-wrong-answer class ENG-1113 existed to fix, one layer down.
    """

    def setUp(self):
        self.mod = _load("ledger_reader.py")
        FakeLedger.error = self.mod.llm_ledger.LedgerError
        self.start, self.end = 1785000000, 1785600000

    def test_supported_filter_is_passed_through(self):
        rows = [row(1, created=self.mod._iso(self.start + 5))]
        fake = FakeLedger(rows)
        self.mod.walk_ledger(self.start, self.end, getter=fake,
                             project="conversation")
        self.assertEqual(fake.calls[0].get("project"), "conversation")

    def test_the_plausible_wrong_name_raises(self):
        """`prompter` is the trap: it looks right and returns the whole window."""
        fake = FakeLedger([row(1, created=self.mod._iso(self.start + 5))])
        for call in (
            lambda: self.mod.walk_ledger(self.start, self.end, getter=fake, prompter="coach-prompter"),
            lambda: self.mod.ledger_stats(self.start, self.end, getter=fake, prompter="coach-prompter"),
        ):
            with self.assertRaises(self.mod.LedgerError) as ctx:
                call()
            self.assertIn("prompter", str(ctx.exception))
            self.assertIn("prompterName", str(ctx.exception))

    def test_a_typo_raises_too(self):
        fake = FakeLedger([])
        with self.assertRaises(self.mod.LedgerError):
            self.mod.ledger_stats(self.start, self.end, getter=fake, projekt="conversation")

    def test_it_raises_before_issuing_any_request(self):
        """A partial walk then a raise would leave the caller guessing."""
        fake = FakeLedger([row(1, created=self.mod._iso(self.start + 5))])
        with self.assertRaises(self.mod.LedgerError):
            self.mod.walk_ledger(self.start, self.end, getter=fake, prompter="x")
        self.assertEqual(fake.calls, [])

    def test_the_allowlist_matches_the_endpoint(self):
        """Guards against the allowlist drifting from parseParameters.js."""
        self.assertEqual(
            self.mod.SUPPORTED_FILTERS,
            frozenset({"conversationId", "openaiAPI", "openaiId", "project",
                       "prompterName", "provider", "serviceTier", "transaction",
                       "userId"}),
        )


class CoverageTest(unittest.TestCase):
    """Coverage must be a share, never a boolean (the ENG-1114 lesson)."""

    def setUp(self):
        self.mod = _load("ledger_reader.py")

    def test_reports_per_api_shares_not_a_flag(self):
        rows = [row(1, created="a", api="responses"),
                row(2, created="b", api="chatCompletions")]
        stats = {"responses": {"calls": 4}, "chatCompletions": {"calls": 2}}
        cov = self.mod.coverage(rows, stats)
        self.assertEqual(cov["by_api"]["responses"]["share"], 0.25)
        self.assertEqual(cov["by_api"]["chatCompletions"]["share"], 0.5)
        self.assertFalse(cov["complete"])

    def test_a_surface_present_in_stats_but_absent_from_the_walk_is_visible(self):
        """The exact ENG-1113 shape: a whole API read as zero.

        A summary keyed only on what the walk returned would omit the row
        entirely, which is how 63,543 Responses calls went unmentioned rather
        than reported as missing.
        """
        rows = [row(1, created="a", api="chatCompletions")]
        stats = {"chatCompletions": {"calls": 1}, "responses": {"calls": 63543}}
        cov = self.mod.coverage(rows, stats)
        self.assertIn("responses", cov["by_api"])
        self.assertEqual(cov["by_api"]["responses"]["walked"], 0)
        self.assertEqual(cov["by_api"]["responses"]["share"], 0.0)
        self.assertFalse(cov["complete"])

    def test_complete_when_the_walk_reaches_the_oracle(self):
        rows = [row(1, created="a"), row(2, created="b")]
        cov = self.mod.coverage(rows, {"responses": {"calls": 2}})
        self.assertTrue(cov["complete"])
        self.assertEqual(cov["share"], 1.0)


class CoverageStartTest(unittest.TestCase):
    """A window reaching before the ledger's history must be detectable."""

    def setUp(self):
        self.mod = _load("ledger_reader.py")
        self.start, self.end = 1785000000, 1785000000 + 8 * 86400

    def test_returns_start_when_day_one_has_rows(self):
        rows = [row(1, created=self.mod._iso(self.start + 10))]
        self.assertEqual(
            self.mod.coverage_start(self.start, self.end, getter=FakeLedger(rows)),
            self.start,
        )

    def test_returns_none_when_the_window_is_entirely_uncovered(self):
        self.assertIsNone(
            self.mod.coverage_start(self.start, self.end, getter=FakeLedger([]))
        )

    # Deliberately NOT a whole number of days from `start`. A first row sitting
    # exactly on a bisection midpoint makes `lo` and `hi` converge to the same
    # value, so the bracket's two ends become indistinguishable and any
    # assertion about which one is returned passes either way. The first draft
    # of this suite used `start + 5 days` on an 8-day window and was silently
    # vacuous for precisely that reason.
    OFFSET = 5 * 86400 + 12_345

    def test_reports_an_upper_bound_when_coverage_begins_midway(self):
        """Partial coverage is the dangerous case — it still returns rows.

        The answer must be an **upper** bound on the first covered instant:
        `report.py` turns it into "re-run starting here", and the lower end of
        the bracket is by construction a point with no rows.
        """
        first = self.start + self.OFFSET
        rows = [row(i, created=self.mod._iso(first + i * 60)) for i in range(3)]
        got = self.mod.coverage_start(self.start, self.end, getter=FakeLedger(rows))
        self.assertIsNotNone(got)
        self.assertGreater(got, self.start)
        self.assertGreaterEqual(got, first)

    def test_the_reported_start_names_the_right_day(self):
        """The caller prints this as a date, so hour-level precision matters.

        A day-sized bracket can name the day before the data actually starts.
        """
        import datetime as dt
        first = self.start + self.OFFSET
        rows = [row(i, created=self.mod._iso(first + i * 60)) for i in range(3)]
        got = self.mod.coverage_start(self.start, self.end, getter=FakeLedger(rows))
        as_date = dt.datetime.fromtimestamp(got, dt.timezone.utc).date()
        true_date = dt.datetime.fromtimestamp(first, dt.timezone.utc).date()
        self.assertEqual(as_date, true_date)


class AggregateSemanticsTest(unittest.TestCase):
    """Dedup is per-metric. Getting it backwards is silent in both directions."""

    def setUp(self):
        self.report = _load("report.py")

    def test_one_thread_across_both_surfaces_is_one_conversation_but_two_calls(self):
        """The `aiResponseAPI` A/B put a single thread on both surfaces.

        Two rows, one `conversationId`, different object ids. Conversations and
        users must collapse to one; calls and turns must not. Dedup the calls
        and turn counts shrink; key the conversations on the object id and one
        thread counts twice. Both totals stay plausible either way.
        """
        rows = [
            row(1, created="2026-08-04T00:00:00.000Z", api="chatCompletions"),
            row(2, created="2026-08-04T00:10:00.000Z", api="responses"),
        ]
        users, convs, calls, span, turns, api_calls = self.report.aggregate_ledger(rows)
        self.assertEqual(len(convs["coach-prompter"]), 1)
        self.assertEqual(len(users["coach-prompter"]), 1)
        self.assertEqual(calls["coach-prompter"], 2)
        self.assertEqual(sum(turns.values()), 2)
        self.assertEqual(api_calls["coach-prompter"],
                         {"chatCompletions": 1, "responses": 1})

    def test_duration_span_covers_both_surfaces(self):
        """Collapsing cross-surface rows would shorten max(created) − min(created)."""
        rows = [
            row(1, created="2026-08-04T00:00:00.000Z", api="chatCompletions"),
            row(2, created="2026-08-04T00:30:00.000Z", api="responses"),
        ]
        *_, span, _turns, _api = self.report.aggregate_ledger(rows)
        (lo, hi), = span.values()
        self.assertEqual(hi - lo, 1800)

    def test_mixed_case_conversation_ids_fold_to_one_thread(self):
        """Aurie writes the UUID upper-case on voice paths, lower-case elsewhere.

        The same thread therefore arrives under both casings, and an exact key
        counts it twice. Measured at 32 of 587 threads (5.5%) on 2026-08-09.
        """
        rows = [
            row(1, created="2026-08-04T00:00:00.000Z", conversation="abc-DEF"),
            row(2, created="2026-08-04T00:05:00.000Z", conversation="ABC-def"),
        ]
        _users, _convs, _calls, span, turns, _api = self.report.aggregate_ledger(rows)
        self.assertEqual(len(span), 1)
        self.assertEqual(sum(turns.values()), 2)

    def test_per_prompter_conversations_use_the_same_key_space_as_spans(self):
        """§1's conversation count and §2's thread count must not disagree.

        The first cut folded casing for `span`/`turns` and for the voice set but
        left the per-prompter `convs` set on the raw id, so the same report said
        4,266 coach conversations in §1 and 4,008 in §2 — with nothing to
        indicate which was right. Partial normalisation is worse than none.
        """
        rows = [
            row(1, created="2026-08-04T00:00:00.000Z", conversation="abc-DEF"),
            row(2, created="2026-08-04T00:05:00.000Z", conversation="ABC-def"),
        ]
        _users, convs, _calls, span, _turns, _api = self.report.aggregate_ledger(rows)
        self.assertEqual(len(convs["coach-prompter"]), len(span))
        self.assertEqual(len(convs["coach-prompter"]), 1)

    def test_an_unknown_prompter_still_reaches_the_table(self):
        """The root cause: a hardcoded type list made journal invisible.

        A prompter the report has never heard of must appear on its own rather
        than be silently dropped.
        """
        types = self.report.report_types({"brand-new-prompter": 5})
        self.assertIn("brand-new-prompter", types)
        self.assertIn("journal-prompter", types)

    def test_known_but_idle_types_are_retained(self):
        """A type at zero must render as zero, not vanish — absence is a signal."""
        self.assertIn("companion-prompter", self.report.report_types({}))

    def test_every_transcript_prompter_is_a_known_type(self):
        """The rule "which prompters are user-visible turns" lives in two files.

        `responses_reader.CONVERSATION_PROMPTERS` and this report's type lists
        are different filenames, so no `diff -q` compares them. When
        `quick-processing-sentinel-prompter` joined the reader it was missing
        here too, rendering as "discovered — not a known type".
        """
        reader = load_reader(REPO / "plugins/data/skills/analyze-user-conversations"
                             "/scripts/responses_reader.py")
        known = set(self.report.CONVERSATION_TYPES) | set(self.report.SECONDARY_TYPES)
        self.assertEqual(set(reader.CONVERSATION_PROMPTERS) - known, set())


class WindowClampTest(unittest.TestCase):
    """A window ending in the future would make the coverage check cry wolf."""

    def setUp(self):
        self.report = _load("report.py")

    def test_future_end_is_clamped_to_now(self):
        """Unclamped, the population grows between the walk and the /stats read.

        That produced a phantom shortfall — and therefore an INCOMPLETE banner —
        on every window ending today, which is the common case.
        """
        import datetime as dt
        now = dt.datetime.now(tz=dt.timezone.utc)
        future = (now + dt.timedelta(days=3)).strftime("%Y-%m-%d")
        past = (now - dt.timedelta(days=2)).strftime("%Y-%m-%d")
        _s, e, label = self.report.resolve_window(None, past, future)
        self.assertLessEqual(e, int(now.timestamp()) + 2)
        self.assertIn("through", label)


if __name__ == "__main__":
    unittest.main()
