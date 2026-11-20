"""Pre-consent screens: one canonical list, three copies, one behaviour.

`welcome` renders before the age-gate consent, where ExperimentService and Mixpanel have not
started, so no idea can bucket or expose there (PROD-7022, IDEA-1). The list lives in
brainstorm-ideas' reference; design_idea and build_idea carry copies because skills are
self-contained, and this test is what keeps the copies equal.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
import argparse
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SKILLS = REPO / "plugins" / "product" / "skills"
sys.path.insert(0, str(SKILLS / "build-idea" / "scripts"))
import build_idea as b  # noqa: E402


def _design():
    spec = importlib.util.spec_from_file_location(
        "design_idea", SKILLS / "design-idea" / "scripts" / "design_idea.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["design_idea"] = mod
    spec.loader.exec_module(mod)
    return mod


def canonical() -> set[str]:
    text = (SKILLS / "brainstorm-ideas" / "references" / "pre-consent-screens.md").read_text()
    block = re.search(r"<!-- pre-consent-screens -->(.*?)<!-- /pre-consent-screens -->", text, re.S)
    assert block, "marker block missing"
    return set(re.findall(r"^- `([a-z_]+)`", block.group(1), re.M))


class TheListsAgree(unittest.TestCase):
    def test_welcome_is_on_the_list(self):
        self.assertIn("welcome", canonical())

    def test_build_idea_copy(self):
        self.assertEqual(b.PRE_CONSENT_SCREENS, canonical())

    def test_design_idea_copy_and_model_flags(self):
        d = _design()
        self.assertEqual(d.PRE_CONSENT_SCREENS, canonical())
        flagged = {k for k, s in d.SCREENS.items() if s.pre_consent}
        modelled = set(d.SCREENS) & canonical()
        self.assertEqual(flagged, modelled)


class PlanRefusesAPreConsentScreen(unittest.TestCase):
    def idea(self, screen, instrumentation=""):
        return {"screen": screen, "instrumentation": instrumentation}

    def test_the_row_identifier_form_is_read(self):
        for s in ("welcome", "welcome_screen", "Welcome_Screen"):
            self.assertIsNotNone(b.pre_consent_refusal(self.idea(s)), s)

    def test_a_post_consent_screen_passes(self):
        for s in ("onboarding_valueprops", "home_screen", "affirmation", ""):
            self.assertIsNone(b.pre_consent_refusal(self.idea(s)), s)

    def test_a_named_bucketing_path_lets_it_through(self):
        i = self.idea("welcome_screen", "Bucketing path: hash(installId) local, exposure on first frame")
        self.assertIsNone(b.pre_consent_refusal(i))

    def test_the_message_says_what_to_do(self):
        msg = b.pre_consent_refusal(self.idea("welcome_screen"))
        self.assertIn("before consent", msg)
        self.assertIn("post-consent screen", msg)
        self.assertIn("bucketing path:", msg)

    def test_plan_exits_2_before_start(self):
        idea = {"title": "t", "idea_id": "IDEA-1", "fingerprint": "f", "status": "approved",
                "screen": "welcome_screen", "domain": "onboarding", "linear": "", "note": "",
                "experiment": "", "instrumentation": ""}
        args = argparse.Namespace(data_source_id="x", ref="IDEA-1", experiment=None)
        with mock.patch.object(b, "find_idea", return_value=idea), \
                mock.patch.object(b, "notion", mock.Mock(available=True)):
            self.assertEqual(b.cmd_plan(args), 2)


if __name__ == "__main__":
    unittest.main()
