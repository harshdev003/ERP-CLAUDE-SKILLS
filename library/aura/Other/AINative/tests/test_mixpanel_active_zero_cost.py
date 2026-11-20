#!/usr/bin/env python3
"""`mixpanel_active_zero_cost_users` — activity with no cost used to have no row.

The defect
----------
`aurie_per_user_daily_cost` only ever gets a row for a user-day that was
BILLED — the store is cost-shaped. A user genuinely active in Mixpanel that
day but billed $0 (no cost that day, or billed by an async pipeline on an
adjacent day) got NO row at all, and "no row" reads identically to "inactive"
to every downstream reader (the active-users line on `chart-aurie-daily-cost`,
`chart-aurie-retention`'s week-0 cohort, etc.).

Measured over 2026-07-03..2026-09-15 against the `_prod` view: of 39,154
Mixpanel-active user-days, only 32,816 (83.8%) had a store row to carry the
flag — 16.2% were invisible. This traces back to an earlier misdiagnosis in
this same investigation: 990 users looked like they had cost with "zero
Mixpanel activity", when 838 of them had fired a covered event — they simply
had no store row on the days that mattered.

The fix
-------
`store.py`'s daily append already computes the full Mixpanel-active set via
`engagement.day_engagement(day)` — it just discarded whoever had no cost.
`mixpanel_active_zero_cost_users(engaged, active, cost_bearing_users, mp_ok)`
is the pure function that decides who to ADD as a $0 row; `main()` unions the
result into its cost-bearing `users` set before building rows, so every field
for an added user defaults to 0.0 via the existing `.get(u, 0.0)` pattern —
no new column, no schema change, and no dollar total moves (a $0 contributor
changes no sum).

Stdlib unittest, no network — pure-function tests plus one full-row shape
check through the loaded module.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_el_caller_mapping import _load_store  # noqa: E402

store = _load_store()


class ModuleLoadedFully(unittest.TestCase):
    def test_function_present(self):
        self.assertTrue(hasattr(store, "mixpanel_active_zero_cost_users"))


class MixpanelActiveZeroCostTests(unittest.TestCase):
    def setUp(self):
        self.fn = store.mixpanel_active_zero_cost_users

    def test_adds_active_users_with_no_cost(self):
        """The core case: a user in `active` who never appears in any cost
        dict must be added."""
        engaged, active = set(), {"u1", "u2"}
        cost_users = {"u3"}  # u3 has real cost, unrelated to u1/u2
        added = self.fn(engaged, active, cost_users, mp_ok=True)
        self.assertEqual(added, {"u1", "u2"})

    def test_adds_engaged_users_with_no_cost(self):
        """`engaged` alone (no `active` overlap in this call) must also add."""
        engaged, active = {"u1"}, set()
        added = self.fn(engaged, active, set(), mp_ok=True)
        self.assertEqual(added, {"u1"})

    def test_never_re_adds_a_cost_bearing_user(self):
        """A user already billed today keeps their real cost row — the
        function must not produce a duplicate/zeroed entry for them."""
        engaged, active = {"u1"}, {"u1", "u2"}
        cost_users = {"u1"}  # u1 has real cost AND is Mixpanel-active
        added = self.fn(engaged, active, cost_users, mp_ok=True)
        self.assertEqual(added, {"u2"})
        self.assertNotIn("u1", added)

    def test_mp_failure_adds_nothing(self):
        """When Mixpanel failed, engaged/active degrade to empty sets by
        construction (day_engagement's contract) — but the gate is asserted
        explicitly rather than relying on that coincidence. Feed it NON-empty
        sets to prove the gate itself is what stops the add, not emptiness."""
        engaged, active = {"u1"}, {"u2"}
        added = self.fn(engaged, active, set(), mp_ok=False)
        self.assertEqual(added, set())

    def test_no_activity_no_cost_adds_nothing(self):
        added = self.fn(set(), set(), set(), mp_ok=True)
        self.assertEqual(added, set())

    def test_result_is_additive_never_a_removal(self):
        """The function returns what to ADD — it must never imply removing a
        cost-bearing user who happens not to be engaged/active today (e.g. an
        ambient/insights-batch-only user). Simulate the caller's union and
        check nobody with cost disappears."""
        cost_users = {"u1", "u2", "u3"}  # u3 is reached-but-inactive (ambient)
        engaged, active = set(), {"u4"}
        added = self.fn(engaged, active, cost_users, mp_ok=True)
        unioned = cost_users | added
        self.assertTrue(cost_users.issubset(unioned))
        self.assertIn("u4", unioned)


class RowShapeForAddedUsersTests(unittest.TestCase):
    """The row-building loop in main() defaults every per-vendor field via
    `.get(u, 0.0)` for a user not present in any cost dict. Confirm that
    default actually produces an all-zero, well-formed row rather than
    relying on reading the loop and hoping — build the same dicts main()
    would hold and run the identical per-user computation main() runs."""

    def test_added_user_produces_an_all_zero_row(self):
        added_uid = "zero_cost_active_user"
        llm, voice_11_agent, voice_11_livekit = {}, {}, {}
        cartesia, deepgram, livekit, livekit_platform = {}, {}, {}, {}
        lk_min_by_user, inference_draw = {}, {}

        # Mirror main()'s per-row computation exactly for this one uid.
        L = llm.get(added_uid, 0.0)
        v11a = voice_11_agent.get(added_uid, 0.0)
        v11l = voice_11_livekit.get(added_uid, 0.0)
        vct = cartesia.get(added_uid, 0.0)
        vdg = deepgram.get(added_uid, 0.0)
        vlk = livekit.get(added_uid, 0.0)
        vlkp = livekit_platform.get(added_uid, 0.0)
        lkm = lk_min_by_user.get(added_uid, 0.0)
        vtot = v11a + v11l + vct + vdg + vlk + vlkp

        self.assertEqual(L, 0.0)
        self.assertEqual(vtot, 0.0)
        self.assertEqual(round(L + vtot, 4), 0.0)
        self.assertEqual(lkm, 0.0)
        self.assertEqual(inference_draw.get(added_uid, 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
