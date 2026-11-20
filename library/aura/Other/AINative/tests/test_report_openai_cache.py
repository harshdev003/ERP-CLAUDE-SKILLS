"""eng:report-openai-cache — the daily cache cost table (ENG-1872).

The money is the point, so the arithmetic is pinned against hand-worked numbers, the Total row
against the days, and the query against what BigQuery is actually sent. No network: the query
callable is stubbed at its one seam.
"""

import importlib.util
import pathlib
import unittest
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "_report_openai_cache", ROOT / "plugins/eng/skills/report-openai-cache/scripts/report.py")
rep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rep)

UNCACHED, CACHED = 5.0, 0.5
DAYS = [{"day": "2026-10-01", "requests": 1000, "inputTokens": 10_000_000, "cachedTokens": 9_000_000},
        {"day": "2026-10-02", "requests": 500, "inputTokens": 4_000_000, "cachedTokens": 2_000_000}]


class CostTableTest(unittest.TestCase):
    def test_a_day_is_priced_by_cached_and_uncached_input(self):
        (d1, d2, total) = rep.table(DAYS, UNCACHED, CACHED, 90.0)
        # 1M uncached * $5 + 9M cached * $0.50 = $5 + $4.50
        self.assertAlmostEqual(d1["cost"], 9.5)
        self.assertAlmostEqual(d1["baselineCost"], 9.5)  # exactly at the 90% baseline
        self.assertAlmostEqual(d1["difference"], 0.0)
        # 2M * $5 + 2M * $0.50 = $11; at 90%: 0.4M * $5 + 3.6M * $0.50 = $3.80
        self.assertAlmostEqual(d2["cost"], 11.0)
        self.assertAlmostEqual(d2["baselineCost"], 3.8)
        self.assertAlmostEqual(d2["difference"], 7.2)
        self.assertAlmostEqual(d2["cachedPct"], 50.0)
        self.assertAlmostEqual(d2["tokensPerRequest"], 8000)

    def test_the_total_row_is_the_sum_of_the_days(self):
        rows = rep.table(DAYS, UNCACHED, CACHED, 90.0)
        total = rows[-1]
        self.assertEqual((total["day"], total["requests"], total["inputTokens"]), ("Total", 1500, 14_000_000))
        for k in ("cost", "baselineCost", "difference"):
            self.assertAlmostEqual(total[k], sum(r[k] for r in rows[:-1]))
        self.assertAlmostEqual(total["cachedPct"], 100 * 11 / 14)

    def test_the_table_has_the_agreed_columns_and_a_total(self):
        out = rep.render(rep.table(DAYS, UNCACHED, CACHED, 88.96), "coach-prompter", 88.96, UNCACHED, CACHED)
        self.assertIn("| Day | Requests | Input (M tokens) | Cached % | Input cost | Cost at 88.96% | Difference "
                      "| Tokens per request |", out)
        self.assertIn("| 2026-10-02 | 500 | 4.00 | 50.00% | $11.00 |", out)
        self.assertIn("| **Total** | **1,500** | **14.00** |", out)

    def test_a_saving_shows_as_negative(self):
        (row, _) = rep.table([{"day": "d", "requests": 1, "inputTokens": 1_000_000, "cachedTokens": 1_000_000}],
                             UNCACHED, CACHED, 50.0)
        self.assertAlmostEqual(row["difference"], 0.5 - 2.75)
        self.assertEqual(rep.money(row["difference"]), "-$2.25")
        self.assertEqual(rep.money(-0.001), "$0.00")

    def test_an_empty_day_has_no_rate(self):
        (row, _) = rep.table([{"day": "d", "requests": 0, "inputTokens": 0, "cachedTokens": 0}], UNCACHED, CACHED, 90)
        self.assertEqual((row["cachedPct"], row["tokensPerRequest"]), (None, None))


class QueryTest(unittest.TestCase):
    def test_the_query_is_bounded_and_filtered_by_prompter(self):
        sent = {}

        def query(sql, params=None, max_bytes=None):
            sent.update(sql=sql, params=params, max_bytes=max_bytes)
            return [dict(DAYS[0], extra=1)]

        start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        end = datetime(2026, 10, 3, tzinfo=timezone.utc)
        rows = rep.fetch_daily("coach-prompter", start, end, query=query)
        self.assertEqual(rows, [DAYS[0]])
        self.assertEqual(sent["params"], {"prompter": "coach-prompter", "since": "2026-10-01T00:00:00Z",
                                          "until": "2026-10-03T00:00:00Z"})
        self.assertIn("createdAt >= TIMESTAMP(@since)", sent["sql"])  # the table requires a partition filter
        self.assertIn("GROUP BY day", sent["sql"])
        self.assertTrue(sent["max_bytes"])

    def test_a_plain_date_is_utc_midnight(self):
        self.assertEqual(rep.iso(rep.parse_ts("2026-10-01")), "2026-10-01T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
