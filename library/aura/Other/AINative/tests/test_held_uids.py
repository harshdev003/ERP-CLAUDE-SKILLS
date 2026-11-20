"""The held-account keep list (`held_uids.py`, ENG-1890): two accounts no job may delete or rewrite.

Pinned over every copy of the helper: a missing, empty, unreadable, malformed or readable-by-others
list refuses (never reads as empty), a listed uid is skipped with its prefix only, and the CLI's exit
codes. Then through the skills that act on a list of uids: delete-facebook-user-data skips a listed
uid before any delete, dry runs included, and preserve-incident-account names its target as held
without refusing. clean-up-users, cleanup-orphaned-auth and disable-user-account have their own
keep-list tests next to their other tests. A fixture uid only, never the real list.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "plugins/eng/skills/cleanup-orphaned-auth/scripts/held_uids.py"
COPIES = sorted(ROOT.glob("plugins/**/scripts/held_uids.py"))
# Every skill that deletes or rewrites users ships the helper.
SKILLS = {"clean-up-users", "cleanup-orphaned-auth", "disable-user-account", "delete-coach",
          "delete-facebook-user-data", "respond-to-erasure", "preserve-incident-account", "fix-one-auth"}
HELD = "HeldUidHeldUidHeld0000000001"
FREE = "FreeUidFreeUidFree0000000001"


def load(path, name=None):
    spec = importlib.util.spec_from_file_location(name or f"held_uids_{path.parts[-3]}", path)
    mod = sys.modules[spec.name] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def held_file(data, mode=0o600):
    p = pathlib.Path(tempfile.mkdtemp()) / "held-uids.json"
    if data is not None:
        p.write_text(data if isinstance(data, str) else json.dumps(data))
        p.chmod(mode)
    return p


class Copies(unittest.TestCase):
    def test_every_delete_skill_ships_an_identical_copy(self):
        self.assertEqual({c.parts[-3] for c in COPIES}, SKILLS)
        for c in COPIES:
            self.assertEqual(c.read_bytes(), CANONICAL.read_bytes(), c)


class Load(unittest.TestCase):
    def test_refuses_what_it_cannot_trust(self):
        cases = {"missing": held_file(None), "empty": held_file([]), "not json": held_file("nope"),
                 "not a list": held_file({"uids": [HELD]}), "blank uid": held_file([""]),
                 "group readable": held_file([HELD], 0o640)}
        for path in COPIES:
            h = load(path)
            for name, f in cases.items():
                with self.subTest(copy=path.parts[-3], case=name):
                    with self.assertRaises(h.Refused):
                        h.load(p=f)

    def test_reads_the_env_path_and_splits(self):
        h = load(CANONICAL)
        f = held_file([HELD])
        with mock.patch.dict(os.environ, {"HELD_UIDS_JSON": str(f)}):
            held = h.load()
        self.assertEqual(held, {HELD})
        self.assertEqual(h.split([FREE, HELD], held), ([FREE], [HELD]))
        self.assertEqual(h.skip_line(HELD), f"skip {HELD[:6]}…: keep list")
        # A provider prefix is kept, and a short id is never printed in full.
        self.assertEqual(h.skip_line("google:1234567890abc"), "skip google:123456…: keep list")
        self.assertEqual(h.skip_line("abcd"), "skip ab…: keep list")
        self.assertNotIn("abcd", h.skip_line("abcd"))

    def test_not_required_reads_a_missing_list_as_empty_but_still_refuses_a_bad_one(self):
        h = load(CANONICAL)
        self.assertEqual(h.load(required=False, p=held_file(None)), frozenset())
        self.assertEqual(h.load(required=False, p=held_file([])), frozenset())
        with self.assertRaises(h.Refused):
            h.load(required=False, p=held_file([HELD], 0o644))


class Cli(unittest.TestCase):
    def check(self, f, uid):
        env = {**os.environ, "HELD_UIDS_JSON": str(f)}
        return subprocess.run([sys.executable, str(CANONICAL), "check", uid], capture_output=True, text=True,
                              encoding="utf-8", env=env)

    def test_exit_codes_and_prefix_only(self):
        f = held_file([HELD])
        r = self.check(f, HELD)
        self.assertEqual(r.returncode, 3)
        self.assertIn("keep list", r.stdout)
        self.assertNotIn(HELD, r.stdout + r.stderr)
        self.assertEqual(self.check(f, FREE).returncode, 0)
        self.assertEqual(self.check(held_file(None), FREE).returncode, 2)


class FacebookDeletion(unittest.TestCase):
    def setUp(self):
        self.fb = load(ROOT / "plugins/eng/skills/delete-facebook-user-data/scripts/fb_deletion.py", "fb_deletion_t")
        self.plan = pathlib.Path(tempfile.mkdtemp()) / "plan.json"
        self.plan.write_text(json.dumps(
            [{"batch": "b1", "class": c, "uid": u} for c in ("account", "orphan") for u in (HELD, FREE)]))

    def run_cmd(self, cmd, held, execute=False):
        args = mock.Mock(plan=str(self.plan), execute=execute, backup=str(self.plan.with_name("b.ndjson")))
        out, calls = io.StringIO(), []
        with mock.patch.dict(os.environ, {"HELD_UIDS_JSON": str(held), "AURA_ADMIN_TOKEN": "t"}), \
                mock.patch.object(self.fb, "_aura", side_effect=lambda m, p, t: calls.append(p) or (204, {})), \
                mock.patch.object(self.fb, "_lookup_uids", side_effect=lambda uids: {u: {"localId": u} for u in uids}), \
                mock.patch.object(self.fb, "_idtk", side_effect=lambda m, b: calls.extend(b["localIds"]) or {}), \
                contextlib.redirect_stdout(out):
            try:
                getattr(self.fb, cmd)(args)
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), calls

    def test_a_listed_uid_never_reaches_a_delete(self):
        for cmd in ("cmd_delete_accounts", "cmd_delete_logins"):
            for execute in (False, True):
                with self.subTest(cmd=cmd, execute=execute):
                    code, out, calls = self.run_cmd(cmd, held_file([HELD]), execute)
                    self.assertEqual(code, 0)
                    self.assertIn("kept: 1", out)
                    self.assertIn(f"skip {HELD[:6]}…: keep list", out)
                    self.assertNotIn(HELD, out)
                    self.assertFalse([c for c in calls if HELD in c])

    def test_a_held_profiles_secondary_login_is_kept_too(self):
        login = "fbLoginfbLoginfbLogin0000001"
        self.plan.write_text(json.dumps([{"batch": "b1", "class": "secondary", "uid": login, "main_profile": HELD},
                                         {"batch": "b1", "class": "orphan", "uid": FREE}]))
        for execute in (False, True):
            with self.subTest(execute=execute):
                code, out, calls = self.run_cmd("cmd_delete_logins", held_file([HELD]), execute)
                self.assertEqual(code, 0)
                self.assertIn("kept: 1", out)
                self.assertNotIn(login, out)
                self.assertFalse([c for c in calls if login in c or HELD in c])

    def test_no_list_refuses(self):
        code, _, calls = self.run_cmd("cmd_delete_accounts", held_file(None), True)
        self.assertIn("REFUSED", str(code))
        self.assertEqual(calls, [])


class PreserveIncidentAccount(unittest.TestCase):
    def test_names_a_held_target_and_never_refuses(self):
        d = ROOT / "plugins/eng/skills/preserve-incident-account/scripts"
        sys.path.insert(0, str(d))
        try:
            acct = load(d / "account.py", "preserve_account_t")
        finally:
            sys.path.remove(str(d))
        with mock.patch.dict(os.environ, {"HELD_UIDS_JSON": str(held_file([HELD]))}):
            self.assertIn("keep list (a held account", acct.keep_list_line(HELD))
            self.assertIn("not listed", acct.keep_list_line(FREE))
        with mock.patch.dict(os.environ, {"HELD_UIDS_JSON": str(held_file(None))}):
            self.assertIn("not listed", acct.keep_list_line(HELD))


if __name__ == "__main__":
    unittest.main()
