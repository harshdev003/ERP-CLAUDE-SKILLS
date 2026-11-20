"""eng:preserve-incident-account (ENG-1809): one account's data kept, then the account contained.

What is pinned is what the two runs by hand (ENGBACK-2292, ENGBACK-2311) showed matters:
- the order: logs, then every snapshot, then the verify, then containment, which comes last;
- nothing in a plan deletes anything, and containment disables, never deletes;
- one paste line per owner (a relayed go was refused by every owner), naming the email file, never
  the email;
- the live Auth state is read, not assumed, and the account's ids stay in mode-600 files: printed
  output carries the uid prefix only;
- verify lists each object by itself, fails on a missing object, a size mismatch, a path outside the
  bucket or a store with no entry, and containment is refused until a verify passed.
gcloud and HTTP are stubbed at their one call each; nothing here reaches GCP.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import re
import stat
import subprocess
import tempfile
import unittest
import unittest.mock

SCRIPT = (pathlib.Path(__file__).resolve().parents[1]
          / "plugins/eng/skills/preserve-incident-account/scripts/account.py")
spec = importlib.util.spec_from_file_location("preserve_incident_account", SCRIPT)
account = importlib.util.module_from_spec(spec)
spec.loader.exec_module(account)

UID = "aB3dE5fG7hI9jK1lM3nO5pQ7rS9t"
UUID = "11111111-2222-4333-8444-555555555555"
EMAIL = "someone@example.com"
BUCKET = "hold-bucket-test"


def private_tmp():
    d = tempfile.mkdtemp()
    os.chmod(d, 0o700)
    return d


def write_account(d, **over):
    acct = {"uid": UID, "uidPrefix": UID[:6], "uuid": UUID, "referralCode": None,
            "emailFile": os.path.join(d, "email.txt"), "auth": {"exists": True, "disabled": False},
            "logRetentionDays": {"firebase-auratech16": 90}}
    acct.update(over)
    with open(os.path.join(d, "account.json"), "w") as f:
        json.dump(acct, f)
    return acct


def run_main(argv, run=None, opener=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = account.main(argv, run, opener)
    return code, out.getvalue(), err.getvalue()


def make_plan(d, *extra):
    write_account(d)
    code, out, _ = run_main(["plan", "--dir", d, "--go-id", "42", "--bucket", BUCKET, "--card", "TEST-1",
                             "--owners", os.path.join(d, "none.json"), *extra])
    assert code == 0
    with open(os.path.join(d, "plan.json")) as f:
        return json.load(f), out


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.d = private_tmp()
        self.plan, self.out = make_plan(self.d, "--ips-file", "/x/ips.txt")

    def test_phases_run_logs_then_snapshots_then_confirm_then_contain(self):
        order = [account.PHASES.index(r["phase"]) for r in self.plan["rows"]]
        self.assertEqual(order, sorted(order))
        contain = [r for r in self.plan["rows"] if r["phase"] == "contain"]
        # Firebase disables and revokes first; the token rows and cache keys go last (ENGBACK-2320).
        self.assertEqual([r["owner"] for r in contain], ["firebase", "service-db"])
        self.assertEqual(self.plan["rows"][-2:], contain)

    def test_every_store_is_planned(self):
        stores = " ".join(r["store"] for r in self.plan["rows"])
        for s in ("logs by IP", "logs by id", "Auth record", "RTDB", "Storage", "userDB", "paymentDB",
                  "intelligenceDB", "contentDB", "BigQuery and Mixpanel", "Bugsnag", "Iterable", "Stripe"):
            self.assertIn(s, stores)

    def test_no_ips_file_means_no_ip_row(self):
        plan, _ = make_plan(private_tmp())
        self.assertNotIn("logs by IP", [r["store"] for r in plan["rows"]])

    def test_nothing_but_token_rows_and_cache_keys_is_deleted(self):
        disable, tokens = self.plan["contain"]
        text = " ".join([g["go"] for g in self.plan["gos"]] + [disable])
        rest = re.sub(r"(?i)\bno delete\b|\bdeletes nothing\b|\bnever delete\b", "", text)
        self.assertNotRegex(rest.lower(), r"delet|drop |truncate|remove")
        self.assertIn("disables (no delete)", disable)
        # The one delete: the account's token rows (exported first) and their cache keys. Never the account.
        self.assertIn('in one transaction, exports to the hold any userDB "token" row', tokens)
        self.assertIn('then deletes all of that account\'s "token" rows with RETURNING id', tokens)
        # The cache keys come from what the delete returned: a token minted after the snapshot is cached too.
        self.assertIn("for every id that delete returned", tokens)
        self.assertNotRegex(tokens.lower(), r"delet\w* (the )?(auth|account|user row|/users)")

    def test_plan_output_carries_the_label(self):
        self.assertTrue(self.out.startswith("**Draft — Privileged & Confidential** — "), self.out[:80])
        self.assertIn("reflects information currently known", self.out.split("\n")[0])

    def test_containment_is_not_a_paste_line_until_verify(self):
        self.assertNotIn("disables", " ".join(g["go"] for g in self.plan["gos"]))
        self.assertNotIn("disables", self.out)

    def test_one_paste_line_per_owner(self):
        owners = [g["owner"] for g in self.plan["gos"]]
        self.assertEqual(len(owners), len(set(owners)))
        self.assertEqual(set(owners), {r["owner"] for r in self.plan["rows"] if r["phase"] in ("logs", "snapshot")})
        for g in self.plan["gos"]:
            self.assertTrue(g["go"].startswith("go 42: "), g["go"])
            self.assertTrue(g["go"].endswith("; counts only"), g["go"])

    def test_go_lines_name_the_email_file_never_the_email(self):
        text = json.dumps(self.plan) + self.out
        self.assertNotIn(EMAIL, text)
        self.assertNotIn("@", " ".join(g["go"] for g in self.plan["gos"]))
        self.assertIn(os.path.join(self.d, "email.txt"), self.plan["gos"][0]["go"])

    def test_owner_names_come_from_the_personal_file(self):
        d = private_tmp()
        with open(os.path.join(d, "owners.json"), "w") as f:
            json.dump({"firebase": "Fire", "nonsense": "x"}, f)
        write_account(d)
        run_main(["plan", "--dir", d, "--go-id", "7", "--bucket", BUCKET, "--owners", os.path.join(d, "owners.json")])
        with open(os.path.join(d, "plan.json")) as f:
            plan = json.load(f)
        fire = next(g for g in plan["gos"] if g["owner"] == "firebase")
        self.assertTrue(fire["go"].startswith("go 7: Fire "))
        self.assertTrue(plan["contain"][0].startswith("go 7: Fire disables"))
        self.assertNotIn("nonsense", json.dumps(plan))

    def test_object_folders_carry_the_prefix_never_the_full_uid(self):
        for r in self.plan["rows"]:
            self.assertNotIn(UID, r["folder"])
        self.assertTrue(any(UID[:6] in r["folder"] for r in self.plan["rows"]))

    def test_plan_is_private(self):
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.d, "plan.json")).st_mode), 0o600)

    def test_open_folder_is_refused(self):
        d = tempfile.mkdtemp()
        os.chmod(d, 0o755)
        write_account(d)
        code, _, err = run_main(["plan", "--dir", d, "--go-id", "1"])
        self.assertEqual(code, 2)
        self.assertIn("group or others", err)


class Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ResolveTest(unittest.TestCase):
    def resolve(self, users, retention="90"):
        d = private_tmp()
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if argv[:2] == ["auth", "print-access-token"]:
                return Proc("token\n")
            if argv[:2] == ["logging", "buckets"]:
                return Proc(retention + "\n")
            return Proc(returncode=1)

        def opener(req, timeout=None):
            body = json.loads(req.data or b"{}")
            if "localId" in body:
                return Resp(json.dumps({"users": users}).encode())
            return Resp(json.dumps({"users": users[:1]}).encode())
        env = {k: v for k, v in os.environ.items() if k not in ("AURA_API_TOKEN", "AURA_ADMIN_TOKEN")}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            code, out, err = run_main(["resolve", "--uid", UID, "--dir", d, "--uuid", UUID], run, opener)
        return d, code, out, err

    def test_live_state_is_read_and_ids_stay_in_private_files(self):
        user = {"localId": UID, "email": EMAIL, "disabled": False, "createdAt": "1759305176000",
                "lastLoginAt": "1761820620000", "validSince": "1759305176", "passwordHash": "secret"}
        d, code, out, _ = self.resolve([user])
        self.assertEqual(code, 0)
        self.assertIn("Auth exists, enabled", out)
        self.assertNotIn(UID, out)
        self.assertNotIn(EMAIL, out)
        self.assertIn(UID[:6], out)
        for name in ("account.json", "email.txt"):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, name)).st_mode), 0o600)
        with open(os.path.join(d, "account.json")) as f:
            acct = json.load(f)
        self.assertNotIn("secret", json.dumps(acct))
        self.assertEqual(acct["accountsSharingEmail"], 1)
        self.assertEqual(acct["logRetentionDays"]["firebase-auratech16"], 90)

    def test_disabled_account_is_reported_as_read(self):
        _, _, out, _ = self.resolve([{"localId": UID, "disabled": True}])
        self.assertIn("DISABLED", out)

    def test_missing_account_is_said_plainly(self):
        d, code, out, _ = self.resolve([])
        self.assertEqual(code, 0)
        self.assertIn("NOT FOUND", out)
        self.assertFalse(os.path.exists(os.path.join(d, "email.txt")))

    def test_bad_uid_is_refused(self):
        code, _, err = run_main(["resolve", "--uid", "../../etc", "--dir", private_tmp()])
        self.assertEqual(code, 2)


class AdminApiTest(unittest.TestCase):
    """A failed admin API call is said as a failure, never read as an account without a uuid."""

    def call(self, exc):
        def opener(req, timeout=None):
            raise exc
        with unittest.mock.patch.dict(os.environ, {"AURA_API_TOKEN": "t"}):
            return account.aura_user(UID, opener)

    def test_404_is_no_row(self):
        import urllib.error
        self.assertEqual(self.call(urllib.error.HTTPError("u", 404, "nf", {}, None)), ({}, "no userDB row (404)"))

    def test_auth_and_server_errors_are_failures(self):
        import urllib.error
        for code in (401, 403, 503):
            row, note = self.call(urllib.error.HTTPError("u", code, "x", {}, None))
            self.assertEqual((row, note), ({}, f"admin API failed (HTTP {code})"))
        self.assertIn("failed", self.call(urllib.error.URLError("down"))[1])


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self.d = private_tmp()
        self.plan, _ = make_plan(self.d)
        self.sizes = {}
        self.calls = []

    def run_ls(self, argv, **kw):
        self.calls.append(argv)
        path = argv[-1]
        if path in self.sizes:
            return Proc(f"{self.sizes[path]:>10}  2026-11-05T10:00:00Z  {path}\nTOTAL: 1 objects\n")
        return Proc(returncode=1, stderr="One or more URLs matched no objects.")

    def full_manifest(self):
        rows = []
        for r in self.plan["rows"]:
            if r["verify"] and r["phase"] in ("logs", "snapshot"):
                path = r["folder"] + ("x.json" if r["folder"].endswith("/") else "user-x.json")
                self.sizes[path] = 100
                rows.append(f"{path}\t100\tAAAAAA==\t1")
        rows.append("\t0\t\t0\tllm_call")
        return rows

    def verify(self, rows):
        m = os.path.join(self.d, "m.tsv")
        with open(m, "w") as f:
            f.write("\n".join(rows) + "\n")
        return run_main(["verify", "--dir", self.d, "--manifest", m], self.run_ls)

    def contain(self):
        return run_main(["contain", "--dir", self.d])

    def test_contain_is_refused_before_any_verify(self):
        code, out, err = self.contain()
        self.assertEqual(code, 2)
        self.assertIn("verify first", err)
        self.assertNotIn("disables", out)

    def test_full_match_passes_and_unlocks_containment(self):
        code, out, _ = self.verify(self.full_manifest())
        self.assertEqual(code, 0, out)
        self.assertIn("PASSED", out)
        self.assertIn("1 zero(s)", out)
        code, out, _ = self.contain()
        self.assertEqual(code, 0)
        self.assertIn("disables (no delete)", out)

    def test_token_step_is_held_until_the_token_export_is_verified(self):
        self.verify(self.full_manifest())
        code, out, _ = self.contain()
        self.assertEqual(code, 0)
        self.assertIn("HELD", out)
        self.assertNotIn('"token" rows', out)

    def test_token_step_follows_a_verified_token_export(self):
        rows = self.full_manifest()
        tok = f"gs://{BUCKET}/incident-accessed/userDB-token-{UID[:6]}-20261105.json"
        self.sizes[tok] = 50
        code, out, _ = self.verify(rows + [f"{tok}\t50\tAAAA==\t8"])
        self.assertEqual(code, 0, out)
        code, out, _ = self.contain()
        disable, tokens = out.strip().split("\n")
        self.assertIn("disables (no delete)", disable)
        self.assertIn('deletes all of that account\'s "token" rows', tokens)
        self.assertIn("@aura/user-service:<major.minor>/userService/verifyToken/<id>/withProfile/true", tokens)
        # Session-cookie keys are sha256 hashes since UserService v2.91.17, never the raw cookie.
        self.assertIn("userService/sessionCookie/sha256:*", tokens)
        self.assertNotIn("raw", tokens)
        self.assertIn("sessionCookie", tokens)
        self.assertNotIn("HELD", out)

    def test_a_mismatched_token_export_keeps_the_token_step_held(self):
        rows = self.full_manifest()
        tok = f"gs://{BUCKET}/incident-accessed/userDB-token-{UID[:6]}-20261105.json"
        self.sizes[tok] = 49
        self.verify(rows + [f"{tok}\t50\tAAAA==\t8"])
        self.assertEqual(self.contain()[0], 2)  # the whole verify failed

    def test_each_object_is_listed_by_itself(self):
        self.verify(self.full_manifest())
        for argv in self.calls:
            self.assertEqual(argv[:3], ["storage", "ls", "-l"])
            self.assertEqual(sum(a.startswith("gs://") for a in argv), 1)

    def test_size_mismatch_fails_and_keeps_containment_locked(self):
        rows = self.full_manifest()
        self.sizes[rows[0].split("\t")[0]] = 99
        code, out, _ = self.verify(rows)
        self.assertEqual(code, 1)
        self.assertIn("listed 99 B, manifest 100 B", out)
        self.assertEqual(self.contain()[0], 2)

    def test_object_not_listed_fails(self):
        rows = self.full_manifest()
        del self.sizes[rows[1].split("\t")[0]]
        code, out, _ = self.verify(rows)
        self.assertEqual(code, 1)
        self.assertIn("not listed", out)

    def test_store_with_no_entry_fails(self):
        rows = [r for r in self.full_manifest() if "contentDB-" not in r]
        code, out, _ = self.verify(rows)
        self.assertEqual(code, 1)
        self.assertIn("MISSING", out)
        self.assertIn("contentDB-", out)

    def test_path_outside_the_bucket_fails(self):
        rows = self.full_manifest() + ["gs://other-bucket/x.json\t10\tAA==\t1"]
        code, out, _ = self.verify(rows)
        self.assertEqual(code, 1)
        self.assertIn("outside the hold bucket", out)

    def test_bucket_name_is_masked_in_output(self):
        rows = self.full_manifest()
        self.sizes[rows[0].split("\t")[0]] = 1
        _, out, _ = self.verify(rows)
        self.assertNotIn(BUCKET, out)


EXPORT = SCRIPT.with_name("export_analytics.py")
espec = importlib.util.spec_from_file_location("preserve_incident_export", EXPORT)
export_analytics = importlib.util.module_from_spec(espec)
espec.loader.exec_module(export_analytics)


class ExportSqlTest(unittest.TestCase):
    """The query shapes the run proved: params never inlined, partitions filtered, cutoff applied."""

    def ctx(self, until=None):
        acct = {"uid": UID, "uidPrefix": UID[:6], "uuid": UUID, "referralCode": "aura-xyz", "bucket": BUCKET}
        return export_analytics.Ctx(private_tmp(), acct, EMAIL, until, False)

    def test_ids_are_query_parameters_never_inlined(self):
        c = self.ctx()
        sql = export_analytics.raw_events_sql(c) + export_analytics.table_sql(
            c, "aura-main.llm.llm_call", {"cols": ["userId"], "part": ("createdAt", "TIMESTAMP")})
        for v in (UID, UUID, EMAIL, "aura-xyz"):
            self.assertNotIn(v, sql)
        self.assertEqual(c.params["email"], EMAIL)

    def test_raw_events_match_every_id_kind(self):
        sql = export_analytics.raw_events_sql(self.ctx())
        for f in export_analytics.RAW_FIELDS + export_analytics.RAW_ARRAYS + ["$.email", "$.uuid", "@ref"]:
            self.assertIn(f, sql)

    def test_partitioned_table_gets_a_partition_filter(self):
        c = self.ctx()
        sql = export_analytics.table_sql(c, "aura-main.llm.llm_call",
                                         {"cols": ["userId"], "part": ("createdAt", "TIMESTAMP")})
        self.assertIn("`createdAt` >= TIMESTAMP('1970-01-01')", sql)
        sql = export_analytics.table_sql(c, "aura-main.x.y", {"cols": ["user_id"], "part": ("date", "DATE")})
        self.assertIn("`date` >= DATE('1970-01-01')", sql)

    def test_cutoff_applies(self):
        c = self.ctx("2026-11-05T12:00:00Z")
        self.assertIn("createdAt <= TIMESTAMP('2026-11-05T12:00:00Z')", export_analytics.raw_events_sql(c))
        sql = export_analytics.table_sql(c, "aura-main.x.y", {"cols": ["user_id"], "part": ("date", "DATE")})
        self.assertIn("<= DATE('2026-11-05')", sql)

    def test_user_id_columns_are_recognised(self):
        for col in ("userId", "user_id", "uid", "followingUserId", "UserID"):
            self.assertTrue(export_analytics.USER_COL.search(col), col)
        for col in ("userIdentity", "uuid", "guid", "id", "fluid"):
            self.assertFalse(export_analytics.USER_COL.search(col), col)

    def test_zero_rows_write_no_object_but_a_zero_row(self):
        c = self.ctx()
        c.apply = True
        with unittest.mock.patch.object(export_analytics, "query", return_value=([{"n": 0}], None)) as q:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(export_analytics.export(c, "llm/llm_call", "SELECT 1"), 0)
        self.assertEqual(q.call_count, 1)  # the count only: no EXPORT DATA
        with open(os.path.join(c.d, "manifest-analytics.tsv")) as f:
            self.assertEqual(f.read(), "\t0\t\t0\tllm/llm_call\n")

    def test_unresolved_ids_add_no_match(self):
        # An unresolved referral code is a sentinel: a substring match on it would export strangers' events.
        acct = {"uid": UID, "uidPrefix": UID[:6], "uuid": None, "referralCode": None, "bucket": BUCKET}
        c = export_analytics.Ctx(private_tmp(), acct, "", None, False)
        sql = export_analytics.raw_events_sql(c)
        for term in ("@ref", "@email", "@uuid"):
            self.assertNotIn(term, sql)
        self.assertNotIn("@email", export_analytics.mp_cond(c, {"email", "uuid", "distinct_id"}))

    def test_export_dates_are_in_the_project_timezone(self):
        # 2026-11-06 03:00Z is still 11-05 in Pacific time: a to_date of 11-06 is a 400.
        self.assertEqual(str(export_analytics.project_date("2026-11-06T03:00:00Z")), "2026-11-05")
        self.assertEqual(str(export_analytics.project_date("2026-11-05T12:00:00Z")), "2026-11-05")

    def test_counts_only_run_exports_nothing(self):
        c = self.ctx()
        with unittest.mock.patch.object(export_analytics, "query", return_value=([{"n": 5}], None)) as q:
            with contextlib.redirect_stdout(io.StringIO()):
                export_analytics.export(c, "firebase_raw_events", "SELECT 1")
        self.assertEqual(q.call_count, 1)
        self.assertFalse(os.path.exists(os.path.join(c.d, "manifest-analytics.tsv")))


class CliTest(unittest.TestCase):
    def test_help_runs(self):
        r = subprocess.run(["python3", str(SCRIPT), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("resolve", r.stdout)


if __name__ == "__main__":
    unittest.main()
