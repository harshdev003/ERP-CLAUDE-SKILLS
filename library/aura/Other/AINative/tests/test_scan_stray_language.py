"""`eng:scan-stray-language`: which replies count as a stray script.

The fixtures are the shapes from the reference review on ENG-1659 (2026-09-27 to 10-27): the three
true strays it found, and the deliberate uses the input-only rule wrongly flagged. Excerpts are
paraphrased or trimmed, never full transcripts. Stdlib `unittest`; no network.
"""

from __future__ import annotations

import importlib.util
import pathlib
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/eng/skills/scan-stray-language/scripts/scan.py"
spec = importlib.util.spec_from_file_location("scan_stray_language_under_test", SCRIPT)
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)

EN = [{"role": "user", "content": "I feel like I have reached the edge of what I can handle."}]


class Strays(unittest.TestCase):
    def strays(self, output, inp=EN):
        return scan.classify(scan.text_of(output), scan.text_of(inp))

    def test_a_word_dropped_into_an_english_sentence_flags(self):
        hits, deliberate = self.strays("This sounds very close to the edge of what you can تحمل. In my role...")
        self.assertEqual(hits, [("Arabic", "تحمل")])
        self.assertEqual(deliberate, [])

    def test_a_word_glued_to_english_flags(self):
        self.assertEqual(self.strays("changing yourself, and تحملing treatment that hurt you")[0], [("Arabic", "تحمل")])

    def test_structured_output_is_read(self):
        out = {"affirmation": "I can trust what was real for me, even बिना someone else confirming it."}
        self.assertEqual(self.strays(out)[0], [("Devanagari", "बिना")])

    def test_a_reply_to_a_user_writing_in_that_script_never_flags(self):
        inp = [{"role": "user", "content": "Привет, как дела?"}]
        self.assertEqual(self.strays("Привет! Я рядом.", inp), ([], []))

    def test_quoting_the_users_own_words_never_flags(self):
        inp = [{"role": "user", "content": "my grandma used to say «всё будет хорошо»"}]
        self.assertEqual(self.strays("She told you «всё будет хорошо», and you still carry it.", inp), ([], []))

    def test_a_full_reply_in_another_language_is_deliberate(self):
        hits, deliberate = self.strays("أكيد يا صديقي، أقدر أتكلم معك بالعربية بكل راحة.")
        self.assertEqual(hits, [])
        self.assertEqual(deliberate, ["Arabic"])

    def test_a_named_term_is_deliberate(self):
        hits, deliberate = self.strays("Naming your bear after the Greek word πίστις (faith) is lovely.")
        self.assertEqual((hits, deliberate), ([], ["Greek"]))

    def test_a_quoted_title_is_deliberate(self):
        hits, deliberate = self.strays('There is **“این نیز”** by Imminence, and it fits.')
        self.assertEqual((hits, deliberate), ([], ["Arabic"]))

    def test_a_stray_inside_an_english_quote_still_flags(self):
        out = '{"affirmation":"I can trust what was real for me, even बिना someone else confirming it."}'
        self.assertEqual(self.strays(out)[0], [("Devanagari", "बिना")])

    def test_latin_only_replies_are_untouched(self):
        self.assertEqual(self.strays("Café, naïve, jalapeño and Zoë are all Latin script."), ([], []))


class Paging(unittest.TestCase):
    def test_a_full_page_is_split_until_it_fits(self):
        import datetime as dt
        calls = []

        def fake(query):
            calls.append(query)
            # the whole day is "full"; each half fits
            return [{"id": str(i)} for i in range(scan.PAGE if len(calls) == 1 else 3)]

        with mock.patch.object(scan, "btql", side_effect=fake):
            rows = scan.candidates(dt.datetime(2026, 10, 1), dt.datetime(2026, 10, 2), None, None)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(rows), 6)

    def test_filters_are_quoted(self):
        import datetime as dt
        w = scan._where(dt.datetime(2026, 10, 1), dt.datetime(2026, 10, 2), "gpt-5.4", "o'brien")
        self.assertIn("metadata.prompterName = 'o''brien'", w)


if __name__ == "__main__":
    unittest.main()
