#!/usr/bin/env python3
"""A soft-deleted profile's leftover Auth account: `reconcile` lists it, `confirm-deleted` gates it.

**The rule.** When a user deletes their account, UserService soft-deletes the profile
(`deletedAt`). If the Auth delete then fails, the Auth account stays live with nothing to log
into (PROD-7883). `reconcile` counted these as `deleted_with_live_auth` and never listed them,
so nothing removed them. It now writes them to `reconcile_deleted_with_live_auth.txt`, and
`confirm-deleted` keeps a uid only when, re-checked live, the Auth account exists and isn't
disabled, the profile still has `deletedAt`, and the delete is older than the grace window. Any
lookup failure drops the uid and counts it; it never confirms one.

Stdlib `unittest`, no network: the Identity Toolkit lookup and `GET /users/<uid>` are stubbed.
"""

from __future__ import annotations

import io
import os
import pathlib
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_orphan_reconcile_claim_candidates import _ReconcileHarness, load  # noqa: E402

DAY = timedelta(days=1)


def ago(delta):
    return (datetime.now(timezone.utc) - delta).isoformat().replace("+00:00", "Z")


class ReconcileListsDeletedWithLiveAuth(_ReconcileHarness, unittest.TestCase):
    ACCOUNTS = [
        {"uid": "owner", "email": "a@x.io"},                                # healthy
        {"uid": "softDeleted", "email": "left@x.io"},                       # deleted_with_live_auth
        {"uid": "googleSecondary", "providerData": [{"email": "A@x.io"}]},  # sso_resolvable
        {"uid": "profileless", "email": "nobody@x.io"},                     # orphan
    ]
    USERS = [["owner", "a@x.io", ""], ["softDeleted", "left@x.io", "2026-10-01T00:00:00Z"]]

    def test_the_soft_deleted_profile_lands_in_its_own_list(self):
        claims, out = self.run_reconcile(self.ACCOUNTS, self.USERS, [])
        self.assertEqual(self.list_file("reconcile_deleted_with_live_auth.txt"), ["softDeleted"])
        self.assertIn("soft-deleted profile, live auth: 1", out)
        self.assertIn("reconcile_deleted_with_live_auth.txt", out)
        # Every other bucket is unchanged.
        self.assertEqual(claims, ["googleSecondary"])
        self.assertEqual(self.list_file("orphan_reconcile_profileless.txt"), ["profileless"])
        self.assertEqual(self.list_file("orphan_reconcile_deleted_profile.txt"), [])
        self.assertRegex(out, r"deleted_with_live_auth\s*:\s+1\b")
        self.assertRegex(out, r"healthy\s*:\s+1\b")

    def test_an_empty_list_is_still_written(self):
        self.run_reconcile(self.ACCOUNTS[:1], self.USERS[:1], [])
        self.assertEqual(self.list_file("reconcile_deleted_with_live_auth.txt"), [])


class ConfirmDeleted(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.dict(os.environ, {"AURA_API_TOKEN": "t"}))
        self.auth = {}      # uid -> Identity Toolkit record
        self.profiles = {}  # uid -> profile row, or an exception to raise

    def lookup(self, uids):
        return {u: self.auth[u] for u in uids if u in self.auth}

    def profile(self, uid):
        p = self.profiles.get(uid)
        if isinstance(p, Exception):
            raise p
        return p

    def run_gate(self, uids, grace_days=1, lookup=None, argv=False):
        (self.tmp / "in.txt").write_text("\n".join(uids) + "\n")
        out = self.tmp / "out.txt"
        buf = io.StringIO()
        with mock.patch.object(self.mod, "_lookup", lookup or self.lookup), \
                mock.patch.object(self.mod, "_profile", self.profile), redirect_stdout(buf):
            if argv:
                held = self.tmp / "held.json"
                held.write_text("[]")
                held.chmod(0o600)
                with mock.patch.object(sys, "argv", ["orphan_cleanup.py", "confirm-deleted", "--in",
                                                     str(self.tmp / "in.txt"), "--out", str(out)]), \
                        mock.patch.dict(os.environ, {"HELD_UIDS_JSON": str(held)}):
                    self.mod.main()
                counts = None
            else:
                counts = self.mod.cmd_confirm_deleted(mock.Mock(
                    infile=str(self.tmp / "in.txt"), out=str(out), grace_days=grace_days))
        self.stdout = buf.getvalue()
        return out.read_text().split(), counts

    def deleted(self, uid, when=2 * DAY, **auth):
        self.auth[uid] = {"localId": uid, **auth}
        self.profiles[uid] = {"id": uid, "deletedAt": ago(when)}

    def test_all_conditions_met_is_confirmed(self):
        self.deleted("u1")
        got, counts = self.run_gate(["u1"])
        self.assertEqual(got, ["u1"])
        self.assertEqual(counts["confirmed"], 1)

    def test_each_failed_condition_drops_the_uid(self):
        self.deleted("restored")
        self.profiles["restored"]["deletedAt"] = None
        self.deleted("authGone")
        del self.auth["authGone"]
        self.deleted("disabled", disabled=True)
        self.deleted("fresh", when=timedelta(hours=3))
        self.deleted("profileGone")
        self.profiles["profileGone"] = None
        self.deleted("raises")
        self.profiles["raises"] = urllib.error.HTTPError("u", 500, "boom", {}, None)
        self.deleted("garbled")
        self.profiles["garbled"]["deletedAt"] = "not a date"
        self.deleted("ok")
        got, counts = self.run_gate(["restored", "authGone", "disabled", "fresh", "profileGone",
                                     "raises", "garbled", "ok"])
        self.assertEqual(got, ["ok"])
        self.assertEqual(counts, {"confirmed": 1, "auth_gone": 1, "auth_disabled": 1, "profile_gone": 1,
                                  "restored": 1, "in_grace": 1, "errored": 2})

    def test_the_grace_window_is_a_flag(self):
        self.deleted("u1", when=timedelta(hours=3))
        self.assertEqual(self.run_gate(["u1"], grace_days=0.1)[0], ["u1"])

    def test_a_failed_auth_lookup_skips_the_chunk(self):
        self.deleted("u1")

        def boom(uids):
            raise urllib.error.URLError("down")
        got, counts = self.run_gate(["u1"], lookup=boom)
        self.assertEqual(got, [])
        self.assertEqual(counts["errored"], 1)

    def test_output_never_prints_a_full_uid_or_an_email(self):
        self.deleted("abcdefghijklmnopqrst", email="left@x.io")
        self.profiles["abcdefghijklmnopqrst"] = RuntimeError("x")
        self.run_gate(["abcdefghijklmnopqrst"])
        self.assertNotIn("abcdefghijklmnopqrst", self.stdout)
        self.assertNotIn("left@x.io", self.stdout)

    def test_cli_wiring(self):
        self.deleted("u1")
        got, _ = self.run_gate(["u1"], argv=True)
        self.assertEqual(got, ["u1"])
        self.assertIn("confirm-deleted: candidates=1 confirmed=1", self.stdout)

    def test_it_needs_the_aura_token(self):
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(SystemExit):
            self.mod.cmd_confirm_deleted(mock.Mock(infile="x", out="y", grace_days=1))


if __name__ == "__main__":
    unittest.main()
