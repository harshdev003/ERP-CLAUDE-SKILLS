"""search-gcp-by-user queries the uid paths services actually log, and keeps what they match.

The caller's uid is logged as the flat `jsonPayload.userId` (the request context's meta);
user-service's verifyToken logs the signed-in uid as `jsonPayload.signInUid` (ENGBACK-2797).
No service logs `verifiedToken`, so a filter on it matches nothing. A verifyToken line called
by a service carries that service's id in `userId`, so the client-side check must still keep
it when `signInUid` matches. No network: the Cloud Logging call is stubbed.
"""
from __future__ import annotations

import importlib
import io
import pathlib
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "plugins/eng/skills/search-gcp-by-user/scripts"
UID = "uidUidUidUidUidUidUidUid0001"

# gcp.py sys.exit(2)s at import without google-cloud-logging, and CI installs none.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_deepgram_transport import _stubbed_google  # noqa: E402


def load():
    """Import search.py with its siblings bound to this skill's copies, then restore."""
    siblings = ("auth_logs", "format", "gcp", "normalize", "search")
    saved_path = list(sys.path)
    saved = {s: sys.modules.pop(s, None) for s in siblings}
    try:
        sys.path.insert(0, str(SCRIPTS))
        with _stubbed_google():
            return importlib.import_module("search")
    finally:
        sys.path[:] = saved_path
        for s in siblings:
            sys.modules.pop(s, None)
            if saved[s] is not None:
                sys.modules[s] = saved[s]


def entry(ts, **payload):
    return {"timestamp": ts, "severity": "INFO", "resource": {"type": "k8s_container", "labels": {}},
            "jsonPayload": {"message": "m", **payload}}


class FilterTest(unittest.TestCase):
    def run_search(self, entries):
        mod = load()
        seen = {}

        def list_entries(auth, filter_str, **kw):
            seen["filter"] = filter_str
            return entries
        auth = mock.Mock(source="sa", project="p", principal="x")
        out = io.StringIO()
        with mock.patch.object(mod, "get_client", return_value=auth), \
                mock.patch.object(mod, "list_entries", list_entries), \
                redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.assertEqual(mod.main(["--user", UID]), 0)
        return seen["filter"], out.getvalue()

    def test_queries_the_logged_paths_only(self):
        flt, _ = self.run_search([])
        self.assertIn(f'jsonPayload.userId="{UID}"', flt)
        self.assertIn(f'jsonPayload.signInUid="{UID}"', flt)
        self.assertNotIn("verifiedToken", flt)

    def test_a_verify_token_line_called_by_a_service_is_kept(self):
        _, out = self.run_search([
            entry("2026-11-17T22:10:00Z", userId="serviceAccountId", signInUid=UID, message="No profile for the token"),
            entry("2026-11-17T22:11:00Z", userId="someoneElse", message="unrelated"),
        ])
        self.assertIn("No profile for the token", out)
        self.assertNotIn("unrelated", out)


if __name__ == "__main__":
    unittest.main()
