"""Tests for how a pipeline's workflows collapse into one status per job.

A rerun does not replace a workflow — it adds a new one to the same pipeline and leaves the
superseded run in place. So a pipeline routinely holds both a failed and a successful run of the
same job, and every consumer that reads "the pipeline's workflows" has to decide which one counts.

Two implementations of that rule live in this repo, under different filenames and in different
skills, so this suite covers both:

* `release-version/scripts/release.py` — `latest_job_statuses`, behind `verify`
* `update-node-packages/scripts/land_pr.sh` — the inline CI poll behind the merge gate

The load-bearing assertions here are the ones about a **recovered** pipeline. Both implementations
passed every same-pipeline test before the fix, because a pipeline with one workflow has no ordering
to get wrong. The bug only appears once a rerun exists, and it appears in opposite-but-equally-quiet
ways: `verify` reported a healthy deploy as failed forever, and the merge gate refused a green PR
forever. Neither errors.

CircleCI lists workflows newest-first, so every fixture below is ordered newest-first to match what
the API actually returns — an ascending fixture would pass against the broken code.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "plugins/eng/skills/release-version/scripts"))

import release as R  # noqa: E402

LAND_PR = REPO / "plugins/eng/skills/update-node-packages/scripts/land_pr.sh"


def wf(name, status, created):
    return {"id": f"{name}-{created}", "name": name, "status": status, "created_at": created}


class TestLatestJobStatuses(unittest.TestCase):
    """`release.latest_job_statuses` — one status per job, newest wins."""

    def collect(self, workflows, jobs_by_wf):
        return R.latest_job_statuses(workflows, lambda w: jobs_by_wf[w["id"]])

    def test_single_workflow_passes_through(self):
        w = wf("build", "success", "2026-08-25T10:00:00Z")
        got = self.collect([w], {w["id"]: [{"name": "test", "status": "success"}]})
        self.assertEqual(got, {"test": "success"})

    def test_rerun_supersedes_the_failed_run(self):
        """The Ingestor case: push failed at 13:09, rerun succeeded at 15:13."""
        new = wf("build", "success", "2026-08-25T15:13:55Z")
        old = wf("build", "failed", "2026-08-25T13:09:36Z")
        got = self.collect(
            [new, old],  # newest-first, as the API returns it
            {
                new["id"]: [{"name": "test", "status": "success"},
                            {"name": "push", "status": "success"}],
                old["id"]: [{"name": "test", "status": "success"},
                            {"name": "push", "status": "failed"}],
            },
        )
        self.assertEqual(got["push"], "success")

    def test_from_failed_rerun_keeps_jobs_it_did_not_rerun(self):
        """`rerun --from-failed` re-runs only the failed subtree.

        The earlier workflow is then the ONLY record of the jobs that already passed, so a
        newest-workflow-wins rule (rather than newest-per-JOB) would lose `test` entirely.
        """
        new = wf("build", "success", "2026-08-25T15:13:55Z")
        old = wf("build", "failed", "2026-08-25T13:09:36Z")
        got = self.collect(
            [new, old],
            {
                new["id"]: [{"name": "push", "status": "success"}],   # only the rerun subtree
                old["id"]: [{"name": "test", "status": "success"},
                            {"name": "push", "status": "failed"}],
            },
        )
        self.assertEqual(got, {"test": "success", "push": "success"})

    def test_a_genuine_failure_is_still_reported(self):
        """The negative case: no rerun, so nothing supersedes the failure."""
        w = wf("build", "failed", "2026-08-25T13:09:36Z")
        got = self.collect([w], {w["id"]: [{"name": "test", "status": "success"},
                                           {"name": "push", "status": "failed"}]})
        self.assertEqual(got["push"], "failed")

    def test_a_rerun_that_failed_again_is_still_a_failure(self):
        """Newest wins in BOTH directions — a rerun must be able to report failure."""
        new = wf("build", "failed", "2026-08-25T15:13:55Z")
        old = wf("build", "success", "2026-08-25T13:09:36Z")
        got = self.collect(
            [new, old],
            {new["id"]: [{"name": "push", "status": "failed"}],
             old["id"]: [{"name": "push", "status": "success"}]},
        )
        self.assertEqual(got["push"], "failed")

    def test_distinct_workflows_are_both_kept(self):
        """Two different workflow names are not reruns of each other."""
        a = wf("build", "success", "2026-08-25T10:00:00Z")
        b = wf("deploy", "failed", "2026-08-25T11:00:00Z")
        got = self.collect([b, a], {a["id"]: [{"name": "test", "status": "success"}],
                                    b["id"]: [{"name": "ship", "status": "failed"}]})
        self.assertEqual(got, {"test": "success", "ship": "failed"})

    def test_missing_created_at_does_not_raise(self):
        """A workflow with no timestamp sorts first rather than crashing the verify."""
        a = {"id": "a", "name": "build", "status": "failed"}
        b = wf("build", "success", "2026-08-25T15:00:00Z")
        got = self.collect([b, a], {"a": [{"name": "push", "status": "failed"}],
                                    b["id"]: [{"name": "push", "status": "success"}]})
        self.assertEqual(got["push"], "success")


class TestLandPrAggregation(unittest.TestCase):
    """The same rule as implemented inline in `land_pr.sh`.

    Extracted by reading the heredoc out of the script rather than reimplemented, so the test
    fails if the script's own logic regresses. Aggregates workflow STATUS (not jobs), because
    that is what the merge gate branches on.
    """

    @staticmethod
    def aggregate(workflows):
        src = LAND_PR.read_text()
        start = src.index("    latest={}")
        # Anchor the end search AFTER start: an earlier `except: print('none')` guards the
        # stdin parse higher up the script, and matching that one yields an empty slice.
        end = src.index("except: print('none')", start)
        body = src[start:end]
        body = body.replace("\\\"", '"').replace("\\$", "$")
        ns = {"wfs": workflows, "print": lambda v: ns.__setitem__("_out", v)}
        exec("\n".join(l[4:] if l.startswith("    ") else l
                       for l in body.splitlines() if l.strip()), ns)
        return ns.get("_out")

    def test_rerun_unblocks_a_previously_failed_pipeline(self):
        """Before the fix this returned 'failed' forever, so the gate could never merge."""
        got = self.aggregate([wf("build", "success", "2026-08-25T15:13:55Z"),
                              wf("build", "failed", "2026-08-25T13:09:36Z")])
        self.assertEqual(got, "success")

    def test_a_genuine_failure_still_blocks(self):
        got = self.aggregate([wf("build", "failed", "2026-08-25T13:09:36Z")])
        self.assertEqual(got, "failed")

    def test_one_of_two_distinct_workflows_failing_blocks(self):
        """The behaviour the original comment was protecting — do not regress it."""
        got = self.aggregate([wf("deploy", "failed", "2026-08-25T11:00:00Z"),
                              wf("build", "success", "2026-08-25T10:00:00Z")])
        self.assertEqual(got, "failed")

    def test_still_running_is_not_success(self):
        got = self.aggregate([wf("build", "running", "2026-08-25T15:00:00Z")])
        self.assertEqual(got, "running")

    def test_no_workflows_is_none(self):
        self.assertEqual(self.aggregate([]), "none")


if __name__ == "__main__":
    unittest.main()
