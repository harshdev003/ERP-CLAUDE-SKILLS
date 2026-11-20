#!/usr/bin/env python3
"""`marketing:harvest-app-reviews` — `fetch_reviews.py` against branches live data rarely reaches.

The failures this suite pins are all *silent shrinkage*: a run that returns fewer
reviews than exist and still looks complete.

- **Apple's CDN caches an empty feed for one URL spelling.** Observed 2026-10-15:
  Aurie US returned 0 entries on the canonical URL and 48 on two equivalent
  spellings. A fetch that believed the first empty page reported "no reviews".
- **A single-review page is a JSON object, not a list**, and page 1 can lead
  with app metadata that has no rating.
- **The 500-review cap.** A window that starts before the oldest review the store
  will serve must be reported as truncated, unless the archive already covers it.
- **Play without its optional dependency** must surface as an error source, never
  as a clean zero.

Transport is stubbed at its two chokepoints (`_get_json`, `_play_reviews`); no network.
"""

import argparse
import importlib.util
import json
import pathlib
import tempfile
import unittest
from datetime import date, datetime
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/marketing/skills/harvest-app-reviews/scripts/fetch_reviews.py"

spec = importlib.util.spec_from_file_location("fetch_reviews", SCRIPT)
fr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fr)

TODAY = date(2026, 10, 15)


def ios_entry(i, day="2026-10-10", rating=1):
    return {
        "id": {"label": str(i)},
        "updated": {"label": f"{day}T00:00:00-07:00"},
        "im:rating": {"label": str(rating)},
        "im:version": {"label": "1.32.0"},
        "title": {"label": "t"},
        "content": {"label": "c"},
        "author": {"name": {"label": "SomeReviewer"}},
    }


def args(**kw):
    base = dict(since="30d", apps="aurie", stores="ios", countries="us",
                archive="", no_archive=True)
    base.update(kw)
    return argparse.Namespace(**base)


class IosFeed(unittest.TestCase):
    def test_empty_cached_spelling_falls_through_to_next(self):
        def get(url):
            if "/us/rss/" in url and "sortby=mostrecent" in url:
                return {"feed": {}}  # the poisoned cache entry
            if "page=1" in url:
                return {"feed": {"entry": [ios_entry(1), ios_entry(2)]}}
            return {"feed": {}}

        with mock.patch.object(fr, "_get_json", side_effect=get):
            rows, err = fr.fetch_ios("aurie", 6748324270, "us")
        self.assertIsNone(err)
        self.assertEqual([r["id"] for r in rows], ["1", "2"])

    def test_single_entry_object_and_metadata_entry(self):
        pages = {
            1: {"feed": {"entry": [{"id": {"label": "app"}}, ios_entry(1)]}},
            2: {"feed": {"entry": ios_entry(2)}},
        }

        def get(url):
            for p, body in pages.items():
                if f"page={p}/" in url:
                    return body
            return {"feed": {}}

        with mock.patch.object(fr, "_get_json", side_effect=get):
            rows, _ = fr.fetch_ios("aurie", 1, "us")
        self.assertEqual([r["id"] for r in rows], ["1", "2"])

    def test_reviewer_name_never_emitted(self):
        with mock.patch.object(fr, "_get_json",
                               side_effect=lambda u: {"feed": {"entry": [ios_entry(1)]}} if "page=1/" in u else {"feed": {}}):
            rows, _ = fr.fetch_ios("aurie", 1, "us")
        self.assertNotIn("SomeReviewer", json.dumps(rows))

    def test_page_one_failure_is_an_error_not_zero(self):
        with mock.patch.object(fr, "_get_json", side_effect=OSError("boom")):
            rows, err = fr.fetch_ios("aurie", 1, "us")
        self.assertEqual(rows, [])
        self.assertIn("boom", err)


class LaterPageFailure(unittest.TestCase):
    def test_page_two_error_is_partial_not_complete(self):
        def get(url):
            if "page=1/" in url:
                return {"feed": {"entry": [ios_entry(i) for i in range(50)]}}
            raise OSError("429")

        with mock.patch.object(fr, "_get_json", side_effect=get):
            out = fr.run(args(), TODAY)
        src = out["meta"]["sources"][0]
        self.assertEqual(src["status"], "partial")
        self.assertEqual(src["fetched"], 50)
        self.assertEqual(out["meta"]["counts"]["sources_error"], 1)


class CountryFilter(unittest.TestCase):
    def test_archived_other_countries_stay_out_of_the_window(self):
        with tempfile.TemporaryDirectory() as d:
            arch = pathlib.Path(d) / "r.jsonl"
            gb = {"store": "ios", "app": "aurie", "country": "gb", "id": "gb1",
                  "date": "2026-10-10", "rating": 1, "version": "", "title": "",
                  "text": "", "helpful": 0, "reply": ""}
            arch.write_text(json.dumps(gb) + "\n")
            feed = lambda u: {"feed": {"entry": [ios_entry(1)]}} if "page=1/" in u else {"feed": {}}
            with mock.patch.object(fr, "_get_json", side_effect=feed):
                out = fr.run(args(archive=str(arch), no_archive=False, countries="us"), TODAY)
        self.assertEqual([r["id"] for r in out["reviews"]], ["1"])


class Truncation(unittest.TestCase):
    def full_feed(self, oldest_day):
        def get(url):
            p = int(url.split("page=")[1].split("/")[0])
            return {"feed": {"entry": [ios_entry(p * 100 + i, day=oldest_day) for i in range(50)]}}
        return get

    def test_cap_reached_inside_window_is_truncated(self):
        with mock.patch.object(fr, "_get_json", side_effect=self.full_feed("2026-10-01")):
            out = fr.run(args(), TODAY)
        src = out["meta"]["sources"][0]
        self.assertTrue(src["window_truncated"])
        self.assertEqual(out["meta"]["counts"]["sources_truncated"], 1)

    def test_archive_reaching_back_covers_truncation(self):
        with tempfile.TemporaryDirectory() as d:
            arch = pathlib.Path(d) / "r.jsonl"
            old = {"store": "ios", "app": "aurie", "country": "us", "id": "old",
                   "date": "2026-09-15", "rating": 5, "version": "", "title": "",
                   "text": "", "helpful": 0, "reply": ""}
            arch.write_text(json.dumps(old) + "\n")
            with mock.patch.object(fr, "_get_json", side_effect=self.full_feed("2026-10-01")):
                out = fr.run(args(archive=str(arch), no_archive=False), TODAY)
        self.assertTrue(out["meta"]["sources"][0]["covered_by_archive"])
        self.assertEqual(out["meta"]["counts"]["sources_truncated"], 0)
        self.assertIn("old", [r["id"] for r in out["reviews"]])


class Archive(unittest.TestCase):
    def test_rerun_adds_nothing(self):
        feed = lambda u: {"feed": {"entry": [ios_entry(1)]}} if "page=1/" in u else {"feed": {}}
        with tempfile.TemporaryDirectory() as d:
            a = args(archive=str(pathlib.Path(d) / "r.jsonl"), no_archive=False)
            with mock.patch.object(fr, "_get_json", side_effect=feed):
                first = fr.run(a, TODAY)
                second = fr.run(a, TODAY)
            lines = (pathlib.Path(d) / "r.jsonl").read_text().splitlines()
        self.assertEqual(first["meta"]["counts"]["new_to_archive"], 1)
        self.assertEqual(second["meta"]["counts"]["new_to_archive"], 0)
        self.assertEqual(len(lines), 1)


class NoArchive(unittest.TestCase):
    def test_no_archive_reports_none_not_a_count(self):
        feed = lambda u: {"feed": {"entry": [ios_entry(1)]}} if "page=1/" in u else {"feed": {}}
        with mock.patch.object(fr, "_get_json", side_effect=feed):
            out = fr.run(args(), TODAY)
        self.assertIsNone(out["meta"]["counts"]["new_to_archive"])


class ReplyRedaction(unittest.TestCase):
    """Support replies open with the reviewer's first name: 22 of 492 Play replies on
    2026-10-15, every one as `Hi <Name>` followed by `,` or `!`."""

    def test_greeting_name_redacted(self):
        for raw, want in [
            ("Hi Claire, we're so sorry", "Hi [name], we're so sorry"),
            ("Hi Anna! Hi! We're sorry", "Hi [name]! Hi! We're sorry"),
            ("Dear Mary Ann, sorry", "Dear [name], sorry"),
        ]:
            self.assertEqual(fr.redact_reply(raw), want)

    def test_non_names_untouched(self):
        for raw in ["Hi there, thanks", "Hello again! Sorry", "Hi, thanks",
                    "We're sorry to hear about your experience."]:
            self.assertEqual(fr.redact_reply(raw), raw)

    def test_fresh_play_reply_redacted(self):
        row = {"reviewId": "p1", "at": datetime(2026, 10, 10), "score": 1, "content": "x",
               "replyContent": "Hi Cynthia, we fixed it"}
        with mock.patch.object(fr, "_play_reviews", return_value=[row]):
            out = fr.run(args(apps="aura", stores="play"), TODAY)
        self.assertNotIn("Cynthia", json.dumps(out))

    def test_archived_raw_reply_redacted_on_emit(self):
        with tempfile.TemporaryDirectory() as d:
            arch = pathlib.Path(d) / "r.jsonl"
            old = {"store": "play", "app": "aura", "country": "us", "id": "old",
                   "date": "2026-10-01", "rating": 1, "version": "", "title": "",
                   "text": "", "helpful": 0, "reply": "Hi Judy, we're so sorry"}
            arch.write_text(json.dumps(old) + "\n")
            with mock.patch.object(fr, "_play_reviews", return_value=[]):
                out = fr.run(args(apps="aura", stores="play", archive=str(arch),
                                  no_archive=False), TODAY)
        self.assertNotIn("Judy", json.dumps(out))


class Play(unittest.TestCase):
    def test_missing_dependency_is_an_error_source(self):
        with mock.patch.object(fr, "_play_reviews", side_effect=ImportError("no module")):
            out = fr.run(args(apps="aura", stores="play"), TODAY)
        src = out["meta"]["sources"][0]
        self.assertEqual(src["status"], "error")
        self.assertIn("google-play-scraper", src["error"])
        self.assertEqual(out["meta"]["counts"]["sources_error"], 1)

    def test_fetched_once_regardless_of_countries(self):
        row = {"reviewId": "p1", "at": datetime(2026, 10, 10), "score": 2,
               "content": "x", "thumbsUpCount": 3, "reviewCreatedVersion": "3.19.1",
               "replyContent": "hi", "userName": "Somebody"}
        with mock.patch.object(fr, "_play_reviews", return_value=[row]) as m:
            out = fr.run(args(apps="aura", stores="play", countries="us,gb,ca"), TODAY)
        self.assertEqual(m.call_count, 1)
        self.assertEqual(out["reviews"][0]["reply"], "hi")
        self.assertNotIn("Somebody", json.dumps(out))

    def test_aurie_not_listed_on_play(self):
        out = fr.run(args(apps="aurie", stores="play"), TODAY)
        self.assertEqual(out["meta"]["sources"][0]["status"], "not_listed")


if __name__ == "__main__":
    unittest.main()
