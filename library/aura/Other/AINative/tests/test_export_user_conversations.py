#!/usr/bin/env python3
"""`eng:export-user-conversations`: conversations and daily check-ins, from the SQL export file.

The rules pinned here:
- **Input is eng:query-user-export-data's file**, never a route; another version, section or user
  is refused.
- **Check-ins have their own document:** conversations flagged as a daily practice (morning,
  evening, walking) are only in `--part checkins`, never twice. The main cover says they are in a
  separate file, and the check-ins cover counts each practice.
- **Onboarding is left out** unless asked for; every message carries its author chip.

Stdlib `unittest`, no network: the timezone is passed with `--timezone`.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import stat
import sys
import tempfile
import unittest
import uuid
from unittest import mock
from zoneinfo import ZoneInfo

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/eng/skills/export-user-conversations/scripts/export_conversations.py"
UID = "TestUidTestUidTestUid0000001"


def load():
    name = f"_export_conversations_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def msg(role, text, at):
    return {"role": role, "message": text, "createdAt": at}


ROWS = [
    {"id": "c1", "practice": None, "isOnboarding": False, "rating": None, "feedback": None,
     "messages": [msg("user", "hello there", "2026-11-07T16:00:00.000Z"),
                  {**msg("assistant", "Hi!", "2026-11-07T16:01:00.000Z"), "rating": -1, "feedback": "Too short"}]},
    {"id": "c2", "practice": "morning", "isOnboarding": False,
     "messages": [msg("assistant", "Good morning", "2026-11-08T15:00:00.000Z"), msg("user", "fine", "2026-11-08T15:01:00.000Z")]},
    {"id": "c3", "practice": "evening", "isOnboarding": False,
     "messages": [msg("assistant", "Wind down", "2026-11-09T03:00:00.000Z")]},
    {"id": "c4", "practice": "walking", "isOnboarding": False,
     "messages": [msg("assistant", "Walk with me", "2026-11-09T15:00:00.000Z")]},
    {"id": "c5", "practice": None, "isOnboarding": True,
     "messages": [msg("assistant", "Welcome to Aurie", "2026-11-06T15:00:00.000Z")]},
    {"id": "c6", "practice": None, "isOnboarding": False, "removed": True,
     "messages": [msg("user", "REMOVED-BY-USER", "2026-11-06T16:00:00.000Z")]},
]


class File(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))

    def write(self, **over):
        body = {"version": 1, "entity": "conversations", "generatedAt": "x", "userIdPrefix": UID[:8],
                "rows": ROWS, **over}
        f = self.tmp / f"{uuid.uuid4().hex}.json"
        f.write_text(json.dumps(body))
        return f

    def test_practices_go_to_checkins_only_and_never_twice(self):
        main = self.mod.load_export(self.write(), UID, "conversations")
        checkins = self.mod.load_export(self.write(), UID, "checkins")
        self.assertEqual([c["id"] for c in main["conversations"]], ["c1"])
        self.assertEqual([c["id"] for c in checkins["conversations"]], ["c2", "c3", "c4"])
        self.assertEqual(main["practice_counts"], {"morning": 1, "evening": 1, "walking": 1})
        self.assertEqual(main["left_out"], {"practice": 3, "onboarding": 1, "removed": 1})

    def test_onboarding_only_when_asked(self):
        got = self.mod.load_export(self.write(), UID, "conversations", include_onboarding=True)
        self.assertIn("c5", [c["id"] for c in got["conversations"]])

    def test_refuses_another_version_section_or_user(self):
        for bad in ({"version": 2}, {"entity": "journal"}, {"userIdPrefix": "Someone1"}, {"rows": None}):
            with self.subTest(bad=bad), self.assertRaises(SystemExit) as e:
                self.mod.load_export(self.write(**bad), UID, "conversations")
            self.assertNotIn(UID, str(e.exception))

    def test_no_route_is_called(self):
        self.assertNotIn("/ai/conversations", SCRIPT.read_text())


class Documents(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.tz = ZoneInfo("America/Chicago")
        tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        f = tmp / "c.json"
        f.write_text(json.dumps({"version": 1, "entity": "conversations", "generatedAt": "x",
                                 "userIdPrefix": UID[:8], "rows": ROWS}))
        self.file = f

    def build(self, part):
        export = self.mod.load_export(self.file, UID, part)
        doc = self.mod.document(part, export["practice_counts"])
        html_doc, stats = self.mod.build_html(self.mod.flatten(export), self.tz, 4000, doc)
        md, _ = self.mod.build_markdown(self.mod.flatten(export), self.tz, 4000, doc)
        return html_doc, md, stats

    def test_main_cover_points_to_the_checkins_file(self):
        html_doc, md, stats = self.build("conversations")
        self.assertIn("Your conversations with Aurie", html_doc)
        self.assertIn("are in a separate file", html_doc)
        self.assertIn("are in a separate file", md)
        self.assertNotIn("Good morning", html_doc + md)
        self.assertNotIn("Welcome to Aurie", html_doc + md)
        self.assertNotIn("REMOVED-BY-USER", html_doc + md)
        self.assertEqual(html_doc.count('class="author '), stats["user_turns"] + stats["aurie_turns"] + 2)  # + legend

    def test_checkins_cover_counts_each_practice_and_heads_name_it(self):
        html_doc, md, stats = self.build("checkins")
        self.assertIn("Your daily check-ins with Aurie", html_doc)
        for label in ("Morning check-ins</span><span>1", "Evening wind-downs</span><span>1",
                      "Walking practices</span><span>1"):
            self.assertIn(label, html_doc)
        for head in ("### Morning check-in ·", "### Evening wind-down ·", "### Walking practice ·"):
            self.assertIn(head, md)
        self.assertNotIn("hello there", html_doc + md)

    def test_an_empty_part_says_where_the_rest_is_and_writes_nothing(self):
        tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        only = tmp / "only.json"
        only.write_text(json.dumps({"version": 1, "entity": "conversations", "generatedAt": "x",
                                    "userIdPrefix": UID[:8], "rows": ROWS[1:3]}))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.mod.main(["--uid", UID, "--from-file", str(only), "--timezone", "America/Chicago",
                           "--format", "md", "--out", str(tmp / "x.md")])
        self.assertIn("nothing to write", out.getvalue())
        self.assertIn("2 in the checkins document", out.getvalue())
        self.assertFalse((tmp / "x.md").exists())

    def test_kept_html_is_private(self):
        out_dir = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        with contextlib.redirect_stdout(io.StringIO()):
            self.mod.main(["--uid", UID, "--from-file", str(self.file), "--timezone", "America/Chicago",
                           "--html-only", "--out", str(out_dir / "x.pdf")])
        self.assertEqual(stat.S_IMODE((out_dir / "x.html").stat().st_mode), 0o600)

    def test_a_failed_render_leaves_no_html_behind(self):
        out_dir = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        made = []

        def boom(html_path, pdf_path):
            made.append(html_path)
            raise SystemExit("Chrome did not produce a PDF.")
        with mock.patch.object(self.mod, "render_pdf", boom), contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit):
            self.mod.main(["--uid", UID, "--from-file", str(self.file), "--timezone", "America/Chicago",
                           "--out", str(out_dir / "x.pdf")])
        self.assertFalse(made[0].parent.exists())

    def test_print_design_one_chip_per_run_running_header_and_months(self):
        html_doc, _, stats = self.build("checkins")
        body = html_doc.split('<div class="day', 1)[1]
        runs = body.count('<div class="run ')
        self.assertEqual(body.count('class="author '), runs, "one chip per run of messages, not per message")
        self.assertIn("@top-left { content: \"Your daily check-ins with Aurie · November 8 – November 9, 2026\"", html_doc)
        self.assertIn('counter(page) " of " counter(pages)', html_doc)
        self.assertIn('<h2 class="month" id="m-2026-11">November 2026</h2>', html_doc)
        self.assertEqual(self.mod.time_span(*[__import__("datetime").datetime(2026, 1, 1, 9, 0)] * 2), "9:00 AM")

    def test_chips_name_the_speaker_not_a_writer(self):
        html_doc, md, _ = self.build("conversations")
        self.assertIn('<span class="author you">You</span>', html_doc)
        self.assertIn('<span class="author aurie">Aurie</span>', html_doc)
        self.assertNotIn("Written by", html_doc + md)
        self.assertIn("what you said or typed", html_doc)

    def test_feedback_on_a_reply_shows_under_that_reply_only(self):
        html_doc, md, _ = self.build("conversations")
        self.assertEqual(html_doc.count("Too short"), 1)
        self.assertIn("👎", html_doc)
        self.assertEqual(html_doc.count('class="fb"'), 1, "no feedback, no line")
        self.assertIn("*Your feedback:* 👎 · “Too short”", md)

    def test_conversation_feedback_prints_once_on_its_last_day(self):
        rows = [{"id": "x", "practice": None, "isOnboarding": False, "rating": 1, "feedback": "Lovely chat",
                 "messages": [msg("user", "day one", "2026-11-07T16:00:00.000Z"),
                              msg("assistant", "day two", "2026-11-08T16:00:00.000Z")]}]
        tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory())) / "x.json"
        tmp.write_text(json.dumps({"version": 1, "entity": "conversations", "generatedAt": "x",
                                   "userIdPrefix": UID[:8], "rows": rows}))
        export = self.mod.load_export(tmp, UID, "conversations")
        html_doc, _ = self.mod.build_html(self.mod.flatten(export), self.tz, 4000)
        md, _ = self.mod.build_markdown(self.mod.flatten(export), self.tz, 4000)
        self.assertEqual(html_doc.count("Lovely chat"), 1)
        self.assertEqual(md.count("Lovely chat"), 1)
        self.assertGreater(html_doc.index("Lovely chat"), html_doc.index("day two"))

    def test_output_files_are_private(self):
        out_dir = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory())) / "new"
        with contextlib.redirect_stdout(io.StringIO()):
            self.mod.main(["--uid", UID, "--from-file", str(self.file), "--part", "checkins",
                           "--timezone", "America/Chicago", "--format", "md", "--out", str(out_dir / "x.md")])
        self.assertEqual(stat.S_IMODE(out_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((out_dir / "x.md").stat().st_mode), 0o600)


class Render(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_a_pdf_from_an_earlier_run_never_passes_for_a_failed_one(self):
        out = self.tmp / "c.pdf"
        for code, writes in ((1, True), (0, False), (1, False)):
            out.write_bytes(b"%PDF old")

            def run(cmd, **kw):
                if writes:
                    out.write_bytes(b"%PDF partial")
                return mock.Mock(returncode=code)
            with self.subTest(code=code, writes=writes), \
                    mock.patch.object(self.mod, "find_chrome", return_value="chrome"), \
                    mock.patch.object(self.mod.subprocess, "run", run), self.assertRaises(SystemExit):
                self.mod.render_pdf(self.tmp / "x.html", out)
            self.assertFalse(out.exists())

    def test_a_new_pdf_passes_and_chrome_output_is_decoded_as_utf8(self):
        seen = {}

        def run(cmd, **kw):
            seen.update(kw)
            pathlib.Path(cmd[-2].split("=", 1)[1]).write_bytes(b"%PDF")
            return mock.Mock(returncode=0)
        with mock.patch.object(self.mod, "find_chrome", return_value="chrome"), \
                mock.patch.object(self.mod.subprocess, "run", run):
            self.mod.render_pdf(self.tmp / "x.html", self.tmp / "é.pdf")
        self.assertTrue((self.tmp / "é.pdf").exists())
        self.assertEqual((seen["encoding"], seen["errors"]), ("utf-8", "replace"))


class Span(unittest.TestCase):
    def test_a_span_across_years_keeps_the_first_year(self):
        import datetime as dt
        mod = load()
        self.assertEqual(mod.date_range(dt.date(2024, 10, 11), dt.date(2026, 11, 11)),
                         "October 11, 2024 – November 11, 2026")
        self.assertEqual(mod.date_range(dt.date(2026, 1, 2), dt.date(2026, 3, 4)), "January 2 – March 4, 2026")


if __name__ == "__main__":
    unittest.main()
