"""ElevenLabs per-user cost: days past the 10-day retention come from the cost store (ENG-1415).

`eng:report-elevenlabs-user-cost` used to walk only the live ConvAI API. ElevenLabs purges
`userId` after 10 days, so on the `last-30d` window every caller uses, older calls were
unattributable and rendered as $0 — a heavy user read ~5x low. The window is now split at the
first UTC midnight after the retention cutoff: whole days before it are summed from the per-user
daily cost store (`voice_11labs_agent`), everything after it from the live walk.

What this pins, and why each is load-bearing:

  * **No day is read twice.** The store's last day is the day before the split and the live
    walk starts exactly at the split. An off-by-one here double-counts a whole day silently.
  * **A recent window never touches BigQuery.** Most calls are `last-7d`; making them depend
    on GCP_KEY would be a regression the old skill never had.
  * **Pending and unavailable are never $0.** A store day not yet written is pending; a store
    that can't be reached is `unavailable`, named, exit 2 — and the live half still reports.

HTTP is stubbed through the same `Stub` the user-filter suite uses; the store through the
module's `_store_query` chokepoint. No network, no keys.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_elevenlabs_user_filter import TARGET, Stub, load  # noqa: E402

M = load()


def _ts(iso: str) -> int:
    return int(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


class FakeStore:
    """Answers read_store's two queries. `stored` = dates the store holds; `rows` = uid's $."""

    def __init__(self, stored=(), rows=None, raise_as=None):
        self.stored, self.rows, self.raise_as = list(stored), rows or {}, raise_as
        self.calls = []

    def __call__(self, sql, params=None):
        self.calls.append((sql, params))
        if self.raise_as:
            raise type(self.raise_as, (Exception,), {})("GCP_KEY not set")
        if "DISTINCT date" in sql:
            return [{"date": d} for d in self.stored]
        return [{"date": d, "voice_11labs_agent": v} for d, v in self.rows.items()]


def run(stub, store, window="last-30d"):
    out = pathlib.Path(tempfile.mkdtemp()) / "r.json"
    orig = (M.requests.get, sys.argv, dict(M.os.environ), M._store_query)
    M.requests.get = stub
    M._store_query = lambda: store
    M.os.environ["ELEVENLABS_ADMIN_API_KEY"] = "stub-key"
    sys.argv = ["report.py", TARGET, "--window", window, "--json", str(out)]
    code = 0
    try:
        M.main()
    except SystemExit as e:
        code = e.code or 0
    finally:
        M.requests.get, sys.argv, env, M._store_query = orig
        M.os.environ.clear()
        M.os.environ.update(env)
    return code, json.loads(out.read_text())


class TestSplit(unittest.TestCase):

    def test_split_is_the_midnight_after_the_retention_cutoff(self):
        now = _ts("2026-09-25T20:22:00")
        self.assertEqual(M.split_point(now), _ts("2026-09-16T00:00:00"))

    def test_store_days_end_the_day_before_the_split(self):
        split = _ts("2026-09-16T00:00:00")
        dates = M.store_dates(_ts("2026-09-13T08:00:00"), _ts("2026-09-25T20:00:00"), split)
        self.assertEqual(dates, ["2026-09-13", "2026-09-14", "2026-09-15"])

    def test_a_recent_window_has_no_store_days(self):
        split = _ts("2026-09-16T00:00:00")
        self.assertEqual(M.store_dates(_ts("2026-09-18T20:00:00"), _ts("2026-09-25T20:00:00"), split), [])

    def test_an_old_window_is_all_store(self):
        split = _ts("2026-09-16T00:00:00")
        dates = M.store_dates(_ts("2026-08-01T00:00:00"), _ts("2026-09-01T00:00:00"), split)
        self.assertEqual((dates[0], dates[-1], len(dates)), ("2026-08-01", "2026-08-31", 31))


class TestMain(unittest.TestCase):

    def _split(self):
        return M.split_point(int(datetime.now(tz=timezone.utc).timestamp()))

    def test_long_window_adds_store_days_and_walks_only_from_the_split(self):
        split = self._split()
        stub = Stub(workspace={"c1": TARGET})
        store = FakeStore(stored=M.store_dates(_ts("2000-01-01T00:00:00"), split, split)[-25:],
                          rows={"2026-01-01": 0.0})
        # Put real $ on the last store day — the one next to the boundary.
        last_store_day = datetime.fromtimestamp(split - 1, tz=timezone.utc).date().isoformat()
        store.rows = {last_store_day: 2.5}
        code, rep = run(stub, store)
        self.assertEqual(code, 0)
        self.assertEqual(rep["sources"]["store"]["cost_usd"], 2.5)
        self.assertEqual(rep["sources"]["store"]["dates"][-1], last_store_day)
        # the live walk starts exactly at the split: the boundary day is never walked
        self.assertEqual(stub.list_calls[0]["call_start_after_unix"], split)
        self.assertAlmostEqual(rep["total"]["cost_usd"], 2.5 + rep["sources"]["live"]["cost_usd"])
        self.assertEqual(rep["status"], "ok")

    def test_a_recent_window_never_queries_the_store(self):
        store = FakeStore(raise_as="ShouldNotBeCalled")
        code, rep = run(Stub(workspace={"c1": TARGET}), store, window="last-7d")
        self.assertEqual(code, 0)
        self.assertEqual(store.calls, [])
        self.assertIsNone(rep["sources"]["store"])

    def test_unstored_store_days_are_pending_not_zero(self):
        store = FakeStore(stored=[], rows={})
        code, rep = run(Stub(workspace={"c1": TARGET}), store)
        s = rep["sources"]["store"]
        self.assertEqual(s["status"], "pending")
        self.assertIsNone(s["cost_usd"])
        self.assertEqual(rep["status"], "partial")
        self.assertTrue(any("pending, not $0" in n for n in rep["notes"]))
        md = M.render_markdown(rep, show_by_bucket=False)
        self.assertIn("**pending**", md)

    def test_store_unavailable_degrades_names_the_half_and_keeps_live(self):
        code, rep = run(Stub(workspace={"c1": TARGET}), FakeStore(raise_as="BQUnavailable"))
        self.assertEqual(code, 2)
        self.assertEqual(rep["sources"]["store"]["status"], "unavailable")
        self.assertIsNone(rep["sources"]["store"]["cost_usd"])
        self.assertEqual(rep["status"], "partial")
        self.assertGreater(rep["total"]["cost_usd"], 0)  # the live call still counts
        self.assertTrue(any("NOT in this figure" in n for n in rep["notes"]))
        md = M.render_markdown(rep, show_by_bucket=False)
        self.assertIn("**unavailable**", md)
        self.assertNotIn("| $0.00 | not in total", md)

    def test_an_all_store_window_skips_the_walk(self):
        stub = Stub(workspace={"c1": TARGET})
        store = FakeStore(stored=[f"2026-08-{d:02d}" for d in range(1, 32)], rows={"2026-08-03": 1.0})
        orig = M.resolve
        M.resolve = lambda *a: orig(None, "2026-08-01", "2026-09-01")
        try:
            code, rep = run(stub, store)
        finally:
            M.resolve = orig
        self.assertEqual(stub.list_calls, [])
        self.assertEqual(rep["total"]["cost_usd"], 1.0)
        self.assertIsNone(rep["sources"]["live"]["cost_usd"])


if __name__ == "__main__":
    unittest.main()
