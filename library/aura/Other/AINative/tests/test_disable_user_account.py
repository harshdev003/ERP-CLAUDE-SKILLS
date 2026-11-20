"""eng:disable-user-account (ENG-1780): one Firebase Auth account, disabled, re-enabled or checked.

Pinned here: a prod write is refused without --prod, a go and a card; disable revokes refresh tokens
and enable leaves them alone; the read-back must match or the run fails; a failed read is UNVERIFIED;
and nothing printed carries the email or more than the uid prefix. Identity Toolkit is stubbed at
`call`, its one chokepoint, and at urlopen for the error mapping.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "plugins/eng/skills/disable-user-account/scripts"
sys.path.insert(0, str(SCRIPTS))
import account  # noqa: E402

UID = "AbCdEfGh1234567890uvwxyz"
EMAIL = "someone+private@example.com"
KEY = json.dumps({"client_email": "<redacted-email>",
                  "project_id": "firebase-auratech16"})
NOW = 1_790_000_000
HELD_UID = "HeldUidHeldUidHeld0000000001"
# Never the machine's real keep list: a fixture list holding one uid that isn't UID.
HELD = pathlib.Path(tempfile.mkdtemp()) / "held-uids.json"
HELD.write_text(json.dumps([HELD_UID]))
HELD.chmod(0o600)


class Fake:
    """Identity Toolkit with one account; `apply=False` drops writes to test the read-back."""

    def __init__(self, disabled=False, valid_since=1_700_000_000, apply=True, fail=None):
        self.user = {"localId": UID, "email": EMAIL, "displayName": "Private Name", "disabled": disabled,
                     "validSince": str(valid_since),
                     "providerUserInfo": [{"providerId": "password", "email": EMAIL}]}
        self.apply, self.fail, self.calls = apply, fail, []

    def __call__(self, project, method, body):
        self.calls.append((project, method, body))
        if self.fail == method:
            raise account.Unverified(f"accounts:{method} on {project}: INSUFFICIENT_PERMISSION")
        if method == "lookup":
            hit = body.get("localId") == [UID] or body.get("email") == [EMAIL]
            return {"users": [dict(self.user)]} if hit else {}
        if method == "update" and self.apply:
            self.user["disabled"] = body["disableUser"]
            if "validSince" in body:
                self.user["validSince"] = body["validSince"]
        return {"localId": UID}


def run(args, fake, held=HELD):
    out = io.StringIO()
    with mock.patch.dict(os.environ, {"GCP_KEY": KEY, "HELD_UIDS_JSON": str(held)}), \
            mock.patch.object(account, "call", fake), \
            contextlib.redirect_stdout(out):
        code = account.run(args, now=lambda: NOW)
    return code, out.getvalue()


class ActionTests(unittest.TestCase):
    def test_status_is_read_only(self):
        f = Fake(disabled=True)
        code, out = run(["status", "--project", "firebase-auratech16", "--uid", UID], f)
        self.assertEqual(code, 0)
        self.assertEqual([m for _, m, _ in f.calls], ["lookup"])
        self.assertIn("before  disabled=yes  providers=password", out)

    def test_disable_revokes_and_reads_back(self):
        f = Fake()
        code, out = run(["disable", "--project", "aura-dev", "--uid", UID], f)
        self.assertEqual(code, 0, out)
        update = [b for _, m, b in f.calls if m == "update"]
        self.assertEqual(update, [{"localId": UID, "disableUser": True, "validSince": str(NOW)}])
        self.assertIn("after   disabled=yes", out)
        self.assertIn("refresh tokens revoked", out)

    def test_disable_never_moves_valid_since_back(self):
        f = Fake(valid_since=NOW + 5)
        code, out = run(["disable", "--project", "aura-dev", "--uid", UID], f)
        self.assertEqual(code, 0, out)
        self.assertEqual([b["validSince"] for _, m, b in f.calls if m == "update"], [str(NOW + 5)])

    def test_enable_leaves_valid_since_alone(self):
        f = Fake(disabled=True)
        code, out = run(["enable", "--project", "aura-dev", "--uid", UID], f)
        self.assertEqual(code, 0, out)
        self.assertEqual([b for _, m, b in f.calls if m == "update"], [{"localId": UID, "disableUser": False}])

    def test_read_back_mismatch_fails(self):
        code, out = run(["disable", "--project", "aura-dev", "--uid", UID], Fake(apply=False))
        self.assertEqual(code, 1)
        self.assertIn("FAIL: read-back", out)

    def test_email_lookup_never_prints_the_email(self):
        code, out = run(["status", "--project", "aura-dev", "--email", EMAIL], Fake())
        self.assertEqual(code, 0)
        self.assertIn(UID[:6] + "…", out)
        for leak in (EMAIL, "example.com", "Private Name", UID):
            self.assertNotIn(leak, out)

    def test_label_keeps_a_provider_prefix(self):
        self.assertEqual(account.label("google:1234567890"), "google:123456")
        self.assertEqual(account.label("AbCdEfGh123"), "AbCdEf")

    def test_unknown_account_is_unverified(self):
        code, out = run(["status", "--project", "aura-dev", "--uid", "nope"], Fake())
        self.assertEqual(code, 2)
        self.assertIn("UNVERIFIED: no such account in aura-dev", out)

    def test_unknown_email_points_at_uid(self):
        code, out = run(["status", "--project", "aura-dev", "--email", "other@example.org"], Fake())
        self.assertEqual(code, 2)
        self.assertIn("use --uid", out)
        self.assertNotIn("example.org", out)

    def test_failed_write_is_unverified(self):
        code, out = run(["disable", "--project", "aura-dev", "--uid", UID], Fake(fail="update"))
        self.assertEqual(code, 2)
        self.assertIn("UNVERIFIED", out)


class KeepListTests(unittest.TestCase):
    """A held account is never disabled, re-enabled or rewritten; a write without the list does nothing."""

    def test_a_listed_uid_is_refused_before_any_write(self):
        fake = Fake()
        fake.user["localId"] = HELD_UID
        orig = fake.__call__

        def call(project, method, body):
            if method == "lookup" and body.get("localId") == [HELD_UID]:
                return {"users": [dict(fake.user)]}
            return orig(project, method, body)
        for action in ("disable", "enable"):
            with self.subTest(action=action):
                fake.calls.clear()
                code, out = run([action, "--project", "aura-dev", "--uid", HELD_UID], call)
                self.assertEqual(code, 1)
                self.assertIn(f"skip {HELD_UID[:6]}…: keep list", out)
                self.assertNotIn(HELD_UID, out)
                self.assertFalse([c for c in fake.calls if c[1] == "update"])

    def test_a_write_without_the_list_is_refused(self):
        for held in (pathlib.Path(tempfile.mkdtemp()) / "missing.json", None):
            if held is None:
                held = pathlib.Path(tempfile.mkdtemp()) / "empty.json"
                held.write_text("[]")
                held.chmod(0o600)
            fake = Fake()
            code, out = run(["disable", "--project", "aura-dev", "--uid", UID], fake, held=held)
            self.assertEqual(code, 1)
            self.assertIn("REFUSED", out)
            self.assertEqual(fake.calls, [])

    def test_status_needs_no_list(self):
        fake = Fake()
        code, _ = run(["status", "--project", "aura-dev", "--uid", UID], fake,
                      held=pathlib.Path(tempfile.mkdtemp()) / "missing.json")
        self.assertEqual(code, 0)

    def test_an_unlisted_uid_is_written(self):
        fake = Fake()
        code, _ = run(["disable", "--project", "aura-dev", "--uid", UID], fake)
        self.assertEqual(code, 0)
        self.assertTrue([c for c in fake.calls if c[1] == "update"])


class ProdGuardTests(unittest.TestCase):
    PROD = ["--project", "firebase-auratech16", "--uid", UID]

    def test_prod_write_refused_without_all_three(self):
        prod, go, card = ["--prod"], ["--go", "disable it"], ["--card", "ENGBACK-1"]
        for args in (go + card, prod + card, prod + go, []):
            for action in ("disable", "enable"):
                f = Fake()
                code, out = run([action] + self.PROD + args, f)
                self.assertEqual(code, 1, (action, args))
                self.assertIn("REFUSED", out)
                self.assertEqual(f.calls, [], "refused before any call")

    def test_blank_go_is_refused(self):
        code, _ = run(["disable"] + self.PROD + ["--prod", "--go", "  ", "--card", "ENGBACK-1"], Fake())
        self.assertEqual(code, 1)

    def test_prod_write_prints_the_card_record(self):
        code, out = run(["disable"] + self.PROD + ["--prod", "--go", "yes disable AbCdEfGh", "--card", "ENGBACK-1",
                                                   "--reason", "abuse"], Fake())
        self.assertEqual(code, 0, out)
        self.assertIn('card record (ENGBACK-1): disable AbCdEf… on firebase-auratech16', out)
        self.assertIn('go: "yes disable AbCdEfGh"; reason: abuse', out)

    def test_card_record_masks_an_email_in_the_go(self):
        code, out = run(["disable"] + self.PROD + ["--prod", "--go", f"yes disable {EMAIL}", "--card", "ENGBACK-1"],
                        Fake())
        self.assertEqual(code, 0, out)
        self.assertIn('go: "yes disable …@…"', out)
        self.assertNotIn("example.com", out)

    def test_dev_needs_no_go(self):
        code, out = run(["disable", "--project", "aura-dev", "--uid", UID], Fake())
        self.assertEqual(code, 0)
        self.assertNotIn("card record", out)

    def test_other_projects_rejected(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            run(["status", "--project", "aura-main", "--uid", UID], Fake())


class TransportTests(unittest.TestCase):
    def test_permission_error_names_role_project_and_account(self):
        err = urllib.error.HTTPError("u", 400, "x", {}, io.BytesIO(
            b'{"error": {"message": "INSUFFICIENT_PERMISSION"}}'))
        with mock.patch.dict(os.environ, {"GCP_KEY": KEY}), mock.patch.object(account, "_token", lambda: "t"), \
                mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(account.Unverified) as cm:
                account.call("aura-dev", "lookup", {"localId": [UID]})
        msg = str(cm.exception)
        for part in ("roles/firebaseauth.admin", "aura-dev", "claude@firebase-auratech16", "GCP_KEY"):
            self.assertIn(part, msg)

    def test_request_carries_the_user_agent(self):
        seen = {}

        class R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"users": []}'

        def fake(req, *a, **k):
            seen["ua"] = req.get_header("User-agent")
            seen["url"] = req.full_url
            return R()
        with mock.patch.object(account, "_token", lambda: "t"), mock.patch("urllib.request.urlopen", fake):
            account.call("aura-dev", "lookup", {"localId": [UID]})
        self.assertRegex(seen["ua"], r"^AuraClaude/[A-Za-z0-9._-]+/eng:disable-user-account@")
        self.assertTrue(seen["url"].endswith("/projects/aura-dev/accounts:lookup"))


if __name__ == "__main__":
    unittest.main()
