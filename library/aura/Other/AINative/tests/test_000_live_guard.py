"""Installs tests/live_guard.py for the whole run (ENG-1746) and checks it holds.

Named to sort first: `unittest discover` imports test modules in name order, so the guard is in place
before any other module is imported or any test runs.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import socket
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import live_guard  # noqa: E402

live_guard.install()


class LiveGuard(unittest.TestCase):
    def test_installed(self):
        self.assertTrue(live_guard.installed())

    def test_live_commands_fail_in_child_processes_too(self):
        for name in live_guard.BLOCKED:
            r = subprocess.run(["sh", "-c", f"{name} --version"], capture_output=True, text=True)
            self.assertEqual(r.returncode, live_guard.EXIT, name)
            self.assertIn("blocked in tests", r.stderr)

    def test_no_credentials_reach_a_test(self):
        for k in ("GCP_KEY", "NOTION_TOKEN", "LINEAR_API_KEY", "SLACK_BOT_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS"):
            self.assertNotIn(k, os.environ)
        home = os.path.dirname(os.path.dirname(shutil.which("gcloud")))
        self.assertEqual(os.path.dirname(os.environ["CLOUDSDK_CONFIG"]), home)

    def test_outside_network_is_refused(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(s.close)
        with self.assertRaises(live_guard.LiveCallBlocked):
            s.connect(("192.0.2.1", 443))

    def test_loopback_is_allowed(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(srv.close)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(c.close)
        c.connect(srv.getsockname())


if __name__ == "__main__":
    unittest.main()
