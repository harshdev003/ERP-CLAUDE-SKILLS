"""Tests for the anthropic_usage harvest path.

Failure injection is the point here. Every defect worth catching lives in a
branch real data cannot reach: an expired cookie, a reordered upstream column, a
day with no activity. All three produce *zero rows*, and only one of them should
reach BigQuery — so the tests assert on which of them writes.

Stdlib unittest, no network, no keys.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/harvest-anthropic-usage/scripts"


def _load(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_au_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


ca = _load("claude_admin")


HEADER = ("user_email,account_uuid,product,model,total_requests,total_prompt_tokens,"
          "total_completion_tokens,total_net_spend_usd,total_gross_spend_usd,user_id,"
          "total_uncached_input_tokens,total_cache_read_tokens,total_cache_write_5m_tokens,"
          "total_cache_write_1h_tokens,total_web_search_count,slack_channel_id,teams_channel_id")
ROW = ("a@x.io,uuid-1,Claude Code,claude-opus-5,100,1000,50,1.25,1.25,user_01,"
       "10,900,40,50,0,,")


class FakeTransport:
    """Stands in for the HTTP layer at its single chokepoint."""

    def __init__(self, status=200, body=""):
        self.status, self.body, self.calls = status, body, []

    def __call__(self, url, params):
        self.calls.append((url, params))
        return self.status, self.body


def _client(transport) -> ca.ClaudeAdmin:
    c = ca.ClaudeAdmin(session_key="fake", org_id="org-1", max_retries=1)
    c._once = transport
    return c


class TestAuthFailureNeverLooksLikeNoUsage(unittest.TestCase):
    """The defect this whole module exists to prevent.

    An expired cookie returns 403 and a quiet day returns an empty CSV. If the
    403 degraded to "no rows", the harvest would replace a real partition with
    nothing and the result would read downstream exactly like a day nobody
    worked. So 403/401 must raise, and must raise a type that cannot be caught
    as a generic request error.
    """

    def test_403_raises_auth_error(self):
        c = _client(FakeTransport(403, "forbidden"))
        with self.assertRaises(ca.ClaudeAdminAuthError):
            c.spend_report("2026-09-16", "2026-09-16")

    def test_401_raises_auth_error(self):
        c = _client(FakeTransport(401, "unauthorized"))
        with self.assertRaises(ca.ClaudeAdminAuthError):
            c.spend_report("2026-09-16", "2026-09-16")

    def test_auth_error_is_distinguishable_from_a_transport_error(self):
        # A caller that wants to skip a broken day but stop on a bad cookie has
        # to be able to tell them apart. Catching ClaudeAdminError must not
        # swallow the auth case silently -- it is a subclass, so the ordering of
        # except clauses is what matters, and this asserts the subclass link
        # exists at all.
        self.assertTrue(issubclass(ca.ClaudeAdminAuthError, ca.ClaudeAdminError))
        self.assertIsNot(ca.ClaudeAdminAuthError, ca.ClaudeAdminError)

    def test_missing_key_raises_auth_error_not_a_silent_empty(self):
        saved = {k: os.environ.pop(k, None) for k in ca._SESSION_ENV}
        try:
            with self.assertRaises(ca.ClaudeAdminAuthError):
                ca.resolve_session_key(None)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


class TestEmptyDayIsRealButBrokenReadIsNot(unittest.TestCase):
    def test_header_only_csv_is_an_empty_day(self):
        self.assertEqual(ca.parse_spend_csv(HEADER), [])

    def test_totally_empty_body_is_an_empty_day(self):
        self.assertEqual(ca.parse_spend_csv(""), [])

    def test_missing_required_column_raises_rather_than_loading_zeros(self):
        # Drop total_prompt_tokens. Parsed leniently this would yield rows whose
        # token counts are all 0 -- a well-formed, plausible, entirely wrong day.
        broken = HEADER.replace(",total_prompt_tokens", "") + "\n" + \
            ROW.replace(",1000", "", 1)
        with self.assertRaises(ca.ClaudeAdminError) as cm:
            ca.parse_spend_csv(broken)
        self.assertIn("total_prompt_tokens", str(cm.exception))


class TestParsingIsByNameNotPosition(unittest.TestCase):
    def test_reordered_columns_still_parse_correctly(self):
        # Swap the first two columns. Positional parsing would put the uuid in
        # user_email and never error.
        hdr = HEADER.split(",")
        row = ROW.split(",")
        hdr[0], hdr[1] = hdr[1], hdr[0]
        row[0], row[1] = row[1], row[0]
        out = ca.parse_spend_csv(",".join(hdr) + "\n" + ",".join(row))
        self.assertEqual(out[0]["user_email"], "a@x.io")
        self.assertEqual(out[0]["account_uuid"], "uuid-1")

    def test_an_added_upstream_column_does_not_shift_values(self):
        hdr = HEADER + ",brand_new_column"
        row = ROW + ",surprise"
        out = ca.parse_spend_csv(hdr + "\n" + row)
        self.assertEqual(out[0]["user_email"], "a@x.io")
        self.assertEqual(out[0]["prompt_tokens"], 1000)
        self.assertEqual(out[0]["completion_tokens"], 50)


class TestRowShape(unittest.TestCase):
    def test_token_fields_are_ints_and_money_stays_string(self):
        r = ca.parse_spend_csv(HEADER + "\n" + ROW)[0]
        self.assertIsInstance(r["prompt_tokens"], int)
        self.assertEqual(r["cache_read_tokens"], 900)
        # NUMERIC is loaded from CSV text; keeping it a string avoids a float
        # round-trip on money.
        self.assertIsInstance(r["net_spend_usd"], str)
        self.assertEqual(r["net_spend_usd"], "1.25")

    def test_blank_money_becomes_zero_not_empty(self):
        row = ROW.replace(",1.25,1.25,", ",,,")
        r = ca.parse_spend_csv(HEADER + "\n" + row)[0]
        self.assertEqual(r["net_spend_usd"], "0")
        self.assertEqual(r["gross_spend_usd"], "0")

    def test_org_service_rows_keep_a_blank_email(self):
        # Claude Tag emits rows with no user. Coalescing at write time would
        # invent a user; the blank is the honest value and the report layer
        # labels it.
        r = ca.parse_spend_csv(HEADER + "\n" + ROW.replace("a@x.io", "", 1))[0]
        self.assertEqual(r["user_email"], "")

    def test_prompt_tokens_is_a_total_of_the_detail_columns(self):
        # Documented invariant: prompt = uncached + cache_read + cw5m + cw1h.
        # Summing all five would double-count input.
        r = ca.parse_spend_csv(HEADER + "\n" + ROW)[0]
        self.assertEqual(
            r["prompt_tokens"],
            r["uncached_input_tokens"] + r["cache_read_tokens"]
            + r["cache_write_5m_tokens"] + r["cache_write_1h_tokens"],
        )


MEMBERS = ('"Name","Email","Role","Status","Seat Tier"\n'
           '"Steve","steve@x.io","Primary owner","Active","Premium"\n'
           '"Rose","rose@x.io","Admin","Active","Standard"\n')


class TestMembersParsing(unittest.TestCase):
    def test_seat_tier_is_lowercased_for_joining(self):
        rows = ca.parse_members_csv(MEMBERS)
        self.assertEqual([r["seat_tier"] for r in rows], ["premium", "standard"])

    def test_rows_without_an_email_are_skipped(self):
        rows = ca.parse_members_csv(MEMBERS + '"Ghost","","User","Pending","Standard"\n')
        self.assertEqual(len(rows), 2)

    def test_missing_seat_tier_column_raises(self):
        broken = MEMBERS.replace(',"Seat Tier"', "")
        with self.assertRaises(ca.ClaudeAdminError):
            ca.parse_members_csv(broken)

    def test_blank_tier_becomes_unknown_not_empty(self):
        rows = ca.parse_members_csv(
            '"Name","Email","Role","Status","Seat Tier"\n"X","x@x.io","User","Active",""\n')
        self.assertEqual(rows[0]["seat_tier"], "unknown")


class TestCheckAuthRefusesAnEmptyOrg(unittest.TestCase):
    def test_zero_members_is_treated_as_a_bad_session_not_an_empty_org(self):
        # A live org always has members. Zero rows here means the response was
        # not what we think it was, and proceeding would harvest against a
        # session we have not actually validated.
        c = _client(FakeTransport(200, '"Name","Email","Role","Status","Seat Tier"\n'))
        with self.assertRaises(ca.ClaudeAdminError):
            c.check_auth()

    def test_a_real_members_response_passes(self):
        c = _client(FakeTransport(200, MEMBERS))
        self.assertEqual(c.check_auth()["members"], 2)


class TestHarvestWiring(unittest.TestCase):
    """The consumer must read only what the producer actually emits.

    `harvest.py` writes CSV in `USAGE_ORDER`; every one of those keys other than
    the two it stamps itself has to come out of `parse_spend_csv`. A key the
    parser cannot emit would silently serialise as empty for every row.
    """

    def test_every_usage_column_is_produced_by_the_parser_or_stamped(self):
        harvest = _load("harvest")
        produced = set(ca.parse_spend_csv(HEADER + "\n" + ROW)[0].keys())
        stamped = {"usage_date", "harvested_at"}
        for col in harvest.USAGE_ORDER:
            with self.subTest(col=col):
                self.assertTrue(
                    col in produced or col in stamped,
                    f"{col} is in USAGE_ORDER but nothing produces it",
                )

    def test_usage_order_matches_the_declared_schema_exactly(self):
        harvest = _load("harvest")
        self.assertEqual(harvest.USAGE_ORDER, [c for c, _ in harvest.USAGE_SCHEMA])

    def test_seat_order_matches_the_declared_schema_exactly(self):
        harvest = _load("harvest")
        self.assertEqual(harvest.SEAT_ORDER, [c for c, _ in harvest.SEAT_SCHEMA])

    def test_seat_columns_are_produced_by_the_members_parser_or_stamped(self):
        harvest = _load("harvest")
        produced = set(ca.parse_members_csv(MEMBERS)[0].keys())
        stamped = {"as_of_date", "harvested_at"}
        for col in harvest.SEAT_ORDER:
            with self.subTest(col=col):
                self.assertTrue(col in produced or col in stamped)

    def test_csv_serialisation_round_trips_column_count(self):
        harvest = _load("harvest")
        rows = ca.parse_spend_csv(HEADER + "\n" + ROW)
        rows[0]["usage_date"] = "2026-09-16"
        rows[0]["harvested_at"] = "2026-09-17 00:00:00"
        line = harvest._rows_to_csv(rows, harvest.USAGE_ORDER).decode().strip()
        self.assertEqual(len(line.split(",")), len(harvest.USAGE_SCHEMA))


class TestPricingTable(unittest.TestCase):
    def test_every_model_row_has_all_five_rates(self):
        sp = _load("seed_pricing")
        for row in sp.build_rows():
            with self.subTest(model=row["model"]):
                for k in ("input_usd_per_mtok", "output_usd_per_mtok",
                          "cache_read_usd_per_mtok", "cache_write_5m_usd_per_mtok",
                          "cache_write_1h_usd_per_mtok"):
                    self.assertGreater(float(row[k]), 0, f"{k} must be a real rate")

    def test_cache_read_is_cheaper_than_uncached_input(self):
        # Cache reads are ~98% of our token volume. If this inverted, every
        # valuation would be wrong by roughly an order of magnitude -- and it
        # would still be a plausible-looking number.
        sp = _load("seed_pricing")
        for row in sp.build_rows():
            with self.subTest(model=row["model"]):
                self.assertLess(float(row["cache_read_usd_per_mtok"]),
                                float(row["input_usd_per_mtok"]))

    def test_cache_writes_cost_more_than_uncached_input(self):
        sp = _load("seed_pricing")
        for row in sp.build_rows():
            with self.subTest(model=row["model"]):
                inp = float(row["input_usd_per_mtok"])
                self.assertGreater(float(row["cache_write_5m_usd_per_mtok"]), inp)
                self.assertGreater(float(row["cache_write_1h_usd_per_mtok"]), inp)

    def test_rates_carry_a_verification_date(self):
        # Nothing can refresh this table, so an undated one ages into fiction
        # without ever changing. The date is what makes staleness visible.
        import datetime as dt
        sp = _load("seed_pricing")
        dt.date.fromisoformat(sp.VERIFIED_ON)  # raises if malformed
        for row in sp.build_rows():
            self.assertEqual(row["verified_on"], sp.VERIFIED_ON)


if __name__ == "__main__":
    unittest.main()


class TestFreshnessCheckNeedsNoSessionKey(unittest.TestCase):
    """The staleness check must not depend on the thing most likely to be broken.

    The expected failure of this pipeline is the session cookie expiring and
    nobody noticing. A freshness check that itself needed the cookie would fail
    in exactly the situation it exists to detect, and would fail in a way
    indistinguishable from "the warehouse is fine". So `find_gaps` reads
    BigQuery alone.
    """

    def setUp(self):
        self.harvest = _load("harvest")

    def _stub_bq(self, present_dates):
        import datetime as dt
        calls = []

        def fake_query(sql, project=None, **kw):
            calls.append((sql, kw.get("params")))
            return [{"d": dt.date.fromisoformat(d)} for d in present_dates]

        self.harvest.bq.query = fake_query
        return calls

    def test_gaps_are_identified_without_any_credential(self):
        import datetime as dt
        saved = {k: os.environ.pop(k, None) for k in ca._SESSION_ENV}
        try:
            self._stub_bq(["2026-09-01", "2026-09-03"])
            g = self.harvest.find_gaps(dt.date(2026, 9, 1), dt.date(2026, 9, 4))
            self.assertEqual(g["missing"], ["2026-09-02", "2026-09-04"])
            self.assertEqual(g["latest"], "2026-09-03")
            self.assertEqual(g["want"], 4)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_an_entirely_empty_window_is_reported_not_treated_as_complete(self):
        import datetime as dt
        self._stub_bq([])
        g = self.harvest.find_gaps(dt.date(2026, 9, 1), dt.date(2026, 9, 3))
        self.assertIsNone(g["latest"])
        self.assertEqual(len(g["missing"]), 3)

    def test_the_query_is_date_cast_so_string_params_compare_correctly(self):
        # bq.py types every string param as STRING; comparing a DATE column to a
        # STRING without a cast errors at query time rather than returning wrong
        # rows -- but only if the cast is actually present.
        import datetime as dt
        calls = self._stub_bq(["2026-09-01"])
        self.harvest.find_gaps(dt.date(2026, 9, 1), dt.date(2026, 9, 2))
        sql, params = calls[0]
        self.assertIn("DATE(@s)", sql)
        self.assertIn("DATE(@e)", sql)
        self.assertEqual(set(params), {"s", "e"})


class TestDownloadClassification(unittest.TestCase):
    """The filename is the only source of a file's date.

    The spend CSV has no date column at all, so which partition a file belongs
    to is knowable only from its name. That makes classification load-bearing
    rather than cosmetic: a misread name writes real usage onto the wrong day,
    and nothing downstream could detect it.
    """

    def setUp(self):
        self.ld = _load("load_downloads")
        self.org = "ffe6fe72-fe43-499d-b9ec-fa7f2991e9b4"

    def test_single_day_export_is_loadable(self):
        c = self.ld.classify(f"/d/spend-report-{self.org}-2026-09-16-to-2026-09-16.csv")
        self.assertEqual(c["kind"], "spend_day")
        self.assertEqual(c["start"], "2026-09-16")

    def test_multi_day_export_is_never_treated_as_a_day(self):
        # A range export is one aggregate over its whole window. Loading it into
        # a day-partition would book 17 days of usage onto a single date -- a
        # large, plausible, undetectable error. It must classify as a range.
        c = self.ld.classify(f"/d/spend-report-{self.org}-2026-09-01-to-2026-09-17.csv")
        self.assertEqual(c["kind"], "spend_range")

    def test_members_export_carries_its_own_as_of_date(self):
        c = self.ld.classify(f"/d/members-{self.org}-2026-09-18.csv")
        self.assertEqual(c["kind"], "members")
        self.assertEqual(c["as_of"], "2026-09-18")

    def test_chrome_repeat_download_is_reported_not_loaded(self):
        # " (1)" files are byte-identical repeats. Loading one is harmless but
        # ignoring it silently is not -- the count printed must distinguish
        # "ignored" from "loaded".
        c = self.ld.classify(f"/d/spend-report-{self.org}-2026-09-16-to-2026-09-16 (1).csv")
        self.assertEqual(c["kind"], "duplicate")

    def test_an_unrelated_csv_is_not_mistaken_for_an_export(self):
        self.assertEqual(self.ld.classify("/d/budget-2026.csv")["kind"], "unknown")
        self.assertEqual(
            self.ld.classify("/d/claude-code-usage-2026-09-01-to-2026-09-30-2026.csv")["kind"],
            "unknown")

    def test_a_renamed_file_is_unloadable_rather_than_misdated(self):
        # Losing the date means losing the partition. Refusing is the only safe
        # option -- there is nothing in the file to fall back to.
        self.assertEqual(self.ld.classify("/d/september-usage.csv")["kind"], "unknown")

    def test_loader_reuses_the_harvesters_schema_not_its_own_copy(self):
        # Two column orders for one table would drift, and the drift would be a
        # silent column shift on load -- every value one position off, all types
        # still plausible. So the loader must import the harvester's rather than
        # restate it.
        harvest = _load("harvest")
        self.assertEqual(self.ld.USAGE_ORDER, harvest.USAGE_ORDER)
        self.assertEqual(self.ld.USAGE_SCHEMA, harvest.USAGE_SCHEMA)
        self.assertEqual(self.ld.SEAT_ORDER, harvest.SEAT_ORDER)
        # Equality alone would still pass against a hand-kept duplicate, so
        # assert the source really imports rather than redefines.
        src = (SCRIPTS / "load_downloads.py").read_text()
        self.assertIn("from harvest import", src)
        for name in ("USAGE_SCHEMA =", "SEAT_SCHEMA =", "USAGE_ORDER =", "SEAT_ORDER ="):
            self.assertNotIn(name, src, f"{name} is redefined in load_downloads.py")

    def test_loading_a_day_needs_no_session_key(self):
        # The whole point of this path: a human already authenticated in the
        # browser, so the loader must not reach for a credential.
        saved = {k: os.environ.pop(k, None) for k in ca._SESSION_ENV}
        try:
            import tempfile
            with tempfile.TemporaryDirectory() as d:
                p = os.path.join(
                    d, f"spend-report-{self.org}-2026-09-16-to-2026-09-16.csv")
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(HEADER + "\n" + ROW + "\n")
                out = self.ld.load_day(self.ld.classify(p), "2026-09-18 00:00:00",
                                       dry_run=True)
            self.assertEqual(out["date"], "2026-09-16")
            self.assertEqual(out["rows"], 1)
            self.assertEqual(out["tokens"], 1050)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


class TestCoverageComesFromTheLogNotTheFactTable(unittest.TestCase):
    """An idle day and an unharvested day are different claims.

    Both hold zero usage rows, so the fact table cannot tell them apart. Asking
    it which days it has reports every quiet weekend as a gap, and equally hides
    a real gap sitting next to a busy day. The two cases demand opposite
    readings — idle means the totals are right, unharvested means they are a
    lower bound — so coverage is asserted in `harvest_log`, which gets a row
    either way.
    """

    def setUp(self):
        self.harvest = _load("harvest")

    def test_find_gaps_reads_the_log_table_not_the_usage_table(self):
        seen = {}

        def fake_query(sql, project=None, **kw):
            seen["sql"] = sql
            return []

        self.harvest.bq.query = fake_query
        import datetime as dt
        self.harvest.find_gaps(dt.date(2026, 9, 1), dt.date(2026, 9, 2))
        self.assertIn("harvest_log", seen["sql"])
        self.assertNotIn("daily_user_usage", seen["sql"])

    def test_an_idle_day_is_logged_so_it_never_reads_as_a_gap(self):
        # The zero-row case is the whole reason the log exists: if a quiet day
        # wrote nothing anywhere, it would be indistinguishable from a miss.
        calls = []
        self.harvest.bq.load_partition = lambda *a, **k: calls.append(a) or 1
        self.harvest.record_harvest(
            "2026-09-12", {"rows": 0, "tokens": 0, "users": 0}, "download", "t")
        self.assertEqual(len(calls), 1)
        self.assertIn("harvest_log", calls[0][0])

    def test_log_row_column_count_matches_its_schema(self):
        rows = [{"harvest_date": "2026-09-12", "rows_loaded": 0, "tokens": 0,
                 "users": 0, "source": "download", "harvested_at": "t"}]
        line = self.harvest._rows_to_csv(rows, self.harvest.LOG_ORDER).decode().strip()
        self.assertEqual(len(line.split(",")), len(self.harvest.LOG_SCHEMA))

    def test_log_order_matches_its_schema_exactly(self):
        self.assertEqual(self.harvest.LOG_ORDER,
                         [c for c, _ in self.harvest.LOG_SCHEMA])


class TestPricingIsEffectiveDated(unittest.TestCase):
    """A rate change must not restate history, and must not double it.

    Both failures are silent. Taking the latest rate per model reprices every
    past month the day a new rate lands; joining on model alone fans out to one
    row per rate row, doubling every valuation. Neither errors.
    """

    def setUp(self):
        import importlib.util as ilu
        path = (REPO / "plugins/eng/skills/report-anthropic-usage/scripts/report.py")
        spec = ilu.spec_from_file_location("_au_report", path)
        self.report = ilu.module_from_spec(spec)
        sys.modules[spec.name] = self.report
        spec.loader.exec_module(self.report)

    def test_the_join_bounds_each_rate_by_a_validity_window(self):
        sql = self.report.VALUED
        self.assertIn("LEAD(effective_from)", sql)
        self.assertIn("usage_date >= p.effective_from", sql)
        self.assertIn("effective_to IS NULL OR u.usage_date < p.effective_to", sql)

    def test_the_join_does_not_pick_a_single_latest_rate(self):
        # ROW_NUMBER ... rn = 1 was the original bug: it selects the newest rate
        # regardless of the usage date.
        sql = self.report.VALUED
        self.assertNotIn("rn = 1", sql)
        self.assertNotIn("ROW_NUMBER", sql)

    def test_no_bare_model_join_that_would_fan_out(self):
        self.assertNotIn("USING (model)", self.report.VALUED)

    def test_no_correlated_subquery_bigquery_will_reject(self):
        # BigQuery refuses correlated subqueries referencing another table. A
        # version of this fix using one parsed fine and failed only at runtime.
        self.assertNotIn("LEFT JOIN UNNEST([(", self.report.VALUED)

    def test_every_mode_reports_coverage(self):
        # trend and user originally never checked, so a harvest gap was folded
        # into the bucket totals with nothing said.
        src = (REPO / "plugins/eng/skills/report-anthropic-usage/scripts/report.py").read_text()
        for mode in ("cmd_summary", "cmd_seats", "cmd_trend", "cmd_user"):
            body = src.split(f"def {mode}(")[1].split("\ndef ")[0]
            with self.subTest(mode=mode):
                self.assertIn("_coverage(", body, f"{mode} never checks coverage")


class TestHarvestLoopSurvivesAPerDayFailure(unittest.TestCase):
    def test_bigquery_errors_are_caught_per_day(self):
        # Uncaught, one bad load abandoned every remaining day and the members
        # snapshot, and surfaced as a traceback rather than a recoverable gap.
        src = (SCRIPTS / "harvest.py").read_text()
        loop = src.split("for d in days:")[1].split("if not a.skip_members")[0]
        self.assertIn("except bq.BQError", loop)
        handler = loop.split("except bq.BQError")[1]
        # The handler must continue to the next day, not return or re-raise.
        self.assertIn("continue", handler)
        self.assertLess(handler.index("continue"),
                        (handler.index("return") if "return" in handler else len(handler)),
                        "the BQError handler returns before it continues")


class TestFreshnessCheckFailsOnHolesNotJustLag(unittest.TestCase):
    """Two independent ways to be wrong; a check testing one is half a monitor.

    Lag catches "the harvest stopped running". Gaps catch "the harvest has
    holes" — a run that died mid-window, or days nobody backfilled. Exiting 0
    because the newest day happens to be present, while 23 of 30 days are
    missing, is the silent success this mode exists to prevent.
    """

    def setUp(self):
        self.harvest = _load("harvest")

    def _run_check(self, present, window=30, max_age=3):
        import argparse, datetime as dt, io, contextlib
        through = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
        self.harvest.bq.query = lambda sql, project=None, **kw: [
            {"d": dt.date.fromisoformat(d)} for d in present]
        a = argparse.Namespace(since=None, window=window, max_age_days=max_age)
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = self.harvest.cmd_check(a)
        return rc, buf.getvalue() + err.getvalue(), through

    def test_a_hole_fails_even_when_the_newest_day_is_fresh(self):
        import datetime as dt
        through = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
        # Only the last 3 days of a 30-day window: newest is current, 27 missing.
        present = [(through - dt.timedelta(days=i)).isoformat() for i in range(3)]
        rc, out, _ = self._run_check(present, window=30, max_age=3)
        self.assertEqual(rc, 1, "holes must fail the check")
        self.assertIn("GAPS", out)

    def test_a_fully_harvested_window_passes(self):
        import datetime as dt
        through = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
        present = [(through - dt.timedelta(days=i)).isoformat() for i in range(7)]
        rc, out, _ = self._run_check(present, window=7, max_age=3)
        self.assertEqual(rc, 0)
        self.assertNotIn("GAPS", out)
        self.assertNotIn("STALE", out)

    def test_lag_still_fails_on_its_own(self):
        import datetime as dt
        through = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
        # A contiguous run that stopped 10 days ago: no holes after the last
        # harvested day, but badly behind.
        present = [(through - dt.timedelta(days=i)).isoformat() for i in range(10, 17)]
        rc, out, _ = self._run_check(present, window=7, max_age=3)
        self.assertEqual(rc, 1)

    def test_an_empty_window_fails(self):
        rc, out, _ = self._run_check([], window=7)
        self.assertEqual(rc, 1)
        self.assertIn("STALE", out)

    def test_the_gap_message_names_a_runnable_range(self):
        import datetime as dt
        through = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
        present = [(through - dt.timedelta(days=i)).isoformat() for i in range(3)]
        rc, out, _ = self._run_check(present, window=10, max_age=3)
        self.assertIn("--since", out)
        self.assertIn("--through", out)


class TestSystemicBQFailureStopsTheRun(unittest.TestCase):
    """`BQUnavailable` is systemic; `BQError` is per-day. The order matters.

    A missing GCP_KEY or library fails every write identically, but auth to
    claude.ai still succeeds — so catching it per-day meant a backfill would
    politely fetch all 201 days and discard every one. It subclasses BQError, so
    the specific clause must come first or it is swallowed by the general one.
    """

    def setUp(self):
        self.src = (SCRIPTS / "harvest.py").read_text()
        self.loop = self.src.split("for d in days:")[1].split("if not a.skip_members")[0]

    def test_bq_unavailable_is_handled_separately(self):
        self.assertIn("except bq.BQUnavailable", self.loop)

    def test_the_unavailable_clause_precedes_the_general_one(self):
        # Python matches except clauses in order; subclass second means dead code.
        self.assertLess(self.loop.index("except bq.BQUnavailable"),
                        self.loop.index("except bq.BQError"),
                        "BQUnavailable must be caught before BQError, which it subclasses")

    def test_unavailable_aborts_rather_than_continuing(self):
        handler = self.loop.split("except bq.BQUnavailable")[1].split("except ")[0]
        self.assertIn("return", handler)
        self.assertNotIn("continue", handler)

    def test_the_hierarchy_this_relies_on_still_holds(self):
        # If bq.py ever stopped subclassing, the ordering rule above would be
        # pointless and this test should be the thing that says so.
        import importlib.util as ilu
        spec = ilu.spec_from_file_location(
            "_au_bq", REPO / "plugins/eng/skills/query-bigquery/scripts/bq.py")
        m = ilu.module_from_spec(spec); spec.loader.exec_module(m)
        self.assertTrue(issubclass(m.BQUnavailable, m.BQError))


class TestEmptyResultsStillStateCoverage(unittest.TestCase):
    """"No rows" is not an answer until you say whether the window was read.

    A user with no usage in a half-harvested window is not a user with no usage.
    Returning early on an empty result skipped the coverage note in exactly the
    case where it decides the meaning — the idle-versus-missing collapse the
    coverage work was added to prevent.
    """

    def setUp(self):
        self.src = (REPO / "plugins/eng/skills/report-anthropic-usage/scripts/report.py").read_text()

    def _empty_path(self, mode):
        body = self.src.split(f"def {mode}(")[1].split("\ndef ")[0]
        return body.split("if not rows:")[1].split("return 0")[0]

    def test_trend_states_coverage_before_returning_empty(self):
        self.assertIn("_coverage_note(cov)", self._empty_path("cmd_trend"))

    def test_user_states_coverage_before_returning_empty(self):
        self.assertIn("_coverage_note(cov)", self._empty_path("cmd_user"))

    def test_summary_states_coverage_before_returning_empty(self):
        # summary reports the raw have/want counts rather than the note.
        self.assertIn("cov[", self._empty_path("cmd_summary"))

    def test_every_mode_says_something_about_coverage_when_empty(self):
        for mode in ("cmd_summary", "cmd_trend", "cmd_user"):
            with self.subTest(mode=mode):
                path = self._empty_path(mode)
                self.assertTrue("cov" in path,
                                f"{mode} returns empty without stating coverage")
