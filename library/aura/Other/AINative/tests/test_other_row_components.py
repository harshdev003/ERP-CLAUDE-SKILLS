#!/usr/bin/env python3
"""The cost report's `Other`-row split must cover every cost column the store writes.

`chart-aurie-daily-margin` sums the `Other` reconciling row per cost column
(`OTHER_COMPONENTS`) and `build-aurie-cost-report` renders that split under its
own label table. Three files encode one set, and each can drift silently:

- A cost column added to the store (the way `voice_fish` was) and not to
  `OTHER_COMPONENTS` leaves the split summing to less than `other_cost`, with
  nothing to flag the missing slice.
- A key added to `OTHER_COMPONENTS` and not to the renderer's labels is dropped
  from the callout, so the split reads as complete while omitting a vendor.

The store's column list is read from `store.py`'s source (not imported: it pulls
in `requests` and the LiveKit log client at import time).

Origin: PROD-6991 — the callout described `Other` as a voice-only remainder while
55% of it was LLM.
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILLS = REPO / "plugins/eng/skills"
STORE = SKILLS / "store-aurie-user-daily-cost/scripts/store.py"
MARGIN = SKILLS / "chart-aurie-daily-margin/scripts/margin.py"
RENDER = SKILLS / "build-aurie-cost-report/scripts/render.py"


def _load(name, path):
    sys.path.insert(0, str(path.parent))  # margin.py imports its sibling bq.py
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(path.parent))


def _store_cost_columns():
    """The columns that sum into `total_cost`: llm_cost + every voice_* but voice_cost."""
    tree = ast.parse(STORE.read_text())
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "COLS" for t in node.targets)):
            cols = ast.literal_eval(node.value)
            return {c for c in cols
                    if c == "llm_cost" or (c.startswith("voice_") and c != "voice_cost")}
    raise AssertionError("COLS not found in store.py")


margin = _load("aurie_margin", MARGIN)
render = _load("aurie_cost_render", RENDER)


class OtherComponentCoverage(unittest.TestCase):
    def test_margin_split_covers_every_store_cost_column(self):
        split = {col for _, col in margin.OTHER_COMPONENTS}
        self.assertEqual(split, _store_cost_columns())

    def test_renderer_labels_every_margin_component(self):
        self.assertEqual({k for k, _ in render.OTHER_COMPONENT_LABELS},
                         {k for k, _ in margin.OTHER_COMPONENTS})

    def test_sql_selects_every_component(self):
        sql = margin._excluded_sql("")
        for key, col in margin.OTHER_COMPONENTS:
            self.assertIn(f"b.{col} ELSE 0 END) AS other_{key}", sql)


class OtherCallout(unittest.TestCase):
    def _margin(self, by_component):
        exc = {"dev_cost": 1.0, "unknown_cost": 0.0, "other_cost": 100.0,
               "untagged_cost": 0.0, "all_cost": 1000.0, "total_excluded": 101.0}
        if by_component is not None:
            exc["other_by_component"] = by_component
        return {"excluded": exc}

    def test_split_is_largest_first_and_skips_dust(self):
        html = render._excluded_callout(self._margin(
            {"llm": 60.0, "livekit_unused_capacity": 30.0, "cartesia": 10.0,
             "deepgram": 0.001}))
        self.assertIn("Split: LLM $60.00 · LiveKit unused capacity $30.00 · Cartesia $10.00.",
                      html)
        self.assertNotIn("Deepgram", html)

    def test_negative_unused_capacity_is_shown(self):
        # Signed: a month crossing into overage reverses earlier unused capacity.
        html = render._excluded_callout(self._margin(
            {"llm": 120.0, "livekit_unused_capacity": -20.0}))
        self.assertIn("LiveKit unused capacity −$20.00", html)

    def test_never_describes_other_as_voice_only(self):
        html = render._excluded_callout(self._margin({"llm": 100.0}))
        self.assertNotIn("voice remainder", html)
        self.assertIn("LLM", html)

    def test_older_margin_json_renders_without_split(self):
        html = render._excluded_callout(self._margin(None))
        self.assertIn("reconciling row", html)
        self.assertNotIn("Split:", html)


# Unattributable by construction, every day: not a collapse when they reach no user.
BY_DESIGN = {"voice_11labs_intelligence", "voice_livekit_platform"}


class CollapsedVoicePaths(unittest.TestCase):
    """PROD-6992: a voice path that billed and reached no user is stored, then flagged."""

    # Rows in the shape `_collapsed_paths_sql` returns (date, path, total), from the
    # 2026-06-01 -> 2026-10-04 store: two above the floor, one dust day.
    ROWS = [{"date": "2026-08-12", "path": "cartesia", "total": 45.0},
            {"date": "2026-09-24", "path": "elevenlabs_agent", "total": 33.1013},
            {"date": "2026-08-15", "path": "livekit", "total": 0.04}]

    def test_paths_are_every_per_user_voice_column(self):
        voice = {c for c in _store_cost_columns() if c.startswith("voice_")}
        self.assertEqual({col for _, col in margin.COLLAPSE_PATHS}, voice - BY_DESIGN)

    def test_renderer_labels_every_collapse_path(self):
        labels = {k for k, _ in render.OTHER_COMPONENT_LABELS}
        self.assertLessEqual({k for k, _ in margin.COLLAPSE_PATHS}, labels)

    def test_summary_lists_above_floor_and_counts_the_rest(self):
        out = margin._collapsed_summary(self.ROWS)
        self.assertEqual(out["count"], 2)
        self.assertEqual([d["date"] for d in out["days"]], ["2026-08-12", "2026-09-24"])
        self.assertEqual(out["below_floor_count"], 1)   # tagged, not dropped
        self.assertAlmostEqual(out["dollars"], 78.1013)

    def test_callout_flags_the_day_with_its_dollars(self):
        m = {"excluded": {"dev_cost": 0, "unknown_cost": 0, "other_cost": 100.0,
                          "untagged_cost": 0, "all_cost": 1000.0, "total_excluded": 100.0,
                          "collapsed_voice_paths": margin._collapsed_summary(self.ROWS)}}
        html = render._excluded_callout(m)
        self.assertIn("Voice billed to no user", html)
        self.assertIn("ElevenLabs agent 2026-09-24 $33.10", html)
        self.assertIn("Another 1 under $1.00", html)

    def test_no_line_when_nothing_collapsed_or_block_absent(self):
        self.assertIsNone(render._collapsed_line(margin._collapsed_summary([])))
        self.assertIsNone(render._collapsed_line(None))


if __name__ == "__main__":
    unittest.main()
