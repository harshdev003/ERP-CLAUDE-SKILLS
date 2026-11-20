"""report-cartesia-quota anchors its billing period to the 21st, the day Cartesia renews (ENG-1702).

The default was 11, so a run on 2026-10-29 measured from 10-11 instead of 10-21 and overstated
consumed, projected and overage credits. The canonical renewal day is `Tools/Cartesia` in AI Knowledge.
"""
import importlib.util
import pathlib
import sys
import unittest
from datetime import datetime, timezone

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "plugins/eng/skills/report-cartesia-quota/scripts/report.py"


def load():
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec = importlib.util.spec_from_file_location("cartesia_report_under_test", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(SCRIPT.parent))


class RenewalDay(unittest.TestCase):
    def test_the_default_period_starts_on_the_21st(self):
        mod = load()
        self.assertEqual(mod.DEFAULT_RENEWAL_DAY, 21)
        utc = lambda *a: datetime(*a, tzinfo=timezone.utc)
        self.assertEqual(mod.billing_period_start(utc(2026, 10, 29, 12), mod.DEFAULT_RENEWAL_DAY), utc(2026, 10, 21))
        self.assertEqual(mod.billing_period_start(utc(2026, 11, 3), mod.DEFAULT_RENEWAL_DAY), utc(2026, 10, 21))
        self.assertEqual(mod.billing_period_start(utc(2026, 11, 21), mod.DEFAULT_RENEWAL_DAY), utc(2026, 11, 21))


if __name__ == "__main__":
    unittest.main()
