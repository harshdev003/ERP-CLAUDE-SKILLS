#!/usr/bin/env python3
"""`fetch_cancellations.py` — the clock the k8bot `Purchased` string is rendered in.

The defect this guards
----------------------
The k8bot metadata line renders `Purchased` as a **US Pacific wall clock**
(`Purchased: Aug 10, 2026, 11:24 AM`), while the cancellation instant is the
Slack message `ts` — a true UTC epoch. `parse_purchased` used to stamp
`tzinfo=utc` onto the parsed wall clock, so the subtraction mixed clocks and
added the entire UTC offset to every gap.

Observed 2026-08-10 on uid `ildkmzwJ…` during a Group 4 cancellation sweep: a
cancel tapped ~30 seconds after purchase (Mixpanel: 18:24:40 purchased →
18:25:10 cancel tapped → 18:25:16 reason submitted) was emitted as
`days_to_cancel: 0.29` — 7.006 h, exactly the PDT offset.

Why that matters more than the rounding suggests: triage keys off how fast the
cancel came. A 30-second cancel inside the purchase flow means the price shape
drove it, because nothing post-purchase had happened yet; a 7-hour one reads as
a short trial of the product. The artifact pushed every instant-cancel into the
second category across the whole feed, the tracked "no-trial paywall pattern"
count included.

Why unit tests rather than a live check
--------------------------------------
Nothing here needs the network — `parse_purchased` and `parse_entry` are pure —
but the interesting inputs are ones a live sweep cannot choose. The offset the
bug adds is invisible on a multi-day cancel (0.29 d lost in 12 d rounds to
nothing) and only glaring on a same-second one; and the DST cases are reachable
two days a year. So the clock, the date and the boundary all have to be supplied
by the test.

The assertions are anchored on **UTC instants**, not on the offset the code
happens to apply, so they hold on any host timezone and would still hold if the
fallback path were exercised.

Runs against **every** copy of `fetch_cancellations.py` in `plugins/` (canonical
in `investigate-cancellation-feedback`, copy in `harvest-cancellation-feedback`
— Architecture principle 1), so a copy that diverges behaviourally fails here
even when the byte-diff has been reconciled.
"""
from __future__ import annotations

import pathlib
import unittest
from datetime import datetime, timezone

from reader_harness import load_reader, script_paths

CANCEL_PATHS = script_paths("fetch_cancellations.py")

# `fetch_cancellations` imports `slack_read` from its own scripts/ dir. That caches
# under a name identical across copies, so it is purged per load — otherwise copy 2
# binds copy 1's helper and drift in the sibling goes unseen.
SIBLINGS = ("slack_read", "fetch_cancellations")


def utc(y, mo, d, h, mi) -> float:
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp()


def k8bot_message(ts: str, purchased: str) -> dict:
    """A minimal `*New Cancellation Feedback*` post carrying one `Purchased` value."""
    return {
        "ts": ts,
        "text": (
            "*New Cancellation Feedback*\n"
            "*Reason:* too_expensive\n"
            "*Feedback:* ```too much```\n"
            "_User: abcdefghijklmnopqrstuvwxyz12 | Subscription: sub_1 | "
            f"Type: ai_monthly | Product: aurie | Purchased: {purchased} | Provider: stripe_"
        ),
    }


class PathsTests(unittest.TestCase):
    def test_both_copies_are_present(self):
        """A rename that drops a copy from the glob must fail, not silently narrow."""
        names = {p.parent.parent.name for p in CANCEL_PATHS}
        self.assertEqual(
            names,
            {"investigate-cancellation-feedback", "harvest-cancellation-feedback"},
            f"unexpected copy set: {[str(p) for p in CANCEL_PATHS]}",
        )


def _case(path: pathlib.Path):
    class _Case(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            cls.mod = load_reader(path, siblings=SIBLINGS)

        # ---- the clock, at the parse layer -------------------------------------

        def test_purchased_is_read_as_pacific_not_utc(self):
            """11:24 Pacific on a PDT date is 18:24 UTC, not 11:24 UTC."""
            self.assertEqual(
                self.mod.parse_purchased("Aug 10, 2026, 11:24 AM"),
                utc(2026, 8, 10, 18, 24),
            )

        def test_pst_date_uses_the_winter_offset(self):
            """The same wall clock in January is 19:24 UTC — a zone, not a fixed -7."""
            self.assertEqual(
                self.mod.parse_purchased("Jan 10, 2026, 11:24 AM"),
                utc(2026, 1, 10, 19, 24),
            )

        def test_date_only_form_is_localized_too(self):
            """Midnight Pacific is 07:00 UTC on a PDT date — the date-only form is a wall clock too."""
            self.assertEqual(
                self.mod.parse_purchased("Jun 4, 2026"), utc(2026, 6, 4, 7, 0)
            )

        def test_unparseable_purchased_stays_none(self):
            self.assertIsNone(self.mod.parse_purchased("whenever"))
            self.assertIsNone(self.mod.parse_purchased(""))

        # ---- DST boundary ------------------------------------------------------

        def test_offset_flips_across_the_spring_forward(self):
            """2026-03-08 02:00 local: 01:00 is still PST (-8), 03:00 is PDT (-7)."""
            self.assertEqual(
                self.mod.parse_purchased("Mar 8, 2026, 1:00 AM"), utc(2026, 3, 8, 9, 0)
            )
            self.assertEqual(
                self.mod.parse_purchased("Mar 8, 2026, 3:00 AM"), utc(2026, 3, 8, 10, 0)
            )

        def test_offset_flips_across_the_fall_back(self):
            """2026-11-01 02:00 local: 00:30 is PDT (-7), 03:00 is PST (-8).

            The 01:00-02:00 hour occurs twice and is deliberately not asserted —
            `zoneinfo` resolves it to the first (DST) pass via `fold=0`, and a
            renderer cannot tell us which one it meant.
            """
            self.assertEqual(
                self.mod.parse_purchased("Nov 1, 2026, 12:30 AM"),
                utc(2026, 11, 1, 7, 30),
            )
            self.assertEqual(
                self.mod.parse_purchased("Nov 1, 2026, 3:00 AM"), utc(2026, 11, 1, 11, 0)
            )

        def test_a_cancel_spanning_the_dst_change_keeps_true_elapsed_days(self):
            """Purchased 11:00 Pacific Oct 25, cancelled 11:00 Pacific Nov 8.

            Fourteen days on the wall clock, 14 days *and an hour* of elapsed
            time — the fall-back adds one. So the cancel instant here is 19:00
            UTC (PST) against a purchase at 18:00 UTC (PDT), and anything that
            subtracts wall clocks reports a flat 14.0.
            """
            msg = k8bot_message(str(utc(2026, 11, 8, 19, 0)), "Oct 25, 2026, 11:00 AM")
            self.assertEqual(self.mod.parse_entry(msg, "C0AENS8D48N")["days_to_cancel"], 14.04)

        # ---- days_to_cancel, at the entry layer --------------------------------

        def test_same_minute_cancel_reads_as_zero_days(self):
            """The regression case: ~30 s after purchase must not read as 0.29 d.

            `ts` is the real message ts from the 2026-08-10 entry
            (18:25:16.759 UTC) against its rendered `Purchased` of 11:24 AM
            Pacific (18:24 UTC, minute-truncated by the renderer).
            """
            entry = self.mod.parse_entry(
                k8bot_message("1786386316.759339", "Aug 10, 2026, 11:24 AM"),
                "C0AENS8D48N",
            )
            self.assertEqual(entry["days_to_cancel"], 0.0)
            self.assertLess(
                float(entry["ts"]) - self.mod.parse_purchased(entry["purchased"]),
                120,
                "purchase→cancel gap must stay inside two minutes",
            )
            # Same-day (<1) is the histogram bucket the SKILL.md reports on.
            self.assertLess(entry["days_to_cancel"], 1)

        def test_multi_day_cancel_is_exact(self):
            """A genuine 12-day cancel: right answer, and no offset folded in.

            12.29 rather than 12.0 is what the bug looked like here — invisible
            in the `7-30d` bucket, which is why it survived.
            """
            msg = k8bot_message(str(utc(2026, 6, 16, 15, 7)), "Jun 4, 2026, 8:07 AM")
            self.assertEqual(self.mod.parse_entry(msg, "C0AENS8D48N")["days_to_cancel"], 12.0)

        def test_purchased_utc_makes_the_clock_auditable(self):
            """`purchased` stays verbatim; `purchased_utc` states the resolved instant."""
            entry = self.mod.parse_entry(
                k8bot_message("1786386316.759339", "Aug 10, 2026, 11:24 AM"),
                "C0AENS8D48N",
            )
            self.assertEqual(entry["purchased"], "Aug 10, 2026, 11:24 AM")
            self.assertEqual(entry["purchased_utc"], "2026-08-10 18:24 UTC")
            self.assertEqual(entry["when"], "2026-08-10 18:25 UTC")

        def test_missing_purchased_leaves_both_fields_empty(self):
            """No metadata line → no derived gap, and no bogus epoch-0 timestamp."""
            msg = {
                "ts": "1786386316.759339",
                "text": "*New Cancellation Feedback*\n*Reason:* too_expensive",
            }
            entry = self.mod.parse_entry(msg, "C0AENS8D48N")
            self.assertIsNone(entry["days_to_cancel"])
            self.assertEqual(entry["purchased_utc"], "")

        # ---- the tzdata-less fallback -----------------------------------------

        def test_fallback_rule_agrees_with_zoneinfo(self):
            """`_pacific_offset` is only reached without tzdata, so test it directly.

            Asserted against `zoneinfo` on this host: a fallback that silently
            disagreed would produce a one-hour error only on the machines that
            need it.
            """
            from zoneinfo import ZoneInfo

            la = ZoneInfo(self.mod.PURCHASED_TZ_NAME)
            for naive in (
                datetime(2026, 1, 15, 11, 24),   # PST
                datetime(2026, 3, 8, 1, 0),      # hours before spring-forward
                datetime(2026, 3, 8, 3, 0),      # hours after
                datetime(2026, 7, 4, 12, 0),     # PDT
                datetime(2026, 11, 1, 0, 30),    # before fall-back
                datetime(2026, 11, 1, 3, 0),     # after
                datetime(2026, 12, 31, 23, 59),  # PST, year edge
            ):
                with self.subTest(naive=naive):
                    self.assertEqual(
                        self.mod._pacific_offset(naive),
                        naive.replace(tzinfo=la).utcoffset(),
                    )

        def test_dst_transition_dates_are_the_us_rule(self):
            """Second Sunday in March, first Sunday in November."""
            self.assertEqual(self.mod._nth_sunday(2026, 3, 2), 8)
            self.assertEqual(self.mod._nth_sunday(2026, 11, 1), 1)
            self.assertEqual(self.mod._nth_sunday(2027, 3, 2), 14)
            self.assertEqual(self.mod._nth_sunday(2027, 11, 1), 7)

    _Case.__name__ = f"DaysToCancel_{path.parent.parent.name.replace('-', '_')}"
    _Case.__qualname__ = _Case.__name__
    return _Case


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(PathsTests))
    for path in CANCEL_PATHS:
        suite.addTests(loader.loadTestsFromTestCase(_case(path)))
    return suite


if __name__ == "__main__":
    unittest.main()
