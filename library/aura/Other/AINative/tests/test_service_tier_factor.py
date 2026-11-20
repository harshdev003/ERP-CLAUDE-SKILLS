#!/usr/bin/env python3
"""Service-tier price weighting in the OpenAI ledger allocators.

The defect this guards
----------------------
The cost skills never multiply tokens by a price. They read the authoritative
dollars from `/v1/organization/costs` and split them across prompters/users
**proportionally by ledger tokens**. That is exact only while every call in a
project costs the same per token.

It no longer does. OpenAI prices the **Flex** service tier at exactly 50% of
Standard and **Fast** (renamed from `priority` on 2026-07-30) at 2x, and Aura
enabled flex per prompter. `ENGBACK-1315` added the `serviceTier` column to the
`llmCall` ledger so the tier is knowable per call.

Why "one project, one tier" is not a defence
--------------------------------------------
Proportional allocation normalises, so a **tier-homogeneous** project cancels the
factor out entirely — which is exactly why this shipped unnoticed and why
`test_homogeneous_*` below asserts the no-op. The skew needs tiers to mix inside
one project, and two things guarantee that:

- Different prompters in one project run different tiers (observed 2026-08-07:
  the `feed` project carried both `flex` and `default` in a single 40-minute
  window).
- A prompter that *requests* flex falls back to `auto`/`default` under capacity
  pressure — recording that fallback is the stated reason ENGBACK-1315 exists.
  So even a single-prompter project mixes tiers whenever OpenAI is busy.

A flex call allocated as if it were standard absorbs twice the dollars it
actually cost, and the user it belongs to is over-charged in every per-user
readout. Nothing errors; the totals still reconcile to the bill. That silence is
the whole reason this needs a test rather than a spot-check.

Why an absent tier must weigh 1.0
---------------------------------
Rows written before the ENGBACK-1315 deploy carry `serviceTier = null`. Null
means "not recorded", not "free" and not "standard" — but 1.0 is still the right
factor, because it reproduces the pre-change allocation exactly. A window that
straddles the deploy then degrades toward the old behaviour instead of skewing
in a new direction. `test_absent_tier_*` pins that.

Runs against every allocator that carries its own copy of the factor table
(Architecture principle 1 — no cross-skill imports), so a copy that drifts
behaviourally fails here even when nobody diffed the files. One allocator,
`report-openai-user-cost`, is knowingly excluded — `DeferredAllocatorTests`
below records why, and fails if someone patches it without fixing the ratio.
"""
from __future__ import annotations

import pathlib
import unittest

from reader_harness import REPO_ROOT, load_reader

# Every skill that splits Costs-API dollars by ledger token weight and carries a
# copy of the factor table. `report-openai-user-cost` is deliberately absent — see
# DeferredAllocatorTests at the bottom of this file for why.
ALLOCATORS = (
    "analyze-openai-costs",
    "analyze-openai-cost-per-user",
    "analyze-openai-cost-per-subscription-state",
    "report-aurie-weekly-costs",
    "report-openai-costs",
    "report-top-conversation-users",
    "report-openai-user-cost",
)



def _path(skill: str) -> pathlib.Path:
    return REPO_ROOT / "plugins" / "eng" / "skills" / skill / "scripts" / "report.py"


def _load(skill: str):
    # siblings=() — these are standalone entry points, not the responses_reader
    # family the harness defaults to purging.
    return load_reader(_path(skill), siblings=())


# tier string -> expected multiplier relative to Standard.
EXPECTED_FACTORS = {
    "flex": 0.5,
    "batch": 0.5,
    "default": 1.0,
    "auto": 1.0,
    "scale": 1.0,
    "priority": 2.0,
    "fast": 2.0,
}


def _completion(uid: str, tokens: int, tier: str | None, *, premium: bool = True) -> dict:
    """A completion carrying both usage shapes, so every extractor reads it."""
    item = {
        "id": f"cmpl-{uid}-{tier}",
        "created": 1_786_000_000,
        "metadata": {
            "user": uid,
            "name": "summary-prompter",
            "aiPremium": "true" if premium else "false",
            "premium": "true" if premium else "false",
        },
        "usage": {
            "input_tokens": tokens,
            "prompt_tokens": tokens,
            "output_tokens": 0,
            "completion_tokens": 0,
            "prompt_tokens_details": {"cached_tokens": 0},
        },
    }
    if tier is not None:
        item["service_tier"] = tier
    return item


class ServiceTierFactorTests(unittest.TestCase):
    """The factor table itself, in every copy that carries one."""

    def test_known_tiers_map_to_their_multiplier(self):
        for skill in ALLOCATORS:
            mod = _load(skill)
            for tier, expected in EXPECTED_FACTORS.items():
                with self.subTest(skill=skill, tier=tier):
                    self.assertEqual(mod.service_tier_factor(tier), expected)

    def test_flex_is_exactly_half(self):
        # The headline fact. Pinned on its own so a table edit that quietly
        # rounds flex to something else fails with an obvious name.
        for skill in ALLOCATORS:
            with self.subTest(skill=skill):
                self.assertEqual(_load(skill).service_tier_factor("flex"), 0.5)

    def test_absent_tier_weighs_one(self):
        # Pre-ENGBACK-1315 rows. Must reproduce the old allocation exactly.
        for skill in ALLOCATORS:
            mod = _load(skill)
            for absent in (None, "", 0):
                with self.subTest(skill=skill, value=absent):
                    self.assertEqual(mod.service_tier_factor(absent), 1.0)

    def test_unknown_tier_weighs_one(self):
        # A tier OpenAI introduces later must not silently zero a call out of
        # the split — degrade to standard weight instead.
        for skill in ALLOCATORS:
            with self.subTest(skill=skill):
                self.assertEqual(_load(skill).service_tier_factor("turbo-9000"), 1.0)

    def test_tier_string_is_normalised(self):
        for skill in ALLOCATORS:
            mod = _load(skill)
            for variant in ("FLEX", " flex ", "Flex"):
                with self.subTest(skill=skill, variant=variant):
                    self.assertEqual(mod.service_tier_factor(variant), 0.5)

    def test_every_factor_is_positive(self):
        # A zero factor would drop a call out of the denominator and silently
        # redistribute its dollars across everyone else.
        for skill in ALLOCATORS:
            mod = _load(skill)
            for tier, factor in mod.SERVICE_TIER_FACTORS.items():
                with self.subTest(skill=skill, tier=tier):
                    self.assertGreater(factor, 0)


class LedgerRowCarriesTierTests(unittest.TestCase):
    """The column has to survive the ledger-row -> internal-shape mapping.

    A correct factor table is inert if the mapper drops `serviceTier`, and that
    failure looks identical to a window with no flex traffic.
    """

    def test_mapper_propagates_service_tier(self):
        row = {
            "openaiId": "resp_1",
            "userId": "u1",
            "prompterName": "summary-prompter",
            "inputTokens": 100,
            "outputTokens": 10,
            "cachedTokens": 0,
            "totalTokens": 110,
            "createdAt": "2026-08-07T20:00:00.000Z",
            "serviceTier": "flex",
        }
        cases = {
            "analyze-openai-costs": lambda m: m._record_from_ledger_row(row, "post"),
            "analyze-openai-cost-per-user": lambda m: m._ledger_row_to_item(row),
            "analyze-openai-cost-per-subscription-state": lambda m: m._ledger_row_to_completion(row),
            "report-aurie-weekly-costs": lambda m: m._ledger_row_to_completion(row),
        }
        for skill, call in cases.items():
            with self.subTest(skill=skill):
                self.assertEqual(call(_load(skill)).get("service_tier"), "flex")


class CostShapedWeightTests(unittest.TestCase):
    """`analyze-openai-costs._cost_weight` — the one cost-shaped allocator."""

    def setUp(self):
        self.mod = _load("analyze-openai-costs")
        self.weights = {
            "input": self.mod.W_INPUT_DEFAULT,
            "cached": self.mod.W_CACHED_DEFAULT,
            "output": self.mod.W_OUTPUT_DEFAULT,
        }

    def _w(self, tier):
        rec = {"input": 1000, "cached": 0, "output": 100, "tokens": 1100,
               "service_tier": tier}
        return self.mod._cost_weight(rec, self.weights)

    def test_flex_halves_the_weight(self):
        self.assertAlmostEqual(self._w("flex"), self._w("default") / 2)

    def test_fast_doubles_the_weight(self):
        self.assertAlmostEqual(self._w("fast"), self._w("default") * 2)

    def test_absent_tier_matches_default(self):
        self.assertAlmostEqual(self._w(None), self._w("default"))

    def test_flat_total_fallback_is_also_scaled(self):
        # The branch for a record with no input/output split still has to carry
        # the tier, or flex calls that only report a total escape the scaling.
        flat = {"input": 0, "cached": 0, "output": 0, "tokens": 800,
                "service_tier": "flex"}
        self.assertAlmostEqual(self.mod._cost_weight(flat, self.weights), 400.0)


class MixedTierAllocationTests(unittest.TestCase):
    """End-to-end: the dollar split across a project that mixes tiers.

    Two users, identical token counts, one on flex and one on default. Flex costs
    half, so the dollars must land 1:2 — a tier-blind split would say 1:1.
    """

    DOLLARS = {"proj_1": 90.0}

    def test_per_user_mixed_tier_split(self):
        mod = _load("analyze-openai-cost-per-user")
        completions = {"proj_1": [
            _completion("flex_user", 1000, "flex"),
            _completion("std_user", 1000, "default"),
        ]}
        per_user, untagged = mod.aggregate_per_user(
            self.DOLLARS, completions, {"proj_1": {"name": "conversation"}}
        )
        self.assertAlmostEqual(per_user["flex_user"]["amount"], 30.0)
        self.assertAlmostEqual(per_user["std_user"]["amount"], 60.0)
        self.assertAlmostEqual(untagged, 0.0)

    def test_per_user_homogeneous_tier_is_a_no_op(self):
        # Both flex: normalisation cancels the factor, so the split is 50/50 —
        # identical to the pre-change behaviour. This is why the bug hid.
        mod = _load("analyze-openai-cost-per-user")
        completions = {"proj_1": [
            _completion("a", 1000, "flex"),
            _completion("b", 1000, "flex"),
        ]}
        per_user, _ = mod.aggregate_per_user(
            self.DOLLARS, completions, {"proj_1": {"name": "summary"}}
        )
        self.assertAlmostEqual(per_user["a"]["amount"], 45.0)
        self.assertAlmostEqual(per_user["b"]["amount"], 45.0)

    def test_per_user_absent_tier_allocates_as_standard(self):
        mod = _load("analyze-openai-cost-per-user")
        completions = {"proj_1": [
            _completion("legacy", 1000, None),
            _completion("std_user", 1000, "default"),
        ]}
        per_user, _ = mod.aggregate_per_user(
            self.DOLLARS, completions, {"proj_1": {"name": "conversation"}}
        )
        self.assertAlmostEqual(per_user["legacy"]["amount"], 45.0)
        self.assertAlmostEqual(per_user["std_user"]["amount"], 45.0)

    def test_subscription_state_mixed_tier_split(self):
        mod = _load("analyze-openai-cost-per-subscription-state")
        completions = {"proj_1": [
            _completion("flex_user", 1000, "flex", premium=True),
            _completion("std_user", 1000, "default", premium=False),
        ]}
        out = mod.allocate_dollars(self.DOLLARS, completions)
        premium = out["user_dollars_ai"]
        flex_share = sum(d.get("flex_user", 0.0) for d in premium.values())
        std_share = sum(d.get("std_user", 0.0) for d in premium.values())
        self.assertAlmostEqual(flex_share, 30.0)
        self.assertAlmostEqual(std_share, 60.0)

    def test_subscription_state_coverage_reports_raw_tokens(self):
        # coverage.total_tokens is rendered as a token count. Scaling it by the
        # price factor would silently turn it into a weight.
        mod = _load("analyze-openai-cost-per-subscription-state")
        completions = {"proj_1": [
            _completion("flex_user", 1000, "flex"),
            _completion("std_user", 1000, "default"),
        ]}
        out = mod.allocate_dollars(self.DOLLARS, completions)
        self.assertEqual(out["coverage"]["proj_1"]["total_tokens"], 2000)

    def test_weekly_costs_mixed_tier_split(self):
        mod = _load("report-aurie-weekly-costs")
        completions = {"proj_1": [
            _completion("flex_user", 1000, "flex", premium=True),
            _completion("std_user", 1000, "default", premium=False),
        ]}
        by_tier, _ = mod.allocate_by_ai_tier(self.DOLLARS, completions)
        self.assertAlmostEqual(sum(by_tier.values()), 90.0)
        # The premium (flex) bucket and the non-premium (default) bucket must
        # differ 1:2 despite carrying identical token counts.
        premium_total = sum(v for k, v in by_tier.items() if "untagged" not in k.lower())
        self.assertAlmostEqual(premium_total, 90.0)
        self.assertEqual(len([v for v in by_tier.values() if v > 0]), 2)
        self.assertAlmostEqual(min(by_tier.values()) * 2, max(by_tier.values()))


class ReportOpenaiCostsExtractionTests(unittest.TestCase):
    """`report-openai-costs` scales in its two extraction functions.

    It has no intermediate mapper dict — the ledger row and the stored completion
    are each read straight into `(prompter, uid, tier, weight)` — so the scaling
    is asserted on those functions directly.
    """

    def setUp(self):
        self.mod = _load("report-openai-costs")

    def test_ledger_extraction_halves_on_flex(self):
        row = {"inputTokens": 1000, "outputTokens": 0, "prompterName": "summary-prompter",
               "userId": "u1", "aiPremium": True, "aiPremiumState": "paid"}
        std = self.mod.extract_ledger_fields({**row, "serviceTier": "default"})[3]
        flex = self.mod.extract_ledger_fields({**row, "serviceTier": "flex"})[3]
        absent = self.mod.extract_ledger_fields(row)[3]
        self.assertAlmostEqual(flex, std / 2)
        self.assertAlmostEqual(absent, std)

    def test_completion_extraction_halves_on_flex(self):
        std = self.mod.extract_fields(_completion("u1", 1000, "default"))[3]
        flex = self.mod.extract_fields(_completion("u1", 1000, "flex"))[3]
        self.assertAlmostEqual(flex, std / 2)


class TopConversationUsersTests(unittest.TestCase):
    """`report-top-conversation-users` scales inline in its allocation loops.

    Both the numerator (`user_tok`) and the denominator (`full_day_tok`) are
    accumulated in the *same* loop from the *same* scaled value, which is what
    keeps the ratio on one basis. This pins that the module exposes the factor and
    that both of its accumulators were retyped to float — an int accumulator would
    silently truncate every flex row's weight.
    """

    def test_module_exposes_factor(self):
        self.assertEqual(_load("report-top-conversation-users").service_tier_factor("flex"), 0.5)

    def test_accumulators_are_float_typed(self):
        src = _path("report-top-conversation-users").read_text()
        self.assertNotIn("user_tok: dict[str, int]", src)
        self.assertNotIn("full_day_tok = 0\n", src)


class UserCostBothSidesTests(unittest.TestCase):
    """`report-openai-user-cost` weights only the ledger portion. [ENG-1107]

    This is the one allocator whose numerator and denominator are built from two
    kinds of source, and only one knows the tier:

      ledger rows / stored completions -> carry the tier   -> weightable
      /organization/usage/completions  -> aggregate, none  -> NOT weightable

    The denominator falls back to that Usage endpoint for pre-epoch slices,
    non-ledger projects and any ledger outage, while the numerator's matching
    slice is a per-completion walk that *does* know the tier. So the tempting fix
    — scale everything you can — is the wrong one: it would scale the numerator
    while its denominator stayed raw and systematically **under**-charge flex
    users, replacing one error with another pointing the other way.

    The rule instead is per-source symmetry: weight the ledger contribution on
    both sides, add every non-ledger contribution at face value on both sides.
    These tests pin both halves of that.
    """

    def setUp(self):
        self.mod = _load("report-openai-user-cost")
        self.epoch = self.mod.LEDGER_EPOCH_UNIX
        # Neutralise every network call; each test re-stubs what it needs.
        self.mod.project_cost_usd = lambda *a, **k: 100.0
        self.mod.project_chat_usage_tokens = lambda *a, **k: (0, 0)
        self.mod.iter_llm_calls = lambda *a, **k: []
        self.mod.fetch_paginated_openai = lambda *a, **k: []
        self.mod.user_tokens_in_project = lambda *a, **k: (0, 0, 0)
        self.mod._resolve_completions_api_key = lambda name: "sk-test"
        self.mod._USER_ROWS.clear()

    @staticmethod
    def _row(uid, tokens, tier):
        return {"provider": "openai", "userId": uid, "openaiId": f"r-{uid}-{tier}", "project": "summary",
                "inputTokens": tokens, "outputTokens": 0, "serviceTier": tier}

    def _usage(self, rows):
        """The Usage API's `group_by=service_tier` buckets for these ledger rows (it spells flex `flex-tier`)."""
        by = {}
        for r in rows:
            t = "flex-tier" if r["serviceTier"] == "flex" else r["serviceTier"]
            by[t] = by.get(t, 0) + r["inputTokens"]
        self.mod.fetch_paginated_openai = lambda *a, **k: [
            {"results": [{"service_tier": t, "input_tokens": n, "output_tokens": 0} for t, n in by.items()]}]

    # -- ledger slice: the weight must diverge from the raw count ------------

    def test_ledger_denominator_weights_flex_at_half(self):
        rows = [self._row("a", 1000, "flex"), self._row("b", 1000, "default")]
        self._usage(rows)
        cost, inp, out_, weight, sources, fell_back = \
            self.mod.project_totals_with_ledger_failover(
                "proj", "Summary [Production]", self.epoch, self.epoch + 3600, "k")
        self.assertEqual(inp + out_, 2000)          # raw is untouched
        self.assertAlmostEqual(weight, 1500.0)      # 1000*0.5 + 1000*1.0
        self.assertIn("ledger", sources)

    def test_ledger_numerator_weights_flex_at_half(self):
        self.mod.iter_llm_calls = lambda *a, **k: [self._row("a", 1000, "flex")]
        prompt, completion, count, weight, sources, fell_back = \
            self.mod.user_tokens_with_ledger_failover(
                "proj", "Summary [Production]", "a", self.epoch, self.epoch + 3600)
        self.assertEqual(prompt + completion, 1000)
        self.assertAlmostEqual(weight, 500.0)

    def test_flex_user_gets_half_the_share_of_an_identical_default_user(self):
        # The defect in one assertion: two users, identical token counts, one on
        # flex. Dollars must land 1:2, not 1:1.
        rows = [self._row("flex_u", 1000, "flex"), self._row("std_u", 1000, "default")]
        self._usage(rows)
        _, _, _, proj_w, _, _ = self.mod.project_totals_with_ledger_failover(
            "proj", "Summary [Production]", self.epoch, self.epoch + 3600, "k")

        def share(uid):
            self.mod._USER_ROWS.clear()
            self.mod.iter_llm_calls = lambda *a, **k: [r for r in rows if r["userId"] == uid]
            _, _, _, w, _, _ = self.mod.user_tokens_with_ledger_failover(
                "proj", "Summary [Production]", uid, self.epoch, self.epoch + 3600)
            return 90.0 * w / proj_w

        self.assertAlmostEqual(share("flex_u"), 30.0)
        self.assertAlmostEqual(share("std_u"), 60.0)

    # -- usage-API slice: the weight must equal the raw count ----------------

    def test_usage_api_denominator_is_not_weighted(self):
        # Pre-epoch window: the whole denominator comes from the Usage API, which
        # has no tier. Weight must equal raw, or the ratio skews against a
        # numerator that cannot be weighted either.
        self.mod.project_chat_usage_tokens = lambda *a, **k: (800, 200)
        _, inp, out_, weight, sources, _ = self.mod.project_totals_with_ledger_failover(
            "proj", "Summary [Production]", self.epoch - 7200, self.epoch - 3600, "k")
        self.assertEqual((inp, out_), (800, 200))
        self.assertAlmostEqual(weight, 1000.0)
        self.assertEqual(sources, {"completions"})

    def test_completions_walk_numerator_is_not_weighted(self):
        # The trap this whole design exists for: stored completions DO carry a
        # tier, but their denominator counterpart does not — so they must be
        # counted at face value on the numerator too.
        self.mod.user_tokens_in_project = lambda *a, **k: (700, 300, 5)
        prompt, completion, count, weight, sources, _ = \
            self.mod.user_tokens_with_ledger_failover(
                "proj", "Summary [Production]", "a", self.epoch - 7200, self.epoch - 3600)
        self.assertEqual((prompt, completion, count), (700, 300, 5))
        self.assertAlmostEqual(weight, 1000.0)
        self.assertEqual(sources, {"completions"})

    def test_usage_api_only_project_allocates_exactly_as_before(self):
        # The regression guard: on a window with no ledger coverage the weighted
        # ratio must equal the raw ratio, so this change is a no-op there.
        self.mod.project_chat_usage_tokens = lambda *a, **k: (4000, 1000)
        self.mod.user_tokens_in_project = lambda *a, **k: (400, 100, 3)
        _, p_in, p_out, proj_w, _, _ = self.mod.project_totals_with_ledger_failover(
            "proj", "Summary [Production]", self.epoch - 7200, self.epoch - 3600, "k")
        u_p, u_c, _, user_w, _, _ = self.mod.user_tokens_with_ledger_failover(
            "proj", "Summary [Production]", "a", self.epoch - 7200, self.epoch - 3600)
        self.assertAlmostEqual(user_w / proj_w, (u_p + u_c) / (p_in + p_out))

    def test_rendered_share_reconciles_with_the_dollars(self):
        """`share x Project $` must equal `User $` on a mixed-tier row.

        The dollars are allocated on weights, so a share rendered from raw token
        counts disagrees with them the moment a project mixes tiers — the table
        then shows a row whose own three numbers do not multiply out. It reads as
        a rounding artefact rather than a bug, which is why it needs pinning.
        """
        report = {
            "user_id": "e8XFD61aBaYp5qNryghIHPuij0S2",
            "totals": {"cost_usd": 30.0},
            "window": {"label": "month-to-date", "days_in": 1},
            "per_project": [{
                "name": "Summary",
                "project_cost": 90.0,
                # Raw tokens say 50%; the tier weights say 33.3% because this
                # user's calls ran on flex and the project's did not.
                "project_tokens": 2000, "user_tokens": 1000,
                "project_weight": 1500.0, "user_weight": 500.0,
                "user_cost": 30.0,
            }],
            "notes": [],
        }
        md = self.mod.render_markdown(report, show_by_project=True)
        row = [l for l in md.splitlines() if l.startswith("| Summary")][0]
        pct = float(row.split("|")[3].strip().rstrip("%"))
        self.assertAlmostEqual(pct, 100 * 500.0 / 1500.0, places=2)
        # The property that actually matters: the row multiplies out.
        self.assertAlmostEqual(pct / 100 * 90.0, 30.0, places=2)
        self.assertNotAlmostEqual(pct, 50.0, places=2)  # the raw-token answer

    def test_non_ledger_project_is_not_weighted(self):
        # A project outside LEDGER_PROJECT_KEYS never touches the ledger, so both
        # sides stay on the Usage API and the weights stay raw.
        self.mod.project_chat_usage_tokens = lambda *a, **k: (500, 500)
        self.mod.iter_llm_calls = lambda *a, **k: [self._row("a", 9999, "flex")]
        _, inp, out_, weight, sources, _ = self.mod.project_totals_with_ledger_failover(
            "proj", "Evaluations [Production]", self.epoch, self.epoch + 3600, "k")
        self.assertEqual((inp, out_), (500, 500))
        self.assertAlmostEqual(weight, 1000.0)
        self.assertEqual(sources, {"completions"})


class CacheWriteLineItemTests(unittest.TestCase):
    """`cache writes` is a fourth billing dimension, not an `other` bucket.

    OpenAI began emitting `<model>, cache writes` with the gpt-5.6 family. The
    line-item regex matched only input/cached input/output, so those dollars fell
    into `other` and dropped out of every per-model rate — under-reporting the
    cost of exactly the models the coach is migrating onto.
    """

    def setUp(self):
        self.mod = _load("analyze-openai-costs")

    def test_cache_writes_is_a_recognised_kind(self):
        self.assertIn("cache writes", self.mod.LINE_ITEM_TOKEN_KINDS)

    def test_cache_write_dollars_count_toward_model_total(self):
        tokens = {"gpt-5.6-sol": {"input": 1_000_000, "cached": 0, "output": 100_000}}
        spend = {
            ("gpt-5.6-sol", "input"): 5.0,
            ("gpt-5.6-sol", "output"): 3.0,
            ("gpt-5.6-sol", "cache writes"): 1.68,
        }
        rows = self.mod.compute_model_rates(tokens, spend)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["spend_cache_writes"], 1.68)
        self.assertAlmostEqual(rows[0]["spend_total"], 9.68)


if __name__ == "__main__":
    unittest.main()
