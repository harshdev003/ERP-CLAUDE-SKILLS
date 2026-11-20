"""STT is billed by whoever the transport routed it through (PROD-5995).

The defect: on 2026-09-10 the LiveKit voice agent moved STT from a direct Deepgram
connection to LiveKit Inference. `voice_deepgram` kept reading Deepgram's own usage API,
which now legitimately reports nothing, so the column went to $0.00 on days carrying
thousands of LiveKit minutes. Nothing raised. It is a well-formed zero, and it is also
what a genuinely quiet day looks like -- which is why it went unnoticed for four days.

Two things had to be true, and only one of them is a number:

  1. $0 is the CORRECT figure post-cutover. The minutes draw down LiveKit's included
     credit block, and that block is part of the $500 subscription PROD-5998 already
     books in full -- so pricing them again would have added $12.65 to a $500 invoice.
  2. It has to be $0 *because the transport says so*. A zero nobody can explain must
     stop the run rather than persist, because an absent cost has no shape in the store.

Every assertion below covers a failure that produces a plausible number instead of an
error. None of it touches BigQuery, Deepgram, or GCP: the decision path is pure, which
is the property that makes the silent zero catchable at all.
"""

import contextlib
import importlib.abc
import importlib.util
import pathlib
import re
import sys
import types
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/store-aurie-user-daily-cost/scripts"


# `livekit_minutes.py` calls sys.exit(2) at import when google-cloud-logging is
# absent, so the suite stubs `google.*` -- a meta_path finder rather than an
# enumerated submodule list, which cannot fall behind a newly-added import.
#
# Applied UNCONDITIONALLY, not only-when-absent. A conditional stub makes a
# machine with the SDK take a different path from CI, which is exactly how the
# first version of this file passed locally and died in CI. Nothing asserted here
# touches the transport; the stubs only have to satisfy the import.
class _StubModule(types.ModuleType):
    def __getattr__(self, name):
        # Exception subclasses so `except gexc.NotFound:` clauses stay valid.
        cls = type(name, (Exception,), {"__module__": self.__name__})
        setattr(self, name, cls)
        return cls


class _GoogleStubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "google" or fullname.startswith("google."):
            return importlib.util.spec_from_loader(fullname, self, is_package=True)
        return None

    def create_module(self, spec):
        mod = _StubModule(spec.name)
        mod.__path__ = []
        return mod

    def exec_module(self, module):
        pass


@contextlib.contextmanager
def _stubbed_google():
    saved = {k: v for k, v in sys.modules.items()
             if k == "google" or k.startswith("google.")}
    finder = _GoogleStubFinder()
    for k in saved:
        del sys.modules[k]
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        for k in [k for k in sys.modules if k == "google" or k.startswith("google.")]:
            del sys.modules[k]
        sys.modules.update(saved)


def _load(name):
    sys.path.insert(0, str(SCRIPTS))
    try:
        with _stubbed_google():
            spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(SCRIPTS))


def _entry(room, ts, **payload):
    return {"timestamp": ts, "jsonPayload": dict(room=room, **payload)}


class TestSttCapture(unittest.TestCase):
    """The agent emits `stt_provider` / `stt_seconds` as structured `extra=` fields."""

    def setUp(self):
        self.lm = _load("livekit_minutes")

    def test_the_fields_are_read_structurally_not_from_the_message(self):
        # A regex over the message would break on any wording change and do so silently.
        # These arrive as their own payload keys, so there is nothing to parse.
        s = self.lm._build_sessions([
            _entry("r1", "2026-09-12T10:00:00Z", message="Session usage totals",
                   stt_provider="deepgram-inference", stt_seconds=120.0),
        ])["r1"]
        self.assertEqual(s.stt_provider, "deepgram-inference")
        self.assertEqual(s.stt_seconds, 120.0)

    def test_a_malformed_duration_is_never_logged_not_zero_speech(self):
        s = self.lm._build_sessions([
            _entry("r1", "2026-09-12T10:00:00Z", stt_seconds="not-a-number"),
        ])["r1"]
        self.assertIsNone(s.stt_seconds)

    def test_speech_seconds_are_not_the_rooms_wall_clock(self):
        # The room spans 10 minutes; the recognizer processed 2. The fixture's 5x is
        # chosen to make the confusion visible, NOT a claim about production: the real
        # ratio is unmeasured. Do not put a number on it here — an earlier version of
        # this comment asserted "~2.5x on real traffic", which was never measured and
        # which the first live session (1.14x) contradicted.
        sess = self.lm._build_sessions([
            _entry("r1", "2026-09-12T10:00:00Z", stt_seconds=120.0,
                   stt_provider="deepgram-inference"),
            _entry("r1", "2026-09-12T10:10:00Z"),
        ])
        self.assertAlmostEqual(sess["r1"].minutes, 10.0, places=6)
        self.assertAlmostEqual(self.lm._stt_totals(sess)["stt_minutes"], 2.0, places=6)


class TestSttTotals(unittest.TestCase):
    def setUp(self):
        self.lm = _load("livekit_minutes")

    def _sessions(self, *specs):
        out = {}
        for i, (provider, seconds) in enumerate(specs):
            s = self.lm._Session(room=f"r{i}")
            s.stt_provider, s.stt_seconds = provider, seconds
            out[s.room] = s
        return out

    def test_an_unmeasured_session_is_excluded_never_counted_as_zero(self):
        # THE load-bearing assertion. A day predating the field has no durations at all;
        # folding those in as zero makes it indistinguishable from a day with no speech,
        # and the zero is the one that persists into an append-only store.
        t = self.lm._stt_totals(self._sessions(
            ("deepgram-inference", 60.0), (None, None)))
        self.assertEqual(t["stt_sessions_measured"], 1)
        self.assertAlmostEqual(t["stt_minutes"], 1.0, places=6)

    def test_a_stated_transport_survives_an_unmeasured_duration(self):
        # Excluding the MINUTES must not also drop the TRANSPORT. If it does, an
        # unmeasured Inference day carries no inference key, `stt_transport_split`
        # cannot recognise it, and the store falls back to Deepgram's usage API for the
        # well-formed $0 this change exists to remove.
        t = self.lm._stt_totals(self._sessions(("deepgram-inference", None)))
        self.assertEqual(t["stt_minutes_by_transport"], {"deepgram-inference": 0.0})
        self.assertEqual(t["stt_sessions_measured"], 0)

    def test_the_tag_carries_a_count(self):
        # A tag with no reported count is just a quieter drop: nothing downstream can
        # say how much of the day the transport label actually covers.
        t = self.lm._stt_totals(self._sessions(
            ("deepgram-inference", None), ("deepgram-inference", 60.0), (None, None)))
        self.assertEqual(t["stt_sessions_with_transport"], 2)
        self.assertEqual(t["stt_sessions_measured"], 1)

    def test_the_measured_count_is_what_makes_a_partial_day_legible(self):
        t = self.lm._stt_totals(self._sessions((None, None), (None, None)))
        self.assertEqual(t["stt_sessions_measured"], 0)
        self.assertEqual(t["stt_minutes"], 0.0)
        self.assertEqual(t["stt_minutes_by_transport"], {})

    def test_minutes_split_by_transport_because_the_invoice_does(self):
        t = self.lm._stt_totals(self._sessions(
            ("deepgram-inference", 120.0), ("deepgram", 60.0),
            ("deepgram-inference", 60.0)))
        self.assertEqual(t["stt_minutes_by_transport"],
                         {"deepgram": 1.0, "deepgram-inference": 3.0})

    def test_a_measured_session_with_no_transport_is_tagged_not_dropped(self):
        t = self.lm._stt_totals(self._sessions((None, 60.0)))
        self.assertEqual(t["stt_minutes_by_transport"], {"unknown": 1.0})


class TestTransportSplit(unittest.TestCase):
    """Which source is authoritative is decided by the transport, not by the dollar."""

    def setUp(self):
        self.store = _load("store")

    def test_inference_traffic_routes_to_the_credit_block(self):
        on, inf, direct, _ = self.store.stt_transport_split(
            {"stt_minutes": 10.0, "stt_sessions_measured": 3,
             "stt_minutes_by_transport": {"deepgram-inference": 10.0}})
        self.assertTrue(on)
        self.assertAlmostEqual(inf, 10.0, places=6)
        self.assertAlmostEqual(direct, 0.0, places=6)

    def test_direct_traffic_still_routes_to_deepgrams_own_invoice(self):
        on, inf, direct, _ = self.store.stt_transport_split(
            {"stt_minutes": 10.0, "stt_sessions_measured": 3,
             "stt_minutes_by_transport": {"deepgram": 10.0}})
        self.assertFalse(on)
        self.assertAlmostEqual(direct, 10.0, places=6)

    def test_a_stated_transport_beats_an_absent_measurement(self):
        # Sessions logged the transport but not the duration (the field shipped later).
        # Trusting the missing measurement over the stated routing is exactly how a
        # routing change becomes a silent zero.
        #
        # Fed from `_stt_totals` rather than a hand-built dict, deliberately: the first
        # version of this test asserted a contract the PRODUCER did not honour -- it
        # passed against a meta shape `_stt_totals` could never emit, so the branch it
        # claimed to cover was unreachable on real logs. A fixture that only the test
        # can construct proves nothing about the path.
        lm = _load("livekit_minutes")
        sess = lm._build_sessions([
            _entry("r1", "2026-09-12T10:00:00Z", stt_provider="deepgram-inference"),
        ])
        on, inf, _, by = self.store.stt_transport_split(lm._stt_totals(sess))
        self.assertIn("deepgram-inference", by)
        self.assertTrue(on)
        self.assertEqual(inf, 0.0)

    def test_a_day_with_no_stt_signal_at_all_falls_back_to_deepgram(self):
        on, _, _, by = self.store.stt_transport_split({})
        self.assertFalse(on)
        self.assertEqual(by, {})

    def test_a_mixed_day_is_not_silently_priced_as_all_inference(self):
        # Direct minutes present means Deepgram's own invoice still has something on it,
        # so main() must keep reading the usage API rather than booking the day at $0.
        on, inf, direct, _ = self.store.stt_transport_split(
            {"stt_minutes": 10.0, "stt_sessions_measured": 4,
             "stt_minutes_by_transport": {"deepgram": 4.0, "deepgram-inference": 6.0}})
        self.assertTrue(on)
        self.assertGreater(direct, 0.0)  # so `on_inference and direct <= 0` is False


class TestInferenceCredits(unittest.TestCase):
    def setUp(self):
        self.store = _load("store")
        self.plan = _load("livekit_plan")

    def test_inside_the_block_the_marginal_cost_is_zero(self):
        billed, draw, note = self.store.deepgram_inference_day("scale", 3013.0, 0.0)
        self.assertEqual(billed, 0.0)
        self.assertAlmostEqual(draw, 3013.0 * 0.0042, places=6)
        self.assertEqual(note, "")

    def test_zero_is_a_decision_not_a_rounding(self):
        # September's 3,013 STT minutes are $12.65 of drawdown. Booking that in
        # `voice_deepgram` would have put $512.65 against a $500 invoice, because
        # PROD-5998 already books the whole subscription the block sits inside.
        _, draw, _ = self.store.deepgram_inference_day("scale", 3013.0, 0.0)
        self.assertAlmostEqual(draw, 12.65, places=2)

    def test_past_the_block_only_the_excess_is_billed(self):
        block = self.plan.included("inference_credits_usd", "scale")
        billed, draw, _ = self.store.deepgram_inference_day("scale", 1000.0, block - 3.0)
        self.assertAlmostEqual(billed, draw - 3.0, places=6)

    def test_a_month_of_daily_charges_sums_to_the_months_overage(self):
        # Marginal, not cumulative: a cumulative form re-charges the whole excess every
        # day, growing quadratically while each single day still looks reasonable.
        block = self.plan.included("inference_credits_usd", "scale")
        per_day, mtd, total = 4000.0, 0.0, 0.0
        for _ in range(30):
            billed, draw, _ = self.store.deepgram_inference_day("scale", per_day, mtd)
            total += billed
            mtd += draw
        self.assertAlmostEqual(total, max(0.0, mtd - block), places=6)

    def test_the_multilingual_band_is_not_the_monolingual_one(self):
        # ~19% apart on Scale. A rate applied without checking the language is wrong in
        # a direction nothing reports.
        _, mono, _ = self.store.deepgram_inference_day("scale", 1000.0, 0.0)
        _, multi, _ = self.store.deepgram_inference_day(
            "scale", 1000.0, 0.0, multilingual=True)
        self.assertGreater(multi, mono)

    def test_an_unknown_model_reports_rather_than_returning_a_bare_zero(self):
        billed, draw, note = self.store.deepgram_inference_day(
            "scale", 1000.0, 0.0, model="deepgram/nova-9")
        self.assertEqual((billed, draw), (0.0, 0.0))
        self.assertIn("deepgram_inference_pending", note)

    def test_the_rate_card_is_read_from_the_plan_of_record(self):
        # A second reader of the card is a second place to get the band wrong.
        self.assertAlmostEqual(
            self.store.deepgram_inference_day("scale", 100.0, 0.0)[1],
            self.plan.inference_usd("deepgram/nova-3", "scale", 100.0), places=9)


class TestUnexplainedZeroGuard(unittest.TestCase):
    """A $0 nobody can explain is TAGGED, loudly — and the day is still written.

    The first cut raised SystemExit here. The blast radius was wrong: an *explained*
    failure (`deepgram_pending`) let the day store, while an *unexplained* zero stopped
    the whole day — LLM and ElevenLabs included — over one vendor's unknown. Measured on
    2026-09-14 it blocked the entire cost store because the voice agent had not yet
    shipped the STT log fields. Halting the financial pipeline to report an unknown in
    one column is the withholding-the-deliverable shape.
    """

    def setUp(self):
        self.store = _load("store")

    def test_the_cutover_shape_is_tagged(self):
        msg = self.store.unexplained_deepgram_zero("2026-09-11", 0.0, 3013.0, "", {})
        self.assertIsNotNone(msg)
        self.assertIn("2026-09-11", msg)
        self.assertIn("3,013.0 LiveKit minutes", msg)
        self.assertIn("deepgram_transport_unknown", msg)

    def test_the_tag_says_unmeasured_not_zero_speech(self):
        # The whole point of the tag: a later reader must not take the $0 at face value.
        msg = self.store.unexplained_deepgram_zero("2026-09-11", 0.0, 3013.0, "", {})
        self.assertIn("UNMEASURED", msg)

    def test_it_never_halts_the_store(self):
        # The day carries LLM and ElevenLabs cost too. Blocking all of it on one vendor's
        # unknown is disproportionate, and it is what shipped before this change.
        src = (SCRIPTS / "store.py").read_text()
        self.assertNotIn("raise SystemExit(refusal)", src)
        self.assertIn("dg_note = zero_note", src)

    def test_the_warning_is_raised_where_it_is_detected(self):
        # A line at the end of a long run is exactly what went unread for four days.
        src = (SCRIPTS / "store.py").read_text()
        self.assertIn('print(f"WARNING: {zero_note}", file=sys.stderr)', src)

    def test_an_explained_zero_is_not_tagged_twice(self):
        self.assertIsNone(self.store.unexplained_deepgram_zero(
            "2026-09-11", 0.0, 3013.0,
            "deepgram_via_livekit_inference: 3,013.0 STT min = $12.65 drawn …",
            {"deepgram-inference": 3013.0}))

    def test_a_failed_read_is_already_explained_by_its_own_note(self):
        # deepgram_day_total never returns a bare $0 for a failed read, so the guard
        # must not double-report it -- two notes on the same fact train people to
        # ignore both.
        self.assertIsNone(self.store.unexplained_deepgram_zero(
            "2026-09-11", 0.0, 3013.0, "deepgram_pending: key not authorised", {}))

    def test_a_day_with_no_livekit_traffic_legitimately_bills_nothing(self):
        # Deepgram is LiveKit-path-only: $0 with 0 minutes is the expected answer.
        self.assertIsNone(
            self.store.unexplained_deepgram_zero("2026-09-11", 0.0, 0.0, "", {}))

    def test_a_nonzero_charge_is_never_tagged(self):
        self.assertIsNone(
            self.store.unexplained_deepgram_zero("2026-09-11", 4.20, 3013.0, "", {}))

    def test_the_tag_names_the_transports_it_saw(self):
        # Without this the operator cannot tell a broken read from a routing change,
        # which is the distinction the whole guard exists to force.
        msg = self.store.unexplained_deepgram_zero(
            "2026-09-11", 0.0, 3013.0, "", {"cartesia-stt": 100.0})
        self.assertIn("cartesia-stt", msg)


class TestMonthToDateDrawdown(unittest.TestCase):
    """The drawdown has to be STORED, and the store has to read it back (PROD-6008).

    `deepgram_inference_day` took a month-to-date figure from the day it landed;
    `store.py` passed a literal `0.0`, so every day was priced against an empty credit
    block. Right while a month stays inside the block, and silently $0 the moment it
    does not -- the same shape one layer up from the zero PROD-5995 removed.
    """

    def setUp(self):
        self.store = _load("store")
        self.src = (SCRIPTS / "store.py").read_text()

    def _mtd(self, rows):
        """Call the read-back with a stubbed `bq`, returning the SQL it issued."""
        captured = {}

        def query(sql, project, params=None):
            captured["sql"] = " ".join(sql.split())
            captured["params"] = params
            return rows

        stub = types.ModuleType("bq")
        stub.query = query
        saved = sys.modules.get("bq")
        sys.modules["bq"] = stub
        try:
            value = self.store.mtd_inference_drawdown_before(
                "2026-09-20", "p.d.t", "p")
        finally:
            if saved is not None:
                sys.modules["bq"] = saved
            else:
                del sys.modules["bq"]
        return value, captured

    def test_it_sums_the_stored_column(self):
        value, _ = self._mtd([{"drawdown": 31.5}])
        self.assertAlmostEqual(value, 31.5, places=6)

    def test_the_window_is_this_month_and_excludes_the_day_itself(self):
        # Including today would double-count the day being written; starting before the
        # month would carry a spent block across a renewal that reset it. Both produce a
        # plausible number.
        _, cap = self._mtd([{"drawdown": 0.0}])
        self.assertIn("DATE_TRUNC(DATE(@day), MONTH)", cap["sql"])
        self.assertIn("date < DATE(@day)", cap["sql"])
        self.assertNotIn("date <= DATE(@day)", cap["sql"])
        self.assertEqual(cap["params"], {"day": "2026-09-20"})

    def test_a_query_failure_raises_rather_than_defaulting_to_zero(self):
        # Zero puts the whole month back inside the block, so a day that IS past it
        # books $0 -- exactly the undercount the column exists to remove.
        def boom(sql, project, params=None):
            raise RuntimeError("bigquery unavailable")

        stub = types.ModuleType("bq")
        stub.query = boom
        saved = sys.modules.get("bq")
        sys.modules["bq"] = stub
        try:
            with self.assertRaises(RuntimeError):
                self.store.mtd_inference_drawdown_before("2026-09-20", "p.d.t", "p")
        finally:
            if saved is not None:
                sys.modules["bq"] = saved
            else:
                del sys.modules["bq"]

    def test_the_read_back_is_actually_wired_in(self):
        # THE regression guard. The arithmetic was always right; the defect was that
        # main() handed it a literal. A test of `deepgram_inference_day` alone passes
        # against the broken store, which is how this shipped in the first place.
        self.assertIn("mtd_inference_drawdown_before(day, per_user_table, bq_project)",
                      self.src)
        self.assertIn("args.livekit_plan, inference_minutes, mtd_draw_before", self.src)
        self.assertNotIn("args.livekit_plan, inference_minutes, 0.0", self.src)


class TestDrawdownColumn(unittest.TestCase):
    def setUp(self):
        self.store = _load("store")
        self.bq_sink = _load("bq_sink")

    def test_the_column_exists_in_both_schemas(self):
        self.assertIn("deepgram_inference_drawdown", self.store.COLS)
        self.assertEqual([n for n, _ in self.bq_sink.STORES["per_user"]["schema"]],
                         self.store.COLS)

    def test_it_sits_next_to_the_vendor_it_draws_for(self):
        i = self.store.COLS.index("voice_deepgram")
        self.assertEqual(self.store.COLS[i + 1], "deepgram_inference_drawdown")

    def test_it_is_not_a_cost_column(self):
        # Spend against a PREPAID block the subscription already books. Folding it into
        # voice_cost would double-count the subscription and break the reconciliation
        # every other LiveKit column exists to preserve.
        src = (SCRIPTS / "store.py").read_text()
        vtot = [l for l in src.split("\n") if "vtot = v11a" in l]
        self.assertEqual(len(vtot), 1, vtot)
        self.assertNotIn("draw", vtot[0])
        ov = [l for l in src.split("\n") if l.strip().startswith("ov = round(")]
        self.assertEqual(len(ov), 1, ov)
        self.assertNotIn("odraw", ov[0])

    def test_the_previous_schema_is_todays_minus_the_new_column(self):
        # Minus every column that postdates it — `voice_fish` (ENG-1306) landed later too.
        added_since = {"deepgram_inference_drawdown", "voice_fish", "voice_inworld"}
        self.assertEqual(self.store.PRE_DRAWDOWN_COLS,
                         [c for c in self.store.COLS if c not in added_since])

    def test_an_unattributed_remainder_still_reaches_the_other_row(self):
        # The Other-row gate is driven by COST totals, and the drawdown is not a cost.
        # A day whose only leftover is drawdown would write no Other row and lose it —
        # and a lost drawdown delays the month's crossing day, silently.
        src = (SCRIPTS / "store.py").read_text()
        gate = [l for l in src.split("\n")
                if l.strip().startswith("if ") and "0.005" in l]
        self.assertEqual(len(gate), 1, gate)
        self.assertIn("abs(odraw) > 0.005", gate[0])


class TestMixedDayDrawdown(unittest.TestCase):
    """A mixed day draws against the block too (Bugbot, PROD-6008).

    The first cut let the branch that decides *who bills* also decide whether a drawdown
    was recorded: a day with both direct and Inference STT took the Deepgram-invoice path
    and set the drawdown to 0. The day's Inference usage vanished, so month-to-date
    under-counted and the block's crossing day arrived late — silently, which is the
    failure the column exists to remove.
    """

    def setUp(self):
        self.store = _load("store")
        self.src = (SCRIPTS / "store.py").read_text()

    def test_the_drawdown_is_computed_before_any_billing_branch(self):
        # The structural guard: `dg_draw` must not be assigned inside a branch, because
        # whether speech drew on the block is a fact about the minutes, not about which
        # invoice covers the day.
        body = self.src[self.src.index("    mtd_draw_before = "):]
        # Anchored on the CALL, not on the variable it is assigned to -- the name has
        # already changed once (refusal -> zero_note) and an anchor that tracks a name
        # fails for a reason unrelated to what the test is checking.
        body = body[: body.index("unexplained_deepgram_zero(")]
        self.assertIn("inference_billed, dg_draw, dg_note = deepgram_inference_day(", body)
        self.assertNotIn("dg_draw = 0.0", body)

    def test_a_mixed_day_adds_inference_overage_to_the_direct_invoice(self):
        # Additive, never an alternative: Deepgram bills the direct minutes, the block
        # bills the rest. Picking one would drop real spend either way.
        body = self.src[self.src.index("    mtd_draw_before = "):]
        self.assertIn("day_deepgram = round(direct_total + inference_billed, 4)", body)

    def test_a_pure_direct_day_still_draws_nothing(self):
        # With no inference minutes the helper is a no-op, so the direct path is unchanged.
        billed, draw, note = self.store.deepgram_inference_day("scale", 0.0, 0.0)
        self.assertEqual((billed, draw, note), (0.0, 0.0, ""))


class TestInferenceNote(unittest.TestCase):
    """Three states, three notes — a note that disagrees with the column is the same
    silent shape as an unexplained zero."""

    def setUp(self):
        self.store = _load("store")

    def test_unmeasured_says_so_rather_than_claiming_zero_speech(self):
        n = self.store.inference_note(0.0, 0.0, 0.0, 50.0, measured_sessions=0)
        self.assertIn("UNMEASURED", n)

    def test_inside_the_block_reports_the_drawdown(self):
        n = self.store.inference_note(3013.0, 12.65, 0.0, 50.0, measured_sessions=40)
        self.assertIn("$12.65", n)
        self.assertIn("$50.00", n)
        self.assertNotIn("PAST", n)

    def test_an_overflow_is_never_worded_as_an_included_block_zero(self):
        n = self.store.inference_note(12000.0, 50.40, 0.40, 50.0, measured_sessions=40)
        self.assertIn("deepgram_inference_overage", n)
        self.assertIn("PAST", n)
        self.assertIn("$0.40", n)
        self.assertNotIn("billed $0", n)

    def test_a_mixed_day_says_deepgram_also_billed(self):
        # Without this the note reads as though the block covered the whole day.
        n = self.store.inference_note(100.0, 0.42, 0.0, 50.0, measured_sessions=10,
                                      mixed=True)
        self.assertIn("also billed direct to Deepgram", n)
        plain = self.store.inference_note(100.0, 0.42, 0.0, 50.0, measured_sessions=10)
        self.assertNotIn("also billed direct", plain)


class TestConsumerReadsOnlyWhatTheProducerEmits(unittest.TestCase):
    """A fixture only the test can construct proves nothing about the real path.

    This is the mechanical form of the defect that shipped in #981. `_stt_totals` did not
    record a stated transport when the duration was unmeasured, so the
    `stt_transport_split` branch that is *supposed* to catch that case could never fire on
    real logs. The test covering it passed — because it hand-built a `meta` dict with a
    key `_stt_totals` was incapable of emitting. Green test, unreachable branch, and the
    zero it existed to prevent went on shipping.

    The generalisable check is a **subset relation discovered from source**: every `stt_*`
    key the consumer reads out of `meta` must be one the producer can actually put there.
    Listing the keys by hand would reintroduce the same flaw one level up — the list would
    be another artifact only this test knows about — so the read set is parsed out of
    `store.py` and the emitted set comes from calling `_stt_totals` for real.
    """

    def setUp(self):
        self.lm = _load("livekit_minutes")
        self.src = (SCRIPTS / "store.py").read_text()

    def _keys_read(self):
        # Every `<something>.get("stt_…")` / `.get('stt_…')` in the consumer.
        return set(re.findall(r"""\.get\(\s*["'](stt_[a-z_]+)["']""", self.src))

    def _keys_emitted(self):
        return set(self.lm._stt_totals({}).keys())

    def test_the_consumer_reads_something(self):
        # Guards the guard: a regex that silently matches nothing would make every
        # assertion below vacuously true — the same green-but-blind shape.
        self.assertGreaterEqual(len(self._keys_read()), 4, self._keys_read())

    def test_every_key_read_is_a_key_the_producer_emits(self):
        read, emitted = self._keys_read(), self._keys_emitted()
        self.assertEqual(read - emitted, set(),
                         f"store.py reads meta keys `_stt_totals` never emits: "
                         f"{sorted(read - emitted)}. Either the producer stopped writing "
                         f"them or the consumer invented them; both make the reading "
                         f"branch unreachable on real data while unit tests that hand-build "
                         f"`meta` keep passing.")

    def test_an_empty_session_map_still_emits_the_whole_shape(self):
        # The producer's contract has to hold on the boring input too — a day with no
        # sessions must still carry every key, or a consumer's `.get()` silently defaults
        # and the day reads as measured-and-zero rather than unmeasured.
        self.assertEqual(self._keys_emitted(),
                         {"stt_minutes", "stt_sessions_measured",
                          "stt_sessions_with_transport", "stt_minutes_by_transport"})


if __name__ == "__main__":
    unittest.main()
