"""The Security Incident Log's group window is one rule in two writers (ENG-1760).

A Group key (`system|attack-type|outcome`) keeps one open row until GROUP-WINDOW days pass with no hits.
eng:flag-suspicious-traffic (`incident_log.py` GROUP_OPEN_DAYS) and eng:monitor-rtdb-probes
(`plan-incident-log.js` GROUP_CLOSE_MS) each implement it, under different names in different files, so
a `diff -q` can't see them drift (CLAUDE.md, "The drift guards compare copies, never implementations").
Jerome set it to 2 days on 2026-11-03 (it was 14). This pins both to the same value.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DETECT = ROOT / "plugins/eng/skills/flag-suspicious-traffic/scripts/incident_log.py"
PLANNER = ROOT / "plugins/eng/skills/monitor-rtdb-probes/scripts/plan-incident-log.js"
DAYS = 2


class GroupWindow(unittest.TestCase):
    def test_flag_suspicious_traffic(self):
        m = re.search(r"^GROUP_OPEN_DAYS = (\d+)$", DETECT.read_text(encoding="utf-8"), re.M)
        self.assertEqual(int(m[1]), DAYS)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_monitor_rtdb_probes(self):
        out = subprocess.run(["node", "-e", f"console.log(require({str(PLANNER)!r}).GROUP_CLOSE_MS)"],
                             capture_output=True, text=True, encoding="utf-8", check=True).stdout
        self.assertEqual(int(out), DAYS * 24 * 60 * 60 * 1000)


if __name__ == "__main__":
    unittest.main()
