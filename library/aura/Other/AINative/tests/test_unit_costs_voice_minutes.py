#!/usr/bin/env python3
"""voice_minutes counts each voice session once (PROD-7584).

On 2026-09-24 ConvAI was switched off and every voice session moved to LiveKit with
ElevenLabs as TTS only. `unit_costs.py` added ElevenLabs `minutes_used` (no breakdown)
to LiveKit session minutes, but ElevenLabs reports TTS minutes for the speech inside
those same sessions, so voice_minutes read ~10k/day against ~6k real and
cost_per_voice_min halved.

What this pins, against the real 2026-09-25 ElevenLabs product_type response:

  * voice_minutes = ConvAI minutes + LiveKit minutes, never + TTS minutes.
  * voice_cost still carries every ElevenLabs dollar (ConvAI + TTS) — the fix must not
    trade a minutes double count for a dollar drop.
  * cost_per_voice_min_11labs divides ConvAI $ by ConvAI minutes.
  * cost_per_voice_min_livekit keeps its 3-vendor basis (no ElevenLabs TTS):
    report-livekit-daily-cost inverts it to recover session minutes.
  * With no LiveKit minutes, TTS $ is LiveKit-path $ for the rate guard: the blended
    rate is ConvAI $ / ConvAI minutes, never ConvAI+TTS $ over ConvAI minutes.
  * An unknown ElevenLabs product lands in the agent bucket rather than vanishing.
  * The LiveKit-path $ and minutes come from the per-user store's booked day when it has
    one: store.py books the month-to-date overage and LiveKit Inference STT, which the
    vendor recompute (no prior minutes, Deepgram direct only) misses. Real 2026-10-05
    per-user sums: $93.06 booked vs ~$17 recomputed.
"""

from __future__ import annotations

import csv
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_elevenlabs_retry import Resp, _load  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/store-aurie-user-daily-cost/scripts"
U = _load(SCRIPTS / "unit_costs.py", "unit_costs_under_voice_minutes_test")

DAY, NXT = "2026-09-25", "2026-09-26"
# /v1/usage/character-stats?breakdown_type=product_type, 2026-09-25, as returned.
EL = {
    "fiat_units_spent": {"Conversational AI": [2587.7], "TTS": [19198.2]},
    "minutes_used": {"Conversational AI": [332.8], "TTS": [3539.8]},
}
LK_MIN, LK_USD, DG_USD = 5980.0, 59.8, 45.3


def _el_get(url, headers, params=None, **_):
    assert params["breakdown_type"] == "product_type", params
    return Resp(200, {"time": [0], "usage": EL[params["metric"]]})


class TestVoiceMinutes(unittest.TestCase):

    def _row(self, el=None, lk_min=LK_MIN, lk_usd=LK_USD, dg_usd=DG_USD, lk_path=None):
        el_get = _el_get
        if el is not None:
            el_get = lambda url, headers, params=None, **_: Resp(  # noqa: E731
                200, {"time": [0], "usage": el[params["metric"]]})
        projs = {"p1": "Conversation [Production]"}
        with mock.patch.object(U.store_mod, "_el_get", el_get), \
             mock.patch.object(U, "by_project", lambda *a: {"p1": 100.0}), \
             mock.patch.object(U.store_mod, "cartesia_day_total", lambda d, r: (0.0, "")), \
             mock.patch.object(U.store_mod, "deepgram_day_total", lambda d, r: (dg_usd, "")), \
             mock.patch.object(U.store_mod, "fish_day_total", lambda m, r: (0.0, "")), \
             mock.patch.object(U.store_mod, "inworld_day_total", lambda d, m, r: (0.0, "")), \
             mock.patch.object(U.store_mod, "livekit_costs",
                               lambda d, r: ({}, lk_min, lk_usd, "", {})):
            return U.build_row(DAY, projs, {}, {}, {"cartesia": 1, "fish": 1, "inworld": 1, "deepgram": 1,
                                                    "livekit": 1},
                               lk_path)

    def test_tts_minutes_are_not_added_to_livekit_sessions(self):
        r = self._row()
        self.assertAlmostEqual(r["voice_minutes"], 332.8 + LK_MIN, places=1)

    def test_every_elevenlabs_dollar_stays_in_voice_cost(self):
        r = self._row()
        self.assertAlmostEqual(r["voice_cost"], 25.877 + 191.982 + LK_USD + DG_USD, places=2)

    def test_blended_rate_uses_session_minutes(self):
        r = self._row()
        self.assertAlmostEqual(r["cost_per_voice_min"],
                               round((25.877 + 191.982 + LK_USD + DG_USD) / (332.8 + LK_MIN), 4))

    def test_convai_rate_is_convai_only(self):
        self.assertAlmostEqual(self._row()["cost_per_voice_min_11labs"], round(25.877 / 332.8, 4))

    def test_livekit_rate_keeps_its_three_vendor_basis(self):
        self.assertAlmostEqual(self._row()["cost_per_voice_min_livekit"],
                               round((LK_USD + DG_USD) / LK_MIN, 4))

    def test_an_unknown_product_is_kept_in_the_agent_bucket(self):
        el = {"fiat_units_spent": {"Conversational AI": [100.0], "TTS": [200.0], "Dubbing": [50.0]},
              "minutes_used": {"Conversational AI": [10.0], "TTS": [20.0], "Dubbing": [5.0]}}
        r = self._row(el)
        self.assertAlmostEqual(r["voice_minutes"], 15.0 + LK_MIN, places=1)
        self.assertAlmostEqual(r["voice_cost"], 3.5 + LK_USD + DG_USD, places=2)

    def test_no_livekit_minutes_keeps_tts_dollars_out_of_the_rate(self):
        r = self._row(lk_min=0.0, lk_usd=0.0, dg_usd=0.0)
        self.assertAlmostEqual(r["cost_per_voice_min"], round(25.877 / 332.8, 4))
        self.assertAlmostEqual(r["voice_cost"], 25.877 + 191.982, places=2)


# Real 2026-10-05 per-user sums (aurie_per_user_daily_cost): the store booked the LiveKit
# overage and Inference STT; deepgram_inference_drawdown is outside voice_cost.
STORE_ROWS = [
    {"date": DAY, "user_id": "u1", "voice_cartesia": "0", "voice_fish": "0", "voice_deepgram": "25.38",
     "deepgram_inference_drawdown": "25.38", "voice_livekit": "67.68", "voice_livekit_platform": "0",
     "voice_11labs_livekit": "166.7", "livekit_minutes": "6768"},
    {"date": "2026-09-24", "user_id": "u1", "voice_deepgram": "99", "voice_livekit": "99",
     "livekit_minutes": "99"},
]


class TestStoreBookedLivekitPath(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
        cols = sorted({k for r in STORE_ROWS for k in r})
        w = csv.DictWriter(self.tmp, fieldnames=cols)
        w.writeheader()
        w.writerows(STORE_ROWS)
        self.tmp.close()

    def tearDown(self):
        pathlib.Path(self.tmp.name).unlink()

    def test_sums_only_the_day_and_only_livekit_path_columns(self):
        usage, cost, minutes = U.store_livekit_path(DAY, self.tmp.name)
        self.assertAlmostEqual(cost, 25.38 + 67.68, places=2)
        self.assertAlmostEqual(usage, 25.38, places=2)
        self.assertAlmostEqual(minutes, 6768.0)

    def test_a_day_with_no_rows_is_none_not_zero(self):
        self.assertIsNone(U.store_livekit_path("2026-10-06", self.tmp.name))
        self.assertIsNone(U.store_livekit_path(DAY, "/nonexistent.csv"))

    def test_build_row_uses_the_booked_day_over_the_vendor_recompute(self):
        lk = U.store_livekit_path(DAY, self.tmp.name)
        r = TestVoiceMinutes._row(self, lk_path=lk)
        self.assertAlmostEqual(r["voice_cost"], 25.877 + 191.982 + 25.38 + 67.68, places=2)
        self.assertAlmostEqual(r["voice_minutes"], 332.8 + 6768.0, places=1)
        self.assertAlmostEqual(r["cost_per_voice_min_livekit"], round((25.38 + 67.68) / 6768.0, 4))


class TestForceKeepsCohortColumns(unittest.TestCase):
    """A --force re-store of a day whose walk JSON is gone must not blank the stored
    cost_per_msg_paying / _free: they don't depend on anything the re-store changes."""

    def test_source_keeps_prior_cohort_columns(self):
        src = (SCRIPTS / "unit_costs.py").read_text()
        self.assertIn("if not (pr_ or fr_) and d in prior_rows:", src)
        self.assertIn('if r["date"] in forced}', src)


if __name__ == "__main__":
    unittest.main()
