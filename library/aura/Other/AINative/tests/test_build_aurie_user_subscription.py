"""eng:build-aurie-user-subscription: refund rows, provider/country and the current-plan view.

The build is SQL run in BigQuery, so these tests pin the SQL the script emits: refunds become
negative `refunded` rows that are never deduped against the periods, every row carries provider
and country_code, and the view picks each user's latest charge and zeroes a refunded plan.
"""

import importlib.util
import pathlib
import re
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/build-aurie-user-subscription/scripts"


def _load():
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("aurie_subscription_build", SCRIPTS / "subscription.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(SCRIPTS))


S = _load()
TABLE = "aura-main.aurie_cost.aurie_user_subscription"
VIEW = "aura-main.aurie_cost.aurie_user_current_plan"


def _flat(sql):
    return re.sub(r"\s+", " ", sql)


def _top_level(select_list):
    """Split a SELECT list on commas outside parentheses."""
    items, depth, cur = [], 0, ""
    for ch in select_list:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            items.append(cur)
            cur = ""
        else:
            cur += ch
    return items + [cur]


class RefundRows(unittest.TestCase):
    def setUp(self):
        self.sql = _flat(S.build_sql(TABLE))

    def test_refunds_are_read_from_the_aurie_refund_table(self):
        self.assertIn("ai_subscription_refunded", self.sql)
        self.assertNotIn(".subscription_refunded`", self.sql)  # never the unprefixed Aura table

    def test_a_refund_is_a_negative_refunded_row_at_the_refund_time(self):
        refunds = self.sql[self.sql.index("refunds AS ("):self.sql.index("all_rows AS (")]
        self.assertIn("'refunded' AS event_type", refunds)
        self.assertIn("-ABS(SAFE_CAST(revenue AS FLOAT64)) AS revenue", refunds)
        self.assertIn("SAFE_CAST(time AS TIMESTAMP) AS event_time", refunds)

    def test_a_refund_keeps_the_refunded_periods_window_and_falls_back_to_its_time(self):
        refunds = self.sql[self.sql.index("refunds AS ("):self.sql.index("all_rows AS (")]
        self.assertIn("COALESCE(SAFE_CAST(purchaseat AS TIMESTAMP), SAFE_CAST(time AS TIMESTAMP)) AS purchase_at",
                      refunds)
        self.assertIn("AS expiration_at", refunds)

    def test_refunds_are_added_after_the_period_dedup_not_through_it(self):
        before = self.sql[:self.sql.index("refunds AS (")]
        self.assertNotIn("ai_subscription_refunded", before)
        self.assertIn("SELECT * FROM dedup UNION ALL SELECT * EXCEPT(dates_inferred) FROM refunds", self.sql)

    def test_every_row_carries_provider_and_country(self):
        self.assertEqual(self.sql.count("CAST(provider AS STRING)"), 3)
        self.assertEqual(self.sql.count("CAST(country_code AS STRING)"), 3)
        self.assertIn("provider, country_code, CURRENT_TIMESTAMP() AS fetched_at FROM all_rows", self.sql)
        names = [c for c, _ in S.SCHEMA]
        self.assertIn("provider", names)
        self.assertIn("country_code", names)

    def test_the_summary_separates_gross_refunds_and_net(self):
        cols = _flat(S._SUMMARY_COLS)
        self.assertIn("COUNTIF(event_type = 'refunded') AS refunds", cols)
        self.assertIn("SUM(IF(event_type = 'refunded', revenue, 0)), 2) AS refunded_revenue", cols)
        self.assertIn("COUNTIF(event_type != 'refunded') AS periods", cols)
        self.assertIn("ROUND(SUM(revenue), 2) AS total_revenue", cols)


class CurrentPlanView(unittest.TestCase):
    def setUp(self):
        self.sql = _flat(S.view_sql(TABLE, VIEW))

    def test_the_view_reads_the_built_table(self):
        self.assertTrue(self.sql.startswith(f"CREATE OR REPLACE VIEW `{VIEW}` AS WITH"))
        self.assertIn(f"FROM `{TABLE}` WHERE event_type = 'paid'", self.sql)

    def test_the_current_subscription_is_the_latest_paid_row(self):
        self.assertIn("PARTITION BY user_id ORDER BY event_time DESC, purchase_at DESC", self.sql)
        self.assertIn("WHERE rn = 1", self.sql)

    def test_revenue_adds_later_renewals_of_the_same_product_only(self):
        self.assertIn("r.event_type = 'renewed' AND r.product_id = p.product_id AND r.event_time > p.event_time",
                      self.sql)
        self.assertIn("p.revenue + IFNULL(rn.renewed_revenue, 0)", self.sql)

    def test_a_refund_after_the_payment_zeroes_revenue_and_flags_the_plan(self):
        self.assertIn("r.event_type = 'refunded' AND r.event_time > p.event_time AND r.product_id = p.product_id",
                      self.sql)
        self.assertIn("IF(ra.user_id IS NOT NULL, 0, p.revenue", self.sql)
        self.assertIn("ra.user_id IS NOT NULL AS current_plan_refunded", self.sql)

    def test_monthly_price_is_the_paid_rows_price_normalized(self):
        self.assertIn("CASE LOWER(p.period) WHEN 'year' THEN p.revenue / 12 WHEN 'month' THEN p.revenue END "
                      "AS monthly_price", self.sql)

    def test_the_view_has_exactly_the_specified_columns(self):
        select = self.sql[self.sql.rindex("SELECT p.user_id"):self.sql.rindex(" FROM latest_paid p")]
        names = [re.split(r" AS |\.", item.strip())[-1] for item in _top_level(select[len("SELECT "):])]
        self.assertEqual(names, ["user_id", "product_id", "package_id", "period", "purchase_at",
                                 "expiration_at", "provider", "country_code", "revenue",
                                 "first_payment", "renewal_count", "is_active", "monthly_price",
                                 "current_plan_refunded"])
        self.assertIn("p.fetched_at AS is_active", self.sql)

    def test_expiry_and_is_active_come_from_the_newest_row_in_the_chain(self):
        self.assertIn("MAX(r.expiration_at) AS last_expiration", self.sql)
        self.assertIn("GREATEST(p.expiration_at, IFNULL(rn.last_expiration, p.expiration_at)) AS expiration_at",
                      self.sql)
        self.assertIn("GREATEST(p.expiration_at, IFNULL(rn.last_expiration, p.expiration_at)) > p.fetched_at "
                      "AS is_active", self.sql)

    def test_unchained_renewals_are_counted_not_dropped_silently(self):
        self.assertIn("SUM(renewal_count) AS renewals", _flat(S._PLAN_COLS))
        self.assertIn("unchained_renewals", (SCRIPTS / "subscription.py").read_text())

    def test_active_plans_leave_out_refunded_plans_that_have_not_expired(self):
        cols = _flat(S._PLAN_COLS)
        self.assertIn("COUNTIF(is_active AND NOT current_plan_refunded) AS active_plans", cols)
        self.assertIn("COUNTIF(is_active AND current_plan_refunded) AS refunded_unexpired_plans", cols)
        self.assertIn("SUM(IF(is_active AND NOT current_plan_refunded, monthly_price, 0))", cols)

    def test_the_dry_run_summarizes_the_same_select(self):
        dry = _flat(S.dry_plan_sql())
        self.assertIn("src AS (SELECT *, CURRENT_TIMESTAMP() AS fetched_at FROM all_rows)", dry)
        self.assertIn("IF(ra.user_id IS NOT NULL, 0, p.revenue", dry)
        self.assertIn("COUNTIF(current_plan_refunded) AS refunded_plans", dry)


class Readers(unittest.TestCase):
    """Readers that count periods must not count a refund as a purchase."""

    def test_revenue_bookings_count_excludes_refund_rows(self):
        src = (REPO / "plugins/eng/skills/chart-aurie-revenue/scripts/revenue.py").read_text()
        self.assertIn("COUNTIF(event_type != 'refunded') AS periods", src)

    def test_tier_bookings_count_excludes_refund_rows(self):
        src = (REPO / "plugins/eng/skills/chart-aurie-revenue-by-tier/scripts/revenue_by_tier.py").read_text()
        self.assertIn("AND event_type != 'refunded') AS bookings_periods", src)


if __name__ == "__main__":
    unittest.main()
