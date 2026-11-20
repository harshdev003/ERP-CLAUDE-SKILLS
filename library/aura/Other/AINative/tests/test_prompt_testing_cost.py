#!/usr/bin/env python3
"""Derived per-key cost for `eng:report-prompt-testing-cost`.

Two defects this guards, both of which produce a *plausible* report rather than
an error — which is why they need tests rather than a spot-check against a live
window.

**1. The service tier is spelled differently by the two endpoints.**
`/usage/completions` returns `service_tier: "flex-tier"`; `/costs` prefixes the
line item `"flex | ..."`. The derivation looks a rate up by
`(model, tier, dimension)`, so if the two sides normalise differently the lookup
misses and every flex request bills as **$0.00**. Non-zero requests against a
$0.00 derived cost is the signature, and `TierNormalisationTests` /
`FlexTrafficIsPricedTests` fail on exactly that.

**2. Cached input must be priced separately from uncached input.**
Cached input is roughly 10x cheaper, and the measured cache-hit rate on these
keys swings from 30% to 96% between days. A blended $/token — total dollars
divided by total tokens — is therefore wrong by multiples in both directions,
not by a rounding margin. `CachedRateSplitTests` builds a corpus where the
blended and split answers differ by ~3x and pins the split one; a blended
implementation cannot pass it.

The transport is stubbed at its single chokepoint (`openai_admin.request`), so
pagination, key discovery, rate derivation, and costing all run for real.
"""
from __future__ import annotations

import pathlib
import unittest

from reader_harness import REPO_ROOT, load_reader

SKILL_DIR = REPO_ROOT / "plugins" / "eng" / "skills" / "report-prompt-testing-cost"
SCRIPTS = SKILL_DIR / "scripts"

# `report.py` does `from cost import ...` after putting its own dir on sys.path,
# so the sibling has to be evicted between loads or a later load binds an
# earlier copy's module.
SIBLINGS = ("cost", "openai_admin")


def _load(name: str):
    return load_reader(SCRIPTS / name, siblings=SIBLINGS)


# ---------------------------------------------------------------------------
# Fake transport
# ---------------------------------------------------------------------------

def _cost_bucket(day_ts: int, items: list[tuple[str, float]]) -> dict:
    return {
        "start_time": day_ts,
        "results": [
            {"line_item": line_item, "amount": {"value": usd}}
            for line_item, usd in items
        ],
    }


def _usage_result(model: str, tier: str, *, uncached=0, cached=0, cachewrite=0,
                  output=0, requests=0, key_id=None) -> dict:
    row = {
        "model": model,
        "service_tier": tier,
        "input_uncached_tokens": uncached,
        "input_cached_tokens": cached,
        "input_cache_write_tokens": cachewrite,
        "output_tokens": output,
        "num_model_requests": requests,
    }
    if key_id is not None:
        row["api_key_id"] = key_id
    return row


class FakeAPI:
    """Canned responses keyed by endpoint, with real pagination on projects."""

    def __init__(self, *, cost_buckets, org_usage, key_usage, api_keys,
                 projects=(("proj_1", "Conversation [Production]"),),
                 api_key_page_size=100):
        self.cost_buckets = cost_buckets
        self.org_usage = org_usage
        self.key_usage = key_usage          # project_id -> [buckets]
        self.api_keys = api_keys            # project_id -> [key rows]
        self.api_key_page_size = api_key_page_size
        self.projects = list(projects)
        self.calls: list[tuple[str, dict]] = []

    def install(self, module) -> "FakeAPI":
        module.request = self._request
        return self

    def _request(self, path, params, api_key, *, project_id=None):
        self.calls.append((path, params))

        if path.endswith("/api_keys"):
            pid = path.split("/")[-2]
            rows = self.api_keys.get(pid, [])
            after = params.get("after")
            if after is not None:
                ids = [r.get("id") for r in rows]
                rows = rows[ids.index(after) + 1:] if after in ids else []
            page = rows[: self.api_key_page_size]
            return {"data": page, "has_more": len(rows) > len(page)}

        if path == "/organization/projects":
            return {
                "data": [{"id": pid, "name": name} for pid, name in self.projects],
                "has_more": False,
            }

        if path == "/organization/costs":
            return {"data": self.cost_buckets, "next_page": None}

        if path == "/organization/usage/completions":
            if "api_key_id" in (params.get("group_by") or []):
                pid = (params.get("project_ids") or [None])[0]
                return {"data": self.key_usage.get(pid, []), "next_page": None}
            return {"data": self.org_usage, "next_page": None}

        raise AssertionError(f"unexpected path {path}")


DAY_TS = 1_786_060_800          # a fixed UTC midnight
CLAUDE_KEY = {"id": "key_claude", "name": "Claude",
              "owner": {"service_account": {"name": "Claude"}}}
OTHER_KEY = {"id": "key_other", "name": "intelligence-service",
             "owner": {"service_account": {"name": "intelligence-service"}}}


class _Base(unittest.TestCase):
    def setUp(self):
        self.cost = _load("cost.py")
        self.report = _load("report.py")
        # report.py imported its own `openai_admin`; patch the object it holds.
        self.api = self.report.api

    def run_collect(self, fake: FakeAPI) -> dict:
        fake.install(self.api)
        return self.report.collect(DAY_TS, DAY_TS + 86_400, "sk-admin-test")


# ---------------------------------------------------------------------------
# 1. Tier normalisation
# ---------------------------------------------------------------------------

class TierNormalisationTests(_Base):
    def test_usage_and_cost_spellings_agree(self):
        """`flex-tier` (usage) and `flex` (costs) must fold to one token."""
        self.assertEqual(self.cost.normalize_tier("flex-tier"),
                         self.cost.normalize_tier("flex"))

    def test_default_aliases_collapse(self):
        for raw in ("standard", "auto", "default", "", None, "  Default  "):
            with self.subTest(raw=raw):
                self.assertEqual(self.cost.normalize_tier(raw), "default")

    def test_unknown_tier_is_preserved_not_defaulted(self):
        """A tier we don't know must not be silently folded into `default`.

        Folding it would price it at the standard rate — wrong, and invisible.
        """
        self.assertEqual(self.cost.normalize_tier("priority-tier"), "priority")

    def test_line_item_tier_prefix_is_parsed(self):
        parsed = self.cost.parse_line_item("flex | gpt-5.4-2026-03-05, cached input")
        self.assertEqual(parsed, ("gpt-5.4-2026-03-05", "flex", "cached"))

    def test_line_item_without_prefix_is_default_tier(self):
        parsed = self.cost.parse_line_item("gpt-5.4-2026-03-05, input")
        self.assertEqual(parsed, ("gpt-5.4-2026-03-05", "default", "uncached"))

    def test_model_names_containing_commas_are_not_assumed(self):
        """Split on the LAST comma — model names carry dots and hyphens."""
        parsed = self.cost.parse_line_item("gpt-5.6-terra, output")
        self.assertEqual(parsed, ("gpt-5.6-terra", "default", "output"))

    def test_non_token_line_items_are_dropped(self):
        """Fixed per-request charges must not pollute the per-token rates."""
        for line_item in ("web search calls", "", None, "gpt-5.4, mystery dimension"):
            with self.subTest(line_item=line_item):
                self.assertIsNone(self.cost.parse_line_item(line_item))


class FlexTrafficIsPricedTests(_Base):
    """The end-to-end form of the bug: real flex requests billing as $0.00."""

    def _flex_only_fake(self) -> FakeAPI:
        return FakeAPI(
            cost_buckets=[_cost_bucket(DAY_TS, [("flex | gpt-5.4, input", 10.0)])],
            org_usage=[{"start_time": DAY_TS, "results": [
                _usage_result("gpt-5.4", "flex-tier", uncached=1_000_000),
            ]}],
            key_usage={"proj_1": [{"start_time": DAY_TS, "results": [
                _usage_result("gpt-5.4", "flex-tier", uncached=500_000,
                              requests=250, key_id="key_claude"),
            ]}]},
            api_keys={"proj_1": [CLAUDE_KEY]},
        )

    def test_flex_traffic_gets_non_zero_dollars(self):
        data = self.run_collect(self._flex_only_fake())
        day = data["daily"][0]
        self.assertEqual(day["requests"], 250)
        # $10 / 1M tokens x 500k tokens = $5.00
        self.assertAlmostEqual(day["usd"], 5.0, places=6)

    def test_requests_without_dollars_is_the_failure_signature(self):
        """The assertion that fails if tier normalisation regresses."""
        data = self.run_collect(self._flex_only_fake())
        day = data["daily"][0]
        self.assertTrue(
            day["requests"] > 0 and day["usd"] > 0,
            "non-zero requests priced at $0.00 — the tier lookup missed",
        )
        self.assertEqual(data["derivation"]["unpriced"], [])

    def test_broken_normalisation_would_be_caught(self):
        """Failure injection: neuter the fold, confirm the suite would notice.

        Guards the guard — if `normalize_tier` stopped folding `-tier`, the
        report must go to $0.00 with requests intact, which the two tests above
        assert against.
        """
        self.cost.normalize_tier = lambda raw: (raw or "default").strip().lower()
        self.report.normalize_tier = self.cost.normalize_tier

        data = self.run_collect(self._flex_only_fake())
        day = data["daily"][0]
        self.assertEqual(day["requests"], 250)
        self.assertEqual(day["usd"], 0.0)
        self.assertTrue(data["derivation"]["unpriced"])


# ---------------------------------------------------------------------------
# 2. Cached vs uncached rate split
# ---------------------------------------------------------------------------

class CachedRateSplitTests(_Base):
    """A blended $/token cannot reproduce these numbers.

    Org-wide corpus for the day:
        uncached 1,000,000 tok  ->  $10.00   ($10 per 1M)
        cached   9,000,000 tok  ->  $ 9.00   ($ 1 per 1M)
        totals  10,000,000 tok  ->  $19.00   (blended $1.90 per 1M)

    The key used a *cache-light* mix (90% uncached), the inverse of the org.
    """

    def _fake(self) -> FakeAPI:
        return FakeAPI(
            cost_buckets=[_cost_bucket(DAY_TS, [
                ("gpt-5.4, input", 10.0),
                ("gpt-5.4, cached input", 9.0),
            ])],
            org_usage=[{"start_time": DAY_TS, "results": [
                _usage_result("gpt-5.4", "default",
                              uncached=1_000_000, cached=9_000_000),
            ]}],
            key_usage={"proj_1": [{"start_time": DAY_TS, "results": [
                _usage_result("gpt-5.4", "default",
                              uncached=900_000, cached=100_000,
                              requests=100, key_id="key_claude"),
            ]}]},
            api_keys={"proj_1": [CLAUDE_KEY]},
        )

    def test_split_rates_are_used(self):
        data = self.run_collect(self._fake())
        # 900k x $10/1M + 100k x $1/1M = 9.00 + 0.10 = $9.10
        self.assertAlmostEqual(data["total_usd"], 9.10, places=6)

    def test_blended_rate_answer_is_rejected(self):
        """The number a blended implementation would produce, pinned as wrong.

        1,000,000 tok x blended $1.90/1M = $1.90 — off by ~4.8x.
        """
        data = self.run_collect(self._fake())
        self.assertNotAlmostEqual(data["total_usd"], 1.90, places=2)

    def test_cache_heavy_and_cache_light_days_diverge(self):
        """Same token count, opposite cache mix, materially different cost."""
        light = self.run_collect(self._fake())["total_usd"]

        self.setUp()
        heavy_fake = self._fake()
        heavy_fake.key_usage["proj_1"][0]["results"] = [
            _usage_result("gpt-5.4", "default",
                          uncached=100_000, cached=900_000,
                          requests=100, key_id="key_claude"),
        ]
        heavy = self.run_collect(heavy_fake)["total_usd"]

        # 100k x $10/1M + 900k x $1/1M = 1.00 + 0.90 = $1.90
        self.assertAlmostEqual(heavy, 1.90, places=6)
        self.assertGreater(light / heavy, 4.0,
                           "cache mix must move the derived cost by multiples")

    def test_cache_write_and_output_are_priced_on_their_own_rates(self):
        fake = FakeAPI(
            cost_buckets=[_cost_bucket(DAY_TS, [
                ("gpt-5.4, output", 40.0),
                ("gpt-5.4, cache write", 5.0),
            ])],
            org_usage=[{"start_time": DAY_TS, "results": [
                _usage_result("gpt-5.4", "default", output=1_000_000,
                              cachewrite=1_000_000),
            ]}],
            key_usage={"proj_1": [{"start_time": DAY_TS, "results": [
                _usage_result("gpt-5.4", "default", output=100_000,
                              cachewrite=200_000, requests=10, key_id="key_claude"),
            ]}]},
            api_keys={"proj_1": [CLAUDE_KEY]},
        )
        data = self.run_collect(fake)
        # 100k x $40/1M + 200k x $5/1M = 4.00 + 1.00 = $5.00
        self.assertAlmostEqual(data["total_usd"], 5.0, places=6)


# ---------------------------------------------------------------------------
# 3. Key discovery
# ---------------------------------------------------------------------------

class KeyDiscoveryTests(_Base):
    def test_matches_key_name_or_service_account_name(self):
        cases = [
            ({"name": "Claude"}, True),
            ({"name": "claude"}, True),
            ({"name": "x", "owner": {"service_account": {"name": "Claude"}}}, True),
            ({"name": "x", "owner": {"user": {"name": "claude"}}}, True),
            ({"name": "intelligence-service"}, False),
            ({"name": "claude-code"}, False),   # not an exact name match
            ({}, False),
        ]
        for item, expected in cases:
            with self.subTest(item=item):
                self.assertEqual(self.cost.is_claude_key(item), expected)

    def test_other_keys_traffic_is_excluded(self):
        """A project's non-Claude key must contribute nothing."""
        fake = FakeAPI(
            cost_buckets=[_cost_bucket(DAY_TS, [("gpt-5.4, input", 10.0)])],
            org_usage=[{"start_time": DAY_TS, "results": [
                _usage_result("gpt-5.4", "default", uncached=1_000_000),
            ]}],
            key_usage={"proj_1": [{"start_time": DAY_TS, "results": [
                _usage_result("gpt-5.4", "default", uncached=100_000,
                              requests=10, key_id="key_claude"),
                _usage_result("gpt-5.4", "default", uncached=900_000,
                              requests=990, key_id="key_other"),
            ]}]},
            api_keys={"proj_1": [CLAUDE_KEY, OTHER_KEY]},
        )
        data = self.run_collect(fake)
        self.assertEqual(data["key_count"], 1)
        self.assertEqual(data["daily"][0]["requests"], 10)
        self.assertAlmostEqual(data["total_usd"], 1.0, places=6)

    def test_api_key_listing_follows_pagination(self):
        """A key past the first page must not be silently dropped.

        `/api_keys` was requested one page deep at `limit` 100 with no
        `has_more` follow, so a project holding more keys than a page silently
        lost the rest — and a lost `Claude` key is an undercount with no error,
        the exact failure class this skill exists to surface. Page size is
        forced to 1 here so two keys span two pages.
        """
        second = {"id": "key_claude_2", "name": "Claude",
                  "owner": {"service_account": {"name": "Claude"}}}
        fake = FakeAPI(
            cost_buckets=[_cost_bucket(DAY_TS, [("gpt-5.4, input", 10.0)])],
            org_usage=[{"start_time": DAY_TS, "results": [
                _usage_result("gpt-5.4", "default", uncached=1_000_000),
            ]}],
            key_usage={"proj_1": [{"start_time": DAY_TS, "results": [
                _usage_result("gpt-5.4", "default", uncached=100_000,
                              requests=10, key_id="key_claude"),
                _usage_result("gpt-5.4", "default", uncached=400_000,
                              requests=40, key_id="key_claude_2"),
            ]}]},
            api_keys={"proj_1": [CLAUDE_KEY, second]},
            api_key_page_size=1,
        )
        data = self.run_collect(fake)
        self.assertEqual(data["key_count"], 2, "second page of keys was dropped")
        self.assertEqual(data["daily"][0]["requests"], 50)
        self.assertAlmostEqual(data["total_usd"], 5.0, places=6)

    def test_api_key_pagination_terminates_without_forward_progress(self):
        """A server that keeps saying has_more must not spin forever."""
        fake = FakeAPI(
            cost_buckets=[], org_usage=[], key_usage={},
            api_keys={"proj_1": [CLAUDE_KEY]},
        )
        fake.install(self.api)
        stuck = {"n": 0}

        def always_more(path, params, api_key, *, project_id=None):
            if path.endswith("/api_keys"):
                stuck["n"] += 1
                if stuck["n"] > 20:
                    raise AssertionError("pagination did not terminate")
                return {"data": [CLAUDE_KEY], "has_more": True}
            return fake._request(path, params, api_key, project_id=project_id)

        self.api.request = always_more
        keys = self.api.fetch_project_api_keys("proj_1", "sk-admin-test")
        self.assertTrue(keys)
        self.assertLessEqual(stuck["n"], 2)

    def test_partial_failure_keeps_the_pages_already_read(self):
        """Losing access mid-walk returns what paged in, not nothing."""
        fake = FakeAPI(
            cost_buckets=[], org_usage=[], key_usage={},
            api_keys={"proj_1": [CLAUDE_KEY]},
        )
        fake.install(self.api)
        seen = {"n": 0}

        def fails_on_second(path, params, api_key, *, project_id=None):
            if path.endswith("/api_keys"):
                seen["n"] += 1
                if seen["n"] == 1:
                    return {"data": [CLAUDE_KEY], "has_more": True}
                raise self.api.OpenAIAdminError("OpenAI 403 on api_keys")
            return fake._request(path, params, api_key, project_id=project_id)

        self.api.request = fails_on_second
        keys = self.api.fetch_project_api_keys("proj_1", "sk-admin-test")
        self.assertEqual([k["id"] for k in keys], ["key_claude"])

    def test_unreadable_project_is_skipped_not_fatal(self):
        """A 403 on one project must not kill an org-wide walk."""
        fake = FakeAPI(
            cost_buckets=[_cost_bucket(DAY_TS, [("gpt-5.4, input", 10.0)])],
            org_usage=[{"start_time": DAY_TS, "results": [
                _usage_result("gpt-5.4", "default", uncached=1_000_000),
            ]}],
            key_usage={"proj_1": [{"start_time": DAY_TS, "results": [
                _usage_result("gpt-5.4", "default", uncached=100_000,
                              requests=10, key_id="key_claude"),
            ]}]},
            api_keys={"proj_1": [CLAUDE_KEY]},
            projects=(("proj_1", "Conversation [Production]"), ("proj_denied", "Locked")),
        )
        original = fake._request

        def denying(path, params, api_key, *, project_id=None):
            if path == "/organization/projects/proj_denied/api_keys":
                raise self.api.OpenAIAdminError("OpenAI 403 on api_keys")
            return original(path, params, api_key, project_id=project_id)

        self.api.request = denying
        data = self.report.collect(DAY_TS, DAY_TS + 86_400, "sk-admin-test")
        self.assertEqual(data["project_count"], 1)
        self.assertAlmostEqual(data["total_usd"], 1.0, places=6)


# ---------------------------------------------------------------------------
# 4. Rate fallback and unpriced disclosure
# ---------------------------------------------------------------------------

class FallbackAndDisclosureTests(_Base):
    def test_thin_day_falls_back_to_window_rate_and_says_so(self):
        rates = self.cost.RateTable()
        rates.add_cost("2026-08-20", "gpt-5.4", "default", "uncached", 10.0)
        rates.add_tokens("2026-08-20", "gpt-5.4", "default", "uncached", 1_000_000)

        # A different day with tokens but no dollars of its own.
        rate = rates.rate("2026-08-21", "gpt-5.4", "default", "uncached")
        self.assertAlmostEqual(rate, 10.0 / 1_000_000)
        self.assertTrue(rates.fallbacks)

    def test_no_rate_anywhere_is_recorded_as_unpriced(self):
        rates = self.cost.RateTable()
        self.assertEqual(rates.rate("2026-08-21", "ghost", "default", "output"), 0.0)
        self.assertIn(("ghost", "default", "output"), rates.unpriced)

    def test_zero_token_dimensions_do_not_register_as_unpriced(self):
        """A model that never writes cache isn't missing a cache-write rate."""
        rates = self.cost.RateTable()
        rates.add_cost("2026-08-21", "gpt-5.4", "default", "output", 4.0)
        rates.add_tokens("2026-08-21", "gpt-5.4", "default", "output", 1_000_000)
        usd = rates.cost_of("2026-08-21", "gpt-5.4", "default",
                            {"uncached": 0, "cached": 0, "cachewrite": 0, "output": 500_000})
        self.assertAlmostEqual(usd, 2.0, places=6)
        self.assertEqual(rates.unpriced, set())

    def test_unpriced_traffic_is_surfaced_in_the_markdown(self):
        fake = FakeAPI(
            cost_buckets=[],   # no dollars at all
            org_usage=[],
            key_usage={"proj_1": [{"start_time": DAY_TS, "results": [
                _usage_result("gpt-mystery", "default", uncached=100_000,
                              requests=10, key_id="key_claude"),
            ]}]},
            api_keys={"proj_1": [CLAUDE_KEY]},
        )
        data = self.run_collect(fake)
        self.assertEqual(data["total_usd"], 0.0)
        self.assertTrue(data["derivation"]["unpriced"])
        rendered = self.report.render_markdown(data)
        self.assertIn("undercount", rendered)
        self.assertIn("DERIVED", rendered)


# ---------------------------------------------------------------------------
# 5. Window resolution
# ---------------------------------------------------------------------------

class WindowTests(_Base):
    class Args:
        def __init__(self, **kw):
            self.last = kw.get("last")
            self.date = kw.get("date")
            self.since = kw.get("since")
            self.until = kw.get("until")

    def _now(self):
        from datetime import datetime, timezone
        return datetime(2026, 8, 23, 14, 30, tzinfo=timezone.utc)

    def test_default_is_thirty_days_ending_tomorrow(self):
        start, end = self.report.resolve_window(self.Args(), now=self._now())
        self.assertEqual(start.strftime("%Y-%m-%d"), "2026-07-25")
        self.assertEqual(end.strftime("%Y-%m-%d"), "2026-08-24")
        self.assertEqual((end - start).days, 30)

    def test_single_date_is_one_day_half_open(self):
        start, end = self.report.resolve_window(
            self.Args(date="2026-08-21"), now=self._now())
        self.assertEqual(start.strftime("%Y-%m-%d"), "2026-08-21")
        self.assertEqual((end - start).days, 1)

    def test_explicit_range_end_is_inclusive(self):
        start, end = self.report.resolve_window(
            self.Args(since="2026-08-01", until="2026-08-21"), now=self._now())
        self.assertEqual(start.strftime("%Y-%m-%d"), "2026-08-01")
        self.assertEqual(end.strftime("%Y-%m-%d"), "2026-08-22")

    def test_half_open_range_excludes_nothing_from_a_single_day(self):
        """--from X --to X must cover exactly day X, not zero days."""
        start, end = self.report.resolve_window(
            self.Args(since="2026-08-21", until="2026-08-21"), now=self._now())
        self.assertEqual((end - start).days, 1)

    def test_bad_input_is_rejected(self):
        for kwargs in ({"date": "21-08-2026"}, {"since": "2026-08-01"},
                       {"since": "2026-08-21", "until": "2026-08-01"}, {"last": 0}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(SystemExit):
                    self.report.resolve_window(self.Args(**kwargs), now=self._now())


class ScriptConventionTests(unittest.TestCase):
    def test_stdout_is_reconfigured_to_utf8(self):
        """CLAUDE.md: every script printing to stdout guards Windows cp1252."""
        source = (SCRIPTS / "report.py").read_text(encoding="utf-8")
        main_body = source.split("def main()", 1)[1]
        self.assertIn('reconfigure(encoding="utf-8")', main_body)

    def test_skill_is_self_contained(self):
        """Architecture principle 1 — no imports reaching outside the folder."""
        for script in SCRIPTS.glob("*.py"):
            text = script.read_text(encoding="utf-8")
            self.assertNotIn("../", text, f"{script.name} reaches outside its folder")
            self.assertNotIn("plugins/", text, f"{script.name} hardcodes a repo path")


if __name__ == "__main__":
    unittest.main()
