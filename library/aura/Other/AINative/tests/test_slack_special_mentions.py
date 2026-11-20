#!/usr/bin/env python3
"""No Slack message template under plugins/ pages with a bare @channel / @here.

Skills post through `shared:post-to-slack` (`slack_post.py`, i.e. `chat.postMessage`).
Over the API a typed `@channel` is plain text and notifies no one — only the
special-mention syntax `<!channel>` / `<!here>` pages. On 2026-10-08 four
high-priority guardrail pages in #guardrails-conversation went out as inert
`@channel` and had to be re-posted by hand: both guardrail skills' Step 6b
templates told the model to write the literal, and one claimed it "fires the
notification even inside the thread".

A template is a fenced block the model copies verbatim, so the check is every
fenced block in every markdown file under plugins/ (SKILL.md and the references/
it extracts to). Prose that merely *names* the page ("the @channel page") is out
of scope — it never reaches Slack.

Stdlib only, no network.
"""
from __future__ import annotations

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
MARKDOWN = sorted((REPO / "plugins").rglob("*.md"))

FENCE = re.compile(r"^\s*(```|~~~)")
# `@channel` / `@here` not already inside `<!…>`; `<!channel>` has no `@` at all.
BARE_MENTION = re.compile(r"(?<![\w<!])@(channel|here|everyone)\b")


def bare_mentions_in_fences(text: str) -> list[tuple[int, str]]:
    """(line number, line) for every fenced line carrying a bare special mention."""
    hits, in_fence = [], False
    for n, line in enumerate(text.split("\n"), 1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence and BARE_MENTION.search(line):
            hits.append((n, line.strip()))
    return hits


class DetectorTests(unittest.TestCase):
    def test_flags_the_incident_template(self):
        text = "Post:\n\n```\n@channel 🚨 HIGH PRIORITY — x · UID `abcd1234…`\nReviewer needed.\n```\n"
        self.assertEqual([n for n, _ in bare_mentions_in_fences(text)], [4])

    def test_flags_here_inside_a_line(self):
        self.assertTrue(bare_mentions_in_fences("```\nheads up @here please\n```"))

    def test_accepts_special_mention_syntax(self):
        text = "```\n<!channel> 🚨 HIGH PRIORITY\n<!here> ping\n```"
        self.assertEqual(bare_mentions_in_fences(text), [])

    def test_ignores_prose_outside_fences(self):
        self.assertEqual(bare_mentions_in_fences("### Step 6b — High-priority @channel page\n"), [])

    def test_ignores_email_addresses(self):
        self.assertEqual(bare_mentions_in_fences("```\nops@here.example\n```"), [])


class RepoTests(unittest.TestCase):
    def test_corpus_is_nonempty(self):
        self.assertGreater(len(MARKDOWN), 100)

    def test_no_bare_special_mentions_in_templates(self):
        offenders = [
            f"{p.relative_to(REPO)}:{n}: {line}"
            for p in MARKDOWN
            for n, line in bare_mentions_in_fences(p.read_text(encoding="utf-8"))
        ]
        self.assertEqual(
            offenders, [],
            "Slack's API pages only on <!channel> / <!here>; a bare @channel is plain text:\n"
            + "\n".join(offenders),
        )

    def test_guardrail_pages_still_use_special_mention(self):
        # The fix is the literal; losing the page entirely would also pass the check above.
        # A user-group mention (<!subteam^S…>) may sit between <!channel> and the label.
        page = re.compile(r"<!channel>(?: <!subteam\^S[A-Z0-9]+>)* 🚨 HIGH PRIORITY")
        text = (REPO / "plugins/eng/skills/investigate-memory-ingestion-guardrail/SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(text, page)

    def test_conversation_guardrail_tags_the_reviewer_not_cs(self):
        # The conversation triage tags the reviewer by user mention on levels 1 and 2, and
        # never Customer Support or the whole channel: CS is tagged only on an approved email.
        text = (REPO / "plugins/eng/skills/investigate-conversation-guardrail/SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(text, re.compile(r"<@U[A-Z0-9]+> 🚨 HIGH PRIORITY"))
        self.assertRegex(text, re.compile(r"<@U[A-Z0-9]+> for review"))
        fenced = "\n".join(re.findall(r"```.*?```", text, flags=re.S))
        self.assertNotIn("<!subteam^", fenced)
        self.assertNotIn("<!channel>", fenced)


if __name__ == "__main__":
    unittest.main()
