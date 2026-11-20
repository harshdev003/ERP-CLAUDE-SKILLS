"""A failed vendor read must never render as $0.00 (ENG-1214).

`eng:report-aurie-daily-costs` walked the forwarded LiveKit logs once per day.
Each walk is ~20 Cloud Logging read requests against a 60/min project cap, so a
week-long window exhausted the quota partway through and every remaining day's
LiveKit column rendered `$0.00` — summed into the Voice and Combined totals as a
zero. On a last-7d window on 2026-08-31 that understated LiveKit by $34.84 with
nothing in the table looking wrong, because Deepgram and LiveKit are
LiveKit-path-only and a genuine $0 day is legitimate and common.

The load-bearing assertions here are the NEGATIVE ones. Any implementation that
blanks every zero passes the "a failed read shows —" tests; only these fail it:

  * a quiet day (real $0, no note) must still render $0.00, not "—"
  * a fully-read window must carry no ">=" marker and no incomplete callout
  * a quota rejection must RAISE rather than return a short entry list, because
    a truncated walk yields fewer minutes and looks exactly like a quiet day

Network is stubbed at each module's single chokepoint; no API keys, no calls.

Both modules under test hard-require `google-cloud-logging` at import and
`sys.exit(2)` without it, and the suite runs with no third-party packages at all,
so every load here goes through `_stubbed_google` from `test_el_caller_mapping`.
Reusing that helper rather than declaring a second stub keeps one list of
`google.*` names in the suite; doing it explicitly in this module — rather than
leaning on an alphabetically-earlier module having already cached a stubbed
`livekit_minutes` — is what makes these tests independent of discovery order.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types
import unittest

# Same two lines the other cross-module test importers use: `unittest discover`
# puts `tests/` on the path, but `-m unittest tests.<mod>` does not.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_el_caller_mapping import _stubbed_google  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
REPORT = REPO / "plugins/eng/skills/report-aurie-daily-costs/scripts/report.py"
MINUTES = REPO / "plugins/eng/skills/store-aurie-user-daily-cost/scripts/livekit_minutes.py"


def _load(path: pathlib.Path, name: str):
    """Import a skill script under stubbed `google.*`, without running its CLI."""
    with _stubbed_google():
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


R = _load(REPORT, "aurie_daily_report")


# ---------------------------------------------------------------------------
# A fake of the sibling store skill's vendor helpers. Their real contract is the
# thing this report depends on: a note is returned ONLY on a genuine read
# failure, never for a day that legitimately had no usage.
# ---------------------------------------------------------------------------
class FakeStore:
    def __init__(self, cartesia=(0.0, ""), deepgram=(0.0, ""), livekit=(0.0, ""),
                 inworld=(0.0, "")):
        self._ct, self._dg, self._lk, self._iw = cartesia, deepgram, livekit, inworld
        self.livekit_calls = []

    def cartesia_day_total(self, day, rate):
        return self._ct

    def inworld_day_total(self, day, lk_meta, rate):
        return self._iw

    def deepgram_day_total(self, day, rate):
        return self._dg

    def livekit_costs(self, day, rate):
        self.livekit_calls.append(day)
        usd, note = self._lk
        return {}, 0.0, usd, note, {}

    def __getattr__(self, name):
        # The pure booking helpers (STT transport split, Inference pricing, the
        # unexplained-zero check) come from the real store: faking them would test
        # the fake, not the rules the report shares with the store.
        return getattr(REAL_STORE, name)


REAL_STORE = R._store


class VendorNoteMeansUnknown(unittest.TestCase):
    """A vendor note makes that day UNKNOWN; its absence keeps a real zero."""

    def _fetch(self, store, **kw):
        orig = R._store
        R._store = store
        try:
            return R.fetch_livekit_path_daily(
                _date("2026-08-28"), _date("2026-08-28"),
                {"cartesia": 45.0, "deepgram": 0.348, "livekit": 0.01},
                bq_project="p", bq_dataset="d", use_store=False, **kw)
        finally:
            R._store = orig

    def test_failed_livekit_read_is_unknown_not_zero(self):
        out, notes = self._fetch(FakeStore(
            livekit=(0.0, "livekit_pending: log query failed (ResourceExhausted)")))
        self.assertIs(out["2026-08-28"]["livekit"], R.UNKNOWN)
        self.assertTrue(any("livekit_pending" in n for n in notes))

    def test_unreadable_inworld_is_unknown_not_zero(self):
        out, notes = self._fetch(FakeStore(
            inworld=(0.0, "inworld_pending: INWORLD_API_KEY not set and no LiveKit log data")))
        self.assertIs(out["2026-08-28"]["inworld"], R.UNKNOWN)
        self.assertTrue(any("inworld_pending" in n for n in notes))

    def test_derived_inworld_is_a_figure_not_unknown(self):
        # The agent-log fallback is a measurement with a caveat, not a failed read.
        out, notes = self._fetch(FakeStore(
            inworld=(1.25, "inworld_derived: usage API failed (HTTPError)")))
        self.assertEqual(out["2026-08-28"]["inworld"], 1.25)
        self.assertTrue(any("inworld_derived" in n for n in notes))

    def test_quiet_day_stays_a_real_zero(self):
        """NEGATIVE CASE: $0 with no note is a measurement, and must survive."""
        out, notes = self._fetch(FakeStore())
        for vendor in ("cartesia", "inworld", "deepgram", "livekit"):
            self.assertEqual(out["2026-08-28"][vendor], 0.0,
                             f"{vendor}: a quiet day must stay $0, not become UNKNOWN")
        self.assertEqual(notes, [])

    def test_missing_key_makes_that_vendor_unknown(self):
        out, _ = self._fetch(FakeStore(
            cartesia=(0.0, "cartesia_pending: CARTESIA_ADMIN_API_KEY not set")))
        self.assertIs(out["2026-08-28"]["cartesia"], R.UNKNOWN)
        # ...and does not contaminate the vendors that DID read.
        self.assertEqual(out["2026-08-28"]["deepgram"], 0.0)

    def test_no_store_at_all_is_unknown_across_the_board(self):
        orig = R._store
        R._store = None
        try:
            out, notes = R.fetch_livekit_path_daily(
                _date("2026-08-28"), _date("2026-08-29"),
                {"cartesia": 1.0, "deepgram": 1.0, "livekit": 1.0},
                bq_project="p", bq_dataset="d", use_store=False)
        finally:
            R._store = orig
        self.assertEqual(sorted(out), ["2026-08-28", "2026-08-29"])
        for day in out.values():
            self.assertTrue(all(v is R.UNKNOWN for v in day.values()))
        self.assertTrue(notes)


def _date(s):
    import datetime as dt
    return dt.date.fromisoformat(s)


class TotalsExcludeUnread(unittest.TestCase):
    """A total may be a floor, but it may never be silently short."""

    def test_known_sums_and_counts_the_gaps(self):
        self.assertEqual(R._known([1.0, 2.0, 3.0]), (6.0, 0))
        self.assertEqual(R._known([1.0, R.UNKNOWN, 3.0]), (4.0, 1))
        self.assertEqual(R._known([R.UNKNOWN, R.UNKNOWN]), (0, 2))

    def test_r2_preserves_unknown(self):
        self.assertIs(R._r2(R.UNKNOWN), R.UNKNOWN)
        self.assertEqual(R._r2(1.239), 1.24)


class RenderMarksUnread(unittest.TestCase):
    """The table itself must say which figures are lower bounds."""

    @staticmethod
    def _payload(*, cartesia, deepgram, livekit, incomplete, unread):
        voice, missing = R._known([5.0, cartesia, deepgram, livekit])
        return {
            "label": "test", "from": "2026-08-28", "to": "2026-08-28",
            "rows": [{
                "date": "2026-08-28", "weekday": "Fri", "openai_usd": 100.0,
                "elevenlabs_usd": 5.0, "elevenlabs_agent_usd": 5.0,
                "elevenlabs_intelligence_usd": 0.0, "elevenlabs_livekit_usd": 0.0,
                "cartesia_usd": cartesia, "deepgram_usd": deepgram,
                "livekit_usd": livekit, "voice_usd": round(voice, 2),
                "combined_usd": round(100.0 + voice, 2),
                "voice_incomplete": incomplete, "unread_vendors": [], "partial": False,
            }],
            "total_openai_usd": 100.0, "total_elevenlabs_usd": 5.0,
            "total_elevenlabs_agent_usd": 5.0, "total_elevenlabs_intelligence_usd": 0.0,
            "total_elevenlabs_livekit_usd": 0.0,
            "total_cartesia_usd": 0.0, "total_deepgram_usd": 0.0, "total_livekit_usd": 0.0,
            "total_voice_usd": round(voice, 2), "total_combined_usd": round(100.0 + voice, 2),
            "voice_incomplete": incomplete, "unread_vendor_days": unread,
            "vendor_rates": {}, "vendor_notes": [], "spike_notes": [],
            "elevenlabs_subscription": None,
        }

    @staticmethod
    def _cells(md, prefix):
        """Table cells of the row starting with `prefix`.

        Scoped to the row on purpose: UNREAD_CELL is an em-dash, which also
        appears in the report's title and prose, so a whole-document
        `assertNotIn` would match text that has nothing to do with a vendor read.
        """
        row = next(l for l in md.splitlines() if l.startswith(prefix))
        return [c.strip() for c in row.split("|")[1:-1]]

    def test_unread_cell_is_not_a_dollar_zero(self):
        md = R.render_md(self._payload(
            cartesia=0.0, deepgram=0.0, livekit=R.UNKNOWN, incomplete=True,
            unread={"cartesia": 0, "deepgram": 0, "livekit": 1}))
        cells = self._cells(md, "| 2026-08-28")
        self.assertIn(R.UNREAD_CELL, cells, f"no unread marker in {cells!r}")
        self.assertGreaterEqual(md.count("≥"), 1, "lower bounds not marked")
        self.assertIn("Incomplete voice total", md)
        self.assertIn("NOT a $0 day", md)

    def test_fully_read_window_carries_no_bound_marker(self):
        """NEGATIVE CASE: a clean run must not be decorated as partial."""
        md = R.render_md(self._payload(
            cartesia=0.0, deepgram=6.03, livekit=11.2, incomplete=False,
            unread={"cartesia": 0, "deepgram": 0, "livekit": 0}))
        self.assertNotIn("≥", md)
        self.assertNotIn("Incomplete voice total", md)
        for prefix in ("| 2026-08-28", "| **Total**"):
            cells = self._cells(md, prefix)
            self.assertNotIn(R.UNREAD_CELL, cells,
                             f"clean run marked a cell unread in {prefix}: {cells!r}")
        # A real zero is still printed as a number.
        self.assertIn("$0.00", self._cells(md, "| 2026-08-28"))

    def test_column_total_is_marked_when_any_of_its_days_went_unread(self):
        md = R.render_md(self._payload(
            cartesia=0.0, deepgram=0.0, livekit=R.UNKNOWN, incomplete=True,
            unread={"cartesia": 0, "deepgram": 0, "livekit": 2}))
        total = next(l for l in md.splitlines() if l.startswith("| **Total**"))
        self.assertIn("≥", total)
        self.assertIn("livekit (2 days)", md)


class StoreReadReplacesPerDayWalks(unittest.TestCase):
    """The BigQuery cost store is what keeps the read count off the quota."""

    def setUp(self):
        self._bq, self._store = R._bq, R._store

    def tearDown(self):
        R._bq, R._store = self._bq, self._store

    def _fake_bq(self, rows=None, raises=None):
        mod = types.SimpleNamespace(PROJECT="p", DATASET="d")

        def query(sql, project, params=None):
            if raises:
                raise raises
            return rows or []
        mod.query = query
        return mod

    def test_stored_days_are_not_walked(self):
        R._bq = self._fake_bq(rows=[
            {"date": "2026-08-28", "livekit_usd": 0.0, "platform_usd": 9.86,
             "livekit_minutes": 986.0},
            {"date": "2026-08-29", "livekit_usd": 0.0, "platform_usd": 14.44,
             "livekit_minutes": 1444.0},
        ])
        store = FakeStore()
        R._store = store
        out, notes = R.fetch_livekit_path_daily(
            _date("2026-08-28"), _date("2026-08-30"),
            {"cartesia": 1.0, "deepgram": 1.0, "livekit": 0.01},
            bq_project="p", bq_dataset="d")
        # Only the day the store didn't have costs a live log walk.
        self.assertEqual(store.livekit_calls, ["2026-08-30"])
        # The dollars booked for each stored day pass through unchanged.
        self.assertAlmostEqual(out["2026-08-28"]["livekit"], 9.86, places=4)
        self.assertAlmostEqual(out["2026-08-29"]["livekit"], 14.44, places=4)
        self.assertTrue(any("livekit_source" in n for n in notes))

    def test_a_stored_day_is_priced_from_what_was_booked(self):
        """SUPERSEDES test_rate_flag_is_applied_to_stored_minutes (PROD-5998).

        That test asserted `--livekit-rate-per-min` re-priced a stored day's recovered
        minutes. Correct while LiveKit cost was `minutes x rate`; wrong now that it is a
        flat subscription plus overage — the flag sets the OVERAGE rate, and re-pricing
        every minute with it charges for minutes inside the allotment that are free.

        The property that replaces it is a comparison, not an arithmetic identity: a
        stored day and a live-fallback day in the same window must be on the SAME scale.
        Otherwise today (priced from the invoice) looks several times more expensive than
        a stored day with identical traffic, and every number still looks well-formed.
        """
        R._bq = self._fake_bq(rows=[{"date": "2026-08-28", "livekit_usd": 0.0,
                                     "platform_usd": 16.13, "livekit_minutes": 1000.0}])
        R._store = FakeStore()
        out, _ = R.fetch_livekit_path_daily(
            _date("2026-08-28"), _date("2026-08-28"),
            {"cartesia": 1.0, "deepgram": 1.0, "livekit": 0.02},
            bq_project="p", bq_dataset="d")
        # The booked dollars pass through: overage ($0, inside the allotment) + the
        # day's subscription accrual. A non-default rate flag must NOT inflate it.
        self.assertAlmostEqual(out["2026-08-28"]["livekit"], 16.13, places=4)

    def test_a_stored_quiet_day_stays_zero_and_is_not_walked(self):
        """A recorded $0 LiveKit day is a measurement, not a gap.

        Walking it would spend Cloud Logging quota the store already paid for, and
        a failed walk would promote a known $0 to an unread `—`. 47 of the 91
        stored days are exactly this shape, so this is the common case rather than
        an edge one.
        """
        R._bq = self._fake_bq(rows=[
            {"date": "2026-08-28", "livekit_usd": 0.0},
            {"date": "2026-08-29", "livekit_usd": None},   # NULL reads as zero
        ])
        store = FakeStore(livekit=(0.0, "livekit_pending: should never be called"))
        R._store = store
        out, _ = R.fetch_livekit_path_daily(
            _date("2026-08-28"), _date("2026-08-29"),
            {"cartesia": 1.0, "deepgram": 1.0, "livekit": 0.01},
            bq_project="p", bq_dataset="d")
        self.assertEqual(store.livekit_calls, [],
                         "a stored quiet day must not trigger a live log walk")
        for day in ("2026-08-28", "2026-08-29"):
            self.assertEqual(out[day]["livekit"], 0.0,
                             f"{day}: stored $0 must stay $0, not become UNKNOWN")

    def test_unstored_days_still_fall_back_to_a_walk(self):
        """Absent from the store is the only thing that means 'walk it'."""
        R._bq = self._fake_bq(rows=[{"date": "2026-08-28", "livekit_usd": 9.86}])
        store = FakeStore(livekit=(4.44, ""))
        R._store = store
        out, _ = R.fetch_livekit_path_daily(
            _date("2026-08-28"), _date("2026-08-29"),
            {"cartesia": 1.0, "deepgram": 1.0, "livekit": 0.01},
            bq_project="p", bq_dataset="d")
        self.assertEqual(store.livekit_calls, ["2026-08-29"])
        self.assertEqual(out["2026-08-29"]["livekit"], 4.44)

    def test_recovery_query_shape(self):
        """Two properties of the SQL that no behavioural assertion can reach.

        The fake transport ignores the SQL it is handed, so a wrong numerator or a
        reinstated join produces identical fake results — but both are silent,
        plausible-looking errors in production, so they are asserted on the query
        text.

        **The numerator is `voice_livekit` alone.** The store divides its LiveKit
        rate into that column only. Widening it to the transport + Deepgram +
        Cartesia basis (which is what `aurie_daily_unit_costs`' blended rate is
        built from, and what the first implementation here used) makes recovered
        minutes ~2x high and the LiveKit column ~2x too big — the same trap
        eng:report-livekit-daily-cost documents on its own inversion.

        **The unit-cost table stays out.** Joining it is the intuitive recovery,
        but it carried a rate for 41 of 91 stored days, so 50 stored days fell
        through to a live walk — reinstating the quota pressure on any window past
        about a week — and its blended, 4-dp-rounded rate is ~200x less accurate.
        """
        captured = []
        mod = types.SimpleNamespace(PROJECT="p", DATASET="d")

        def query(sql, project, params=None):
            captured.append(sql)
            return []
        mod.query = query
        R._bq, R._store = mod, FakeStore()
        R.fetch_livekit_path_daily(
            _date("2026-08-28"), _date("2026-08-28"),
            {"cartesia": 1.0, "deepgram": 1.0, "livekit": 0.01},
            bq_project="p", bq_dataset="d")
        # The Deepgram store read is its own query, so it can never widen this one's
        # numerator. Pick the LiveKit query by the column only it selects.
        lk = [q for q in captured if "voice_livekit_platform" in q]
        self.assertEqual(len(lk), 1, "exactly one LiveKit recovery query")
        sql = lk[0]
        self.assertNotIn("aurie_daily_unit_costs", sql)
        self.assertNotIn("cost_per_voice_min_livekit", sql)
        self.assertIn("voice_livekit", sql)
        for other in ("voice_deepgram", "voice_cartesia", "voice_11labs"):
            self.assertNotIn(other, sql,
                             f"{other} in the minutes numerator inflates recovered "
                             f"minutes; the store divides its rate into voice_livekit only")
        # And it must read the BASE table, not the env-filtered _prod view: this
        # report's totals reconcile to the authoritative daily vendor bill.
        self.assertIn("aurie_per_user_daily_cost`", sql)
        self.assertNotIn("_prod`", sql)

    def test_bigquery_failure_degrades_to_the_live_walk(self):
        R._bq = self._fake_bq(raises=RuntimeError("boom"))  # noqa: F841
        store = FakeStore(livekit=(1.11, ""))
        R._store = store
        out, notes = R.fetch_livekit_path_daily(
            _date("2026-08-28"), _date("2026-08-28"),
            {"cartesia": 1.0, "deepgram": 1.0, "livekit": 0.01},
            bq_project="p", bq_dataset="d")
        self.assertEqual(store.livekit_calls, ["2026-08-28"])
        self.assertEqual(out["2026-08-28"]["livekit"], 1.11)
        self.assertTrue(any("livekit_store_unavailable" in n for n in notes))

    def test_bigquery_failure_plus_walk_failure_is_unknown(self):
        """Both sources down is the case that used to print $0.00."""
        R._bq = self._fake_bq(raises=RuntimeError("boom"))
        R._store = FakeStore(livekit=(0.0, "livekit_pending: log query failed"))
        out, _ = R.fetch_livekit_path_daily(
            _date("2026-08-28"), _date("2026-08-28"),
            {"cartesia": 1.0, "deepgram": 1.0, "livekit": 0.01},
            bq_project="p", bq_dataset="d")
        self.assertIs(out["2026-08-28"]["livekit"], R.UNKNOWN)


# ---------------------------------------------------------------------------
# The live log walk: quota handling
# ---------------------------------------------------------------------------
class _FakeEntry:
    def __init__(self, i):
        self._i = i

    def to_api_repr(self):
        return {"timestamp": f"2026-08-28T00:{self._i // 60:02d}:{self._i % 60:02d}Z",
                "jsonPayload": {"room": f"r{self._i % 3}", "userId": f"u{self._i % 2}",
                                "message": "Session started"}}


class QuotaHandling(unittest.TestCase):
    """A quota rejection must not turn into a short read."""

    def setUp(self):
        # Import fresh so patched module state can't leak between tests.
        self.lm = _load(MINUTES, "lk_minutes_under_test")
        self.lm.READS_PER_MIN = 0        # don't actually sleep in tests
        self.slept = []
        self.lm.time = types.SimpleNamespace(
            monotonic=lambda: 0.0, sleep=self.slept.append)

    def _auth(self, behaviour):
        client = types.SimpleNamespace(list_entries=behaviour)
        return types.SimpleNamespace(client=client, project="p", source="TEST",
                                     principal="t")

    def test_persistent_quota_error_raises_instead_of_undercounting(self):
        exhausted = self.lm.gexc.ResourceExhausted("Quota exceeded for read_requests")

        def always_429(**kw):
            raise exhausted

        self.lm.QUOTA_RETRIES = 2
        with self.assertRaises(self.lm.QuotaExhausted):
            list(self.lm._list_entries(self._auth(always_429), 'logName="x"'))
        # Backed off past the one-minute quota window each time, rather than
        # hammering: a shorter wait cannot clear a per-minute quota.
        self.assertEqual(len(self.slept), 2)
        self.assertTrue(all(w >= 60.0 for w in self.slept), self.slept)

    def test_transient_quota_error_recovers_the_full_walk(self):
        state = {"calls": 0}

        def flaky(**kw):
            state["calls"] += 1
            if state["calls"] == 1:
                raise self.lm.gexc.ResourceExhausted("Quota exceeded")
            return iter([_FakeEntry(i) for i in range(5)])

        self.lm.QUOTA_RETRIES = 3
        got = list(self.lm._list_entries(self._auth(flaky), 'logName="x"'))
        self.assertEqual(len(got), 5, "a recovered walk must return every entry")
        self.assertEqual(state["calls"], 2)

    def test_no_partial_results_leak_from_a_failed_attempt(self):
        """The retry restarts the walk, so a caller must never see both attempts."""
        state = {"calls": 0}

        def half_then_ok(**kw):
            state["calls"] += 1
            if state["calls"] == 1:
                def gen():
                    yield _FakeEntry(0)
                    yield _FakeEntry(1)
                    raise self.lm.gexc.ResourceExhausted("Quota exceeded mid-walk")
                return gen()
            return iter([_FakeEntry(i) for i in range(4)])

        self.lm.QUOTA_RETRIES = 2
        got = list(self.lm._list_entries(self._auth(half_then_ok), 'logName="x"'))
        self.assertEqual(len(got), 4, f"partial attempt leaked: got {len(got)} entries")

    def test_entries_come_back_ascending(self):
        def ok(**kw):
            # The walk fetches newest-first; callers are promised oldest-first.
            return iter([_FakeEntry(i) for i in (9, 5, 1)])

        got = list(self.lm._list_entries(self._auth(ok), 'logName="x"'))
        stamps = [e["timestamp"] for e in got]
        self.assertEqual(stamps, sorted(stamps))

    def test_max_entries_refuses_a_capped_read(self):
        # A capped read is the newest slice of the day; it must raise, not return it.
        def ok(**kw):
            return iter([_FakeEntry(i) for i in range(50)])

        with self.assertRaises(self.lm.TruncatedReadError):
            list(self.lm._list_entries(self._auth(ok), 'logName="x"', max_entries=7))


class Pacer(unittest.TestCase):
    """The pacer keeps a well-behaved caller under the per-minute read cap."""

    def setUp(self):
        self.lm = _load(MINUTES, "lk_minutes_pacer")

    def test_slots_beyond_the_cap_wait_for_the_window(self):
        now = {"t": 0.0}
        slept = []

        def sleep(s):
            slept.append(s)
            now["t"] += s

        self.lm.time = types.SimpleNamespace(monotonic=lambda: now["t"], sleep=sleep)
        self.lm.READS_PER_MIN = 3
        self.lm._read_times.clear()
        for _ in range(4):
            self.lm._await_read_slot()
        self.assertEqual(len(slept), 1, "4th read in a 3/min budget should have waited")
        self.assertGreater(slept[0], 0)

    def test_zero_means_opt_out(self):
        self.lm.READS_PER_MIN = 0
        self.lm._read_times.clear()
        self.lm.time = types.SimpleNamespace(
            monotonic=lambda: 0.0,
            sleep=lambda s: self.fail("opted-out pacer must never sleep"))
        for _ in range(100):
            self.lm._await_read_slot()


if __name__ == "__main__":
    unittest.main()
