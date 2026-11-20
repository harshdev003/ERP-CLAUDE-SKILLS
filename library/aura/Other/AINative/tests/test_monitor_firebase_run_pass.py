#!/usr/bin/env python3
"""`eng:monitor-firebase` `run_pass.sh`: a check that doesn't run is a FAIL, never PASS OK.

2026-10-28 16:42Z: a prune removed the eng version directory a pass was running from. Every check
after the profile failed with "No such file or directory" and the pass still printed PASS OK, so ten
profiled minutes were never evaluated. The real run_pass.sh runs here against a fake plugin tree
whose checks are stubs that succeed, fail, or are missing.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
RUN_PASS = REPO / "plugins/eng/skills/monitor-firebase/scripts/run_pass.sh"
CHECKS = ("evaluate_profile.py", "check_db_load.py", "check_shallow_gets.py", "check_functions.py",
          "check_networks.py")


@unittest.skipUnless(shutil.which("bash"), "bash not installed")
class RunPass(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        plugin = self.tmp / "eng"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text('{"name": "eng", "version": "9.9.9"}')
        self.scripts = plugin / "skills/monitor-firebase/scripts"
        self.scripts.mkdir(parents=True)
        shutil.copy(RUN_PASS, self.scripts / "run_pass.sh")
        for name in CHECKS:
            (self.scripts / name).write_text("import sys\nsys.exit(0)\n")
        prof = plugin / "skills/profile-firebase-rtdb/scripts"
        prof.mkdir(parents=True)
        self.profile = prof / "run-profile.sh"
        self.profile.write_text("exit 0\n")

    def run_pass(self):
        r = subprocess.run(["bash", str(self.scripts / "run_pass.sh"), "--minutes", "1",
                            "--run-dir", str(self.tmp / "run")], capture_output=True, text=True, timeout=60)
        return r.stdout

    def test_all_checks_run_is_pass_ok(self):
        out = self.run_pass()
        self.assertIn("PASS OK", out)
        self.assertIn(f"SCRIPTS {self.tmp / 'run' / 'code/skills/monitor-firebase/scripts'}", out)

    def test_a_failing_check_is_a_fail_naming_the_script(self):
        (self.scripts / "evaluate_profile.py").write_text("import sys\nsys.exit(2)\n")
        out = self.run_pass()
        self.assertIn("FAIL  run       evaluate_profile.py exited 2: its check did not run", out)
        self.assertNotIn("PASS OK", out)
        self.assertIn("PASS 0 alert(s), 1 failed check(s)", out)

    def test_a_missing_check_script_is_a_fail(self):
        (self.scripts / "check_db_load.py").unlink()
        out = self.run_pass()
        self.assertIn("check_db_load.py exited 2", out)
        self.assertNotIn("PASS OK", out)

    def test_a_failed_profile_is_a_fail(self):
        self.profile.write_text("exit 1\n")
        out = self.run_pass()
        self.assertIn("run-profile.sh exited 1", out)
        self.assertNotIn("PASS OK", out)

    def test_removing_the_plugin_version_mid_pass_cannot_stop_the_checks(self):
        # The profiler step deletes the installed scripts, as the 16:42Z prune did.
        (self.scripts / "check_networks.py").write_text(
            'print(\'{"check": "networks", "level": "note", "key": "k", "text": "checked"}\')\n')
        self.profile.write_text(f"rm -rf '{self.scripts}'\nexit 0\n")
        out = self.run_pass()
        self.assertFalse(self.scripts.exists())
        self.assertIn("NOTE  networks  checked", out)  # the check really ran, from the copy
        self.assertIn("PASS OK", out)

    def test_an_unreadable_findings_line_is_a_fail(self):
        (self.scripts / "check_networks.py").write_text("print('{\"check\": \"networks\", \"lev')\n")
        out = self.run_pass()
        self.assertIn("a check wrote an unreadable line", out)
        self.assertNotIn("PASS OK", out)


if __name__ == "__main__":
    unittest.main()
