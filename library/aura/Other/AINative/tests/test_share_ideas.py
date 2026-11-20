"""Tests for share-ideas' message shape.

The thread a reviewer sees is: a short parent post, then two replies (reasoning,
hypothesis), then the design sheet. These pin that split — the parent must stay a
summary, and the reasoning must not be lost in the move.
"""

from __future__ import annotations

import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "plugins", "product", "skills", "share-ideas", "scripts")
sys.path.insert(0, SCRIPTS)

import share_ideas as s  # noqa: E402


def idea(**over):
    base = {
        "page_id": "p1",
        "idea_id": "IDEA-1",
        "title": "Add social proof below the tagline",
        "status": "pending",
        "screen": "welcome_screen",
        "category": "conversion",
        "lever": "activation",
        "impact": "high",
        "effort": "S",
        "description": "The tagline is poetic but abstract; add one concrete trust line.",
        "rationale": "Users landing here have no context about what Aurie does.",
        "hypothesis": "If we add a trust line, Get Started tap rate improves.",
        "fingerprint": "abc123def456",
        "slack_thread": "",
        "screenshot_url": "",
        "screenshot_urls": [],
    }
    base.update(over)
    return base


class RenderMessage(unittest.TestCase):
    def test_parent_is_a_summary_with_the_idea_id_first(self):
        text = s.render_message(idea())
        self.assertTrue(text.startswith("*IDEA-1 · Add social proof below the tagline*"))
        self.assertIn("`welcome_screen`", text)
        self.assertIn("Impact: High", text)
        self.assertIn("Effort: Small", text)
        self.assertIn("*The idea*", text)
        self.assertTrue(text.endswith("add one concrete trust line."))

    def test_parent_carries_no_protocol_and_no_footer(self):
        # The channel intro carries the protocol; the header already is the address.
        text = s.render_message(idea(screenshot_urls=["https://x/a.png"]))
        self.assertNotIn("Reply to decide", text)
        self.assertNotIn(s.DECISION_PROTOCOL, text)
        self.assertNotIn("_Idea `IDEA-1`_", text)
        self.assertEqual(text.count("IDEA-1"), 1)

    def test_reasoning_and_hypothesis_are_not_in_the_parent(self):
        text = s.render_message(idea())
        self.assertNotIn("no context about what Aurie does", text)
        self.assertNotIn("tap rate improves", text)
        self.assertNotIn("Why it lifts the lever", text)
        self.assertNotIn("Hypothesis", text)

    def test_the_parent_never_announces_the_pictures(self):
        """They are the next thing in the thread; a sentence saying so is only a line
        between the reader and the idea. Held whether the row carries files or not, so
        the hint cannot creep back for the case it was originally written for."""
        for row in (idea(), idea(screenshot_urls=["https://x/a.png"]),
                    idea(screenshot_url="https://x/a.png")):
            self.assertNotIn("Current vs Proposed", s.render_message(row))
            self.assertNotIn("🖼", s.render_message(row))

    def test_protocol_text_still_names_the_redesign_path(self):
        # kept as the canonical channel-topic wording, even though posts no longer carry it
        self.assertIn("not the design", s.DECISION_PROTOCOL)

    def test_falls_back_to_fingerprint_without_an_idea_id(self):
        text = s.render_message(idea(idea_id=""))
        self.assertIn("abc123def456", text)


class RenderReplies(unittest.TestCase):
    def test_two_replies_in_order(self):
        replies = s.render_replies(idea())
        self.assertEqual(len(replies), 2)
        self.assertTrue(replies[0].startswith("*Why it lifts the lever*\n"))
        self.assertIn("no context about what Aurie does", replies[0])
        self.assertTrue(replies[1].startswith("*Hypothesis*\n"))
        self.assertIn("tap rate improves", replies[1])

    def test_empty_fields_post_nothing(self):
        self.assertEqual(s.render_replies(idea(rationale="", hypothesis="")), [])
        self.assertEqual(len(s.render_replies(idea(hypothesis=""))), 1)


class FileUrls(unittest.TestCase):
    def _row(self, files):
        return {"id": "p", "properties": {"Screenshot": {"files": files}}}

    def test_all_urls_in_row_order(self):
        row = self._row([
            {"name": "sheet.png", "file": {"url": "https://n/sheet"}},
            {"name": "a.png", "external": {"url": "https://n/a"}},
            {"name": "broken.png", "file": {}},
        ])
        self.assertEqual(s._file_urls(row, "Screenshot"), ["https://n/sheet", "https://n/a"])
        self.assertEqual(s._first_file_url(row, "Screenshot"), "https://n/sheet")

    def test_empty(self):
        self.assertEqual(s._file_urls(self._row([]), "Screenshot"), [])
        self.assertEqual(s._first_file_url(self._row([]), "Screenshot"), "")
        self.assertEqual(s._file_urls({"id": "p", "properties": {}}, "Screenshot"), [])

    def test_files_keep_names_in_row_order(self):
        row = self._row([
            {"name": "Current vs Proposed.png", "file": {"url": "https://n/sheet"}},
            {"name": "a · Trust badge, above the orb.png", "external": {"url": "https://n/a"}},
        ])
        self.assertEqual(s._files(row, "Screenshot"), [
            ("Current vs Proposed.png", "https://n/sheet"),
            ("a · Trust badge, above the orb.png", "https://n/a"),
        ])


class AttachIsolation(unittest.TestCase):
    """One failed picture must never cost the pictures after it — the parent is already
    `shared`, so nothing comes back for the missing ones."""

    def test_a_failing_upload_does_not_drop_later_files(self):
        import types
        from unittest import mock
        calls = []

        def fake_upload(channel, paths, *, thread_ts=None, comment=None, **kw):
            calls.append(comment)
            if len(calls) == 2:
                raise SystemExit("slack said no")   # what upload_files does on a Slack error
            return {"ok": True}

        fake_resp = types.SimpleNamespace(content=b"png", raise_for_status=lambda: None)
        with mock.patch.object(s, "upload_files", fake_upload), \
             mock.patch("requests.get", return_value=fake_resp), \
             mock.patch.object(s.time, "sleep"):
            # Single-screen variants are on the combined sheet and are not posted;
            # flows are — so the files after the sheet are flows here.
            ok = s._attach_screenshots("C1", "1.2", [
                ("Current vs Proposed.png", "https://n/sheet"),
                ("Flow a · after the tap.png", "https://n/a"),
                ("Flow c · after the tap.png", "https://n/c"),
            ], "fp")
        self.assertFalse(ok)                      # the run reports the failure…
        self.assertEqual(len(calls), 3)           # …but every file was attempted
        self.assertEqual(calls[2], "Flow c · after the tap")
        self.assertIn("b is the current screen (control)", calls[0])


class CaptionFor(unittest.TestCase):
    def test_labelled_names_are_the_caption(self):
        self.assertEqual(s._caption_for("a · Trust badge, above the orb.png"),
                         "a · Trust badge, above the orb")

    def test_bare_stems_are_humanised(self):
        self.assertEqual(s._caption_for("idea1_c.png"), "Variant c")
        self.assertEqual(s._caption_for("idea12_compare.png"), "Current vs Proposed")
        self.assertEqual(s._caption_for("idea1_current.png"), "Current · control (b)")

    def test_empty_name(self):
        self.assertEqual(s._caption_for(""), "Design")


class ThreadFromPermalink(unittest.TestCase):
    def test_parses_channel_and_ts(self):
        got = s._thread_from_permalink(
            "https://auramobile.slack.com/archives/C0C1JTK3YCC/p1789694236837849")
        self.assertEqual(got, ("C0C1JTK3YCC", "1789694236.837849"))

    def test_with_query_string(self):
        got = s._thread_from_permalink(
            "https://auramobile.slack.com/archives/C0AV0U4HN6P/p1789696694350479"
            "?thread_ts=1789696599.176149&cid=C0AV0U4HN6P")
        self.assertEqual(got, ("C0AV0U4HN6P", "1789696694.350479"))

    def test_not_a_permalink(self):
        self.assertIsNone(s._thread_from_permalink(""))
        self.assertIsNone(s._thread_from_permalink("https://notion.so/some-page"))



class ChannelResolution(unittest.TestCase):
    """Each loop posts to its own channel, derived from the database it shares FROM.

    The destination used to be a module constant, so a caller that got
    --data-source-id right for a second loop would still post those ideas into
    the Aurie channel — a cross-post with nothing to catch it.
    """

    def test_the_aurie_default_is_unchanged(self):
        self.assertEqual(
            s.resolve_channel(s.AURIE_FEATURES_DS),
            s.LOOP_CHANNEL_ID)

    def test_a_mapped_loop_resolves_to_its_own_channel(self):
        self.assertEqual(
            s.resolve_channel(s.AURA_RECOMMENDATIONS_DS),
            "C0C0ZSMU7C7")

    def test_an_explicit_channel_overrides_the_map(self):
        self.assertEqual(
            s.resolve_channel(s.AURIE_FEATURES_DS, "C0OVERRIDE"),
            "C0OVERRIDE")

    def test_an_unmapped_loop_warns_rather_than_defaulting_silently(self):
        import contextlib, io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = s.resolve_channel("00000000-0000-0000-0000-000000000000")
        self.assertEqual(got, s.LOOP_CHANNEL_ID)
        self.assertIn("no channel mapped", err.getvalue())

    def test_the_parser_accepts_channel(self):
        args = s.build_parser().parse_args(["--channel", "C0X", "preview"])
        self.assertEqual(args.channel, "C0X")

    def test_channel_defaults_to_empty_so_the_map_decides(self):
        args = s.build_parser().parse_args(["preview"])
        self.assertEqual(args.channel, "")

if __name__ == "__main__":
    unittest.main()
