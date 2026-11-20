"""Tests that `normalize.parse_frames` reads the Data Access API's field names.

The API returns `line_number`, `column_number` and `in_project` in snake_case and
never emits the camelCase spellings. Reading camelCase against it fails silently
and totally: every `.get()` misses, so every frame reports no line number and
falls through to the `stack` default for `in_project`. Nothing raises, nothing
looks empty, and the stack still renders with plausible function names — so a
degraded stack is indistinguishable from a good one.

That is not hypothetical. A triage on 2026-09-03 concluded an iOS project was
entirely unsymbolicated — "0 of 97 frames carry a line number or inProject
marking" — when 100% of its app-source frames carried both, and its dSYM had
uploaded and processed normally.

The FRAME fixture below is copied verbatim from a real
GET /projects/<id>/events/<id> response.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
NORMALIZE = REPO / "plugins/eng/skills/investigate-bugsnag-errors/scripts/normalize.py"

# Verbatim from ai-aurie-ios, error 6a42843ad7a912dd24e39d45, 2026-09-03.
FRAME = {
    "column_number": 20,
    "in_project": True,
    "line_number": 44,
    "method": "static Logger.fileName(from:)",
    "file": "AuraConsumerApp/Services/Log/Logger+FileName.swift",
    "macho_uuid": "737614FE-2227-3732-A1D0-6FC9823D5914",
    "inlined": True,
}


def load():
    spec = importlib.util.spec_from_file_location("bugsnag_normalize", NORMALIZE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bugsnag_normalize"] = mod
    spec.loader.exec_module(mod)
    return mod


class ParseFramesFieldNames(unittest.TestCase):

    def setUp(self):
        self.normalize = load()

    def test_real_api_frame_yields_line_column_and_in_project(self):
        frame, = self.normalize.parse_frames([FRAME], stack="ios-native")

        self.assertEqual(frame.line, 44)
        self.assertEqual(frame.column, 20)
        self.assertTrue(frame.in_project)
        self.assertEqual(frame.method, "static Logger.fileName(from:)")

    def test_vendor_frame_is_not_marked_in_project(self):
        """The bug's second half: a false `in_project` cannot be seen in output.

        With the field unread, `ios-native` defaulted every frame to in-project,
        so `top_in_project_frame` always returned frame 0 and `(vendor)` never
        rendered on any frame of any iOS event.
        """
        vendor = dict(FRAME, in_project=False, file="/usr/lib/swift/libswift_Concurrency.dylib")

        frame, = self.normalize.parse_frames([vendor], stack="ios-native")

        self.assertFalse(frame.in_project)
        self.assertIn("(vendor)", self.normalize.format_frame(frame))

    def test_camel_case_notifier_payload_still_parses(self):
        """Accepted as a fallback so a hand-pasted notifier body still works."""
        camel = {
            "lineNumber": 44,
            "columnNumber": 20,
            "inProject": True,
            "method": "static Logger.fileName(from:)",
            "file": "AuraConsumerApp/Services/Log/Logger+FileName.swift",
        }

        frame, = self.normalize.parse_frames([camel], stack="ios-native")

        self.assertEqual(frame.line, 44)
        self.assertEqual(frame.column, 20)
        self.assertTrue(frame.in_project)

    def test_absent_field_still_falls_back_to_the_stack_default(self):
        bare = {"method": "f", "file": "a.swift"}

        ios, = self.normalize.parse_frames([bare], stack="ios-native")
        electron, = self.normalize.parse_frames([bare], stack="electron")

        self.assertTrue(ios.in_project)
        self.assertFalse(electron.in_project)
        self.assertIsNone(ios.line)

    def test_top_in_project_frame_skips_vendor_frames(self):
        vendor = dict(FRAME, in_project=False, method="swift_task_switch")
        app = dict(FRAME, in_project=True, method="ChatService.sync()")

        frames = self.normalize.parse_frames([vendor, app], stack="ios-native")

        self.assertEqual(self.normalize.top_in_project_frame(frames).method, "ChatService.sync()")


if __name__ == "__main__":
    unittest.main()
