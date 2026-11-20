"""`record-ideas` never sends a property the target database does not have.

Every loop database is meant to carry the same schema, and they drift: `Scope` and
`Expected impact` landed on the Aurie database first. Notion refuses a page create that
names an unknown property, and — unlike a screenshot upload — that is not enrichment:
the whole idea fails to land. So unknown properties are dropped with a warning, both on
create and on update, and a schema that cannot be read prunes nothing so the write
behaves exactly as it did before the guard existed.
"""

from __future__ import annotations

import io
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "plugins", "product", "skills", "record-ideas", "scripts"))

import record_ideas as r  # noqa: E402


class _Notion:
    def __init__(self, props=None, fail=False, ids=None):
        self.props, self.fail, self.ids = props, fail, ids or {}

    def _request(self, method, path, data=None):
        if self.fail:
            raise RuntimeError("boom")
        # Notion returns each property with its ID; the title property's ID is the
        # literal `title`, which is how a create payload keys it.
        return {"properties": {k: {"id": self.ids.get(k, k.lower().replace(" ", "_"))}
                               for k in self.props}}


class PruneToSchema(unittest.TestCase):
    def setUp(self):
        r._SCHEMA_CACHE.clear()
        self.real = r.notion

    def tearDown(self):
        r.notion = self.real
        r._SCHEMA_CACHE.clear()

    def test_unknown_properties_are_dropped_and_named(self):
        r.notion = _Notion({"Title", "Status", "Scope"})
        err, real_err = io.StringIO(), sys.stderr
        sys.stderr = err
        try:
            out = r.prune_to_schema("ds", {"Title": 1, "Scope": 2, "Expected impact": 3}, "x")
        finally:
            sys.stderr = real_err
        self.assertEqual(set(out), {"Title", "Scope"})
        self.assertIn("Expected impact", err.getvalue())

    def test_the_title_keyed_by_id_survives(self):
        """The create payload keys the title as `title` (its ID); the schema names it
        `Title`. The first version of this guard compared names only and stripped the
        title off every create — BugBot caught it before it shipped."""
        r.notion = _Notion({"Title", "Status"}, ids={"Title": "title"})
        out = r.prune_to_schema("ds", {"title": [{"text": {"content": "x"}}], "Status": 1})
        self.assertIn("title", out)
        self.assertIn("Status", out)

    def test_a_database_with_every_property_is_untouched(self):
        r.notion = _Notion({"Title", "Scope", "Expected impact"})
        props = {"Title": 1, "Scope": 2, "Expected impact": 3}
        self.assertEqual(r.prune_to_schema("ds", props), props)

    def test_an_unreadable_schema_prunes_nothing(self):
        # The guard must never be the reason a write fails.
        r.notion = _Notion(fail=True)
        err, real_err = io.StringIO(), sys.stderr
        sys.stderr = err
        try:
            props = {"Title": 1, "Scope": 2}
            self.assertEqual(r.prune_to_schema("ds", props), props)
        finally:
            sys.stderr = real_err
        self.assertIn("could not read the schema", err.getvalue())

    def test_the_schema_is_fetched_once_per_database(self):
        calls = []
        class Counting(_Notion):
            def _request(self, *a, **k):
                calls.append(a); return super()._request(*a, **k)
        r.notion = Counting({"Title"})
        r.prune_to_schema("ds", {"Title": 1}); r.prune_to_schema("ds", {"Title": 1})
        self.assertEqual(len(calls), 1)

class PriorArtIsRecorded(unittest.TestCase):
    """`brainstorm-ideas` requires `priorArt` whenever anything it read touches the same
    lever or job — it is the evidence that an idea is not a settled question asked again.
    It was being emitted and dropped at ingest, which is the one place it mattered."""

    def test_the_field_reaches_the_row(self):
        idea = r._coerce({"title": "t", "description": "d", "screen": "home_screen",
                          "priorArt": "aiTestimonials lost on paid cancellation; this is "
                                      "a share control, not social proof."})
        self.assertIn("aiTestimonials", idea.prior_art)
        self.assertIn("aiTestimonials", r._build_props(idea)["Prior art"]
                      ["rich_text"][0]["text"]["content"])

    def test_absent_prior_art_writes_nothing(self):
        idea = r._coerce({"title": "t", "description": "d", "screen": "home_screen"})
        self.assertEqual(idea.prior_art, "")
        self.assertNotIn("Prior art", r._build_props(idea))

if __name__ == "__main__":
    unittest.main()
