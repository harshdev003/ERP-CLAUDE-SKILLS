"""Guard tests for `eng:clean-up-users`.

The skill wraps a prod batch job, so the failures worth pinning are the quiet ones:
an apply without Jerome's go text, a run with no summary read as a clean one,
a cleanupDeletedUsers exit 0 read as success, an export path that git would commit,
and an rm printed for the wrong folder. No network: the CLI is a stub script.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins" / "eng" / "skills" / "clean-up-users" / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("clean_up_users", SCRIPTS / "clean_up_users.py")
cu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cu)

SUMMARY = {"counts": {"deleted": 0, "invalidLocale": 0, "lite": 2, "premiumNoSubscription": 0,
                      "premiumTrial": 0, "updated": 1, "users": 3, "googleTokens": 0},
           "doUpdate": False}
UID = "uidSECRETabcdefghijkl"


def fake_cli(root: pathlib.Path, body: str) -> pathlib.Path:
    """A git checkout shaped like Tools/CLI whose commander.js runs `body`."""
    (root / "src" / "cli").mkdir(parents=True)
    (root / "src" / "tasks" / "user").mkdir(parents=True)
    (root / "src" / "tasks" / "user" / "cleanupUsers.js").write_text("")
    (root / "src" / "config.js").write_text("const {CLEANUP_EXPORTS} = process.env;\n")
    (root / "src" / "cli" / "commander.js").write_text(body)
    (root / ".gitignore").write_text("data/cleanup-users/\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def run_main(*argv, env=None):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, "argv", ["clean_up_users.py", *argv]), \
            mock.patch.dict(os.environ, env or {}, clear=False), \
            redirect_stdout(out), redirect_stderr(err):
        rc = cu.main()
    return rc, out.getvalue(), err.getvalue()


class TestSummaryParsing(unittest.TestCase):
    def test_summary_followed_by_log_lines(self):
        text = "info: start\n" + json.dumps(SUMMARY, indent=2) + "\ninfo: uid lists written\nGood bye\n"
        self.assertEqual(cu.last_json_object(text), SUMMARY)

    def test_no_summary(self):
        self.assertIsNone(cu.last_json_object("info: start\n{not json\nGood bye\n"))

    def test_a_logged_object_without_counts_is_not_the_summary(self):
        self.assertIsNone(cu.last_json_object('{\n "givenName": "x"\n}\n'))

    def test_cleanup_users_verdicts(self):
        good = json.dumps(SUMMARY, indent=2)
        self.assertEqual(cu.summarize("cleanupUsers", good, 0)["verdict"], "ok")
        self.assertEqual(cu.summarize("cleanupUsers", "Good bye", 0)["verdict"], "unverified")
        self.assertEqual(cu.summarize("cleanupUsers", good, 1)["verdict"], "failed")

    def test_a_stopped_window_is_stopped_with_its_resume_row(self):
        # CLI#72: SIGTERM or Ctrl-C writes the uid lists, prints the summary with resumeRow and exits 143 or 130.
        stopped = dict(SUMMARY, resumeRow=120000, stopped="SIGTERM")
        out = "info: Processed 10000 / 500000 users\n" + json.dumps(stopped, indent=2) + \
            "\ninfo: Stopped by SIGTERM: uid lists written (mode 600): x\n"
        for rc in (143, 130):
            s = cu.summarize("cleanupUsers", out, rc)
            self.assertEqual((s["verdict"], s["resumeRow"]), ("stopped", 120000))
            self.assertEqual(s["summary"]["counts"], SUMMARY["counts"])
        # Any other non-zero exit, or a stop without its summary, stays failed
        self.assertEqual(cu.summarize("cleanupUsers", out, 1)["verdict"], "failed")
        self.assertEqual(cu.summarize("cleanupUsers", json.dumps(SUMMARY), 143)["verdict"], "failed")
        self.assertEqual(cu.summarize("cleanupUsers", "Terminated", 143)["verdict"], "failed")

    def test_deleted_users_summary_is_read_like_cleanup_users(self):
        # CLI#65: cleanupDeletedUsers prints counts on stdout, after its log lines, and exits 1 on any error.
        summary = {"counts": {"errors": 0, "existing": 1, "invalid": 0, "lite": 2, "users": 3}, "doUpdate": False}
        out = "info: Number of deleted users: 3\n" + json.dumps(summary, indent=2) + "\ninfo: uid lists written (mode 600): x\n"
        s = cu.summarize("cleanupDeletedUsers", out, 0)
        self.assertEqual((s["verdict"], s["summary"]), ("ok", summary))
        self.assertEqual(cu.summarize("cleanupDeletedUsers", out, 1)["verdict"], "failed")

    def test_deleted_users_without_a_summary_exit_0_is_never_ok(self):
        # A CLI older than CLI#65: no summary, and a caught error still exits 0.
        log = ("info: Number of deleted users: 3\nwarn: Lite user 'a'\n"
               "warn: User found 'b' without name\nwarn: User found 'c'\n"
               "info: Should remove all references for user 'd'\n")
        s = cu.summarize("cleanupDeletedUsers", log, 0)
        self.assertEqual(s["verdict"], "unverified")
        self.assertEqual(s["counts"], {"lite": 1, "withoutName": 1, "existing": 1,
                                       "referencesRemoved": 1, "errors": 0, "deletedUsers": 3})
        self.assertEqual(cu.summarize("cleanupDeletedUsers", log, 1)["verdict"], "failed")

    def test_errors_counts_logger_error_lines_not_profile_text(self):
        log = ('info: {"givenName": "error", "bio": "no error here"}\n'
               "\x1b[31merror\x1b[39m: Error processing user 'a': boom\n"
               "error: Error processing user 'b': boom\n")
        self.assertEqual(cu.deleted_summary(log)["errors"], 2)


class TestInputs(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def test_all_three_user_shapes(self):
        p = self.tmp / "u.json"
        for data in ({"select": [{"id": "a"}, {"id": "b"}]}, ["a", "b"], [{"id": "a"}, {"id": "b"}]):
            p.write_text(json.dumps(data))
            self.assertEqual(cu.read_user_ids(p), ["a", "b"])

    def test_bad_user_ids_refused(self):
        p = self.tmp / "u.json"
        for data in (["a", ""], [{"uid": "a"}], {"q1": [], "q2": []}, {"q": "a"}):
            p.write_text(json.dumps(data))
            with self.assertRaises(cu.Refused):
                cu.read_user_ids(p)

    def test_mixpanel_counts_bad_lines(self):
        p = self.tmp / "m.jsonl"
        p.write_text('{"userid": "a"}\nnot json\n{"userid": null}\n\n')
        self.assertEqual(cu.check_mixpanel(p), {"rows": 2, "unparsable": 1, "withoutUserid": 1})

    def test_export_file_is_private(self):
        dest = self.tmp / "m.jsonl"
        n = cu.write_jsonl([{"userid": "a", "age_min": 18, "extra": "dropped"}], dest)
        self.assertEqual(n, 1)
        self.assertEqual(dest.stat().st_mode & 0o777, 0o600)
        self.assertEqual(set(json.loads(dest.read_text())), set(cu.MIXPANEL_KEYS))


class TestWithFakeCli(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        patcher = mock.patch.object(cu, "RUN_LOG", self.tmp / "runs" / "runs.jsonl")
        patcher.start()
        self.addCleanup(patcher.stop)
        # Never the real held-uid list: each test sets its own.
        self.held = self.tmp / "held.json"
        p2 = mock.patch.object(cu, "HELD_UIDS", self.held)
        p2.start()
        self.addCleanup(p2.stop)
        # A keep list is required; this fixture uid is in no users.json unless a test puts it there.
        self.write_held(["fixtureHeldUidFixtureHeld01"])
        self.env = {"AURA_CLI_DIR": "", "CLEANUP_EXPORTS": "", "CLEANUP_USERS_JSON": "",
                    "CLEANUP_MIXPANEL_JSONL": "", "CLEANUP_PRIVATE_DIR": ""}
        for k in self.env:
            os.environ.pop(k, None)

    def cli(self, body="process.stdout.write('ok\\n')"):
        root = self.tmp / "CLI"
        n = 0
        while root.exists():
            n += 1
            root = self.tmp / f"CLI{n}"
        return fake_cli(root, body)

    def write_held(self, uids, mode=0o600):
        self.held.write_text(json.dumps(uids))
        self.held.chmod(mode)

    def users(self, cli, data):
        ex = cli / "data" / "cleanup-users"
        ex.mkdir(parents=True, exist_ok=True)
        (ex / "users.json").write_text(json.dumps(data))
        return ex

    def test_exclude_held_keeps_shape_and_prints_counts_only(self):
        held = "heldUidHeldUidHeldUid0000001"
        for data in ([held, "a1", "a2"], [{"id": held}, {"id": "a1"}],
                     {"select id": [{"id": "a1"}, {"id": held}]}):
            with self.subTest(shape=type(data).__name__):
                cli = self.cli()
                ex = self.users(cli, data)
                self.write_held([held])
                rc, out, _ = run_main("--cli-dir", str(cli), "exclude-held")
                self.assertEqual(rc, 0)
                self.assertNotIn(held, out)
                self.assertEqual(json.loads(out)["removed"], 1)
                after = json.loads((ex / "users.json").read_text())
                self.assertEqual(type(after), type(data))
                self.assertNotIn(held, json.dumps(after))
                self.assertEqual((ex / "users.json").stat().st_mode & 0o777, 0o600)

    def test_no_or_empty_keep_list_is_refused(self):
        cli = self.cli("require('fs').writeFileSync('RAN', '')")
        self.users(cli, ["a1"])
        for setup in (lambda: self.held.unlink(), lambda: self.write_held([])):
            setup()
            for argv in (("exclude-held",), ("check-held",), ("run", "cleanupUsers")):
                with self.subTest(argv=argv):
                    self.assertEqual(run_main("--cli-dir", str(cli), *argv)[0], 2)
            self.assertFalse((cli / "RAN").exists())

    def test_exclude_held_logs_a_prefix_line_per_skip(self):
        held = "heldUidHeldUidHeldUid0000001"
        cli = self.cli()
        self.users(cli, [held, "a1"])
        self.write_held([held])
        rc, out, err = run_main("--cli-dir", str(cli), "exclude-held")
        self.assertEqual((rc, json.loads(out)["kept"]), (0, 1))
        self.assertIn(f"skip {held[:6]}…: keep list", err)
        self.assertNotIn(held, out + err)

    def test_open_or_malformed_held_list_is_refused(self):
        cli = self.cli()
        self.users(cli, ["a1"])
        for content, mode in (([1], 0o600), (["x"], 0o644)):
            self.write_held(content, mode)
            self.assertEqual(run_main("--cli-dir", str(cli), "exclude-held")[0], 2)

    def test_run_refuses_a_users_json_with_a_held_uid(self):
        cli = self.cli("require('fs').writeFileSync('RAN', '')")
        self.users(cli, ["heldX", "a1"])
        self.write_held(["heldX"])
        rc, _, err = run_main("--cli-dir", str(cli), "run", "cleanupUsers")
        self.assertEqual(rc, 2)
        self.assertIn("run exclude-held", err)
        self.assertNotIn("heldX", err)
        self.assertFalse((cli / "RAN").exists())

    def test_follow_up_refuses_a_deleted_json_with_a_held_uid(self):
        cli = self.cli("require('fs').writeFileSync('RAN', '')")
        ex = self.users(cli, ["a1"])
        (ex / "output").mkdir()
        (ex / "output" / "deleted.json").write_text(json.dumps(["heldX"]))
        self.write_held(["heldX"])
        rc, out, _ = run_main("--cli-dir", str(cli), "check-held")
        self.assertEqual((rc, json.loads(out)["heldFound"]), (1, 1))
        rc, _, _ = run_main("--cli-dir", str(cli), "run", "cleanupDeletedUsers", "--apply", "--go", "go")
        self.assertEqual(rc, 2)
        self.assertFalse((cli / "RAN").exists())

    def test_sample_must_be_a_positive_whole_number(self):
        cli = self.cli("require('fs').writeFileSync('RAN', '')")
        self.users(cli, ["a1"])
        for bad in ("0", "-5", "x"):
            with self.assertRaises(SystemExit):
                run_main("--cli-dir", str(cli), "run", "cleanupUsers", "--sample", bad)
        self.assertFalse((cli / "RAN").exists())

    def test_held_uids_are_matched_without_surrounding_whitespace(self):
        cli = self.cli("require('fs').writeFileSync('RAN', '')")
        ex = self.users(cli, ["heldX", "a1"])
        self.write_held([" heldX\n"])
        self.assertEqual(run_main("--cli-dir", str(cli), "run", "cleanupUsers")[0], 2)
        rc, out, _ = run_main("--cli-dir", str(cli), "exclude-held")
        self.assertEqual(json.loads(out)["removed"], 1)
        self.assertNotIn("heldX", (ex / "users.json").read_text())

    def test_sample_is_a_cleanup_users_dry_run_only(self):
        cli = self.cli()
        self.users(cli, ["a1"])
        for argv in (["run", "cleanupUsers", "--sample", "10", "--apply", "--go", "go"],
                     ["run", "cleanupDeletedUsers", "--sample", "10"]):
            self.assertEqual(run_main("--cli-dir", str(cli), *argv)[0], 2)

    @unittest.skipUnless(subprocess.run(["which", "node"], capture_output=True).returncode == 0, "node")
    def test_sample_runs_on_the_head_of_users_json_and_projects_the_full_run(self):
        # The stub CLI reads the file the wrapper points it at and reports how many users it saw.
        body = ("const ids = JSON.parse(require('fs').readFileSync(process.env.CLEANUP_USERS_JSON, 'utf8'));"
                "require('fs').writeFileSync('DIR', process.env.CLEANUP_PRIVATE_DIR);"
                "process.stdout.write(JSON.stringify({counts: {users: ids.length}, doUpdate: false}) + '\\n');")
        cli = self.cli(body)
        ex = self.users(cli, [f"u{i}" for i in range(30)])
        rc, out, _ = run_main("--cli-dir", str(cli), "run", "cleanupUsers", "--sample", "10")
        self.assertEqual(rc, 0, out)
        res = json.loads(out)
        self.assertEqual(res["summary"]["counts"]["users"], 10)
        self.assertEqual(res["timing"]["fullUsers"], 30)
        self.assertIsNotNone(res["timing"]["projectedFullHours"])
        self.assertEqual((ex / "users-sample-10.json").stat().st_mode & 0o777, 0o600)
        self.assertTrue((cli / "DIR").read_text().endswith("output-sample"))

    def test_prepare_creates_private_folder(self):
        cli = self.cli()
        rc, out, _ = run_main("--cli-dir", str(cli), "prepare")
        self.assertEqual(rc, 0)
        ex = cli / "data" / "cleanup-users"
        self.assertEqual(json.loads(out)["exports"], str(ex.resolve()))
        self.assertEqual(ex.stat().st_mode & 0o777, 0o700)

    def test_unignored_path_in_repo_refused(self):
        cli = self.cli()
        (cli / ".gitignore").write_text("")
        rc, _, err = run_main("--cli-dir", str(cli), "prepare")
        self.assertEqual(rc, 2)
        self.assertIn("not gitignored", err)

    def test_no_cli_dir_refused(self):
        rc, _, err = run_main("prepare")
        self.assertEqual(rc, 2)
        self.assertIn("AURA_CLI_DIR", err)

    def test_run_refuses_unignored_logs_folder(self):
        cli = self.cli("require('fs').writeFileSync('RAN', '')")
        (cli / ".gitignore").write_text("")
        rc, _, err = run_main("--cli-dir", str(cli), "run", "cleanupUsers")
        self.assertEqual(rc, 2)
        self.assertIn("not gitignored", err)
        self.assertFalse((cli / "RAN").exists())

    def test_cli_output_is_decoded_as_utf8_whatever_the_locale(self):
        cli = self.cli()
        real = subprocess.run
        seen = {}

        def spy(cmd, **kw):
            if cmd and cmd[0] == "node":
                seen.update(kw)
                return subprocess.CompletedProcess(cmd, 0, json.dumps(SUMMARY, indent=2))
            return real(cmd, **kw)
        with mock.patch.object(cu.subprocess, "run", side_effect=spy):
            rc, _, _ = run_main("--cli-dir", str(cli), "run", "cleanupUsers")
        self.assertEqual(rc, 0)
        self.assertEqual((seen.get("encoding"), seen.get("errors")), ("utf-8", "replace"))

    def test_old_cli_refused(self):
        cli = self.cli()
        (cli / "src" / "config.js").write_text("")
        rc, _, err = run_main("--cli-dir", str(cli), "prepare")
        self.assertEqual(rc, 2)
        self.assertIn("pull master", err)

    def test_apply_without_a_go_is_refused_before_the_cli_starts(self):
        cli = self.cli("require('fs').writeFileSync('RAN', '')")
        for go in (None, "", "   "):
            argv = ["--cli-dir", str(cli), "run", "cleanupUsers", "--apply"] + ([] if go is None else ["--go", go])
            rc, _, err = run_main(*argv)
            self.assertEqual(rc, 2)
            self.assertIn("pass --go with Jerome's exact go text", err)
        self.assertFalse((cli / "RAN").exists())

    @unittest.skipUnless(subprocess.run(["which", "node"], capture_output=True).returncode == 0, "node")
    def test_apply_with_a_go_runs_the_cli_with_do_update(self):
        # No standing refusal (CLI#67: the cleanups take no hold exclusion); the go is the gate.
        body = (f"if (process.env.DO_UPDATE === 'true') require('fs').writeFileSync('RAN', '');"
                f"process.stdout.write(JSON.stringify({json.dumps(SUMMARY)}, undefined, 2) + '\\n');")
        cli = self.cli(body)
        rc, out, _ = run_main("--cli-dir", str(cli), "run", "cleanupUsers", "--apply", "--go", "go cleanupUsers")
        self.assertEqual(rc, 0, out)
        self.assertTrue((cli / "RAN").exists())
        self.assertEqual(json.loads(out)["verdict"], "ok")

    @unittest.skipUnless(subprocess.run(["which", "node"], capture_output=True).returncode == 0, "node")
    def test_dry_run_prints_counts_never_log_lines(self):
        body = (f"console.log(\"warn: Lite user '{UID}'\");"
                f"process.stdout.write(JSON.stringify({json.dumps(SUMMARY)}, undefined, 2) + '\\n');"
                f"console.log('Good bye'); if (process.env.DO_UPDATE) process.exit(9);")
        cli = self.cli(body)
        rc, out, _ = run_main("--cli-dir", str(cli), "run", "cleanupUsers", env={"DO_UPDATE": "true"})
        self.assertEqual(rc, 0, out)
        res = json.loads(out)
        self.assertEqual(res["summary"], SUMMARY)
        self.assertNotIn(UID, out)
        runs = [json.loads(l) for l in pathlib.Path(res["runLog"]).read_text().splitlines()]
        self.assertEqual([r["event"] for r in runs], ["start", "end"])
        self.assertEqual(runs[1]["counts"], SUMMARY["counts"])
        self.assertTrue(runs[0]["startedAt"].endswith("+00:00") and runs[1]["endedAt"])
        self.assertEqual((runs[0]["command"], runs[0]["apply"]), ("aura cleanupUsers", False))
        self.assertNotIn(UID, pathlib.Path(res["runLog"]).read_text())
        self.assertEqual(pathlib.Path(res["runLog"]).stat().st_mode & 0o777, 0o600)
        log = pathlib.Path(res["log"])
        self.assertIn(UID, log.read_text())
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)

    def test_a_stopped_window_reports_and_logs_its_resume_row(self):
        # The stop handler's output as CLI#72 writes it: summary, resumeRow and the signal, then exit 143
        stopped = dict(SUMMARY, resumeRow=120000, stopped="SIGTERM")
        body = (f"process.stdout.write(JSON.stringify({json.dumps(stopped)}, undefined, 2) + '\\n');"
                f"console.log(\"info: Stopped by SIGTERM: uid lists written (mode 600): x\"); process.exit(143);")
        rc, out, _ = run_main("--cli-dir", str(self.cli(body)), "run", "cleanupUsers")
        self.assertEqual(rc, 1, out)  # only ok exits 0: nothing follows a stopped window
        res = json.loads(out)
        self.assertEqual((res["verdict"], res["resumeRow"], res["exitCode"]), ("stopped", 120000, 143))
        end = json.loads(pathlib.Path(res["runLog"]).read_text().splitlines()[-1])
        self.assertEqual((end["verdict"], end["resumeRow"], end["counts"]), ("stopped", 120000, SUMMARY["counts"]))

    def test_remove_prints_only_the_exports_folder(self):
        cli = self.cli()
        run_main("--cli-dir", str(cli), "prepare")
        rc, out, _ = run_main("--cli-dir", str(cli), "remove")
        self.assertEqual(rc, 0)
        ex = (cli / "data" / "cleanup-users").resolve()
        self.assertEqual(json.loads(out)["command"], f"rm -rf -- '{ex}'")
        self.assertTrue(ex.exists())

    def test_remove_manifest_lists_rows_and_hashes_never_content(self):
        import hashlib
        cli = self.cli()
        run_main("--cli-dir", str(cli), "prepare")
        ex = cli / "data" / "cleanup-users"
        (ex / "users.json").write_text(json.dumps({"q": [{"id": UID}, {"id": "b"}]}))
        (ex / "mixpanel.jsonl").write_text('{"userid": "a"}\n{"userid": "b"}\n{"userid": "c"}\n')
        (ex / "output").mkdir()
        (ex / "output" / "lite.json").write_text(json.dumps([UID]))
        rc, out, _ = run_main("--cli-dir", str(cli), "remove")
        self.assertEqual(rc, 0)
        self.assertNotIn(UID, out)
        m = {r["file"]: r for r in json.loads(out)["manifest"]}
        self.assertEqual({k: v["rows"] for k, v in m.items()},
                         {"mixpanel.jsonl": 3, "output/lite.json": 1, "users.json": 2})
        self.assertEqual(m["users.json"]["sha256"], hashlib.sha256((ex / "users.json").read_bytes()).hexdigest())

    def test_remove_refuses_another_folder(self):
        cli = self.cli()
        other = self.tmp / "data"
        other.mkdir()
        rc, _, err = run_main("--cli-dir", str(cli), "remove", env={"CLEANUP_EXPORTS": str(other)})
        self.assertEqual(rc, 2)
        self.assertIn("not a 'cleanup-users' folder", err)


if __name__ == "__main__":
    unittest.main()
