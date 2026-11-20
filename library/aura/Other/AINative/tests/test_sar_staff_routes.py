"""respond-to-sar: staff support lookups stay out of a person's server log, before and after the
services log route patterns on "Ending transaction" instead of raw paths on "Starting transaction"."""

from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
import zipfile
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "plugins/eng/skills/respond-to-sar/scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(f"sar_{name}", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build_pack = load("build_pack")
collect = load("collect")


def line(ts, msg, tx, **payload):
    return {"ts": ts, "resource": "k8s_container", "container": "user-web",
            "payload": {"message": msg, "transaction": tx, **payload}}


class TestStaffRoute(unittest.TestCase):
    def test_raw_ids_and_route_patterns_both_match(self):
        for url in ("/receipts/checkUser/AbC123", "/receipts/checkUser/:userId",
                    "GET /subscriptions/list/:uid", "/payments/getCustomerId/:id"):
            with self.subTest(url=url):
                self.assertTrue(build_pack.STAFF_ROUTE.search(url))
        for url in ("/users/me", "/receipts/checkUser", "<baseUrl>/:unmatched"):
            with self.subTest(url=url):
                self.assertFalse(build_pack.STAFF_ROUTE.search(url))

    def test_the_join_finds_staff_transactions_on_ending_lines(self):
        endings = [line("t", "Ending transaction", "tx-staff", originalUrl="/receipts/checkUser/:userId"),
                   line("t", "Ending transaction", "tx-own", originalUrl="/users/me")]
        self.assertEqual(build_pack.staff_transactions([], endings), {"tx-staff"})


class TestServerLogExport(unittest.TestCase):
    def build_06(self, window, endings, summary=None, review=None):
        out = tempfile.mkdtemp()
        os.makedirs(f"{out}/raw")
        if review is not None:
            with open(f"{out}/raw/route_review.json", "w") as f:
                json.dump(review, f)
        with open(f"{out}/raw/postgres_user.json", "w") as f:
            json.dump({"id": "u1"}, f)
        for name, rows in (("gcp_window.jsonl", window), ("gcp_endings.jsonl", endings)):
            with open(f"{out}/raw/{name}", "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
        result = build_pack.build(build_pack.Pack(out, "UTC", "UTC"), {})
        if summary is not None:
            summary.update(result)
            with open(f"{out}/review_unresolved_routes.csv", encoding="utf-8") as f:
                summary["review"] = list(csv.DictReader(f))
            summary["zipped"] = zipfile.ZipFile(result["zip"]).namelist()
        with open(f"{out}/export/06_server_request_log.csv", encoding="utf-8") as f:
            return [r["Action / request"] for r in csv.DictReader(f)]

    def test_a_staff_transaction_is_dropped_through_its_ending_line(self):
        window = [line("2026-11-09T10:00:00Z", "Starting transaction", "tx-staff", ip="203.0.113.5"),
                  line("2026-11-09T10:00:01Z", "Account record saved for staff check", "tx-staff", ip="203.0.113.5"),
                  line("2026-11-09T11:00:00Z", "Starting transaction", "tx-own", ip="198.51.100.7")]
        endings = [line("2026-11-09T10:00:02Z", "Ending transaction", "tx-staff",
                        originalUrl="/receipts/checkUser/:userId", statusCode=200)]
        rows = self.build_06(window, endings)
        self.assertEqual(rows, ["Request received from your browser or app"])

    def test_older_logs_with_the_raw_path_are_still_dropped(self):
        window = [line("2026-10-01T10:00:00Z", "Starting transaction", "tx-old", ip="203.0.113.5",
                       originalUrl="/receipts/checkUser/AbC123"),
                  line("2026-10-01T10:00:01Z", "Looked up receipts", "tx-old", ip="203.0.113.5")]
        self.assertEqual(self.build_06(window, []), [])


    def test_a_transaction_with_no_known_route_is_listed_for_review_never_zipped(self):
        window = [line("2026-11-09T10:00:00Z", "Starting transaction", "tx-lost", ip="203.0.113.5"),
                  line("2026-11-09T11:00:00Z", "Starting transaction", "tx-own", ip="198.51.100.7")]
        endings = [line("2026-11-09T11:00:02Z", "Ending transaction", "tx-own", originalUrl="/users/me")]
        out = {}
        rows = self.build_06(window, endings, out)
        self.assertEqual(len(rows), 2)
        self.assertEqual(out["unresolved_routes"], 1)
        self.assertEqual([r["Transaction"] for r in out["review"]], ["tx-lost"])
        self.assertNotIn("review_unresolved_routes.csv", out["zipped"])

    def test_review_verdicts_remove_staff_rows_on_every_rebuild(self):
        window = [line("2026-11-09T10:00:00Z", "Starting transaction", "tx-a", ip="203.0.113.5"),
                  line("2026-11-09T11:00:00Z", "Starting transaction", "tx-b", ip="198.51.100.7")]
        out = {}
        rows = self.build_06(window, [], out, review={"tx-a": "staff", "tx-b": "own"})
        self.assertEqual(rows, ["Request received from your browser or app"])
        self.assertEqual(out["unresolved_routes"], 0)

    def test_a_bad_review_verdict_stops_the_build(self):
        with self.assertRaises(SystemExit):
            self.build_06([line("2026-11-09T10:00:00Z", "Starting transaction", "tx-a")], [], review={"tx-a": "maybe"})

    def test_the_review_time_matches_06(self):
        out = {}
        self.build_06([line("2026-11-09T10:00:00Z", "Starting transaction", "tx-a", ip="203.0.113.5")], [], out)
        self.assertEqual(out["review"][0]["Time (UTC), as in 06"], "2026-11-09 10:00:00 UTC")

    def test_older_logs_with_the_route_on_the_start_line_are_resolved(self):
        window = [line("2026-10-01T10:00:00Z", "Starting transaction", "tx-old", ip="198.51.100.7",
                       originalUrl="/users/me")]
        out = {}
        self.build_06(window, [], out)
        self.assertEqual(out["unresolved_routes"], 0)


class TestCollectEndings(unittest.TestCase):
    def test_only_transactions_without_a_url_are_fetched_in_paced_batches(self):
        rows = [line(f"2026-11-09T10:{i // 60:02d}:{i % 60:02d}.123456789Z", "Starting transaction", f"tx-{i}")
                for i in range(collect.ENDING_BATCH + 5)]
        rows.append(line("2026-11-09T09:00:00Z", "Starting transaction", "tx-old", originalUrl="/users/me"))
        rows.append({"ts": "2026-11-09T09:00:00Z", "payload": {"message": "no transaction"}})
        filters = []

        def fake_window(lo, hi, clause, truncated):
            filters.append((lo, hi, clause))
            return [{"timestamp": "2026-11-09T10:00:05Z", "jsonPayload": {"message": "Ending transaction"}}]

        with mock.patch.object(collect, "_gcp_window", side_effect=fake_window), \
                mock.patch.object(collect.time, "sleep") as sleep:
            got, wanted = collect._endings(rows, [])
        self.assertEqual(len(filters), 2)
        self.assertEqual(sleep.call_count, 1)
        self.assertEqual(len(got), 2)
        self.assertEqual(wanted, collect.ENDING_BATCH + 5)
        clauses = " ".join(c for _, _, c in filters)
        self.assertIn('jsonPayload.message="Ending transaction"', clauses)
        self.assertNotIn('"tx-old"', clauses)
        self.assertEqual(sum(c.count('"tx-') for _, _, c in filters), collect.ENDING_BATCH + 5)
        lo, hi, _ = filters[0]
        self.assertLess(lo, dt.datetime(2026, 11, 9, 10, 0, tzinfo=dt.timezone.utc))
        self.assertGreater(hi, dt.datetime(2026, 11, 9, 10, 0, 39, tzinfo=dt.timezone.utc))

    def test_no_transactions_means_no_query(self):
        with mock.patch.object(collect, "_gcp_window") as w:
            self.assertEqual(collect._endings([], []), ([], 0))
        w.assert_not_called()


if __name__ == "__main__":
    unittest.main()
