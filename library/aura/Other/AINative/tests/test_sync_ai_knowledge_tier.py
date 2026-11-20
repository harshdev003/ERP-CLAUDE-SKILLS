"""The Tier column in the AI Knowledge prompter inventory.

Failure injection is the point here. The defect this suite exists for
(AINative #840 review) needed TWO conditions at once — a prompter missing from
the table AND a table that had already gained the Tier column — so neither the
in-sync live table nor a pre-regenerate one could reach it. Hand-verification
against production passed while the branch was broken.

Stdlib unittest, no network: the Notion transport is stubbed at its chokepoint.
"""
import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = (REPO / "plugins/eng/skills/audit-intelligence-prompts/scripts"
                 "/sync_ai_knowledge.py")


def load_module():
    """Import the script by path; it is not on sys.path as a package."""
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("sync_ai_knowledge", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TierColumnContract(unittest.TestCase):
    def setUp(self):
        self.m = load_module()

    def test_tier_is_a_declared_column(self):
        self.assertIn("Tier", self.m.TABLE_COLS)
        self.assertIn("Tier", self.m.REGEN_HEADERS)

    def test_prose_columns_stay_last(self):
        """Generated columns first so Role / Trigger remain adjacent at the end."""
        self.assertEqual(self.m.REGEN_HEADERS[-2:], ["Role", "Trigger / gating"])
        self.assertLess(self.m.REGEN_HEADERS.index("Tier"),
                        self.m.REGEN_HEADERS.index("Role"))

    def test_absent_service_tier_is_standard_not_em_dash(self):
        """An absent serviceTier means the OpenAI default tier, not "unset".

        Rendering it as the em dash the tuning knobs use would read as "no tier
        decided" -- the opposite of what the config means -- and would make every
        standard-tier row look like a gap to fill in.
        """
        self.assertEqual(self.m.DEFAULT_TIER, "standard")
        self.assertNotEqual(self.m.DEFAULT_TIER, "—")

    def test_param_cols_declares_per_column_absent_rendering(self):
        """Each generated column owns its own "key absent from config" value."""
        # PARAM_COLS is local to main(); assert the shape via the source so a
        # regression to a flat {col: field} mapping is caught.
        src = SCRIPT.read_text()
        self.assertIn('"Tier": ("serviceTier", DEFAULT_TIER)', src)
        self.assertIn('"Reasoning": ("reasoningEffort", "—")', src)


class MissingRowCoversEveryResolvedColumn(unittest.TestCase):
    """The regression Bugbot caught: a missing row written into a widened table."""

    def setUp(self):
        self.m = load_module()

    def test_content_by_col_covers_all_declared_columns(self):
        """Every name in TABLE_COLS must have cell content in the apply path.

        This is the invariant whose violation raised KeyError mid-write. Asserted
        against the source because the dict is built inside main()'s apply branch,
        which needs a live Notion client to reach.
        """
        src = SCRIPT.read_text()
        start = src.index("content_by_col = {")
        end = src.index("}", start)
        block = src[start:end]
        for col in self.m.TABLE_COLS:
            self.assertIn(f'"{col}":', block,
                          f"TABLE_COLS declares {col!r} but the missing-row apply "
                          f"path defines no cell content for it — this raises "
                          f"KeyError once the table gains that column.")

    def test_uncovered_column_fails_before_any_write(self):
        """A future TABLE_COLS addition must fail up front, not mid-append."""
        src = SCRIPT.read_text()
        self.assertIn("uncovered = [c for c, i in col_idx.items()", src)
        # The guard has to precede the cell assembly it protects.
        self.assertLess(src.index("uncovered = [c for c, i in col_idx.items()"),
                        src.index("cells[idx] = content_by_col[col]"))

    def test_missing_payload_captures_service_tier(self):
        """A guarded lookup still cannot fill the cell if the payload lacks it."""
        src = SCRIPT.read_text()
        start = src.index("missing.append({")
        end = src.index("})", start)
        self.assertIn('"serviceTier"', src[start:end])


if __name__ == "__main__":
    unittest.main()
