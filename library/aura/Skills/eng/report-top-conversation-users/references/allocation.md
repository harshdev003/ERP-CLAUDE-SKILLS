# Allocation methodology — combined OpenAI + ElevenLabs top-user ranking

Why this skill uses **proportional per-bucket allocation** on the OpenAI side and **per-call duration × rate** on the ElevenLabs side — and how the two reconcile inside a single Pacific-aligned window.

This is the same allocation pattern documented in the two sibling cost-snapshot skills (`eng:report-openai-costs/references/allocation.md` and `eng:report-elevenlabs-costs/references/methodology.md`). Copied here rather than cross-referenced because AINative architecture principle 1 (skills are self-contained) prohibits reaching into sibling skill directories at runtime.

The canonical, authoritative write-up of the OpenAI proportional-allocation trap (why naive `tokens × per-model rate` overstates by 2–3×) lives at [`Tools/OpenAI` → Cost computation methodology](https://www.notion.so/34c75e23af20812cbac8e0fcb0eefe1e). The ElevenLabs per-minute rates and the **voice-vs-text request flow** live at [`Tools/ElevenLabs`](https://www.notion.so/34c75e23af20819abea7d64a826b72f3). Update those pages first if the methodology evolves; this file mirrors.

## Request flow — why combining the two providers per-user is meaningful

Aurie has two equally-supported user-facing modes that share the same backend:

- **Voice mode**: Mobile → ElevenLabs Conversational AI → IntelligenceService (`POST /ai/openai/chat-completion`, treating us as an LLM provider) → OpenAI (`coach-prompter`, `gpt-5.2-chat-latest`).
- **Text mode**: Mobile → IntelligenceService (`POST /ai/dialogue/ask`) → OpenAI (same `coach-prompter`, same model).

**Both paths fire identical OpenAI calls and identical side effects** — memory pipeline every 10 user turns, end-of-conversation pipeline (profile, summary, memory, days, streak) 2 minutes after the last turn. The only difference is the transport. Full architecture in [`Tools/ElevenLabs` → Request flow](https://www.notion.so/34c75e23af20819abea7d64a826b72f3).

**The implication for this skill's combined ranking:**

1. Every ElevenLabs voice minute *also* generates OpenAI spend (~$0.04/min for `coach-prompter` + side effects, verified 2026-05-17). A user with 14 hours of voice will appear both on the OpenAI side (via the per-voice-turn `chat-completion` calls) AND on the ElevenLabs side (via per-call `duration_secs × rate`).
2. The combined per-user total isn't double-counting — it's the **total marginal cost** for that user across both providers. Voice cost per user is well-approximated as `ELabs_$0.09/min × min + OpenAI ~$0.04/min × min ≈ $0.13/voice-min`.
3. **OpenAI-only users** (text-only chatters) genuinely cost zero on the ElevenLabs side. **ElevenLabs-only users** would imply a voice user whose `coach-prompter` calls don't appear in OpenAI — that shouldn't happen since voice routes through OpenAI; if it does, it's a window-alignment artifact (see Option A below).

This is what makes the "who burned the most across both providers" question meaningful — it captures the full marginal cost per user without making the reader add two reports together by UID.

## The problem this skill solves

The two cost-snapshot siblings (`report-openai-costs`, `report-elevenlabs-costs`) each tell you what *one provider* cost yesterday and who its top spenders were. Neither can answer: **who burned the most across both providers combined?**

Runaway users frequently dominate one provider (say, a Trial user voice-chatting for two hours) and look ordinary on the other (the same user only sent text in the morning). Until you sum the two providers, the worst offenders are invisible.

Three things make this non-trivial:

1. **OpenAI Costs API is daily UTC.** `/v1/organization/costs` gives authoritative $ per project per UTC calendar day. There is no narrower bucket; the API rejects sub-day windows.
2. **ElevenLabs ConvAI is per-conversation, PT-natural.** Each call has its own `created_at_unix` and `call_duration_secs`. The sibling skill (`eng:analyze-elevenlabs-cost-per-user`) walks Pacific yesterday natively.
3. **Window misalignment creates phantom voice-only users.** If we pick "yesterday UTC" for both providers, evening-PT OpenAI traffic (which falls in today UTC, not yesterday UTC) is missed — a user who chatted at 8pm PT yesterday looks ElevenLabs-only. Verified on 2026-05-17: 2 of 68 "ElevenLabs-only" users were normal coach-prompter chatters whose evening PT traffic lived in today-UTC, not yesterday-UTC. Pure seam artifact.

The fix — and the methodology this skill implements — is **Option A: PT-aligned with multi-UTC-bucket allocation on the OpenAI side**.

## OpenAI side: per-bucket proportional allocation

For yesterday-PT (00:00–24:00 PT), the window spans two UTC buckets:

```
yesterday_PT_start_utc    today_utc_midnight    today_PT_start_utc
       │                          │                       │
       └────── Span 1 (17h) ──────┴────── Span 2 (7h) ────┘
                                  ▲
       (yesterday's UTC bucket    today's UTC bucket)
        from Costs API)            from Costs API)
```

For each UTC bucket B in `[yesterday_utc, today_utc]`:

```
total_$_B[pid] = CostsAPI(B, group_by=project_id)            # authoritative

# Walk the FULL UTC bucket — we need full_day_tokens as the allocation
# denominator so that we only attribute the PT-share of the bucket's $ to
# PT-window users, not 100% of it.
For each completion in B (full UTC day):
    proj_full_day_tokens[pid] += tokens
    if completion falls in PT window:
        proj_user_pt_tokens[pid][uid] += tokens
        proj_user_pt_calls[pid][uid]  += 1

For each (pid, uid):
    user_$_from_B[uid] += total_$_B[pid] × proj_user_pt_tokens[pid][uid] / proj_full_day_tokens[pid]

user_$[uid] = Σ_B user_$_from_B[uid]
```

The allocation key is **PT-window token weight divided by FULL UTC day tokens**. Summed across PT-window users for one (bucket, project), this gives `total_$_B[pid] × pt_share` where `pt_share = sum(pt_tokens) / full_day_tokens` — i.e., the **PT-attributable slice** of that bucket × project's $, not the whole bucket. The grand total across (bucket, project) gives the PT-aligned authoritative spend.

### Why divide by full-day tokens, not PT-span tokens?

If you divide by `Σ pt_tokens` (the PT-span tokens only), the sum across PT-window users equals `total_$_B[pid]` — you've attributed 100% of the UTC day's $ to the users active in the PT slice. That overstates by `1 / pt_share` per bucket:

- For yesterday-UTC bucket (17h slice in PT window, 24h total): overstates by ~1.4×
- For today-UTC bucket (7h slice in PT window, 24h total): overstates by ~3.5×
- Blended across both buckets for a typical yesterday-PT report: **~2× overall**

**This was the ENGBACK-1087 bug fix.** Verified 2026-05-17 against live API for yesterday-PT 2026-05-16:

| | Buggy (`/ pt_tokens`) | Corrected (`/ full_day_tokens`) |
|---|---:|---:|
| OpenAI yesterday PT | $319.33 | **$161.83** |

Bucket-level breakdown (corrected):
- 5/16 UTC bucket: $161.54 full day × ~73% PT share = $118.26 attributable
- 5/17 UTC bucket: $158.06 full day × ~28% PT share = $43.55 attributable

The fix walks the full UTC bucket per project (~2× walltime on the OpenAI side) so we have `full_day_tokens` available as the denominator. Correctness > speed.

### Why this is accurate-by-construction

The Costs API number for `(project, UTC day)` is the **authoritative billable $**. We never invent dollars — we only redistribute the **PT-share** of each bucket's $ across the PT-window users, weighted by their token contribution. The total attributed across the whole window reconciles to `Σ_B Σ_pid (total_$_B[pid] × pt_share_B_pid)` — the true PT-aligned slice of authoritative spend.

**Actuals only.** No projected averages, no `+X% vs 7d avg` comparisons, no anomaly flags computed against a stitched-together baseline. Same posture as `eng:analyze-elevenlabs-costs` after its 2026-05 actuals-only rework. If you want trend analysis, run the report against multiple windows and compare those actuals directly — don't derive one.

Compared to the naive alternative (`tokens × per-model rate` back-calc):

- ❌ Naive: drifts whenever OpenAI ships a new model or repricing. Forever-tax on the rate table.
- ❌ Naive: the `/v1/chat/completions` list endpoint **omits `prompt_tokens_details.cached_tokens`**, and Aura traffic has 70–90% cache hit rate. Cached input bills ~10× cheaper than fresh input on the gpt-5.x family — so naive back-calc overstates cost by 2–3×. Empirically validated 2026-05-16 UTC: a back-calc came out at $255 vs OpenAI's authoritative $106 for the same window (2.4× over).
- ✅ Proportional: never sees a public price list. Robust to repricing, model changes, cache-hit variation, and the cached-tokens omission. Org totals always reconcile.

### Why allocating per-bucket separately matters

It's tempting to merge the two buckets first (`total_tokens_overall = bucket1_tokens + bucket2_tokens`), then do one allocation against `bucket1_$ + bucket2_$`. **This is wrong.** Bucket-internal cache hit rates and model mix vary day-to-day — yesterday's bucket may have been 80% cached gpt-5.2 inference while today's bucket so far is 60% cached. Cross-bucket merging blurs the two distributions and biases allocation toward the bucket with cheaper tokens.

Per-bucket allocation isolates each day's local cost density to its own bucket's $, then sums the user's slices. The implementation in `scripts/report.py` reflects this — `aggregate_openai_users` iterates `for bucket_start, span_start, span_end in spans` and does an independent allocation per bucket.

### The "today UTC bucket may not have propagated" caveat

OpenAI's Costs API typically lags 1–4 hours on the most recent bucket. If the script runs at 3am PT (10am UTC), today's UTC bucket may have zero $ even though traffic has been flowing for 7 hours. When `bucket_total_$ == 0` for `bucket_start == today_utc`, the script:

1. Skips allocation for that bucket (no $ to redistribute)
2. Sets `today_bucket_propagated_warning = True`
3. Surfaces a banner in the rendered header: *"Today's OpenAI Costs API bucket has not propagated yet — evening PT usage may be undercounted on the OpenAI side. Re-run after ~10am UTC for clean numbers."*

The report is still usable — yesterday's bucket (Span 1, 17 hours) is the bulk of yesterday-PT traffic. The 7h tail being undercounted shifts the rankings only marginally for users whose volume is heavily evening-PT-shifted.

### What about last-7d and custom windows?

For `--window last-7d` the multi-UTC-bucket dance is unnecessary — both providers operate on the same rolling 7-day window, and at that granularity the seam errors average out. The script still iterates `utc_bucket_spans` (which generates 7 daily spans), but each bucket is fully inside the window so there's no PT-vs-UTC misalignment to correct for.

For `--since/--until` custom PT ranges, the same per-bucket allocation logic applies — `utc_bucket_spans` splits the window into however many UTC buckets it touches and the script allocates each independently.

## ElevenLabs side: per-call duration × rate

ElevenLabs ConvAI has authoritative per-call data, so no proportional allocation is needed:

- **`call_duration_secs`** — from `/v1/convai/conversations/{id}` metadata
- **`metadata.charging.is_burst`** — actual flag for whether the call billed at burst rate (over plan quota)
- **Per-minute rates** — from `Tools/ElevenLabs` ([Business plan](https://www.notion.so/34c75e23af20815eb88dffd1bbd5757b)):
  - **$0.09 / min in-plan**
  - **$0.19 / min burst**

```
For each conversation C in PT window:
    detail = /v1/convai/conversations/{C.id}
    uid    = detail.conversation_initiation_client_data.dynamic_variables.userId
    dur    = detail.metadata.call_duration_secs
    burst  = detail.metadata.charging.is_burst
    rate   = $0.19/min if burst else $0.09/min
    user[uid].minutes += dur / 60
    user[uid].cost    += (dur / 60) × rate
```

This matches `eng:analyze-elevenlabs-cost-per-user`'s methodology after [its 2026-05 actuals-only refactor](https://github.com/AuraHealth/AINative). Both inputs are API actuals — no credits → minutes derivation, no cost projections.

### Retention caveat

ConvAI conversation detail has **10-day retention** in the Aura workspace's ElevenLabs Privacy settings. After 10 days the detail endpoint deletes the transcript + dynamic variables, returning 404. Implications:

- Default window (yesterday PT) is always inside retention — top-N works.
- `--window last-7d` is fully inside retention.
- Custom windows that extend past 10 days will show top-N **understating** the back end of the window; the script prints a stderr warning when this happens.

### Per-user $ ≠ workspace plan + overage

The per-user ElevenLabs $ figures don't sum to the workspace bill — they're a per-call cost basis, not a slice of the plan/overage. ElevenLabs bills as **flat plan + overage** ($500/mo Business plan + `current_overage.amount` if over quota), so even on a busy day a user's per-call $ adds to "what this user cost us at marginal voice rates," not "what we paid ElevenLabs that day."

For ranking purposes that's fine — we want **marginal cost per user** to identify runaway spenders. The combined Total $ column is `OpenAI marginal $ (proportional) + ElevenLabs marginal $ (per-call)` and serves the runaway-user-investigation use case.

If you need "what did we actually pay this month?" use `eng:report-openai-costs` (authoritative OAI org totals) + `eng:report-elevenlabs-costs` (plan + live overage from `/v1/user/subscription`).

## Reconciling the two providers

A few notes on what the combined Total $ means and doesn't mean:

1. **Different cost bases.** OpenAI side is "your share of the project's UTC-bucket bill, weighted by tokens." ElevenLabs side is "duration × marginal per-minute rate." They're commensurable in dollar terms but represent different billing concepts. Don't read the ratio as "user X is more of an OpenAI cost than an ElevenLabs cost" without that caveat.

2. **Window alignment is exact (Option A).** Both providers respect the same PT range. A user whose voice call started at 23:45 PT yesterday but ran 20 minutes into today PT is split — the part inside yesterday PT contributes to the yesterday-PT report; the part inside today PT (if the call straddled midnight) goes to the today-PT report. (ElevenLabs only reports `created_at_unix` per conversation; for cross-midnight calls we attribute the whole call to the bucket the call started in. This is a small bias for boundary-straddling calls only.)

3. **Tagging gap.** The OpenAI metadata-tagging rollout (`metadata.aiPremium`, `metadata.user`) went live 2026-05-12. Calls before that date have no `metadata.user`, so they contribute to the per-project authoritative $ totals but not to any user's share — they sit in the unattributed remainder of the project's bucket allocation. The script surfaces a banner when `pt_start < 2026-05-12` for windows that straddle the rollout. Don't compare rankings across the rollout boundary.

4. **Overlap stats are exact within the window.** "Both providers" / "OpenAI-only" / "ElevenLabs-only" counts come from `set(oai_users) ∩ set(elabs_users)` etc. With Option A's PT alignment, **ElevenLabs-only should be ~0 for a normal day** — voice users almost always also send text (because the app opens with a chat surface, not a phone surface). When it's >0, the script flags it:

   - `elabs_only > 0` ⚠️ — check for: (a) today UTC bucket not propagated (header banner above), (b) OpenAI tagging gap (window straddles 2026-05-12), or (c) prompter-coverage gap (a new prompter chain not in `PROD_PROJECTS`).

   "OpenAI-only" >> "ElevenLabs-only" is normal — most users only chat in text and never start a voice call. That's the "text chatters" cohort.

## Validation

The skill's outputs should reconcile:

- **OpenAI grand total** ≈ `eng:report-openai-costs` for the same PT window (within rounding; both use per-bucket Costs API + proportional allocation).
- **ElevenLabs grand total** ≈ `eng:report-elevenlabs-costs` per-user sum for the same PT window (both use per-call duration × rate).
- **Top-N ranking by UID** ≈ the merge of `eng:analyze-openai-cost-per-user --top N` + `eng:analyze-elevenlabs-cost-per-user --top N` summed by UID, with Option A capturing the additional UIDs whose evening PT activity falls in today-UTC.

If any of these diverge by more than 1%, something's wrong — re-fetch and inspect.

## When to switch to model-aware allocation (v2)

We should refine to a token × model-mix allocation when **any** of these become true:

1. A project starts routing prompters to different models (e.g. `coach-prompter` → gpt-5.2, `summary-prompter` → gpt-4.1-mini). At that point per-user dollars in that project will be off by 30–50%.
2. The cost gap between adjacent models in active use exceeds ~5× on either input or output tokens. Today's prod mix is mostly within ~2× across the GPT-5.x family.
3. A consumer wants to attribute *exactly* per-model spend per user — e.g. for unit-economics work or routing-cost optimisation experiments.

**v2 sketch.** Replace the per-bucket token weight with a `(model, uid)` weight. The authoritative source for the model-level $ split is `/v1/organization/usage/completions` (returns `input_tokens`, `input_cached_tokens`, `input_uncached_tokens`, `output_tokens` per model with `group_by` support). Combined with Aura's empirical per-model rates from `Tools/OpenAI` → Per-model rates, this avoids ever maintaining a public-price-list table. **Limitation**: the Usage API only catches calls that set the top-level `user=` param on the request, not `metadata.user`. Most Aura calls only set `metadata.user`, so per-user grouping via Usage API catches <10% of UIDs today. Until IntelligenceService is updated to also set top-level `user=`, the proportional allocation in this skill is the best we have.

## Calibration target

For `--window yesterday-pt` on 2026-05-16 PT data (verified 2026-05-17 against live API):

```
$384.91 combined
($161.83 OpenAI + $223.08 ElevenLabs)

OpenAI per-bucket reconciliation:
  5/16 UTC bucket: $161.54 full × ~73% PT share = $118.26
  5/17 UTC bucket: $158.06 full × ~28% PT share = $43.55
                                                  ------
                                                  $161.83

Overlap:
  66 users on both providers
  ~2,000 OpenAI-only (text chatters)
  0 ElevenLabs-only ✅
```

### Historical note — the two seam artifacts removed

This skill went through two iterations of seam-handling:

1. **Option B (initial design, never shipped)** — naive "yesterday UTC for both providers" merge produced phantom voice-only users when evening-PT OpenAI traffic fell in today-UTC instead of yesterday-UTC. Calibration showed 2 of 68 ElevenLabs users (~3%) were ElevenLabs-only as a window-misalignment artifact.

2. **Option A v1 (shipped in ENGBACK-1086)** — PT-aligned with multi-UTC-bucket allocation, eliminating the seam artifact. But the allocation formula divided by PT-span tokens instead of full-day tokens, attributing 100% of each UTC bucket's $ to PT-window users → ~2× overstatement.

3. **Option A v2 (ENGBACK-1087, current)** — same PT alignment, corrected formula: divide by full-day tokens so we only attribute the PT-share of each bucket. Verified by walking the full UTC days and computing the actual `pt_tokens / full_day_tokens` ratio per project. Pure actuals, no projected baselines.

If `elabs_only` is >0 after Option A v2, investigate — may indicate an OpenAI tagging gap (window straddles 2026-05-12 rollout) or a prompter chain not in `PROD_PROJECTS`. The script flags this with a ⚠️ in the Overlap section.
