"""PROD-6242 — the store-vs-warehouse preflight refuses drift and reports divergence.

The guard exists because the `voice_fish` column was added to the store and never
to BigQuery: every partition load 400'd, `bq_sink.py` printed it and exited
non-zero, and the days kept accumulating locally for three days while every
`chart-aurie-*` read of the `_prod` view stayed clean, plausible and understated.

Per /CLAUDE.md -> "Verify the path production takes, not one that resembles it",
the drift fixture is built from the **real producer's output**: the local column
list comes from `bq_sink.STORES[*]["schema"]` and the warehouse list is that same
list with a column removed — which is exactly the shape the incident had (the
warehouse was the store's schema minus `voice_fish`). A hand-typed column list
would let the test pass against a schema the store can no longer emit.
"""
import os
import pathlib
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "plugins", "eng", "skills", "store-aurie-user-daily-cost", "scripts")
sys.path.insert(0, SCRIPTS)

import bq_preflight  # noqa: E402
import bq_sink  # noqa: E402


def _probe_input(store_key, *, drop=None, add=None, local_max=None, warehouse_max=None):
    """One store's probe entry, sourced from the real declared schema."""
    spec = bq_sink.STORES[store_key]
    local = [n for n, _ in spec["schema"]]
    warehouse = [c for c in local if c != drop] + ([add] if add else [])
    return {
        "table": spec["table"],
        "missing_table": False,
        "local_cols": local,
        "warehouse_cols": warehouse,
        "local_max": local_max,
        "warehouse_max": warehouse_max,
    }


def _all_stores(**overrides):
    base = {k: _probe_input(k, local_max="2026-09-19", warehouse_max="2026-09-19")
            for k in bq_sink.STORES}
    base.update(overrides)
    return base


class TestSchemaDriftRefuses(unittest.TestCase):
    def test_the_voice_fish_incident_refuses(self):
        """A column the store declares and BigQuery lacks stops the run."""
        report = bq_preflight.build_report(_all_stores(
            per_user=_probe_input("per_user", drop="voice_fish",
                                  local_max="2026-09-19", warehouse_max="2026-09-17")))
        self.assertTrue(report["refuse"])
        self.assertEqual(report["stores"]["per_user"]["local_only"], ["voice_fish"])
        joined = " ".join(report["reasons"])
        self.assertIn("voice_fish", joined)
        self.assertIn("--recreate", joined, "the refusal must name the remedy, not just the fault")

    def test_voice_fish_is_still_a_declared_column(self):
        """The fixture is only meaningful while the store really declares it."""
        self.assertIn("voice_fish", [n for n, _ in bq_sink.STORES["per_user"]["schema"]])

    def test_drift_on_any_of_the_three_stores_refuses(self):
        for key in bq_sink.STORES:
            victim = [n for n, _ in bq_sink.STORES[key]["schema"]][-1]
            with self.subTest(store=key, column=victim):
                report = bq_preflight.build_report(_all_stores(**{key: _probe_input(key, drop=victim)}))
                self.assertTrue(report["refuse"])

    def test_a_warehouse_only_column_does_not_refuse(self):
        """The load names its columns, so an extra warehouse column is never written."""
        report = bq_preflight.build_report(_all_stores(
            per_user=_probe_input("per_user", add="some_retired_column")))
        self.assertFalse(report["refuse"])
        self.assertEqual(report["stores"]["per_user"]["warehouse_only"], ["some_retired_column"])

    def test_an_absent_table_is_not_drift(self):
        """A missing table is created on first write — not a reason to refuse."""
        entry = _probe_input("per_user")
        entry.update(missing_table=True, warehouse_cols=[], warehouse_max=None)
        report = bq_preflight.build_report(_all_stores(per_user=entry))
        self.assertFalse(report["refuse"])

    def test_the_live_schemas_agree_with_themselves(self):
        """No drift when the warehouse mirrors the declared schema exactly."""
        report = bq_preflight.build_report(_all_stores())
        self.assertFalse(report["refuse"])
        self.assertEqual(report["reasons"], [])


class TestMaxDateDivergence(unittest.TestCase):
    def test_the_mixed_state_is_surfaced(self):
        """Per-user at 09-17 while the other two reached 09-19 — the real symptom."""
        report = bq_preflight.build_report(_all_stores(
            per_user=_probe_input("per_user", local_max="2026-09-19", warehouse_max="2026-09-17")))
        self.assertTrue(report["tables_divergent"])
        self.assertIn("per_user", report["behind"])
        rendered = bq_preflight.render(report)
        self.assertIn("MIXED STATE", rendered)
        self.assertIn("BEHIND", rendered)

    def test_a_warehouse_behind_its_store_is_reported_without_refusing(self):
        """Divergence has benign causes (a store with no rows for a date); drift does not."""
        report = bq_preflight.build_report(_all_stores(
            per_user=_probe_input("per_user", local_max="2026-09-19", warehouse_max="2026-09-18"),
            unit_costs=_probe_input("unit_costs", local_max="2026-09-19", warehouse_max="2026-09-18"),
            eng_kpis=_probe_input("eng_kpis", local_max="2026-09-19", warehouse_max="2026-09-18"),
        ))
        self.assertFalse(report["refuse"], "a gap must not refuse — only schema drift does")
        self.assertEqual(set(report["behind"]), set(bq_sink.STORES))
        self.assertFalse(report["tables_divergent"], "all three agree, so there is no mixed state")

    def test_aligned_tables_report_no_divergence(self):
        report = bq_preflight.build_report(_all_stores())
        self.assertFalse(report["tables_divergent"])
        self.assertEqual(report["behind"], {})

    def test_a_warehouse_ahead_of_its_store_is_not_behind(self):
        """Only the warehouse lagging matters — the store is the rebuildable copy."""
        report = bq_preflight.build_report(_all_stores(
            per_user=_probe_input("per_user", local_max="2026-09-17", warehouse_max="2026-09-19")))
        self.assertEqual(report["behind"], {})

    def test_every_store_gets_a_plan_line(self):
        """The cadence pastes render() into its 'what's due' block — one line per table."""
        rendered = bq_preflight.render(build := bq_preflight.build_report(_all_stores()))
        for key, spec in bq_sink.STORES.items():
            self.assertIn(spec["table"], rendered, f"{key} has no plan line")
            self.assertIn("store max", rendered)
        self.assertEqual(set(build["stores"]), set(bq_sink.STORES))


class TestExitCodes(unittest.TestCase):
    def test_the_three_codes_are_distinct(self):
        """A probe that could not run must not read as a clean one."""
        codes = {bq_preflight.EXIT_OK, bq_preflight.EXIT_REFUSE, bq_preflight.EXIT_UNRESOLVABLE}
        self.assertEqual(len(codes), 3)
        self.assertEqual(bq_preflight.EXIT_OK, 0)


class TestSinkIsGated(unittest.TestCase):
    def test_the_sink_calls_the_probe_and_exempts_recreate(self):
        """The guard is worth nothing unless main() actually runs it."""
        src = pathlib.Path(SCRIPTS, "bq_sink.py").read_text()
        self.assertIn("bq_preflight.probe", src, "bq_sink.main() must call the probe")
        self.assertIn("if not args.recreate:", src, "--recreate is the remedy and must not be gated")


class TestRemedyIsRunnable(unittest.TestCase):
    """The refusal's whole value is that the operator can act on it."""

    def test_the_hint_points_at_a_bq_sink_that_exists(self):
        """A repo-relative hint does not resolve from the plugin install cache."""
        self.assertIn("--recreate", bq_preflight.RECREATE_HINT)
        script = bq_preflight.RECREATE_HINT.split()[1]
        self.assertTrue(os.path.isabs(script), "the hint must not be repo-relative")
        self.assertTrue(os.path.exists(script), f"the hint names a file that does not exist: {script}")
        self.assertEqual(os.path.dirname(script), SCRIPTS,
                         "the remedy must be the bq_sink.py next to this preflight")


if __name__ == "__main__":
    unittest.main()
