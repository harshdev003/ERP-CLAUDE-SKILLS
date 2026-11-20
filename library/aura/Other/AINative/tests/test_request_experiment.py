"""Guard tests for `eng:request-experiment`.

Two failures this skill exists to prevent, both silent once posted: an experiment
created on one arm (assigns nobody to the treatment), and a minimum version that
lets in a build without the change. AuraBot has no update command, so either one
needs a manual edit. Plus the check: an unreadable record must never read as
verified. No network — gh, Slack and the Aura API are stubbed.
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "plugins", "eng", "skills", "request-experiment", "scripts")
sys.path.insert(0, SCRIPTS)
_spec = importlib.util.spec_from_file_location("request_experiment",
                                               os.path.join(SCRIPTS, "request_experiment.py"))
rx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rx)

PR = "https://github.com/AuraHealth/AuraConsumerApp/pull/1904"
SHA = "a" * 40


class Arms(unittest.TestCase):
    def test_letters_in_order(self):
        self.assertEqual(rx.arm_letters("a: echo cancel on, b: control"), "ab")
        self.assertEqual(rx.arm_letters("a: x; b: control; c: y"), "abc")

    def test_one_arm_is_refused(self):
        self.assertIn("at least two", rx.arms_problem("a: control"))

    def test_no_control_is_refused(self):
        self.assertIn("control", rx.arms_problem("a: x, b: y"))

    def test_gap_is_refused(self):
        self.assertIn("consecutive", rx.arms_problem("a: x, c: control"))

    def test_good_arms(self):
        self.assertEqual(rx.arms_problem("a: x, b: control"), "")


class RequestText(unittest.TestCase):
    def render(self, arms="a: echo cancel, b: control", app="ai"):
        return rx.render_request("aiVoiceAudioMode", "Voice echo test", arms, "1.33.11",
                                 app, "ios", [PR])

    def test_asks_active_on_every_arm_with_minimum(self):
        text = self.render(arms="a: x, b: control, c: y")
        self.assertIn("*active* on all arms (variants `abc`)", text)
        self.assertIn("minimum version *1.33.11*", text)

    def test_never_asks_for_off(self):
        text = self.render().lower()
        self.assertNotIn(" off", text)
        self.assertNotIn("everyone on b", text)

    def test_addresses_viktor_never_aurabot(self):
        text = self.render()
        self.assertTrue(text.startswith(f"<@{rx.VIKTOR}>"))
        self.assertNotIn(rx.AURABOT, text)

    def test_target_app(self):
        self.assertIn("targetApp `ai`", self.render(app="ai"))
        self.assertIn("no targetApp", self.render(app="aura"))


class FileVersion(unittest.TestCase):
    def test_project_yml_takes_the_app_version(self):
        yml = 'targets:\n  App:\n    MARKETING_VERSION: 1.33.11\n  Ext:\n    MARKETING_VERSION: "1.30.0"\n'
        self.assertEqual(rx.file_version(yml, "project.yml"), "1.33.11")

    def test_pbxproj_ignores_lagging_targets(self):
        pbx = "MARKETING_VERSION = 1.0;\nMARKETING_VERSION = 3.33.1;\nMARKETING_VERSION = 3.21.1;\n"
        self.assertEqual(rx.file_version(pbx, "ios/Aura.xcodeproj/project.pbxproj"), "3.33.1")

    def test_gradle(self):
        self.assertEqual(rx.file_version('versionName "3.18.115"', "android/app/build.gradle"),
                         "3.18.115")

    def test_unreadable_is_empty(self):
        self.assertEqual(rx.file_version("", "project.yml"), "")


class NextReleaseVersion(unittest.TestCase):
    """Stubs gh at its three chokepoints and fails each on demand."""

    def run_with(self, *, pr=None, tags=("v1.33.10-1",), contains=None, yml="MARKETING_VERSION: 1.33.11",
                 pr_url=PR, app="ai", platform="ios"):
        pr = pr if pr is not None else {"state": "MERGED", "mergeCommit": {"oid": SHA}}
        contains = contains or {}

        def gh(args):
            if args[:2] == ["pr", "view"]:
                return None if pr == "FAIL" else pr
            if args[1].startswith("repos/") and "/compare/" in args[1]:
                ref = args[1].split("...")[1]
                return None if contains.get(ref, True) is None else {
                    "status": "ahead" if contains.get(ref, True) else "diverged"}
            return {"default_branch": "main"}

        with mock.patch.object(rx, "_gh", gh), \
             mock.patch.object(rx, "_gh_raw", lambda *a: yml), \
             mock.patch.object(rx, "_tags", lambda repo: None if tags is None else list(tags)):
            return rx.next_release_version(app, platform, pr_url)

    def test_uncut_version_on_main(self):
        self.assertEqual(self.run_with(), ("1.33.11", ""))

    def test_a_released_build_without_the_merge_refuses(self):
        with mock.patch.object(rx, "app_store_version", lambda b: "1.33.11"):
            v, why = self.run_with(tags=["v1.33.11-1"], contains={"v1.33.11-1": False})
        self.assertEqual(v, "")
        self.assertIn("cut without the merge", why)

    def test_unreadable_store_with_a_build_without_the_merge_refuses(self):
        with mock.patch.object(rx, "app_store_version", lambda b: ""):
            self.assertEqual(self.run_with(tags=["v1.33.11-1"], contains={"v1.33.11-1": False})[0], "")

    def test_a_testflight_only_build_without_the_merge_is_noted(self):
        with mock.patch.object(rx, "app_store_version", lambda b: "1.33.10"), \
             redirect_stderr(io.StringIO()) as err:
            v, _ = self.run_with(tags=["v1.33.11-1"], contains={"v1.33.11-1": False})
        self.assertEqual(v, "1.33.11")
        self.assertIn("TestFlight", err.getvalue())

    def test_aura_build_without_the_merge_refuses(self):
        v, _ = self.run_with(app="aura", pr_url="https://github.com/AuraHealth/Aura/pull/1",
                             yml="MARKETING_VERSION = 3.33.1;", tags=["3.33.1-4"],
                             contains={"3.33.1-4": False})
        self.assertEqual(v, "")

    def test_a_build_cut_with_the_merge_is_fine(self):
        self.assertEqual(self.run_with(tags=["v1.33.11-2"], contains={"v1.33.11-2": True})[0],
                         "1.33.11")

    def test_unmerged_pr_refuses(self):
        v, why = self.run_with(pr={"state": "OPEN", "mergeCommit": None})
        self.assertEqual(v, "")
        self.assertIn("not merged", why)

    def test_merge_not_on_main_refuses(self):
        self.assertIn("does not carry", self.run_with(contains={"main": False})[1])

    def test_unreadable_steps_refuse(self):
        self.assertIn("could not list", self.run_with(tags=None)[1])
        self.assertIn("could not read the version", self.run_with(yml="")[1])
        self.assertIn("could not check", self.run_with(contains={"main": None})[1])
        self.assertIn("could not read", self.run_with(pr="FAIL")[1])

    def test_pr_in_the_wrong_repo_refuses(self):
        self.assertIn("ships from", self.run_with(
            pr_url="https://github.com/AuraHealth/Aura/pull/1")[1])

    def test_aura_needs_one_platform(self):
        self.assertIn("no version file", self.run_with(app="ai", platform="android")[1])


class Check(unittest.TestCase):
    def test_config_matches(self):
        rec = {"active": "ab", "minVersion": "1.33.11", "targetApp": "ai", "targetPlatform": "ios"}
        self.assertEqual(rx.config_problems(rec, "ab", "1.33.11", "ai", "ios"), [])

    def test_one_arm_and_wrong_minimum_are_named(self):
        rec = {"active": "b", "minVersion": None, "targetApp": "ai"}
        problems = rx.config_problems(rec, "ab", "1.33.11", "ai", "ios")
        self.assertTrue(any("one arm only" in p for p in problems))
        self.assertTrue(any("minVersion" in p for p in problems))

    def test_missing_platform_is_a_mismatch(self):
        rec = {"active": "ab", "minVersion": "3.18.115"}
        self.assertTrue(any("targetPlatform" in p
                            for p in rx.config_problems(rec, "ab", "3.18.115", "aura", "android")))

    def test_aura_must_have_no_target_app(self):
        rec = {"active": "ab", "minVersion": "3.33.1", "targetApp": "ai"}
        self.assertTrue(rx.config_problems(rec, "ab", "3.33.1", "aura", "ios"))

    def test_thread_state_reads_aurabot_confirmation(self):
        msgs = [{"user": "UJEROME", "text": "request"},
                {"user": rx.VIKTOR, "text": "on it"},
                {"user": rx.AURABOT, "text": "Created experiment `aiX`"}]
        self.assertTrue(rx.thread_state(msgs, "aiX")["created"])
        self.assertFalse(rx.thread_state(msgs, "aiY")["created"])
        self.assertEqual(rx.thread_state(msgs, "aiX")["viktor_last"], "on it")

    def check(self, record_result):
        args = rx.build_parser().parse_args(
            ["check", "--target-app", "ai", "--experiment", "aiX", "--ts", "1.2",
             "--arms", "a: x, b: control", "--version", "1.33.11"])
        msgs = [{"user": "U"}, {"user": rx.AURABOT, "text": "Created experiment `aiX`"}]
        import slack_post, slack_read  # noqa: E401
        with mock.patch.object(slack_read, "replies", lambda *a, **k: msgs), \
             mock.patch.object(slack_post, "get_permalink", lambda *a, **k: {"ok": True, "permalink": "https://link"}), \
             mock.patch.object(rx, "experiment_record", lambda name: record_result), \
             redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
            rc = args.func(args)
        return rc, out.getvalue()

    def test_unreadable_record_is_never_verified(self):
        rc, out = self.check((None, "HTTP 401", {}))
        self.assertEqual(rc, 2)
        self.assertNotIn("VERIFIED", out)

    def test_verified(self):
        rc, out = self.check((True, "HTTP 200", {"active": "ab", "minVersion": "1.33.11",
                                                  "targetApp": "ai", "targetPlatform": "ios"}))
        self.assertEqual(rc, 0)
        self.assertIn("VERIFIED", out)
        self.assertIn("Thread: https://link", out)

    def test_mismatch(self):
        rc, out = self.check((True, "HTTP 200", {"active": "b", "minVersion": "1.33.11",
                                                  "targetApp": "ai"}))
        self.assertEqual(rc, 2)
        self.assertIn("MISMATCH", out)

    def test_empty_200_is_unknown(self):
        class Resp:
            status = 200
            def read(self): return b"{}"
            def __enter__(self): return self
            def __exit__(self, *a): return False
        with mock.patch.dict(os.environ, {"AURA_API_TOKEN": "t"}), \
             mock.patch.object(rx.urllib.request, "urlopen", lambda *a, **k: Resp()):
            self.assertIsNone(rx.experiment_record("aiX")[0])


if __name__ == "__main__":
    unittest.main()
