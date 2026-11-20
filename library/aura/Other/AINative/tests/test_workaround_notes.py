"""Detector #13 — repeated workaround notes (`shared:healthcheck-knowledge`).

The detector exists because a defect that is correctly diagnosed *in the
output* protects one row and nothing else. `Learnings/Experiments` carried one
such note word for word in 13 rows for months, and every guard in the repo
stayed green the whole time because none of them reads a row body.

The load-bearing test is `TestRealCorpus`: it runs the detector over a corpus
fetched from the live `Learnings/Experiments` database on 2026-09-19 and
asserts the 13-row cluster comes back. A detector for a known cluster that
cannot find that cluster is worth nothing, and a synthetic fixture cannot make
that assertion — so the fixture is real rows, produced by the real producer
([PROD-6225]).
"""

import json
import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "plugins/shared/skills/healthcheck-knowledge/scripts",
    ),
)

import workaround_notes as wn  # noqa: E402

FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "experiments_workaround_notes.json",
)

# The note, verbatim, that 13 rows carry. Kept here as a literal so a change to
# the fixture that loses the cluster fails loudly rather than silently passing
# on whatever the fixture happens to hold.
THE_NOTE = (
    "_Note: `SUL` and `Paid` land almost entirely in the `undefined` variant bucket "
    "— the `Experiment:` super-property is stamped at sign-up, before variant "
    "bucketing, so per-variant paid rate isn't recoverable from the raw cut."
)


def row(title, text, url=None, path="Learnings/Experiments", hrefs=None):
    """A row as blocks — the shape the crawl actually supplies.

    `hrefs` attaches to every block, which is enough for these fixtures: the
    cases that care about *which* block carries the link build their blocks
    explicitly via `block_row`.
    """
    return block_row(
        title,
        [{"text": line, "hrefs": list(hrefs or ())} for line in text.splitlines()],
        url=url, path=path,
    )


def block_row(title, blocks, url=None, path="Learnings/Experiments"):
    return {
        "path": path,
        "title": title,
        "url": url or f"https://www.notion.so/{title}",
        "blocks": [{"text": b.get("text", ""), "hrefs": list(b.get("hrefs") or ())}
                   for b in blocks],
    }


def text_row(title, text, url=None, path="Learnings/Experiments"):
    """The degraded flat-text shape, which cannot acquit."""
    return {"path": path, "title": title,
            "url": url or f"https://www.notion.so/{title}", "text": text}


DB = {"path": "Learnings/Experiments", "url": "https://www.notion.so/experiments-db"}


def corpus(rows, scanned=None, total=None, database=DB):
    out = {"rows": rows}
    if database is not None:
        out["database"] = database
    if scanned is not None:
        out["scanned"] = scanned
    if total is not None:
        out["total"] = total
    return out


class TestRealCorpus(unittest.TestCase):
    """The regression test for the detector itself (acceptance criterion 4)."""

    @classmethod
    def setUpClass(cls):
        with open(FIXTURE, encoding="utf-8") as fh:
            cls.corpus = json.load(fh)
        cls.findings = wn.findings(cls.corpus)

    def test_the_thirteen_row_cluster_is_found(self):
        big = [f for f in self.findings if len(f["rows"]) >= 3]
        self.assertTrue(big, "the detector found no multi-row cluster in the real corpus")
        cluster = max(big, key=lambda f: len(f["rows"]))
        self.assertEqual(13, len(cluster["rows"]))
        self.assertEqual("High", cluster["severity"])

    def test_the_cluster_holds_the_rows_the_card_names(self):
        cluster = max(self.findings, key=lambda f: len(f["rows"]))
        titles = {r["title"] for r in cluster["rows"]}
        # The row whose `## Result` section the card quotes, plus two others that
        # carry the identical sentence.
        for expected in ("paywallTestimonial", "paywallCTAPosition", "encourageRating"):
            self.assertIn(expected, titles)

    def test_the_cluster_is_the_note_the_card_quotes(self):
        # Not just "a 13-row cluster" — the cluster has to be *that* note, not
        # some other marker-bearing line those rows happen to share.
        cluster = max(self.findings, key=lambda f: len(f["rows"]))
        self.assertEqual(wn.subcategory(wn.fingerprint(THE_NOTE)), cluster["subcategory"])
        self.assertTrue(cluster["note"].startswith("_Note: `SUL` and `Paid` land"))

    def test_cluster_members_are_distinct_rows(self):
        for f in self.findings:
            urls = [r["url"] for r in f["rows"]]
            self.assertEqual(len(urls), len(set(urls)), f["subcategory"])

    def test_clean_rows_do_not_produce_findings(self):
        # 20 rows in the fixture carry no marker at all; none may appear.
        clustered = {r["url"] for f in self.findings for r in f["rows"]}
        for r in self.corpus["rows"]:
            if not wn.workaround_sentences(r):
                self.assertNotIn(r["url"], clustered, r["title"])

    def test_the_real_corpus_links_cards_only_in_hrefs(self):
        # The measured fact the acquittal path is built on, asserted against the
        # rows themselves so a future fixture refresh cannot quietly invalidate it.
        in_href = sum(1 for r in self.corpus["rows"] for b in r["blocks"]
                      for h in b["hrefs"] if "linear.app" in h)
        in_text = sum(1 for r in self.corpus["rows"] for b in r["blocks"]
                      if "linear.app" in b["text"])
        self.assertGreater(in_href, 0)
        self.assertEqual(0, in_text, "a text-only acquittal would still never fire")

    def test_the_real_corpus_is_not_acquittal_blind(self):
        self.assertFalse(wn.acquittal_blind(self.corpus))

    def test_the_real_scan_is_partial_and_says_so(self):
        cov = wn.coverage(self.corpus)
        self.assertFalse(cov["complete"])
        self.assertIn("PARTIAL", cov["note"])


class TestRepetitionIsTheSignal(unittest.TestCase):
    def test_one_row_is_never_a_finding(self):
        note = "Per-variant paid rate isn't recoverable from the raw cut."
        self.assertEqual([], wn.findings(corpus([row("a", note)])))

    def test_a_row_repeating_itself_is_not_a_cluster(self):
        note = "Per-variant paid rate isn't recoverable from the raw cut."
        body = f"## Result\n{note}\n\n## Learning\n{note}"
        self.assertEqual([], wn.findings(corpus([row("a", body)])))

    def test_two_rows_are_a_medium_finding(self):
        note = "Per-variant paid rate isn't recoverable from the raw cut."
        out = wn.findings(corpus([row("a", note), row("b", note)]))
        self.assertEqual(1, len(out))
        self.assertEqual("Medium", out[0]["severity"])
        self.assertEqual(2, len(out[0]["rows"]))

    def test_three_rows_escalate_to_high(self):
        note = "Per-variant paid rate isn't recoverable from the raw cut."
        out = wn.findings(corpus([row(n, note) for n in "abc"]))
        self.assertEqual("High", out[0]["severity"])

    def test_a_loose_marker_costs_nothing_on_its_own(self):
        # Three rows, each with a marker, no two saying the same thing.
        rows = [
            row("a", "The raw cut is trustworthy here — no over-stamping."),
            row("b", "No readout for the control arm; the experiment never rendered."),
            row("c", "A known issue with the trial flag inflated day-1 retention."),
        ]
        self.assertEqual([], wn.findings(corpus(rows)))


class TestNoteIdentity(unittest.TestCase):
    def test_formatting_differences_are_not_a_different_note(self):
        a = "`SUL` and `Paid` land in the `undefined` bucket — not recoverable."
        b = "SUL and Paid land in the undefined bucket - not recoverable."
        self.assertEqual(wn.fingerprint(a), wn.fingerprint(b))
        self.assertEqual(1, len(wn.findings(corpus([row("a", a), row("b", b)]))))

    def test_copy_paste_drift_still_clusters(self):
        base = ("The Experiment: super-property is stamped at sign-up, before variant "
                "bucketing, so per-variant paid rate for {} isn't recoverable from the raw cut.")
        rows = [row("a", base.format("paywallTestimonial")),
                row("b", base.format("paywallCTAPosition"))]
        out = wn.findings(corpus(rows))
        self.assertEqual(1, len(out), "one changed token should not split the cluster")
        self.assertEqual(2, len(out[0]["rows"]))

    def test_two_unrelated_notes_do_not_merge(self):
        a = "Per-variant paid rate isn't recoverable from the raw cut."
        b = "Cartesia minutes are a known issue on the LiveKit path and read zero."
        out = wn.findings(corpus([row("a", a), row("b", a), row("c", b), row("d", b)]))
        self.assertEqual(2, len(out))
        self.assertNotEqual(out[0]["subcategory"], out[1]["subcategory"])


class TestFindingKey(unittest.TestCase):
    """Step 5 de-dupes and Step 3 reconciles on `(page_url, category, subcategory)`.

    Anything in that key that moves when the cluster gains a member loses the
    prior run's decision — silently, and silence is what this detector exists to
    end. So every case below is the same question asked a different way: does
    the key depend on the defect, or on which rows this scan happened to read?
    """

    NOTE = "Per-variant paid rate isn't recoverable from the raw cut."

    def key(self, finding):
        return (finding["page_url"], finding["category"], finding["subcategory"])

    def test_the_key_survives_the_cluster_growing(self):
        two = wn.findings(corpus([row("a", self.NOTE), row("b", self.NOTE)]))[0]
        five = wn.findings(corpus([row(n, self.NOTE) for n in "abcde"]))[0]
        # Same defect, same key — the only reason a cluster rejected at two rows
        # comes back once it escalates to High.
        self.assertEqual(self.key(two), self.key(five))
        self.assertEqual("Medium", two["severity"])
        self.assertEqual("High", five["severity"])

    def test_a_new_row_that_sorts_first_does_not_move_the_key(self):
        # The case a row anchor gets wrong: the 14th row to carry the note may
        # sort before every existing member.
        before = wn.findings(corpus([row("m", self.NOTE), row("z", self.NOTE)]))[0]
        after = wn.findings(corpus([row(n, self.NOTE) for n in ("m", "z", "aaa")]))[0]
        self.assertEqual(self.key(before), self.key(after))

    def test_the_anchor_is_the_database_not_a_row(self):
        f = wn.findings(corpus([row("a", self.NOTE), row("b", self.NOTE)]))[0]
        self.assertEqual(DB["url"], f["page_url"])
        self.assertEqual(DB["path"], f["page_title"])
        self.assertTrue(f["key_stable"])
        # Every member row is still reachable from the finding.
        self.assertEqual({r["url"] for r in f["rows"]}, set(f["related_pages"]))

    def test_an_undeclared_database_falls_back_and_says_the_key_is_unstable(self):
        f = wn.findings(corpus([row("a", self.NOTE), row("b", self.NOTE)], database=None))[0]
        self.assertFalse(f["key_stable"])
        self.assertIn("/a", f["page_url"])
        self.assertIn("key not stable", wn.render({}, [f], wn.coverage({})))

    def test_the_subcategory_follows_the_dominant_spelling_not_the_first_seen(self):
        # One drifted member, arriving first. The key must be the note 3 rows
        # carry, not the one row 1 happens to spell differently.
        base = ("The Experiment: super-property is stamped at sign-up, before variant "
                "bucketing, so per-variant paid rate for {} isn't recoverable from the raw cut.")
        drifted = row("aaa", base.format("someOtherExperiment"))
        majority = [row(n, base.format("paywallTestimonial")) for n in ("m", "n", "o")]
        with_drift = wn.findings(corpus([drifted] + majority))[0]
        without = wn.findings(corpus(majority))[0]
        self.assertEqual(4, len(with_drift["rows"]))
        self.assertEqual(without["subcategory"], with_drift["subcategory"])

    def test_the_key_does_not_depend_on_row_order(self):
        rows = [row(n, self.NOTE) for n in ("zeta", "alpha", "mid")]
        a = wn.findings(corpus(rows))[0]
        b = wn.findings(corpus(list(reversed(rows))))[0]
        self.assertEqual(self.key(a), self.key(b))

    def test_two_clusters_in_one_database_keep_separate_keys(self):
        # With the database as the anchor, `subcategory` is the *whole* of what
        # separates two findings — so it carrying the note is load-bearing.
        a = "Per-variant paid rate isn't recoverable from the raw cut."
        b = "Session duration is a known issue on the web surface."
        rows = [row("anchor", a + "\n" + b), row("x", a), row("y", b)]
        out = wn.findings(corpus(rows))
        self.assertEqual(2, len(out))
        self.assertEqual(1, len({f["page_url"] for f in out}))
        self.assertEqual(2, len({self.key(f) for f in out}))


class TestCardLinkAcquits(unittest.TestCase):
    """A note that links its card is not a finding.

    Without this the detector cannot be closed. Its own suggested fix leaves the
    notes in place, so the cluster still fires after the card is filed — and
    Step 5 reads a still-firing `Approved + Fixed` finding as a **regression**,
    re-raising it at full severity on every later run, forever. A High finding
    nobody can close is a High finding nobody reads.
    """

    NOTE = "Per-variant paid rate isn't recoverable from the raw cut."
    LINKED = NOTE + " Tracked in https://linear.app/aura-health/issue/PROD-6175."

    def test_a_linked_note_does_not_form_a_cluster(self):
        self.assertEqual([], wn.findings(corpus([row("a", self.LINKED), row("b", self.LINKED)])))

    def test_the_finding_names_only_the_rows_still_missing_a_link(self):
        rows = [row("a", self.NOTE), row("b", self.NOTE), row("c", self.LINKED)]
        out = wn.findings(corpus(rows))
        self.assertEqual(1, len(out))
        self.assertEqual({"a", "b"}, {r["title"] for r in out[0]["rows"]})

    def test_acquitted_rows_are_counted_not_silently_dropped(self):
        rows = [row(n, self.NOTE) for n in "ab"] + [row(n, self.LINKED) for n in "cde"]
        out = wn.findings(corpus(rows))[0]
        self.assertEqual(3, out["linked_rows"])
        self.assertIn("3 further row(s) already link a card",
                      wn.render({}, [out], wn.coverage({})))

    def test_linking_the_last_unacquitted_rows_closes_the_finding(self):
        # The reviewer's actual path out: file the card, link it from each row.
        before = wn.findings(corpus([row(n, self.NOTE) for n in "abc"]))
        self.assertEqual("High", before[0]["severity"])
        after = wn.findings(corpus([row(n, self.LINKED) for n in "abc"]))
        self.assertEqual([], after)

    def test_one_unlinked_row_left_is_below_the_cluster_bar(self):
        rows = [row("a", self.NOTE)] + [row(n, self.LINKED) for n in "bc"]
        self.assertEqual([], wn.findings(corpus(rows)))

    def test_a_bare_ticket_id_does_not_acquit(self):
        # `PROD-6175` unlinked is not a link a reader can follow, and no pattern
        # loose enough to catch every team prefix can tell it from `GPT-4`.
        bare = self.NOTE + " Tracked in PROD-6175."
        self.assertEqual(1, len(wn.findings(corpus([row("a", bare), row("b", bare)]))))

    def test_a_notion_hyperlink_acquits_even_though_its_text_is_a_bare_id(self):
        # The way every Linear reference in this teamspace is actually written:
        # visible text `PROD-6175`, URL only in the href. Measured on the real
        # corpus: 12 card links, all in hrefs, none in visible text. An
        # acquittal that reads only visible text can never fire in production.
        blocks = [{"text": self.NOTE + " Tracked in PROD-6175.",
                   "hrefs": ["https://linear.app/aura-health/issue/PROD-6175"]}]
        rows = [block_row("a", blocks), block_row("b", blocks)]
        self.assertEqual([], wn.findings(corpus(rows)))

    def test_an_href_on_a_different_block_does_not_acquit(self):
        rows = []
        for n in "ab":
            rows.append(block_row(n, [
                {"text": self.NOTE, "hrefs": []},
                {"text": "See PROD-1.", "hrefs": ["https://linear.app/aura-health/issue/PROD-1"]},
            ]))
        out = wn.findings(corpus(rows))
        self.assertEqual(1, len(out))
        self.assertEqual(0, out[0]["linked_rows"])

    def test_a_non_linear_href_does_not_acquit(self):
        blocks = [{"text": self.NOTE, "hrefs": ["https://mixpanel.com/project/963821"]}]
        rows = [block_row("a", blocks), block_row("b", blocks)]
        self.assertEqual(1, len(wn.findings(corpus(rows))))

    def test_a_card_linked_elsewhere_in_the_row_does_not_acquit_the_note(self):
        # Acquittal is judged on the note's own block: a link three sections
        # away does not help the reader of the note.
        elsewhere = self.NOTE + "\n\nSee https://linear.app/aura-health/issue/PROD-1."
        out = wn.findings(corpus([row("a", elsewhere), row("b", elsewhere)]))
        self.assertEqual(1, len(out))
        self.assertEqual(0, out[0]["linked_rows"])


class TestAcquittalBlind(unittest.TestCase):
    """A close path that structurally cannot fire must say so.

    A flat-text corpus can never see a Notion hyperlink, so every row reads as
    unlinked — indistinguishable from a corpus where nothing is filed. Same
    shape as the PARTIAL rule: the honest failure is loud, the dangerous one
    looks like a clean run.
    """

    NOTE = "Per-variant paid rate isn't recoverable from the raw cut."

    def test_a_flat_text_corpus_is_blind(self):
        c = corpus([text_row("a", self.NOTE), text_row("b", self.NOTE)], scanned=2, total=2)
        self.assertTrue(wn.acquittal_blind(c))
        self.assertTrue(wn.findings(c)[0]["acquittal_blind"])
        self.assertIn("BLIND", wn.render(c, wn.findings(c), wn.coverage(c)))

    def test_blindness_is_reported_even_when_coverage_is_undeclared(self):
        # The combination an old flat-`text` caller actually sends. Letting the
        # coverage branch own this note silenced the warning in exactly the case
        # it exists for.
        c = corpus([text_row("a", self.NOTE), text_row("b", self.NOTE)])
        cov = wn.coverage(c)
        self.assertTrue(cov["acquittal_blind"])
        self.assertIn("BLIND", cov["acquittal_note"])
        self.assertIn("BLIND", wn.render(c, wn.findings(c), cov))
        # …and the coverage half is still reported on its own terms.
        self.assertFalse(cov["complete"])
        self.assertIn("not declared", cov["note"])

    def test_a_block_corpus_with_undeclared_coverage_is_not_blind(self):
        cov = wn.coverage(corpus([row("a", self.NOTE), row("b", self.NOTE)]))
        self.assertFalse(cov["acquittal_blind"])
        self.assertIn("reads block hrefs", cov["acquittal_note"])

    def test_a_block_corpus_is_not_blind_even_when_nothing_is_linked(self):
        # Judged on the corpus shape, not on whether an href happened to appear:
        # a caller passing blocks is href-aware regardless of what the rows hold.
        c = corpus([row("a", self.NOTE), row("b", self.NOTE)], scanned=2, total=2)
        self.assertFalse(wn.acquittal_blind(c))
        self.assertFalse(wn.findings(c)[0]["acquittal_blind"])
        self.assertIn("reads block hrefs", wn.render(c, wn.findings(c), wn.coverage(c)))


class TestCoverage(unittest.TestCase):
    def test_a_full_scan_is_complete(self):
        cov = wn.coverage(corpus([], scanned=10, total=10))
        self.assertTrue(cov["complete"])

    def test_a_partial_scan_is_not_complete(self):
        cov = wn.coverage(corpus([], scanned=9, total=10))
        self.assertFalse(cov["complete"])
        self.assertIn("9 of 10", cov["note"])

    def test_an_undeclared_scan_is_treated_as_partial(self):
        # The failure this guards is a caller that forgot to say how much it
        # read: an empty finding list then reads as a clean database.
        cov = wn.coverage(corpus([]))
        self.assertFalse(cov["complete"])

    def test_an_empty_result_on_a_partial_scan_is_not_reported_as_clean(self):
        c = corpus([row("a", "nothing to see")], scanned=9, total=10)
        text = wn.render(c, wn.findings(c), wn.coverage(c))
        self.assertIn("PARTIAL", text)
        self.assertIn("in the scanned rows", text)

    def test_an_empty_result_on_a_full_scan_is_reported_as_clean(self):
        c = corpus([row("a", "nothing to see")], scanned=10, total=10)
        text = wn.render(c, wn.findings(c), wn.coverage(c))
        self.assertIn("No repeated workaround notes.", text)


if __name__ == "__main__":
    unittest.main()
