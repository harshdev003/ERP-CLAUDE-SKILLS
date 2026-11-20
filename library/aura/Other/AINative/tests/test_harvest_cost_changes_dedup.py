#!/usr/bin/env python3
"""`eng:harvest-cost-changes` — a shared Refs token alone must not drop a distinct row.

`apply_updates.py` re-checks every approved new row against the live Cost Changes ledger.
It used to skip any row sharing one identifier (PROD-7290, IS#409) with any existing row.
Related changes cite the same parent ticket all the time, so approved rows were dropped
and reported as "skipped", which reads like the guard working (ENG-1626):

* the voice_minutes double-count fix, sharing PROD-7584 with the LiveKit-saving row;
* the OpenAI credit request and the OpenAI spend-limit row, both citing PROD-7290.

The fixture is the real ledger (163 rows, 2026-10-23; Refs reduced to the identifier
tokens the dedup reads). Every row in it is a distinct change, so no pair of them may
read as a duplicate: that is the corpus check on the threshold, not a hand-picked sample.

Stdlib unittest, no network, no keys.
"""
from __future__ import annotations

import importlib.util
import itertools
import json
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/harvest-cost-changes/scripts"
FIXTURE = REPO / "tests/fixtures/cost_changes_ledger_2026-10-23.json"


def _load():
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("apply_updates", SCRIPTS / "apply_updates.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(SCRIPTS))


au = _load()
ROWS = json.loads(FIXTURE.read_text(encoding="utf-8"))
LEDGER = [(r["change"], au.refs_tokens(r["refs"])) for r in ROWS]
CACHE_COLLAPSE = next(t for t, _ in LEDGER if t.startswith("Live-conversation prompt-cache collapses"))


class TestRealLedger(unittest.TestCase):
    def test_fixture_has_rows_sharing_refs(self):
        pairs = [1 for (_, a), (_, b) in itertools.combinations(LEDGER, 2) if a & b]
        self.assertGreaterEqual(len(pairs), 30, "fixture lost the shared-ref pairs it exists to test")

    def test_no_distinct_ledger_rows_read_as_duplicates(self):
        for (ta, ra), (tb, rb) in itertools.combinations(LEDGER, 2):
            if not ra & rb:
                continue
            verdict = au.dedup_verdict(ta, " ".join(ra), [(tb, rb)])
            self.assertEqual(verdict[0], "shared", f"{ta!r} vs {tb!r}: {verdict}")


class TestIncidents(unittest.TestCase):
    def test_credit_request_is_added_not_skipped(self):
        v = au.dedup_verdict(
            "Ask OpenAI for an $8k credit for the chat-latest prompt-cache regression (Oct 7 – Oct 20)",
            "PROD-7725 · PROD-7290 · OpenAI case 16776940", LEDGER)
        self.assertEqual(v[0], "shared")
        self.assertIn(CACHE_COLLAPSE, [t for t, _, _ in v[1]])

    def test_spend_limit_is_added_not_skipped(self):
        v = au.dedup_verdict(
            "OpenAI organization spend limit raised to $30k for October (not enforced); "
            "bring it back down after the Nov 1 reset", "PROD-7752 · PROD-7290", LEDGER)
        self.assertEqual(v[0], "shared")


class TestStillDedups(unittest.TestCase):
    def test_rerun_of_an_applied_row_is_skipped_on_title(self):
        title, toks = LEDGER[0]
        self.assertEqual(au.dedup_verdict(title, " ".join(toks), LEDGER)[0], "title")

    def test_title_match_ignores_case_and_punctuation(self):
        v = au.dedup_verdict(CACHE_COLLAPSE.upper() + "!!", "", LEDGER)
        self.assertEqual(v[0], "title")

    def test_reworded_title_with_shared_ref_is_skipped(self):
        ledger = [("Reduce context instructions", {"ENGBACK-1136"})]
        v = au.dedup_verdict("Trimmed context instructions", "ENGBACK-1136", ledger)
        self.assertEqual(v[0], "refs")

    def test_an_old_is_ref_and_the_new_spelling_are_one_ref(self):
        ledger = [("Reduce context instructions", au.refs_tokens("IS#409"))]
        v = au.dedup_verdict("Trimmed context instructions", "IntelligenceService#409", ledger)
        self.assertEqual(v[0], "refs")
        self.assertEqual(au.refs_tokens("IS#409"), au.refs_tokens("intelligenceservice#409"))

    def test_another_repos_409_is_not_the_same_ref(self):
        ledger = [("Reduce context instructions", au.refs_tokens("IS#409"))]
        v = au.dedup_verdict("Trimmed context instructions", "AINative#409", ledger)
        self.assertEqual(v[0], "new")

    def test_similar_title_without_shared_ref_is_new(self):
        ledger = [("Reduce context instructions", {"ENGBACK-1136"})]
        v = au.dedup_verdict("Trimmed context instructions", "ENGBACK-9999", ledger)
        self.assertEqual(v[0], "new")

    def test_no_refs_and_new_title_is_new(self):
        self.assertEqual(au.dedup_verdict("Something new entirely", "#ai-cost 2026-10-23", LEDGER)[0], "new")


class TestSimilarity(unittest.TestCase):
    def test_threshold_sits_above_every_distinct_pair(self):
        top = max(au.title_similarity(ta, tb)
                  for (ta, ra), (tb, rb) in itertools.combinations(LEDGER, 2) if ra & rb)
        self.assertLess(top, au.TITLE_DUP_SIMILARITY)

    def test_stopwords_do_not_count(self):
        self.assertEqual(au.title_similarity("the cost of the voice", "cost voice"), 1.0)


if __name__ == "__main__":
    unittest.main()
