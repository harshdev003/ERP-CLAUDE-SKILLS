"""A skill script run without its packages names the venv, not `pip install --user`.

Outside a Claude Code session a bare `python3` is often Homebrew's, which has none of the skill
packages and refuses `pip install --user` (PEP 668). So the missing-package error names the
interpreter that ran and `~/.claude/python-env/bin/python3` (ENG-1908). The import is made to
fail at its single point, over every copy of each shared helper.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
VENV = "~/.claude/python-env/bin/python3"
NO_GOOGLE = {k: None for k in ("google", "google.cloud", "google.cloud.bigquery", "google.cloud.logging",
                               "google.oauth2", "google.api_core", "google.api_core.gapic_v1",
                               "google.api_core.gapic_v1.client_info", "google.auth")}


def _load(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(f"venv_hint_{abs(hash(str(path)))}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class BigQueryHelper(unittest.TestCase):
    def test_every_bq_copy_names_the_venv(self):
        copies = sorted(REPO.glob("plugins/*/skills/*/scripts/bq.py"))
        self.assertGreater(len(copies), 50)
        for path in copies:
            with self.subTest(str(path.relative_to(REPO))):
                bq = _load(path)
                bq._CLIENTS.clear()
                with mock.patch.dict(sys.modules, NO_GOOGLE):
                    with self.assertRaises(bq.BQUnavailable) as cm:
                        bq.client("p")
                self.assertIn(VENV, str(cm.exception))
                self.assertIn(sys.executable, str(cm.exception))


class LoggingHelpers(unittest.TestCase):
    """gcp.py and livekit_core.py exit 2 at import with one line naming the venv."""

    def test_every_copy_exits_with_the_venv_line(self):
        copies = sorted([*REPO.glob("plugins/*/skills/*/scripts/gcp.py"),
                         *REPO.glob("plugins/*/skills/*/scripts/livekit_core.py")])
        self.assertGreater(len(copies), 10)
        for path in copies:
            with self.subTest(str(path.relative_to(REPO))):
                err = io.StringIO()
                with mock.patch.dict(sys.modules, NO_GOOGLE), contextlib.redirect_stderr(err), \
                        self.assertRaises(SystemExit) as cm:
                    _load(path)
                self.assertEqual(cm.exception.code, 2)
                self.assertIn(VENV, err.getvalue())


class NoUserInstallHints(unittest.TestCase):
    def test_no_script_tells_you_to_pip_install_user(self):
        out = subprocess.run(["git", "grep", "-n", "pip install --user", "--", "plugins/**/*.py"],
                             cwd=REPO, capture_output=True, text=True).stdout
        self.assertEqual(out, "", "PEP 668 refuses it; name ~/.claude/python-env instead")


if __name__ == "__main__":
    unittest.main()
