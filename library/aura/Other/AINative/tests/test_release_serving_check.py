#!/usr/bin/env python3
"""A green CI pipeline is not a rollout, and `verify` must not imply it is.

`verify` read CircleCI job status and nothing else, then returned
`status: success` — which reads as *deployed*. CI green means the image was
built and pushed; the Kubernetes rollout lands a minute or more later.

Observed twice on 2026-09-23 (v1.22.0 and v1.22.1), and once it mattered: a
hand-run test of a newly shipped field would have seen the field absent, which
is indistinguishable from the model declining to populate it — the exact
question the test existed to answer.

The probe reads the version stamped on live `llmCall` rows, so it reports what
is *handling traffic* rather than what CI believes it deployed.
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest
from unittest import mock

_HERE = pathlib.Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "plugins/eng/skills/release-version/scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import release  # noqa: E402


def shape(slug: str):
    return mock.Mock(slug=slug)


class ServingCheck(unittest.TestCase):
    def test_confirmed_when_the_tag_is_serving(self):
        with mock.patch.object(
            release, "_serving_versions_aura_ledger", return_value=({"1.22.1"}, None)
        ):
            out = release.serving_check(shape("AuraHealth/IntelligenceService"), "v1.22.1")
        self.assertEqual(out["deployed"], "confirmed")

    def test_not_serving_when_traffic_is_on_an_older_version(self):
        # The rollout-in-flight case: this is the false green.
        with mock.patch.object(
            release, "_serving_versions_aura_ledger", return_value=({"1.22.0"}, None)
        ):
            out = release.serving_check(shape("AuraHealth/IntelligenceService"), "v1.22.1")
        self.assertEqual(out["deployed"], "not-serving")
        self.assertEqual(out["serving"], ["1.22.0"])

    def test_not_serving_when_the_tag_was_superseded(self):
        # One read cannot tell "not yet" from "already replaced", and the
        # caller does not care: neither is what production runs.
        with mock.patch.object(
            release, "_serving_versions_aura_ledger", return_value=({"1.22.1"}, None)
        ):
            out = release.serving_check(shape("AuraHealth/IntelligenceService"), "v1.22.0")
        self.assertEqual(out["deployed"], "not-serving")

    def test_confirmed_during_a_mixed_rollout(self):
        # Pods drain gradually; the new version appearing at all is the signal.
        with mock.patch.object(
            release,
            "_serving_versions_aura_ledger",
            return_value=({"1.22.0", "1.22.1"}, None),
        ):
            out = release.serving_check(shape("AuraHealth/IntelligenceService"), "v1.22.1")
        self.assertEqual(out["deployed"], "confirmed")

    def test_unknown_when_the_probe_cannot_run(self):
        # Must never read as a pass — same convention as `unverified`.
        with mock.patch.object(
            release,
            "_serving_versions_aura_ledger",
            return_value=(set(), "AURA_API_TOKEN not set"),
        ):
            out = release.serving_check(shape("AuraHealth/IntelligenceService"), "v1.22.1")
        self.assertEqual(out["deployed"], "unknown")
        self.assertNotEqual(out["deployed"], "confirmed")

    def test_unknown_for_a_repo_with_no_probe(self):
        out = release.serving_check(shape("AuraHealth/SomeOtherService"), "v1.0.0")
        self.assertEqual(out["deployed"], "unknown")
        self.assertIn("CI green does not", out["deploy_detail"])

    def test_tag_prefix_is_stripped_before_comparing(self):
        # Tags are `v1.2.3`; the ledger stamps `1.2.3`.
        with mock.patch.object(
            release, "_serving_versions_aura_ledger", return_value=({"1.22.1"}, None)
        ):
            self.assertEqual(
                release.serving_check(
                    shape("AuraHealth/IntelligenceService"), "v1.22.1"
                )["deployed"],
                "confirmed",
            )


class ProbeDoesNotShadowTheCISummary(unittest.TestCase):
    """The probe result is merged into verify's own dict, so a shared key wins.

    `verify`'s first output line is `f"{status}: {detail or jobs}"`. A probe
    returning `detail` therefore REPLACED the CI job summary with its own note
    — in exactly the common case (no token, no probe, unreachable ledger) — and
    the second line then repeated it. The release lost its CI readout to a
    message about the readout.
    """

    def test_probe_keys_never_collide_with_verify_detail(self):
        for ret in (
            (set(), "AURA_API_TOKEN not set"),
            (set(), "ledger unreachable: boom"),
        ):
            with mock.patch.object(
                release, "_serving_versions_aura_ledger", return_value=ret
            ):
                out = release.serving_check(
                    shape("AuraHealth/IntelligenceService"), "v1.2.3"
                )
            self.assertNotIn("detail", out)
            self.assertIn("deploy_detail", out)

    def test_no_probe_case_also_avoids_detail(self):
        out = release.serving_check(shape("AuraHealth/Other"), "v1.0.0")
        self.assertNotIn("detail", out)
        self.assertIn("deploy_detail", out)


class VerifyOnlyProbesOnceCIIsGreen(unittest.TestCase):
    def test_probe_is_gated_on_pipeline_success(self):
        # Asking mid-pipeline would report "not-serving" on every release, which
        # is noise, not signal — the rollout has not been attempted yet.
        #
        # Structural, not behavioural: driving `verify` itself would mean
        # stubbing the CircleCI transport, and the property here really is
        # "the call sits under the green guard". Matched loosely so a rename
        # of the intermediate does not fail a correct implementation.
        source = (_SCRIPTS / "release.py").read_text()
        guard = re.search(
            r'if status == "success":(.{0,200}?)serving_check\(', source, re.S
        )
        self.assertIsNotNone(guard, "serving_check is not under the CI-green guard")

    def test_intelligence_service_has_a_probe_registered(self):
        self.assertIn("AuraHealth/IntelligenceService", release.SERVING_PROBES)


if __name__ == "__main__":
    unittest.main()
