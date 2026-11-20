#!/usr/bin/env python3
"""The run log labels a negative LiveKit unused-capacity delta as a reversal (ENG-1440).

`livekit_plan.unused_capacity_usd` is a signed month-to-date delta: a day consuming more
than its share of the subscription accrual goes negative and reverses capacity earlier
days booked. 2026-09-28 printed `livekit-unused $-9.31 [paid-for capacity nobody used]`,
which read as a bug. Only the label changes; the value is pinned unchanged here too.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_el_caller_mapping import _stubbed_google  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/store-aurie-user-daily-cost/scripts"


def _load(path: pathlib.Path, name: str):
    with _stubbed_google():
        sys.path.insert(0, str(path.parent))
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


S = _load(SCRIPTS / "store.py", "store_under_unused_label_test")

POSITIVE = "paid-for capacity nobody used"
REVERSAL = "reverses earlier unused capacity: consumption above today's accrual"


class LivekitUnusedLabel(unittest.TestCase):
    def test_positive_keeps_the_idle_capacity_label(self):
        self.assertEqual(S.livekit_unused_label(16.67), POSITIVE)

    def test_zero_is_not_a_reversal(self):
        self.assertEqual(S.livekit_unused_label(0.0), POSITIVE)
        self.assertEqual(S.livekit_unused_label(-0.004), POSITIVE)  # prints $-0.00

    def test_negative_is_labelled_a_reversal(self):
        self.assertEqual(S.livekit_unused_label(-9.31), REVERSAL)

    def test_the_2026_09_28_day_is_negative_and_labelled_a_reversal(self):
        # Scale $500/mo, 30-day month: $129.19 unused to date vs $138.50 the day before.
        v = S.livekit_plan.unused_capacity_usd("scale", "2026-09-28", 31_150, 33_748 - 31_150)
        self.assertAlmostEqual(v, -9.31, places=2)
        self.assertEqual(S.livekit_unused_label(v), REVERSAL)

    def test_both_run_log_lines_use_the_helper(self):
        src = (SCRIPTS / "store.py").read_text()
        self.assertNotIn("[paid-for capacity nobody used]", src)
        self.assertEqual(src.count("livekit_unused_label(day_livekit_unused)"), 2)


if __name__ == "__main__":
    unittest.main()
