"""Deepgram served through LiveKit Inference must not render as a well-formed $0.00.

The LiveKit agent's STT moved to LiveKit Inference. Deepgram's own usage API sees only
speech sent to it DIRECTLY, so for an Inference day it answers $0 with no error — and
`eng:report-aurie-daily-costs` read Deepgram from that API alone. `eng:store-aurie-user-
daily-cost` books the day correctly (overage past the plan's included Inference credit
block, `deepgram_inference_day`), so on 2026-09-29 the store held `voice_deepgram` =
$10.40 while `report.py --window last-7d` printed $0.00, and the Voice and Combined
totals were short by the same amount with nothing in the table looking wrong. Same
failure class as ENG-1214.

Every test here injects the failure the defect needed: a Deepgram API that answers a
clean $0. The load-bearing assertions are the negative ones:

  * a stored day is NEVER read from the Deepgram API (that is where the $0 came from)
  * an inside-the-block day stays a real $0.00, not an unread `—`
  * a quiet stored day stays $0.00
  * a day whose transport or drawdown cannot be established is `—`, never $0.00

Pricing helpers come from the real store module, so the report and the store cannot
disagree about the credit block. Network is stubbed; no keys, no calls.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import re
import sys
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_livekit_degraded_render import R  # noqa: E402  — report loaded under stubbed google

REPO = pathlib.Path(__file__).resolve().parents[1]
SINK = REPO / "plugins/eng/skills/store-aurie-user-daily-cost/scripts/bq_sink.py"
REAL_STORE = R._store
RATES = {"cartesia": 45.0, "deepgram": 0.348, "livekit": 0.01}
INFERENCE = "deepgram-inference"   # the transport key livekit_minutes._stt_totals emits


def _d(s):
    return dt.date.fromisoformat(s)


def _inference_meta(minutes, measured=10):
    """The meta block `livekit_minutes._stt_totals` produces for an all-Inference day."""
    return {"stt_minutes": minutes, "stt_sessions_measured": measured,
            "stt_sessions_with_transport": measured,
            "stt_minutes_by_transport": {INFERENCE: minutes}}


class Store:
    """Real store helpers, with the two network reads replaced.

    `deepgram_day_total` answers exactly what Deepgram's API answers for an Inference
    day: $0 and no note. That is the injected failure.
    """

    def __init__(self, deepgram=(0.0, ""), livekit=None):
        self._dg = deepgram
        # {day: (minutes, usd, note, meta)} for the live walk; default is a failed walk.
        self._lk = livekit or {}
        self.deepgram_calls, self.livekit_calls = [], []

    def cartesia_day_total(self, day, rate):
        return 0.0, ""

    def inworld_day_total(self, day, lk_meta, rate):
        return 0.0, ""

    def deepgram_day_total(self, day, rate):
        self.deepgram_calls.append(day)
        return self._dg

    def livekit_costs(self, day, rate):
        self.livekit_calls.append(day)
        minutes, usd, note, meta = self._lk.get(
            day, (0.0, 0.0, "livekit_pending: log query failed", {}))
        return {}, minutes, usd, note, meta

    def __getattr__(self, name):
        return getattr(REAL_STORE, name)


def _bq(dg_rows=(), lk_rows=(), dg_raises=None):
    """Fake BigQuery dispatching on which query it is handed."""
    mod = types.SimpleNamespace(PROJECT="p", DATASET="d", calls=[])

    def query(sql, project, params=None):
        mod.calls.append((sql, params))
        if "voice_deepgram" in sql:
            if dg_raises:
                raise dg_raises
            return list(dg_rows)
        return list(lk_rows)
    mod.query = query
    return mod


def _dg_row(day, usd, drawdown, minutes):
    return {"date": day, "deepgram_usd": usd, "drawdown_usd": drawdown,
            "livekit_minutes": minutes, "livekit_usd": minutes * 0.01}


def _lk_row(day, minutes):
    return {"date": day, "livekit_minutes": minutes, "livekit_usd": minutes * 0.01,
            "platform_usd": 10.0}


class _Base(unittest.TestCase):
    def setUp(self):
        self._saved = R._bq, R._store

    def tearDown(self):
        R._bq, R._store = self._saved

    def fetch(self, frm, to, *, bq, store):
        R._bq, R._store = bq, store
        return R.fetch_livekit_path_daily(_d(frm), _d(to), RATES,
                                          bq_project="p", bq_dataset="d")


class StoredDaysComeFromTheStore(_Base):

    def test_the_incident_day_shows_what_the_store_booked(self):
        """2026-09-29: store booked $10.40 past the $50 block; the report said $0.00."""
        store = Store()
        out, notes = self.fetch(
            "2026-09-29", "2026-09-29", store=store,
            bq=_bq(dg_rows=[_dg_row("2026-09-29", 10.40, 11.20, 2400.0)],
                   lk_rows=[_lk_row("2026-09-29", 2400.0)]))
        self.assertAlmostEqual(out["2026-09-29"]["deepgram"], 10.40, places=4)
        self.assertEqual(store.deepgram_calls, [],
                         "a stored day must not be read from the Deepgram API — that "
                         "API cannot see Inference STT and answers $0")
        self.assertTrue(any(n.startswith("deepgram_source:") for n in notes))

    def test_inside_the_block_is_a_real_zero(self):
        """NEGATIVE: $0 billed while drawing on the block is correct, not unread."""
        out, notes = self.fetch(
            "2026-09-03", "2026-09-03", store=Store(),
            bq=_bq(dg_rows=[_dg_row("2026-09-03", 0.0, 4.10, 900.0)],
                   lk_rows=[_lk_row("2026-09-03", 900.0)]))
        self.assertEqual(out["2026-09-03"]["deepgram"], 0.0)
        self.assertTrue(any("inside it" in n for n in notes),
                        "an inside-the-block $0 must say why it is $0")

    def test_a_stored_quiet_day_is_a_real_zero(self):
        """NEGATIVE: no traffic, no drawdown, $0 — a measurement."""
        out, _ = self.fetch(
            "2026-09-05", "2026-09-05", store=Store(),
            bq=_bq(dg_rows=[_dg_row("2026-09-05", 0.0, 0.0, 0.0)],
                   lk_rows=[_lk_row("2026-09-05", 0.0)]))
        self.assertEqual(out["2026-09-05"]["deepgram"], 0.0)

    def test_a_stored_unexplained_zero_is_unread(self):
        """Traffic, no drawdown, $0: the store tagged it at write time; the tag is lost."""
        out, notes = self.fetch(
            "2026-09-14", "2026-09-14", store=Store(),
            bq=_bq(dg_rows=[_dg_row("2026-09-14", 0.0, 0.0, 1200.0)],
                   lk_rows=[_lk_row("2026-09-14", 1200.0)]))
        self.assertIs(out["2026-09-14"]["deepgram"], R.UNKNOWN)
        self.assertTrue(any("deepgram_store_unexplained_zero" in n for n in notes))

    def test_query_reaches_back_to_the_month_start(self):
        """Month-to-date drawdown for an unstored day needs the whole month so far."""
        bq = _bq()
        self.fetch("2026-09-24", "2026-09-24", store=Store(), bq=bq)
        dg = [p for sql, p in bq.calls if "voice_deepgram" in sql]
        self.assertEqual(dg, [{"from_d": "2026-09-01", "to_d": "2026-09-24"}])


class UnstoredDaysArePricedLikeTheStore(_Base):

    def test_inference_day_past_the_block_is_priced_not_zeroed(self):
        """Today's partial: block already full from stored days, so today is billed."""
        meta = _inference_meta(600.0)
        store = Store(livekit={"2026-09-30": (700.0, 16.0, "", meta)})
        out, _ = self.fetch(
            "2026-09-29", "2026-09-30", store=store,
            bq=_bq(dg_rows=[_dg_row("2026-09-01", 0.0, 49.0, 5000.0),
                            _dg_row("2026-09-29", 10.40, 11.20, 2400.0)],
                   lk_rows=[_lk_row("2026-09-29", 2400.0)]))
        expected, _, _ = REAL_STORE.deepgram_inference_day("scale", 600.0, 49.0 + 11.20)
        self.assertGreater(expected, 0)
        self.assertAlmostEqual(out["2026-09-30"]["deepgram"], expected, places=6)
        self.assertEqual(store.deepgram_calls, [],
                         "an all-Inference day has nothing for Deepgram's API to report")

    def test_an_earlier_unstored_day_counts_toward_the_block(self):
        """Two unstored days: the second must see the first one's drawdown."""
        m = _inference_meta(3000.0)
        store = Store(livekit={"2026-09-29": (3100.0, 16.0, "", m),
                               "2026-09-30": (3100.0, 16.0, "", m)})
        out, _ = self.fetch("2026-09-29", "2026-09-30", store=store,
                            bq=_bq(dg_rows=[_dg_row("2026-09-01", 0.0, 40.0, 5000.0)]))
        b1, d1, _ = REAL_STORE.deepgram_inference_day("scale", 3000.0, 40.0)
        b2, _, _ = REAL_STORE.deepgram_inference_day("scale", 3000.0, 40.0 + d1)
        self.assertAlmostEqual(out["2026-09-29"]["deepgram"], b1, places=6)
        self.assertAlmostEqual(out["2026-09-30"]["deepgram"], b2, places=6)
        self.assertGreater(b2, b1)

    def test_inference_without_a_readable_drawdown_is_unread(self):
        """Deepgram store query down: the day cannot be placed against the block."""
        store = Store(livekit={"2026-09-30": (700.0, 16.0, "", _inference_meta(600.0))})
        out, notes = self.fetch("2026-09-30", "2026-09-30", store=store,
                                bq=_bq(dg_raises=RuntimeError("boom")))
        self.assertIs(out["2026-09-30"]["deepgram"], R.UNKNOWN)
        self.assertTrue(any("deepgram_inference_unpriced" in n for n in notes))
        self.assertTrue(any("deepgram_store_unavailable" in n for n in notes))

    def test_unmeasured_inference_is_unread(self):
        store = Store(livekit={"2026-09-30": (
            700.0, 16.0, "", _inference_meta(0.0, measured=0))})
        out, _ = self.fetch("2026-09-30", "2026-09-30", store=store, bq=_bq())
        self.assertIs(out["2026-09-30"]["deepgram"], R.UNKNOWN)

    def test_failed_walk_plus_api_zero_is_unread(self):
        """No transport known, and the Deepgram API says $0: that $0 means nothing."""
        store = Store()   # every walk fails
        out, notes = self.fetch("2026-09-30", "2026-09-30", store=store, bq=_bq())
        self.assertIs(out["2026-09-30"]["deepgram"], R.UNKNOWN)
        self.assertTrue(any("deepgram_transport_unread" in n for n in notes))

    def test_store_holds_livekit_but_deepgram_query_failed(self):
        """LiveKit from the store means no walk, so no transport — still not $0."""
        store = Store()
        out, _ = self.fetch(
            "2026-09-29", "2026-09-29", store=store,
            bq=_bq(lk_rows=[_lk_row("2026-09-29", 2400.0)],
                   dg_raises=RuntimeError("boom")))
        self.assertAlmostEqual(out["2026-09-29"]["livekit"], 24.0 + 10.0, places=4)
        self.assertIs(out["2026-09-29"]["deepgram"], R.UNKNOWN)

    def test_direct_only_day_still_reads_deepgram(self):
        """NEGATIVE: a pre-Inference / direct day keeps using the Deepgram API."""
        meta = {"stt_minutes": 100.0, "stt_sessions_measured": 5,
                "stt_minutes_by_transport": {"deepgram": 100.0}}
        store = Store(deepgram=(0.58, ""),
                      livekit={"2026-09-30": (120.0, 16.0, "", meta)})
        out, _ = self.fetch("2026-09-30", "2026-09-30", store=store, bq=_bq())
        self.assertEqual(store.deepgram_calls, ["2026-09-30"])
        self.assertAlmostEqual(out["2026-09-30"]["deepgram"], 0.58, places=6)


class RenderedTotalsIncludeIt(_Base):
    """Through build() and render_md(): the path the skill actually runs."""

    def test_voice_and_combined_carry_the_stored_deepgram(self):
        saved = {n: getattr(R, n) for n in (
            "fetch_openai_daily", "fetch_elevenlabs_daily", "fetch_elevenlabs_split_daily",
            "fetch_elevenlabs_subscription", "fetch_openai_project_names")}
        R.fetch_openai_daily = lambda f, t: ({"2026-09-29": 100.0}, {})
        R.fetch_elevenlabs_daily = lambda f, t: {"2026-09-29": 20.0}
        R.fetch_elevenlabs_split_daily = lambda f, t: {}
        R.fetch_elevenlabs_subscription = lambda: None
        R.fetch_openai_project_names = lambda: {}
        R._bq = _bq(dg_rows=[_dg_row("2026-09-29", 10.40, 11.20, 2400.0)],
                    lk_rows=[_lk_row("2026-09-29", 2400.0)])
        R._store = Store()
        try:
            payload = R.build(_d("2026-09-29"), _d("2026-09-29"), "t", RATES,
                              bq_project="p", bq_dataset="d")
        finally:
            for n, f in saved.items():
                setattr(R, n, f)
        row = payload["rows"][0]
        self.assertAlmostEqual(row["deepgram_usd"], 10.40)
        # 20 ElevenLabs + 0 Cartesia + 10.40 Deepgram + 34 LiveKit (24 consumed + 10 accrual)
        self.assertAlmostEqual(row["voice_usd"], 64.40)
        self.assertAlmostEqual(row["combined_usd"], 164.40)
        self.assertFalse(payload["voice_incomplete"])
        md = R.render_md(payload)
        self.assertIn("| $10.40 |", md)
        self.assertNotIn("| $0.00 | $34.00 |", md)


class ConsumerReadsWhatTheProducerWrites(unittest.TestCase):
    """Every column the Deepgram store query selects must exist in the store's schema."""

    def test_selected_columns_are_in_the_sink_schema(self):
        captured = []
        saved = R._bq
        R._bq = types.SimpleNamespace(query=lambda sql, p, params=None: captured.append(sql) or [])
        try:
            R._bq_deepgram_days(_d("2026-09-01"), _d("2026-09-02"), "p", "d")
        finally:
            R._bq = saved
        schema = set(re.findall(r'\("([a-z0-9_]+)",\s*"(?:FLOAT|DATE|STRING|INTEGER)"\)',
                                SINK.read_text()))
        cols = set(re.findall(r"COALESCE\((\w+),", captured[0]))
        self.assertTrue(cols, "no columns parsed from the query")
        self.assertLessEqual(cols, schema, f"not in the store schema: {cols - schema}")


if __name__ == "__main__":
    unittest.main()
