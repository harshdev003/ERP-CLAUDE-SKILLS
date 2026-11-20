"""data:sync-experiments `Variants` (ENG-1813): arm names from the notes, or nothing; never bare letters.

The 2026-11-04 run left eight `Learnings/Experiments` rows reading "b:" or "a:\\nb:\\nc:\\nd:". The
experiment server has no variant field, but the engineers' `notes` name the arms. The fixtures below
are those eight experiments' real notes, taken from `GET /experiments` on 2026-11-05.
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

SCRIPT = (pathlib.Path(__file__).resolve().parents[1]
          / "plugins/data/skills/sync-experiments/scripts/variants.py")
spec = importlib.util.spec_from_file_location("sync_experiments_variants", SCRIPT)
variants = importlib.util.module_from_spec(spec)
spec.loader.exec_module(variants)

NOTES = {
    "aiWebLandingPageCopy": ("New hero copy on the  aurie.ai  web landing page. a: new hero copy, b: control, "
                             "current copy",
                             "a: new hero copy\nb: control, current copy"),
    "aiWebPaywallNoTrial": ("No-trial plans on the Aurie web paywall, yearly $69.99 and monthly $19.99 charged "
                            "today. a: yearly and monthly cards both visible, b: control, Annual/Monthly toggle "
                            "with one plan card at a time, c: new design",
                            "a: yearly and monthly cards both visible\nb: control, Annual/Monthly toggle with one "
                            "plan card at a time\nc: new design"),
    "coachPrompterNoEmojiRule": ("a = old coach prompt without the emoji rule (emoji allowed); b = control, "
                                 "current coach prompt with emoji rule v2.",
                                 "a: old coach prompt without the emoji rule (emoji allowed)\nb: control, current "
                                 "coach prompt with emoji rule v2"),
    "guardrailsHandlerPrompterAbusePath": (
        "a = previous guardrails handler (before v6), as is; b = control, current guardrails handler v7 with "
        "the abuse, sexual violence and relationships path. IntelligenceService PR #812, PROD-8236. Ask from "
        "Steve in the guardrails thread; read retention from each user's first guardrail reply.",
        "a: previous guardrails handler (before v6), as is\nb: control, current guardrails handler v7 with the "
        "abuse, sexual violence and relationships path"),
    "aiMeditationRoom": ("Meditation Room. a: Growth at today'\\''s price, b: control (no Aura tab), c: Growth "
                         "13h/4x at $29.99/$159.99 (Stripe only), d: Aura offer",
                         "a: Growth at today's price\nb: control (no Aura tab)\nc: Growth 13h/4x at "
                         "$29.99/$159.99 (Stripe only)\nd: Aura offer"),
    "aiVoiceProvider2": ("PROD-8250 Aurie voice A/B on LiveKit voice sessions, follow-up to aiVoiceProvider. "
                         "a: Cartesia Aurie V7 - V1, b: control, Cartesia Aurie V7 - V3 (the aiVoiceProvider "
                         "winner), c: Cartesia Aurie V7 - V3 played 2% faster (playback_rate 1.02), d: "
                         "ElevenLabs, the old control voice. A user is assigned when",
                         "a: Cartesia Aurie V7 - V1\nb: control, Cartesia Aurie V7 - V3 (the aiVoiceProvider "
                         "winner)\nc: Cartesia Aurie V7 - V3 played 2% faster (playback_rate 1.02)\nd: "
                         "ElevenLabs, the old control voice"),
}


class VariantsTest(unittest.TestCase):
    def test_real_notes_give_arm_names(self):
        for exp, (notes, want) in NOTES.items():
            with self.subTest(exp=exp):
                self.assertEqual(variants.variants_value(notes), want)

    def test_never_bare_letters(self):
        for exp, (notes, _) in NOTES.items():
            self.assertFalse(variants.is_blank_labels(variants.variants_value(notes)), exp)
        for notes in (None, "", "control only", "b: control", "a: x", "a:  , b: y", "Test the a: b: c: flow"):
            with self.subTest(notes=notes):
                value = variants.variants_value(notes)
                self.assertTrue(value is None or not variants.is_blank_labels(value), value)

    def test_no_names_means_leave_empty(self):
        for notes in (None, "", "Aurie paywall test, three arms", "b: control, c: new"):
            self.assertIsNone(variants.variants_value(notes), notes)

    def test_letters_inside_words_are_not_arms(self):
        self.assertIsNone(variants.arm_names("Sofa: blue, Tea: green"))

    def test_bare_letter_values_are_detected(self):
        for text in ("b:", "a:\nb:", "a:\nb:\nc:\nd:", " a: \n b:"):
            self.assertTrue(variants.is_blank_labels(text), text)
        for text in ("", None, "a: control", "a: x\nb:"):
            self.assertFalse(variants.is_blank_labels(text), text)


if __name__ == "__main__":
    unittest.main()
