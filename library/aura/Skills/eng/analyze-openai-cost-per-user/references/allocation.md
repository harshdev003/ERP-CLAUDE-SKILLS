# Cost-allocation math + per-user nuances

This is the per-user variant of the allocation doc that ships with [`eng:analyze-openai-cost-per-subscription-state`](../../analyze-openai-cost-per-subscription-state/references/allocation.md). The core math is identical — proportional allocation of Costs-API dollars by token weight, **no model-pricing table** — collapsed to the user dimension instead of the subscription-state dimension.

The two skills make different *trade-offs* on top of the same allocation, so this doc captures the per-user-specific ones.

## The shared core

For each project P with non-zero spend in the window:

```
total_$_P  = CostsAPI(project_id=P, window)        # source of truth
weight_u   = sum of (prompt_tokens + completion_tokens) per user u in P
total_w_P  = sum of weight_u across all users in P

For each user u in P:
    user_$_P[u] = total_$_P × weight_u / total_w_P
    user_$_total[u] += user_$_P[u]
```

The token weight is a proxy for relative cost within a single project. As long as the model mix doesn't differ wildly between users in that project — which is the case today, since every user goes through the same prompter chain — the proportions are accurate.

When a project has spend but zero completions in the window (the project's completions were swept by OpenAI's ~30-day retention, or the spend pre-dates the metadata-tagging rollout), the project's entire $ falls into `untagged_amount`. The skill reports `untagged_amount` separately in the header so this isn't silently absorbed into the top users.

## Per-user-specific choices

### Tier resolution: last-seen

Each completion carries the user's tier *at the time of the call*. A user can have multiple tiers over a window (e.g., started trial, upgraded to paid, hit a payment-failure dunning state). The report needs one label per user.

**The skill takes the most recent completion's tier.** Both dimensions independently — `ai_tier` is from the latest completion that has `aiPremium` / `aiPremiumState` set; `content_tier` is from the latest with `premium` / `premiumState`. The latest could differ between dimensions if the user toggled one but not the other.

Why last-seen and not modal or first-seen:

- **Unit economics is about now, not history.** "How much did this Paid user cost us?" is the question; "their last call shows Paid" is the right answer. If they were on Trial for the first half of the window, those dollars are still counted (the allocation step doesn't care about tier — it only cares about token weight per user) but they're attributed to "Paid" in the row label, which matches how the user looks today.
- **Modal would mask upgrades.** A trial-to-paid conversion mid-window would still show as "Trial" if most of the calls happened pre-upgrade. That's misleading for renewal modelling.
- **First-seen would mask downgrades.** A paid-to-payment-pending user would still show as "Paid" — actively misleading for dunning-risk analysis.

The trade-off: a user who upgrades on the last day of the window flips entirely to the new tier, even though most of their cost was incurred at the old tier. This is fine for monthly reports (most users don't change state mid-month) and is documented here so anyone reading a confusing edge case can find the rule.

### Top project per user

For each user, the report shows their *single largest project* by allocated dollars. Useful as a routing hint: "this user lives in Conversation, so check their session length / generation count" or "all this user's spend is in Memory, so check their memory write rate."

When a single project accounts for ≥95% of a user's cost, the headline-outliers section calls that out explicitly ("`<short_uid>` spends 95% of their cost in **<project>** alone — single-flow user, easy to investigate"). We cap that callout at one per report — if 5 of the top 5 are single-flow, listing them all is noise; one prompt to investigate is enough.

### Untagged users are not in the distribution

`metadata.user` is set by Aura's instrumented prompters. When it's not set (pre-account / system / batch / a regressed code path), the completion gets the sentinel UID `"(untagged)"` during aggregation. The dollar share of those completions accumulates into a separate `untagged_amount` and **does not enter the per-user distribution**.

This is deliberate. If we included untagged spend as a synthetic "user" with a $X000 amount, that single fake row would skew every percentile and the top user would always be `(untagged)`. The header line `Untagged spend: $X (Y% of org)` carries that information cleanly without contaminating the distribution.

### Percentile method: inclusive, not exclusive

Python's `statistics.quantiles` supports two methods:

- `exclusive` (the default): treats the data as a sample from a larger population. Percentiles can extrapolate beyond the observed range (p99 may exceed max).
- `inclusive`: treats the data as the entire population. p100 = max, p0 = min; percentiles are linear interpolation between order statistics.

We use `inclusive` because **we have the entire population** of users in the window — we're not sampling. This also avoids the rendering artifact of "p95 > max" on small populations, which would look like a bug to anyone reading the report.

### Stddev as a calibration aid, not a confidence interval

The distribution is heavily right-skewed (typical for SaaS — a few users 100×+ the median). Stddev on a right-skewed distribution doesn't behave the way intuition wants: the value is dominated by the top users, and you can't apply normal-distribution rules of thumb ("68% within 1σ", etc.).

The skill reports stddev anyway because **it's a useful "is this report wider than usual" cadence signal** — week-over-week stddev moves tell you if cost concentration is shifting, even if you can't read a confidence interval off it. The headline copy never says "1σ" or "2σ" because that's misleading on a skewed distribution; it says "Nx the median" instead, which is robust to skew.

## Caveats to flag in the readout

- **Single-tier project bias.** If a project's traffic is overwhelmingly one tier (e.g., 99% Premium Paid), the per-user numbers within that project are uninteresting but correct. Don't read into "user X in tier Y is the top spender in project Z" if Z is mono-tier — they're the top spender because there's nobody else.
- **Top-user precision.** We allocate the project's $ across users proportional to their token share, not their actual per-call cost. The ranking is robust (a user who burns 10× the tokens of #2 still ranks #1) but the absolute dollar numbers per user are off by the same model-mix factor as in `eng:analyze-openai-cost-per-subscription-state`.
- **Premium Free framing.** A `Premium Free` user is a user who has Premium *access* without paying — typically via redemption codes, gifts, comps, or partnerships. They cost like Premium Paid (same prompters, same model routing) but contribute zero revenue. A Premium Free user in the top 5 is a unit-economics signal — not noise, not a leak, but worth a conscious decision ("is this partnership worth $40/mo of OpenAI spend per user?").

## When to refine

Switch to model-aware allocation (v2) when:

1. A project starts routing different users to different models — at which point per-user dollars in that project get biased by the model-mix factor.
2. A consumer wants exact per-model spend per user — e.g., for routing-cost optimisation experiments. The empirical per-model rates landed by [ENG-501](https://linear.app/aura-health/issue/ENG-501) in `eng:analyze-openai-costs --by-model` give us the right ingredients for this; v2 of the per-user skill could multiply per-completion token counts by per-model rates from that source.

Add Gini / Lorenz when chart output is on the table — markdown alone can't carry the visual that makes concentration intuitive. Until then, "top user is ~Nx the median" + the percentile table cover most of the signal.

## Validation

The report's `tagged_amount + untagged_amount` should equal (within rounding) the org-wide total from `eng:analyze-openai-costs` for the same window. Rounding error is bounded by the number of users × $0.01.

For a tier-filtered run (`--tier "Premium Free"`), `tagged_amount` for that tier should approximately equal the corresponding state's `amount` from `eng:analyze-openai-cost-per-subscription-state` (both cuts derive from the same allocation; small rounding differences are expected).
