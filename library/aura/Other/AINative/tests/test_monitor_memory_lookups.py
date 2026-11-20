"""eng:monitor-memory-lookups: the drop rule and the two reads, with no network (PROD-8674)."""

import datetime as dt
import importlib.util
import json
import pathlib
import subprocess
import sys
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "plugins/eng/skills/monitor-memory-lookups/scripts"


def _load():
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("memory_lookups_monitor", SCRIPTS / "monitor.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(SCRIPTS))


M = _load()
DAYS = M.window(dt.date(2026, 11, 19))


def counts(shares, calls=30_000):
    c = {d: calls for d in DAYS}
    return c, {d: round(s * calls) for d, s in zip(DAYS, shares)}


class Rule(unittest.TestCase):
    def test_the_window_is_eight_full_days_ending_yesterday(self):
        self.assertEqual(DAYS[0], "2026-11-11")
        self.assertEqual(DAYS[-1], "2026-11-18")
        self.assertEqual(len(DAYS), 8)

    def test_a_steady_low_baseline_does_not_alert(self):
        v = M.evaluate(DAYS, *counts([0.0026] * 8))
        self.assertFalse(v["alert"])

    def test_the_oct_8_drop_alerts(self):
        v = M.evaluate(DAYS, *counts([0.063] * 7 + [0.006]))
        self.assertTrue(v["alert"])
        self.assertEqual(v["reason"], "under half the prior 7-day median")

    def test_just_above_half_does_not_alert(self):
        v = M.evaluate(DAYS, *counts([0.02] * 7 + [0.0101]))
        self.assertFalse(v["alert"])

    def test_a_rise_never_alerts(self):
        v = M.evaluate(DAYS, *counts([0.0026] * 7 + [0.06]))
        self.assertFalse(v["alert"])

    def test_zero_lookups_on_a_busy_day_alerts_even_without_a_baseline(self):
        calls, lookups = counts([0.0] * 8)
        v = M.evaluate(DAYS, calls, lookups)
        self.assertTrue(v["alert"])
        self.assertEqual(v["reason"], "no memory lookups")

    def test_a_quiet_day_never_alerts(self):
        calls, lookups = counts([0.05] * 7 + [0.0])
        calls[DAYS[-1]] = M.MIN_CALLS - 1
        self.assertFalse(M.evaluate(DAYS, calls, lookups)["alert"])

    def test_days_without_calls_are_left_out_of_the_median(self):
        calls, lookups = counts([0.05] * 7 + [0.02])
        for d in DAYS[:3]:
            calls[d], lookups[d] = 0, 0
        v = M.evaluate(DAYS, calls, lookups)
        self.assertEqual(v["prior_days"], 4)
        self.assertTrue(v["alert"])

    def test_no_ledger_rows_for_yesterday_is_unmeasured_not_a_zero(self):
        calls, lookups = counts([0.003] * 8)
        del calls[DAYS[-1]]
        with self.assertRaises(M.Unmeasured):
            M.evaluate(DAYS, calls, lookups)


class Fake:
    def __init__(self, rc=0, stdout="[]", stderr=""):
        self.returncode, self.stdout, self.stderr = rc, stdout, stderr


class Reads(unittest.TestCase):
    def test_log_lines_are_counted_per_utc_day_with_the_lookup_filter(self):
        seen = {}

        def run(args, **kw):
            seen["args"] = args
            return Fake(stdout=json.dumps([{"timestamp": "2026-11-18T01:00:00Z"},
                                           {"timestamp": "2026-11-18T23:59:59.9Z"},
                                           {"timestamp": "2026-11-17T12:00:00Z"}]))
        got = M.lookups_by_day("2026-11-11", "2026-11-19", run=run)
        self.assertEqual(got, {"2026-11-18": 2, "2026-11-17": 1})
        flt = seen["args"][2]
        self.assertIn('jsonPayload.component="findSimilarInMemory"', flt)
        self.assertIn('jsonPayload.message="Retrieving memory"', flt)
        self.assertIn('timestamp<"2026-11-19T00:00:00Z"', flt)
        self.assertIn("firebase-auratech16", seen["args"])

    def test_a_failed_log_read_is_unmeasured(self):
        with self.assertRaises(M.Unmeasured):
            M.lookups_by_day("2026-11-11", "2026-11-19", run=lambda a, **k: Fake(rc=1, stderr="denied"))

    def test_a_log_read_at_its_limit_is_unmeasured(self):
        full = json.dumps([{"timestamp": "2026-11-18T00:00:00Z"}] * M.LOG_LIMIT)
        with self.assertRaises(M.Unmeasured):
            M.lookups_by_day("2026-11-11", "2026-11-19", run=lambda a, **k: Fake(stdout=full))

    def test_unreadable_log_output_is_unmeasured(self):
        for stdout in ("not json", '{"entries": []}'):
            with self.subTest(stdout=stdout), self.assertRaises(M.Unmeasured):
                M.lookups_by_day("2026-11-11", "2026-11-19", run=lambda a, **k: Fake(stdout=stdout))

    def test_a_missing_gcloud_is_unmeasured(self):
        def run(args, **kw):
            raise FileNotFoundError("gcloud")
        with self.assertRaises(M.Unmeasured):
            M.lookups_by_day("2026-11-11", "2026-11-19", run=run)

    def test_any_ledger_exception_is_unmeasured(self):
        def query(sql, params=None):
            raise RuntimeError("job.result() failed")
        with self.assertRaises(M.Unmeasured):
            M.calls_by_day("2026-11-11", "2026-11-19", query=query)

    def test_the_ledger_query_counts_coach_prompter_only(self):
        seen = {}

        def query(sql, params=None):
            seen.update(sql=sql, params=params)
            return [{"day": "2026-11-18", "calls": 33088}]
        self.assertEqual(M.calls_by_day("2026-11-11", "2026-11-19", query=query), {"2026-11-18": 33088})
        self.assertEqual(seen["params"]["prompter"], "coach-prompter")
        self.assertIn("createdAt >= TIMESTAMP(@start)", seen["sql"])

    def test_a_failed_ledger_read_is_unmeasured(self):
        def query(sql, params=None):
            raise M.bq.BQError("boom")
        with self.assertRaises(M.Unmeasured):
            M.calls_by_day("2026-11-11", "2026-11-19", query=query)


class Cli(unittest.TestCase):
    def test_an_unexpected_crash_exits_3_never_the_alert_code(self):
        orig = M.lookups_by_day
        M.lookups_by_day = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.assertEqual(M.main(["--today", "2026-11-19"]), 3)
        finally:
            M.lookups_by_day = orig

    def test_a_bad_date_is_an_argument_error(self):
        r = subprocess.run([sys.executable, str(SCRIPTS / "monitor.py"), "--today", "nope"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
