#!/usr/bin/env python3
"""`marketing:harvest-social-mentions` — `fetch_mentions.py` on the branches live runs rarely hit.

PullPush is unofficial and rate-limits hard (a 12-request burst blocked the probe
machine for 45+ minutes on 2026-10-15), so the failures worth pinning are the
ones that make a blocked or half-finished run look like a quiet week:

- A 429 on the first page is an `error` source, not zero mentions.
- A failure on a later page keeps the rows that arrived and reports `partial`.
- Pagination walks `before` backwards and stops on a short page.
- The relevance prefilter **tags, never drops**: every hit reaches `mentions[]`.
- The prefilter passes the genuine mentions from the probe and fails the
  obvious look-alikes. These are real texts, trimmed.
- Usernames are never emitted.

Transport is stubbed at `_get_json`; `_sleep` is stubbed so retries cost nothing.
"""

import argparse
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
import urllib.error
from datetime import date, datetime, timezone
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/marketing/skills/harvest-social-mentions/scripts/fetch_mentions.py"

spec = importlib.util.spec_from_file_location("fetch_mentions", SCRIPT)
fm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fm)

TODAY = date(2026, 10, 15)


def ts(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())


def comment(i, day="2026-10-10", body="I love the Aura app hypnosis tracks"):
    return {"id": f"c{i}", "created_utc": ts(day), "body": body, "subreddit": "Sleep",
            "permalink": f"/r/Sleep/comments/x/y/c{i}/", "score": 3, "author": "someuser"}


def args(**kw):
    base = dict(since="30d", queries='"aura app"', archive="", no_archive=True, delay=0)
    base.update(kw)
    return argparse.Namespace(**base)


def comments_only(rows):
    """Stub `_get_json`: the comment endpoint returns `rows`, the submission endpoint nothing."""
    return lambda url: {"data": rows if "/comment/" in url else []}


def http_429():
    return urllib.error.HTTPError("u", 429, "Too Many Requests", {}, io.BytesIO(b""))


class Failures(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(fm, "_sleep")
        p.start()
        self.addCleanup(p.stop)

    def test_blocked_query_is_an_error_not_zero(self):
        with mock.patch.object(fm, "_get_json", side_effect=http_429()):
            out = fm.run(args(), TODAY)
        self.assertEqual({s["status"] for s in out["meta"]["sources"]}, {"error"})
        self.assertIn("429", out["meta"]["sources"][0]["error"])
        self.assertEqual(out["meta"]["counts"]["sources_error"], 2)

    def test_later_page_failure_keeps_rows_as_partial(self):
        full = {"data": [comment(i) for i in range(fm.PAGE_SIZE)]}

        def get(url):
            if "before=" in url:
                raise http_429()
            return full

        with mock.patch.object(fm, "_get_json", side_effect=get):
            rows, err = fm.fetch_pullpush("comment", '"aura app"', 0, 0)
        self.assertEqual(len(rows), fm.PAGE_SIZE)
        self.assertIn("429", err)

    def test_retry_recovers(self):
        calls = iter([http_429(), {"data": [comment(1)]}])

        def get(url):
            r = next(calls)
            if isinstance(r, Exception):
                raise r
            return r

        with mock.patch.object(fm, "_get_json", side_effect=get):
            rows, err = fm.fetch_pullpush("comment", "q", 0, 0)
        self.assertIsNone(err)
        self.assertEqual(len(rows), 1)


class Pagination(unittest.TestCase):
    def test_walks_before_and_stops_on_short_page(self):
        urls = []

        def get(url):
            urls.append(url)
            if "before=" not in url:
                return {"data": [comment(i, "2026-10-10") for i in range(fm.PAGE_SIZE)]}
            return {"data": [comment(999, "2026-10-01")]}

        with mock.patch.object(fm, "_get_json", side_effect=get), mock.patch.object(fm, "_sleep"):
            rows, err = fm.fetch_pullpush("comment", "q", 0, 0)
        self.assertEqual(len(urls), 2)
        self.assertIn(f"before={ts('2026-10-10')}", urls[1])
        self.assertEqual(len(rows), fm.PAGE_SIZE + 1)


class Prefilter(unittest.TestCase):
    OURS = [
        "No but the aura app is good. Sometimes stuff you would know. But committing to it has reduced my stress and raised my hrv 5 points.",
        "I do daily hypnosis which is sooooo good. Try Aura app. free 7 day trial.",
        "I have used Calm, Headspace, Insight Timer, Medito, and AuraHealth a handful of times",
        "Listen to Chronic Pain Reduction And Relaxation https://aurahealth.io/track/-NN83ps6BajaLlBIjp0O?aftp=true",
        "Now I use hypnosis tracks on my phone to settle my mind before sleep. Aura app is my favorite.",
        "I've been talking to Aurie, the AI app, every night this week",
        "The AI therapy app I use now is called Aurie",
    ]
    NOT_OURS = [
        "Gitae's Black Aura Was Always Conviction",
        "Tim Hortons, Paul, blue rewards will give you freebies",
        "Rereading any of Aila Aurie's stuff is never a bad decision. :)",
        "There's a specific Dispatch room in San Aurie PD",
    ]

    def test_genuine_mentions_pass(self):
        for t in self.OURS:
            self.assertTrue(fm.PREFILTER.search(t), t)

    def test_lookalikes_fail(self):
        for t in self.NOT_OURS:
            self.assertFalse(fm.PREFILTER.search(t), t)

    def test_link_post_target_is_kept_and_matched(self):
        x = {"id": "s1", "created_utc": ts("2026-10-10"), "title": "This helped my pain",
             "selftext": "", "url": "https://aurahealth.io/track/-NN83ps6BajaLlBIjp0O",
             "subreddit": "Fibromyalgia", "permalink": "/r/Fibromyalgia/comments/s1/x/"}
        r = fm.normalize("submission", "aurahealth", x)
        self.assertEqual(r["link"], "https://aurahealth.io/track/-NN83ps6BajaLlBIjp0O")
        self.assertTrue(r["prefilter"])

    def test_self_post_link_is_empty(self):
        x = {"id": "s2", "created_utc": ts("2026-10-10"), "title": "t", "selftext": "b",
             "url": "https://www.reddit.com/r/Sleep/comments/s2/x/",
             "subreddit": "Sleep", "permalink": "/r/Sleep/comments/s2/x/"}
        self.assertEqual(fm.normalize("submission", "q", x)["link"], "")

    def test_prefilter_tags_never_drops(self):
        rows = [comment(1), comment(2, body="Gitae's Black Aura")]
        with mock.patch.object(fm, "_get_json", side_effect=comments_only(rows)), \
                mock.patch.object(fm, "_sleep"):
            out = fm.run(args(), TODAY)
        self.assertEqual(len(out["mentions"]), 2)
        self.assertEqual(out["meta"]["counts"]["prefilter_pass"], 1)
        self.assertEqual(out["meta"]["counts"]["prefilter_fail"], 1)


class Output(unittest.TestCase):
    def test_username_never_emitted_and_queries_merge(self):
        with mock.patch.object(fm, "_get_json", side_effect=comments_only([comment(1)])), \
                mock.patch.object(fm, "_sleep"):
            out = fm.run(args(queries='"aura app",aurahealth'), TODAY)
        self.assertNotIn("someuser", json.dumps(out))
        self.assertEqual(len(out["mentions"]), 1)
        self.assertEqual(out["mentions"][0]["queries"], ['"aura app"', "aurahealth"])
        self.assertEqual(out["mentions"][0]["url"], "https://www.reddit.com/r/Sleep/comments/x/y/c1/")

    def test_archive_dedup_and_no_archive(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(fm, "_get_json", side_effect=comments_only([comment(1)])), \
                mock.patch.object(fm, "_sleep"):
            a = args(archive=str(pathlib.Path(d) / "m.jsonl"), no_archive=False)
            self.assertEqual(fm.run(a, TODAY)["meta"]["counts"]["new_to_archive"], 1)
            self.assertEqual(fm.run(a, TODAY)["meta"]["counts"]["new_to_archive"], 0)
            self.assertIsNone(fm.run(args(), TODAY)["meta"]["counts"]["new_to_archive"])


if __name__ == "__main__":
    unittest.main()
