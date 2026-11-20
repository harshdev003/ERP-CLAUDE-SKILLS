#!/usr/bin/env python3
"""`llm_ledger.py` — the BigQuery read of the `llmCall` ledger that replaced `GET /ai/llm-calls`.

Runs against every copy. What has to hold, because each failure is silent or refused:

- every query carries a `createdAt` bound (the table has `require_partition_filter`);
- a plain-date `to` covers that whole day, the way the endpoint read it;
- filter values travel as query parameters, never spliced into SQL, and an unknown filter raises;
- every row counts: no dedupe on `openaiId`;
- rows come back in the endpoint's shape (`createdAt` an ISO string ending in `Z`);
- a BigQuery failure raises `LedgerError` instead of reading as an empty ledger.

`bq.query` is the single chokepoint and is stubbed; no GCP call is made.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from reader_harness import load_reader, script_paths  # noqa: E402


class LedgerContract(unittest.TestCase):
    path: pathlib.Path = None  # type: ignore[assignment]

    def setUp(self):
        self.mod = load_reader(self.path, siblings=("llm_ledger", "bq"))
        self.calls = []
        self.result = []
        self._orig = self.mod.bq.query
        self.mod.bq.query = self._query

    def tearDown(self):
        self.mod.bq.query = self._orig

    def _query(self, sql, **kw):
        self.calls.append((sql, kw))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def test_every_query_carries_a_partition_bound(self):
        sql, qp = self.mod.build({"conversationId": "c1"})
        self.assertIn("createdAt >= TIMESTAMP(@from_ts)", sql)
        self.assertEqual(qp["from_ts"], self.mod.EPOCH_FLOOR)

    def test_a_plain_date_to_covers_the_whole_day(self):
        _, qp = self.mod.build({"from": "2026-10-01", "to": "2026-10-02"})
        self.assertEqual(qp["from_ts"], "2026-10-01T00:00:00Z")
        self.assertEqual(qp["to_ts"], "2026-10-03T00:00:00Z")

    def test_a_timestamp_to_is_an_exclusive_bound(self):
        _, qp = self.mod.build({"from": "2026-10-01T10:00:00Z", "to": "2026-10-01T11:00:00Z"})
        self.assertEqual(qp["to_ts"], "2026-10-01T11:00:00Z")

    def test_filter_values_are_parameters_not_sql(self):
        hostile = "u1' OR 1=1 --"
        sql, qp = self.mod.build({"userId": hostile, "prompterName": "coach-prompter"})
        self.assertNotIn(hostile, sql)
        self.assertEqual(qp["userId"], hostile)
        self.assertIn("userId = @userId", sql)

    def test_an_unknown_filter_raises_instead_of_widening_the_query(self):
        # The endpoint dropped unknown params and returned the unfiltered total.
        for bad in ({"prompter": "coach-prompter"}, {"projekt": "conversation"}):
            with self.assertRaises(self.mod.LedgerError):
                self.mod.build(bad)
            with self.assertRaises(self.mod.LedgerError):
                self.mod.stats(bad)
        self.assertEqual(self.calls, [])

    def test_stats_groups_by_api_with_the_same_bounds(self):
        self.result = [{"openaiAPI": "responses", "calls": 7, "inputTokens": 70, "outputTokens": 7},
                       {"openaiAPI": None, "calls": 1, "inputTokens": 1, "outputTokens": 0}]
        got = self.mod.stats({"from": "2026-10-01", "to": "2026-10-02", "project": "conversation", "limit": 5})
        self.assertEqual(got["responses"]["calls"], 7)
        self.assertEqual(got["unknown"]["calls"], 1)
        sql, kw = self.calls[0]
        self.assertIn("createdAt >= TIMESTAMP(@from_ts)", sql)
        self.assertIn("GROUP BY openaiAPI", sql)
        self.assertEqual(kw["params"]["project"], "conversation")

    def test_every_row_counts_no_dedupe_on_openai_id(self):
        # A repeated openaiId can be a real handoff to another prompt (Jerome, 2026-10-27).
        sql, _ = self.mod.build({})
        self.assertNotIn("QUALIFY", sql)
        self.assertNotIn("ROW_NUMBER", sql)

    def test_limit_offset_and_order(self):
        sql, qp = self.mod.build({"limit": "1000", "offset": 2000, "order": "asc"})
        self.assertIn("ORDER BY createdAt ASC", sql)
        self.assertIn("LIMIT @lim OFFSET @off", sql)
        self.assertEqual((qp["lim"], qp["off"]), (1000, 2000))
        self.assertIn("ORDER BY createdAt DESC", self.mod.build({})[0])

    def test_rows_come_back_in_the_endpoint_shape(self):
        when = dt.datetime(2026, 10, 27, 22, 35, 59, 129000, tzinfo=dt.timezone.utc)
        self.result = [{"id": "a", "createdAt": when, "usage": {"input_tokens": 3}, "callChain": [{"x": 1}]}]
        row = self.mod.rows({"userId": "u1"})[0]
        self.assertEqual(row["createdAt"], "2026-10-27T22:35:59.129Z")
        self.assertEqual(row["usage"], {"input_tokens": 3})
        self.assertEqual(row["callChain"], [{"x": 1}])
        self.assertIsNotNone(self.calls[0][1].get("max_bytes"))

    def test_a_bigquery_failure_raises(self):
        self.result = self.mod.bq.BQError("403 access denied")
        with self.assertRaises(self.mod.LedgerError):
            self.mod.rows({"userId": "u1"})

    def test_a_job_error_bq_does_not_wrap_still_raises_ledger_error(self):
        # bq.query wraps client.query() but not job.result(), which raises google exceptions.
        self.result = TimeoutError("job exceeded maximum_bytes_billed")
        with self.assertRaises(self.mod.LedgerError):
            self.mod.rows({"userId": "u1"})

    def test_bq_sits_beside_every_copy(self):
        self.assertTrue((self.path.parent / "bq.py").exists())


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    paths = script_paths("llm_ledger.py")
    if not paths:
        raise AssertionError("no llm_ledger.py found")
    for path in paths:
        name = f"Ledger_{path.parent.parent.name.replace('-', '_')}"
        cls = type(name, (LedgerContract,), {"path": path, "__module__": __name__})
        suite.addTests(loader.loadTestsFromTestCase(cls))
    return suite


if __name__ == "__main__":
    unittest.main()
