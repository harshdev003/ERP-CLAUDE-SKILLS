"""Guard tests for the Domain backfill in `product:manage-ideas`.

The backfill's only real risk is quiet divergence: if this mapping and
`sim_priors.DOMAIN_PATTERNS` disagree, old rows get filed under names the
ideation analyzer never produces, and a screen's history splits across two
domains inside the block whose whole job is to show it whole. Nothing errors.
"""

from __future__ import annotations

import os
import pathlib
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "plugins", "product", "skills", "manage-ideas", "scripts"))

import manage_ideas as m  # noqa: E402


class ScreenToDomain(unittest.TestCase):
    """Every screen the live database actually carries."""

    REAL = {
        "paywall_screen": "paywall",
        "paywall_container_screen": "paywall",
        "packagePaywall_screen": "paywall",
        "onboarding_valueProps_screen": "onboarding",
        "welcome_screen": "onboarding",
        "onboardingQP_screen": "onboarding",
        "home_screen": "home/feed",
        "conversation_history_screen": "conversation",
        "conversation_screen": "conversation",
        "journal_screen": "journal",
        "dailyGrounding_screen": "grounding",
        "affirmationDetail_screen": "affirmations",
        "settings_screen": "settings",
        "profile_screen": "profile",
    }

    def test_every_live_screen_resolves(self):
        for screen, domain in self.REAL.items():
            with self.subTest(screen=screen):
                self.assertEqual(m.screen_domain(screen), domain)

    def test_welcome_is_onboarding_not_a_domain_of_its_own(self):
        """`welcome_screen` is the first screen of onboarding. A separate
        `welcome` value would split one funnel across two domains."""
        self.assertEqual(m.screen_domain("welcome_screen"), "onboarding")
        self.assertNotIn("welcome", m.VALID_DOMAINS)

    def test_onboarding_wins_over_conversation_on_the_qp_intro(self):
        """`onboardingQP_screen` matches both patterns; first match wins, and it
        must win the same way `sim_priors` resolves it."""
        self.assertEqual(m.screen_domain("onboardingQP_screen"), "onboarding")

    def test_an_unclaimed_screen_is_none_not_other(self):
        """`other` is a real value the collision gate treats as a domain, so
        writing it would record a decision rather than the absence of one."""
        for s in ("", None, "quantumFluxCapacitor_screen"):
            with self.subTest(screen=s):
                self.assertIsNone(m.screen_domain(s))

    def test_every_mapped_domain_is_in_the_vocabulary(self):
        """A pattern producing a value the Notion select lacks is rejected by the
        API — after the run has already written the rows before it."""
        for _, domain in m.SCREEN_DOMAIN_PATTERNS:
            with self.subTest(domain=domain):
                self.assertIn(domain, m.VALID_DOMAINS)


# The canonical mapping, pinned. `sim_priors.DOMAIN_PATTERNS` in AuraConsumerApp is
# the source — the ideation analyzer tags new ideas with it — and this literal is a
# third copy, on purpose.
#
# The first version of this test compared the two lists directly and skipped when the
# sibling checkout was absent. That made the ONLY guard against quiet drift a test
# that ran on one laptop and silently asserted nothing on CI, which is worse than no
# test: the suite goes green and reports coverage it does not have.
#
# So it asserts in two layers. The pinned literal always runs and catches this repo's
# copy changing. The sibling comparison runs when the checkout is discoverable and
# catches the canonical changing — the case the literal cannot see.
CANONICAL_PATTERNS = [
    (r"paywall|packagePaywall|pricingTiers|subscription|subscriptionDetail|packageUsage"
     r"|packageConfig|paymentPending|cancellationReason|unsubscribe", "paywall"),
    (r"onboarding|welcome|signIn|signUp|splash|quiz|valueProps|didYouKnow|selfCommitment"
     r"|tutorial", "onboarding"),
    (r"home|useCases|encourageRating|dailyPractices|editPractices|customPractice", "home/feed"),
    (r"conversation|liveSession|quickProcessing|qpDone|chat|voice|orbReactions", "conversation"),
    (r"journal|practiceJournal", "journal"),
    (r"grounding|meditation|prayer|player|practiceRedo", "grounding"),
    (r"affirmation|bookmarks", "affirmations"),
    (r"profile|editProfileName|bringYourMemory", "profile"),
    (r"settings|preferences|about", "settings"),
    (r"notification", "notifications"),
]


def find_priors_scripts():
    """Locate AuraConsumerApp's Scripts dir without a named-user path.

    Order: explicit env, then a sibling of this checkout, then $HOME-relative
    conventions. Never a personal home directory — that is what made the previous
    version of this guard a no-op everywhere but one machine.
    """
    env = os.environ.get("AURA_CONSUMER_APP")
    candidates = []
    if env:
        candidates.append(os.path.join(env, "AuraConsumerAppSimTests", "Scripts"))
    here = pathlib.Path(REPO).resolve()
    for base in list(here.parents)[:4]:
        candidates.append(str(base / "AuraConsumerApp" / "AuraConsumerAppSimTests" / "Scripts"))
        candidates.append(str(base / "Mobile" / "AuraConsumerApp" / "AuraConsumerAppSimTests" / "Scripts"))
    home = pathlib.Path(os.path.expanduser("~"))
    for suffix in ("Development/Aura/Mobile/AuraConsumerApp", "AuraConsumerApp",
                   "Development/AuraConsumerApp"):
        candidates.append(str(home / suffix / "AuraConsumerAppSimTests" / "Scripts"))
    for c in candidates:
        if os.path.isfile(os.path.join(c, "sim_priors.py")):
            return c
    return None


class AgreesWithTheCanonicalMapping(unittest.TestCase):

    def test_this_copy_matches_the_pinned_canonical(self):
        """Always runs. Catches this repo's copy drifting."""
        self.assertEqual(list(m.SCREEN_DOMAIN_PATTERNS), CANONICAL_PATTERNS)

    def test_the_pinned_canonical_matches_the_live_source(self):
        """Runs wherever AuraConsumerApp is checked out. Catches the SOURCE
        drifting, which the pinned literal above cannot see on its own."""
        scripts = find_priors_scripts()
        if scripts is None:
            self.skipTest("AuraConsumerApp checkout not found (set AURA_CONSUMER_APP)")
        sys.path.insert(0, scripts)
        import sim_priors as sp
        self.assertEqual([list(x) for x in sp.DOMAIN_PATTERNS],
                         [list(x) for x in CANONICAL_PATTERNS])



class OfflineBarRejectionReason(unittest.TestCase):
    """The Aura Recommendations loop closes a failed candidate with
    `offline bar` — the one rejection reason automation may write without a
    human. The vocabulary is enforced twice (argparse choices and the guard in
    the set command), so a value present only in the Notion select is refused
    and the row stays pending for share-ideas to post."""

    def test_offline_bar_is_in_the_enforced_vocabulary(self):
        self.assertIn("offline bar", m.REJECTION_REASONS)

    def test_the_retiring_reasons_are_unchanged(self):
        for reason in ("already tested", "no longer valid", "timing", "tech cost"):
            self.assertIn(reason, m.REJECTION_REASONS)

    def test_other_stays_last_so_it_reads_as_the_fallback(self):
        self.assertEqual(m.REJECTION_REASONS[-1], "other")

if __name__ == "__main__":
    unittest.main()
