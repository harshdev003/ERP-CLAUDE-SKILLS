"""Tests for `search-gcp-by-user --auth` (Identity Platform user activity logs).

The fixture is real `identitytoolkit_project` entries — one per method seen on
prod, plus a dev SignUp error — with every string value that is not an enum
replaced by a `PII_<field>` sentinel and every uid by a test uid. Same shape the
producer emits; no real values.

The load-bearing assertion is the negative one: no sentinel may reach stdout.
"""

from __future__ import annotations

import contextlib
import copy
import importlib
import io
import json
import pathlib
import re
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/search-gcp-by-user/scripts"
FIXTURE = REPO / "tests/fixtures/identitytoolkit_entries.json"
TEST_UID = "TESTUIDaaaaaaaaaaaaaaaaaaaa01"

# gcp.py sys.exit(2)s at import without google-cloud-logging, and CI installs
# none. Reuse the unconditional google.* stub so local and CI take one path;
# the transport is stubbed per test, so the SDK is never called.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_deepgram_transport import _stubbed_google  # noqa: E402


def _load_search():
    """Import search.py with its siblings bound to THIS skill's copies.

    `format`, `gcp` and `normalize` exist in several GCP skills; a name already
    cached by another test would otherwise be reused here."""
    siblings = ("auth_logs", "format", "gcp", "normalize", "search")
    saved_path = list(sys.path)
    saved = {s: sys.modules.pop(s, None) for s in siblings}
    try:
        sys.path.insert(0, str(SCRIPTS))
        with _stubbed_google():
            return importlib.import_module("search"), importlib.import_module("auth_logs")
    finally:
        sys.path[:] = saved_path
        for s in siblings:
            sys.modules.pop(s, None)
            if saved[s] is not None:
                sys.modules[s] = saved[s]


search, auth_logs = _load_search()


def entries() -> list[dict]:
    return json.loads(FIXTURE.read_text())


def by_method(name: str) -> dict:
    return next(e for e in entries() if e["jsonPayload"]["methodName"].endswith("." + name))


def uid_paths(o, path="") -> set[str]:
    """Every payload path whose value is the test uid."""
    if isinstance(o, dict):
        return set().union(*(uid_paths(v, f"{path}.{k}" if path else k) for k, v in o.items()))
    return {path} if o == TEST_UID else set()


class _Auth:
    source, project, principal = "stub", "firebase-auratech16", "stub@example.invalid"


def run_main(stub_entries: list[dict], *extra: str) -> tuple[str, list[str]]:
    filters: list[str] = []

    def list_entries(auth, filter_str, max_entries=5000, order_by="timestamp desc", **kw):
        # Mirror gcp.list_entries: honour the order, stop at the cap.
        filters.append(order_by)
        filters.append(filter_str)
        ordered = sorted(stub_entries, key=lambda e: e["timestamp"],
                         reverse=order_by.endswith("desc"))
        return iter(ordered[:max_entries])

    orig = search.get_client, search.list_entries
    search.get_client = lambda project_override=None: _Auth()
    search.list_entries = list_entries
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = search.main(["--user", TEST_UID, "--auth", "--since", "2d", *extra])
    finally:
        search.get_client, search.list_entries = orig
    assert rc == 0, err.getvalue()
    return out.getvalue(), filters[1::2]


class TestNoPiiReachesOutput(unittest.TestCase):
    def test_fixture_carries_pii_sentinels(self):
        # Guards the negative test below against a fixture that lost its PII.
        text = FIXTURE.read_text()
        for field in ("email", "displayName", "federatedId", "callerIp"):
            self.assertIn(f'"PII_{field}"', text)

    def test_main_output_has_no_sentinel(self):
        out, _ = run_main(entries())
        self.assertNotIn("PII_", out)
        self.assertNotIn("@", out)

    def test_unknown_upstream_field_does_not_leak(self):
        e = copy.deepcopy(by_method("SignInWithIdp"))
        e["jsonPayload"]["response"]["phoneNumber"] = "PII_phoneNumber"
        e["jsonPayload"]["response"]["providerId"] = "google.com"
        out, _ = run_main([e])
        self.assertNotIn("PII_", out)

    def test_status_free_text_is_cut_to_the_enum(self):
        e = copy.deepcopy(by_method("SignUp"))
        e["jsonPayload"]["status"]["message"] = "INVALID_EMAIL : PII_email"
        self.assertEqual(auth_logs.summarize(e).error, "INVALID_EMAIL")
        e["jsonPayload"]["status"]["message"] = "PII_email is not valid"
        self.assertEqual(auth_logs.summarize(e).error, "(message withheld)")


class TestFilter(unittest.TestCase):
    def test_query_targets_auth_logs_not_the_cluster(self):
        _, filters = run_main(entries())
        self.assertEqual(len(filters), 1)
        self.assertIn('resource.type="identitytoolkit_project"', filters[0])
        self.assertNotIn("k8s_container", filters[0])

    def test_filter_covers_every_path_the_producer_puts_the_uid(self):
        seen = set().union(*(uid_paths(e["jsonPayload"], "jsonPayload") for e in entries()))
        queried = set(re.findall(r'(jsonPayload\.[\w.]+)="', auth_logs.uid_filter(TEST_UID)))
        self.assertTrue(seen, "fixture has no uid-bearing entry")
        self.assertLessEqual(seen - {"jsonPayload.metadata.tokenInfo.claims.sub"}, queried)


class TestCap(unittest.TestCase):
    def test_cap_keeps_the_newest_events_and_says_so(self):
        all_entries = entries()
        newest = max(all_entries, key=lambda e: e["timestamp"])["timestamp"][:23]
        oldest = min(all_entries, key=lambda e: e["timestamp"])["timestamp"][:23]
        out, _ = run_main(all_entries, "--max-entries", "3")
        self.assertIn("CAP HIT", out.splitlines()[0])
        self.assertIn(newest, out)
        self.assertNotIn(oldest, out)

    def test_no_cap_banner_under_the_cap(self):
        out, _ = run_main(entries())
        self.assertNotIn("CAP HIT", out)


class TestSummarize(unittest.TestCase):
    def test_idp_sign_in(self):
        s = auth_logs.summarize(by_method("SignInWithIdp"))
        self.assertEqual(s.method, "AuthenticationService.SignInWithIdp")
        self.assertIn(s.provider, {"google.com", "apple.com", "facebook.com"})
        self.assertIsNone(s.status_code)

    def test_token_lookup_takes_provider_from_claims(self):
        s = auth_logs.summarize(by_method("GetAccountInfo"))
        self.assertEqual(s.method, "AccountManagementService.GetAccountInfo")
        self.assertIsNotNone(s.provider)

    def test_error_entry(self):
        s = auth_logs.summarize(by_method("SignUp"))
        self.assertEqual((s.severity, s.status_code, s.error), ("ERROR", 3, "EMAIL_EXISTS"))

    def test_success_has_empty_status(self):
        # The producer emits `status: {}` on success, not an absent key.
        e = by_method("SignInWithCustomToken")
        self.assertEqual(e["jsonPayload"]["status"], {})
        self.assertIsNone(auth_logs.summarize(e).status_code)

    def test_empty_result_explains_the_gaps(self):
        out, _ = run_main([])
        self.assertIn("No auth events matched", out)
        self.assertIn("sign out and back in", out)


if __name__ == "__main__":
    unittest.main()
