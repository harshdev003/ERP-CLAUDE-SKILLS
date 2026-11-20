#!/usr/bin/env python3
"""`eng:export-user-profile`: the profile Aurie keeps about a user, plus their account page.

The rules pinned here:
- **Allow-lists:** the account page shows only the listed UserService fields and the plan and its
  dates. Credentials, device tokens, ip, staff notes, revenue, receipts and cancel feedback never
  reach the document, whatever the responses carry.
- **Cards as the app shows them:** only cards with `display` on; tensions read as two poles.
- **Who wrote it:** the profile is "Written by Aurie", onboarding answers "Written by you".
- **Structure-safe:** a stored document's own headings can't take over the Markdown outline.
- **Input:** the `profile` file from eng:query-user-export-data; another version, section or user
  is refused.

Stdlib `unittest`, no network: the account reads are stubbed at `_get`.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest
import uuid
from unittest import mock
from zoneinfo import ZoneInfo

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/eng/skills/export-user-profile/scripts/export_profile.py"
UID = "TestUidTestUidTestUid0000001"
SECRET = "INTERNAL-FIELD-SENTINEL"

PROFILE = {
    "profile": "Likes long walks.",
    "profileIngestedAt": "2026-11-01T00:00:00.000Z",
    "insights": "## Values\n- Family first.",
    "insightsCompiledAt": "2026-11-02T00:00:00.000Z",
    "insightsRenderers": {
        "core-quote-renderer": {"quote_card": {"quote": "Steady wins.", "display": True},
                                "at_your_core": {"body": "Calm and kind.", "display": True}},
        "inner-tensions-renderer": {"inner_tensions": {"display": True, "tensions": [
            {"left_pole": {"title": "Rest", "phrase": "letting go"}, "right_pole": {"title": "Drive", "phrase": "pushing on"}}]}},
        "you-best-renderer": {"you_at_your_best": {"display": True, "summary": "Focused.",
                                                   "strengths": [{"label": "Grit", "sentence": "You keep going."}]}},
        "hidden-renderer": {"hidden_card": {"body": "HIDDEN-CARD", "display": False}},
    },
    "insightsRenderersCompiledAt": "2026-11-03T00:00:00.000Z",
    "onboardingConversation": [{"role": "assistant", "message": "Welcome!", "createdAt": "x"},
                               {"role": "user", "message": "Hi, I'm here.", "createdAt": "x"}],
}
USER = {"email": "a@example.com", "givenName": "Ann", "createdAt": "2020-01-02T00:00:00Z", "locale": "en",
        "timezone": "America/Chicago", "countryCode": "US", "calendarRefreshToken": SECRET,
        "deviceToken": SECRET, "ip": SECRET, "notes": SECRET}
SUBS = [{"type": "premium", "period": "yearly", "trial": False, "autoRenew": True,
         "expirationAt": "2999-01-01T00:00:00Z", "createdAt": "2025-01-01T00:00:00Z", "provider": "apple",
         "revenue": SECRET, "transactionId": SECRET, "receiptId": SECRET, "cancelFeedback": SECRET, "deletedAt": None},
        {"type": "premium", "period": "monthly", "expirationAt": "2024-01-01T00:00:00Z", "deletedAt": None},
        {"type": "content", "period": "monthly", "expirationAt": "2025-06-01T00:00:00Z", "deletedAt": None}]


def load():
    name = f"_export_profile_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class Document(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.tz = ZoneInfo("America/Chicago")

        def get(url, token, method="GET", body=None):
            return SUBS if "/subscriptions/list/" in url else USER
        with mock.patch.object(self.mod, "_get", get):
            self.account, self.plan = self.mod.fetch_account("https://h", "t", UID)

    def docs(self):
        return (self.mod.build_html(PROFILE, self.account, self.plan, self.tz),
                self.mod.build_markdown(PROFILE, self.account, self.plan, self.tz))

    def test_only_allow_listed_account_and_plan_fields(self):
        self.assertEqual(set(self.account), {k for k, _ in self.mod.ACCOUNT_FIELDS})
        self.assertTrue(all(set(p) <= set(self.mod.PLAN_FIELDS) for p in self.plan))
        for doc in self.docs():
            self.assertNotIn(SECRET, doc)
            for shown in ("a@example.com", "Ann", "January 1, 2020", "Premium, yearly", "Renews on", "App Store",
                          "English", "United States"):
                self.assertIn(shown, doc)

    def test_only_plans_in_force_one_per_product(self):
        # The lapsed content plan and the expired monthly premium are not shown as current.
        self.assertEqual([(p["type"], p["period"]) for p in self.plan], [("premium", "yearly")])
        self.assertEqual(self.mod.current_plans([{"deletedAt": "x", "lifetime": True}]), [])
        both = self.mod.current_plans([{"type": "ai", "lifetime": True}, {"type": "content", "expirationAt": "2999-01-01T00:00:00Z"}])
        self.assertEqual([p["type"] for p in both], ["ai", "content"])
        groups = dict(self.mod.account_rows(self.account, None, self.tz))
        self.assertNotIn("Plan", groups, "no plan on record prints no plan rows at all")

    def test_a_field_the_record_lacks_prints_no_row(self):
        sparse = {k: None for k, _ in self.mod.ACCOUNT_FIELDS}
        sparse.update({"email": "a@example.com", "countryCode": "", "locale": "  "})
        groups = dict(self.mod.account_rows(sparse, [{"provider": "something-else"}], self.tz))
        self.assertEqual(groups, {"Identity": [("Email", "a@example.com")]})
        self.assertNotIn("Age group", self.mod.build_html(PROFILE, sparse, None, self.tz))

    def test_cards_as_the_app_shows_them(self):
        html_doc, md = self.docs()
        for doc in (html_doc, md):
            self.assertNotIn("HIDDEN-CARD", doc)
            for shown in ("Steady wins.", "Calm and kind.", "Rest — letting go", "Drive — pushing on",
                          "Grit — You keep going.", "Focused."):
                self.assertIn(shown, doc)
        self.assertIn('<td class="arrow">↔</td>', html_doc)
        self.assertIn("- Rest — letting go ↔ Drive — pushing on", md)

    def test_authors_and_legend(self):
        html_doc, md = self.docs()
        cover = html_doc.split('<div class="day">', 1)[0]
        for chip in ("Your account", "Written by Aurie", "Written by you"):
            self.assertIn(chip, cover)
        # The onboarding quiz is a numbered list of questions with the user's chosen answers.
        self.assertIn("1. Welcome!", md)
        self.assertIn("**Written by you**: Hi, I'm here.", md)
        self.assertIn('<ol class="quiz">', html_doc)

    def test_a_stored_document_cannot_take_over_the_outline(self):
        _, md = self.docs()
        self.assertIn("**Values**", md)
        self.assertNotIn("\n## Values", md)

    def test_no_uid_in_the_document(self):
        for doc in self.docs():
            self.assertNotIn(UID, doc)


class Blocks(unittest.TestCase):
    def test_insight_evidence_pairs_headings_and_a_duplicate_title(self):
        mod = load()
        text = "# User Profile\n## Values\n- Insight: Family first.\nEvidence: Said so twice.\n- plain bullet\nA line."
        self.assertEqual(mod.doc_blocks(text, drop_title="User Profile"),
                         [("label", "Values"), ("pair", "Family first.", "Said so twice."),
                          ("bullet", "plain bullet"), ("para", "A line.")])

    def test_onboarding_pairs_questions_with_the_answers_that_follow(self):
        mod = load()
        convo = [{"role": "assistant", "message": "Welcome."}, {"role": "assistant", "message": "Q1?"},
                 {"role": "user", "message": "A1"}, {"role": "assistant", "message": "Bye."}]
        self.assertEqual(mod.onboarding_pairs(convo), [("Welcome.", []), ("Q1?", ["A1"]), ("Bye.", [])])


class Ratings(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.tz = ZoneInfo("America/Chicago")

    def test_previous_feedback_after_the_profile_with_the_note_and_no_snapshot(self):
        ratings = [{"rendererKey": "core-quote-renderer", "rating": 1, "feedback": "Spot on", "createdAt": "2026-10-01T12:00:00Z"},
                   {"rendererKey": None, "rating": -1, "feedback": None, "createdAt": "2026-10-02T12:00:00Z"},
                   {"rendererKey": "tending-renderer", "rating": None, "feedback": None, "createdAt": "2026-10-03T12:00:00Z"}]
        html_doc = self.mod.build_html(PROFILE, {"email": "a@example.com"}, None, self.tz, ratings)
        md = self.mod.build_markdown(PROFILE, {"email": "a@example.com"}, None, self.tz, ratings)
        for doc in (html_doc, md):
            self.assertIn("Previous profile feedback", doc)
            self.assertIn(self.mod.PREVIOUS_NOTE, doc)
            self.assertIn("Your quote and At your core", doc)
            self.assertIn("Your profile as a whole", doc)
            self.assertNotIn("What you are tending · ", doc.split("Previous profile feedback")[1], "no feedback, no line")
        self.assertLess(html_doc.index("Your onboarding answers"), html_doc.index("Previous profile feedback"))
        self.assertNotIn("Previous profile feedback", self.mod.build_html(PROFILE, {}, None, self.tz, []))

    def test_quiz_keeps_multi_paragraph_text_and_tells_intro_from_close(self):
        convo = [{"role": "assistant", "message": "Hi.\n\nWelcome."}, {"role": "assistant", "message": "Q1?"},
                 {"role": "user", "message": "A1"}, {"role": "assistant", "message": "Thanks.\n\nBye."}]
        prof = {**PROFILE, "onboardingConversation": convo}
        html_doc = self.mod.build_html(prof, {}, None, self.tz)
        self.assertIn("<b>Aurie introduced the quiz with:</b><p>Hi.</p><p>Welcome.</p>", html_doc)
        self.assertIn("<b>Aurie closed with:</b><p>Thanks.</p><p>Bye.</p>", html_doc)
        long_q = [{"role": "assistant", "message": "Line one.\n\nWhat now?"}, {"role": "user", "message": "A"}]
        md = self.mod.build_markdown({**PROFILE, "onboardingConversation": long_q}, {}, None, self.tz)
        self.assertIn("1. Line one.\n   \n   What now?", md)
        middle = [{"role": "assistant", "message": "Q1?"}, {"role": "user", "message": "A1"},
                  {"role": "assistant", "message": "NOTE-BETWEEN"}, {"role": "assistant", "message": "Q2?"},
                  {"role": "user", "message": "A2"}]
        mid_html = self.mod.build_html({**PROFILE, "onboardingConversation": middle}, {}, None, self.tz)
        self.assertIn('<li class="between"><div class="aside"><p>NOTE-BETWEEN</p></div></li>', mid_html)
        self.assertIn('<li value="2"><div class="q"><p>Q2?</p>', mid_html)
        no_intro = {**PROFILE, "onboardingConversation": convo[1:]}
        self.assertNotIn("introduced the quiz", self.mod.build_html(no_intro, {}, None, self.tz))


class Input(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))

    def write(self, **over):
        body = {"version": 1, "entity": "profile", "generatedAt": "x", "userIdPrefix": UID[:8], "rows": [PROFILE], **over}
        f = self.tmp / f"{uuid.uuid4().hex}.json"
        f.write_text(json.dumps(body))
        return f

    def test_refuses_another_version_section_user_or_row_count(self):
        self.assertEqual(self.mod.load_profile(self.write(), UID)["profile"], "Likes long walks.")
        for bad in ({"version": 2}, {"entity": "memory"}, {"userIdPrefix": "Someone1"}, {"rows": []},
                    {"rows": [PROFILE, PROFILE]}):
            with self.subTest(bad=list(bad)), self.assertRaises(SystemExit) as e:
                self.mod.load_profile(self.write(**bad), UID)
            self.assertNotIn(UID, str(e.exception))

    def test_a_failed_render_leaves_no_html_behind(self):
        made = []

        def boom(html_path, pdf_path):
            made.append(html_path)
            raise SystemExit("Chrome did not produce a PDF")
        def get(url, token, method="GET", body=None):
            return SUBS if "/subscriptions/list/" in url else USER
        with mock.patch.object(self.mod, "_get", get), mock.patch.object(self.mod, "render_pdf", boom), \
                mock.patch.dict(os.environ, {"AURA_API_TOKEN": "t"}), contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit):
            self.mod.main(["--uid", UID, "--from-file", str(self.write()), "--out", str(self.tmp / "p.pdf")])
        self.assertFalse(made[0].exists())
        self.assertFalse(made[0].parent.exists())

    def test_private_output(self):
        def get(url, token, method="GET", body=None):
            return SUBS if "/subscriptions/list/" in url else USER
        out_dir = self.tmp / "new"
        with mock.patch.object(self.mod, "_get", get), mock.patch.dict(os.environ, {"AURA_API_TOKEN": "t"}), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.mod.main(["--uid", UID, "--from-file", str(self.write()), "--format", "md",
                           "--out", str(out_dir / "p.md")])
        self.assertEqual(stat.S_IMODE(out_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((out_dir / "p.md").stat().st_mode), 0o600)
        self.assertNotIn(UID, out.getvalue())


if __name__ == "__main__":
    unittest.main()
