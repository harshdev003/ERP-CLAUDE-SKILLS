"""`gcloud_sa.py` runs gcloud as the GCP_KEY service account, never the user login.

A user gcloud login lapses under Google's reauth policy, and every unattended call
then fails with "Reauthentication failed" — on 2026-10-20 that turned all 15
conversation-guardrail triage replies' handoff categories into `unknown`. The
helper activates GCP_KEY into a private CLOUDSDK_CONFIG instead.

One suite, run against every copy (discovered by glob), with gcloud replaced by a
fake on PATH that logs each call. The load-bearing assertions are the negative
ones: with GCP_KEY set, nothing runs on the user's config — not even when
activation fails.
"""

from __future__ import annotations

import glob
import importlib.util
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
CANONICAL = REPO / "plugins/eng/skills/investigate-conversation-guardrail/scripts/gcloud_sa.py"
COPIES = sorted(glob.glob(str(REPO / "plugins/**/scripts/gcloud_sa.py"), recursive=True))

KEY = {"type": "service_account", "client_email": "<redacted-email>",
       "project_id": "proj", "private_key": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n"}
USER_CFG = "/user/home/.config/gcloud"

FAKE = r'''#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
rec = {"argv": argv, "config": os.environ.get("CLOUDSDK_CONFIG"), "x": os.environ.get("X"),
       "menv": os.environ.get("CLOUDSDK_METRICS_ENVIRONMENT"),
       "mver": os.environ.get("CLOUDSDK_METRICS_ENVIRONMENT_VERSION")}
if argv[:2] == ["auth", "activate-service-account"]:
    key = [a for a in argv if a.startswith("--key-file=")][0].split("=", 1)[1]
    rec["key_email"] = json.load(open(key))["client_email"]
    rec["key_mode"] = oct(os.stat(key).st_mode & 0o777)
    rec["key_path"] = key
with open(os.environ["FAKE_GCLOUD_LOG"], "a") as f:
    f.write(json.dumps(rec) + "\n")
if argv[:2] == ["auth", "activate-service-account"] and os.environ.get("FAKE_FAIL_ACTIVATE"):
    sys.stderr.write("ERROR: (gcloud.auth.activate-service-account) invalid key\n"
                     "to select an already authenticated account to use.\n")
    sys.exit(1)
if argv[:1] not in (["auth"], ["config"]):
    sys.stdout.write("out\n")
    sys.exit(int(os.environ.get("FAKE_RC", "0")))
'''


def _load(path: str):
    spec = importlib.util.spec_from_file_location(f"gcloud_sa_{abs(hash((path, os.urandom(4))))}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        bindir = pathlib.Path(self.tmp.name, "bin")
        bindir.mkdir()
        fake = bindir / "gcloud"
        fake.write_text(FAKE.replace("#!/usr/bin/env python3", f"#!{sys.executable}", 1))
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.log = pathlib.Path(self.tmp.name, "calls.log")
        self.env = {
            "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
            "FAKE_GCLOUD_LOG": str(self.log),
            "CLOUDSDK_CONFIG": USER_CFG,
            "HOME": self.tmp.name,
        }
        self.patch = mock.patch.dict(os.environ, self.env, clear=True)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]


class EveryCopy(Base):
    def test_copies_exist_and_match_the_canonical(self):
        self.assertGreaterEqual(len(COPIES), 2)
        for c in COPIES:
            self.assertEqual(pathlib.Path(c).read_bytes(), CANONICAL.read_bytes(), c)

    def _each(self):
        for path in COPIES:
            with self.subTest(copy=os.path.relpath(path, REPO)):
                self.log.unlink(missing_ok=True)
                os.environ.pop("GCP_KEY", None)
                os.environ.pop("FAKE_FAIL_ACTIVATE", None)
                yield path

    def test_unset_key_runs_on_the_user_login(self):
        for path in self._each():
            m = _load(path)
            out = m.run(["logging", "read", "f"], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0)
            calls = self.calls()
            self.assertEqual([c["argv"][:2] for c in calls], [["logging", "read"]])
            self.assertEqual(calls[0]["config"], USER_CFG)
            self.assertIn("user gcloud login", m.credential())

    def test_inline_key_activates_once_into_a_private_config(self):
        for path in self._each():
            os.environ["GCP_KEY"] = json.dumps(KEY)
            m = _load(path)
            m.run(["logging", "read", "a"], capture_output=True, text=True)
            m.run(["logging", "read", "b"], capture_output=True, text=True)
            calls = self.calls()
            self.assertEqual(sum(c["argv"][:2] == ["auth", "activate-service-account"] for c in calls), 1)
            act = calls[0]
            self.assertEqual(act["key_email"], KEY["client_email"])
            self.assertEqual(act["key_mode"], "0o600")
            self.assertFalse(os.path.exists(act["key_path"]), "key file must not outlive activation")
            for c in calls:
                self.assertNotEqual(c["config"], USER_CFG, c["argv"])
                self.assertEqual(c["config"], act["config"])
            self.assertEqual([c["argv"][-1] for c in calls if c["argv"][0] == "logging"], ["a", "b"])
            self.assertIn(KEY["client_email"], m.credential())

    def test_key_given_as_a_path(self):
        for path in self._each():
            kp = pathlib.Path(self.tmp.name, "key.json")
            kp.write_text(json.dumps(KEY))
            os.environ["GCP_KEY"] = str(kp)
            m = _load(path)
            m.run(["logging", "read"], capture_output=True)
            self.assertTrue(all(c["config"] != USER_CFG for c in self.calls()))
            self.assertEqual(self.calls()[0]["key_email"], KEY["client_email"])

    def test_failed_activation_never_falls_back_to_the_user(self):
        for path in self._each():
            os.environ["GCP_KEY"] = json.dumps(KEY)
            os.environ["FAKE_FAIL_ACTIVATE"] = "1"
            m = _load(path)
            out = m.run(["logging", "read"], capture_output=True, text=True)
            self.assertNotEqual(out.returncode, 0)
            self.assertIn("GCP_KEY", out.stderr)
            self.assertIn(KEY["client_email"], out.stderr)
            self.assertEqual([c["argv"][:2] for c in self.calls()], [["auth", "activate-service-account"]])
            # The cause, not only gcloud's trailing hint.
            self.assertIn("invalid key", out.stderr)
            raw = m.run(["logging", "read"], capture_output=True)
            self.assertIsInstance(raw.stderr, bytes)

    def test_malformed_key_runs_nothing(self):
        for path in self._each():
            os.environ["GCP_KEY"] = "{not json"
            m = _load(path)
            out = m.run(["logging", "read"], capture_output=True, text=True)
            self.assertNotEqual(out.returncode, 0)
            self.assertIn("GCP_KEY", out.stderr)
            self.assertEqual(self.calls(), [])
            self.assertIn("GCP_KEY", m.credential())

    def test_parallel_callers_activate_once(self):
        for path in self._each():
            os.environ["GCP_KEY"] = json.dumps(KEY)
            m = _load(path)
            threads = [threading.Thread(target=m.run, args=(["logging", "read"],),
                                        kwargs={"capture_output": True}) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            argv = [c["argv"][:2] for c in self.calls()]
            self.assertEqual(argv.count(["auth", "activate-service-account"]), 1)
            self.assertEqual(argv.count(["logging", "read"]), 8)

    def test_caller_env_is_kept(self):
        for path in self._each():
            os.environ["GCP_KEY"] = json.dumps(KEY)
            m = _load(path)
            m.run(["logging", "read"], capture_output=True, env={**os.environ, "X": "1"})
            last = self.calls()[-1]
            self.assertEqual(last["x"], "1")
            self.assertNotEqual(last["config"], USER_CFG)

    def test_cli_passes_the_exit_code_through(self):
        for path in self._each():
            env = {**os.environ, "GCP_KEY": json.dumps(KEY), "FAKE_RC": "3"}
            out = subprocess.run([sys.executable, path, "logging", "read"],
                                 capture_output=True, text=True, env=env)
            self.assertEqual(out.returncode, 3)
            self.assertIn(KEY["client_email"], out.stderr)
            self.assertEqual(out.stdout, "out\n")


    def _expected(self, path):
        skill = pathlib.Path(path).resolve().parents[1]
        plugin = json.loads((skill.parents[1] / ".claude-plugin/plugin.json").read_text())["name"]
        front = (skill / "SKILL.md").read_text().split("---", 2)[1]
        version = __import__("re").search(r'version:\s*"([^"]+)"', front).group(1)
        return f"{plugin}:{skill.name}", version

    def test_every_call_is_labelled_as_aura_claude(self):
        for path in self._each():
            for key in (None, json.dumps(KEY)):
                self.log.unlink(missing_ok=True)
                os.environ.pop("GCP_KEY", None)
                if key:
                    os.environ["GCP_KEY"] = key
                os.environ["AINATIVE_SESSION_NAME"] = "Firebase Security"
                os.environ["AINATIVE_UA_WHO"] = "jdoe"
                m = _load(path)
                m.run(["logging", "read"], capture_output=True)
                name, version = self._expected(path)
                for c in self.calls():
                    self.assertEqual(c["menv"], "AuraClaude", c["argv"])
                    self.assertEqual(c["mver"], f"jdoe/{name}@{version}+Firebase-Security", c["argv"])
                self.assertEqual(m.user_agent(), f"AuraClaude/jdoe/{name}@{version} (session Firebase-Security)")
                os.environ.pop("AINATIVE_UA_WHO")

    def test_session_falls_back_to_the_claude_session_id(self):
        for path in self._each():
            os.environ["CLAUDE_CODE_SESSION_ID"] = "0123456789abcdef"
            self.assertTrue(_load(path).user_agent().endswith(" (session 01234567)"))
            os.environ.pop("CLAUDE_CODE_SESSION_ID")
            self.assertTrue(_load(path).user_agent().endswith(" (session none)"))

    def test_cli_prints_the_user_agent(self):
        for path in self._each():
            out = subprocess.run([sys.executable, path, "--user-agent"], capture_output=True, text=True,
                                 env={**os.environ, "AINATIVE_SESSION_NAME": "s"})
            self.assertEqual(out.returncode, 0)
            self.assertEqual(out.stdout, _load(path).user_agent().replace("(session none)", "(session s)") + "\n")
            self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
