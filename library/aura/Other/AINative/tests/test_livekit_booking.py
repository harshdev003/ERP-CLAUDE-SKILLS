"""Booking LiveKit as prepaid CREDITS (PROD-5998).

The subscription buys a block of minutes, so a used minute costs what the block paid for
it -- Scale is $500 / 50,000 = $0.01/min -- and the capacity nobody used is booked
separately rather than charged to whoever happened to talk that day.

This supersedes a daily-accrual model that spread $500 across each day's users by minute
share. That reconciled to the invoice but answered the per-minute question badly: a quiet
day looked expensive per user, and $/min moved with the reporting window (September read
$0.0681/min month-to-date against $0.0318 projected -- one month, two answers). A credit
rate is stable by construction.

Every assertion here covers a failure that produces a plausible number instead of an
error. The defect being removed was exactly that shape: `voice_livekit` booked
`minutes x $0.01` for three months, which is a well-formed column, internally
consistent, wrong by ~4x against the invoice, and wrong in the direction nobody checks
(too LOW — the $500 subscription was booked nowhere).

The suite deliberately does not touch BigQuery: `rebook` is pure so the dry run and the
write path cannot diverge, and that is what makes it testable.
"""

import importlib.util
import pathlib
import re
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/store-aurie-user-daily-cost/scripts"


def _load(name):
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(SCRIPTS))


class TestCreditRate(unittest.TestCase):
    def setUp(self):
        self.plan = _load("livekit_plan")
        self.store = _load("store")

    def test_the_credit_rate_is_the_block_price(self):
        self.assertAlmostEqual(self.plan.credit_rate_per_min("scale"), 500.0 / 50_000, places=9)

    def test_the_credit_rate_equals_the_overage_rate(self):
        # The identity that makes the two regimes agree: past the allotment,
        # minutes x credit_rate IS the invoice, so there is no seam to get wrong.
        for plan in ("ship", "scale"):
            with self.subTest(plan=plan):
                self.assertAlmostEqual(
                    self.plan.credit_rate_per_min(plan),
                    self.plan.overage_per_unit("agent_session_minutes", plan), places=9)

    def test_a_zero_dollar_plan_buys_no_capacity(self):
        # Build has no subscription, so there is no block price to divide out. None
        # rather than 0.0, which would read as "a minute is free".
        self.assertIsNone(self.plan.credit_rate_per_min("build"))

    def test_the_rate_does_not_move_with_volume(self):
        # The whole point. The accrual model gave a different $/min for every usage
        # level; this one is flat, so two months are comparable.
        rates = [self.store.livekit_consumed_day("scale", m) / m
                 for m in (100, 7_344, 15_737, 50_000, 90_000)]
        self.assertEqual(len(set(round(r, 9) for r in rates)), 1)

    def test_past_the_allotment_the_consumed_figure_is_the_invoice(self):
        # 60,000 min: invoice = $500 subscription + 10,000 over x $0.01 = $600.
        self.assertAlmostEqual(self.store.livekit_consumed_day("scale", 60_000), 600.0, places=6)


class TestUnusedCapacity(unittest.TestCase):
    def setUp(self):
        self.plan = _load("livekit_plan")

    def _month(self, month, days, per_day):
        mtd, total = 0.0, 0.0
        for d in range(1, days + 1):
            total += self.plan.unused_capacity_usd("scale", f"{month}-{d:02d}", mtd, per_day)
            mtd += per_day
        return total, mtd

    def test_consumed_plus_unused_is_the_subscription(self):
        # The reconciliation the model rests on.
        unused, minutes = self._month("2026-08", 31, 11_990 / 31)
        self.assertAlmostEqual(unused + minutes * 0.01, 500.0, places=2)

    def test_a_month_that_crosses_into_overage_reverses_its_earlier_unused(self):
        # Booked as a month-to-date DELTA, so the day that crosses drives unused-to-date
        # to zero and carries a NEGATIVE that undoes what the light early days booked.
        # A per-day max(0, accrual - consumed) cannot do that: it leaves them permanently
        # over-booked and the month silently stops reconciling.
        unused, minutes = self._month("2026-09", 30, 90_000 / 30)
        self.assertAlmostEqual(unused, 0.0, places=6)
        self.assertAlmostEqual(minutes * 0.01, 900.0, places=2)

    def test_a_silent_month_books_the_whole_subscription_as_unused(self):
        unused, minutes = self._month("2026-09", 30, 0.0)
        self.assertEqual(minutes, 0.0)
        self.assertAlmostEqual(unused, 500.0, places=6)


class TestDailyAccrual(unittest.TestCase):
    def setUp(self):
        self.store = _load("store")

    def test_a_month_of_accruals_sums_to_the_subscription(self):
        # The reconciliation the whole change rests on. A per-day figure that does not
        # sum to the invoice is the same class of error being fixed, just quieter.
        for month, days in (("2026-08", 31), ("2026-09", 30), ("2026-02", 28)):
            with self.subTest(month=month):
                total = sum(self.store.livekit_platform_day_total(f"{month}-{d:02d}", "scale")[0]
                            for d in range(1, days + 1))
                self.assertAlmostEqual(total, 500.0, places=6)

    def test_february_accrues_more_per_day_than_august(self):
        # Spread across the CALENDAR month, not a fixed 1/30 — otherwise a short month
        # under-books and a long one over-books, and neither errors.
        feb = self.store.livekit_platform_day_total("2026-02-10", "scale")[0]
        aug = self.store.livekit_platform_day_total("2026-08-10", "scale")[0]
        self.assertGreater(feb, aug)


class TestMarginalOverage(unittest.TestCase):
    def setUp(self):
        self.store = _load("store")

    def test_inside_the_allotment_is_free(self):
        # September 2026: 7,344 of 50,000 minutes. The previous model charged $73.44
        # for exactly this case.
        self.assertEqual(self.store.livekit_overage_day("scale", 0, 7_344), 0.0)
        self.assertEqual(self.store.livekit_overage_day("scale", 40_000, 800), 0.0)

    def test_only_the_minutes_past_the_allotment_are_charged(self):
        # The day that straddles the boundary must bill the 300 minutes beyond it, not
        # all 800 and not none.
        self.assertAlmostEqual(self.store.livekit_overage_day("scale", 49_500, 800), 3.00, places=6)

    def test_a_month_of_daily_overages_sums_to_the_month_total(self):
        # Marginal, not cumulative. A cumulative form would re-charge the whole
        # month-to-date excess every day — growing quadratically, with each individual
        # day still looking like a reasonable number.
        allot, rate = 50_000, 0.01
        daily = [3_000.0] * 20  # 60,000 min, 10,000 over
        mtd, total = 0.0, 0.0
        for m in daily:
            total += self.store.livekit_overage_day("scale", mtd, m)
            mtd += m
        self.assertAlmostEqual(total, (sum(daily) - allot) * rate, places=6)

    def test_rate_override_applies_to_overage_only(self):
        self.assertEqual(self.store.livekit_overage_day("scale", 0, 7_344, rate=0.05), 0.0)
        self.assertAlmostEqual(
            self.store.livekit_overage_day("scale", 49_500, 800, rate=0.02), 6.00, places=6)


class TestSchema(unittest.TestCase):
    def test_csv_and_bigquery_schemas_agree(self):
        # They are declared in two files; a mismatch surfaces as a malformed load, or
        # worse, silently shifted columns.
        store, bq_sink = _load("store"), _load("bq_sink")
        self.assertEqual([n for n, _ in bq_sink.STORES["per_user"]["schema"]], store.COLS)

    def test_new_columns_sit_next_to_voice_livekit(self):
        store = _load("store")
        i = store.COLS.index("voice_livekit")
        self.assertEqual(store.COLS[i + 1], "voice_livekit_platform")
        self.assertEqual(store.COLS[i + 2], "livekit_minutes")

    def test_the_previous_schema_is_still_recognised(self):
        # An unmigratable store is a hard stop for the daily cadence.
        #
        # Pins the DIFFERENCE, not a column count: a count has to be edited by hand every
        # time any later card adds a column, and the edit is indistinguishable from
        # loosening the check. The property is that PRE_PLATFORM_COLS is exactly today's
        # schema minus the columns that postdate it, in order.
        store = _load("store")
        added_since = {"voice_livekit_platform", "livekit_minutes",
                       "deepgram_inference_drawdown", "voice_fish", "voice_inworld"}
        self.assertEqual(store.PRE_PLATFORM_COLS,
                         [c for c in store.COLS if c not in added_since])
        self.assertTrue(added_since.isdisjoint(store.PRE_PLATFORM_COLS))


class TestCallerContract(unittest.TestCase):
    """`livekit_costs` has callers OUTSIDE this module, and changing its shape broke them
    silently at review time — a TypeError in the daily unit-cost ETL and in two report
    fallbacks, none of which any unit test reached. This is the drift blind spot CLAUDE.md
    describes: the change was to a *function*, and nothing grepped for its callers."""

    def setUp(self):
        self.store = _load("store")

    def test_livekit_costs_still_returns_the_five_tuple(self):
        import inspect
        sig = inspect.signature(self.store.livekit_costs)
        # `rate` must stay the 2nd POSITIONAL parameter: two callers pass it positionally.
        self.assertEqual(list(sig.parameters)[:2], ["day", "rate"])
        # They must have DEFAULTS (the value may legitimately be None) — the three
        # external callers pass neither.
        for name in ("plan", "minutes_before"):
            self.assertIsNot(sig.parameters[name].default, inspect.Parameter.empty)

    def test_every_external_caller_matches_the_signature(self):
        # Binds each real call site's arity against the live signature, so a future
        # reshaping fails here instead of at 03:00 in the cadence.
        import inspect
        sig = inspect.signature(self.store.livekit_costs)
        callers = {
            "eng/skills/store-aurie-user-daily-cost/scripts/unit_costs.py": 2,
            "eng/skills/report-aurie-daily-costs/scripts/report.py": 2,
            "eng/skills/report-aurie-weekly-costs/scripts/report.py": 2,
        }
        for rel, argc in callers.items():
            src = (REPO / "plugins" / rel).read_text()
            with self.subTest(caller=rel):
                self.assertIn("livekit_costs(", src, "caller no longer calls it — update this test")
                sig.bind(*["x"] * argc)  # raises TypeError if the shape moved

    def test_the_minutes_primitive_is_separate(self):
        # store.py needs minutes WITHOUT a dollar opinion, so it can book the subscription
        # and the overage into two different columns.
        import inspect
        self.assertEqual(list(inspect.signature(self.store.livekit_minutes_for_day).parameters), ["day"])

    def test_livekit_costs_bills_the_invoice_model(self):
        # The back-compat shape must carry the NEW model, or the three callers keep
        # reporting minutes x overage-rate under a function that looks updated.
        plat, _ = self.store.livekit_platform_day_total("2026-09-14", "scale")
        self.assertAlmostEqual(plat, 500.0 / 30, places=6)
        self.assertEqual(self.store.livekit_overage_day("scale", 0.0, 7_344), 0.0)


class TestOneScalePerWindow(unittest.TestCase):
    """A window mixing stored days with the live fallback must price LiveKit the SAME way.

    The failure is a comparison, not a crash: price stored days as `minutes x rate` while
    the live fallback returns subscription + overage, and today looks several times more
    expensive than a stored day with identical traffic. Every number stays well-formed.
    """

    def test_stored_days_are_priced_from_booked_dollars(self):
        src = (REPO / "plugins/eng/skills/report-aurie-daily-costs/scripts/report.py").read_text()
        # The stored branch reads what was booked...
        self.assertIn('stored_minutes[ds]["usd"]', src)
        # ...not minutes re-multiplied by the overage rate.
        self.assertNotIn('stored_minutes[ds] * rates["livekit"]', src)
        # and the booked figure is both LiveKit columns, not the overage-only one.
        self.assertIn("platform_usd", src)

    def test_both_livekit_columns_are_selected(self):
        src = (REPO / "plugins/eng/skills/report-aurie-daily-costs/scripts/report.py").read_text()
        for col in ("voice_livekit", "voice_livekit_platform", "livekit_minutes"):
            self.assertIn(col, src, f"{col} must be read for the stored-day path")


class TestQuietDayIsNotADataGap(unittest.TestCase):
    """A day with no voice traffic still owes the subscription — that is not a failed read.

    The guard's arithmetic was always right (you cannot divide dollars by zero minutes);
    its PREMISE changed. `$ > 0 with no minutes` implied a failed log read only while
    every LiveKit-path dollar was usage-derived. The subscription accrual is owed on a
    silent day, so the old test flags every quiet day as missing-logs and drops the
    accrual — and a warning that fires on ordinary days is one people stop reading.
    """

    def setUp(self):
        self.src = (REPO / "plugins/eng/skills/store-aurie-user-daily-cost/scripts/unit_costs.py").read_text()

    def test_the_read_failure_note_is_a_gap_signal(self):
        # lk_note was being discarded as `_lk_note`; it is the one unambiguous signal
        # that the forwarded-log query itself failed.
        self.assertIn("lk_data_gap = bool(lk_note) or", self.src)
        self.assertNotIn("_lk_note, _lk_meta", self.src)

    def test_only_usage_derived_dollars_imply_missing_minutes(self):
        # Cartesia/Deepgram bill from their own usage APIs, so a non-zero figure proves
        # there WAS traffic. The subscription proves nothing about traffic.
        self.assertIn("usage_dollars = day_cartesia + day_deepgram", self.src)
        self.assertIn("(usage_dollars > 0 and lk_min <= 0)", self.src)

    def test_exclusion_and_gap_are_separate_flags(self):
        # Excluding dollars from a blended rate with no denominator is arithmetic;
        # calling it a gap is a claim about the data. They must not be the same boolean.
        # ElevenLabs TTS $ joins the LiveKit-path $ here: its minutes are lk_min (PROD-7584).
        self.assertIn("lk_rate_cost = lk_cost + el_tts_usd", self.src)
        self.assertIn("lk_excluded_no_minutes = lk_rate_cost > 0 and lk_min <= 0", self.src)
        self.assertIn("if lk_excluded_no_minutes:", self.src)
        self.assertNotIn("if lk_data_gap:", self.src)

    def test_a_quiet_day_does_not_print_a_data_gap_warning(self):
        self.assertIn("still counted in voice_cost", self.src)
        self.assertIn("DATA GAP", self.src)

    def test_the_gap_warning_fires_on_a_failed_read_that_excluded_nothing(self):
        # livekit_costs returns $0 on LiveKitUnavailable, so a log failure with no
        # Cartesia/Deepgram charges excludes nothing. Gating the message on the excluded
        # AMOUNT meant a successful quiet day printed an explanation while a genuinely
        # unread day printed nothing — exactly backwards.
        # The gap branch is evaluated FIRST; the excluded-amount case is the fallback.
        self.assertLess(self.src.index("if lk_gap:"), self.src.index("elif lk_excluded:"),
                        "the failed-read branch must be reached before the amount branch")

    def test_reported_totals_always_carry_the_livekit_path(self):
        # voice_cost is a REPORTED TOTAL, not a rate denominator. A real dollar must not
        # vanish from it for want of a denominator — and days WITH minutes kept it, which
        # left one persisted column on two models.
        self.assertIn("vcost_total = vcost_11 + lk_cost", self.src)
        # With no LiveKit minutes the rate is ConvAI $ over ConvAI minutes (PROD-7584).
        self.assertIn("rate_num, rate_den = el_agent_usd, vmin_11", self.src)


class TestSignedUnusedSurvives(unittest.TestCase):
    """Unused capacity is signed, and three places assumed it was not."""

    def setUp(self):
        self.rb = _load("rebook_livekit")
        self.store_src = (REPO / "plugins/eng/skills/store-aurie-user-daily-cost/scripts/store.py").read_text()

    def test_the_other_row_is_written_when_its_total_is_negative(self):
        # A positive-only gate drops the crossing-into-overage reversal — and takes every
        # other leftover on that row with it, so the month stops reconciling. That is the
        # exact failure this column was added to prevent.
        #
        # Matched structurally rather than as a literal, so adding a term to the gate
        # (PROD-6008 added the drawdown remainder) does not require editing the
        # assertion -- an edit that would be indistinguishable from weakening it.
        gates = [l.strip() for l in self.store_src.split("\n")
                 if l.strip().startswith("if ") and "0.005" in l]
        self.assertEqual(len(gates), 1, f"expected one Other-row gate, got {gates}")
        terms = re.findall(r"(\w+\(?\w*\)?) > 0\.005", gates[0])
        self.assertTrue(terms, gates[0])
        for t in terms:
            self.assertTrue(t.startswith("abs("),
                            f"un-abs()'d term {t!r} in the Other-row gate: {gates[0]}")

    def test_rebook_creates_an_other_row_when_a_day_lacks_one(self):
        # Without this the unused is dropped silently while the printed summary still
        # uses the computed figure: a dry run shows delta 0 and the write does not
        # reconcile. A dry run that promises something the write won't do is worse than
        # the miss.
        rows = [{"date": "2026-06-05", "user_id": "u1", "llm_cost": 1.0,
                 "voice_livekit": 5.00, "voice_cost": 5.00, "total_cost": 6.00}]
        by_date, _ = self.rb.rebook(rows, "scale", 0.01)
        other = [r for r in by_date["2026-06-05"] if r["user_id"] == "Other"]
        self.assertEqual(len(other), 1, "an Other row must be created to carry unused")
        self.assertGreater(other[0]["voice_livekit_platform"], 0.0)

    def test_the_created_row_carries_the_full_days_unused(self):
        # 500 min consumed = $5.00; the day's accrual is $500/30, so the rest is unused.
        rows = [{"date": "2026-06-05", "user_id": "u1", "llm_cost": 0.0,
                 "voice_livekit": 5.00, "voice_cost": 5.00, "total_cost": 5.00}]
        by_date, _ = self.rb.rebook(rows, "scale", 0.01)
        booked = sum(r["voice_livekit"] + r["voice_livekit_platform"]
                     for r in by_date["2026-06-05"])
        self.assertAlmostEqual(booked, 500.0 / 30, places=4)

    def test_a_created_other_row_carries_every_schema_column(self):
        # A partition replace serializes by schema, so an omitted column lands as NULL —
        # and any SUM(voice_x) without COALESCE then drops the unused-capacity figure
        # this row exists to carry. The row must mirror store.py's own Other constructor.
        store = _load("store")
        rows = [{"date": "2026-06-05", "user_id": "u1", "llm_cost": 1.0,
                 "voice_livekit": 5.00, "voice_cost": 5.00, "total_cost": 6.00}]
        by_date, _ = self.rb.rebook(rows, "scale", 0.01)
        other = next(r for r in by_date["2026-06-05"] if r["user_id"] == "Other")
        for col in store.COLS:
            with self.subTest(column=col):
                self.assertIn(col, other, f"synthesized Other row is missing {col}")
                self.assertIsNotNone(other[col])

    def test_a_zero_dollar_plan_does_not_crash_the_run_log(self):
        # `build` is a valid --livekit-plan and has no subscription, so the credit rate
        # is None; formatting it with :.4f raised TypeError before the day was written.
        store = _load("store")
        self.assertIn("n/a", store._fmt_rate(None))
        self.assertIn("0.0100", store._fmt_rate(0.01))


class TestRebookWritePath(unittest.TestCase):
    """The write branch had never executed, so nothing caught that its call was wrong.

    `rebook_livekit` called `load_partition(TABLE, day, rows, project=...)` against a
    signature of `(table_fqn, csv_bytes, schema, date, project)` — `day` bound to
    `csv_bytes`, a list of dicts to `schema`, and `date` unfilled. A TypeError on the
    first write, shipped and merged, because the script is dry-run by default and only
    the dry run was ever run. Exactly the branch-real-data-cannot-reach case CLAUDE.md
    names: stub the chokepoint and make it fail on demand.
    """

    def setUp(self):
        sys.path.insert(0, str(REPO / "plugins/eng/skills/query-bigquery/scripts"))
        self.addCleanup(sys.path.remove, str(REPO / "plugins/eng/skills/query-bigquery/scripts"))
        self.rb = _load("rebook_livekit")

    def test_the_write_call_matches_load_partitions_signature(self):
        import inspect
        from bq import load_partition
        sig = inspect.signature(load_partition)
        src = (SCRIPTS / "rebook_livekit.py").read_text()
        self.assertIn("load_partition(TABLE, _csv_bytes(by_date[day]), schema, day", src)
        # Bind the real shape: 4 positionals + project. Raises if the signature moves.
        sig.bind("t", b"", [("a", "STRING")], "2026-06-05", project="p")

    def test_csv_is_serialized_in_schema_order(self):
        # The CSV is parsed POSITIONALLY against the schema the load job supplies, so its
        # column order is load-bearing even though BigQuery then maps that schema to the
        # destination by name.
        store = _load("store")
        row = {c: f"v{i}" for i, c in enumerate(store.COLS)}
        line = self.rb._csv_bytes([row]).decode().strip()
        self.assertEqual(line.split(","), [f"v{i}" for i in range(len(store.COLS))])

    def test_a_missing_field_becomes_empty_not_shifted(self):
        # A row lacking a column must emit an empty cell, never drop it — dropping one
        # shifts every later value into the wrong column, which BigQuery would accept.
        store = _load("store")
        line = self.rb._csv_bytes([{"date": "2026-06-05", "user_id": "u1"}]).decode().strip()
        self.assertEqual(len(line.split(",")), len(store.COLS))


class TestRebook(unittest.TestCase):
    """The backfill. Pure by construction, so this is the same code the write path runs."""

    def setUp(self):
        self.rb = _load("rebook_livekit")

    def _day(self, date, per_user, other_lk=0.0):
        rows = [{"date": date, "user_id": u, "llm_cost": 1.0, "voice_livekit": lk,
                 "voice_cost": lk + 2.0, "total_cost": lk + 3.0}
                for u, lk in per_user.items()]
        rows.append({"date": date, "user_id": "Other", "llm_cost": 0.0,
                     "voice_livekit": other_lk, "voice_cost": other_lk, "total_cost": other_lk})
        return rows

    def test_minutes_are_recovered_exactly(self):
        # voice_livekit was minutes x rate by construction, so dividing inverts it.
        rows = self._day("2026-08-01", {"u1": 5.00, "u2": 1.50})
        by_date, _ = self.rb.rebook(rows, "scale", 0.01)
        got = {r["user_id"]: r["livekit_minutes"] for r in by_date["2026-08-01"]}
        self.assertAlmostEqual(got["u1"], 500.0, places=4)
        self.assertAlmostEqual(got["u2"], 150.0, places=4)

    def test_users_are_billed_for_the_minutes_they_used(self):
        # 750 and 250 minutes -> $7.50 and $2.50 at the $0.01 credit rate. Each user pays
        # for their own consumption, not a share of a fee the business owes anyway.
        rows = self._day("2026-08-01", {"u1": 7.50, "u2": 2.50})
        by_date, _ = self.rb.rebook(rows, "scale", 0.01)
        got = {r["user_id"]: r["voice_livekit"] for r in by_date["2026-08-01"]}
        self.assertAlmostEqual(got["u1"], 7.50, places=4)
        self.assertAlmostEqual(got["u2"], 2.50, places=4)

    def test_unused_capacity_is_never_allocated_to_a_user(self):
        # It belongs to nobody. Splitting it by minute share would bill the users who
        # showed up for the ones who did not — and it is the per-user figure that feeds
        # margin, payback and lifetime-cost.
        rows = self._day("2026-08-01", {"u1": 7.50, "u2": 2.50})
        by_date, _ = self.rb.rebook(rows, "scale", 0.01)
        for r in by_date["2026-08-01"]:
            if r["user_id"] != "Other":
                self.assertEqual(r["voice_livekit_platform"], 0.0)
        other = next(r for r in by_date["2026-08-01"] if r["user_id"] == "Other")
        self.assertAlmostEqual(other["voice_livekit_platform"], 500.0 / 31 - 10.0, places=3)

    def test_a_day_with_no_minutes_books_the_fee_to_the_other_row(self):
        # The fee is owed whether or not anyone talked. Dropping it on a quiet day
        # would under-book the month by one day and reconcile to nothing.
        rows = self._day("2026-08-02", {})
        by_date, _ = self.rb.rebook(rows, "scale", 0.01)
        other = next(r for r in by_date["2026-08-02"] if r["user_id"] == "Other")
        self.assertAlmostEqual(other["voice_livekit_platform"], 500.0 / 31, places=4)

    def test_a_quiet_month_still_reconciles_to_the_subscription(self):
        # 14 days x 500 min = 7,000 min = $70 consumed; the rest of those days' capacity
        # is unused, and the two must add to the 14 days' share of the $500.
        rows = []
        for d in range(1, 15):
            rows += self._day(f"2026-06-{d:02d}", {"u1": 5.00})  # 500 min/day
        by_date, _ = self.rb.rebook(rows, "scale", 0.01)
        consumed = sum(r["voice_livekit"] for day in by_date.values() for r in day)
        unused = sum(r["voice_livekit_platform"] for day in by_date.values() for r in day)
        self.assertAlmostEqual(consumed, 70.0, places=2)
        self.assertAlmostEqual(consumed + unused, 500.0 * 14 / 30, places=2)

    def test_a_month_past_the_allotment_books_no_net_unused(self):
        # 25 days x 3,000 min = 75,000, well past the 50,000 allotment. Consumed is the
        # invoice ($750 = $500 + 25,000 over x $0.01), and the unused booked on the early
        # light days must be fully reversed — month-to-date tracking is what does that.
        # June deliberately: August and September carry INVOICED_EXTRA_OVERAGE entries.
        rows = []
        for d in range(1, 26):
            rows += self._day(f"2026-06-{d:02d}", {"u1": 30.00})
        by_date, _ = self.rb.rebook(rows, "scale", 0.01)
        consumed = sum(r["voice_livekit"] for day in by_date.values() for r in day)
        unused = sum(r["voice_livekit_platform"] for day in by_date.values() for r in day)
        self.assertAlmostEqual(consumed, 750.0, places=2)
        self.assertAlmostEqual(unused, 0.0, places=6)

    def test_voice_cost_swaps_the_old_livekit_figure_for_the_new_ones(self):
        # voice_cost is the sum of the vendor columns; leaving the old LiveKit amount in
        # it would double-count, and the row would still add up internally.
        rows = self._day("2026-08-01", {"u1": 5.00})
        by_date, _ = self.rb.rebook(rows, "scale", 0.01)
        r = next(x for x in by_date["2026-08-01"] if x["user_id"] == "u1")
        # started at voice_cost 7.00 = 5.00 livekit + 2.00 other vendors
        self.assertAlmostEqual(
            r["voice_cost"], 2.00 + r["voice_livekit"] + r["voice_livekit_platform"], places=4)
        self.assertAlmostEqual(r["total_cost"], 1.0 + r["voice_cost"], places=4)

    def test_august_restates_from_119_to_the_invoice(self):
        # The headline: a full month of stored days lands on the ACTUAL invoice —
        # $500.00 subscription + $1.14 of Krisp overage — rather than the $119.90 the
        # rate card produced. Reconciling to the invoice, not merely to the
        # subscription, is the whole claim of this change.
        rows = []
        for d in range(1, 32):
            rows += self._day(f"2026-08-{d:02d}", {"u1": 119.90 / 31})
        by_date, report = self.rb.rebook(rows, "scale", 0.01)
        booked = sum(r["voice_livekit"] + r["voice_livekit_platform"]
                     for day in by_date.values() for r in day)
        self.assertAlmostEqual(booked, 501.14, places=2)
        self.assertAlmostEqual(sum(x["stored_livekit"] for x in report), 119.90, places=2)

    def test_unmeasurable_overage_lands_on_the_other_row_only(self):
        # Krisp is not derivable from agent-session minutes, so splitting it by minute
        # share would assert a per-user allocation of something we cannot measure per
        # user. It belongs on the reconciling row — present in the total, not invented
        # against a UID.
        rows = []
        for d in range(1, 32):
            rows += self._day(f"2026-08-{d:02d}", {"u1": 3.00})
        by_date, _ = self.rb.rebook(rows, "scale", 0.01)
        other = sum(r["voice_livekit"] for day in by_date.values()
                    for r in day if r["user_id"] == "Other")
        # The Other row carries ONLY the unmeasurable overage — no consumed credits,
        # which are all attributable to the users who spent them.
        self.assertAlmostEqual(other, 1.14, places=2)
        users = sum(r["voice_livekit"] for day in by_date.values()
                    for r in day if r["user_id"] != "Other")
        self.assertAlmostEqual(users, 31 * 3.00, places=2)


class TestExpectedInvoice(unittest.TestCase):
    """The summary's invoice column must include the agent-minute overage (ENG-1605)."""

    @classmethod
    def setUpClass(cls):
        cls.rb = _load("rebook_livekit")
        cls.plan = cls.rb.livekit_plan

    def _with_reading(self, month, reading, extra=None):
        readings = dict(self.plan.DASHBOARD_READINGS)
        extras = dict(self.rb.INVOICED_EXTRA_OVERAGE)
        self.addCleanup(setattr, self.plan, "DASHBOARD_READINGS", self.plan.DASHBOARD_READINGS)
        self.addCleanup(setattr, self.rb, "INVOICED_EXTRA_OVERAGE", self.rb.INVOICED_EXTRA_OVERAGE)
        if reading is not None:
            readings[month] = reading
        else:
            readings.pop(month, None)
        if extra is not None:
            extras[month] = extra
        self.plan.DASHBOARD_READINGS = readings
        self.rb.INVOICED_EXTRA_OVERAGE = extras

    def test_agent_overage_is_part_of_the_invoice(self):
        # September 2026 as invoiced: $500 + 11,838 min over at $0.01 + $113.20 extras.
        self._with_reading("2099-01", {"partial": False, "agent_session_minutes": 61838}, 113.20)
        amount, complete = self.rb.expected_invoice("2099-01", "scale")
        self.assertTrue(complete)
        self.assertAlmostEqual(amount, 500 + 118.38 + 113.20, places=2)

    def test_no_reading_is_flagged_not_passed_off_as_whole(self):
        self._with_reading("2099-02", None, 1.14)
        amount, complete = self.rb.expected_invoice("2099-02", "scale")
        self.assertFalse(complete)
        self.assertAlmostEqual(amount, 501.14, places=2)

    def test_partial_reading_does_not_count_its_minutes(self):
        self._with_reading("2099-03", {"partial": True, "agent_session_minutes": 90000})
        amount, complete = self.rb.expected_invoice("2099-03", "scale")
        self.assertFalse(complete)
        self.assertAlmostEqual(amount, 500.0, places=2)

    def test_summary_marks_a_month_missing_the_agent_overage(self):
        import contextlib, io
        self._with_reading("2099-02", None)
        report = [{"date": f"2099-02-{d:02d}", "month": "2099-02", "minutes": 100.0,
                   "stored_livekit": 1.0, "new_overage": 1.0, "new_platform": 0.0,
                   "new_extra": 0.0} for d in range(1, 29)]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.rb.summarise(report, "scale")
        self.assertIn("500.00*", buf.getvalue())
        self.assertIn("leaves out any agent-minute overage", buf.getvalue())


if __name__ == "__main__":
    unittest.main()


class TestCurrentModelMonth(unittest.TestCase):
    """ENG-1860: a month store.py wrote itself is booked from its closed statement.

    Such a day already carries `livekit_minutes`, minutes x the credit rate plus the pool
    accrual in `voice_livekit`, and signed unused capacity on `Other`. The old pass would
    recover minutes from that `voice_livekit` (~39% too many on 2026-10-15 -> 10-31) and
    add the invoiced pools on top of the accrual.
    """

    MEASURED = 272_172.0  # October's log minutes; 263,719 were billed

    @classmethod
    def setUpClass(cls):
        cls.rb = _load("rebook_livekit")

    def october(self):
        rows, per_day = [], self.MEASURED / 31
        for d in range(1, 32):
            day = f"2026-10-{d:02d}"
            accrual = 60.0 if d >= 15 else 0.0
            for uid, share, dg in (("u1", 0.75, 24.0), ("u2", 0.25, 8.0)):
                m = per_day * share
                lk = m * 0.01 + accrual * share
                rows.append({"date": day, "user_id": uid, "llm_cost": 1.0, "livekit_minutes": m,
                             "voice_livekit": lk, "voice_livekit_platform": 0.0,
                             "voice_deepgram": dg, "voice_cost": lk + dg + 2.0,
                             "total_cost": lk + dg + 3.0})
            dg_other, plat = 1002.79 / 31 - 32.0, (-3.0 if d == 6 else 0.0)
            rows.append({"date": day, "user_id": "Other", "llm_cost": 0.0, "livekit_minutes": 0.0,
                         "voice_livekit": 5.0, "voice_livekit_platform": plat,
                         "voice_deepgram": dg_other, "voice_cost": 5.0 + plat + dg_other + 2.0,
                         "total_cost": 5.0 + plat + dg_other + 2.0})
        return rows

    @staticmethod
    def total(by_date, col):
        return sum(r[col] for day in by_date.values() for r in day)

    def test_october_reconciles_to_the_statement_to_the_cent(self):
        by_date, report = self.rb.rebook(self.october(), "scale", 0.01)
        lk = self.total(by_date, "voice_livekit") + self.total(by_date, "voice_livekit_platform")
        dg = self.total(by_date, "voice_deepgram")
        self.assertAlmostEqual(lk, 500 + 2137.18 + 1716.80, places=2)
        self.assertAlmostEqual(dg, 911.91, places=2)
        self.assertAlmostEqual(lk + dg, 5265.89, places=2)

    def test_minutes_are_read_not_recovered_and_the_split_is_kept(self):
        by_date, _ = self.rb.rebook(self.october(), "scale", 0.01)
        mins = sum(r["livekit_minutes"] for day in by_date.values() for r in day)
        self.assertAlmostEqual(mins, self.MEASURED, places=0)
        for day in by_date.values():
            u = {r["user_id"]: r for r in day}
            self.assertAlmostEqual(u["u1"]["voice_livekit"] / u["u2"]["voice_livekit"], 3.0, places=3)
            self.assertAlmostEqual(u["u1"]["voice_deepgram"] / u["u2"]["voice_deepgram"], 3.0, places=3)
            self.assertEqual(u["u1"]["voice_livekit_platform"], 0.0)

    def test_the_pools_replace_the_accrual_and_a_second_run_changes_nothing(self):
        by_date, _ = self.rb.rebook(self.october(), "scale", 0.01)
        other_lk = sum(r["voice_livekit"] for day in by_date.values() for r in day if r["user_id"] == "Other")
        self.assertAlmostEqual(other_lk, 1716.80, places=1)
        again, _ = self.rb.rebook([r for day in by_date.values() for r in day], "scale", 0.01)
        for col in ("voice_livekit", "voice_livekit_platform", "voice_deepgram", "voice_cost"):
            self.assertAlmostEqual(self.total(again, col), self.total(by_date, col), places=2, msg=col)

    def test_voice_cost_and_total_cost_follow_the_new_columns(self):
        by_date, _ = self.rb.rebook(self.october(), "scale", 0.01)
        for r in by_date["2026-10-06"] + by_date["2026-10-20"]:
            # Every row carries $2.00 of other vendors, which must survive untouched.
            self.assertAlmostEqual(r["voice_cost"], 2.0 + r["voice_livekit"]
                                   + r["voice_livekit_platform"] + r["voice_deepgram"], places=3)
            self.assertAlmostEqual(r["total_cost"], r["llm_cost"] + r["voice_cost"], places=4)

    def test_a_partial_month_or_one_without_a_statement_is_skipped_untouched(self):
        rows = [r for r in self.october() if r["date"] != "2026-10-31"]
        by_date, report = self.rb.rebook(rows, "scale", 0.01)
        self.assertEqual(dict(by_date), {})
        self.assertIn("30 of 31 days", report[0]["skipped"])
        nov = [{**r, "date": r["date"].replace("2026-10", "2026-11")} for r in self.october()
               if r["date"] != "2026-10-31"]
        _, report = self.rb.rebook(nov, "scale", 0.01)
        self.assertIn("no closed statement", report[0]["skipped"])

    def test_a_month_an_earlier_run_restated_does_not_block_october(self):
        # July-September carry livekit_minutes from the PROD-5998 restatement. The default
        # --since 2026-07-01 must still book October, and leave July untouched.
        july = [{**r, "date": r["date"].replace("2026-10", "2026-07")} for r in self.october()]
        by_date, report = self.rb.rebook(july + self.october(), "scale", 0.01)
        self.assertFalse(any(d.startswith("2026-07") for d in by_date))
        self.assertEqual([r["month"] for r in report if r.get("skipped")], ["2026-07"])
        lk = self.total(by_date, "voice_livekit") + self.total(by_date, "voice_livekit_platform")
        self.assertAlmostEqual(lk + self.total(by_date, "voice_deepgram"), 5265.89, places=2)
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.rb.summarise(report, "scale")
        self.assertIn("SKIPPED 2026-07 (left untouched, nothing written)", buf.getvalue())

    def test_the_dry_run_prints_each_line_against_the_statement(self):
        import contextlib, io
        _, report = self.rb.rebook(self.october(), "scale", 0.01)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.rb.summarise(report, "scale")
        out = buf.getvalue()
        self.assertIn("2026-10 booked from its closed statement", out)
        self.assertRegex(out, r"total\s+5265\.89  invoice    5265\.89  delta    0\.00")
