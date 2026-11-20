"""Guard tests for who `product:manage-ideas` is allowed to name as a decider.

`Decided by` / `Decided at` are the record of a **person's Gate 1 reply in Slack**.
Nothing else may write them. An agent triaging the backlog — re-validating, deferring,
rejecting before anything is posted — has no decider to name, and naming one anyway is
not a cosmetic slip: it is a false record of a decision, and Notion pages the person for
each row. That happened to 26 rows in one pass on 2026-09-30.

So the CLI refuses `--by` / `--by-id` without `--decided-in`, the permalink of the reply
being claimed; it checks that the link is a link; and it writes the link onto the row, so
the name has its evidence beside it rather than only in someone's shell history.

`harvest-idea-decisions` is deliberately not routed through here: it *derives* the decider
from a reply it has actually read, so it carries the evidence by construction.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "plugins", "product", "skills", "manage-ideas", "scripts"))

import manage_ideas as m  # noqa: E402

PERMALINK = "https://auramobile.slack.com/archives/C0C1JTK3YCC/p1790751790455289"


def args(**kw) -> argparse.Namespace:
    base = dict(data_source_id="ds", ref="IDEA-1", note=None, reason=None,
                by=None, by_id=None, decided_in=None, at=None)
    base.update(kw)
    return argparse.Namespace(**base)


class Parser(unittest.TestCase):
    """Every verdict subcommand takes the flag, so none of them is a back door."""

    def test_every_decision_subcommand_accepts_decided_in(self):
        p = m.build_parser()
        for cmd, extra in (("approve", []), ("reject", ["--reason", "duplicate"]),
                           ("set-status", ["deferred", "--reason", "timing"])):
            ns = p.parse_args([cmd, "IDEA-1", *extra, "--by", "steve", "--decided-in", PERMALINK])
            self.assertEqual(ns.decided_in, PERMALINK, cmd)


class Refusals(unittest.TestCase):
    """The four ways a name can be asserted without evidence, all closed.

    `_set_status` reaches Notion, so these assert it refuses *before* that — each case
    raises rather than returning, and none of them needs a token.
    """

    def _refused(self, ns) -> str:
        with self.assertRaises(SystemExit) as e:
            m._set_status(ns, "approved")
        return str(e.exception)

    def test_by_without_decided_in_is_refused(self):
        self.assertIn("--decided-in", self._refused(args(by="steve")))

    def test_by_id_without_decided_in_is_refused(self):
        self.assertIn("--decided-in", self._refused(args(by_id="00000000-0000-0000-0000-000000000000")))

    def test_a_decided_in_that_is_not_a_permalink_is_refused(self):
        for bad in ("he said yes in the thread", "https://example.com/thread",
                    "https://auramobile.slack.com/archives/C0C1JTK3YCC"):
            self.assertIn("permalink", self._refused(args(by="steve", decided_in=bad)), bad)

    def test_at_without_a_decider_is_refused(self):
        # Silently ignoring it would read as a working backdate.
        self.assertIn("--at", self._refused(args(at="2026-04-17")))


class RefusedBeforeAnyToken(unittest.TestCase):
    """A malformed invocation is refused without reaching for a Notion token.

    This is not a detail: the guards originally sat *after* the token check, so they
    passed locally (a token is in the environment) and errored in CI with
    `NOTION_TOKEN is not set` — the wrong error for the wrong reason. Validating the
    arguments first is also simply right; nothing about a missing `--decided-in` needs
    a network.
    """

    def test_the_guards_do_not_need_notion(self):
        class NoToken:
            available = False

            def __getattr__(self, name):  # any call would be a bug — the guard runs first
                raise AssertionError(f"reached notion.{name} without a token")

        real, m.notion = m.notion, NoToken()
        try:
            with self.assertRaises(SystemExit) as e:
                m._set_status(args(by="steve"), "approved")
            self.assertIn("--decided-in", str(e.exception))
        finally:
            m.notion = real


class SummaryTellsTheTruthAboutTheRow(unittest.TestCase):
    """What the confirmation line says must match what Notion ends up holding.

    Three separate bugs lived in this one block: it printed a date that no longer
    exists, it reported an attributed row as unattributed, and it printed a Python
    list where a person's name belongs. All three were claims about the database that
    were not true, on the command whose whole subject is truthful attribution.
    """

    def _run(self, row, ns):
        written = {}
        real_ref, real_notion = m._resolve_ref, m.notion

        class Stub:
            available = True

            @staticmethod
            def update_page_props(page_id, props):
                written.update(props)

        m._resolve_ref = lambda ds, ref: row
        m.notion = Stub()
        buf, real_out = io.StringIO(), sys.stdout
        try:
            sys.stdout = buf
            m._set_status(ns, "approved")
        finally:
            sys.stdout = real_out
            m._resolve_ref, m.notion = real_ref, real_notion
        return buf.getvalue(), written

    @staticmethod
    def row(**kw):
        base = dict(page_id="p", fingerprint="abc123", status="pending", title="An idea",
                    status_note="", decided_by=[], decided_at=None)
        base.update(kw)
        return base

    def test_an_existing_decider_is_named_not_listed(self):
        out, _ = self._run(self.row(decided_by=["Steve Lee"], decided_at="2026-04-17"), args())
        line = next(l for l in out.splitlines() if "decided by" in l)
        self.assertIn("decided by Steve Lee on 2026-04-17", line)
        self.assertNotIn("[", line)   # a list repr, not a name

    def test_an_unattributed_triage_claims_no_date_and_no_decider(self):
        out, written = self._run(self.row(), args(note="Triage (agent): still valid"))
        self.assertIn("unattributed", out)
        self.assertNotIn("None", out)
        self.assertNotIn("Decided at", written)
        self.assertNotIn("Decided by", written)

    def test_the_permalink_lands_on_the_row_and_in_the_summary(self):
        real_user = m._resolve_user
        m._resolve_user = lambda ref: {"id": "u1", "name": "Steve Lee"}
        try:
            out, written = self._run(self.row(), args(by="steve", decided_in=PERMALINK))
        finally:
            m._resolve_user = real_user
        self.assertIn(PERMALINK, out)
        note = "".join(t["text"]["content"] for t in written["Quick status note"]["rich_text"])
        self.assertIn(f"Decided in: {PERMALINK}", note)

    def test_the_note_explaining_a_verdict_is_never_erased_by_it(self):
        real_user = m._resolve_user
        m._resolve_user = lambda ref: {"id": "u1", "name": "Steve Lee"}
        try:
            _, written = self._run(self.row(status_note="Bundle with the home refresh"),
                                   args(by="steve", decided_in=PERMALINK))
        finally:
            m._resolve_user = real_user
        note = "".join(t["text"]["content"] for t in written["Quick status note"]["rich_text"])
        self.assertIn("Bundle with the home refresh", note)
        self.assertIn(PERMALINK, note)


class PermalinkShape(unittest.TestCase):
    def test_accepts_what_slacks_copy_link_produces(self):
        self.assertTrue(m.SLACK_PERMALINK.match(PERMALINK))
        self.assertTrue(m.SLACK_PERMALINK.match(
            "https://aura-health.slack.com/archives/C09ABCDEF/p1234567890123456?thread_ts=1.2&cid=C09"))

    def test_rejects_prose_and_other_hosts(self):
        for bad in ("", "the thread", "http://auramobile.slack.com/archives/C1/p1",
                    "https://slack.com/archives/C1/p1", "https://notion.so/x"):
            self.assertIsNone(m.SLACK_PERMALINK.match(bad), bad)


if __name__ == "__main__":
    unittest.main()
