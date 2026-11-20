"""`slack_post.py`: one worker's body can never land in another worker's thread.

Incident (2026-10-27): parallel feedback-analysis-cadence workers shared one scratch body file.
A guardrail readout written by one worker was posted by another into an unrelated
#ai-cancellation-feedback thread, as 19 chunked Aura AI replies. Two guards, each tested here
against every copy of the helper:

  - `body-path` hands every worker its own mkstemp file, so two workers never share a name;
  - `post --expect <token>` refuses (exit 2, nothing sent) when the body lacks an id only this
    entry's body carries, so a swapped body is stopped at the post.

Two real threads run in parallel through `cmd_post`, with the Slack transport stubbed at
`post_message`, the module's single chokepoint.
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import re
import sys
import threading
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
COPIES = sorted(REPO.glob("plugins/*/skills/*/scripts/slack_post.py"))


def _load(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(f"slack_post_xpost_{abs(hash(path))}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _post_args(channel, thread, text_file, expect):
    return argparse.Namespace(channel=channel, text=None, text_file=text_file, thread_ts=thread, token=None,
                              unfurl=False, username=None, icon_url=None, as_user=False, footer=None,
                              footer_extra=None, footer_version=None, footer_runner=None, footer_skill_md=None,
                              date=None, thread_overflow=False, permalink=False, expect=expect)


class CrossPosting(unittest.TestCase):
    def test_copies_exist(self):
        self.assertGreaterEqual(len(COPIES), 16)

    def run_workers(self, mod, workers):
        """Each worker writes its body to `path` then posts it, all at once. Returns posts and exit codes."""
        posts, codes, lock = [], {}, threading.Lock()
        barrier = threading.Barrier(len(workers))

        def fake_post(channel, text, **kw):
            with lock:
                posts.append((channel, kw.get("thread_ts"), text))
            return {"ok": True, "ts": "1.0", "channel": channel}

        def worker(w):
            pathlib.Path(w["path"]).write_text(w["body"], encoding="utf-8")
            barrier.wait()  # every worker has written before any posts: the race at its worst
            codes[w["name"]] = mod.cmd_post(_post_args(w["channel"], w["thread"], w["path"], w.get("expect")))

        with mock.patch.object(mod, "post_message", side_effect=fake_post), mock.patch("sys.stdout"), \
                mock.patch("sys.stderr"):
            threads = [threading.Thread(target=worker, args=(w,)) for w in workers]
            [t.start() for t in threads]
            [t.join() for t in threads]
        return posts, codes

    def body_path(self, mod, tag):
        out = []
        with mock.patch.object(sys, "stdout") as stdout:
            stdout.write.side_effect = out.append
            mod.cmd_body_path(argparse.Namespace(tag=tag))
        path = "".join(out).strip()
        self.addCleanup(pathlib.Path(path).unlink, missing_ok=True)
        return path

    def test_unique_paths_keep_two_parallel_workers_apart(self):
        for path in COPIES:
            with self.subTest(copy=str(path.relative_to(REPO))):
                mod = _load(path)
                a = self.body_path(mod, "run-cancellation-feedback-analysis-cadence-entryA")
                b = self.body_path(mod, "run-conversation-guardrails-analysis-cadence-entryB")
                self.assertNotEqual(a, b)
                posts, codes = self.run_workers(mod, [
                    {"name": "cancel", "path": a, "channel": "C0BR3UTNL83", "thread": "1793123447.185399",
                     "body": "Cancellation profile for abcd1234…", "expect": ["abcd1234…"]},
                    {"name": "guard", "path": b, "channel": "C0C7HEZ7CEA", "thread": "1793120000.000100",
                     "body": "🚨 active risk for wxyz9876…", "expect": ["wxyz9876…"]},
                ])
                self.assertEqual(codes, {"cancel": 0, "guard": 0})
                by_thread = {t: (c, body) for c, t, body in posts}
                self.assertIn("abcd1234…", by_thread["1793123447.185399"][1])
                self.assertIn("wxyz9876…", by_thread["1793120000.000100"][1])

    def test_a_shared_file_is_refused_not_cross_posted(self):
        """The incident shape: both workers use one fixed name; the last writer wins the file."""
        for path in COPIES:
            with self.subTest(copy=str(path.relative_to(REPO))):
                mod = _load(path)
                shared = self.body_path(mod, "shared-fixed-name")
                posts, codes = self.run_workers(mod, [
                    {"name": "cancel", "path": shared, "channel": "C0BR3UTNL83", "thread": "1793123447.185399",
                     "body": "Cancellation profile for abcd1234…", "expect": ["abcd1234…"]},
                    {"name": "guard", "path": shared, "channel": "C0C7HEZ7CEA", "thread": "1793120000.000100",
                     "body": "🚨 active risk for wxyz9876…", "expect": ["wxyz9876…"]},
                ])
                # Whichever body survived in the file, the other worker's post is refused.
                self.assertEqual(sorted(codes.values()), [0, 2])
                for channel, thread, body in posts:
                    want = "abcd1234…" if thread == "1793123447.185399" else "wxyz9876…"
                    self.assertIn(want, body, "a body reached a thread it was not written for")

    def test_the_incident_body_is_refused_by_each_card_guard(self):
        """A 69k-character transcript in place of a ~1k-character cancellation card."""
        transcript = "\n".join(f"Turn {i}: some transcript text that is not a card." for i in range(1400))
        card = "*Cancellation* · abcd1234… · Day 12\nWhy they left: price.\n"
        for path in COPIES:
            with self.subTest(copy=str(path.relative_to(REPO))):
                mod = _load(path)
                for kw in ({"expect_header": ["*Cancellation*", "abcd1234…"]}, {"max_chars": 6000},
                           {"max_chunks": 3}):
                    self.assertTrue(mod.card_guard_errors(transcript, **kw), kw)
                    self.assertEqual(mod.card_guard_errors(card, **kw), [], kw)
                self.assertGreater(len(mod.split_for_slack(transcript)), 3)

    def test_without_expect_the_old_behaviour_is_unchanged(self):
        mod = _load(COPIES[0])
        path = self.body_path(mod, "x")
        posts, codes = self.run_workers(mod, [{"name": "a", "path": path, "channel": "C1", "thread": None,
                                               "body": "hello"}])
        self.assertEqual((codes, len(posts)), ({"a": 0}, 1))


class NoSharedScratchNames(unittest.TestCase):
    """The cadence and investigate skills must not tell a worker to use a fixed scratch name."""

    def test_no_fixed_tmp_or_generic_names(self):
        bad = re.compile(r"/tmp/[\w.-]+\.(?:txt|json|md)\b|\b[a-z]\d?\.txt\b")
        files = [p for pat in ("plugins/data/skills/run-*-analysis-cadence/**/*.md", "plugins/*/skills/investigate-*/**/*.md")
                 for p in REPO.glob(pat)]
        self.assertGreater(len(files), 10)
        for f in files:
            with self.subTest(file=str(f.relative_to(REPO))):
                hits = [m.group() for m in bad.finditer(f.read_text(encoding="utf-8"))
                        if m.group() not in ("c1.txt", "c2.txt", "c3.txt", "a.txt")]  # the forbidden examples, named as such
                self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
