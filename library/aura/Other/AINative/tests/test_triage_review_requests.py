"""`eng:triage-review-requests`'s Bugbot gate, classified from real GitHub GraphQL nodes.

The fixtures under `fixtures/triage_review_requests/` were captured with the script's own
`fetch_pr` (bodies trimmed), so the classifier is fed what GitHub actually emits — a `cursor`
Bot login, `originalCommit` vs re-anchored `commit`, a rollup mixing CheckRun and
StatusContext. Each case mutates one field of a real node rather than hand-building one.

`send-back` posts a review as the user, so its refusals are the load-bearing half: a moved
head, a PR that now passes, or a draft must never reach `gh pr review`. `remove-self` is
held to the same standard: no other reviewer, a required or unreadable code-owner rule, or a
request that isn't a code-owner one must never reach `gh pr edit`. `gh` is stubbed at its
one chokepoint, `_gh`.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "plugins", "eng", "skills", "triage-review-requests", "scripts"))

import triage as t  # noqa: E402

FIX = os.path.join(REPO, "tests", "fixtures", "triage_review_requests")
ME = "jcurlier"


def load(name: str) -> dict:
    with open(os.path.join(FIX, f"{name}.json"), encoding="utf-8") as f:
        return json.load(f)


def status(pr: dict) -> str:
    return t.classify(pr, ME)["status"]


def bot_thread(commit: str, resolved: bool = False, login: str = "cursor") -> dict:
    return {"isResolved": resolved, "comments": {"nodes": [{
        "author": {"login": login}, "originalCommit": {"oid": commit},
        "body": f"<sup>Reviewed by [Cursor Bugbot](https://cursor.com/bugbot) for commit {commit}.</sup>"}]}}


def set_check(pr: dict, status_: str) -> dict:
    nodes = pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]["nodes"]
    for n in nodes:
        if n.get("name") == "Cursor Bugbot":
            n["status"] = status_
    return pr


class RealNodes(unittest.TestCase):
    """The captured PRs classify as they did live."""

    def test_reviewed_on_head_is_ready(self):
        self.assertEqual(status(load("ready")), t.READY)

    def test_review_on_an_older_commit_is_stale(self):
        self.assertEqual(status(load("stale")), t.STALE)

    def test_no_bugbot_at_all_is_missing(self):
        self.assertEqual(status(load("missing")), t.MISSING)

    def test_resolved_findings_do_not_block(self):
        self.assertEqual(status(load("threads")), t.READY)

    def test_fixtures_carry_the_real_bot_login(self):
        logins = {r["author"]["login"] for r in load("ready")["reviews"]["nodes"]}
        self.assertIn("cursor", logins)  # GraphQL drops the `[bot]` suffix REST carries


class Findings(unittest.TestCase):
    def test_unresolved_finding_raised_on_head_fails(self):
        pr = load("ready")
        pr["reviewThreads"]["nodes"].append(bot_thread(pr["headRefOid"]))
        row = t.classify(pr, ME)
        self.assertEqual(row["status"], t.FINDINGS)
        self.assertEqual(row["open_findings_on_head"], 1)

    def test_unresolved_finding_from_an_older_commit_is_counted_not_failed(self):
        pr = load("ready")
        pr["reviewThreads"]["nodes"].append(bot_thread("0" * 40))
        row = t.classify(pr, ME)
        self.assertEqual(row["status"], t.READY)
        self.assertEqual(row["open_findings_older"], 1)

    def test_reanchored_old_finding_is_not_read_as_raised_on_head(self):
        # GitHub moves an old finding's `commit` to head; `originalCommit` keeps the truth.
        pr = load("threads")
        th = pr["reviewThreads"]["nodes"][0]
        th["isResolved"] = False
        th["comments"]["nodes"][0]["commit"] = {"oid": pr["headRefOid"]}
        self.assertEqual(status(pr), t.READY)

    def test_footer_is_the_fallback_when_original_commit_is_missing(self):
        pr = load("ready")
        th = bot_thread(pr["headRefOid"])
        th["comments"]["nodes"][0]["originalCommit"] = None
        pr["reviewThreads"]["nodes"].append(th)
        self.assertEqual(status(pr), t.FINDINGS)

    def test_a_human_thread_on_head_is_not_a_bugbot_finding(self):
        pr = load("ready")
        pr["reviewThreads"]["nodes"].append(bot_thread(pr["headRefOid"], login="rohanx96"))
        self.assertEqual(status(pr), t.READY)

    def test_resolved_finding_on_head_passes(self):
        pr = load("ready")
        pr["reviewThreads"]["nodes"].append(bot_thread(pr["headRefOid"], resolved=True))
        self.assertEqual(status(pr), t.READY)


class CompletionSignal(unittest.TestCase):
    def test_running_check_is_never_judged(self):
        # Even with an open finding on head: more may land, so wait.
        pr = set_check(load("ready"), "IN_PROGRESS")
        pr["reviewThreads"]["nodes"].append(bot_thread(pr["headRefOid"]))
        self.assertEqual(status(pr), t.RUNNING)

    def test_completed_check_on_head_counts_without_a_review(self):
        pr = load("ready")
        pr["reviews"]["nodes"] = [r for r in pr["reviews"]["nodes"] if r["author"]["login"] != "cursor"]
        self.assertEqual(status(pr), t.READY)

    def test_new_push_after_a_clean_review_is_stale(self):
        pr = load("ready")
        pr["headRefOid"] = "f" * 40
        pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"] = None
        self.assertEqual(status(pr), t.STALE)

    def test_review_body_is_never_read(self):
        # Bugbot empties superseded review bodies; the verdict must not move.
        pr = load("ready")
        for r in pr["reviews"]["nodes"]:
            r["body"] = ""
        self.assertEqual(status(pr), t.READY)


class AlreadySentBack(unittest.TestCase):
    def mine(self, pr, state, commit=None):
        pr["reviews"]["nodes"].append({"author": {"login": ME}, "state": state,
                                       "submittedAt": "2099-01-01T00:00:00Z",
                                       "commit": {"oid": commit or pr["headRefOid"]}})
        return pr

    def test_my_changes_requested_on_head_is_skipped(self):
        self.assertEqual(status(self.mine(load("stale"), "CHANGES_REQUESTED")), t.SENT_BACK)

    def test_my_changes_requested_on_an_older_head_does_not_skip(self):
        self.assertEqual(status(self.mine(load("stale"), "CHANGES_REQUESTED", "0" * 40)), t.STALE)

    def test_a_later_approval_on_head_supersedes_it(self):
        pr = self.mine(load("ready"), "CHANGES_REQUESTED")
        pr["reviews"]["nodes"][-1]["submittedAt"] = "2098-01-01T00:00:00Z"
        self.assertEqual(status(self.mine(pr, "APPROVED")), t.READY)


class SendBack(unittest.TestCase):
    URL = "https://github.com/AuraHealth/AuraConsumerApp/pull/1783"

    def run_send_back(self, pr, head, dry_run=False):
        calls = []

        def fake_gh(args, stdin=None):
            calls.append((args, stdin))
            if args[:2] == ["api", "user"]:
                return ME + "\n"
            if args[:2] == ["api", "graphql"]:
                return json.dumps({"data": {"repository": {"pullRequest": pr}}})
            if args[:2] == ["pr", "review"]:
                return ""
            raise AssertionError(f"unexpected gh call {args}")

        with mock.patch.object(t, "_gh", fake_gh), mock.patch("sys.stdout"):
            code = t.send_back(self.URL, head, dry_run)
        posted = [c for c in calls if c[0][:2] == ["pr", "review"]]
        return code, posted

    def test_failing_pr_posts_request_changes(self):
        pr = load("stale")
        code, posted = self.run_send_back(pr, pr["headRefOid"][:7])
        self.assertEqual(code, 0)
        self.assertEqual(len(posted), 1)
        args, body = posted[0]
        self.assertIn("--request-changes", args)
        self.assertIn(pr["headRefOid"][:7], body)
        self.assertIn("bugbot run", body)

    def test_moved_head_refuses(self):
        code, posted = self.run_send_back(load("stale"), "deadbee")
        self.assertEqual((code, posted), (3, []))

    def test_empty_head_refuses(self):
        code, posted = self.run_send_back(load("stale"), "")
        self.assertEqual((code, posted), (3, []))

    def test_pr_that_now_passes_refuses(self):
        pr = load("ready")
        code, posted = self.run_send_back(pr, pr["headRefOid"])
        self.assertEqual((code, posted), (3, []))

    def test_running_bugbot_refuses(self):
        pr = set_check(load("ready"), "QUEUED")
        pr["reviewThreads"]["nodes"].append(bot_thread(pr["headRefOid"]))
        code, posted = self.run_send_back(pr, pr["headRefOid"])
        self.assertEqual((code, posted), (3, []))

    def test_draft_refuses(self):
        pr = load("stale")
        pr["isDraft"] = True
        code, posted = self.run_send_back(pr, pr["headRefOid"])
        self.assertEqual((code, posted), (3, []))

    def test_dry_run_posts_nothing(self):
        pr = load("missing")
        code, posted = self.run_send_back(pr, pr["headRefOid"], dry_run=True)
        self.assertEqual((code, posted), (0, []))


class Body(unittest.TestCase):
    def test_each_failing_status_names_its_reason(self):
        head = "a" * 40
        bodies = {s: t.send_back_body({"status": s, "head": head, "open_findings_on_head": 2})
                  for s in t.FAIL}
        self.assertIn("2 finding(s)", bodies[t.FINDINGS])
        self.assertIn("older commit", bodies[t.STALE])
        self.assertIn("hasn't run", bodies[t.MISSING])
        for b in bodies.values():
            self.assertIn("aaaaaaa", b)
            self.assertIn("eng:triage-review-requests:", b)  # version resolved from SKILL.md


class GhTransport(unittest.TestCase):
    def test_gh_is_run_as_utf8_whatever_the_locale(self):
        # Review bodies carry an em-dash and titles arbitrary Unicode; cp1252 / LC_ALL=C would raise.
        with mock.patch.object(t.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="ok", stderr="")
            t._gh(["api", "user"], stdin="Sending this back \u2014 please run Bugbot")
        self.assertEqual(run.call_args.kwargs.get("encoding"), "utf-8")


def request(name: str, typename: str = "User", as_code_owner: bool = False) -> dict:
    key = "slug" if typename == "Team" else "login"
    return {"asCodeOwner": as_code_owner,
            "requestedReviewer": {"__typename": typename, key: name}}


def with_requests(pr: dict, *nodes: dict) -> dict:
    pr["reviewRequests"] = {"nodes": list(nodes)}
    pr["baseRefName"] = "main"
    return pr


class CodeOwner(unittest.TestCase):
    """`asCodeOwner` on the viewer's own request, plus another human, marks a step-back."""

    def test_code_owner_with_another_requested_reviewer_is_a_candidate(self):
        pr = with_requests(load("ready"), request(ME, as_code_owner=True), request("naveed-rana"))
        row = t.classify(pr, ME)
        self.assertTrue(row["step_back"])
        self.assertEqual(row["other_reviewers"], ["naveed-rana"])

    def test_code_owner_alone_is_not_a_candidate(self):
        row = t.classify(with_requests(load("ready"), request(ME, as_code_owner=True)), ME)
        self.assertTrue(row["code_owner_request"])
        self.assertFalse(row["step_back"])

    def test_a_manual_request_is_not_a_code_owner_request(self):
        pr = with_requests(load("ready"), request(ME), request("naveed-rana"))
        self.assertFalse(t.classify(pr, ME)["step_back"])

    def test_a_team_code_owner_request_cannot_be_withdrawn_for_one_member(self):
        pr = with_requests(load("ready"), request("mobile", "Team", True), request("naveed-rana"))
        self.assertFalse(t.classify(pr, ME)["code_owner_request"])

    def test_another_team_counts_as_someone_else(self):
        pr = with_requests(load("ready"), request(ME, as_code_owner=True), request("mobile", "Team"))
        self.assertEqual(t.classify(pr, ME)["other_reviewers"], ["team:mobile"])

    def test_a_human_who_already_reviewed_counts_bots_and_the_author_do_not(self):
        pr = with_requests(load("ready"), request(ME, as_code_owner=True))
        author = pr["author"]["login"]
        pr["reviews"]["nodes"] += [
            {"author": {"__typename": "User", "login": "rohanx96"}, "state": "APPROVED"},
            {"author": {"__typename": "User", "login": author}, "state": "COMMENTED"},
            {"author": {"__typename": "Bot", "login": "copilot-pull-request-reviewer"},
             "state": "COMMENTED"},
        ]
        self.assertEqual(t.classify(pr, ME)["other_reviewers"], ["rohanx96"])

    def test_nodes_without_review_requests_still_classify(self):
        # Older captures carry no `reviewRequests`; the gate must not depend on it.
        row = t.classify(load("ready"), ME)
        self.assertEqual((row["status"], row["step_back"]), (t.READY, False))


class RemoveSelf(unittest.TestCase):
    URL = "https://github.com/AuraHealth/Aura/pull/7284"

    def run_remove(self, pr, rules, dry_run=False, allow_required=False):
        calls = []

        def fake_gh(args, stdin=None):
            calls.append(args)
            if args[:2] == ["api", "user"]:
                return ME + "\n"
            if args[:2] == ["api", "graphql"]:
                return json.dumps({"data": {"repository": {"pullRequest": pr}}})
            if args[0] == "api" and any("/rules/branches/" in a for a in args):
                if rules is None:
                    raise t.GhError("HTTP 403")
                self.assertIn("--paginate", args)
                # Pages arrive flattened by `--jq '.[]'`: one compact rule per line.
                return "\n".join(json.dumps(r) for r in rules) + "\n"
            if args[:2] == ["pr", "edit"]:
                return ""
            raise AssertionError(f"unexpected gh call {args}")

        with mock.patch.object(t, "_gh", fake_gh), mock.patch("sys.stdout"):
            code = t.remove_self(self.URL, dry_run, allow_required)
        return code, [c for c in calls if c[:2] == ["pr", "edit"]]

    def candidate(self):
        return with_requests(load("ready"), request(ME, as_code_owner=True), request("naveed-rana"))

    NOT_REQUIRED = [{"type": "pull_request", "parameters": {"require_code_owner_review": False}}]
    REQUIRED = [{"type": "pull_request", "parameters": {"require_code_owner_review": True}}]

    def test_candidate_is_removed(self):
        code, edits = self.run_remove(self.candidate(), self.NOT_REQUIRED)
        self.assertEqual(code, 0)
        # The PR number comes from the URL the user confirmed, not from the fetched node.
        self.assertEqual(edits, [["pr", "edit", "7284", "--repo", "AuraHealth/Aura",
                                  "--remove-reviewer", ME]])

    def test_required_code_owner_review_refuses(self):
        self.assertEqual(self.run_remove(self.candidate(), self.REQUIRED), (3, []))

    def test_required_proceeds_only_when_allowed(self):
        code, edits = self.run_remove(self.candidate(), self.REQUIRED, allow_required=True)
        self.assertEqual((code, len(edits)), (0, 1))

    def test_a_required_rule_after_other_rules_is_found(self):
        rules = [{"type": "deletion"}] * 40 + self.REQUIRED
        self.assertEqual(self.run_remove(self.candidate(), rules), (3, []))

    def test_unreadable_rulesets_refuse(self):
        self.assertEqual(self.run_remove(self.candidate(), None), (3, []))

    def test_no_one_else_reviewing_refuses(self):
        pr = with_requests(load("ready"), request(ME, as_code_owner=True))
        self.assertEqual(self.run_remove(pr, self.NOT_REQUIRED), (3, []))

    def test_no_longer_requested_refuses(self):
        pr = with_requests(load("ready"), request("naveed-rana"))
        self.assertEqual(self.run_remove(pr, self.NOT_REQUIRED), (3, []))

    def test_draft_refuses(self):
        pr = self.candidate()
        pr["isDraft"] = True
        self.assertEqual(self.run_remove(pr, self.NOT_REQUIRED), (3, []))

    def test_dry_run_changes_nothing(self):
        self.assertEqual(self.run_remove(self.candidate(), self.NOT_REQUIRED, dry_run=True), (0, []))


class Triage(unittest.TestCase):
    def test_search_lag_draft_is_skipped_and_errors_are_reported(self):
        good, draft = load("stale"), copy.deepcopy(load("stale"))
        draft["isDraft"] = True
        hits = [{"url": "https://github.com/A/B/pull/1"}, {"url": "https://github.com/A/B/pull/2"},
                {"url": "https://github.com/A/B/pull/3"}]
        nodes = {1: good, 2: draft}

        def fake_fetch(owner, name, number):
            if number not in nodes:
                raise t.GhError("Could not resolve to a PullRequest")
            return nodes[number]

        with mock.patch.object(t, "viewer_login", return_value=ME), \
             mock.patch.object(t, "search_review_requests", return_value=hits), \
             mock.patch.object(t, "fetch_pr", fake_fetch):
            res = t.triage(100, mine=False, suggest=False)
        self.assertEqual([r["status"] for r in res["rows"]], [t.STALE])
        self.assertEqual(len(res["errors"]), 1)  # reported, never dropped as "ready"
        self.assertIn("Could not classify", t.render(res))


NOW = t.datetime(2026, 10, 8, 12, 0, tzinfo=t.timezone.utc)


def human_review(login: str, state: str, at: str) -> dict:
    return {"author": {"__typename": "User", "login": login}, "state": state,
            "submittedAt": at, "commit": {"oid": "0" * 40}}


def with_head_at(pr: dict, at: str) -> dict:
    pr["commits"]["nodes"][0]["commit"]["committedDate"] = at
    return pr


def unreviewed(pr: dict) -> dict:
    """Drop the human reviews a captured node carries, keeping Bugbot's."""
    pr["reviews"]["nodes"] = [r for r in pr["reviews"]["nodes"] if t.BOT_RE.match(r["author"]["login"])]
    return pr


def changes_requested_then_push(push_at: str = "2026-10-05T11:33:22Z") -> dict:
    pr = with_head_at(load("ready"), push_at)
    pr["reviews"]["nodes"].append(human_review("rohanx96", "CHANGES_REQUESTED", "2026-10-05T10:56:27Z"))
    return pr


class Stalled(unittest.TestCase):
    """A reviewer who requested changes and went quiet after the author pushed."""

    def test_push_after_request_changes_is_stalled(self):
        self.assertEqual(t.stalled_reviewers(changes_requested_then_push(), NOW),
                         [{"reviewer": "rohanx96", "days": 3}])

    def test_a_push_younger_than_the_threshold_is_not_stalled(self):
        self.assertEqual(t.stalled_reviewers(changes_requested_then_push("2026-10-07T12:00:00Z"), NOW), [])

    def test_a_reply_after_the_push_means_still_engaged(self):
        pr = changes_requested_then_push()
        pr["reviews"]["nodes"].append(human_review("rohanx96", "COMMENTED", "2026-10-06T09:00:00Z"))
        self.assertEqual(t.stalled_reviewers(pr, NOW), [])

    def test_approval_after_request_changes_is_not_stalled(self):
        pr = changes_requested_then_push("2026-10-01T00:00:00Z")
        pr["reviews"]["nodes"][-1]["submittedAt"] = "2026-09-30T00:00:00Z"
        pr["reviews"]["nodes"].append(human_review("rohanx96", "APPROVED", "2026-09-30T06:00:00Z"))
        self.assertEqual(t.stalled_reviewers(pr, NOW), [])

    def test_no_push_since_the_review_is_not_stalled(self):
        pr = changes_requested_then_push("2026-10-05T10:00:00Z")
        self.assertEqual(t.stalled_reviewers(pr, NOW), [])

    def test_missing_commit_date_is_never_stalled(self):
        pr = changes_requested_then_push()
        pr["commits"]["nodes"][0]["commit"].pop("committedDate")
        self.assertEqual(t.stalled_reviewers(pr, NOW), [])


class Suggestions(unittest.TestCase):
    """"If you have time": someone else's Bugbot-clean PR that is unreviewed or stalled."""

    def test_unreviewed_clean_pr_is_suggested(self):
        row = t.suggestion_row(load("ready"), ME, NOW)
        self.assertEqual((row["why"], row["reviewers"]), ("no_reviewer", []))

    def test_stalled_pr_names_the_current_reviewer(self):
        row = t.suggestion_row(changes_requested_then_push(), ME, NOW)
        self.assertEqual((row["why"], row["reviewers"], row["stalled_days"]), ("stalled", ["rohanx96"], 3))

    def test_actively_reviewed_pr_is_not_suggested(self):
        pr = with_requests(load("ready"), request("rohanx96"))
        self.assertIsNone(t.suggestion_row(pr, ME, NOW))

    def test_pr_failing_the_bugbot_gate_is_not_suggested(self):
        self.assertIsNone(t.suggestion_row(load("stale"), ME, NOW))

    def test_pr_requesting_you_is_your_queue_not_a_suggestion(self):
        pr = with_requests(load("ready"), request(ME))
        self.assertIsNone(t.suggestion_row(pr, ME, NOW))

    def test_your_own_pr_is_never_a_suggestion(self):
        self.assertIsNone(t.suggestion_row(load("ready"), "devashishHp04", NOW))


class Mine(unittest.TestCase):
    """Your own PRs that nobody is reviewing."""

    def test_no_reviewer_is_reported_with_its_gate(self):
        self.assertEqual(t.unreviewed_row(unreviewed(load("stale")), ME)["status"], t.STALE)

    def test_a_requested_reviewer_drops_it(self):
        self.assertIsNone(t.unreviewed_row(with_requests(load("ready"), request("rohanx96")), ME))

    def test_a_human_review_drops_it(self):
        pr = load("ready")
        pr["reviews"]["nodes"].append(human_review("rohanx96", "COMMENTED", "2026-10-05T10:56:27Z"))
        self.assertIsNone(t.unreviewed_row(pr, ME))

    def test_bot_reviewers_do_not_count(self):
        pr = with_requests(load("ready"), request("cursor", typename="Bot"))
        self.assertIsNotNone(t.unreviewed_row(pr, ME))


class TriageSections(unittest.TestCase):
    def test_sections_render_and_skip_the_queue(self):
        queued, mine, other = load("ready"), unreviewed(load("stale")), changes_requested_then_push()
        mine["author"] = {"login": ME}
        urls = {"https://github.com/A/B/pull/1": queued, "https://github.com/A/B/pull/2": mine,
                "https://github.com/A/B/pull/3": other}
        for url, pr in urls.items():
            pr["url"] = url  # search hits and live nodes agree on the URL, as on GitHub

        def fake_fetch(owner, name, number):
            return copy.deepcopy(urls[f"https://github.com/{owner}/{name}/pull/{number}"])

        with mock.patch.object(t, "viewer_login", return_value=ME), \
             mock.patch.object(t, "datetime", wraps=t.datetime) as dt, \
             mock.patch.object(t, "search_review_requests", return_value=[{"url": "https://github.com/A/B/pull/1"}]), \
             mock.patch.object(t, "search_my_open_prs", return_value=[{"url": "https://github.com/A/B/pull/2"}]), \
             mock.patch.object(t, "recent_authored_owners", return_value={"A"}), \
             mock.patch.object(t, "search_owner_open_prs",
                               return_value=[{"url": u} for u in urls]), \
             mock.patch.object(t, "fetch_pr", fake_fetch):
            dt.now.return_value = NOW
            res = t.triage(100)
        self.assertEqual([r["number"] for r in res["mine"]], [mine["number"]])
        # The queued PR is not repeated as a suggestion; the stalled one is.
        self.assertEqual([r["why"] for r in res["suggestions"]], ["stalled"])
        self.assertEqual(res["suggest_owners"], ["A"])
        out = t.render(res)
        self.assertIn("### Your PRs with no reviewer", out)
        self.assertIn("### If you have time", out)
        self.assertIn("@rohanx96", out)

    def test_owner_override_skips_discovery(self):
        with mock.patch.object(t, "viewer_login", return_value=ME), \
             mock.patch.object(t, "search_review_requests", return_value=[]), \
             mock.patch.object(t, "search_my_open_prs", return_value=[]), \
             mock.patch.object(t, "recent_authored_owners", side_effect=AssertionError("discovery ran")), \
             mock.patch.object(t, "search_owner_open_prs", return_value=[]):
            res = t.triage(100, owners=["AuraHealth"])
        self.assertEqual(res["suggest_owners"], ["AuraHealth"])

    def test_each_section_is_a_pr_description_status_link_table(self):
        res = {"viewer": ME, "rows": [t.classify(load("ready"), ME)], "counts": {t.READY: 1},
               "errors": [], "step_back": 0, "search_capped": False, "limit": 100,
               "mine": [t.classify(load("stale"), ME)], "mine_capped": False,
               "suggestions": [], "suggest_owners": ["A"], "suggest_capped": False,
               "suggest_limit": 50}
        out = t.render(res)
        self.assertEqual(out.count("| PR | Description | Status | Link |"), 2)
        self.assertIn("[link](", out)
        self.assertIn("| ✅ Ready |", out)
        # An empty, uncapped section is left out rather than printed as "nothing".
        self.assertNotIn("### If you have time", out)

    def test_empty_queue_says_so(self):
        res = {"viewer": ME, "rows": [], "counts": {}, "errors": [], "step_back": 0,
               "search_capped": False, "limit": 100, "mine": [], "mine_capped": False,
               "suggestions": None}
        out = t.render(res)
        self.assertIn("Nothing is waiting on your review.", out)
        self.assertNotIn("### Your PRs with no reviewer", out)

    def test_capped_own_pr_search_never_reads_as_all_clear(self):
        hits = [{"url": f"https://github.com/A/B/pull/{n}"} for n in range(2)]
        reviewed = with_requests(load("ready"), request("rohanx96"))
        with mock.patch.object(t, "viewer_login", return_value=ME), \
             mock.patch.object(t, "search_review_requests", return_value=[]), \
             mock.patch.object(t, "search_my_open_prs", return_value=hits), \
             mock.patch.object(t, "fetch_pr", return_value=reviewed):
            res = t.triage(2, suggest=False)
        out = t.render(res)
        self.assertTrue(res["mine_capped"])
        self.assertNotIn("every open, ready PR of yours has a reviewer", out)
        self.assertIn("None among your 2 most recently updated open PRs", out)
        self.assertNotIn("| PR |", out)

    def test_capped_empty_suggestions_say_none_in_one_line(self):
        res = {"viewer": ME, "rows": [], "counts": {}, "errors": [], "step_back": 0,
               "search_capped": False, "limit": 100, "mine": None, "suggestions": [],
               "suggest_owners": ["AuraHealth"], "suggest_capped": True, "suggest_limit": 50}
        out = t.render(res)
        section = out.split("### If you have time", 1)[1]
        self.assertIn("None of the 50 most recently updated PRs per owner in AuraHealth", section)
        self.assertNotIn("optional, not your queue", section)
        self.assertEqual(section.count("⚠️"), 1)


if __name__ == "__main__":
    unittest.main()
