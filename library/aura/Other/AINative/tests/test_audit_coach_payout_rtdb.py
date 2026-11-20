"""audit-coach-payout reads /coachAnalytics through its rtdb_get.js copy, never over REST (ENG-1883)."""
import importlib.util
import json
import pathlib
import subprocess
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/audit-coach-payout/scripts"
UID = "A" * 28


def _load():
    spec = importlib.util.spec_from_file_location("audit_coach_payout_audit", SCRIPTS / "audit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeRun:
    """Stands in for subprocess.run on `node rtdb_get.js`; answers each stdin path from `values`."""

    def __init__(self, values, stderr=""):
        self.values, self.stderr, self.calls = values, stderr, []

    def __call__(self, cmd, input=None, env=None, **kwargs):
        if cmd[0] != "node":  # user_agent()'s git config lookup
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        self.calls.append({"cmd": cmd, "input": input, "env": env})
        lines = []
        for path in input.split("\n"):
            if path in self.values:
                value = self.values[path]
                lines.append(json.dumps({"path": path, "error": value["error"]} if isinstance(value, dict) and "error" in value
                                        else {"path": path, "value": value}))
        return subprocess.CompletedProcess(cmd, 0, stdout="\n".join(lines), stderr=self.stderr)


class TestDashboardRead(unittest.TestCase):
    def setUp(self):
        self.m = _load()

    def test_reads_exact_paths_with_values_through_the_bundled_helper(self):
        run = FakeRun({f"/coachAnalytics/{UID}/2025": [None] * 10 + [{"totalListens": 5}, {"totalListens": 7}],
                       f"/coachAnalytics/{UID}/2026": {"0": {"totalListens": 9}}})
        with mock.patch("subprocess.run", run), mock.patch.dict("os.environ", {"GCP_KEY": "{}"}, clear=False):
            out = self.m.fetch_dashboard(UID, {2025, 2026})
        self.assertEqual(out, {(2025, 11): {"totalListens": 5}, (2025, 12): {"totalListens": 7},
                               (2026, 1): {"totalListens": 9}})
        call = run.calls[0]
        self.assertEqual(pathlib.Path(call["cmd"][1]), SCRIPTS / "rtdb_get.js")
        self.assertIn("--values", call["cmd"])
        self.assertEqual(call["input"].split("\n"), [f"/coachAnalytics/{UID}/2025", f"/coachAnalytics/{UID}/2026"])
        self.assertTrue(call["env"]["AINATIVE_USER_AGENT"].startswith("AuraClaude/"))

    def test_empty_year_is_skipped(self):
        run = FakeRun({f"/coachAnalytics/{UID}/2025": None})
        with mock.patch("subprocess.run", run):
            self.assertEqual(self.m.fetch_dashboard(UID, {2025}), {})

    def test_error_row_fails_the_audit(self):
        run = FakeRun({f"/coachAnalytics/{UID}/2025": {"error": "GCP_KEY is unset"}})
        with mock.patch("subprocess.run", run), self.assertRaisesRegex(self.m.AuditError, "GCP_KEY is unset"):
            self.m.fetch_dashboard(UID, {2025})

    def test_missing_row_fails_the_audit(self):
        run = FakeRun({}, stderr="Error: RTDB_URL is unset")
        with mock.patch("subprocess.run", run), self.assertRaisesRegex(self.m.AuditError, "RTDB_URL is unset"):
            self.m.fetch_dashboard(UID, {2025})

    def test_node_not_installed_fails_the_audit(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("node")), \
                self.assertRaises(self.m.AuditError):
            self.m.fetch_dashboard(UID, {2025})

    def test_database_url_and_key_fallbacks(self):
        run = FakeRun({f"/coachAnalytics/{UID}/2025": None})
        env = {"GOOGLE_APPLICATION_CREDENTIALS": "/k.json"}
        with mock.patch("subprocess.run", run), mock.patch.dict("os.environ", env, clear=True):
            self.m.fetch_dashboard(UID, {2025})
        self.assertEqual(run.calls[0]["env"]["RTDB_URL"], self.m.RTDB_BASE)
        self.assertEqual(run.calls[0]["env"]["GCP_KEY"], "/k.json")

    def test_no_rest_read_left_in_the_script(self):
        text = (SCRIPTS / "audit.py").read_text(encoding="utf-8")
        self.assertNotIn("access_token", text)
        self.assertNotRegex(text, r"coachAnalytics/\{uid\}/\{year\}\.json")


if __name__ == "__main__":
    unittest.main()
