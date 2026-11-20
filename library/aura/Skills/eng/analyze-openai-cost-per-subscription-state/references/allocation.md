# Cost-allocation math

Why the skill allocates dollars proportionally to tokens instead of multiplying by a per-model price table — and what that costs us in accuracy.

## The problem

OpenAI gives us two adjacent data sources but neither tells us "$ per subscription tier" directly:

1. **`/v1/organization/costs`** — authoritative dollar totals, grouped by project. No tier dimension; the Costs API has no `group_by[]=metadata.aiPremiumState`.
2. **`/v1/chat/completions`** (per-project, with `OpenAI-Project` header) — stored completions with the full `metadata` blob, including `aiPremiumState` / `premiumState` and `usage.{prompt,completion}_tokens`. No dollar amount per completion.

The naive bridge is a `model → $/1K tokens` lookup table. That works exactly until the next time OpenAI ships a new model or repricing — at which point the report silently drifts. Maintaining the table is a forever-tax we don't want to pay.

## What the skill does instead

For each project P with non-zero spend in the window:

```
total_$_P  = CostsAPI(project_id=P, window)         # source of truth
tokens_t   = sum of (prompt_tokens + completion_tokens) per tier t
                from stored completions in P
total_w_P  = sum of tokens_t across all tiers (including "(untagged)")

For each tier t:
    tier_$_P = total_$_P × tokens_t / total_w_P
    tier_$_overall[t] += tier_$_P
```

The token weight is a proxy for relative cost *within* a single project. As long as the model mix doesn't differ wildly between tiers in that project, the proportions are accurate.

We then do the same for the content-premium dimension on a second pass — independent allocation off the same per-project token weights, with each completion mapped to its `(premium, premiumState)` composite tier instead of the AI variant.

## Why this is good enough

Inside a single project — say `Conversation` — every completion goes through the same prompter chain, so the model mix is roughly identical across tiers. A Premium Paid user and a Premium Free user in the Conversation project both hit `gpt-4.1-mini` for routing, `gpt-5.2` for the main reply, and so on, in the same proportions. The token weight cleanly stands in for the dollar weight.

The approximation breaks down only when a project routes different tiers to different models. We don't know of a project that does this today; if one ever does, the per-tier numbers in that project become biased, but the org-level totals are still correct (they're anchored to the Costs API).

## What the five tiers actually mean

The labels in the report aren't all about subscription state — two of them are revenue-versus-cost signals:

- **Premium Paid** — paying for Premium right now. Revenue ≈ cost.
- **Premium Trial** — in a free trial of Premium. Costs like a Paid user; revenue zero *now* but likely to convert. Trial-cost is a known investment.
- **Premium Free** — has Premium access but is not paying. Granted via redemption codes, gifts, comps, partnerships, etc. Costs like a Paid user, revenue zero, **no conversion path baked in**. This is the most interesting tier from a unit-economics standpoint — if it's growing faster than Paid, we're subsidising more comp users than the funnel justifies.
- **Premium Payment Pending** — payment failed, in retry / dunning window. Costs like a Paid user; revenue may or may not recover. Worth tracking separately from Paid so dunning failures don't silently inflate "Paid" cost.
- **Non Premium** — never had Premium. Costs are limited to the free tier's feature surface, which tends to be a lower per-user model mix.

Two corollaries for anyone interpreting the rendered report:

1. **"Paid + Free" is not the right number to compare against revenue.** Use "Paid" alone for revenue-anchored unit economics; treat Trial / Free / Payment Pending as separate cost cohorts each with their own narrative.
2. **A large Premium Free row is a story, not noise.** If we comped 200 partnership users and they're now running $400/mo of OpenAI traffic with no revenue counter-balance, that's a signal worth raising in the headline — not a footnote.

## Caveats to flag in the readout

- **Single-tier project bias.** If a project's traffic is overwhelmingly one tier (e.g. 99% Premium Paid), the allocation is uninteresting but correct. Don't draw conclusions about minority-tier costs in such a project — the absolute $ are dominated by sampling noise.
- **`(untagged)` is a real cohort.** Pre-account onboarding flows, batch evaluations, internal playground hits — these legitimately omit the metadata. ENG-498's audit puts them at ~5% of org spend. They land in the `(untagged)` row in both cuts, with no users counted. **Untagged is not the same as missing — it's the pre-account / system slice.**
- **Coverage warning at >5%.** If an *instrumented project* (one with `user` set) goes above 5% untagged, the skill flags it as a probable regression in ENG-498 / ENG-500. The threshold is a heuristic; calibrate downward if false positives are noisy.
- **Top-user precision.** The "top user in tier X" ranking is approximate for the same reason — we allocate the project's $ across users proportional to their token share, not their actual per-call cost. The ranking is robust (a user who burns 10× the tokens of #2 still ranks #1) but the absolute dollar numbers per user are off by the same model-mix factor.

## When to switch to model-aware allocation (v2)

We should refine to a token × model-mix allocation when **any** of these become true:

1. A project starts routing tiers to different models (e.g. Premium Paid → GPT-5.2, others → GPT-4.1-mini). At that point per-tier dollars in that project will be off by 30-50%.
2. The cost gap between adjacent models in active use exceeds ~5× on either input or output tokens. Today's prod mix is mostly within ~2× across the GPT-5.x family.
3. A consumer wants to attribute *exactly* per-model spend per tier — e.g. for unit-economics work or for routing-cost optimisation experiments.

v2 sketch: replace the single per-project token weight with a `(model, tier)` weight, then look up `$/token` per model from an authoritative source. The authoritative source can be the Costs API itself with a finer `group_by` — `group_by[]=project_id,line_item` may expose the model-level $ split, in which case we'd avoid the pricing table again. Worth probing before adding one.

## Validation

The skill's output should sum (within rounding) to ≤100% of the project totals from `eng:analyze-openai-costs`. Discrepancies are limited to:

- Rounding (~$0.01 per tier per project)
- The `(untagged)` row absorbing the dollars from projects with zero stored completions in the window — those projects contribute their full $ to `(untagged)` in both cuts, which is the intended behaviour. The skill's headline reports `tagged` vs `untagged` separately so this is transparent.
