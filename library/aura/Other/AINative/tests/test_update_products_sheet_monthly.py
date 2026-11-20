"""data:update-products-sheet-monthly — plan and verify against the real Oct-26 run.

The fixtures are the actual Mixpanel Get-Report results for every row on board
6624751, and the actual reads of both tabs before and after the Oct-26 update
was written by hand on 2026-11-17. The planner must reproduce that run cell for
cell; the only allowed differences are the four values that drifted between the
hand pull and the board pull a day later.
"""
import datetime as dt
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "plugins/data/skills/update-products-sheet-monthly/scripts")
sys.path.insert(0, SCRIPTS)

import plan_update as pu  # noqa: E402
import verify_update as vu  # noqa: E402

FIX = os.path.join(ROOT, "tests/fixtures/products_sheet_monthly")
PM, CM = "Product Metrics", "Content & Marketplace"


def _registry():
    with open(os.path.join(SCRIPTS, "registry.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _reads(pm="pm_before.json", cm="cm_before.json"):
    return {
        PM: pu.TabGrid(PM, pu.load_json(os.path.join(FIX, pm)), "CO"),
        CM: pu.TabGrid(CM, pu.load_json(os.path.join(FIX, cm)), "CD"),
    }


def _plan(as_of="2026-11-17", reads=None):
    return pu.plan(_registry(), pu.load_reports(os.path.join(FIX, "reports")),
                   reads or _reads(), (2026, 10), dt.date.fromisoformat(as_of))


def _cells(plan):
    return {(c["tab"], c["cell"]): c for c in plan["cells"]}


class TestOct26Run(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = _plan()
        cls.cells = _cells(cls.plan)

    def test_one_new_column_per_tab_right_after_the_last_month(self):
        self.assertEqual(self.plan["tabs"][PM], {"prev_col": "CQ", "new_col": "CR"})
        self.assertEqual(self.plan["tabs"][CM], {"prev_col": "CE", "new_col": "CF"})

    def test_new_column_values(self):
        expected = {
            (PM, "CR1"): "Oct-26", (PM, "CR2"): "20,707", (PM, "CR4"): "16,308",
            (PM, "CR6"): "25,183", (PM, "CR8"): "9,166", (PM, "CR13"): "2,253",
            (PM, "CR19"): "21%", (PM, "CR20"): "20%", (PM, "CR21"): "51%",
            (PM, "CR22"): "21%", (PM, "CR23"): "7.36", (PM, "CR24"): "29.7",
            (PM, "CR33"): "9%", (PM, "CR34"): "15%", (PM, "CR35"): "56,915",
            (CM, "CF1"): "Oct-26", (CM, "CF2"): "20,895", (CM, "CF4"): "21,024",
            (CM, "CF6"): "418,491", (CM, "CF8"): "14,477,870", (CM, "CF10"): "14,491,273",
            (CM, "CF12"): "94", (CM, "CF13"): "698",
        }
        for key, want in expected.items():
            self.assertEqual(self.cells[key]["new"], want, key)

    def test_backfills_are_exactly_the_matured_cells(self):
        older = {k: c["new"] for k, c in self.cells.items()
                 if c["kind"] in ("backfill", "cohort")}
        self.assertEqual(older, {
            (PM, "CQ19"): "20%", (PM, "CQ20"): "20%",          # Sep conversion matured
            (PM, "CQ25"): "46%", (PM, "CQ26"): "53%",          # Sep cohort W4 organic
            (PM, "CQ29"): "42%", (PM, "CQ30"): "48%",          # Sep cohort W4 all
            (PM, "CO27"): "37%", (PM, "CO28"): "43%",          # Jul cohort W12 organic
            (PM, "CO31"): "34%", (PM, "CO32"): "39%",          # Jul cohort W12 all
        })

    def test_unchanged_backfills_are_not_rewritten(self):
        # Aug conversion (CP19/CP20) and Sep cancellation (CQ33/CQ34) re-pull to
        # the value already in the sheet, so they stay out of the plan.
        for ref in ("CP19", "CP20", "CQ33", "CQ34"):
            self.assertNotIn((PM, ref), self.cells)

    def test_hard_coded_rows_not_filled_this_month_are_cleared(self):
        # The copy of the previous column would otherwise carry notes, PMF or
        # retention values into the new month.
        for row in (15, 16, 17, 18, 25, 26, 27, 28, 29, 30, 31, 32):
            self.assertEqual(self.cells[(PM, f"CR{row}")]["kind"], "blank", row)
        for row in (14, 15):
            self.assertEqual(self.cells[(CM, f"CF{row}")]["kind"], "blank", row)

    def test_copy_comes_first_then_values(self):
        reqs = self.plan["requests"]
        self.assertIn("copyPaste", reqs[0])
        self.assertIn("copyPaste", reqs[1])
        self.assertTrue(all("updateCells" in r for r in reqs[2:]))

    def test_refund_rows_are_formulas(self):
        formulas = [r["updateCells"]["rows"][0]["values"][0].get("userEnteredValue", {}).get("formulaValue")
                    for r in self.plan["requests"] if "updateCells" in r]
        self.assertIn("=CQ25*1.15", formulas)
        self.assertIn("=CO31*1.15", formulas)

    def test_verify_against_the_real_sheet_finds_only_the_drift(self):
        problems = vu.verify(self.plan, _reads("pm_after.json", "cm_after.json"), _reads())
        cells = sorted(p.split(" (")[0] for p in problems)
        self.assertEqual(cells, ["Product Metrics CR23", "Product Metrics CR4",
                                 "Product Metrics CR6", "Product Metrics CR8"])


class TestGuards(unittest.TestCase):
    def test_refuses_a_month_that_already_has_a_column(self):
        with self.assertRaisesRegex(pu.PlanError, "already exists"):
            _plan(reads=_reads("pm_after.json", "cm_after.json"))

    def test_refuses_a_month_that_has_not_closed(self):
        with self.assertRaisesRegex(pu.PlanError, "has not closed"):
            _plan(as_of="2026-10-20")

    def test_immature_cohorts_are_left_for_next_run(self):
        p = _plan(as_of="2026-11-02")
        cells = _cells(p)
        self.assertNotIn((PM, "CQ25"), cells)
        self.assertTrue(any("not complete until 2026-11-04" in w for w in p["warnings"]))
        self.assertIn("provisional until 2026-11-14", cells[(PM, "CR33")]["note"])


class TestRules(unittest.TestCase):
    def test_week_belongs_to_the_month_holding_most_of_its_days(self):
        spec = {"row": 2, "label": "x", "report_id": 1, "rule": "weekly_second_highest"}
        rows = [["2026-09-27", 100], ["2026-10-04", 50], ["2026-10-11", 40],
                ["2026-10-18", 30], ["2026-10-25", 20], ["2026-11-01", 999]]
        report = {"id": 1, "results": {"s": {"headers": ["date", "value"], "rows": rows}}}
        v, _ = pu.value_for_month(spec, report, 2026, 10, dt.date(2026, 11, 17))
        self.assertEqual(v, 40)  # 9/27 is September's, 11/1 is November's

    def test_formats(self):
        self.assertEqual(pu.format_value(14491272.8, "integer"), (14491273, "14,491,273"))
        self.assertEqual(pu.format_value(46.83, "percent_points"), (0.47, "47%"))
        self.assertEqual(pu.format_value(0.205, "percent"), (0.21, "21%"))
        self.assertEqual(pu.format_value(29.68, "decimal1"), (29.7, "29.7"))
        self.assertEqual(pu.format_value(7.365, "decimal2"), (7.37, "7.37"))

    def test_pmf_only_in_its_quarter_months(self):
        spec = next(s for s in _registry()["rows"] if s["row"] == 16 and s["tab"] == PM)
        self.assertEqual(spec["only_months"], [2, 5, 8, 11])

    def test_column_letters_round_trip(self):
        for col in ("A", "Z", "AA", "CQ", "CR", "ZZ"):
            self.assertEqual(pu.idx_to_col(pu.col_to_idx(col)), col)


if __name__ == "__main__":
    unittest.main()
