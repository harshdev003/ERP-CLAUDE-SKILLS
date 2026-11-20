#!/usr/bin/env python3
"""`slack_post.py` — chunk an over-long body on a boundary we choose.

`chat.postMessage` accepts a body longer than 4,000 characters and then splits it
**itself**, mid-token, returning only the **last** ts. Neither half of that is
visible to a caller that checks `resp["ok"]`:

- A `<url|label>` link straddling the cut is torn into two unusable halves. On the
  2026-09-21 `shared:daily-status` run a 9,148-character standup landed in
  #standup as three messages (4000 / 3975 / 557); one Linear link ended a message
  as `(6233, <ht` and opened the next as `tps://…%7C6236>`, rendering as raw text.
- The returned ts is the **tail** fragment, so `--permalink` pointed at a
  557-character scrap and the "In Progress" reply threaded under it.

Why this needs a test rather than a hand-check: the post *succeeds*. Slack returns
`ok: true` with a real ts, so every assertion a caller makes about the call passes
while the message on screen is broken. The failure only exists above the limit,
and a body only crosses 4,000 characters on a heavy day — which is exactly when
nobody re-reads the post. So the boundary cases are constructed, not sampled.

`split_for_slack` is pure, so it is tested directly; `post_message`'s wiring (the
first response is what comes back, and a failed first chunk stops the run) is
covered through a stubbed `_api`, since that is the part a green splitter test
would still let ship broken. [ENG-1356]
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
CANONICAL = REPO / "plugins/shared/skills/post-to-slack/scripts/slack_post.py"
COPIES = sorted(REPO.glob("plugins/*/skills/*/scripts/slack_post.py"))


def load(path: pathlib.Path):
    """Import one copy under a unique module name so copies don't collide."""
    name = f"slack_post_{path.parts[-3].replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def link(number: int) -> str:
    """A Linear link in the shape daily-status emits — the thing that got torn."""
    return (
        f"<https://linear.app/aura-health/issue/PROD-{number}/"
        f"some-reasonably-long-issue-slug-goes-here|{number}>"
    )


def standup(bullets: int) -> str:
    """A body shaped like a real standup: bold header, italic buckets, bullets."""
    out = ["*Standup — Monday, September 21*", "", "*Completed [263]*", ""]
    for i in range(bullets):
        if i % 5 == 0:
            out += [f"_Bucket {i // 5}_"]
        out += [f"• Did a thing that took a sentence to describe. ({link(6000 + i)})"]
        if i % 5 == 4:
            out += [""]
    return "\n".join(out)


class SplitContract(unittest.TestCase):
    """Properties every copy must hold, on every copy."""

    def test_copies_exist(self) -> None:
        self.assertGreaterEqual(len(COPIES), 2, "expected several slack_post.py copies")
        self.assertIn(CANONICAL, COPIES)

    def test_short_body_is_not_split(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(path=path.parts[-3]):
                self.assertEqual(mod.split_for_slack("hello"), ["hello"])

    def test_every_chunk_is_within_the_limit(self) -> None:
        body = standup(200)
        for path in COPIES:
            mod = load(path)
            with self.subTest(path=path.parts[-3]):
                chunks = mod.split_for_slack(body)
                self.assertGreater(len(chunks), 1, "body should have needed splitting")
                for chunk in chunks:
                    self.assertLessEqual(len(chunk), mod.SLACK_TEXT_LIMIT)

    def test_no_chunk_ends_inside_a_link(self) -> None:
        """The defect itself: `(6233, <ht` + `tps://…|6236>` across a boundary.

        Counting delimiters is the assertion that generalises — a chunk cut inside
        `<…>` leaves an unmatched `<` behind and an unmatched `>` ahead, whatever
        the URL happened to be.
        """
        for path in COPIES:
            mod = load(path)
            for bullets in (60, 120, 200, 400):
                chunks = mod.split_for_slack(standup(bullets))
                for index, chunk in enumerate(chunks):
                    with self.subTest(path=path.parts[-3], bullets=bullets, chunk=index):
                        self.assertEqual(chunk.count("<"), chunk.count(">"))

    def test_chunks_break_between_bullets(self) -> None:
        """A chunk starts at the beginning of a line, never mid-sentence."""
        for path in COPIES:
            mod = load(path)
            chunks = mod.split_for_slack(standup(200))
            for index, chunk in enumerate(chunks[1:], start=1):
                with self.subTest(path=path.parts[-3], chunk=index):
                    self.assertRegex(chunk[:1], r"[•_*]")

    def test_content_survives_the_split(self) -> None:
        """Nothing is dropped: every link in, every link out, in order."""
        body = standup(200)
        wanted = [link(6000 + i) for i in range(200)]
        for path in COPIES:
            mod = load(path)
            with self.subTest(path=path.parts[-3]):
                joined = "\n".join(mod.split_for_slack(body))
                found = [item for item in wanted if item in joined]
                self.assertEqual(found, wanted)

    def test_an_unbroken_run_is_cut_hard_rather_than_looping(self) -> None:
        """No separator anywhere — the fallback must still terminate."""
        for path in COPIES:
            mod = load(path)
            with self.subTest(path=path.parts[-3]):
                chunks = mod.split_for_slack("x" * (mod.SLACK_TEXT_LIMIT * 3 + 7))
                self.assertEqual(len(chunks), 4)
                self.assertEqual("".join(chunks), "x" * (mod.SLACK_TEXT_LIMIT * 3 + 7))


class PostMessageWiring(unittest.TestCase):
    """The half a splitter test cannot see: which response comes back.

    Slack's own split returns the LAST ts. A permalink or a follow-up `thread_ts`
    built on that anchors to the tail fragment — which is how the In Progress reply
    ended up under a 557-character scrap. `post_message` must hand back the FIRST.
    """

    def setUp(self) -> None:
        self.mod = load(CANONICAL)
        self.sent: list[dict] = []
        self.responses: list[dict] = []
        self.slept: list[float] = []
        self.mod._api = self._fake_api  # type: ignore[attr-defined]
        self.mod._token = lambda token=None: "xoxb-test"  # type: ignore[attr-defined]
        self.mod._user_token = lambda token=None: "xoxp-test"  # type: ignore[attr-defined]
        # A stub must fail, not hang: the retry path sleeps seconds per attempt.
        self.mod.time.sleep = self.slept.append  # type: ignore[attr-defined]

    def _fake_api(self, method, *, token=None, body=None, **kwargs):
        self.sent.append(body or {})
        if self.responses:
            return self.responses.pop(0)
        return {"ok": True, "ts": f"17900000{len(self.sent):02d}.000000"}

    def test_a_short_body_posts_exactly_once(self) -> None:
        resp = self.mod.post_message("C123", "hello")
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("chunk_ts", resp)

    def test_the_first_ts_is_returned_not_the_last(self) -> None:
        resp = self.mod.post_message("C123", standup(200))
        self.assertGreater(len(self.sent), 1)
        self.assertEqual(resp["ts"], "1790000001.000000")
        self.assertEqual(len(resp["chunk_ts"]), len(self.sent) - 1)

    def test_each_chunk_carries_the_posting_identity(self) -> None:
        """A continuation posted as the bot under a user's standup is a wrong author."""
        self.mod.post_message(
            "C123", standup(200), username="Jerome Curlier", icon_url="https://x/a.png",
            thread_ts="1790000000.000000",
        )
        for body in self.sent:
            self.assertEqual(body["channel"], "C123")
            self.assertEqual(body["username"], "Jerome Curlier")
            self.assertEqual(body["thread_ts"], "1790000000.000000")
            self.assertIs(body["unfurl_links"], False)

    def test_a_failed_first_chunk_posts_nothing_further(self) -> None:
        """Half a standup is worse than none — and the caller only sees resp['ok']."""
        self.responses = [{"ok": False, "error": "channel_not_found"}]
        resp = self.mod.post_message("C123", standup(200))
        self.assertFalse(resp["ok"])
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("ts", resp)

    def test_a_failed_later_chunk_does_not_return_the_first_chunks_success(self) -> None:
        """The reported defect: a partial post read as clean.

        The first chunk lands, a later one doesn't, and returning the first
        response hands the caller `ok: True` with a real ts for a message that is
        missing most of its body. A wrong value is loud; a missing one has no
        shape, so the failure has to be the thing that comes back.
        """
        self.responses = [
            {"ok": True, "ts": "1790000001.000000"},
            {"ok": False, "error": "msg_too_long"},
        ]
        resp = self.mod.post_message("C123", standup(200))
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"], "msg_too_long")
        # ...and says what DID land, so the partial post can be found.
        self.assertTrue(resp["partial"])
        self.assertEqual(resp["ts"], "1790000001.000000")
        self.assertEqual(resp["posted_chunks"], 1)
        self.assertGreater(resp["total_chunks"], 1)
        self.assertEqual(len(self.sent), 2)  # stopped; no out-of-order tail

    def test_a_ratelimited_chunk_is_retried_not_dropped(self) -> None:
        """Chunking creates the burst, so a chunk lost to it is a hole we opened."""
        self.responses = [
            {"ok": True, "ts": "1790000001.000000"},
            {"ok": False, "error": "ratelimited"},
            {"ok": False, "error": "ratelimited"},
            {"ok": True, "ts": "1790000002.000000"},
        ]
        resp = self.mod.post_message("C123", standup(200))
        self.assertTrue(resp["ok"])
        self.assertIn("1790000002.000000", resp["chunk_ts"])
        self.assertTrue(self.slept, "the retry must back off, not hammer")

    def test_a_non_ratelimit_error_is_not_retried(self) -> None:
        """Retrying `channel_not_found` just delays the failure four times over."""
        self.responses = [
            {"ok": True, "ts": "1790000001.000000"},
            {"ok": False, "error": "not_in_channel"},
        ]
        resp = self.mod.post_message("C123", standup(200))
        self.assertFalse(resp["ok"])
        self.assertEqual(len(self.sent), 2)

    def test_exhausted_retries_still_report_the_partial(self) -> None:
        self.responses = [{"ok": True, "ts": "1790000001.000000"}] + [
            {"ok": False, "error": "ratelimited"}
        ] * 10
        resp = self.mod.post_message("C123", standup(200))
        self.assertFalse(resp["ok"])
        self.assertTrue(resp["partial"])
        self.assertEqual(len(self.sent), 1 + self.mod.SLACK_CHUNK_RETRIES)

    def test_the_footer_lands_on_the_last_chunk_only(self) -> None:
        self.mod.build_footer = (  # type: ignore[attr-defined]
            lambda *a, **k: "_Generated by `shared:post-to-slack:1.0.0`_"
        )
        self.mod.post_message("C123", standup(200), footer_skill="shared:post-to-slack")
        texts = [body["text"] for body in self.sent]
        self.assertEqual(sum("Generated by" in text for text in texts), 1)
        self.assertIn("Generated by", texts[-1])


class CliSurfacesThePartial(unittest.TestCase):
    """`_check` exits on the error name alone — which loses the landed chunks.

    The library return carries `partial` / `ts` / `chunk_ts` so a partial post can
    be cleaned up. `cmd_post` is what `shared:daily-status` actually calls, and it
    routed that through `_check`, so the caller saw only "failed". Retrying then
    reposts chunks that are already on the channel: the recovery is a duplicate.
    """

    def setUp(self) -> None:
        self.mod = load(CANONICAL)
        self.mod._token = lambda token=None: "xoxb-test"  # type: ignore[attr-defined]
        self.mod._user_token = lambda token=None: "xoxp-test"  # type: ignore[attr-defined]
        self.mod.time.sleep = lambda seconds: None  # type: ignore[attr-defined]

    def _args(self, **over):
        import argparse
        base = dict(
            channel="C123", text=standup(200), text_file=None, token=None,
            thread_ts=None, unfurl=False, username=None, icon_url=None,
            as_user=False, footer=None, footer_extra=None, footer_version=None,
            footer_runner=None, footer_skill_md=None, date=None, permalink=False,
            thread_overflow=False,
        )
        base.update(over)
        return argparse.Namespace(**base)

    def test_a_partial_post_exits_non_zero_but_still_names_what_landed(self) -> None:
        import contextlib, io
        responses = [
            {"ok": True, "ts": "1790000001.000000"},
            {"ok": False, "error": "msg_too_long"},
        ]
        self.mod._api = lambda *a, **k: responses.pop(0)  # type: ignore[attr-defined]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.mod.cmd_post(self._args())
        self.assertEqual(code, 1)  # a partial post is not a success
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["partial"])
        self.assertEqual(payload["ts"], "1790000001.000000")
        self.assertEqual(payload["posted_chunks"], 1)
        # and the operator is told not to repost blind
        self.assertIn("duplicate", err.getvalue())

    def test_a_total_failure_still_exits_rather_than_printing_a_partial(self) -> None:
        import contextlib, io
        self.mod._api = lambda *a, **k: {"ok": False, "error": "channel_not_found"}  # type: ignore[attr-defined]
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.mod.cmd_post(self._args())



HEADING = re.compile(r"^(?:\*[^\s*][^\n]*\*|_[^\s_][^\n]*_)[ \t]*$")


def sectioned(completed: int, pad: int = 0) -> str:
    """Completed then Blocked, with a blank line under each section header.

    That blank line is what the 2026-09-25 split latched onto: the latest blank
    line in the window sat right after `*Blocked [10]*`, so the header ended
    message 1 and its cards arrived in message 2. `pad` slides the boundary.
    """
    out = ["*Standup — Friday, September 25*", "", f"*Completed [{completed}]*", ""]
    for bucket in range(completed // 5):
        out += [f"_Bucket {bucket}_"]
        out += [f"• Did a thing that took a sentence to describe. ({link(6000 + bucket * 5 + i)})"
                for i in range(5)]
        out += [""]
    if pad:
        out += ["• " + "x" * pad, ""]
    out += ["*Blocked [10]*", ""]
    out += [f"• Waiting on someone else. ({link(7000 + i)})" for i in range(10)]
    return "\n".join(out)


def blocked_at_window_end(mod) -> str:
    """A body whose `*Blocked [10]*` header sits in the last lines of chunk 1."""
    for completed in range(20, 60, 5):
        for pad in range(0, 400):
            body = sectioned(completed, pad)
            at = body.find("*Blocked [10]*")
            if mod.SLACK_TEXT_LIMIT - 40 < at < mod.SLACK_TEXT_LIMIT - 16:
                return body
    raise AssertionError("fixture could not place the header at the window end")


class SplitAtSectionStart(unittest.TestCase):
    """A chunk starts at a section when it can, and never ends on a heading."""

    def test_the_incident_header_opens_the_next_chunk(self) -> None:
        for path in COPIES:
            mod = load(path)
            with self.subTest(path=path.parts[-3]):
                chunks = mod.split_for_slack(blocked_at_window_end(mod))
                self.assertEqual(len(chunks), 2)
                self.assertNotIn("*Blocked [10]*", chunks[0])
                self.assertTrue(chunks[1].startswith("*Blocked [10]*"))

    def test_a_section_start_beats_a_later_blank_line(self) -> None:
        """Blank lines inside Completed come later in the window; the section wins."""
        body = sectioned(20) + "\n\n" + "\n\n".join(
            f"• Another blocked card. ({link(8000 + i)})" for i in range(40))
        for path in COPIES:
            mod = load(path)
            with self.subTest(path=path.parts[-3]):
                # the section opens past the half-window floor, blank lines follow it
                self.assertGreater(body.find("*Blocked"), mod.SLACK_TEXT_LIMIT // 2)
                chunks = mod.split_for_slack(body)
                self.assertGreater(len(chunks), 1)
                self.assertTrue(chunks[1].startswith("*Blocked [10]*"), chunks[1][:40])

    def test_no_chunk_ends_on_a_heading_across_every_offset(self) -> None:
        """Slide the boundary through every position; the property must hold at each.

        The origin splitter orphaned a heading at 400 of the 800 offsets this walks.
        """
        for path in COPIES:
            mod = load(path)
            for completed in range(20, 40):
                for pad in range(0, 120, 3):
                    body = sectioned(completed, pad)
                    if len(body) <= mod.SLACK_TEXT_LIMIT:
                        continue
                    chunks = mod.split_for_slack(body)
                    with self.subTest(path=path.parts[-3], completed=completed, pad=pad):
                        for chunk in chunks:
                            self.assertLessEqual(len(chunk), mod.SLACK_TEXT_LIMIT)
                        for chunk in chunks[:-1]:
                            last = chunk.rstrip().rsplit("\n", 1)[-1]
                            self.assertIsNone(HEADING.match(last), last)
                        lines = [l for l in body.split("\n") if l.strip()]
                        out = [l for c in chunks for l in c.split("\n") if l.strip()]
                        self.assertEqual(out, lines)

    def test_a_body_that_is_only_headings_still_terminates(self) -> None:
        body = "\n".join(f"*Section {i}*" for i in range(600))
        for path in COPIES:
            mod = load(path)
            with self.subTest(path=path.parts[-3]):
                chunks = mod.split_for_slack(body)
                self.assertEqual("\n".join(chunks).split("\n"), body.split("\n"))


class OverflowThreading(unittest.TestCase):
    """`thread_overflow` keeps a long post as one message plus its thread.

    Without it, chunk 2 of the 2026-09-25 standup landed as a stray top-level
    message in #standup. Every copy carries the flag, so every copy is checked.
    """

    def _stub(self, mod, responses=None):
        sent: list[dict] = []
        queue = list(responses or [])

        def fake_api(method, *, token=None, body=None, **kwargs):
            sent.append(body or {})
            if queue:
                return queue.pop(0)
            return {"ok": True, "ts": f"17900000{len(sent):02d}.000000"}

        mod._api = fake_api
        mod._token = lambda token=None: "xoxb-test"
        mod._user_token = lambda token=None: "xoxp-test"
        mod.time.sleep = lambda seconds: None
        return sent

    def test_overflow_chunks_thread_under_the_first(self) -> None:
        for path in COPIES:
            mod = load(path)
            sent = self._stub(mod)
            with self.subTest(path=path.parts[-3]):
                resp = mod.post_message("C123", standup(200), as_user=True, thread_overflow=True)
                self.assertTrue(resp["ok"])
                self.assertGreater(len(sent), 2)
                self.assertNotIn("thread_ts", sent[0])
                for body in sent[1:]:
                    self.assertEqual(body["thread_ts"], resp["ts"])
                self.assertEqual(resp["ts"], "1790000001.000000")
                self.assertEqual(len(resp["chunk_ts"]), len(sent) - 1)

    def test_default_is_unchanged_top_level_chunks(self) -> None:
        for path in COPIES:
            mod = load(path)
            sent = self._stub(mod)
            with self.subTest(path=path.parts[-3]):
                mod.post_message("C123", standup(200))
                self.assertGreater(len(sent), 1)
                self.assertFalse(any("thread_ts" in body for body in sent))

    def test_an_existing_thread_keeps_every_chunk(self) -> None:
        """Replying into a thread: the overflow goes there too, not under chunk 1."""
        mod = load(CANONICAL)
        sent = self._stub(mod)
        mod.post_message("C123", standup(200), thread_ts="1789999999.000000",
                         thread_overflow=True)
        self.assertTrue(all(b["thread_ts"] == "1789999999.000000" for b in sent))

    def test_a_failed_first_chunk_threads_nothing(self) -> None:
        mod = load(CANONICAL)
        sent = self._stub(mod, [{"ok": False, "error": "not_in_channel"}])
        resp = mod.post_message("C123", standup(200), thread_overflow=True)
        self.assertFalse(resp["ok"])
        self.assertEqual(len(sent), 1)

    def test_a_failed_reply_reports_the_partial_under_the_parent(self) -> None:
        mod = load(CANONICAL)
        sent = self._stub(mod, [
            {"ok": True, "ts": "1790000001.000000"},
            {"ok": False, "error": "thread_not_found"},
        ])
        resp = mod.post_message("C123", standup(200), thread_overflow=True)
        self.assertFalse(resp["ok"])
        self.assertTrue(resp["partial"])
        self.assertEqual(resp["ts"], "1790000001.000000")
        self.assertEqual(sent[1]["thread_ts"], "1790000001.000000")
        self.assertEqual(len(sent), 2)

    def test_the_cli_flag_reaches_the_post(self) -> None:
        """The flag daily-status passes must be the one `cmd_post` forwards."""
        import contextlib, io
        mod = load(CANONICAL)
        sent = self._stub(mod)
        args = mod.build_parser().parse_args(
            ["post", "--channel", "C123", "--text", standup(200), "--thread-overflow"])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(mod.cmd_post(args), 0)
        payload = json.loads(out.getvalue())
        self.assertTrue(all(b["thread_ts"] == payload["ts"] for b in sent[1:]))
        self.assertEqual(len(payload["chunk_ts"]), len(sent) - 1)


if __name__ == "__main__":
    unittest.main()
