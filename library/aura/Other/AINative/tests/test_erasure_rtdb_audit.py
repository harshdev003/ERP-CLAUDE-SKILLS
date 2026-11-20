"""Tests for `respond-to-erasure`'s `check_rtdb_audit.py`.

The script turns FirebaseDenormalizer's "Deleted secondary nodes" audit line into
done / failed / unverified. The rule that matters is one-directional: only one
well-formed line with failed == 0 is "done". A read error, no line, two lines or a
line without counts must never read as done. The transport (`gcp`) is stubbed at
its one chokepoint; the payload shape is the real one, including the float counts
Cloud Logging returns (69.0, not 69).
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import types
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/eng/skills/respond-to-erasure/scripts/check_rtdb_audit.py"
UID = "TESTUIDaaaaaaaaaaaaaaaaaaaa01"


def _load():
    spec = importlib.util.spec_from_file_location("check_rtdb_audit", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


audit = _load()


def _line(removed=69.0, failed=0.0, **extra) -> dict:
    payload = {
        "message": f"Deleted secondary nodes for user '{UID}'",
        "userId": UID,
        "functionName": "userDeleted",
        "transaction": "t",
        "serviceContext": {"service": "firebase-denormalizer", "version": "2.6.0"},
        **extra,
    }
    if removed is not None:
        payload["removed"] = removed
    if failed is not None:
        payload["failed"] = failed
    return {"timestamp": "2026-10-25T06:09:19.246000Z", "jsonPayload": payload}


def _error(reference, message="Error removing reference") -> dict:
    """An error line in the shape removeReference logs: same transaction, the failed reference."""
    return {"timestamp": "2026-10-25T06:09:00Z", "severity": "ERROR", "jsonPayload": {
        "message": message, "reference": reference, "functionName": "userDeleted", "transaction": "t",
        "errorCode": None, "errorName": "Error", "tooManyTriggers": False}}


def _fake_gcp(entries=None, raises=None, errors=None, errors_raise=None):
    calls = []
    fake = types.ModuleType("gcp")

    def get_client(project_override=None):
        if raises:
            raise raises
        calls.append(("project", project_override))
        return object()

    def list_entries(auth, filter_str, **kw):
        calls.append(("filter", filter_str))
        if "jsonPayload.transaction=" in filter_str:
            if errors_raise:
                raise errors_raise
            return iter(errors or [])
        return iter(entries or [])

    fake.get_client, fake.list_entries = get_client, list_entries
    return fake, calls


def run(entries=None, raises=None, **kw):
    fake, calls = _fake_gcp(entries, raises, **kw)
    with mock.patch.dict(sys.modules, {"gcp": fake}):
        return audit.check(UID, "2026-10-25T06:05:00Z"), calls


class Verdicts(unittest.TestCase):
    def test_one_line_failed_zero_is_done(self):
        got, _ = run([_line()])
        self.assertEqual(got["status"], "done")
        self.assertEqual((got["removed"], got["failed"]), (69, 0))

    def test_failed_nodes_is_failed(self):
        got, _ = run([_line(removed=66.0, failed=3.0)])
        self.assertEqual(got["status"], "failed")

    def test_never_done_without_a_clean_line(self):
        cases = {
            "no line": run([]),
            "two lines": run([_line(), _line()]),
            "no counts": run([_line(removed=None, failed=None)]),
            "string counts": run([_line(removed="69", failed="0")]),
            "fractional": run([_line(failed=0.5)]),
            "nan": run([_line(failed=float("nan"))]),
            "read error": run(raises=RuntimeError("403")),
        }
        for name, (got, _) in cases.items():
            with self.subTest(name):
                self.assertEqual(got["status"], "unverified")


class FailedNodes(unittest.TestCase):
    """A failed run names its nodes from the error lines, never their keys (a uid or an email)."""

    def test_error_lines_name_the_nodes(self):
        got, calls = run([_line(removed=67.0, failed=2.0)], errors=[
            _error(f"/userListened/{UID}", "Partially removed reference: children remain"),
            _error(f"/userPlayed/{UID}", "Error removing reference in batches")])
        self.assertEqual(got["failed_nodes"], ["userListened", "userPlayed"])
        self.assertNotIn(UID, json.dumps(got))
        flt = [v for k, v in calls if k == "filter"][1]
        self.assertIn('jsonPayload.transaction="t"', flt)
        self.assertIn("severity>=ERROR", flt)

    def test_repeated_and_unrelated_error_lines_dont_count(self):
        got, _ = run([_line(removed=67.0, failed=2.0)], errors=[
            _error(f"/userListened/{UID}"), _error(f"/userListened/{UID}"),
            _error(f"/userPlayed/{UID}", "Something else went wrong"),
            {"severity": "ERROR", "jsonPayload": {"message": "Error removing reference", "transaction": "t"}}])
        self.assertEqual(got["failed_nodes"], ["userListened"])

    def test_two_email_keys_are_two_nodes(self):
        got, _ = run([_line(removed=67.0, failed=2.0)], errors=[_error("/emailUser/a"), _error("/emailUser/b")])
        self.assertEqual(got["failed_nodes"], ["emailUser", "emailUser"])

    def test_unreadable_error_lines_are_unknown_not_empty(self):
        got, _ = run([_line(removed=66.0, failed=3.0)], errors_raise=RuntimeError("504"))
        self.assertEqual(got["status"], "failed")
        self.assertIsNone(got["failed_nodes"])

    def test_no_transaction_is_unknown(self):
        line = _line(removed=66.0, failed=3.0)
        del line["jsonPayload"]["transaction"]
        got, _ = run([line])
        self.assertIsNone(got["failed_nodes"])

    def test_done_reads_no_error_lines(self):
        _, calls = run([_line()])
        self.assertEqual(len([1 for k, _ in calls if k == "filter"]), 1)


class Query(unittest.TestCase):
    def test_reads_the_denormalizer_log_in_the_firebase_project(self):
        _, calls = run([_line()])
        self.assertIn(("project", "firebase-auratech16"), calls)
        flt = next(v for k, v in calls if k == "filter")
        self.assertIn('logName="projects/firebase-auratech16/logs/firebase-denormalizer"', flt)
        self.assertIn(f'jsonPayload.userId="{UID}"', flt)
        self.assertIn('timestamp>="2026-10-25T06:04:00+00:00"', flt)


class _Stdout(io.StringIO):
    def reconfigure(self, **_):
        pass


class Output(unittest.TestCase):
    def test_stdout_never_carries_the_uid(self):
        fake, _ = _fake_gcp([_line()])
        out = _Stdout()
        with mock.patch.dict(sys.modules, {"gcp": fake}), \
                mock.patch.object(sys, "argv", ["x", "--uid", UID, "--since", "2026-10-25T06:05:00Z"]), \
                mock.patch.object(sys, "stdout", out), \
                contextlib.suppress(SystemExit):
            audit.main()
        self.assertNotIn(UID, out.getvalue())
        self.assertEqual(json.loads(out.getvalue())["status"], "done")


if __name__ == "__main__":
    unittest.main()
