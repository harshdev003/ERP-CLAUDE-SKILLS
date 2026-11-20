"""eng:preserve-prod-logs (ENG-1747): a legal-hold copy that plans first and runs one approved step at a time.

What is pinned is what keeps a hold safe and complete, from the first hold's run (ENGBACK-2176):
- one GCS bucket per source (`logging copy` has no path prefix), named with the hold's id;
- one active copy per project, so a project's copies wait for each other;
- the named members are granted BEFORE the hold project's legacy access is removed (the other order
  locked everyone out), and the temporary writers are removed after the verify;
- nothing changes log retention, nothing locks a bucket, and the retention is exactly 400 days;
- a uid never appears in printed output; a focus copy's filter is masked;
- a verify with an empty bucket or a mismatched sample hour is never a pass;
- Auth records are written without password hashes or salts.
gcloud and HTTP are stubbed at their one call each; nothing here reaches GCP.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "plugins/eng/skills/preserve-prod-logs/scripts"
sys.path.insert(0, str(SCRIPTS))
import plan_hold  # noqa: E402
import run_step  # noqa: E402

UIDS = ["aB3dE5fG7hI9jK1lM3nO5pQ7rS9t", "zY8xW6vU4tS2rQ0pO8nM6lK4jI2h"]
BASE = ["--project", "firebase-auratech16", "--start", "2026-09-20T00:00Z", "--end", "2026-10-08T16:05Z",
        "--card", "PROD-6926", "--member", "user:<redacted-email>", "--copy-as", "sa",
        "--create-as", "sa"]


def make_plan(*extra, uids=False):
    d = pathlib.Path(tempfile.mkdtemp())
    args = [*BASE, *extra, "--out", str(d / "plan.json")]
    if uids:
        (d / "uids.txt").write_text("\n".join(UIDS) + "\n", encoding="utf-8")
        args += ["--uids-file", str(d / "uids.txt")]
    with contextlib.redirect_stdout(io.StringIO()) as out:
        plan_hold.main(args)
    return d, json.loads((d / "plan.json").read_text(encoding="utf-8")), out.getvalue()


class Plan(unittest.TestCase):
    def setUp(self):
        self.d, self.p, self.out = make_plan("--analytics-dataset", "analytics_153603506", "--metrics")
        self.steps = {s["id"]: s for s in self.p["steps"]}

    def of(self, kind):
        return [s for s in self.p["steps"] if s["kind"] == kind]

    def test_one_bucket_per_source_named_with_the_hold(self):
        names = [s["bucket"] for s in self.of("copy")]
        self.assertEqual(names, ["aura-legal-hold-logs-fa16-default-prod-6926",
                                 "aura-legal-hold-logs-fa16-required-prod-6926"])
        for s in self.of("create"):
            self.assertRegex(s["argv"][3], r"^gs://[a-z0-9-]{3,63}$")

    def test_bucket_settings(self):
        create = self.of("create")[0]["argv"]
        for flag in ("--uniform-bucket-level-access", "--public-access-prevention"):
            self.assertIn(flag, create)
        self.assertEqual(create[create.index("--default-storage-class") + 1], "COLDLINE")
        self.assertEqual(create[create.index("--retention-period") + 1], "34560000s")
        self.assertEqual(create[create.index("--project") + 1], "aura-main")
        self.assertEqual(self.of("create")[1]["argv"][-1], "--versioning")

    def test_never_changes_retention_or_locks(self):
        argvs = [x for s in self.p["steps"] for a in (s.get("argvs") or [s.get("argv") or []]) for x in a]
        self.assertNotIn("--lock-retention-period", argvs)
        self.assertFalse(any(re.search(r"lock", x, re.I) for x in argvs))
        self.assertFalse(any(s["argv"] and s["argv"][:3] == ["logging", "buckets", "update"] for s in self.p["steps"]))

    def test_a_projects_copies_queue_behind_each_other(self):
        first, second = self.of("copy")
        self.assertIn(first["id"], second["after"])

    def test_legacy_access_is_removed_last_and_an_admin_keeps_iam(self):
        # ENGBACK-2176: removing it before anyone could change the bucket's IAM locked everyone out.
        for r in self.of("restrict"):
            self.assertEqual({self.steps[x]["kind"] for x in r["after"]}, {"revoke"})
            self.assertEqual(len(r["argvs"]), 6)
            self.assertTrue(all(":aura-main" in a[4] for a in r["argvs"]))
            bucket = r["argvs"][0][3]
            admin = [s for s in self.of("grant") if s["argv"][3] == bucket and "roles/storage.admin" in s["argv"][-1]]
            self.assertEqual(admin[0]["argv"][4], "--member=user:<redacted-email>")

    def test_every_bucket_ends_with_a_person_re_reading_its_iam(self):
        # ENGBACK-2176: the legacy bindings came back after the first removal.
        restricts = self.of("restrict")
        checks = self.of("check-iam")
        self.assertEqual(len(checks), len(restricts))
        for c in checks:
            (r,) = [self.steps[x] for x in c["after"]]
            self.assertEqual(r["kind"], "restrict")
            self.assertEqual(c["runs_as"], "person")
            self.assertEqual(c["argv"][:3], ["storage", "buckets", "get-iam-policy"])
            self.assertEqual(c["expect"]["members"], ["user:<redacted-email>"])
            self.assertIn("projectViewer:", c["expect"]["absent"])
            self.assertIn("roles/storage.objectCreator", c["expect"]["absent"])
        self.assertTrue(all(not any(c["id"] in s["after"] for s in self.p["steps"]) for c in checks))  # last

    def test_temporary_writers_are_removed_after_the_verify_and_are_person_steps(self):
        for v in self.of("verify"):
            revokes = [s for s in self.of("revoke") if v["id"] in s["after"]]
            self.assertEqual(len(revokes), 2)
            caller = [g for g in self.of("grant-temp") if "caller" in g["desc"] and g["id"] in self.steps[v["after"][0]]["after"]]
            self.assertEqual(sorted(a[-1] for a in caller[0]["argvs"]),
                             ["--role=roles/storage.objectCreator", "--role=roles/storage.objectViewer"])
        self.assertTrue(all(s["runs_as"] == "person" for s in self.p["steps"]
                            if s["kind"] in ("grant", "grant-temp", "restrict", "revoke")))
        agent = [s for s in self.of("grant-temp") if "Logging" in s["desc"]][0]
        self.assertIn("--member=serviceAccount:<redacted-email>", agent["argv"])

    def test_exports_get_a_temporary_writer_too(self):
        for kind in ("analytics", "metrics"):
            (s,) = self.of(kind)
            self.assertTrue(any(self.steps[x]["kind"] == "grant-temp" for x in s["after"]))
            self.assertTrue(any(s["id"] in r["after"] for r in self.of("revoke")))
        self.assertEqual(len(self.of("analytics")[0]["days"]), 19)

    def test_no_focus_without_uids(self):
        self.assertEqual([s for s in self.p["steps"] if s.get("scope") == "focus" or s["kind"] == "auth"], [])
        self.assertEqual(self.of("verify")[0]["sample_hour"], "2026-10-08T15")  # the last full hour


class Uids(unittest.TestCase):
    def test_a_focus_bundle_never_prints_a_uid(self):
        d, p, out = make_plan(uids=True)
        self.assertEqual(stat.S_IMODE(os.stat(d / "plan.json").st_mode), 0o600)
        focus = [s for s in p["steps"] if s.get("scope") == "focus" and s["kind"] == "copy"]
        self.assertEqual(len(focus), 2)
        self.assertIn(UIDS[0], " ".join(focus[0]["argv"]))  # the filter, in the private plan file
        self.assertEqual(len([s for s in p["steps"] if s["kind"] == "auth"]), 1)
        for u in UIDS:
            self.assertNotIn(u, out)
        st = {s["id"]: {"status": "done"} for s in p["steps"]}
        st[focus[0]["id"]] = {}
        (d / "plan.json.state.json").write_text(json.dumps(st), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as shown:
            run_step.main(["--plan", str(d / "plan.json"), "--step", focus[0]["id"]], run=fake())
        for u in UIDS:
            self.assertNotIn(u, shown.getvalue())
        self.assertIn("<window AND the focus accounts>", shown.getvalue())

    def test_a_failed_focus_call_never_echoes_its_filter(self):
        d, p, _ = make_plan(uids=True)
        copy = [s for s in p["steps"] if s.get("scope") == "focus" and s["kind"] == "copy"][0]
        st = {x: {"status": "done"} for x in copy["after"]}
        (d / "plan.json.state.json").write_text(json.dumps(st), encoding="utf-8")

        def echo(argv, **kw):
            if argv[:2] == ["logging", "copy"]:
                return subprocess.CompletedProcess(argv, 1, "", "INVALID_ARGUMENT: filter " + " ".join(argv))
            return fake()(argv, **kw)
        with self.assertRaises(run_step.Refused) as c, contextlib.redirect_stdout(io.StringIO()):
            run_step.main(["--plan", str(d / "plan.json"), "--step", copy["id"], "--apply"], run=echo)
        for u in UIDS:
            self.assertNotIn(u, str(c.exception))
        self.assertIn("details withheld", str(c.exception))

    def test_provider_prefixed_ids_and_emails_are_accepted(self):
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "u.txt").write_text("apple:001234.abcd.5678\nsomeone+x@example.com\n", encoding="utf-8")
        self.assertEqual(len(plan_hold.read_uids(str(d / "u.txt"))), 2)

    def test_a_bad_uids_file_is_refused(self):
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "u.txt").write_text("ok_uid\nnot a uid\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            plan_hold.main([*BASE, "--uids-file", str(d / "u.txt")])


def fake(answers=None, calls=None):
    """gcloud_sa.run stand-in: answers keyed by the first matching argv prefix."""
    answers = {("config", "get-value", "account"): "<redacted-email>\n",
               ("auth", "print-access-token"): "tok\n", **(answers or {})}

    def run(argv, **kw):
        if calls is not None:
            calls.append(argv)
        for k, v in answers.items():
            if tuple(argv[:len(k)]) == k:
                code, out = (v if isinstance(v, tuple) else (0, v))
                return subprocess.CompletedProcess(argv, code, out, "denied")
        return subprocess.CompletedProcess(argv, 0, "", "")
    return run


class Steps(unittest.TestCase):
    def setUp(self):
        self.d, self.p, _ = make_plan("--metrics")
        self.plan = str(self.d / "plan.json")
        self.ids = {s["kind"] + str(i): s["id"] for i, s in enumerate(self.p["steps"])}
        self.by_kind = lambda k: [s for s in self.p["steps"] if s["kind"] == k]

    def call(self, *argv, run=None, opener=None):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            try:
                code = run_step.main(["--plan", self.plan, *argv], run=run or fake(), opener=opener)
            except run_step.Refused as e:
                return "REFUSED", str(e)
        return code, out.getvalue()

    def state(self):
        return json.loads((self.d / "plan.json.state.json").read_text(encoding="utf-8"))

    def done(self, *ids):
        st = self.state() if (self.d / "plan.json.state.json").exists() else {}
        st.update({i: {"status": "done"} for i in ids})
        (self.d / "plan.json.state.json").write_text(json.dumps(st), encoding="utf-8")

    def test_a_step_waits_for_its_dependencies(self):
        code, msg = self.call("--step", "02", "--apply")
        self.assertEqual(code, "REFUSED")
        self.assertIn("waits on step(s) 01", msg)

    def test_cleanup_is_never_stuck_behind_an_outcome(self):
        v = self.by_kind("verify")[0]
        revoke = [s for s in self.by_kind("revoke") if v["id"] in s["after"]][0]
        copy = [s for s in self.by_kind("copy")][0]
        st = {x: {"status": "done"} for x in v["after"]}
        st[v["id"]] = {"status": "mismatch"}
        (self.d / "plan.json.state.json").write_text(json.dumps(st), encoding="utf-8")
        code, out = self.call("--step", revoke["id"])
        self.assertIn("PERSON step", out)
        st[v["id"]] = {"status": "running"}
        (self.d / "plan.json.state.json").write_text(json.dumps(st), encoding="utf-8")
        self.assertEqual(self.call("--step", revoke["id"])[0], "REFUSED")
        st[copy["id"]] = {"status": "failed"}
        (self.d / "plan.json.state.json").write_text(json.dumps(st), encoding="utf-8")
        self.assertEqual(self.call("--step", v["id"], "--apply")[0], "REFUSED")  # a verify needs a done copy
        # ...but the copy's failure doesn't strand the temporary grants: the verify can never run, so the
        # revoke after it can.
        st.pop(v["id"])
        (self.d / "plan.json.state.json").write_text(json.dumps(st), encoding="utf-8")
        self.assertIn("PERSON step", self.call("--step", revoke["id"])[1])

    def test_a_step_that_fails_is_recorded_failed(self):
        self.done()
        self.call("--step", "01", "--apply", run=fake({("storage", "buckets", "create"): (1, "")}))
        self.assertEqual(self.state()["01"]["status"], "failed")

    def test_the_iam_check_prints_what_must_remain(self):
        check = self.by_kind("check-iam")[0]
        self.done(*check["after"])
        code, out = self.call("--step", check["id"])
        self.assertIn("get-iam-policy", out)
        self.assertIn("Confirm the bindings hold only: user:<redacted-email>", out)
        self.assertIn("projectEditor:", out)

    def test_without_apply_nothing_runs(self):
        calls = []
        code, out = self.call("--step", "01", run=fake(calls=calls))
        self.assertIn("dry run", out)
        self.assertEqual([c for c in calls if c[:2] == ["storage", "buckets"]], [])

    def test_a_person_step_prints_the_commands_and_records_done(self):
        temp = [s for s in self.by_kind("grant-temp") if "caller" in s["desc"]][0]
        self.done(*temp["after"])
        code, out = self.call("--step", temp["id"])
        self.assertIn("# user-gcloud:", out)
        self.assertIn("--member=serviceAccount:<redacted-email>", out)
        self.assertNotIn(temp["id"], self.state())
        self.call("--step", temp["id"], "--done")
        self.assertEqual(self.state()[temp["id"]]["status"], "done")

    def test_one_active_copy_per_project(self):
        first, second = self.by_kind("copy")
        self.done(*[x for x in second["after"] if x != first["id"]])
        st = self.state()
        st[first["id"]] = {"status": "running", "op": "projects/p/locations/global/operations/1"}
        (self.d / "plan.json.state.json").write_text(json.dumps(st), encoding="utf-8")
        code, msg = self.call("--step", second["id"], "--apply")
        self.assertEqual(code, "REFUSED")
        self.assertIn("waits on step(s) " + first["id"], msg)

    def test_a_copy_records_its_operation_and_status_follows_it(self):
        copy = self.by_kind("copy")[0]
        self.done(*copy["after"])
        op = "projects/firebase-auratech16/locations/global/operations/abc"
        self.call("--step", copy["id"], "--apply", run=fake({("logging", "copy"): json.dumps({"name": op})}))
        self.assertEqual(self.state()[copy["id"]], {"status": "running", "op": op})
        listing = json.dumps([{"name": op, "done": True,
                               "metadata": {"state": "OPERATION_STATE_SUCCEEDED", "progress": 100}}])
        code, out = self.call("status", run=fake({("logging", "operations", "list"): listing}))
        self.assertEqual(self.state()[copy["id"]]["status"], "done")
        self.assertIn("progress=100", out)

    def verify(self, objects, lines_per_object, source_ids):
        v = self.by_kind("verify")[0]
        self.done(*v["after"])
        h = v["sample_hour"]
        tag = f"{h[:4]}/{h[5:7]}/{h[8:10]}/{h[11:13]}:00:00_{h[11:13]}:59:59_S0.json"
        ls = "".join(f"gs://{v['bucket']}/requests/{tag if i == 0 else '2026/09/21/01:00:00_01:59:59_S0.json'}\n"
                     for i in range(objects))
        answers = {("storage", "du"): f"12345 gs://{v['bucket']}\n", ("storage", "ls"): ls,
                   ("storage", "cat"): "{}\n" * lines_per_object,
                   ("logging", "read"): "".join(f"id{i}\n" for i in range(source_ids))}
        return v, self.call("--step", v["id"], "--apply", run=fake(answers))

    def test_verify_matches_the_sample_hour(self):
        v, _ = self.verify(objects=3, lines_per_object=7, source_ids=7)
        self.assertEqual({k: self.state()[v["id"]][k] for k in ("status", "objects", "bytes", "sample_source", "sample_copied")},
                         {"status": "done", "objects": 3, "bytes": 12345, "sample_source": 7, "sample_copied": 7})

    def test_a_mismatch_or_an_empty_bucket_is_never_a_pass(self):
        v, (code, out) = self.verify(objects=3, lines_per_object=6, source_ids=7)
        self.assertEqual(self.state()[v["id"]]["status"], "mismatch")
        self.assertIn("MISMATCH", out)
        self.setUp()
        v, (code, msg) = self.verify(objects=0, lines_per_object=0, source_ids=7)
        self.assertEqual(code, "REFUSED")
        self.assertIn("UNVERIFIED", msg)

    def test_an_empty_sample_hour_or_a_capped_count_is_unverified(self):
        v, _ = self.verify(objects=3, lines_per_object=0, source_ids=0)
        self.assertEqual(self.state()[v["id"]]["status"], "unverified")
        self.setUp()
        with mock.patch.object(run_step, "READ_LIMIT", 7):
            v, (code, out) = self.verify(objects=3, lines_per_object=7, source_ids=7)
        self.assertEqual(self.state()[v["id"]]["status"], "unverified")
        self.assertIn("read cap", out)

    def test_the_sample_hour_runs_to_the_next_hour(self):
        v = self.by_kind("verify")[0]
        self.done(*v["after"])
        seen = []
        run = fake({("storage", "du"): "1 x\n", ("storage", "ls"): f"gs://{v['bucket']}/a\n"}, calls=seen)
        self.call("--step", v["id"], "--apply", "--sample-hour", "2026-10-08T23", run=run)
        flt = next(c for c in seen if c[:2] == ["logging", "read"])[2]
        self.assertIn('timestamp>="2026-10-08T23:00:00Z" AND timestamp<"2026-10-09T00:00:00Z"', flt)

    def test_a_failed_gcloud_names_the_credential(self):
        self.done()
        code, msg = self.call("--step", "01", "--apply", run=fake({("storage", "buckets", "create"): (1, "")}))
        self.assertEqual(code, "REFUSED")
        self.assertIn("failed as", msg)


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class PersonCopy(unittest.TestCase):
    """ENGBACK-2333: GCP_KEY has no logging.buckets.copyLogEntries, so by default a person runs the copy."""

    def plan(self, uids=False):
        d = pathlib.Path(tempfile.mkdtemp())
        args = [a for a in BASE if a not in ("--copy-as", "--create-as", "sa")] + ["--out", str(d / "plan.json")]
        if uids:
            (d / "u.txt").write_text("\n".join(UIDS) + "\n", encoding="utf-8")
            args += ["--uids-file", str(d / "u.txt")]
        with contextlib.redirect_stdout(io.StringIO()):
            plan_hold.main(args)
        return d, json.loads((d / "plan.json").read_text(encoding="utf-8"))

    def step(self, d, sid, *extra):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = run_step.main(["--plan", str(d / "plan.json"), "--step", sid, *extra], run=fake())
        return code, out.getvalue()

    def test_copy_is_a_person_step_by_default(self):
        _, p = self.plan()
        copies = [s for s in p["steps"] if s["kind"] == "copy"]
        self.assertTrue(copies)
        self.assertTrue(all(s["runs_as"] == "person" for s in copies))

    def test_bucket_creation_is_a_person_step_by_default(self):
        d, p = self.plan()
        creates = [s for s in p["steps"] if s["kind"] == "create"]
        self.assertTrue(creates)
        self.assertTrue(all(s["runs_as"] == "person" for s in creates))
        code, out = self.step(d, creates[0]["id"])
        self.assertIn("create the hold bucket", out)
        self.assertIn("gcloud storage buckets create", out)

    def test_person_copy_prints_the_line_and_records_done_without_running_anything(self):
        d, p = self.plan(uids=True)
        st = {s["id"]: {"status": "done"} for s in p["steps"] if s["kind"] != "copy"}
        (d / "plan.json.state.json").write_text(json.dumps(st), encoding="utf-8")
        for s in [s for s in p["steps"] if s["kind"] == "copy"]:
            code, out = self.step(d, s["id"])
            self.assertEqual(code, 0)
            self.assertIn("gcloud logging copy", out)
            self.assertIn("operations list", out)
            for uid in UIDS:
                self.assertNotIn(uid, out)  # a focus filter stays in its mode-600 file
            line = next(l.strip() for l in out.splitlines() if l.strip().startswith("gcloud logging copy"))
            if s.get("scope") != "focus":
                # Pasted into a shell, the line gives back exactly the planned argv: quotes, spaces, < > kept.
                self.assertEqual(shlex.split(line), ["gcloud", *s["argv"]])
            else:
                f = d / f"plan.json.filter-{s['id']}.txt"
                r = subprocess.run(["bash", "-c", f"printf '%s\\n' {line[len('gcloud '):]}"],
                                   capture_output=True, text=True)
                self.assertEqual(r.stdout.splitlines(), ["logging", *[a if not a.startswith("--log-filter=")
                    else "--log-filter=" + f.read_text(encoding="utf-8") for a in s["argv"][1:]]])
            if s.get("scope") == "focus":
                f = d / f"plan.json.filter-{s['id']}.txt"
                self.assertEqual(f.stat().st_mode & 0o777, 0o600)
                self.assertIn(UIDS[0], f.read_text(encoding="utf-8"))
                self.assertIn(str(f), out)
            code, out = self.step(d, s["id"], "--done")
            self.assertIn("recorded as done", out)


class Analytics(unittest.TestCase):
    def step(self):
        _, p, _ = make_plan("--analytics-dataset", "analytics_153603506")
        return [s for s in p["steps"] if s["kind"] == "analytics"][0]

    def test_the_step_waits_for_every_extract_job(self):
        step, polls, slept = self.step(), {}, []

        def opener(req):
            if req.get_method() == "POST":
                n = len(polls)
                polls[f"job{n}"] = 0
                return Resp(json.dumps({"jobReference": {"jobId": f"job{n}", "location": "US"}}).encode())
            job = req.full_url.split("/jobs/")[1].split("?")[0]
            polls[job] += 1
            return Resp(json.dumps({"status": {"state": "DONE" if polls[job] > 1 else "RUNNING"}}).encode())
        r = run_step.run_analytics(step, fake(), opener, sleep=slept.append)
        self.assertEqual(r["extract_jobs"], 19)
        self.assertTrue(all(n == 2 for n in polls.values()))
        self.assertEqual(len(slept), 19)

    def test_a_failed_job_fails_the_step(self):
        step = self.step()

        def opener(req):
            if req.get_method() == "POST":
                return Resp(json.dumps({"jobReference": {"jobId": "j", "location": "US"}}).encode())
            return Resp(json.dumps({"status": {"state": "DONE", "errorResult": {"reason": "notFound"}}}).encode())
        with self.assertRaises(run_step.Refused):
            run_step.run_analytics(step, fake(), opener, sleep=lambda s: None)


class Auth(unittest.TestCase):
    def test_password_hashes_are_never_written(self):
        d, p, _ = make_plan(uids=True)
        step = [s for s in p["steps"] if s["kind"] == "auth"][0]
        written = {}

        def opener(req):
            body = json.loads(req.data)
            self.assertEqual(body["localId"], UIDS)
            self.assertTrue(req.get_header("User-agent").startswith("AuraClaude/"))
            return Resp(json.dumps({"users": [{"localId": u, "passwordHash": "x", "salt": "y", "createdAt": "1",
                                               "providerUserInfo": [{"providerId": "password"}]}
                                              for u in UIDS]}).encode())

        def run(argv, **kw):
            if argv[:2] == ["storage", "cp"]:
                written["data"] = json.loads(pathlib.Path(argv[2]).read_text(encoding="utf-8"))
                written["mode"] = stat.S_IMODE(os.stat(argv[2]).st_mode)
            return fake()(argv, **kw)

        result = run_step.run_auth(step, run, opener)
        self.assertEqual((result["asked"], result["found"]), (2, 2))
        mixed = dict(step, uids_file=str(pathlib.Path(tempfile.mkdtemp(), "u.txt")))
        pathlib.Path(mixed["uids_file"]).write_text(f"{UIDS[0]}\nsomeone@example.com\napple:001.x\n", encoding="utf-8")
        sent = []

        def by_field(req):
            body = json.loads(req.data)
            sent.append(sorted(body))
            return Resp(json.dumps({"users": [{"localId": "x"}] if "localId" in body else []}).encode())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            r = run_step.run_auth(mixed, run, by_field)
        self.assertEqual(sorted(sent), [["email"], ["localId"]])
        self.assertEqual((r["status"], r["asked"], r["found"], r["unsupported"]), ("partial", 3, 1, 1))
        self.assertIn("PARTIAL: 1 of 3", out.getvalue())
        with mock.patch.object(run_step, "AUTH_BATCH", 1):
            batches = []
            run_step.run_auth(step, run, lambda req: batches.append(json.loads(req.data)["localId"]) or Resp(b'{"users": []}'))
        self.assertEqual(batches, [[UIDS[0]], [UIDS[1]]])
        self.assertEqual(written["mode"], 0o600)
        for u in written["data"]["users"]:
            self.assertNotIn("passwordHash", u)
            self.assertNotIn("salt", u)
            self.assertIn("createdAt", u)


if __name__ == "__main__":
    unittest.main()
