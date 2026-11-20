#!/usr/bin/env python3
"""Which Mixpanel namespaces count as Aurie activity — and which must never be folded in.

**The defect.** `backfill-aurie-user-ai-created`'s first-AI-login proxy discovered its
union with `STARTS_WITH(table_name, 'ai_')`. Aurie used **inside the Aura app** emits into
`aiaura_*` — `aiaura_sign_up_start`, `aiaura_sign_up_screen_completed`,
`aiaura_conversation_message_sent` — which does not match `ai_`, because the underscore is
the discriminator. The union was therefore **disjoint** from the entire in-Aura surface
and structurally could not see those users. They were reported `no_ai_activity`, a reason
documented as "only once they act", while having signed up and held conversations.

Discovering the table list from INFORMATION_SCHEMA never protected against this: the
*predicate* decides what the discovery can see, and the predicate was the hand-curated
part. PROD-6096.

**The trap on the other side, which is worse.** Bare `chat_*` / `conversation_*` look like
Aurie and are not — they are the AURA app's own chat feature (`chat_beta_joined`,
`chat_ai_onboarding_started`, `conversation_mode_selected`). Measured against the rows
already stored, folding them in would back-date **56 of 62** resolved users who also have
`chat_*` events, by **651 days on average and up to 1,921**, silently placing real Aurie
users into 2023/2024 cohorts they were never in. That is a confident wrong answer across
every cohort, tenure, retention, payback and lifetime-cost chart — strictly worse than the
under-coverage it would "fix".

So the exclusion is asserted here **with its reason**, on the model of the non-member
`normalize.py` / `projects.py` guards in CLAUDE.md: a `grep` for "AI-looking tables" leads
to a filter with no rationale, and the obvious way to "fix" an under-covering proxy is to
widen it.

Stdlib unittest, no network.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/backfill-aurie-user-ai-created/scripts"


def _load():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("backfill_ns", SCRIPTS / "backfill.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


backfill = _load()


class TestNamespaceConstants(unittest.TestCase):
    def test_both_aurie_namespaces_are_covered(self):
        self.assertIn("ai_", backfill.AURIE_TABLE_PREFIXES)
        self.assertIn("aiaura_", backfill.AURIE_TABLE_PREFIXES)

    def test_aura_chat_is_never_an_aurie_namespace(self):
        """The load-bearing exclusion. Folding these in back-dates 56 of 62 resolved
        users by up to 1,921 days — worse than the bug it would appear to fix."""
        for p in ("chat_", "conversation_"):
            self.assertIn(p, backfill.NON_AURIE_TABLE_PREFIXES)
            self.assertNotIn(p, backfill.AURIE_TABLE_PREFIXES)

    def test_the_two_sets_are_disjoint(self):
        self.assertEqual(
            set(backfill.AURIE_TABLE_PREFIXES) & set(backfill.NON_AURIE_TABLE_PREFIXES),
            set())

    def test_ai_prefix_does_not_match_aiaura_by_accident(self):
        """Why two prefixes are needed at all: `aiaura_` does not start with `ai_`, so one
        predicate cannot cover both. If this ever became true, the second prefix would be
        redundant — and someone would remove it."""
        self.assertFalse("aiaura_sign_up_start".startswith("ai_"))


class TestDiscoveryPredicate(unittest.TestCase):
    """Verify the SQL the function actually sends, not a re-derivation of it."""

    def setUp(self):
        self._query = backfill.bq.query
        self.seen = {}

        def cap(sql, *_a, **_k):
            self.seen["sql"] = sql
            return [{"table_name": "ai_login"}, {"table_name": "aiaura_sign_up_start"}]
        backfill.bq.query = cap

    def tearDown(self):
        backfill.bq.query = self._query

    def test_predicate_asks_for_every_aurie_prefix(self):
        backfill.fallback_event_tables()
        for p in backfill.AURIE_TABLE_PREFIXES:
            self.assertIn(f"STARTS_WITH(table_name, '{p}')", self.seen["sql"])

    def test_predicate_never_asks_for_the_aura_chat_namespace(self):
        backfill.fallback_event_tables()
        for p in backfill.NON_AURIE_TABLE_PREFIXES:
            self.assertNotIn(f"STARTS_WITH(table_name, '{p}')", self.seen["sql"])

    def test_userid_and_time_filter_survives(self):
        """Load-bearing for compilation: one table missing either column breaks the
        whole UNION, so widening the prefix set must not drop this."""
        backfill.fallback_event_tables()
        self.assertIn("column_name = 'userid'", self.seen["sql"])
        self.assertIn("column_name = 'time'", self.seen["sql"])

    def test_returns_tables_from_both_namespaces_sorted(self):
        self.assertEqual(backfill.fallback_event_tables(),
                         ["ai_login", "aiaura_sign_up_start"])


if __name__ == "__main__":
    unittest.main()
