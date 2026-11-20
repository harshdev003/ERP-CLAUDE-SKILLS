#!/usr/bin/env python3
"""`export_ai_items.py`: the data-request exports for moods, insights and affirmations.

One engine ships in each export skill (canonical: export-user-insights; docs/canonical-sources.md), so
every test runs against every copy. The rules pinned here:
- **Input is a file, never a route:** the section's JSON from eng:query-user-export-data. A file of
  another version, section or user is refused; the only network call is the user's timezone.
- **Verbatim, readable fields only:** ids, session ids, ratings, feedback, service and delivery
  flags never render. A row with nothing to show is counted, never silently dropped.
- **Who wrote it:** every entry carries its author label, explained once on the cover.
- **Private output:** files 0600, a created folder 0700; the summary line has a uid prefix only.
- **A failed render** never passes off an earlier PDF.

Stdlib `unittest`, no network: the timezone read is stubbed.
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
COPIES = sorted((REPO / "plugins").glob("*/skills/*/scripts/export_ai_items.py"))
UID = "TestUidTestUidTestUid0000001"
SECRET = "INTERNAL-FIELD-SENTINEL"
INTERNAL = {"id": SECRET, "userId": UID, "sessionId": SECRET,
            "service": SECRET, "notify": SECRET, "source": SECRET, "sessionStartAt": SECRET}

ROWS = {
    "moods": [
        {**INTERNAL, "createdAt": "2026-11-10T03:30:00Z", "kind": "session", "score": 3, "min": 2, "max": 4},
        {**INTERNAL, "createdAt": "2026-11-10T15:00:00Z", "kind": "daily", "score": 4, "min": 4, "max": 4},
        {**INTERNAL, "createdAt": "2026-11-10T16:00:00Z", "kind": "daily", "score": None},
    ],
    "insights": [
        {**INTERNAL, "createdAt": "2026-11-10T15:00:00Z", "kind": "session-insight", "title": "A steady day",
         "message": "You kept **going**.", "emotion": "calm", "mood": "good",
         "highlights": ["First highlight", {"text": "Second highlight"},
                        {"message": "Third highlight", "timestamp": SECRET, "id": SECRET, "url": SECRET,
                         "emotions": [{"name": SECRET}]}],
         "recommendations": ["Rest early", {"id": SECRET, "title": "Lake Meditation"}],
         "timeline": [{"createdAt": SECRET, "emotions": [{"name": SECRET, "score": 1}]}],
         "chatStarters": ["How was your walk?"]},
        {**INTERNAL, "createdAt": "2026-11-10T16:00:00Z", "kind": "weekly-insight", "title": "", "message": ""},
    ],
    "affirmations": [
        {**INTERNAL, "createdAt": "2026-11-10T15:00:00Z", "text": "I am enough."},
        {**INTERNAL, "createdAt": "2026-11-10T16:00:00Z", "text": "  "},
    ],
    "memory": [
        {**INTERNAL, "createdAt": "2026-11-10T15:00:00Z", "memory": "Likes long walks after work.", "recall": SECRET},
        {**INTERNAL, "createdAt": "2026-11-10T16:00:00Z", "memory": ""},
    ],
}
SHOWN = {
    "moods": ["Mood score: 3", "lowest 2, highest 4", "Mood score: 4", "Session", "Daily"],
    "insights": ["A steady day", "going", "Emotion: calm", "Mood: good", "First highlight", "Second highlight",
                 "Third highlight", "Lake Meditation", "Rest early", "How was your walk?", "Session insight"],
    "affirmations": ["I am enough."],
    "memory": ["Likes long walks after work.", "What Aurie remembers about you"],
}


def load(path):
    name = f"_export_ai_items_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class EveryCopy(unittest.TestCase):
    def setUp(self):
        self.assertTrue(COPIES, "no export_ai_items.py found")
        self.tz = ZoneInfo("America/Chicago")

    def each(self):
        for path in COPIES:
            with self.subTest(copy=str(path.relative_to(REPO))):
                yield load(path)

    def test_the_copies_are_identical(self):
        canonical = (REPO / "plugins/eng/skills/export-user-insights/scripts/export_ai_items.py").read_text(encoding="utf-8")
        for path in COPIES:
            self.assertEqual(path.read_text(encoding="utf-8"), canonical, f"{path} drifted from the canonical copy")

    def test_readable_fields_render_and_internal_ones_never_do(self):
        for mod in self.each():
            for entity, rows in ROWS.items():
                for build in (mod.build_html, mod.build_markdown):
                    doc, stats = build(rows, entity, self.tz)
                    self.assertNotIn(SECRET, doc)
                    self.assertNotIn(UID, doc)
                    for text in SHOWN[entity]:
                        self.assertIn(text, doc)
                    self.assertEqual(stats["entries"] + stats["empty"], stats["rows"])
                    self.assertEqual(stats["empty"], 1, "the row with nothing to show is counted")

    def test_every_entry_says_who_wrote_it(self):
        for mod in self.each():
            for entity, rows in ROWS.items():
                doc, stats = mod.build_html(rows, entity, self.tz)
                cover, body = doc.split('<div class="day">', 1)
                self.assertIn('class="legend"', cover)
                self.assertEqual(body.count('class="author '), stats["days"], "one chip per day: one author throughout")
                md, _ = mod.build_markdown(rows, entity, self.tz)
                label = mod.ENTITIES[entity]["author"][1]
                self.assertEqual(sum(1 for l in md.splitlines() if l.startswith(f"**{label}**")), stats["entries"])

    def test_local_day_grouping(self):
        for mod in self.each():
            days, _ = mod.prepare(ROWS["moods"], "moods", self.tz)
            # 03:30Z on the 10th is the evening of the 9th in Chicago.
            self.assertEqual([str(d) for d in days], ["2026-11-09", "2026-11-10"])

    def test_reads_its_section_file_and_refuses_another(self):
        for mod in self.each():
            with tempfile.TemporaryDirectory() as d:
                def write(**over):
                    body = {"version": 1, "entity": "insights", "generatedAt": "2026-11-16T00:00:00Z",
                            "userIdPrefix": UID[:8], "rows": ROWS["insights"], **over}
                    f = pathlib.Path(d) / f"{uuid.uuid4().hex}.json"
                    f.write_text(json.dumps(body))
                    return f
                self.assertEqual(mod.load_rows(write(), "insights", UID), ROWS["insights"])
                for bad in ({"version": 2}, {"entity": "affirmations"}, {"userIdPrefix": "Someone1"},
                            {"rows": None}):
                    with self.subTest(bad=bad), self.assertRaises(SystemExit) as e:
                        mod.load_rows(write(**bad), "insights", UID)
                    self.assertNotIn(UID, str(e.exception))
                with self.assertRaises(SystemExit):
                    mod.load_rows(pathlib.Path(d) / "missing.json", "insights", UID)
            src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
            self.assertNotIn("/list/", src, "no list route: the rows come from the file")
            self.assertNotIn("/compile", src)


class Feedback(unittest.TestCase):
    def test_the_users_feedback_shows_inside_its_item_only(self):
        for path in COPIES:
            mod = load(path)
            rows = [dict(r) for r in ROWS["insights"]]
            rows[0].update(rating=4, feedback="Very true")
            for build in (mod.build_html, mod.build_markdown):
                doc, _ = build(rows, "insights", ZoneInfo("America/Chicago"))
                self.assertEqual(doc.count("Very true"), 1)
                self.assertIn("★★★★☆ (4 of 5)", doc)
                self.assertEqual(doc.count("Your feedback"), 1)


class Main(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.dict(os.environ, {"AURA_API_TOKEN": "t", "AINATIVE_UA_WHO": "tester"}))

    def run_main(self, mod, *argv, rows=None):
        out = io.StringIO()

        def fake_pdf(html_path, pdf_path):
            pdf_path.write_bytes(b"%PDF")
        with mock.patch.object(mod, "load_rows", return_value=rows if rows is not None else ROWS["insights"]), \
                mock.patch.object(mod, "fetch_timezone", return_value="America/Chicago"), \
                mock.patch.object(mod, "render_pdf", fake_pdf), contextlib.redirect_stdout(out):
            mod.main(list(argv))
        return out.getvalue()

    def test_private_files_and_prefix_only_summary(self):
        for path in COPIES:
            mod = load(path)
            folder = self.tmp / path.parent.parent.name / "new"
            out = self.run_main(mod, "--entity", "insights", "--uid", UID, "--from-file", "x.json", "--format", "both",
                                "--out", str(folder / "ENGSUP-1-insights.pdf"))
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
            for name in ("ENGSUP-1-insights.pdf", "ENGSUP-1-insights.md"):
                self.assertEqual(stat.S_IMODE((folder / name).stat().st_mode), 0o600, name)
            self.assertIn(f"uid {UID[:8]}…", out)
            self.assertNotIn(UID, out)
            self.assertIn("1 with nothing to show", out)

    def test_a_failed_render_leaves_no_html_behind(self):
        for path in COPIES:
            mod = load(path)
            made = []

            def boom(html_path, pdf_path):
                made.append(html_path)
                raise SystemExit("Chrome did not produce a PDF.")
            with self.subTest(copy=path.parent.parent.name), \
                    mock.patch.object(mod, "load_rows", return_value=ROWS["insights"]), \
                    mock.patch.object(mod, "fetch_timezone", return_value="America/Chicago"), \
                    mock.patch.object(mod, "render_pdf", boom), contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit):
                mod.main(["--entity", "insights", "--uid", UID, "--from-file", "x.json",
                          "--out", str(self.tmp / f"{path.parent.parent.name}.pdf")])
            self.assertFalse(made[0].parent.exists())

    def test_refusals(self):
        mod = load(COPIES[0])
        with mock.patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(mod, "load_rows", return_value=ROWS["insights"]), \
                mock.patch.object(mod, "fetch_timezone", side_effect=AssertionError("network")), \
                mock.patch.object(mod, "render_pdf", lambda h, p: p.write_bytes(b"%PDF")):
            mod.main(["--entity", "insights", "--uid", UID, "--from-file", "x", "--timezone", "America/Chicago",
                      "--format", "md", "--out", str(self.tmp / "tz.md")])  # no token needed with --timezone
        for argv in (["--entity", "moods", "--from-file", "x"], ["--entity", "moods", "--uid", "has space", "--from-file", "x"],
                     ["--entity", "moods", "--uid", UID], ["--entity", "journal", "--uid", UID, "--from-file", "x"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                self.run_main(mod, *argv)
        with self.assertRaises(SystemExit) as e:
            self.run_main(mod, "--entity", "affirmations", "--uid", UID, "--from-file", "x",
                          rows=[{"createdAt": "2026-11-10T15:00:00Z", "text": ""}])
        self.assertNotIn(UID, str(e.exception))

    def test_a_failed_render_never_keeps_an_old_pdf(self):
        mod = load(COPIES[0])
        out = self.tmp / "x.pdf"
        for code, writes in ((1, True), (0, False)):
            out.write_bytes(b"%PDF old")

            def run(cmd, **kw):
                if writes:
                    out.write_bytes(b"%PDF partial")
                return mock.Mock(returncode=code)
            with self.subTest(code=code), mock.patch.object(mod, "find_chrome", return_value="chrome"), \
                    mock.patch.object(mod.subprocess, "run", run), self.assertRaises(SystemExit):
                mod.render_pdf(self.tmp / "x.html", out)
            self.assertFalse(out.exists())


class Span(unittest.TestCase):
    def test_a_span_across_years_keeps_the_first_year(self):
        import datetime as dt
        for path in COPIES:
            mod = load(path)
            self.assertEqual(mod.date_range(dt.date(2025, 7, 11), dt.date(2026, 10, 19)),
                             "July 11, 2025 – October 19, 2026")


if __name__ == "__main__":
    unittest.main()
