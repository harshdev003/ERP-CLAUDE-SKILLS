"""The LiveKit leg of report-user-costs: pending is not zero, and nothing counts twice (ENG-1412).

`eng:report-livekit-user-cost` sums a user's LiveKit-path columns from the per-user daily
cost store. Two rules carry the whole leg, and both fail silently:

  * A day the store has not written yet (today, or a store gap) is PENDING. Rendering it as
    $0.00 is how a canceller's same-day LiveKit session vanished into a low total. The
    load-bearing assertions are the NEGATIVE ones: a pending window must not render "$0.00",
    must not carry a numeric total, and a genuinely quiet stored day must still render $0.00
    (an implementation that blanks every zero passes the first half and fails that).

  * `voice_11labs_livekit` is counted by this leg and never by the ElevenLabs leg. The store
    splits ElevenLabs by calling API key; the ElevenLabs leg walks ConvAI only, which is the
    keyless slice. The schema test reads the store's own column list, so a new LiveKit vendor
    column (voice_fish was one) fails here until it is classified.

BigQuery is stubbed at `fetch()`'s injected `query` — no network, no keys.
"""

from __future__ import annotations

import ast
import re
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SKILLS = REPO / "plugins/eng/skills"
REPORT = SKILLS / "report-livekit-user-cost/scripts/report.py"
STORE = SKILLS / "store-aurie-user-daily-cost/scripts/store.py"
EL_REPORT = SKILLS / "report-elevenlabs-user-cost/scripts/report.py"


def _load():
    spec = importlib.util.spec_from_file_location("livekit_user_cost_report", REPORT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


R = _load()
UID = "TestUid0000000000000000000001"


def _win(frm: str, to: str) -> dict:
    return {"from": frm, "to": to, "from_iso": f"{frm}T00:00:00Z", "to_iso": f"{to}T00:00:00Z",
            "label": f"{frm} → {to}", "days_in": 1}


def _fake_query(stored_dates, user_rows):
    """A bq.query stand-in answering the two queries fetch() issues."""
    calls = []

    def query(sql, params=None):
        calls.append((sql, params))
        if "DISTINCT date" in sql:
            return [{"date": d} for d in stored_dates]
        assert params["u"] == UID
        return [dict(r) for r in user_rows]

    query.calls = calls
    return query


def _run(win, stored, rows):
    dates = R.window_dates(win)
    q = _fake_query(stored, rows)
    s, r = R.fetch(UID, dates[0], dates[-1], q)
    return R.build_report(UID, win, dates, s, r), q


def _row(date, **cols):
    base = {c: 0.0 for c in R.LIVEKIT_COST_COLUMNS}
    base.update({"date": date, "livekit_minutes": 0.0})
    base.update(cols)
    return base


class TestPendingIsNotZero(unittest.TestCase):

    def test_unstored_window_is_pending_never_zero(self):
        report, _ = _run(_win("2026-09-25", "2026-09-26"), stored=[], rows=[])
        self.assertEqual(report["status"], "pending")
        self.assertIsNone(report["total"]["cost_usd"])
        self.assertIsNone(report["total"]["livekit_minutes"])
        self.assertEqual(report["pending_dates"], ["2026-09-25"])
        md = R.render_markdown(report)
        self.assertIn("pending", md)
        self.assertNotIn("$0.00", md)

    def test_stored_day_without_a_user_row_is_a_real_zero(self):
        # The negative case for any "blank every zero" implementation.
        report, _ = _run(_win("2026-09-24", "2026-09-25"), stored=["2026-09-24"], rows=[])
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["total"]["cost_usd"], 0.0)
        self.assertEqual(report["pending_dates"], [])
        md = R.render_markdown(report)
        self.assertIn("$0.00", md)
        self.assertNotIn("pending", md)

    def test_mixed_window_totals_stored_days_and_names_pending_ones(self):
        rows = [_row("2026-09-23", voice_livekit=0.09, voice_cartesia=0.21, livekit_minutes=9.06)]
        report, _ = _run(_win("2026-09-23", "2026-09-26"),
                         stored=["2026-09-23", "2026-09-24"], rows=rows)
        self.assertEqual(report["status"], "partial")
        self.assertAlmostEqual(report["total"]["cost_usd"], 0.30)
        self.assertEqual(report["total"]["stored_days"], 2)
        self.assertEqual(report["pending_dates"], ["2026-09-25"])
        by_day = {d["date"]: d for d in report["by_day"]}
        self.assertEqual(by_day["2026-09-24"]["cost_usd"], 0.0)
        self.assertIsNone(by_day["2026-09-25"]["cost_usd"])
        md = R.render_markdown(report)
        self.assertIn("$0.30", md)
        self.assertIn("1d pending", md)
        self.assertIn("2026-09-25", md)

    def test_a_gap_inside_the_window_is_pending_too(self):
        report, _ = _run(_win("2026-09-20", "2026-09-23"),
                         stored=["2026-09-20", "2026-09-22"], rows=[])
        self.assertEqual(report["pending_dates"], ["2026-09-21"])

    def test_partial_last_day_counts(self):
        win = {"from": "2026-09-24", "to": "2026-09-25", "from_iso": "2026-09-24T00:00:00Z",
               "to_iso": "2026-09-25T14:03:00Z", "label": "x", "days_in": 2}
        self.assertEqual(R.window_dates(win), ["2026-09-24", "2026-09-25"])

    def test_partial_first_day_note_only_on_a_mid_day_start(self):
        report, _ = _run(_win("2026-09-24", "2026-09-25"), stored=["2026-09-24"], rows=[])
        self.assertFalse(any("partial first day" in n for n in report["notes"]))
        win = dict(_win("2026-09-24", "2026-09-25"), from_iso="2026-09-24T14:03:00Z")
        report, _ = _run(win, stored=["2026-09-24"], rows=[])
        self.assertTrue(any("partial first day" in n for n in report["notes"]))

    def test_missing_credential_degrades_and_names_the_leg(self):
        env = {k: v for k, v in os.environ.items() if k not in ("GCP_KEY",)}
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "lk.json"
            proc = subprocess.run(
                [sys.executable, str(REPORT), UID, "--from", "2026-09-01", "--to", "2026-09-02",
                 "--json", str(out)],
                capture_output=True, text=True, env=env, timeout=60,
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn("unavailable", proc.stdout)
            self.assertNotIn("$0.00", proc.stdout)
            data = json.loads(out.read_text())
        self.assertEqual(data["status"], "unavailable")
        self.assertIsNone(data["total"]["cost_usd"])


class TestNoDoubleCount(unittest.TestCase):

    OTHER_LEGS_OR_NOBODY = {
        "voice_11labs_agent",          # eng:report-elevenlabs-user-cost (ConvAI walk)
        "voice_11labs_intelligence",   # booked on the Other row only
        "voice_cost",                  # the sum column
    }

    def _store_voice_columns(self) -> set[str]:
        tree = ast.parse(STORE.read_text(encoding="utf-8"))
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and getattr(node.targets[0], "id", None) == "COLS"):
                cols = ast.literal_eval(node.value)
                return {c for c in cols if c.startswith("voice_")}
        self.fail("store.py no longer defines COLS")

    def test_every_store_voice_column_is_classified_exactly_once(self):
        voice = self._store_voice_columns()
        mine = set(R.LIVEKIT_COST_COLUMNS)
        self.assertFalse(mine & self.OTHER_LEGS_OR_NOBODY)
        self.assertEqual(voice, mine | self.OTHER_LEGS_OR_NOBODY,
                         "a store voice column is unclassified — decide which leg owns it")

    def test_voice_11labs_livekit_is_this_legs(self):
        self.assertIn("voice_11labs_livekit", R.LIVEKIT_COST_COLUMNS)
        rows = [_row("2026-09-24", voice_11labs_livekit=1.25)]
        report, q = _run(_win("2026-09-24", "2026-09-25"), stored=["2026-09-24"], rows=rows)
        self.assertEqual(report["total"]["cost_usd"], 1.25)
        user_sql = q.calls[1][0]
        self.assertIn("voice_11labs_livekit", user_sql)
        self.assertNotIn("voice_11labs_agent", user_sql)
        self.assertNotIn("voice_cost", user_sql)

    def test_elevenlabs_leg_sees_only_convai(self):
        """The ElevenLabs leg must stay on ConvAI, or voice_11labs_livekit is counted twice.

        Live: the ConvAI conversations walk only, never raw-TTS usage. Store (pre-retention
        days, ENG-1415): the ConvAI column only.
        """
        src = EL_REPORT.read_text(encoding="utf-8")
        self.assertIn("/convai/conversations", src)
        for raw_tts in ("text-to-speech", "/history", "character-stats"):
            self.assertNotIn(raw_tts, src, f"ElevenLabs leg now reads {raw_tts!r}")
        store_cols = set(re.findall(r"\bvoice_\w+", src))
        self.assertEqual(store_cols, {"voice_11labs_agent"},
                         "the ElevenLabs leg may read only the ConvAI column of the store")


if __name__ == "__main__":
    unittest.main()
