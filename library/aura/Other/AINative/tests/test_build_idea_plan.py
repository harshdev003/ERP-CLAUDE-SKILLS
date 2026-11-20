"""`build-idea record-plan` — the build plan is written onto the idea's Notion row.

Every build starts with a plan (step 0). The plan is recorded on the row under a dated
heading, in Notion blocks, so the decision it leads to — continue or `hold` — has its
reasoning beside it. Notion is stubbed at the `notion` client.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "plugins", "product", "skills", "build-idea", "scripts"))

import build_idea as b  # noqa: E402

PLAN = """## What changes
The paywall headline becomes the archetype title.

## Outside the app
- none

## Open questions
- (1) How many free conversations — **N** or a week?
- (2) Who absorbs the cost
"""


def _texts(blocks):
    out = []
    for blk in blocks:
        body = blk[blk["type"]]
        out.append((blk["type"], "".join(r["text"]["content"] for r in body.get("rich_text", []))))
    return out


class PlanBlocks(unittest.TestCase):
    def test_shape(self):
        got = _texts(b.plan_blocks(PLAN, "2026-10-06"))
        self.assertEqual(got[0][0], "divider")
        self.assertEqual(got[1], ("heading_2", "Build plan — 2026-10-06"))
        self.assertIn(("heading_3", "What changes"), got)
        self.assertIn(("paragraph", "The paywall headline becomes the archetype title."), got)
        self.assertIn(("bulleted_list_item", "none"), got)
        # inline markdown is parsed, never left as raw `**`
        q = [t for kind, t in got if kind == "bulleted_list_item" and t.startswith("(1)")][0]
        self.assertNotIn("**", q)

    def test_wrapped_lines_join_into_one_paragraph(self):
        got = _texts(b.plan_blocks("one line\nsame paragraph\n\nnext", "2026-01-01"))
        self.assertEqual([t for kind, t in got if kind == "paragraph"],
                         ["one line same paragraph", "next"])


class RecordPlan(unittest.TestCase):
    def setUp(self):
        self.notion = mock.patch.object(b, "notion").start()
        self.notion.available = True
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(b, "find_idea", return_value={
            "page_id": "page", "idea_id": "IDEA-9", "fingerprint": "f", "title": "t"}).start()

    def _run(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
            fh.write(text)
        self.addCleanup(os.unlink, fh.name)
        return b.cmd_record_plan(argparse.Namespace(data_source_id="ds", ref="IDEA-9", file=fh.name))

    def test_appends_to_the_row_page(self):
        self.assertEqual(self._run(PLAN), 0)
        page, blocks = self.notion.append_block_children.call_args.args
        self.assertEqual(page, "page")
        self.assertEqual(blocks[1]["type"], "heading_2")

    def test_empty_plan_is_refused(self):
        self.assertEqual(self._run("  \n"), 2)
        self.notion.append_block_children.assert_not_called()

    def test_long_plans_are_sent_in_batches_of_100(self):
        self.assertEqual(self._run("\n".join(f"- item {i}" for i in range(250))), 0)
        sizes = [len(c.args[1]) for c in self.notion.append_block_children.call_args_list]
        self.assertEqual(sizes, [100, 100, 52])

    def test_the_plan_is_posted_to_the_card_when_there_is_one(self):
        b.find_idea.return_value = {**b.find_idea.return_value,
                                    "linear": "https://linear.app/a/issue/ENGMOB-1/x"}
        with mock.patch.object(b, "_comment_on_card") as comment:
            self.assertEqual(self._run(PLAN), 0)
        url, body = comment.call_args.args
        self.assertIn("ENGMOB-1", url)
        self.assertIn("Build plan", body)


class Card(unittest.TestCase):
    IDEA = {"page_id": "page", "idea_id": "IDEA-9", "fingerprint": "f", "title": "t",
            "status": "approved", "linear": "", "experiment": "x", "slack": "s",
            "screen": "", "domain": "", "lever": "", "description": "", "rationale": "",
            "hypothesis": ""}

    def setUp(self):
        self.notion = mock.patch.object(b, "notion").start()
        self.notion.available = True
        self.addCleanup(mock.patch.stopall)

    def _card(self, idea, **kw):
        mock.patch.object(b, "find_idea", return_value=idea).start()
        args = argparse.Namespace(data_source_id="ds", ref="IDEA-9", linear=None,
                                  experiment=None, post=True, **kw)
        return b.cmd_card(args)

    def test_a_new_card_is_recorded_linked_and_announced_once(self):
        made = {"id": "u1", "url": "https://linear.app/a/issue/ENGMOB-7/x"}
        with mock.patch.object(b, "_create_card", return_value=made), \
             mock.patch.object(b, "_linear", return_value={"id": "u1"}), \
             mock.patch.object(b, "_attach_thread") as attach, \
             mock.patch.object(b, "_reply_in_thread") as reply:
            self.assertEqual(self._card(dict(self.IDEA)), 0)
        attach.assert_called_once()
        reply.assert_called_once()
        self.notion.update_page_props.assert_called_once()

    def test_an_existing_card_is_linked_but_not_re_announced(self):
        idea = {**self.IDEA, "linear": "https://linear.app/a/issue/ENGMOB-7/x"}
        with mock.patch.object(b, "_create_card") as create, \
             mock.patch.object(b, "_linear", return_value={"id": "u1"}), \
             mock.patch.object(b, "_attach_thread") as attach, \
             mock.patch.object(b, "_reply_in_thread") as reply:
            self.assertEqual(self._card(idea), 0)
        create.assert_not_called()
        attach.assert_called_once()
        reply.assert_not_called()

    def test_a_rejected_idea_gets_no_card(self):
        self.assertEqual(self._card({**self.IDEA, "status": "rejected"}), 2)

    def test_the_thread_falls_back_to_a_url_attachment(self):
        with mock.patch.object(b, "_link_slack", return_value=False), \
             mock.patch.object(b, "_linear", return_value={"id": "a"}) as lin:
            b._attach_thread("u1", {**self.IDEA, "slack": "https://x/archives/C1/p1"})
        self.assertEqual(lin.call_args.args[0], "attach-url")

    def test_a_slack_synced_link_needs_no_fallback(self):
        with mock.patch.object(b, "_link_slack", return_value=True), \
             mock.patch.object(b, "_linear") as lin:
            b._attach_thread("u1", {**self.IDEA, "slack": "https://x/archives/C1/p1"})
        lin.assert_not_called()


if __name__ == "__main__":
    unittest.main()
