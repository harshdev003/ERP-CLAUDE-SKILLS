#!/usr/bin/env python3
"""Attribution materiality — the note must not claim an outage that didn't happen.

The defect this guards
----------------------
`analyze-openai-cost-per-user` reaches its per-user split by two different
non-ledger routes, and they mean opposite things:

- `completions-fallback` — a ledger read RAISED. Post-epoch the stored-completions
  fallback cannot see Responses-API traffic, so those dollars are split on partial
  data. This is the condition ENG-1054 fails closed on.
- `completions-non-ledger` — the project is not in `LEDGER_PROJECT_KEYS`. That is a
  permanent property of the project, not a failure, and there is nothing to retry.

`build_attribution_note` tested them together (`sources_used & {both}`) and then
emitted `fallback_note`, whose text asserts "ledger unavailable". So a run whose
only non-ledger spend was a rounding-level amount announced an outage.

That is not hypothetical. On 2026-08-09 the marker was set by `Front` at $0.01 plus
four projects at $0.00, while Conversation ($154.92), Insights ($102.80), Memory
($54.81), Profile ($20.33), Feed, Notification, Summary, Journal and Onboarding all
went through the ledger — $350.11 of $350.12 ledger-attributed. The day was
withheld from `aurie_per_user_daily_cost` on the strength of that sentence.

Why a share and not a flag
--------------------------
`sources` is a set of strings, so the only test a consumer could write was "is this
marker present", which cannot distinguish one cent from two hundred dollars. The
fix adds dollars-per-class, and the store gates on the *degraded share*. The tests
below pin both halves: that the note stops lying, and that the store refuses a
genuinely degraded day while accepting a trivially non-ledger one.

Failure injection is the point — a live window cannot produce a ledger outage on
demand, so the source sets and dollar splits here are synthesised directly.
"""
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

REPO = pathlib.Path(__file__).resolve().parents[1]
REPORT = REPO / "plugins/eng/skills/analyze-openai-cost-per-user/scripts/report.py"

# `store.py` is loaded through the sibling suite's stubbed-google loader rather
# than a second one here. It imports `livekit_minutes`, which does
# `from google.cloud import logging` at module scope and `sys.exit(2)` when the
# SDK is missing — and CI installs only `requests`. A loader that swallows
# SystemExit therefore yields a HALF-INITIALISED module: import stops at the
# sibling import, every later definition is absent, and the failure surfaces as
# `AttributeError: module has no attribute ...` rather than as an import error.
# That is exactly what happened on the first CI run of this file (8 errors, green
# locally, because this machine has google-cloud-logging installed). Reuse the
# one loader that already handles it.
#
# `tests/` is on sys.path under `unittest discover -s tests` (CI) but not under
# `python3 -m unittest tests.test_attribution_materiality`, so seed it explicitly
# rather than only working in one of the two invocation styles.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_el_caller_mapping import _load_store  # noqa: E402


def _load_report():
    """Load report.py. Deliberately does NOT catch SystemExit — see above."""
    spec = importlib.util.spec_from_file_location("rep_attr", REPORT)
    mod = importlib.util.module_from_spec(spec)
    argv = sys.argv
    sys.argv = ["rep_attr"]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = argv
    return mod


rep = _load_report()
POST = rep.LEDGER_EPOCH_UNIX + 10 * 86400          # safely past the ramp
POST_END = POST + 86400


class NoteDoesNotInventAnOutage(unittest.TestCase):
    """`completions-non-ledger` alone must never produce outage wording."""

    def test_non_ledger_only_does_not_say_ledger_unavailable(self):
        note = rep.build_attribution_note(POST, POST_END, {"completions-non-ledger"})
        self.assertNotIn("ledger unavailable", note)
        self.assertIn("by design", note)

    def test_mixed_ledger_and_non_ledger_does_not_say_ledger_unavailable(self):
        """The exact 2026-08-09 shape: ledger for the money, non-ledger for $0.01."""
        note = rep.build_attribution_note(
            POST, POST_END, {"ledger", "completions-non-ledger"}
        )
        self.assertNotIn("ledger unavailable", note)
        self.assertIn("by design", note)

    def test_real_fallback_still_says_ledger_unavailable(self):
        """The guard must not be defanged — a true outage keeps its wording."""
        note = rep.build_attribution_note(
            POST, POST_END, {"ledger", "completions-fallback"}
        )
        self.assertIn("ledger unavailable", note)

    def test_both_conditions_are_reported_separately(self):
        note = rep.build_attribution_note(
            POST, POST_END, {"ledger", "completions-fallback", "completions-non-ledger"}
        )
        self.assertIn("ledger unavailable", note)
        self.assertIn("by design", note)

    def test_pure_ledger_run_mentions_neither(self):
        note = rep.build_attribution_note(POST, POST_END, {"ledger"})
        self.assertNotIn("ledger unavailable", note)
        self.assertNotIn("by design", note)


class PrimaryClassPrecedence(unittest.TestCase):
    """Worst-first, so materiality errs toward flagging."""

    def test_fallback_beats_ledger(self):
        self.assertEqual(
            rep._primary_attribution_class({"ledger", "completions-fallback"}),
            "completions-fallback",
        )

    def test_non_ledger_beats_ledger(self):
        self.assertEqual(
            rep._primary_attribution_class({"ledger", "completions-non-ledger"}),
            "completions-non-ledger",
        )

    def test_fallback_beats_non_ledger(self):
        self.assertEqual(
            rep._primary_attribution_class(
                {"completions-non-ledger", "completions-fallback"}
            ),
            "completions-fallback",
        )

    def test_ramp_union_does_not_change_the_class(self):
        self.assertEqual(
            rep._primary_attribution_class({"ledger", "ledger-ramp-union"}), "ledger"
        )


class MaterialityShares(unittest.TestCase):
    def test_the_2026_08_09_split_is_immaterial(self):
        m = rep.summarize_attribution_materiality(
            {"ledger": 350.11, "completions-non-ledger": 0.01}
        )
        self.assertEqual(m["degraded_share_pct"], 0.0)
        self.assertLess(m["non_ledger_share_pct"], 0.01)

    def test_a_real_outage_shows_a_large_degraded_share(self):
        m = rep.summarize_attribution_materiality(
            {"ledger": 100.0, "completions-fallback": 250.0}
        )
        self.assertGreater(m["degraded_share_pct"], 70.0)

    def test_zero_attributed_does_not_divide_by_zero(self):
        m = rep.summarize_attribution_materiality({})
        self.assertEqual(m["attributed_dollars"], 0.0)
        self.assertEqual(m["degraded_share_pct"], 0.0)

    def test_skipped_projects_are_excluded_from_the_denominator(self):
        m = rep.summarize_attribution_materiality(
            {"ledger": 100.0, "none": 900.0}
        )
        self.assertEqual(m["attributed_dollars"], 100.0)

    def test_note_carries_the_dollar_figure(self):
        m = rep.summarize_attribution_materiality(
            {"ledger": 350.11, "completions-non-ledger": 0.01}
        )
        note = rep.build_attribution_note(
            POST, POST_END, {"ledger", "completions-non-ledger"}, m
        )
        self.assertIn("$0.01", note)


class StoreRefusesOnlyMaterialDegradation(unittest.TestCase):
    """The writer is the last line of defence — it must gate on the share."""

    def test_the_module_loaded_fully(self):
        """Guard the half-initialised-module trap described at the top of this
        file: a partially-executed store.py fails every other case below with a
        confusing AttributeError instead of naming the real cause."""
        self.assertTrue(hasattr(_load_store(), "assert_attribution_usable"))

    def setUp(self):
        self.store = _load_store()

    def _walk(self, dollars_by_class, sources):
        return {
            "attribution": {
                "sources": sorted(sources),
                "materiality": rep.summarize_attribution_materiality(dollars_by_class),
            }
        }

    def test_accepts_the_2026_08_09_day(self):
        """The regression that motivated this: $0.01 non-ledger must not block."""
        j = self._walk(
            {"ledger": 350.11, "completions-non-ledger": 0.01},
            {"ledger", "completions-non-ledger"},
        )
        with redirect_stderr(io.StringIO()):
            self.store.assert_attribution_usable(j)   # must not raise

    def test_refuses_a_material_fallback(self):
        j = self._walk(
            {"ledger": 100.0, "completions-fallback": 250.0},
            {"ledger", "completions-fallback"},
        )
        with self.assertRaises(SystemExit) as ctx:
            self.store.assert_attribution_usable(j)
        self.assertIn("REFUSING to store", str(ctx.exception))

    def test_override_persists_a_degraded_day_deliberately(self):
        j = self._walk(
            {"ledger": 100.0, "completions-fallback": 250.0},
            {"ledger", "completions-fallback"},
        )
        with redirect_stderr(io.StringIO()):
            self.store.assert_attribution_usable(j, allow_degraded=True)

    def test_a_rounding_level_fallback_is_tolerated(self):
        """A ledger-served project can bill a cent; that is not an outage worth
        discarding a day over."""
        j = self._walk(
            {"ledger": 350.0, "completions-fallback": 0.01},
            {"ledger", "completions-fallback"},
        )
        with redirect_stderr(io.StringIO()):
            self.store.assert_attribution_usable(j)

    def test_pre_eng1114_json_without_materiality_still_refuses_a_fallback(self):
        """Old walk JSON carries no shares — fall back to the coarse marker rather
        than silently accepting."""
        with self.assertRaises(SystemExit):
            self.store.assert_attribution_usable(
                {"attribution": {"sources": ["ledger", "completions-fallback"]}}
            )

    def test_pre_eng1114_json_with_only_non_ledger_is_accepted(self):
        self.store.assert_attribution_usable(
            {"attribution": {"sources": ["ledger", "completions-non-ledger"]}}
        )

    def test_absent_attribution_block_is_accepted(self):
        """Pre-attribution JSON (very old) must not become unreadable."""
        self.store.assert_attribution_usable({})

    def test_llm_costs_applies_the_gate(self):
        """The check has to sit on the real read path, not just be importable."""
        j = self._walk(
            {"ledger": 10.0, "completions-fallback": 90.0},
            {"ledger", "completions-fallback"},
        )
        j["users_all"] = [{"uid": "u1", "amount": 1.0, "top_project": "x"}]
        j["total"] = 100.0
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(j, f)
            path = f.name
        with self.assertRaises(SystemExit):
            self.store.llm_costs(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
