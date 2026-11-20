#!/usr/bin/env python3
"""`verify` reads the Spinnaker rollout from spinbot's posts in #deployment.

Before this, every repo but IntelligenceService ended `verify` on
`deploy UNCONFIRMED`: CI green is the image pushed, and the push only *starts* the
Spinnaker "Quick deployment" pipeline that patches dev, then prod. spinbot posts
`Pipeline starting|complete|failed for <APP>` for each execution — the only
machine-readable record, since Spinnaker's API sits behind OAuth.

The fixtures are real producer output, not hand-built shapes:
`spinnaker_deployment_messages.json` is `conversations.history` from #deployment,
`spinnaker_deployment_mcp.json` the Slack MCP rendering of the same messages, and
`spinnaker_observed_pipelines.json` every (application, pipeline) pair spinbot
posted over a year. The UserService v2.83.0 release of 2026-10-06 (execution
01M483P6…) is the case the card was written from.

The load-bearing negative: a message names an application, and UserService and
UserWeb share one (`user`). Matching the application alone confirms one repo's
release with the other repo's rollout.
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
from contextlib import redirect_stdout
from unittest import mock

_HERE = pathlib.Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "plugins/eng/skills/release-version/scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import release  # noqa: E402
import rollout  # noqa: E402
import slack_read  # noqa: E402

_FIX = _HERE / "fixtures"
API = json.loads((_FIX / "spinnaker_deployment_messages.json").read_text())
MCP = (_FIX / "spinnaker_deployment_mcp.json").read_text()
OBSERVED = json.loads((_FIX / "spinnaker_observed_pipelines.json").read_text())["pairs"]

V2_83_0_TAGGED = 1791273000.0      # ~10 min before Spinnaker started at 08:01:12Z
V2_83_0_EXECUTION = "01M483P6Y94Y9BX7SZEDH73E48"


def shape(slug: str, kind: str = "node"):
    return mock.Mock(slug=slug, shape=kind, root=pathlib.Path("/nonexistent"))


def check(slug, since, messages=API, **kw):
    return rollout.rollout_check(shape(slug, **kw), "v9.9.9", since=since,
                                 log_text=json.dumps(messages))


class RealCorpus(unittest.TestCase):
    def test_the_userservice_release_reads_as_rolled_out(self):
        out = check("AuraHealth/UserService", V2_83_0_TAGGED)
        self.assertEqual(out["rollout"], "complete")
        self.assertIn(V2_83_0_EXECUTION, out["execution"])
        self.assertEqual(out["rollout_started"], "2026-10-06T08:01:12Z")
        self.assertEqual(out["rollout_finished"], "2026-10-06T08:02:50Z")
        self.assertNotIn("rollout_note", out)

    def test_a_sibling_repo_on_the_same_application_is_not_confirmed(self):
        # `user` ran UserService's pipeline after this point, never UserWeb's.
        out = check("AuraHealth/UserWeb", 1790900000.0)
        self.assertEqual(out["rollout"], "not-started")
        self.assertNotIn("execution", out)

    def test_the_sibling_pipeline_in_between_is_skipped(self):
        # UserWeb's execution (2026-10-01) sits between this bound and the
        # UserService one; it must not be read as UserService's.
        out = check("AuraHealth/UserService", 1790870000.0)
        self.assertEqual(out["rollout"], "complete")
        self.assertIn(V2_83_0_EXECUTION, out["execution"])

    def test_executions_before_the_tag_are_ignored(self):
        out = check("AuraHealth/UserService", 1791273700.0)   # after it started
        self.assertEqual(out["rollout"], "not-started")

    def test_an_earlier_rollout_finishing_after_the_tag_is_not_this_one(self):
        # The read is windowed at the tag, so it holds the `complete` of an
        # execution whose `starting` it never saw. That is the previous image.
        windowed = [m for m in API if float(m["ts"]) >= 1791273700.0]
        out = check("AuraHealth/UserService", 1791273700.0, windowed)
        self.assertEqual(out["rollout"], "not-started")

    def test_the_first_execution_after_the_tag_is_taken_and_the_rest_noted(self):
        out = check("AuraHealth/UserService", 1790380000.0)
        self.assertIn("01M3DJ5KWWW7J5QQQ61PKN6G8C", out["execution"])
        self.assertIn("3 executions", out["rollout_note"])

    def test_a_failed_pipeline_reads_as_failed(self):
        out = check("AuraHealth/IterableService", 1771882000.0)
        self.assertEqual(out["rollout"], "failed")
        self.assertIn("01KJ678G3RSE03W4X9EFF2T67F", out["execution"])

    def test_started_but_not_finished_is_running(self):
        msgs = [m for m in API if m["ts"] != "1791273770.215569"]   # drop "complete"
        out = check("AuraHealth/UserService", V2_83_0_TAGGED, msgs)
        self.assertEqual(out["rollout"], "running")

    def test_a_status_nobody_has_seen_is_not_a_pass(self):
        msgs = json.loads(json.dumps(API))
        done = next(m for m in msgs if m["ts"] == "1791273770.215569")
        done["attachments"][0]["title"] = "Pipeline canceled for USER"
        out = check("AuraHealth/UserService", V2_83_0_TAGGED, msgs)
        self.assertEqual(out["rollout"], "unknown")


class BothReadPathsAgree(unittest.TestCase):
    """MCP and the bot token are two reads of one channel; one parser verdict."""

    def key(self, events):
        return sorted((e.ts, e.status, e.app, e.pipeline, e.execution) for e in events)

    def test_mcp_rendering_parses_to_the_same_events(self):
        mcp = rollout.parse_log(MCP)
        self.assertEqual(len(mcp), 4)
        api = [e for e in rollout.parse_api(API) if e.ts >= min(x.ts for x in mcp)]
        self.assertEqual(self.key(mcp), self.key(api))

    def test_every_saved_form_is_accepted(self):
        page = {"ok": True, "messages": API}
        raw_mcp = json.loads(MCP)["messages"]
        for text in (json.dumps(API), json.dumps(page), MCP, raw_mcp):
            out = rollout.rollout_check(shape("AuraHealth/UserService"), "v2.83.0",
                                        since=V2_83_0_TAGGED, log_text=text)
            self.assertEqual(out["rollout"], "complete", text[:60])

    def test_unparseable_log_is_not_started_never_complete(self):
        out = rollout.rollout_check(shape("AuraHealth/UserService"), "v2.83.0",
                                    since=V2_83_0_TAGGED, log_text="nothing here")
        self.assertNotEqual(out["rollout"], "complete")


class MappingIsPinnedToTheProducer(unittest.TestCase):
    def test_every_mapped_pipeline_was_actually_posted(self):
        seen = {(p["app"].casefold(), p["pipeline"].casefold()) for p in OBSERVED}
        for slug, (app, pipe) in rollout.PIPELINES.items():
            self.assertIn((app.casefold(), pipe.casefold()), seen, slug)

    def test_no_two_repos_claim_one_pipeline(self):
        targets = [(a.casefold(), p.casefold()) for a, p in rollout.PIPELINES.values()]
        self.assertEqual(len(targets), len(set(targets)))

    def test_pipeline_names_compare_casefolded(self):
        # CoachService's pipeline is posted as "Quick Deployment for Service".
        ev = [rollout.Event(t, s, "coach", "quick deployment FOR service", "X1")
              for t, s in ((90.0, "starting"), (100.0, "complete"))]
        self.assertEqual(rollout.classify(ev, "coach", "Quick Deployment for Service",
                                          0)["rollout"], "complete")


BUSINESSWEB = json.loads((_FIX / "spinnaker_businessweb_messages.json").read_text())["messages"]
BUSINESSWEB_TAGGED = 1792785600.0  # ~2 min before Spinnaker started at 1792785724
BUSINESSWEB_EXECUTION = "01M5N5PCG2Z7K33FKPVVH4C6Z4"


class BusinessWeb(unittest.TestCase):
    """BusinessWeb and BusinessService share the `business` application (ENG-1712)."""

    def test_the_real_posts_parse(self):
        events = rollout.parse_api(BUSINESSWEB)
        self.assertEqual([(e.status, e.app, e.pipeline, e.execution) for e in events], [
            ("starting", "business", "Quick deployment for Web", BUSINESSWEB_EXECUTION),
            ("complete", "business", "Quick deployment for Web", BUSINESSWEB_EXECUTION)])

    def test_a_businessweb_release_reads_as_rolled_out(self):
        out = check("AuraHealth/BusinessWeb", BUSINESSWEB_TAGGED, messages={"messages": BUSINESSWEB})
        self.assertEqual(out["rollout"], "complete", out)
        self.assertIn(BUSINESSWEB_EXECUTION, json.dumps(out))

    def test_businessservice_is_not_confirmed_by_the_web_rollout(self):
        out = check("AuraHealth/BusinessService", BUSINESSWEB_TAGGED, messages={"messages": BUSINESSWEB})
        self.assertNotEqual(out["rollout"], "complete")


class UnknownIsNotAPass(unittest.TestCase):
    def test_unmapped_repo(self):
        out = rollout.rollout_check(shape("AuraHealth/InvestWeb"), "v1.0.0", since=1.0)
        self.assertEqual(out["rollout"], "unknown")

    def test_functions_repo_says_where_its_deploy_is(self):
        out = rollout.rollout_check(shape("AuraHealth/FirebaseJob", "functions"),
                                    "v1.0.0", since=1.0)
        self.assertEqual(out["rollout"], "unknown")
        self.assertIn("CircleCI", out["rollout_detail"])

    def test_no_tag_time(self):
        with mock.patch.object(rollout, "tag_time", return_value=None):
            out = rollout.rollout_check(shape("AuraHealth/UserService"), "v2.83.0")
        self.assertEqual(out["rollout"], "unknown")

    def test_no_token_points_at_the_mcp_path(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            out = rollout.rollout_check(shape("AuraHealth/UserService"), "v2.83.0",
                                        since=V2_83_0_TAGGED)
        self.assertEqual(out["rollout"], "unknown")
        self.assertIn("--deployment-log", out["rollout_detail"])

    def test_transport_failures(self):
        for exc in (OSError("dns"), TimeoutError("slow"),
                    slack_read.SlackReadError("Slack conversations.history failed: "
                                              "not_in_channel"),
                    ValueError("bad json")):
            def boom(token, oldest, exc=exc):
                raise exc
            with mock.patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-test"}):
                out = rollout.rollout_check(shape("AuraHealth/UserService"), "v2.83.0",
                                            since=V2_83_0_TAGGED, fetch=boom)
            self.assertEqual(out["rollout"], "unknown", exc)
            self.assertIn("--deployment-log", out["rollout_detail"])

    def test_no_result_ever_carries_verify_detail(self):
        cases = [check("AuraHealth/UserService", V2_83_0_TAGGED),
                 check("AuraHealth/UserWeb", 1790900000.0),
                 rollout.rollout_check(shape("AuraHealth/Nope"), "v1", since=1.0)]
        for out in cases:
            self.assertNotIn("detail", out)


class FakeSlack:
    """`slack_read._http_get`, the helper's single transport chokepoint."""

    def __init__(self, pages):
        self.pages, self.calls = list(pages), []

    def __call__(self, url, params, headers):
        self.calls.append(dict(params))
        status, body = self.pages.pop(0)
        return status, body, 0.0


class FetchHistory(unittest.TestCase):
    """The bot-token path, through the real helper down to its HTTP call."""

    def run_check(self, fake):
        with mock.patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-test"}), \
             mock.patch.object(slack_read, "_http_get", fake), \
             mock.patch.object(slack_read.time, "sleep"):
            return rollout.rollout_check(shape("AuraHealth/UserService"), "v2.83.0",
                                         since=V2_83_0_TAGGED)

    def test_reads_every_page(self):
        done = [m for m in API if m["ts"] == "1791273770.215569"]
        start = [m for m in API if m["ts"] == "1791273672.313489"]
        fake = FakeSlack([
            (200, {"ok": True, "messages": done,
                   "response_metadata": {"next_cursor": "c2"}}),
            (200, {"ok": True, "messages": start, "response_metadata": {}}),
        ])
        out = self.run_check(fake)
        self.assertEqual(out["rollout"], "complete")
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(fake.calls[1]["cursor"], "c2")
        self.assertEqual(fake.calls[0]["channel"], "GG6CJ6P0D")
        self.assertEqual(fake.calls[0]["oldest"], f"{V2_83_0_TAGGED:.6f}")

    def test_a_refusal_is_unknown(self):
        out = self.run_check(FakeSlack([(200, {"ok": False, "error": "not_in_channel"})]))
        self.assertEqual(out["rollout"], "unknown")
        self.assertIn("not_in_channel", out["rollout_detail"])

    def test_persistent_rate_limiting_is_unknown(self):
        out = self.run_check(FakeSlack([(429, {}, )] * 4))
        self.assertEqual(out["rollout"], "unknown")
        self.assertIn("429", out["rollout_detail"])

    def test_the_copy_matches_the_canonical_helper(self):
        canonical = _HERE.parent / "plugins/shared/skills/read-slack/scripts/slack_read.py"
        self.assertEqual((_SCRIPTS / "slack_read.py").read_bytes(), canonical.read_bytes())


class TagTime(unittest.TestCase):
    def test_reads_the_local_annotated_tag(self):
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "GIT_AUTHOR_DATE": "2026-10-06T07:51:00Z",
                   "GIT_COMMITTER_DATE": "2026-10-06T07:51:00Z",
                   "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
            for cmd in (["git", "init", "-q"],
                        ["git", "commit", "-q", "--allow-empty", "-m", "x"],
                        ["git", "tag", "-a", "v2.83.0", "-m", "Release 2.83.0"]):
                subprocess.run(cmd, cwd=d, env=env, check=True, capture_output=True)
            self.assertEqual(rollout.tag_time(d, "AuraHealth/UserService", "v2.83.0"),
                             1791273060.0)

    def test_none_when_neither_git_nor_github_answers(self):
        with mock.patch.object(rollout, "_out", return_value=""):
            self.assertIsNone(rollout.tag_time("/x", "AuraHealth/UserService", "v1"))

    def test_none_when_gh_raises(self):
        with mock.patch.object(rollout, "_out",
                               side_effect=["", subprocess.TimeoutExpired("gh", 30)]):
            self.assertIsNone(rollout.tag_time("/x", "AuraHealth/UserService", "v1"))


class VerifyWiring(unittest.TestCase):
    def verify(self, ci, roll=None):
        with mock.patch.object(release, "_verify_ci", return_value=dict(ci)), \
             mock.patch.object(rollout, "rollout_check",
                               return_value=roll or {"rollout": "complete"}) as rc:
            return release.verify(shape("AuraHealth/UserService"), "v2.83.0"), rc

    def test_asks_spinnaker_only_after_the_image_can_have_been_pushed(self):
        for status, asked in (("success", True), ("unverified", True),
                              ("running", False), ("failed", False),
                              ("no-pipeline", False)):
            out, rc = self.verify({"status": status})
            self.assertEqual(rc.called, asked, status)
            self.assertEqual("rollout" in out, asked, status)

    def test_a_failed_rollout_fails_the_exit_code(self):
        out, _ = self.verify({"status": "success"}, {"rollout": "failed"})
        self.assertEqual(release.verify_exit(out), 1)

    def test_exit_codes_otherwise_follow_ci(self):
        out, _ = self.verify({"status": "success"})
        self.assertEqual(release.verify_exit(out), 0)
        out, _ = self.verify({"status": "unverified"})
        self.assertEqual(release.verify_exit(out), 1)

    def test_completed_rollout_drops_the_contradicting_unconfirmed_line(self):
        out = {"status": "success", "jobs": {}, "rollout": "complete",
               "rollout_detail": "done", "execution": "https://spin/x",
               "deployed": "unknown", "serving_probe": False, "deploy_detail": "no probe"}
        text = release.verify_lines(out, "v2.83.0")
        self.assertIn("rolled out: done — https://spin/x", text)
        self.assertNotIn("UNCONFIRMED", text)

    def test_a_probe_that_could_not_run_still_says_so(self):
        # IntelligenceService: Spinnaker finished, but the ledger read failed.
        out = {"status": "success", "jobs": {}, "rollout": "complete",
               "rollout_detail": "done", "deployed": "unknown",
               "deploy_detail": "AURA_API_TOKEN not set"}
        self.assertIn("deploy UNCONFIRMED", release.verify_lines(out, "v1.0.0"))

    def test_failed_and_not_started_are_loud(self):
        for state, word in (("failed", "ROLLOUT FAILED"),
                            ("not-started", "rollout NOT STARTED"),
                            ("unknown", "rollout UNCONFIRMED")):
            out = {"status": "success", "jobs": {}, "rollout": state,
                   "rollout_detail": "x"}
            self.assertIn(word, release.verify_lines(out, "v1"))


class CommandLine(unittest.TestCase):
    """`verify --deployment-log` goes through main(), the path a user runs."""

    def test_deployment_log_flag_reaches_the_check(self):
        argv = ["release.py", "verify", "/repo", "--tag", "v2.83.0", "--json",
                "--deployment-log", str(_FIX / "spinnaker_deployment_mcp.json")]
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", argv), \
             mock.patch.object(release, "detect",
                               return_value=shape("AuraHealth/UserService")), \
             mock.patch.object(release, "_verify_ci",
                               return_value={"status": "unverified", "detail": "no token"}), \
             mock.patch.object(rollout, "tag_time", return_value=V2_83_0_TAGGED), \
             mock.patch.dict(os.environ, {}, clear=True), \
             redirect_stdout(buf), self.assertRaises(SystemExit):
            release.main()
        out = json.loads(buf.getvalue())
        self.assertEqual(out["rollout"], "complete")
        self.assertIn(V2_83_0_EXECUTION, out["execution"])


if __name__ == "__main__":
    unittest.main()
