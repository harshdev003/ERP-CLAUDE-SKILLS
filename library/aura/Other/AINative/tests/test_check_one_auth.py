"""eng:check-one-auth — one person against the ONE auth rules (ENG-1875).

The doc's worked examples (ENGBACK-2048, Part 1) are the fixtures: Maria (R1 clean), Sam (R3,
two accounts), John (R4, claim only), Lea (R15 case 2), Alex (R7 Apple relay). Reads go through
an in-memory fake at the Reader seam: no network. What must hold beyond the verdicts: no full
email or uid in the output, and a held person prints HELD and nothing else.
"""

import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("_check_one_auth", ROOT / "plugins/eng/skills/check-one-auth/scripts/check.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def acct(uid, email, providers, claim=None, verified=True, last=1, created=1):
    return {"localId": uid, "email": email, "emailVerified": verified, "lastLoginAt": str(last),
            "createdAt": str(created), "customAttributes": json.dumps({"userId": claim}) if claim else None,
            "providerUserInfo": [{"providerId": p, "email": e} for p, e in providers]}


class Fake(mod.Reader):
    def __init__(self, accounts=(), profiles=None, mapping=None):
        self.accounts = {a["localId"]: a for a in accounts}
        self.profiles = profiles or {}       # pid -> {email, deletedAt, role}
        self.mapping = mapping or {}         # email -> the live Profile id whose userDB email it is
        self.paths = []

    def account(self, uid):
        return self.accounts.get(uid)

    def accounts_by_email(self, email):
        e = mod.norm(email)
        return [a for a in self.accounts.values()
                if mod.norm(a.get("email")) == e or any(mod.norm(p.get("email")) == e for p in a["providerUserInfo"])]

    def rtdb(self, path):
        self.paths.append(path)
        _, _, pid, field = path.split("/")
        return (self.profiles.get(pid) or {}).get(field)

    def userdb(self, pid):
        p = self.profiles.get(pid)
        return ({k: p.get(k) for k in ("email", "deletedAt", "role")} if p else None), ""

    def profile_by_email(self, email):
        self.lookups = getattr(self, "lookups", []) + [email]
        return next((pid for em, pid in self.mapping.items() if mod.norm(em) == mod.norm(email)), None), ""


P1, P2, X = "P1aaaaaaaaaaaaaaaaaaaa", "P2bbbbbbbbbbbbbbbbbbbb", "Xccccccccccccccccccccc"


def codes(res):
    return sorted(v["code"] for v in res["verdicts"])


class VerdictTest(unittest.TestCase):
    def test_maria_clean_is_ok(self):
        f = Fake([acct(P1, "<redacted-email>", [("google.com", "<redacted-email>")])], {P1: {"email": "<redacted-email>"}},
                 {"<redacted-email>": P1})
        res = mod.check(f, email="<redacted-email>")
        self.assertEqual(codes(res), ["OK"])
        self.assertEqual(mod.check(f, uid=P1)["verdicts"][0]["code"], "OK")

    def test_sam_two_accounts_names_the_r3_profile_without_the_uid(self):
        f = Fake([acct(P1, "<redacted-email>", [("google.com", "<redacted-email>")], last=100),
                  acct(P2, "<redacted-email>", [("password", "<redacted-email>")], last=200)],
                 {P1: {"email": "<redacted-email>"}, P2: {"email": "<redacted-email>"}})
        res = mod.check(f, email="<redacted-email>")
        self.assertIn("DUPLICATE_ACCOUNTS", codes(res))
        self.assertIn("EMAIL_ON_2PLUS_PROFILES", codes(res))
        dup = next(v for v in res["verdicts"] if v["code"] == "DUPLICATE_ACCOUNTS")
        self.assertIn(f"keep Profile {P2[:6]}", dup["detail"])  # last used wins (paying isn't read)
        self.assertNotIn("OK", codes(res))

    def test_john_claim_only(self):
        f = Fake([acct(X, "<redacted-email>", [("google.com", "<redacted-email>")], claim=P1)], {P1: {"email": "<redacted-email>"}})
        self.assertIn("CLAIM_ONLY", codes(mod.check(f, email="<redacted-email>")))

    def test_lea_other_email_signin_comes_off(self):
        f = Fake([acct(P1, "<redacted-email>", [("password", "<redacted-email>"), ("apple.com", "x@<redacted-email>")])],
                 {P1: {"email": "<redacted-email>"}})
        res = mod.check(f, email="<redacted-email>")
        v = next(v for v in res["verdicts"] if v["code"] == "OTHER_EMAIL_SIGNIN")
        self.assertIn("apple.com", v["detail"])
        self.assertEqual(v["rule"], "R15 case 2, R5, R8")

    def test_alex_only_signin_differs_is_apple_relay_free(self):
        f = Fake([acct(P1, "x@<redacted-email>", [("apple.com", "x@<redacted-email>")])],
                 {P1: {"email": "<redacted-email>"}})
        v = next(v for v in mod.check(f, uid=P1)["verdicts"] if v["code"] == "ONLY_SIGNIN_EMAIL_DIFFERS")
        self.assertIn("apple-relay free", v["detail"])

    def test_the_signin_email_taken_by_another_live_profile_is_p6(self):
        f = Fake([acct(P1, "<redacted-email>", [("google.com", "<redacted-email>")]),
                  acct(P2, "<redacted-email>", [("password", "<redacted-email>")])],
                 {P1: {"email": "sam@work.com"}, P2: {"email": "<redacted-email>"}})
        v = next(v for v in mod.check(f, uid=P1)["verdicts"] if v["code"] == "ONLY_SIGNIN_EMAIL_DIFFERS")
        self.assertIn("taken-live", v["detail"])
        self.assertIn("P6", v["rule"])

    def test_empty_profile_email_states(self):
        f = Fake([acct(P1, "<redacted-email>", [("google.com", "<redacted-email>")])], {P1: {"email": ""}})
        self.assertIn("copyable", next(v for v in mod.check(f, uid=P1)["verdicts"] if v["code"] == "PROFILE_EMAIL_EMPTY")["detail"])
        f = Fake([acct(P1, "<redacted-email>", [("google.com", "<redacted-email>")]),
                  acct(P2, "<redacted-email>", [("password", "<redacted-email>")])],
                 {P1: {"email": ""}, P2: {"email": "<redacted-email>", "deletedAt": "2026-01-01"}})
        self.assertIn("deleted-only", next(v for v in mod.check(f, uid=P1)["verdicts"] if v["code"] == "PROFILE_EMAIL_EMPTY")["detail"])

    def test_a_live_profile_holding_the_email_with_no_auth_is_taken_live(self):
        # Found through UserService's email lookup alone: no Auth reaches P2.
        f = Fake([acct(P1, "<redacted-email>", [("google.com", "<redacted-email>")])],
                 {P1: {"email": ""}, P2: {"email": "<redacted-email>"}}, {"<redacted-email>": P2})
        v = next(v for v in mod.check(f, uid=P1)["verdicts"] if v["code"] == "PROFILE_EMAIL_EMPTY")
        self.assertIn("taken-live", v["detail"])

    def test_without_the_token_the_email_state_is_unchecked_never_copyable(self):
        f = Fake([acct(P1, "<redacted-email>", [("google.com", "<redacted-email>")])], {P1: {"email": ""}})
        f.profile_by_email = lambda email: (None, "AURA_API_TOKEN unset")
        v = next(v for v in mod.check(f, uid=P1)["verdicts"] if v["code"] == "PROFILE_EMAIL_EMPTY")
        self.assertIn("unchecked", v["detail"])
        self.assertIn("AURA_API_TOKEN", v["next"])

    def test_without_the_token_a_deleted_holder_is_unchecked_and_a_live_one_stays_taken_live(self):
        accts = [acct(P1, "<redacted-email>", [("google.com", "<redacted-email>")]),
                 acct(P2, "<redacted-email>", [("password", "<redacted-email>")])]
        for deleted, want in (("2026-01-01", "unchecked"), (None, "taken-live")):
            with self.subTest(deleted=deleted):
                f = Fake(accts, {P1: {"email": ""}, P2: {"email": "<redacted-email>", "deletedAt": deleted}})
                f.profile_by_email = lambda email: (None, "AURA_API_TOKEN unset")
                v = next(v for v in mod.check(f, uid=P1)["verdicts"] if v["code"] == "PROFILE_EMAIL_EMPTY")
                self.assertIn(want, v["detail"])

    def test_orphan_deleted_legacy_staff_case_untrusted_anonymous(self):
        f = Fake([acct(P1, "a@x.com", [("facebook.com", "a@x.com")])], {P1: {"email": "a@x.com", "deletedAt": "2026-01-01"}})
        self.assertTrue({"ORPHAN_ACCOUNT", "PROFILE_DELETED", "UNTRUSTED"} <= set(codes(mod.check(f, uid=P1))))
        legacy = "google:1234567890"
        f = Fake([acct(legacy, "<redacted-email>", [("google.com", "<redacted-email>")])],
                 {legacy: {"email": "<redacted-email>"}})
        self.assertTrue({"LEGACY_UID", "STAFF_OR_TEST", "CASE_VARIANT"} <= set(codes(mod.check(f, uid=legacy))))
        f = Fake([acct(P1, None, [])], {P1: {"email": ""}})
        self.assertIn("ANONYMOUS", codes(mod.check(f, uid=P1)))

    def test_account_at_the_profile_id_outside_the_group_is_p1(self):
        f = Fake([acct(X, "<redacted-email>", [("google.com", "<redacted-email>")], claim=P1), acct(P1, None, [])],
                 {P1: {"email": "<redacted-email>"}})
        self.assertIn("ACCOUNT_AT_PROFILE_ID", codes(mod.check(f, email="<redacted-email>")))


    def test_p1_from_the_profile_id_side(self):
        # --uid on the profile id whose account has no email: that account is outside the email's group.
        f = Fake([acct(X, "<redacted-email>", [("google.com", "<redacted-email>")], claim=P1), acct(P1, None, [])],
                 {P1: {"email": "<redacted-email>"}})
        got = codes(mod.check(f, uid=P1))
        self.assertIn("ACCOUNT_AT_PROFILE_ID", got)
        self.assertNotIn("DUPLICATE_ACCOUNTS", got)
        self.assertIn("ANONYMOUS", got)

    def test_legacy_ids_keep_their_provider_in_the_prefix(self):
        self.assertEqual(mod.pre("google:1234567890"), "google:123456")
        self.assertNotEqual(mod.pre("google:1111111111"), mod.pre("google:2222222222"))


class SafetyTest(unittest.TestCase):
    def test_a_held_person_prints_only_held(self):
        f = Fake([acct(P1, "<redacted-email>", [("google.com", "<redacted-email>")])], {P1: {"email": "<redacted-email>"}})
        res = mod.check(f, email="<redacted-email>", held={P1})
        self.assertEqual(mod.render(res), "HELD")
        self.assertFalse(any(p.startswith("/users/") for p in f.paths))  # held is decided before any profile read

    def test_a_held_uid_is_decided_before_any_read(self):
        f = Fake([acct(P1, None, [])], {P1: {"email": "<redacted-email>"}})
        f.account = lambda uid: self.fail("Auth read for a held uid")
        f.userdb = lambda pid: self.fail("userDB read for a held uid")
        self.assertEqual(mod.render(mod.check(f, uid=P1, held={P1})), "HELD")
        self.assertEqual(f.paths, [])

    def test_no_full_email_or_uid_in_the_output(self):
        f = Fake([acct(P1, "<redacted-email>", [("google.com", "<redacted-email>")]),
                  acct(P2, "<redacted-email>", [("password", "<redacted-email>")])],
                 {P1: {"email": "sam@work.com"}, P2: {"email": "<redacted-email>"}}, {"<redacted-email>": P2})
        out = mod.render(mod.check(f, email="<redacted-email>"))
        for secret in (P1, P2, "<redacted-email>", "sam@work.com"):
            self.assertNotIn(secret, out)
        self.assertIn("s***@g***.com", out)

    def test_only_exact_child_paths_are_read_and_never_email_user(self):
        f = Fake([acct(P1, "<redacted-email>", [("google.com", "<redacted-email>")])], {P1: {"email": "<redacted-email>"}})
        mod.check(f, email="<redacted-email>")
        self.assertTrue(f.paths)
        for p in f.paths:
            self.assertRegex(p, r"^/users/[^/]+/(email|deletedAt|role)$")
        self.assertEqual(f.lookups, ["<redacted-email>"])  # the email goes in a POST body, never a path
        src = (ROOT / "plugins/eng/skills/check-one-auth/scripts").glob("*")
        for s in src:
            if s.suffix in (".py", ".js"):
                code = s.read_text(encoding="utf-8")
                self.assertNotRegex(code, r"['\"`]/emailUser|ref\(['\"`]emailUser", s.name)

    def test_the_listing_uses_the_approved_terms(self):
        f = Fake([acct(X, "<redacted-email>", [("google.com", "<redacted-email>")], claim=P1), acct(P1, None, [])],
                 {P1: {"email": "<redacted-email>"}})
        out = mod.render(mod.check(f, uid=P1))
        for term in ("Auths (2)", "Empty Auth", "Extra Auth", "Profiles (", "Profile with this email (userDB)", "Sign-ins:"):
            self.assertIn(term, out)
        f = Fake([acct(P1, "<redacted-email>", [("google.com", "<redacted-email>")])], {P1: {"email": "<redacted-email>"}})
        self.assertIn("Main Auth", mod.render(mod.check(f, uid=P1)))

    def test_an_orphan_is_not_called_an_extra_auth(self):
        f = Fake([acct(X, "<redacted-email>", [("google.com", "<redacted-email>")])], {})
        out = mod.render(mod.check(f, uid=X))
        self.assertIn("Auth reaching no Profile", out)
        self.assertNotIn("Extra Auth", out)
        self.assertIn("ORPHAN_ACCOUNT", out)

    def test_an_auth_with_an_email_but_no_signin_is_not_an_empty_auth(self):
        f = Fake([acct(P1, "<redacted-email>", [])], {P1: {"email": "<redacted-email>"}})
        out = mod.render(mod.check(f, uid=P1))
        self.assertIn("ANONYMOUS", out)
        self.assertIn("Auth with no Sign-in", out)
        self.assertNotIn("Empty Auth", out)

    def test_a_userdb_failure_is_a_failed_read_and_never_ok(self):
        f = Fake([acct(P1, "<redacted-email>", [("google.com", "<redacted-email>")])], {P1: {"email": "<redacted-email>"}})
        f.userdb = lambda pid: (None, "AURA_API_TOKEN unset")
        got = codes(mod.check(f, uid=P1))
        self.assertNotIn("OK", got)
        self.assertIn("NOT_CHECKED", got)

        f.userdb = lambda pid: (None, "")  # a 404: read, no row; RTDB still has the Profile
        self.assertNotIn("NOT_CHECKED", codes(mod.check(f, uid=P1)))

        def boom(pid):
            raise mod.ReadFailed("UserService GET /users: HTTP 500")
        f.userdb = boom
        with self.assertRaises(mod.ReadFailed):
            mod.check(f, uid=P1)

    def test_p1_is_approved_and_p6_is_open(self):
        self.assertTrue(mod.NEXT["ACCOUNT_AT_PROFILE_ID"][1].startswith("APPROVED (P1)"))
        self.assertTrue(mod.NEXT["ONLY_SIGNIN_EMAIL_DIFFERS/taken-live"][1].startswith("OPEN (P6)"))

    def test_an_auth_email_mismatch_is_never_an_empty_verdict(self):
        f = Fake([acct(P1, "<redacted-email>", [("google.com", "<redacted-email>")])], {P1: {"email": "<redacted-email>"}})
        got = codes(mod.check(f, uid=P1))
        self.assertIn("AUTH_EMAIL_DIFFERS", got)
        self.assertNotIn("OK", got)
        self.assertNotIn("NOT_ALIGNED", got)
        f = Fake([acct(P1, "<redacted-email>", [("google.com", "<redacted-email>"), ("facebook.com", "c@x.com")])],
                 {P1: {"email": "<redacted-email>"}})
        v = next(v for v in mod.check(f, uid=P1)["verdicts"] if v["code"] == "SIGNINS_NONE_MATCH")
        self.assertIn("OPEN", v["next"])

    def test_the_catch_all_names_the_real_reason(self):
        # A second live Profile that only the userDB email lookup reaches.
        f = Fake([acct(P1, "<redacted-email>", [("google.com", "<redacted-email>")])],
                 {P1: {"email": "<redacted-email>"}, P2: {"email": "<redacted-email>"}}, {"<redacted-email>": P2})
        v = next(v for v in mod.check(f, email="<redacted-email>")["verdicts"] if v["code"] == "NOT_ALIGNED")
        self.assertEqual(v["detail"], "2 live Profiles reached")
        self.assertNotIn("for example", v["next"])

    def test_an_empty_userdb_email_wins_over_rtdb(self):
        f = Fake([acct(P1, "<redacted-email>", [("google.com", "<redacted-email>")])], {P1: {"email": "<redacted-email>"}})
        f.userdb = lambda pid: ({"email": "", "deletedAt": None, "role": None}, "")
        self.assertIn("PROFILE_EMAIL_EMPTY", codes(mod.check(f, uid=P1)))

    def test_masking(self):
        self.assertEqual(mod.mask_email("<redacted-email>"), "j***@g***.com")
        self.assertEqual(mod.mask_email(None), "(none)")
        self.assertEqual(mod.pre("abcdefghij"), "abcdef")


class RealtimeReaderTest(unittest.TestCase):
    """ENG-1882: RTDB is read over the Admin SDK realtime protocol, never REST, so a run over many people
    doesn't trip the "[Security] RTDB REST read surge" alert. Exact paths only; any failure is a failed read."""

    class Proc:
        def __init__(self, lines):
            import io
            self.stdout = io.StringIO("".join(json.dumps(x) + "\n" for x in lines))
            self.stdin = io.StringIO()

    def reader(self, lines):
        return mod.RealtimeReader(popen=lambda *a, **k: self.Proc(lines))

    def test_a_value_comes_back_for_its_own_path(self):
        r = self.reader([{"ready": True}, {"path": "/users/P1/email", "value": "a@b.com"}])
        self.assertEqual(r.get("/users/P1/email"), "a@b.com")

    def test_errors_and_mismatched_answers_are_failed_reads(self):
        for lines in ([{"ready": True}, {"path": "/users/P1/email", "error": "PERMISSION_DENIED"}],
                      [{"ready": True}, {"path": "/users/P2/email", "value": "x"}],
                      [{"ready": True}]):  # the helper stopped
            with self.assertRaises(mod.ReadFailed):
                self.reader(lines).get("/users/P1/email")
        with self.assertRaises(mod.ReadFailed):
            self.reader([{"fatal": "realtime reader: no credentials"}])

    def test_a_dead_helper_is_a_failed_read_not_a_crash(self):
        r = self.reader([{"ready": True}])

        class Broken:
            def write(self, _):
                raise BrokenPipeError

            def flush(self):
                pass
        r.proc.stdin = Broken()
        with self.assertRaises(mod.ReadFailed):
            r.get("/users/P1/email")

    def test_a_silent_helper_times_out(self):
        import os
        rd, wr = os.pipe()
        os.write(wr, (json.dumps({"ready": True}) + "\n").encode())

        class Proc:
            stdout = os.fdopen(rd)
            stdin = __import__("io").StringIO()
            killed = False

            def kill(self):
                Proc.killed = True
        r = mod.RealtimeReader(popen=lambda *a, **k: Proc())
        r.TIMEOUT = 0.2
        with self.assertRaises(mod.ReadFailed) as e:
            r.get("/users/P1/email")  # nothing is ever written back
        self.assertIn("no answer", str(e.exception))
        self.assertTrue(Proc.killed)
        os.close(wr)

    def test_only_exact_paths_reach_the_helper(self):
        r = mod.Reader()
        for bad in ("/", "/users", "/users/P1", "/users/P1/name", "/emailUser", "/emailUser/YWJj",
                    "/users/P1/email?shallow=true"):
            with self.assertRaises(mod.ReadFailed, msg=bad):
                r.rtdb(bad)
        self.assertIsNone(r._db)  # refused before any connection was opened


@unittest.skipUnless(__import__("shutil").which("node"), "node not installed")
class RealtimeHelperTest(unittest.TestCase):
    """The Node helper itself, against a stand-in firebase-admin: no network."""

    def test_the_helper_reads_exact_paths_and_refuses_the_rest(self):
        import subprocess, tempfile
        with tempfile.TemporaryDirectory() as d:
            fa = pathlib.Path(d) / "node_modules" / "firebase-admin"
            fa.mkdir(parents=True)
            (fa / "app.js").write_text("exports.initializeApp=(o,name)=>({name,delete:async()=>{}});exports.cert=(c)=>c;",
                                       encoding="utf-8")
            (fa / "database.js").write_text(
                "const data={'/users/P1/email':'a@b.com'};"
                "exports.getDatabase=()=>({ref:(p)=>({get:async()=>({exists:()=>p in data,val:()=>data[p]})})});",
                encoding="utf-8")
            env = {"PATH": __import__("os").environ["PATH"], "AURA_CLI_DIR": d,
                   "GCP_KEY": json.dumps({"type": "service_account"}), "RTDB_URL": "https://example.invalid"}
            reqs = "".join(json.dumps({"path": p}) + "\n" for p in ("/users/P1/email", "/users/P1/deletedAt", "/users", "/", "/emailUser/YWJj"))
            out = subprocess.run(["node", str(ROOT / "plugins/eng/skills/check-one-auth/scripts/rtdb_realtime.js")],
                                 input=reqs, capture_output=True, text=True, env=env, timeout=30).stdout
        lines = [json.loads(l) for l in out.splitlines()]
        self.assertEqual(lines[0], {"ready": True})
        self.assertEqual(lines[1], {"path": "/users/P1/email", "value": "a@b.com"})
        self.assertEqual(lines[2], {"path": "/users/P1/deletedAt", "value": None})
        self.assertEqual([l.get("error") for l in lines[3:]], ["path not allowed"] * 3)

    def test_without_a_named_cli_checkout_the_helper_refuses(self):
        import os, subprocess
        env = {"PATH": os.environ["PATH"], "GCP_KEY": "{}", "RTDB_URL": "https://example.invalid"}
        res = subprocess.run(["node", str(ROOT / "plugins/eng/skills/check-one-auth/scripts/rtdb_realtime.js")],
                             input="", capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(res.returncode, 2)
        self.assertIn("AURA_CLI_DIR", res.stdout)

    def test_a_tilde_key_path_is_under_the_home_folder(self):
        import os, subprocess, tempfile
        with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
            key = pathlib.Path(d) / "k.json"
            key.write_text(json.dumps({"type": "service_account"}), encoding="utf-8")
            fa = pathlib.Path(d) / "node_modules" / "firebase-admin"
            fa.mkdir(parents=True)
            (fa / "app.js").write_text("exports.initializeApp=(o,n)=>({delete:async()=>{}});exports.cert=(c)=>{"
                                       "if(c.type!=='service_account')throw new Error('bad key');return c;};",
                                       encoding="utf-8")
            (fa / "database.js").write_text("exports.getDatabase=()=>({});", encoding="utf-8")
            rel = "~/" + os.path.relpath(key, os.path.expanduser("~"))
            env = {"PATH": os.environ["PATH"], "HOME": os.path.expanduser("~"), "GCP_KEY": rel,
                   "AURA_CLI_DIR": d, "RTDB_URL": "https://example.invalid"}
            out = subprocess.run(["node", str(ROOT / "plugins/eng/skills/check-one-auth/scripts/rtdb_realtime.js")],
                                 input="", capture_output=True, text=True, env=env, timeout=30).stdout
        self.assertEqual(json.loads(out.splitlines()[0]), {"ready": True})


if __name__ == "__main__":
    unittest.main()
