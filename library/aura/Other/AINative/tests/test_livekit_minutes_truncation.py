"""The store's LiveKit minute read must refuse a capped day, not keep its newest slice.

`store-aurie-user-daily-cost/scripts/livekit_minutes.py` is a derived copy of
`livekit_core.py` and allocates every LiveKit-path vendor on the minutes it returns. A read
that stops at its entry ceiling keeps only the newest part of the UTC day, so the stored
minutes, user count and LiveKit transport cost all come out low with no visible signal.
Pinned here:

1. `_walk_pages` raises `TruncatedReadError` when entries remain past the ceiling, and
   reads a day that fits without raising — including one that lands exactly on it.
2. The default ceiling sits well above a busy LiveKit day (~135k entries).
3. `store.py` maps the error to `LiveKitTruncated`, a `LiveKitUnavailable` subclass: report
   callers keep degrading it to a note, and the per-user store refuses the day.

Stdlib `unittest`, no network, no keys.
"""

from __future__ import annotations

import pathlib
import re
import sys
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_livekit_truncation import load  # noqa: E402  (stubs google.* for the import)

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/store-aurie-user-daily-cost/scripts"
MINUTES = SCRIPTS / "livekit_minutes.py"
STORE = SCRIPTS / "store.py"


class _FakeEntry:
    def __init__(self, i):
        self.i = i

    def to_api_repr(self):
        return {"jsonPayload": {"n": self.i}}


def _auth_yielding(n):
    client = types.SimpleNamespace(list_entries=lambda **kw: (_FakeEntry(i) for i in range(n)))
    return types.SimpleNamespace(client=client)


class TheReadRefusesACappedDay(unittest.TestCase):
    def setUp(self):
        self.lk = load(MINUTES)

    def test_entries_past_the_ceiling_raise(self):
        with self.assertRaises(self.lk.TruncatedReadError):
            list(self.lk._walk_pages(_auth_yielding(50), "f", page_size=1000, max_entries=10))

    def test_a_day_under_the_ceiling_reads_whole(self):
        got = list(self.lk._walk_pages(_auth_yielding(5), "f", page_size=1000, max_entries=10))
        self.assertEqual(len(got), 5)

    def test_a_day_exactly_at_the_ceiling_reads_whole(self):
        # Raising needs evidence that an entry was left behind, not just a full count.
        got = list(self.lk._walk_pages(_auth_yielding(10), "f", page_size=1000, max_entries=10))
        self.assertEqual(len(got), 10)

    def test_default_ceiling_clears_a_busy_livekit_day(self):
        self.assertGreaterEqual(self.lk.MAX_ENTRIES, 500_000)


def _function_body(src: str, name: str) -> str:
    start = src.index(f"def {name}")
    end = src.find("\ndef ", start + 1)
    return src[start:end if end != -1 else None]


class TheStoreStopsOnIt(unittest.TestCase):
    def setUp(self):
        self.src = STORE.read_text(encoding="utf-8")

    def test_the_read_maps_truncation_to_its_own_exception(self):
        body = _function_body(self.src, "livekit_minutes_for_day")
        trunc = re.search(r"except livekit_minutes\.TruncatedReadError", body)
        broad = re.search(r"except Exception", body)
        self.assertIsNotNone(trunc, "livekit_minutes_for_day does not catch TruncatedReadError")
        self.assertLess(trunc.start(), broad.start())
        self.assertIn("raise LiveKitTruncated", body[trunc.start():broad.start()])

    def test_library_callers_still_degrade(self):
        # A subclass of LiveKitUnavailable, so report callers that turn LiveKit failures
        # into a note keep doing so instead of the process exiting under them.
        self.assertRegex(self.src, r"class LiveKitTruncated\(LiveKitUnavailable\)")
        self.assertNotIn("sys.exit", _function_body(self.src, "livekit_minutes_for_day"))

    def test_the_store_refuses_the_day(self):
        m = re.search(r"except LiveKitTruncated as ex:(.*?)except LiveKitUnavailable as ex:",
                      self.src, re.S)
        self.assertIsNotNone(m, "the store does not catch LiveKitTruncated ahead of LiveKitUnavailable")
        self.assertIn("sys.exit", m.group(1))


if __name__ == "__main__":
    unittest.main()
