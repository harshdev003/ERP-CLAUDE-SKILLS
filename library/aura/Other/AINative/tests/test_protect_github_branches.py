#!/usr/bin/env python3
"""Tests for eng:protect-github-branches pure decision functions.

These are the branches real data does not reach. Every defect found in review on
this skill lived in exactly these functions while a happy-path `audit` against an
already-conforming repo passed each time — which is the failure mode tests/README.md
describes: happy-path verification signs off on broken branches.

Stdlib unittest, no network, no keys.
"""
import os
import sys
import unittest

SKILL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "plugins", "eng", "skills", "protect-github-branches", "scripts")
sys.path.insert(0, SKILL)

from protect import (  # noqa: E402
    ref_coverage, covers_default, conforms, payload, merge_status_checks,
    status_check_rule, classic_checks_from,
)


class RefCoverage(unittest.TestCase):
    """A ruleset that does not reach the default branch protects it not at all."""

    def test_sentinels_and_exact_refs_cover(self):
        for inc in (["~DEFAULT_BRANCH"], ["~ALL"], ["refs/heads/main"]):
            self.assertEqual(ref_coverage(inc, [], "main"), "covers", inc)

    def test_other_refs_do_not_cover(self):
        for inc in (["refs/tags/v1"], ["refs/heads/release"], []):
            self.assertEqual(ref_coverage(inc, [], "main"), "no", inc)

    def test_exclude_of_the_default_branch_wins(self):
        self.assertEqual(ref_coverage(["~ALL"], ["~DEFAULT_BRANCH"], "main"), "no")
        self.assertEqual(ref_coverage(["~ALL"], ["refs/heads/main"], "main"), "no")

    def test_globs_are_undecidable_not_guessed(self):
        # Deliberately NOT resolved. Reimplementing GitHub's glob grammar produced
        # a defect every round; an undecidable pattern is reported, not guessed.
        for inc in (["refs/heads/*"], ["refs/heads/[mr]ain"], ["refs/heads/mai?"]):
            self.assertEqual(ref_coverage(inc, [], "main"), "uncertain", inc)
        self.assertEqual(ref_coverage(["~ALL"], ["refs/heads/ma*"], "main"), "uncertain")

    def test_uncertain_is_not_protection(self):
        """The fail-closed direction: unknown coverage must never read as protected."""
        self.assertFalse(covers_default(["refs/heads/*"], [], "main"))

    def test_non_default_branch_name(self):
        self.assertEqual(ref_coverage(["refs/heads/master"], [], "master"), "covers")
        self.assertEqual(ref_coverage(["refs/heads/main"], [], "master"), "no")


class Conforms(unittest.TestCase):
    """PROTECTED does not imply a pull_request rule — apply must not skip on it."""

    PROT = {"verdict": "PROTECTED", "rules": ["deletion", "non_fast_forward"]}
    PROT_PR = {"verdict": "PROTECTED",
               "rules": ["deletion", "non_fast_forward", "pull_request"]}

    def test_missing_pull_request_is_not_conforming(self):
        self.assertFalse(conforms(self.PROT, want_pr=True))

    def test_present_pull_request_is_conforming(self):
        self.assertTrue(conforms(self.PROT_PR, want_pr=True))

    def test_want_pr_false_ignores_the_rule(self):
        self.assertTrue(conforms(self.PROT, want_pr=False))

    def test_weak_never_conforms(self):
        self.assertFalse(conforms({"verdict": "WEAK", "rules": ["pull_request"]}, True))


class Payload(unittest.TestCase):
    """Narrowing an existing ruleset's ref targets is a downgrade."""

    def test_existing_targets_are_preserved(self):
        keep = {"include": ["~DEFAULT_BRANCH", "refs/heads/rel/*"],
                "exclude": ["refs/heads/tmp/*"]}
        self.assertEqual(payload("main", [], keep)["conditions"]["ref_name"], keep)

    def test_new_ruleset_defaults_to_the_default_branch(self):
        cond = payload("main", [])["conditions"]["ref_name"]
        self.assertEqual(cond["include"], ["~DEFAULT_BRANCH"])
        self.assertEqual(cond["exclude"], [])

    def test_bypass_actors_always_empty(self):
        self.assertEqual(payload("main", [], {"include": ["~ALL"]})["bypass_actors"], [])

    def test_enforcement_always_active(self):
        self.assertEqual(payload("main", [])["enforcement"], "active")




class _StubGh:
    """Transport stub. The mode entry points were never executed by any smoke test —
    a stray paste left `migrate_one` raising NameError on every call while
    `audit` kept passing. These exercise the paths real data does not reach."""

    def __init__(self, rulesets=None, detail=None, classic=None, classic_status=404):
        self.rulesets = rulesets or []
        self.detail = detail or {}
        self.classic = classic
        self.classic_status = classic_status
        self.writes = []

    def gh(self, path, *, method="GET", input_json=None, **kw):
        if method != "GET":
            self.writes.append((method, path, input_json))
            return {"id": 999}
        if path.endswith("/rulesets"):
            return self.rulesets
        if "/rulesets/" in path:
            return self.detail[int(path.rsplit("/", 1)[1])]
        raise AssertionError(f"unexpected GET {path}")

    def gh_optional(self, path, **kw):
        if "/protection" in path:
            return self.classic
        raise AssertionError(f"unexpected optional GET {path}")


REPO = {"name": "R", "owner": {"login": "O"}, "default_branch": "main"}


def _rs(rid, rules, include=("~DEFAULT_BRANCH",), enforcement="active", bypass=()):
    return {"id": rid, "name": "main", "target": "branch", "enforcement": enforcement,
            "conditions": {"ref_name": {"include": list(include), "exclude": []}},
            "rules": [{"type": t} for t in rules], "bypass_actors": list(bypass)}


class ModeEntryPoints(unittest.TestCase):
    def _patch(self, stub):
        import protect
        self._orig = (protect.gh, protect.gh_optional)
        protect.gh, protect.gh_optional = stub.gh, stub.gh_optional
        self.addCleanup(lambda: setattr_pair(protect, self._orig))

    def test_migrate_one_runs_without_nameerror(self):
        """Regression: a stray paste made this raise NameError on every call."""
        import protect
        stub = _StubGh(classic=None)                      # no classic protection
        self._patch(stub)
        out = protect.migrate_one(REPO, dry=True)
        self.assertEqual(out["action"], "skip")

    def test_migrate_dry_run_writes_nothing(self):
        import protect
        stub = _StubGh(classic={"allow_force_pushes": {"enabled": False},
                                "enforce_admins": {"enabled": True}})
        self._patch(stub)
        out = protect.migrate_one(REPO, dry=True)
        self.assertEqual(out["action"], "would-migrate")
        self.assertEqual(stub.writes, [], "dry-run must not mutate")

    def test_inspect_reports_uncertain_for_glob(self):
        import protect
        rs = _rs(1, ["deletion", "non_fast_forward"], include=["refs/heads/*"])
        stub = _StubGh(rulesets=[{"id": 1}], detail={1: rs})
        self._patch(stub)
        self.assertEqual(protect.inspect(REPO)["verdict"], "UNCERTAIN")

    def test_inspect_weak_when_force_push_rule_absent(self):
        import protect
        rs = _rs(1, ["deletion"])
        stub = _StubGh(rulesets=[{"id": 1}], detail={1: rs})
        self._patch(stub)
        row = protect.inspect(REPO)
        self.assertEqual(row["verdict"], "WEAK")
        self.assertIn("non_fast_forward", row["detail"])

    def test_inspect_weak_on_bypass_actor(self):
        import protect
        rs = _rs(1, ["deletion", "non_fast_forward"], bypass=[{"actor_id": 5}])
        stub = _StubGh(rulesets=[{"id": 1}], detail={1: rs})
        self._patch(stub)
        self.assertEqual(protect.inspect(REPO)["verdict"], "WEAK")

    def test_apply_dry_run_writes_nothing(self):
        import protect
        rs = _rs(1, ["deletion", "non_fast_forward"])      # no pull_request rule
        stub = _StubGh(rulesets=[{"id": 1}], detail={1: rs})
        self._patch(stub)
        out = protect.apply_one(REPO, approvals=0, dry=True)
        self.assertEqual(out["action"], "would-apply")
        self.assertIn("pull_request", out["rules"])
        self.assertEqual(stub.writes, [], "dry-run must not mutate")


class MergeStatusChecks(unittest.TestCase):
    """Adding a context raises the bar; replacing the set lowers it.

    Both `apply` and `migrate` write this rule and a repo can pass through both,
    so a replace in either silently discards what the other installed.
    """

    def test_appends_when_absent(self):
        rules = [{"type": "deletion"}]
        merge_status_checks(rules, ["a"], False)
        self.assertEqual([r["type"] for r in rules],
                         ["deletion", "required_status_checks"])

    def test_unions_without_duplicating(self):
        rules = [status_check_rule(["a", "b"], False)]
        merge_status_checks(rules, ["b", "c"], False)
        got = [c["context"] for c in rules[0]["parameters"]["required_status_checks"]]
        self.assertEqual(got, ["a", "b", "c"])

    def test_strict_goes_off_to_on(self):
        rules = [status_check_rule(["a"], False)]
        merge_status_checks(rules, ["a"], True)
        self.assertTrue(rules[0]["parameters"]["strict_required_status_checks_policy"])

    def test_strict_never_goes_on_to_off(self):
        """The downgrade this function exists to refuse."""
        rules = [status_check_rule(["a"], True)]
        merge_status_checks(rules, ["a"], False)
        self.assertTrue(rules[0]["parameters"]["strict_required_status_checks_policy"])

    def test_empty_contexts_is_a_noop(self):
        rules = [{"type": "deletion"}]
        merge_status_checks(rules, [], False)
        self.assertEqual(rules, [{"type": "deletion"}])


class ConformsChecks(unittest.TestCase):
    """A requested check that is absent — or strict that is off — is outstanding
    work. Reporting it conforming makes the flag a silent no-op."""

    BASE = {"verdict": "PROTECTED",
            "rules": ["deletion", "non_fast_forward", "pull_request"]}

    def test_missing_context_is_not_conforming(self):
        row = {**self.BASE, "checks": [], "checks_strict": False}
        self.assertFalse(conforms(row, True, ["ci/x"]))

    def test_present_context_conforms(self):
        row = {**self.BASE, "checks": ["ci/x"], "checks_strict": False}
        self.assertTrue(conforms(row, True, ["ci/x"]))

    def test_strict_requested_but_off_is_not_conforming(self):
        row = {**self.BASE, "checks": ["ci/x"], "checks_strict": False}
        self.assertFalse(conforms(row, True, ["ci/x"], want_strict=True))

    def test_strict_on_satisfies_a_non_strict_request(self):
        row = {**self.BASE, "checks": ["ci/x"], "checks_strict": True}
        self.assertTrue(conforms(row, True, ["ci/x"]))


class _CoverageStub:
    """Transport stub for the coverage gate."""

    def __init__(self, prs, statuses=None, checkruns=None, classic=None):
        self.prs, self.statuses = prs, statuses or {}
        self.checkruns, self.classic = checkruns or {}, classic
        self.writes = []

    def gh(self, path, *, method="GET", input_json=None, **kw):
        if method != "GET":
            self.writes.append((method, path, input_json))
            return {"id": 999}
        if "/pulls?" in path:
            return self.prs
        if "/statuses" in path:
            sha = path.split("/commits/")[1].split("/")[0]
            return [{"context": c} for c in self.statuses.get(sha, [])]
        if "/check-runs" in path:
            sha = path.split("/commits/")[1].split("/")[0]
            from urllib.parse import parse_qs, urlparse
            page = int(parse_qs(urlparse(path).query).get("page", ["1"])[0])
            all_runs = self.checkruns.get(sha, [])
            chunk = all_runs[(page - 1) * 100: page * 100]
            return {"total_count": len(all_runs),
                    "check_runs": [{"name": n} for n in chunk]}
        if path.endswith("/rulesets"):
            return []
        raise AssertionError(f"unexpected GET {path}")

    def gh_optional(self, path, **kw):
        if "/protection" in path:
            return self.classic
        raise AssertionError(f"unexpected optional GET {path}")


def _pr(num, sha, merged_at):
    return {"number": num, "head": {"sha": sha}, "merged_at": merged_at}


class MergedPrOrdering(unittest.TestCase):
    """The pulls endpoint cannot sort by merge date. Ordering by anything else
    drifts the sample to old PRs and reads a covered repo as uncovered."""

    def _patch(self, stub):
        import protect
        orig = (protect.gh, protect.gh_optional)
        protect.gh, protect.gh_optional = stub.gh, stub.gh_optional
        self.addCleanup(lambda: setattr_pair(protect, orig))

    def test_orders_by_merged_at_not_api_order(self):
        import protect
        prs = [_pr(1, "old", "2025-01-01T00:00:00Z"),      # first in API order
               _pr(2, "new", "2026-08-01T00:00:00Z"),
               _pr(3, "mid", "2026-01-01T00:00:00Z"),
               {"number": 4, "head": {"sha": "x"}, "merged_at": None}]  # never merged
        self._patch(_CoverageStub(prs))
        got = [p["number"] for p in protect.merged_prs("O/R", 3)]
        self.assertEqual(got, [2, 3, 1])

    def test_unmerged_prs_are_excluded(self):
        import protect
        self._patch(_CoverageStub([{"number": 9, "head": {"sha": "x"},
                                    "merged_at": None}]))
        self.assertEqual(protect.merged_prs("O/R", 5), [])


class CommitContextTruncation(unittest.TestCase):
    """Both endpoints stop at 30 by default. A context lost to truncation is
    indistinguishable from one that never ran, so the gate would exclude a repo
    that is in fact covered."""

    def _patch(self, stub):
        import protect
        orig = (protect.gh, protect.gh_optional)
        protect.gh, protect.gh_optional = stub.gh, stub.gh_optional
        self.addCleanup(lambda: setattr_pair(protect, orig))

    def test_pages_check_runs_past_the_first_hundred(self):
        import protect
        runs = [f"job-{i}" for i in range(150)]            # needs two pages
        self._patch(_CoverageStub([], checkruns={"s": runs}))
        got = protect.commit_contexts("O/R", "s")
        self.assertIn("job-149", got, "second page was dropped")
        self.assertEqual(len(got), 150)

    def test_collects_statuses_and_check_runs_together(self):
        import protect
        self._patch(_CoverageStub([], statuses={"s": ["ci/circleci: test"]},
                                  checkruns={"s": ["lint"]}))
        self.assertEqual(protect.commit_contexts("O/R", "s"),
                         {"ci/circleci: test", "lint"})


class CoverageGate(unittest.TestCase):
    """An unreported context blocks every future PR forever, with no admin
    override — so the gate must fail closed."""

    def _patch(self, stub):
        import protect
        orig = (protect.gh, protect.gh_optional)
        protect.gh, protect.gh_optional = stub.gh, stub.gh_optional
        self.addCleanup(lambda: setattr_pair(protect, orig))

    def test_context_reporting_everywhere_passes(self):
        import protect
        prs = [_pr(1, "a", "2026-08-01T00:00:00Z"), _pr(2, "b", "2026-07-01T00:00:00Z")]
        self._patch(_CoverageStub(prs, statuses={"a": ["ci/x"], "b": ["ci/x"]}))
        self.assertEqual(protect.check_coverage("O/R", ["ci/x"], 10)["missing"], [])

    def test_one_miss_is_enough_to_refuse(self):
        import protect
        prs = [_pr(1, "a", "2026-08-01T00:00:00Z"), _pr(2, "b", "2026-07-01T00:00:00Z")]
        self._patch(_CoverageStub(prs, statuses={"a": ["ci/x"], "b": []}))
        self.assertEqual(protect.check_coverage("O/R", ["ci/x"], 10)["missing"], ["ci/x"])

    def test_no_merged_prs_fails_closed(self):
        """Nothing to learn from is not evidence of safety."""
        import protect
        self._patch(_CoverageStub([]))
        cov = protect.check_coverage("O/R", ["ci/x"], 10)
        self.assertEqual(cov["sampled"], 0)
        self.assertEqual(cov["missing"], ["ci/x"])


class ClassicChecksSurviveApply(unittest.TestCase):
    """`apply` seeding a ruleset on a classic-protected repo, then `migrate`
    deleting classic, must not drop a context only classic carried."""

    def _patch(self, stub):
        import protect
        orig = (protect.gh, protect.gh_optional)
        protect.gh, protect.gh_optional = stub.gh, stub.gh_optional
        self.addCleanup(lambda: setattr_pair(protect, orig))

    def test_apply_carries_classic_contexts_into_the_new_ruleset(self):
        import protect
        classic = {"allow_force_pushes": {"enabled": False},
                   "enforce_admins": {"enabled": True},
                   "required_status_checks": {"contexts": ["ci/a", "ci/b"],
                                              "strict": True}}
        stub = _CoverageStub([], classic=classic)
        self._patch(stub)
        out = protect.apply_one(REPO, approvals=0, dry=True, checks=["ci/c"],
                                gate=False)
        self.assertEqual(out["checks"], ["ci/a", "ci/b", "ci/c"],
                         "a context only classic carried was dropped")
        self.assertTrue(out["strict"], "classic's strict flag was cleared")


class IntegrationIdBinding(unittest.TestCase):
    """An `integration_id` pins a context to one app. Rebuilding the rule from
    context strings lets any provider satisfy the name — a silent downgrade of
    the very rule this helper claims never to weaken."""

    def test_existing_binding_survives_a_merge(self):
        rules = [status_check_rule([{"context": "ci/x", "integration_id": 42}], False)]
        merge_status_checks(rules, ["ci/y"], False)
        got = rules[0]["parameters"]["required_status_checks"]
        self.assertEqual(got[0], {"context": "ci/x", "integration_id": 42})
        self.assertEqual(got[1], {"context": "ci/y"})

    def test_plain_strings_still_accepted(self):
        r = status_check_rule(["a"], False)
        self.assertEqual(r["parameters"]["required_status_checks"], [{"context": "a"}])

    def test_classic_app_id_maps_to_integration_id(self):
        got = classic_checks_from({"checks": [{"context": "ci/x", "app_id": 7},
                                              {"context": "ci/y", "app_id": None}]})
        self.assertEqual(got, [{"context": "ci/x", "integration_id": 7},
                               {"context": "ci/y"}])

    def test_legacy_contexts_shape_still_read(self):
        self.assertEqual(classic_checks_from({"contexts": ["a", "b"]}), ["a", "b"])


class ClassicSeedingIsGated(unittest.TestCase):
    """Classic can permit admin bypass. Moving its contexts under
    `bypass_actors: []` makes a stale one absolutely blocking, so the contexts
    inherited from classic need the same gate as the requested ones."""

    def _patch(self, stub):
        import protect
        orig = (protect.gh, protect.gh_optional)
        protect.gh, protect.gh_optional = stub.gh, stub.gh_optional
        self.addCleanup(lambda: setattr_pair(protect, orig))

    CLASSIC = {"allow_force_pushes": {"enabled": False},
               "enforce_admins": {"enabled": True},
               "required_status_checks": {"contexts": ["ci/stale"], "strict": False}}

    def test_stale_classic_context_blocks_the_apply(self):
        import protect
        prs = [_pr(1, "a", "2026-08-01T00:00:00Z")]
        # ci/live reports; ci/stale (from classic) never does
        stub = _CoverageStub(prs, statuses={"a": ["ci/live"]}, classic=self.CLASSIC)
        self._patch(stub)
        out = protect.apply_one(REPO, approvals=0, dry=True, checks=["ci/live"])
        self.assertEqual(out["action"], "skip-coverage")
        self.assertIn("ci/stale", out["why"])
        self.assertIn("classic protection", out["why"],
                      "operator needs to know the bad context is inherited")

    def test_classic_contexts_that_do_report_are_carried(self):
        import protect
        prs = [_pr(1, "a", "2026-08-01T00:00:00Z")]
        classic = {**self.CLASSIC,
                   "required_status_checks": {"contexts": ["ci/also"], "strict": True}}
        stub = _CoverageStub(prs, statuses={"a": ["ci/live", "ci/also"]},
                             classic=classic)
        self._patch(stub)
        out = protect.apply_one(REPO, approvals=0, dry=True, checks=["ci/live"])
        self.assertEqual(out["action"], "would-apply")
        self.assertEqual(sorted(out["checks"]), ["ci/also", "ci/live"])
        self.assertTrue(out["strict"], "classic's strict must survive")


class HeadBranchCleanup(unittest.TestCase):
    """`delete_branch_on_merge`: the org listing omits it, and GitHub omits it for
    a non-admin caller. Neither may read as on."""

    class Stub:
        def __init__(self, value, patch_returns=True, omit=False):
            self.value, self.patch_returns, self.omit = value, patch_returns, omit
            self.writes, self.gets = [], []

        def gh(self, path, *, method="GET", input_json=None, **kw):
            if method == "PATCH":
                self.writes.append((path, input_json))
                return {"delete_branch_on_merge": self.patch_returns}
            self.gets.append(path)
            if path == "repos/O/R":
                return {} if self.omit else {"delete_branch_on_merge": self.value}
            raise AssertionError(f"unexpected GET {path}")

    def _patch(self, stub):
        import protect
        orig = protect.gh
        protect.gh = stub.gh
        self.addCleanup(lambda: setattr(protect, "gh", orig))

    def test_listing_without_the_field_triggers_a_repo_get(self):
        import protect
        stub = self.Stub(False)
        self._patch(stub)
        self.assertIs(protect.head_branch_cleanup(REPO), False)
        self.assertEqual(stub.gets, ["repos/O/R"])

    def test_field_already_present_needs_no_get(self):
        import protect
        stub = self.Stub(False)
        self._patch(stub)
        self.assertIs(protect.head_branch_cleanup({**REPO, "delete_branch_on_merge": True}),
                      True)
        self.assertEqual(stub.gets, [])

    def test_omitted_for_non_admin_is_unknown_not_on(self):
        import protect
        self._patch(self.Stub(None, omit=True))
        self.assertIsNone(protect.head_branch_cleanup(REPO))

    def test_off_is_enabled_and_verified(self):
        import protect
        stub = self.Stub(False)
        self._patch(stub)
        self.assertEqual(protect.enable_head_branch_cleanup(REPO, dry=False), "enabled")
        self.assertEqual(stub.writes, [("repos/O/R", {"delete_branch_on_merge": True})])

    def test_already_on_writes_nothing(self):
        import protect
        stub = self.Stub(True)
        self._patch(stub)
        self.assertEqual(protect.enable_head_branch_cleanup(REPO, dry=False), "on")
        self.assertEqual(stub.writes, [])

    def test_dry_run_writes_nothing(self):
        import protect
        stub = self.Stub(False)
        self._patch(stub)
        self.assertEqual(protect.enable_head_branch_cleanup(REPO, dry=True), "would-enable")
        self.assertEqual(stub.writes, [])

    def test_patch_that_does_not_stick_fails_verify(self):
        import protect
        self._patch(self.Stub(False, patch_returns=False))
        self.assertEqual(protect.enable_head_branch_cleanup(REPO, dry=False),
                         "FAILED-VERIFY")

    def test_unknown_is_attempted_not_skipped(self):
        """Unknown is not on, so apply tries the write; the PATCH needs admin
        anyway and raises (into the run's errors) if the caller lacks it."""
        import protect
        stub = self.Stub(None, omit=True)
        self._patch(stub)
        self.assertEqual(protect.enable_head_branch_cleanup(REPO, dry=False), "enabled")
        self.assertEqual(len(stub.writes), 1)

    def test_apply_repo_sets_it_even_when_rulesets_skip(self):
        import protect
        stub = self.Stub(False)
        self._patch(stub)
        orig = protect.apply_one
        protect.apply_one = lambda *a, **k: {"repo": "R", "action": "skip-coverage"}
        self.addCleanup(lambda: setattr(protect, "apply_one", orig))
        row = protect.apply_repo(REPO, 0, False)
        self.assertEqual(row["action"], "skip-coverage")
        self.assertEqual(row["delete_branch_on_merge"], "enabled")

    def test_keep_head_branches_leaves_it_alone(self):
        import protect
        stub = self.Stub(False)
        self._patch(stub)
        orig = protect.apply_one
        protect.apply_one = lambda *a, **k: {"repo": "R", "action": "skip"}
        self.addCleanup(lambda: setattr(protect, "apply_one", orig))
        row = protect.apply_repo(REPO, 0, False, head_cleanup=False)
        self.assertNotIn("delete_branch_on_merge", row)
        self.assertEqual(stub.writes, [])


def setattr_pair(mod, orig):
    mod.gh, mod.gh_optional = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
