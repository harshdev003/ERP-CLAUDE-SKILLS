"""eng:update-node-packages land_pr.sh never merges without --merge (ENG-1752).

It used to merge on its own once CI was green and Bugbot clean, but merges need Jerome's "merge". It now
stops at READY, and merges only when called with --merge after his "merge" for that PR. The poll itself
needs CircleCI and GitHub, so these checks read the script: the one `gh pr merge` sits behind the
--merge flag, and only the literal `--merge` sets it.
"""
from __future__ import annotations

import pathlib
import re
import unittest

LAND_PR = pathlib.Path(__file__).resolve().parents[1] / "plugins/eng/skills/update-node-packages/scripts/land_pr.sh"
SKILL = LAND_PR.parents[1] / "SKILL.md"


def code(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


class LandPrStopsAtReady(unittest.TestCase):
    def setUp(self):
        self.lines = code(LAND_PR.read_text(encoding="utf-8"))

    def test_the_only_merge_is_behind_the_flag(self):
        merges = [i for i, ln in enumerate(self.lines) if re.search(r"\bgh pr merge\b", ln)]
        self.assertEqual(len(merges), 1, merges)
        guard = [i for i, ln in enumerate(self.lines) if re.search(r'if \[ -z "\$MERGE" \]; then', ln)]
        self.assertEqual(len(guard), 1)
        between = self.lines[guard[0]:merges[0]]
        self.assertTrue(any("VERDICT: READY" in ln for ln in between))
        self.assertTrue(any(ln.strip() == "exit 0" for ln in between))

    def test_only_the_literal_flag_sets_merge(self):
        sets = [ln.strip() for ln in self.lines if re.search(r"\bMERGE=1\b", ln)]
        self.assertEqual(sets, ['for a in "${@:4}"; do [ "$a" = "--merge" ] && MERGE=1; done'])
        self.assertTrue(any(re.search(r'MERGE=""', ln) for ln in self.lines))

    def test_the_skill_runs_it_without_merge_first(self):
        runs = [ln for ln in SKILL.read_text(encoding="utf-8").splitlines() if "land_pr.sh\" <repo-name>" in ln]
        self.assertEqual(len(runs), 2, runs)
        self.assertFalse(runs[0].rstrip().endswith("--merge"))
        self.assertTrue(runs[1].rstrip().endswith("--merge"))


if __name__ == "__main__":
    unittest.main()
