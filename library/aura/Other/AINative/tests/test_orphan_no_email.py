#!/usr/bin/env python3
"""Profileless Auth accounts with no email: `reconcile` lists them, `confirm-no-email` gates them,
`delete` removes them in small, paced batches.

**The rule.** An Auth account with no profile under its uid and no email anywhere can't be checked
through `GET /users/email/:email`, so `confirm` skips it and these piled up. `confirm-no-email`
keeps a uid only when, re-checked live, it is not on the keep list, the Auth account exists, isn't
disabled and has no email, it has been inactive past the window, `GET /users/<uid>` is 404, RTDB
`/users/{uid}` doesn't exist, and any userId claim reaches no live profile. Any failed lookup drops
the uid; uncertainty never confirms. Its report is counts only: no uid, no email.

Stdlib `unittest`, no network: Identity Toolkit, UserService and the RTDB reader are stubbed.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_orphan_reconcile_claim_candidates import _ReconcileHarness, load  # noqa: E402

NOW = datetime.now(timezone.utc)


def ms(delta):
    return str(int((NOW - delta).timestamp() * 1000))


OLD = timedelta(days=2000)


class ReconcileListsNoEmail(_ReconcileHarness, unittest.TestCase):
    def test_no_email_without_own_profile_is_listed_unless_its_claim_is_live(self):
        accounts = [
            {"uid": "owner", "email": "a@x.io"},                                   # healthy
            {"uid": "ownerNoEmail"},                                               # has a profile
            {"uid": "fbNoEmail", "providerData": [{"providerId": "facebook.com"}]},  # candidate
            {"uid": "claimLive", "customClaims": {"userId": "owner"}},             # claim reaches owner
            {"uid": "claimDeleted", "customClaims": {"userId": "gone"}},           # claim to a deleted profile
        ]
        users = [["owner", "a@x.io", ""], ["ownerNoEmail", "", ""], ["gone", "", "2026-01-01T00:00:00Z"]]
        _, out = self.run_reconcile(accounts, users, [])
        self.assertEqual(self.list_file("orphan_reconcile_no_email.txt"), ["fbNoEmail", "claimDeleted"])
        self.assertIn("no email, no own profile: 2", out)
        self.assertIn("claim to a live profile, not listed: 1", out)
        self.assertEqual(self.list_file("orphan_reconcile_profileless.txt"), [])


class ConfirmNoEmail(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.held = self.tmp / "held.json"
        self.held.write_text(json.dumps(["heldUid"]))
        self.held.chmod(0o600)
        self.enterContext(mock.patch.dict(os.environ, {"AURA_API_TOKEN": "t", "HELD_UIDS_JSON": str(self.held)}))
        self.auth, self.profiles, self.rtdb = {}, {}, set()
        self.rtdb_error = None

    def lookup(self, uids):
        return {u: self.auth[u] for u in uids if u in self.auth}

    def profile(self, uid):
        p = self.profiles.get(uid)
        if isinstance(p, Exception):
            raise p
        return p

    def has_profile(self, uid):
        p = self.profiles.get(uid)
        if isinstance(p, Exception):
            return None
        return p is not None

    def rtdb_exists(self, uids):
        if self.rtdb_error:
            raise self.rtdb_error
        return {u: u in self.rtdb for u in uids}

    def account(self, uid, provider="facebook.com", created=OLD, last=OLD, **extra):
        self.auth[uid] = {"localId": uid, "createdAt": ms(created), "lastLoginAt": ms(last),
                          "providerUserInfo": [{"providerId": provider}] if provider else [], **extra}

    def run_gate(self, uids, inactive=365):
        (self.tmp / "in.txt").write_text("\n".join(uids) + "\n")
        out = self.tmp / "out.txt"
        buf = io.StringIO()
        with mock.patch.object(self.mod, "_lookup", self.lookup), \
                mock.patch.object(self.mod, "_profile", self.profile), \
                mock.patch.object(self.mod, "_has_profile", self.has_profile), \
                mock.patch.object(self.mod, "_rtdb_exists", self.rtdb_exists), \
                mock.patch.object(self.mod.time, "sleep"), redirect_stdout(buf):
            counts = self.mod.cmd_confirm_no_email(mock.Mock(
                infile=str(self.tmp / "in.txt"), out=str(out), min_inactive_days=inactive, pause=0))
        self.stdout = buf.getvalue()
        self.out = out
        return out.read_text().split(), counts

    def test_each_exclusion_is_counted_and_only_the_orphan_is_kept(self):
        self.account("ok")
        self.account("heldUid")
        self.account("disabled", disabled=True)
        self.account("hasEmail", providerUserInfo=[{"providerId": "google.com", "email": "e@x.io"}])
        self.account("recent", last=timedelta(days=30))
        self.account("liveProfile")
        self.profiles["liveProfile"] = {"id": "liveProfile"}
        self.account("inRtdb")
        self.rtdb.add("inRtdb")
        self.account("claimLive", customAttributes=json.dumps({"userId": "owner"}))
        self.profiles["owner"] = {"id": "owner", "deletedAt": None}
        self.account("claimDeleted", customAttributes=json.dumps({"userId": "softGone"}))
        self.profiles["softGone"] = {"id": "softGone", "deletedAt": "2026-01-01T00:00:00Z"}
        self.account("claimMissing", customAttributes=json.dumps({"userId": "nobody"}))
        self.account("raises")
        self.profiles["raises"] = urllib.error.HTTPError("u", 500, "boom", {}, None)
        got, c = self.run_gate(["ok", "heldUid", "authGone", "disabled", "hasEmail", "recent", "liveProfile",
                                "inRtdb", "claimLive", "claimDeleted", "claimMissing", "raises"])
        self.assertEqual(got, ["ok", "claimDeleted", "claimMissing"])
        self.assertEqual({k: c[k] for k in self.mod.NO_EMAIL_EXCLUDED},
                         {"held": 1, "auth_gone": 1, "disabled": 1, "has_email": 1, "recent_activity": 1,
                          "live_profile": 1, "rtdb_profile": 1, "claim_live": 1, "errored": 1})
        self.assertEqual(c["confirmed"], 3)

    def test_the_inactivity_window_is_a_flag_and_counts_refreshes(self):
        self.account("idle400", last=timedelta(days=400))
        self.account("refreshed", refresh=None, lastRefreshAt=(NOW - timedelta(days=10)).isoformat())
        self.assertEqual(self.run_gate(["idle400", "refreshed"])[0], ["idle400"])
        self.assertEqual(self.run_gate(["idle400"], inactive=500)[0], [])

    def test_an_account_with_no_timestamps_is_never_confirmed(self):
        self.auth["bare"] = {"localId": "bare"}
        got, c = self.run_gate(["bare"])
        self.assertEqual((got, c["recent_activity"]), ([], 1))

    def test_a_failed_rtdb_read_drops_the_chunk(self):
        self.account("ok")
        self.rtdb_error = RuntimeError("down")
        got, c = self.run_gate(["ok"])
        self.assertEqual((got, c["errored"]), ([], 1))

    def test_a_failed_auth_lookup_drops_the_chunk(self):
        def boom(uids):
            raise urllib.error.URLError("down")
        self.lookup = boom
        got, c = self.run_gate(["a", "b"])
        self.assertEqual((got, c["errored"]), ([], 2))

    def test_report_is_counts_only_and_the_file_is_private(self):
        uid = "fbOrphanUid0000000000000001"
        self.account(uid, created=timedelta(days=2200), last=timedelta(days=2200))
        self.account("appleOrphanUid00000000000002", provider="apple.com")
        self.account("noProviderUid0000000000000003", provider=None)
        self.account("emailUid000000000000000004", email="who@x.io")
        _, c = self.run_gate([uid, "appleOrphanUid00000000000002", "noProviderUid0000000000000003",
                              "emailUid000000000000000004"])
        self.assertEqual(c["by_provider"], {"Facebook": 1, "Apple": 1, "none": 1})
        self.assertEqual(sum(c["by_year"].values()), 3)
        self.assertEqual(c["by_inactive"], {"5y+": 3})
        for secret in (uid, "appleOrphan", "noProviderUid", "emailUid", "who@x.io", "heldUid"):
            self.assertNotIn(secret, self.stdout)
        self.assertIn("by provider: Apple 1, Facebook 1, none 1", self.stdout)
        self.assertEqual(self.out.stat().st_mode & 0o777, 0o600)

    def test_it_needs_the_keep_list(self):
        self.held.unlink()
        with self.assertRaises(SystemExit) as e:
            self.run_gate(["x"])
        self.assertEqual(e.exception.code, self.mod.HELD_EXIT)

    def test_it_needs_the_aura_token(self):
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(SystemExit):
            self.mod.cmd_confirm_no_email(mock.Mock(infile="x", out="y", min_inactive_days=365, pause=0))

    def test_cli_defaults(self):
        self.account("ok")
        (self.tmp / "in.txt").write_text("ok\n")
        seen = {}

        def capture(args):
            seen.update(vars(args))
        with mock.patch.object(self.mod, "cmd_confirm_no_email", capture), \
                mock.patch.object(sys, "argv", ["orphan_cleanup.py", "confirm-no-email", "--in", "x"]):
            self.mod.main()
        self.assertEqual((seen["min_inactive_days"], seen["pause"]), (365, 0.1))


class RtdbExists(unittest.TestCase):
    """The RTDB read goes through rtdb_get.js: exact /users/{uid} paths, existence only, no values."""

    def setUp(self):
        self.mod = load()

    def run_reader(self, stdout):
        calls = []

        def fake(cmd, **kw):
            calls.append((cmd, kw))
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout(kw["input"].split("\n")), stderr="")
        with mock.patch("subprocess.run", fake), mock.patch.dict(os.environ, {"AINATIVE_UA_WHO": "tester"}):
            return self.mod._rtdb_exists(["a", "b"]), calls

    def test_reads_exact_paths_without_values(self):
        got, calls = self.run_reader(lambda paths: "\n".join(
            json.dumps({"path": p, "exists": p == "/users/a"}) for p in paths))
        self.assertEqual(got, {"a": True, "b": False})
        cmd, kw = calls[0]
        self.assertEqual(kw["input"].split("\n"), ["/users/a", "/users/b"])
        self.assertNotIn("--values", cmd)
        self.assertTrue(cmd[1].endswith("cleanup-orphaned-auth/scripts/rtdb_get.js"))
        self.assertTrue(kw["env"]["AINATIVE_USER_AGENT"].startswith("AuraClaude/"))

    def test_an_error_or_missing_row_raises(self):
        for stdout in (lambda paths: json.dumps({"path": paths[0], "exists": False}),
                       lambda paths: "\n".join(json.dumps({"path": p, "error": "x"}) for p in paths)):
            with self.subTest(), self.assertRaises(RuntimeError):
                self.run_reader(stdout)


class PacedDelete(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        held = self.tmp / "held.json"
        held.write_text(json.dumps(["heldUidHeldUid0001"]))
        held.chmod(0o600)
        self.enterContext(mock.patch.dict(os.environ, {"HELD_UIDS_JSON": str(held)}))
        self.uids = [f"freeUidFreeUid{i:04d}" for i in range(250)]
        (self.tmp / "in.txt").write_text("\n".join(["heldUidHeldUid0001", *self.uids]) + "\n")

    def delete(self, *extra):
        batches, sleeps = [], []

        def post(url, body, bearer=None, headers=None):
            batches.append(len(body["localIds"]))
            return {}
        out = io.StringIO()
        with mock.patch.object(self.mod, "_post", side_effect=post), \
                mock.patch.object(self.mod, "_creds", return_value=mock.Mock(token="t")), \
                mock.patch.object(self.mod, "_lookup", side_effect=lambda u: {x: {"localId": x} for x in u}), \
                mock.patch.object(self.mod.time, "sleep", side_effect=sleeps.append), \
                mock.patch.object(sys, "argv", ["orphan_cleanup.py", "delete", "--in", str(self.tmp / "in.txt"),
                                                "--done", str(self.tmp / "done.log"),
                                                "--backup", str(self.tmp / "backup.ndjson"), *extra]), \
                redirect_stdout(out):
            self.mod.main()
        return batches, sleeps, out.getvalue()

    def test_batches_of_100_with_a_pause_between(self):
        batches, sleeps, _ = self.delete("--execute", "--pause", "7")
        self.assertEqual(batches, [100, 100, 50])
        self.assertEqual(sleeps, [7.0, 7.0, 7.0])
        for name in ("backup.ndjson", "done.log"):
            self.assertEqual((self.tmp / name).stat().st_mode & 0o777, 0o600, name)
        self.assertNotIn("heldUidHeldUid0001", (self.tmp / "backup.ndjson").read_text())

    def test_files_left_by_an_earlier_run_are_tightened(self):
        backup, done = self.tmp / "backup.ndjson", self.tmp / "done.log"
        backup.write_text("".join(json.dumps({"localId": x}) + "\n" for x in self.uids))
        done.write_text("")
        backup.chmod(0o644)
        done.chmod(0o644)
        self.delete("--execute", "--pause", "0")
        for f in (backup, done):
            self.assertEqual(f.stat().st_mode & 0o777, 0o600, f.name)

    def test_counts_only_dry_run_prints_no_uid(self):
        batches, _, out = self.delete("--counts-only")
        self.assertEqual(batches, [])
        self.assertIn("would delete 250 orphan auth credential(s) in 3 batch(es) of 100, 5s apart", out)
        self.assertIn("kept: 1", out)
        self.assertNotIn("freeUid", out)
        self.assertNotIn("heldUid", out)


if __name__ == "__main__":
    unittest.main()
