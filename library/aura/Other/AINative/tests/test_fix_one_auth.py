"""eng:fix-one-auth: the E2 plan, the encrypted-directory guard and the prod write refusals.

The plan decides which Auth emails change in prod, so every branch that sends a case to Jerome instead
is pinned here, with run.py's own prod guards, which run before any worker starts.
"""
from __future__ import annotations

import argparse
import importlib.util
import pathlib
import sys
import unittest
import unittest.mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/fix-one-auth/scripts"


def _load():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("fix_one_auth_run", SCRIPTS / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run = _load()


def acct(*providers, email="a@x.com", disabled=False):
    return {"email": email, "disabled": disabled,
            "providers": [{"providerId": p, "email": e} for p, e in providers]}


class PlanE2(unittest.TestCase):
    def plan(self, accounts, held=(), lookup=lambda u: None):
        return run.plan_e2(accounts, set(held), lookup)

    def test_single_sign_in_is_kept(self):
        plan, t = self.plan({"u1": acct(("google.com", "b@x.com"))})
        self.assertEqual(plan, [{"uid": "u1", "keep": "google.com"}])
        self.assertEqual(t["planned"], 1)

    def test_password_plus_one_other_last_used_is_planned(self):
        # A password sign-in's email is the Auth email, so it moves with it and R2 holds (5BiKXd, 11-18).
        a = acct(("google.com", "b@x.com"), ("password", "a@x.com"))
        plan, _ = self.plan({"u1": a}, lookup=lambda u: "google.com")
        self.assertEqual(plan, [{"uid": "u1", "keep": "google.com"}])
        plan, t = self.plan({"u1": a}, lookup=lambda u: "password")
        self.assertEqual(plan, [])
        self.assertEqual(t["for Jerome: no clear last-used provider sign-in"], 1)
        plan, t = self.plan({"u1": a})
        self.assertEqual(t["for Jerome: no clear last-used provider sign-in"], 1)

    def test_several_sign_ins_go_to_jerome_naming_the_last_used_one(self):
        # Setting the Auth email would leave the other provider sign-in's email behind: R2 would still be broken.
        a = acct(("google.com", "b@x.com"), ("apple.com", "c@x.com"))
        plan, t = self.plan({"u1": a}, lookup=lambda u: "apple.com")
        self.assertEqual(plan, [])
        self.assertEqual(t["for Jerome: several sign-ins (last used: apple)"], 1)
        plan, t = self.plan({"u1": a})
        self.assertEqual(t["for Jerome: several sign-ins (last used: unclear)"], 1)

    def test_password_goes_to_jerome(self):
        plan, t = self.plan({"u1": acct(("password", "b@x.com"))})
        self.assertEqual(plan, [])
        self.assertEqual(t["for Jerome: no clear last-used provider sign-in"], 1)

    def test_switch_to_apple_relay_goes_to_jerome(self):
        plan, t = self.plan({"u1": acct(("apple.com", "zz@<redacted-email>"))})
        self.assertEqual(plan, [])
        self.assertEqual(t["for Jerome: switched to Apple relay"], 1)

    def test_disabled_held_and_gone(self):
        accounts = {"d": acct(("google.com", "b@x.com"), disabled=True), "h": acct(("google.com", "b@x.com")), "g": None}
        plan, t = self.plan(accounts, held={"h"})
        self.assertEqual(plan, [])
        self.assertEqual((t["for Jerome: disabled"], t["held or staff (check with SecurityAuth)"], t["gone"]), (1, 1, 1))

    def test_a_held_account_is_never_looked_up(self):
        seen = []
        self.plan({"h": acct(("google.com", "b@x.com"), ("apple.com", "c@x.com"))}, held={"h"}, lookup=seen.append)
        self.assertEqual(seen, [])


class Batches(unittest.TestCase):
    def setUp(self):
        import os, tempfile
        self.d = pathlib.Path(tempfile.mkdtemp())
        self.keep = self.d / "held.json"
        self.keep.write_text('["HeldUid0001"]')
        os.chmod(self.keep, 0o600)
        self.env = unittest.mock.patch.dict(os.environ, {"HELD_UIDS_JSON": str(self.keep)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_the_next_batch_moves_past_handled_uids_and_skips_held(self):
        uids = ["a1", "HeldUid0001", "b2", "c3", "d4"]
        self.assertEqual(run.next_batch(uids, self.d, "att.txt", 2, apply=True), ["a1", "b2"])
        (self.d / "att.txt").write_text("a1\nb2\n")
        self.assertEqual(run.next_batch(uids, self.d, "att.txt", 2, apply=True), ["c3", "d4"])

    def test_a_write_refuses_an_untrusted_keep_list(self):
        import os
        os.chmod(self.keep, 0o644)
        with self.assertRaises(SystemExit):
            run.next_batch(["a1"], self.d, "att.txt", 5, apply=True)
        self.keep.write_text("[]")
        os.chmod(self.keep, 0o600)
        with self.assertRaises(SystemExit):
            run.next_batch(["a1"], self.d, "att.txt", 5, apply=True)

    def test_prefixes_keep_the_provider(self):
        self.assertEqual(run.p6("google:1234567890"), "google:12345…")
        self.assertNotIn("abcd", run.p6("abcd"))


class Guards(unittest.TestCase):
    def test_prod_dir_must_be_on_an_encrypted_image(self):
        with self.assertRaises(SystemExit):
            run.require_safe_dir("prod", pathlib.Path("/tmp/x"), mounts={"/Volumes/Enc"})
        run.require_safe_dir("prod", pathlib.Path("/Volumes/Enc/daily"), mounts={"/Volumes/Enc"})
        run.require_safe_dir("dev", pathlib.Path("/tmp/x"), mounts=set())

    def test_a_sibling_mount_name_does_not_count(self):
        with self.assertRaises(SystemExit):
            run.require_safe_dir("prod", pathlib.Path("/Volumes/EncOther/daily"), mounts={"/Volumes/Enc"})

    def test_prod_apply_needs_the_window(self):
        ns = lambda **k: argparse.Namespace(**{"env": "prod", "apply": True, "write_as": None, "window_end": None, **k})
        with self.assertRaises(SystemExit):
            run.write_flags(ns())
        with self.assertRaises(SystemExit):
            run.write_flags(ns(write_as="<redacted-email>"))
        self.assertEqual(run.write_flags(ns(apply=False)), [])
        flags = run.write_flags(ns(write_as="<redacted-email>", window_end="2026-11-18T20:00:00Z"))
        self.assertEqual(flags[0], "--apply")


class Workers(unittest.TestCase):
    def test_every_identity_toolkit_call_sends_the_label(self):
        src = (SCRIPTS / "one_auth.js").read_text()
        self.assertIn("'User-Agent': userAgent", src)
        self.assertNotIn("updateUser(", "".join((SCRIPTS / f).read_text() for f in
                                                ("fix_e2.js", "fix_top_email.js", "one_auth.js")))

    def test_concurrency_is_capped_at_five(self):
        self.assertIn("Math.min(5,", (SCRIPTS / "one_auth.js").read_text())

    def test_no_write_starts_after_a_stop(self):
        src = (SCRIPTS / "fix_top_email.js").read_text()
        self.assertLess(src.index("if (stop) { inc('not run (stopped)'); return 'not run'; }"), src.index("await c.setAuthEmail("))
        # A uid that was not run stays for the next batch.
        self.assertIn("if (c.apply && outcome !== 'not run') c.record(c.arg('attempted'), u);", src)


if __name__ == "__main__":
    unittest.main()
