"""The Conversation QA rubric mirror must be complete, and say so honestly.

This file exists because of a specific, expensive failure. `rubric.v3.yaml`
described itself as the operational mirror of the Notion rubric, declared
`notion_sync.status: synced`, and carried **21 of 52 scored rows** — five of the
missing ones gates. A 700-conversation calibration run was planned and executed
against that fragment before anyone noticed.

Nothing about a fragment fails loudly. It parses, it runs, and it prints a clean
scorecard over whatever rows it happens to contain. The row count is therefore an
invariant, not a statistic.
"""
from __future__ import annotations

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
MIRROR = REPO / "plugins/eng/skills/qa-conversation/references/rubric.v3.yaml"

# The blocks and their sizes, from the Notion source (rev. 3.1).
def _rows(text):
    """Row id -> {key: raw value}, read as text.

    Deliberately not PyYAML: no shipped script imports it, so it is not a repo
    dependency, and a test that needs one it cannot assume either fails in CI or
    gets skipped there — and a skipped completeness check is the same as no check.
    The mirror's rows are flat key/value blocks, which a scanner handles exactly.
    """
    rows, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^  - id: (\S+)\s*$", line)
        if m:
            cur = {}
            rows[m.group(1)] = cur
            continue
        if cur is None:
            continue
        m = re.match(r"^    ([A-Za-z_][\w]*): ?(.*)$", line)
        if m:
            cur[m.group(1)] = m.group(2).strip()
        elif line and not line.startswith("      "):
            cur = None
    return rows


def _section(text, name):
    """A top-level mapping's immediate children, as raw strings."""
    out, inside = {}, False
    for line in text.splitlines():
        if line.startswith(name + ":"):
            inside = True
            continue
        if inside:
            m = re.match(r"^  ([A-Za-z_][\w]*): ?(.*)$", line)
            if m:
                out[m.group(1)] = m.group(2).strip().strip('"')
            elif line.strip() and not line.startswith(" "):
                break
    return out


def _truthy(v):
    return str(v).strip().lower() in ("true", "yes")


EXPECTED_IDS = (
    [f"A{i}" for i in range(1, 13)]
    + [f"F{i}" for i in range(1, 11)]
    + ["H1", "H2"]
    + [f"N{i}" for i in range(1, 4)]
    + [f"R{i}" for i in range(1, 11)]
    + [f"S{i}" for i in range(1, 10)]
    + ["V1a", "V1b", "V1c", "V2", "V3", "V4"]
)


class Mirror(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = MIRROR.read_text()
        cls.rows = _rows(cls.text)
        cls.sync = _section(cls.text, "notion_sync")
        cls.inventory = _section(cls.text, "row_inventory")

    def test_every_scored_row_is_present(self):
        missing = [r for r in EXPECTED_IDS if r not in self.rows]
        self.assertEqual(missing, [], f"mirror is a fragment, missing {len(missing)} rows")

    def test_row_count_matches_the_declared_inventory(self):
        self.assertEqual(len(self.rows), int(self.inventory["scored_rows_expected"]))

    def test_no_duplicate_ids(self):
        ids = re.findall(r"^  - id: (\S+)\s*$", self.text, re.M)
        self.assertEqual(len(ids), len(set(ids)))

    def test_the_sync_claim_is_not_overstated(self):
        """`synced` requires BOTH a complete row set and nothing left
        machine-parsed. Checking only the second is how the old file passed: it
        claimed `synced` while carrying 21 rows and zero parsed ones."""
        status = self.sync["status"]
        if status != "synced":
            return
        unreviewed = [i for i, r in self.rows.items()
                      if r.get("review_status") == "machine_parsed"]
        self.assertEqual(unreviewed, [],
                         f"{len(unreviewed)} rows are machine_parsed; status may not be 'synced'")
        missing = [r for r in EXPECTED_IDS if r not in self.rows]
        self.assertEqual(missing, [],
                         f"{len(missing)} rows missing; status may not be 'synced'")


class Gates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = _rows(MIRROR.read_text())

    def test_the_rows_the_source_calls_gates_are_gates(self):
        for rid in ("V2", "V3", "R10", "H2", "F1", "F2", "F7", "F8", "F9", "F10", "S8"):
            self.assertTrue(_truthy(self.rows[rid].get("gate")), f"{rid} should be a gate")

    def test_every_safety_row_is_a_gate_except_the_one_the_source_excludes(self):
        """'Every row here is a hard gate' — except S9, which the source calls a
        standing zero-tolerance finding and explicitly not a release gate yet."""
        for i in range(1, 9):
            self.assertTrue(_truthy(self.rows[f"S{i}"].get("gate")), f"S{i} should be a gate")
        self.assertFalse(_truthy(self.rows["S9"].get("gate")))

    def test_rows_that_mention_gating_but_refuse_it_are_not_gates(self):
        """F5 says 'Report only, no gate'; F4 says 'Never gate this from one
        drive'. A substring match on GATE promotes both, and a wrongly-gated row
        blocks releases for a measurement its own source distrusts."""
        self.assertFalse(_truthy(self.rows["F5"].get("gate")))
        self.assertFalse(_truthy(self.rows["F4"].get("gate")))

    def test_an_unreviewed_row_may_not_silently_gate(self):
        """A machine-parsed gate is reportable, never blocking — the mirror's own
        rule. This is the guard that makes shipping the parse safe."""
        rule = _section(MIRROR.read_text(), "notion_sync")["rule"]
        self.assertIn("must not gate a release until reviewed", rule)


class Derivations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = _rows(MIRROR.read_text())

    def test_reverse_scored_rows_are_flagged(self):
        """R9 runs 1-best where every other R row is 5-best. An aggregator that
        assumes higher-is-better inverts it silently."""
        self.assertTrue(_truthy(self.rows["R9"].get("reverse_scored")))
        for rid in ("R1", "R2", "R4", "R8"):
            self.assertFalse(_truthy(self.rows[rid].get("reverse_scored")), rid)

    def test_unstated_scope_is_recorded_as_unstated(self):
        """Five A rows state no mode-scope. Defaulting them to `both` is how a
        guess ends up looking like a reviewed value — A7 is the friendship row."""
        self.assertEqual(self.rows["A7"].get("mode_scope"), "unstated")
        self.assertEqual(self.rows["A4"].get("mode_scope"), "both")

    def test_a_scope_token_does_not_leak_into_the_measure_text(self):
        for rid in ("A1", "A4", "A11"):
            self.assertNotRegex(self.rows[rid]["measures"],
                                r"\b(Both|Coaching|Friendship)\s*$", rid)


if __name__ == "__main__":
    unittest.main()
