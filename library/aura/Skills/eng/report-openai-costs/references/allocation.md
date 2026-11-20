# Cost-allocation math

Why this skill allocates dollars proportionally to tokens instead of multiplying by a per-model price table — and what that costs us in accuracy.

This is the same allocation pattern documented in the sibling skills (`eng:analyze-openai-cost-per-subscription-state` / `eng:analyze-openai-cost-per-user`). Copied here rather than cross-referenced because AINative architecture principle 1 (skills are self-contained) prohibits reaching into sibling skill directories at runtime.

The canonical, authoritative write-up of *why* this is the only accurate method (and of the trap that catches anyone trying to back-calculate from `tokens × per-model rate`) lives in AI Knowledge at [`Tools/OpenAI` → Cost computation methodology](https://www.notion.so/34c75e23af20812cbac8e0fcb0eefe1e). That page is the source of truth — update it first if the method evolves; this file mirrors.

## What's in this report — voice + text combined per project

Production OpenAI spend in the `Conversation` project (and most other prod projects) reflects **both transport modes** of Aurie usage:

- **Text mode** — Mobile → IntelligenceService (`POST /ai/dialogue/ask`) → OpenAI.
- **Voice mode** — Mobile → ElevenLabs Conversational AI → IntelligenceService (`POST /ai/openai/chat-completion`, ELabs treats us as an LLM provider) → OpenAI.

Both modes fire the same `coach-prompter` chat completion per user turn, plus the same per-turn and end-of-conversation side effects (memory every 10 turns, profile/summary/etc. at conversation end). They're indistinguishable in `metadata.project` / `metadata.prompterName` — both log as `Conversation` / `coach-prompter`. Full architecture in [`Tools/ElevenLabs` → Request flow](https://www.notion.so/34c75e23af20819abea7d64a826b72f3).

**Implication for reading this report**: a project total like "Conversation = $212/day" is the combined voice + text spend. To split voice from text, look at the IntelligenceService route or join against ElevenLabs ConvAI conversations on `metadata.conversationId`. The companion skill [`eng:report-top-conversation-users`](../../report-top-conversation-users/SKILL.md) handles this join automatically when ranking users by combined cost.

## The problem

OpenAI gives us two adjacent data sources but neither tells us "$ per prompter / user / tier" directly:

1. **`/v1/organization/costs`** — authoritative dollar totals, grouped by project. No dimension for prompter, Firebase UID, or premium tier; the Costs API has no `group_by[]=metadata.*`.
2. **`/v1/chat/completions`** (per-project, with `OpenAI-Project` header) — stored completions with the full `metadata` blob (`name`, `user`, `aiPremium*`, `premium*`) and `usage.{prompt,completion}_tokens`. No dollar amount per completion.

The naive bridge is a `model → $/1K tokens` lookup table. That works exactly until the next time OpenAI ships a new model or repricing — at which point the report silently drifts. Maintaining the table is a forever-tax we don't want to pay.

**Worse**, the chat-completions list endpoint omits `prompt_tokens_details.cached_tokens` in its `usage` object. Cached input bills at ~10x cheaper than fresh input on the gpt-5.x family, and most Aura traffic has 70–90% cache hit rate. So naive `prompt_tokens × per-model fresh rate` overstates real cost by 2–3x. Verified empirically on 2026-05-16 UTC: a back-calc came out at $255 vs OpenAI's authoritative $106 for the same window (2.4x over).

## What this skill does instead

For each project P with non-zero spend in the window:

```
total_$_P  = CostsAPI(project_id=P, window)         # source of truth
tokens_d   = sum of (prompt_tokens + completion_tokens) per dimension value d
                from stored completions in P
total_w_P  = sum of tokens_d across all dimension values

For each (prompter, uid, tier) value d:
    d_$_P = total_$_P × tokens_d / total_w_P
    d_$_overall[d] += d_$_P
```

Three dimensions are computed in a single pass: prompter, Firebase UID, AI premium tier. The token weight is a proxy for relative cost *within* a single project. As long as the model mix doesn't differ wildly between dimension values in that project, the proportions are accurate. The grand totals always reconcile to the Costs API authoritative number for the project.

## Why this is good enough

Inside a single project — say `Conversation` — every completion goes through the same prompter chain, so the model mix is roughly identical across users and tiers. A Premium Paid user and a Premium Free user in the Conversation project both hit `gpt-4.1-mini` for routing, `gpt-5.2` for the main reply, and so on, in similar proportions. The token weight cleanly stands in for the dollar weight.

The approximation breaks down only when a project routes different dimension values to different models. We don't know of a project that does this today; if one ever does, the per-(prompter, uid, tier) numbers in that project become biased, but the org-level totals are still correct (they're anchored to the Costs API).

## Caveats

- **Single-prompter project bias.** If a project's traffic is overwhelmingly one prompter (e.g. Conversation is ~97% `coach-prompter`), the per-prompter allocation for that project is uninteresting but correct. Don't draw model-mix conclusions about minority prompters in such a project — the absolute $ are dominated by sampling noise.

- **`(untagged)` is a real cohort.** Pre-account onboarding flows, batch evaluations, internal playground hits — these legitimately omit the metadata. Today these are ~5% of org spend. They land in the `(untagged)` row with no users counted. **Untagged is not the same as missing** — it's the pre-account / system slice.

- **`Never AI Premium`** (AI dimension only) **is distinct from `(untagged)`.** When a completion has `metadata.user` but no `aiPremium*` fields, that's a user whose subscription column is NULL in the DB — they've never had AI premium. We bucket them as `Never AI Premium` rather than `(untagged)`. See `Tools/OpenAI` → Three-state semantic on the booleans for the full context. ENGBACK-1083 tracks the schema fix that will eventually disambiguate the content-side `premium` column the same way.

- **Top-user precision.** The "top user" ranking is approximate for the same reason — we allocate the project's $ across users proportional to their token share, not their actual per-call cost. The ranking is robust (a user who burns 10× the tokens of #2 still ranks #1) but the absolute dollar numbers per user are off by the same model-mix factor.

- **Project with zero stored completions but non-zero $.** Happens when `store=false` is set on the OpenAI call (e.g., certain low-volume back-office prompters). The skill parks the $ under a `(no-completions) <ProjectName>` sentinel row in the per-prompter table so the dollar total still reconciles. The by-tier mini for that project falls entirely into `(untagged)` since there are no completions to allocate from.

## When to switch to model-aware allocation (v2)

We should refine to a token × model-mix allocation when **any** of these become true:

1. A project starts routing prompters to different models (e.g. `coach-prompter` → gpt-5.2, `summary-prompter` → gpt-4.1-mini). At that point per-prompter dollars in that project will be off by 30–50%.
2. The cost gap between adjacent models in active use exceeds ~5× on either input or output tokens. Today's prod mix is mostly within ~2× across the GPT-5.x family.
3. A consumer wants to attribute *exactly* per-model spend per dimension — e.g. for unit-economics work or routing-cost optimisation experiments.

**v2 sketch.** Replace the single per-project token weight with a `(model, dimension)` weight. The authoritative source for the model-level $ split is `/v1/organization/usage/completions` (returns `input_tokens`, `input_cached_tokens`, `input_uncached_tokens`, `output_tokens` per model with `group_by` support). Combined with Aura's empirical per-model rates from `Tools/OpenAI` → Per-model rates, this avoids ever maintaining a public-price-list table.

## Validation

The skill's output should sum (within rounding) to the project totals from `eng:analyze-openai-costs` for the same window. Discrepancies are limited to:

- Rounding (~$0.01 per row per project)
- The `(no-completions) <Project>` sentinel absorbing dollars from projects with zero stored completions
- Sub-day windowing differences — Costs API only does daily UTC, so a custom range that doesn't align to UTC midnights will not reconcile exactly

If totals diverge by more than 1%, something's wrong — re-fetch and inspect.
