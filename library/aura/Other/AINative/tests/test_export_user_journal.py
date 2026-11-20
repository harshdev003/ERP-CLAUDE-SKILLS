#!/usr/bin/env python3
"""`eng:export-user-journal`: a user's journal, as the app shows it, by local day, verbatim.

The rules pinned here:
- **Shown:** `daily` and `onboarding` entries (By Aurie), `user` entries (By you), saved
  `affirmation` entries, with only the fields the app shows. **Never** rating, feedback, notification,
  emotions, impacts or mood. Any other type is left out **and counted by type**, never silently dropped.
- **Read-only:** pages `POST /ai/journal/list/:userId` until a short page, and never calls
  `/ai/journal/daily/:userId` (which builds an entry).
- **Local day:** entries are grouped by the user's timezone, not UTC.
- **No identifiers out:** the summary line carries the UID prefix only; the cover carries no UID.
- **Structure-safe Markdown:** a `# heading` a user typed can't take over the day outline.

Stdlib `unittest`, no network: the HTTP call is stubbed at `_post` / `urlopen`.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
import urllib.error
import uuid
from unittest import mock
from zoneinfo import ZoneInfo

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/eng/skills/export-user-journal/scripts/export_journal.py"
UID = "TestUidTestUidTestUid0000001"
SECRET = "INTERNAL-FIELD-SENTINEL"


def load():
    name = f"_export_journal_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def rows():
    # rating and feedback are the user's own feedback, shown since the approver's decision: not internal.
    internal = {"notification": {"title": SECRET, "message": SECRET},
                "emotions": [SECRET], "impacts": [SECRET], "mood": 3, "id": SECRET, "userId": UID}
    return [
        {**internal, "type": "daily", "createdAt": "2026-11-10T03:30:00.000Z", "title": "A calm evening",
         "summary": "You talked about **rest**.", "insights": [{"quote": "I need a break", "context": "You noticed it."}],
         "affirmations": ["I deserve rest."]},
        {**internal, "type": "user", "createdAt": "2026-11-10T15:00:00.000Z", "title": None,
         "notes": "# My own heading\nToday was fine.", "insights": ["Plain insight text"], "affirmations": None,
         "summary": None},
        {**internal, "type": "affirmation", "createdAt": "2026-11-11T12:00:00.000Z", "title": "I am enough.",
         "affirmations": ["I am enough."]},
        {**internal, "type": "onboarding", "createdAt": "2026-11-09T12:00:00.000Z", "title": "Welcome",
         "summary": "Your first entry.", "insights": [], "affirmations": []},
        {**internal, "type": "checkin", "createdAt": "2026-11-11T13:00:00.000Z", "notes": "check-in note"},
        {**internal, "type": None, "createdAt": "2026-11-11T14:00:00.000Z", "notes": "untyped"},
    ]


class Content(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.tz = ZoneInfo("America/Chicago")

    def test_shown_types_and_counted_left_outs(self):
        days, stats = self.mod.prepare(rows(), self.tz)
        self.assertEqual(stats["rows"], 6)
        self.assertEqual(stats["entries"], 4)
        self.assertEqual(stats["left_out"], {"checkin": 1, "(no type)": 1})
        self.assertEqual(stats["entries"] + sum(stats["left_out"].values()), stats["rows"])
        self.assertEqual(stats["by_kind"], {"daily": 1, "user": 1, "affirmation": 1, "onboarding": 1})

    def test_grouped_by_the_users_local_day(self):
        days, _ = self.mod.prepare(rows(), self.tz)
        # 2026-11-10T03:30Z is the evening of Nov 9 in Chicago, so the daily entry joins Nov 9.
        self.assertEqual([str(d) for d in days], ["2026-11-09", "2026-11-10", "2026-11-11"])
        self.assertEqual([e["kind"] for e in days[list(days)[0]]], ["onboarding", "daily"])

    def test_internal_fields_never_render(self):
        for build in (self.mod.build_html, self.mod.build_markdown):
            doc, _ = build(rows(), self.tz)
            self.assertNotIn(SECRET, doc)
            self.assertNotIn(UID, doc)
            self.assertNotIn("check-in note", doc)
            for shown in ("A calm evening", "rest", "I need a break", "You noticed it.", "I deserve rest.",
                          "Today was fine.", "Plain insight text", "I am enough.", "Your first entry."):
                self.assertIn(shown, doc)
            self.assertIn("Written by you", doc)
            self.assertIn("Written by Aurie", doc)

    def test_every_entry_says_who_wrote_it_and_the_cover_explains_it(self):
        html_doc, stats = self.mod.build_html(rows(), self.tz)
        body = html_doc.split('<div class="day">', 1)[1]
        self.assertEqual(body.count('class="author you"'), stats["by_kind"]["user"])
        self.assertEqual(body.count('class="author aurie"'), stats["entries"] - stats["by_kind"]["user"])
        cover = html_doc.split('<div class="day">', 1)[0]
        self.assertIn('class="legend"', cover)
        # Aurie's insight on the user's own entry is marked as Aurie's.
        self.assertIn("Insights &middot; written by Aurie", body)
        md, _ = self.mod.build_markdown(rows(), self.tz)
        entry_lines = [l for l in md.splitlines() if l.startswith("**Written by ")]
        self.assertEqual(len(entry_lines), stats["entries"])
        self.assertIn("*Insights · written by Aurie*", md)

    def test_days_flow_on_and_each_heading_keeps_its_first_entry(self):
        html_doc, stats = self.mod.build_html(rows(), self.tz)
        self.assertIn('<div class="flow">', html_doc)
        self.assertEqual(html_doc.count('<div class="keep">'), stats["days"])
        self.assertIn('counter(page) " of " counter(pages)', html_doc)

    def test_the_users_feedback_shows_inside_its_entry_and_nowhere_else(self):
        data = rows()
        data[0]["rating"], data[0]["feedback"] = 1, "Spot on"
        data[1]["rating"], data[1]["feedback"] = None, None
        for build in (self.mod.build_html, self.mod.build_markdown):
            doc, _ = build(data, self.tz)
            self.assertEqual(doc.count("Spot on"), 1)
            self.assertIn("👍", doc)
            self.assertEqual(doc.count("Your feedback"), 1, "an entry without feedback prints nothing")
        self.assertEqual(self.mod.rating_text(-1), "👎")
        self.assertEqual(self.mod.rating_text(4), "★★★★☆ (4 of 5)")

    def test_a_typed_heading_cannot_take_over_the_outline(self):
        md, _ = self.mod.build_markdown(rows(), self.tz)
        self.assertIn("\\# My own heading", md)
        self.assertNotIn("\n# My own heading", md)

    def test_an_empty_title_shows_the_apps_placeholder_and_a_title_shows_as_written(self):
        for title in (None, "", "   "):
            with self.subTest(title=title):
                self.assertEqual(self.mod.entry({"type": "user", "title": title, "notes": "x"})["title"],
                                 "Untitled Journal")
        self.assertEqual(self.mod.entry({"type": "user", "title": " Planning ", "notes": "x"})["title"], "Planning")
        row = {"type": "user", "title": "", "notes": "x"}
        self.mod.entry(row)
        self.assertEqual(row["title"], "", "the placeholder is added to the export, never to the row")
        for build, heading in ((self.mod.build_markdown, "### Untitled Journal"), (self.mod.build_html, "Untitled Journal")):
            doc, _ = build(rows(), self.tz)
            self.assertEqual(doc.count(heading), 1)
            self.assertIn("A calm evening", doc)
        self.assertEqual(self.mod.entry({"type": "affirmation", "title": "", "affirmations": ["A."]})["title"], "")

    def test_affirmation_falls_back_to_the_first_affirmation(self):
        e = self.mod.entry({"type": "affirmation", "title": "", "affirmations": ["From the list."]})
        self.assertEqual(e["body"], "From the list.")


class File(unittest.TestCase):
    """The rows come from eng:query-user-export-data's journal file, never a route."""

    def setUp(self):
        self.mod = load()
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))

    def write(self, **over):
        body = {"version": 1, "entity": "journal", "generatedAt": "x", "userIdPrefix": UID[:8],
                "rows": rows() + [{"type": "user", "createdAt": "2026-11-11T15:00:00Z", "notes": "gone", "removed": True}],
                **over}
        f = self.tmp / f"{uuid.uuid4().hex}.json"
        f.write_text(json.dumps(body))
        return f

    def test_reads_the_file_and_leaves_out_removed_entries(self):
        got, removed = self.mod.load_rows(self.write(), UID)
        self.assertEqual((len(got), removed), (len(rows()), 1))
        self.assertFalse(any(r.get("removed") for r in got))

    def test_refuses_another_version_section_or_user(self):
        for bad in ({"version": 2}, {"entity": "conversations"}, {"userIdPrefix": "Someone1"}, {"rows": None}):
            with self.subTest(bad=bad), self.assertRaises(SystemExit) as e:
                self.mod.load_rows(self.write(**bad), UID)
            self.assertNotIn(UID, str(e.exception))

    def test_no_journal_route_is_called(self):
        src = SCRIPT.read_text()
        self.assertNotIn("/ai/journal/list", src)
        self.assertNotIn("/ai/journal/daily", src.replace("`POST /ai/journal/daily/:userId`", ""))


class Main(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.dict(os.environ, {"AURA_API_TOKEN": "t", "AINATIVE_UA_WHO": "tester"}))

    def run_main(self, *argv, data=None):
        out = io.StringIO()
        made = []

        def fake_pdf(html_path, pdf_path):
            made.append(html_path)
            pdf_path.write_bytes(b"%PDF")
        if "--from-file" not in argv:
            argv = (*argv, "--from-file", "x.json")
        with mock.patch.object(self.mod, "load_rows", return_value=(data if data is not None else rows(), 0)), \
                mock.patch.object(self.mod, "fetch_timezone", return_value="America/Chicago"), \
                mock.patch.object(self.mod, "render_pdf", fake_pdf), contextlib.redirect_stdout(out):
            self.mod.main(list(argv))
        return out.getvalue(), made

    def test_both_formats_summary_has_the_prefix_only_and_html_is_deleted(self):
        out, made = self.run_main("--uid", UID, "--format", "both", "--out", str(self.tmp / "j.pdf"))
        self.assertTrue((self.tmp / "j.pdf").exists() and (self.tmp / "j.md").exists())
        self.assertIn(f"uid {UID[:8]}…", out)
        self.assertNotIn(UID, out)
        self.assertIn("4 entries of 6 rows", out)
        self.assertIn("left out (not in this export): (no type) 1, checkin 1", out)
        self.assertFalse(made[0].exists(), "the intermediate HTML is deleted")
        self.assertNotIn(UID, (self.tmp / "j.md").read_text())

    def test_a_failed_render_leaves_no_html_behind(self):
        made = []

        def boom(html_path, pdf_path):
            made.append(html_path)
            raise SystemExit("Chrome did not produce a PDF.")
        with mock.patch.object(self.mod, "load_rows", return_value=(rows(), 0)), \
                mock.patch.object(self.mod, "fetch_timezone", return_value="America/Chicago"), \
                mock.patch.object(self.mod, "render_pdf", boom), contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit):
            self.mod.main(["--uid", UID, "--from-file", "x.json", "--out", str(self.tmp / "j.pdf")])
        self.assertFalse(made[0].parent.exists())

    def test_removed_entries_count_in_rows_and_left_out(self):
        out = io.StringIO()
        with mock.patch.object(self.mod, "load_rows", return_value=(rows(), 3)), \
                mock.patch.object(self.mod, "fetch_timezone", return_value="America/Chicago"), \
                contextlib.redirect_stdout(out):
            self.mod.main(["--uid", UID, "--from-file", "x.json", "--format", "md", "--out", str(self.tmp / "r.md")])
        self.assertIn("4 entries of 9 rows", out.getvalue())
        self.assertIn("removed in the app 3", out.getvalue())

    def test_bad_inputs_refuse(self):
        for argv in (["--uid", "has space"], ["--from-file", "x.json"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                self.run_main(*argv)

    def test_nothing_to_export_says_why(self):
        with self.assertRaises(SystemExit) as e:
            self.run_main("--uid", UID, data=[{"type": "checkin", "createdAt": "2026-11-11T13:00:00Z"}])
        self.assertIn("left out", str(e.exception))
        self.assertNotIn(UID, str(e.exception))

    def test_a_timezone_that_cannot_be_read_refuses_rather_than_shifting_days(self):
        def boom(*a):
            raise self.mod.TimezoneError("GET /users/:userId answered 503")
        with mock.patch.object(self.mod, "load_rows", return_value=(rows(), 0)), \
                mock.patch.object(self.mod, "fetch_timezone", boom), self.assertRaises(SystemExit) as e:
            self.mod.main(["--uid", UID, "--from-file", "x.json", "--format", "md", "--out", str(self.tmp / "j.md")])
        self.assertIn("--timezone", str(e.exception))
        self.assertFalse((self.tmp / "j.md").exists())

    def test_fetch_timezone_raises_on_http_errors(self):
        def fail(req, timeout=None):
            raise urllib.error.HTTPError("u", 401, "x", {}, io.BytesIO(b"{}"))
        with mock.patch("urllib.request.urlopen", fail), self.assertRaises(self.mod.TimezoneError):
            self.mod.fetch_timezone("https://h", "t", UID)

    def test_timezone_choices(self):
        with mock.patch.object(self.mod, "fetch_timezone", return_value=None):
            tz, how = self.mod.resolve_timezone("https://h", "t", UID, None)
        self.assertEqual((str(tz), how), (self.mod.DEFAULT_TZ, "profile has none: the column default"))
        self.assertEqual(str(self.mod.resolve_timezone("h", "t", None, "Europe/Paris")[0]), "Europe/Paris")
        for uid, override in ((None, None), (UID, "Not/AZone")):
            with self.subTest(uid=uid, override=override), self.assertRaises(SystemExit):
                self.mod.resolve_timezone("h", "t", uid, override)

    def test_chrome_output_is_decoded_as_utf8(self):
        seen = {}

        def run(cmd, **kw):
            seen.update(kw)
            pathlib.Path(cmd[-2].split("=", 1)[1]).write_bytes(b"%PDF")
            return mock.Mock(returncode=0)
        with mock.patch.object(self.mod, "find_chrome", return_value="chrome"), \
                mock.patch.object(self.mod.subprocess, "run", run):
            self.mod.render_pdf(self.tmp / "x.html", self.tmp / "é.pdf")
        self.assertEqual((seen["encoding"], seen["errors"]), ("utf-8", "replace"))

    def test_a_pdf_from_an_earlier_run_never_passes_for_a_failed_one(self):
        out = self.tmp / "j.pdf"
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

    def test_uid_prefix_keeps_the_provider(self):
        self.assertEqual(self.mod.uid_prefix("google:1234567890"), "google:12345678")
        self.assertEqual(self.mod.uid_prefix(UID), UID[:8])
        out, _ = self.run_main("--uid", "google:1234567890", "--format", "md", "--out", str(self.tmp / "g.md"))
        self.assertIn("uid google:12345678…", out)
        self.assertNotIn("1234567890", out)

    def test_it_needs_the_token_only_to_read_the_timezone(self):
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(SystemExit) as e:
            self.mod.main(["--uid", UID, "--from-file", "x.json"])
        self.assertIn("--timezone", str(e.exception))
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(self.mod, "load_rows", return_value=(rows(), 0)), \
                mock.patch.object(self.mod, "fetch_timezone", side_effect=AssertionError("network")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.mod.main(["--uid", UID, "--from-file", "x.json", "--timezone", "America/Chicago",
                           "--format", "md", "--out", str(self.tmp / "t.md")])
        self.assertTrue((self.tmp / "t.md").exists())


class Span(unittest.TestCase):
    def test_a_span_across_years_keeps_the_first_year(self):
        import datetime as dt
        mod = load()
        self.assertEqual(mod.date_range(dt.date(2024, 10, 11), dt.date(2026, 11, 11)),
                         "October 11, 2024 – November 11, 2026")
        self.assertEqual(mod.date_range(dt.date(2026, 1, 2), dt.date(2026, 3, 4)), "January 2 – March 4, 2026")


if __name__ == "__main__":
    unittest.main()
