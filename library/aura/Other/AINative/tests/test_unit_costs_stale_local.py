#!/usr/bin/env python3
"""unit_costs.py refuses a day whose local per-user rows differ from BigQuery (ENG-1864).

`unit_costs.py` reads each day's LiveKit-path $, minutes and cohorts from the LOCAL per-user
CSV. `rebook_livekit.py --confirm` rewrites the per-user store in BigQuery only, so a
`unit_costs.py --force` run before `bq_pull.py` computes unit costs from the pre-rebook
dollars, and `bq_sink.py` then publishes them. Found while booking the October 2026 LiveKit
statement (ENG-1860); that run happened to pull first.

These tests drive `unit_costs.main()` with BigQuery stubbed at its query chokepoint.
Stdlib only, no network, no keys.
"""
from __future__ import annotations

import contextlib
import csv
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_vendor_read_failure import unit_costs, store  # noqa: E402  (google stubbed)

DAY = "2026-10-20"


class _Reached(Exception):
    """Raised by the first call after the guard: the run was NOT refused."""


class StaleLocalCopy(unittest.TestCase):

    def _run(self, *, local_rows, bq_rows, bq_sum):
        tmp = tempfile.mkdtemp()
        user_csv = os.path.join(tmp, "per_user.csv")
        with open(user_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=store.COLS)
            w.writeheader()
            for i, v in enumerate(local_rows):
                w.writerow({c: "" for c in store.COLS} | {"date": DAY, "user_id": f"u{i}",
                                                           "voice_cost": v, "total_cost": v})
        bq = types.ModuleType("bq")
        bq.BQUnavailable = type("BQUnavailable", (Exception,), {})
        bq.BQError = type("BQError", (Exception,), {})
        bq.PROJECT, bq.DATASET = "p", "d"
        bq.table_exists = lambda *a, **k: True

        def query(sql, project, params=None, **_kw):
            if "COUNT(*)" in sql:
                return [{"d": DAY, "n": bq_rows, "v": bq_sum}]
            if "per_user" in sql:
                return [{"d": DAY}]
            return []
        bq.query = query
        bq_sink = types.ModuleType("bq_sink")
        bq_sink.STORES = {"per_user": {"table": "per_user"}, "unit_costs": {"table": "unit"}}
        with contextlib.ExitStack() as st:
            st.enter_context(mock.patch.dict(sys.modules, {"bq": bq, "bq_sink": bq_sink}))
            st.enter_context(mock.patch.dict(os.environ, {"OPENAI_ADMIN_API_KEY": "x",
                                                          "ELEVENLABS_ADMIN_API_KEY": "x"}))
            st.enter_context(mock.patch.object(unit_costs, "project_names", side_effect=_Reached))
            st.enter_context(mock.patch.object(sys, "argv", [
                "unit_costs.py", "--date", DAY, "--force", "--user-csv", user_csv,
                "--csv", os.path.join(tmp, "unit.csv"), "--kpi-csv", os.path.join(tmp, "kpi.csv")]))
            unit_costs.main()

    def test_a_day_rebooked_in_bigquery_but_not_pulled_is_refused(self):
        # BigQuery holds the rebooked day ($4.40 of voice); the CSV the pre-rebook one ($5.10).
        with self.assertRaises(SystemExit) as cm:
            self._run(local_rows=[3.0, 2.1], bq_rows=2, bq_sum=4.40)
        msg = str(cm.exception.code)
        self.assertIn("REFUSING", msg)
        self.assertIn("local 2 rows $5.10, BigQuery 2 rows $4.40", msg)
        # Either side can be the newer one (a rebook rewrites BigQuery, store.py --force the
        # CSV), so both fixes are named and neither is chosen for the operator.
        self.assertIn(f"bq_pull.py --from {DAY} --to {DAY}", msg)
        self.assertIn("bq_sink.py --date <d>", msg)
        self.assertIn("store.py --force", msg)

    def test_a_different_row_count_is_refused_too(self):
        with self.assertRaises(SystemExit):
            self._run(local_rows=[3.0, 2.1], bq_rows=3, bq_sum=5.10)

    def test_a_pulled_copy_runs(self):
        with self.assertRaises(_Reached):
            self._run(local_rows=[3.0, 2.1], bq_rows=2, bq_sum=5.1000004)


class StaleDays(unittest.TestCase):
    def test_only_days_bigquery_holds_are_compared(self):
        self.assertEqual(unit_costs.stale_per_user_days({"2026-10-01": (2, 1.0)}, {}), [])
        stale = unit_costs.stale_per_user_days({}, {"2026-10-01": (2, 1.0)})
        self.assertEqual(len(stale), 1)
        self.assertIn("local 0 rows", stale[0])


if __name__ == "__main__":
    unittest.main()
