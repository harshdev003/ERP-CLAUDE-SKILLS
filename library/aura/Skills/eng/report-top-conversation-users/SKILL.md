---
name: report-top-conversation-users
description: >
  Combined top-N user ranking across OpenAI (chat, coach, memory, feed, summary, journal,
  profile, notification, onboarding) AND ElevenLabs Conversational AI voice for a given
  window. Defaults to yesterday Pacific with a PT-aligned multi-bucket allocation on the
  OpenAI side so the two providers' windows match exactly, with no phantom voice-only users
  from evening PT traffic spilling into today. Output is one screen: headline, top 10 users
  (UID, AI premium state, OpenAI dollars and calls, ElevenLabs dollars and minutes, total,
  cumulative percent), overlap stats and provider mix. Privacy-aware: UIDs truncated in
  rendered output, full UIDs only in `--json`. Trigger on "top conversation users", "top
  spenders across providers", "combined openai elevenlabs top users", "who are our biggest
  users yesterday", "who is costing us the most", "runaway users", "cross-provider top users",
  or "who burned the most yesterday on aurie".

metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index                                                  # baseline routing aid (universal)
    - Tools/OpenAI                                                 # cost methodology + premium-state semantic
    - Tools/ElevenLabs                                                 # per-minute rates, retention, dynamic variables
    - Tools/Aura API/Subscriptions/List Subscriptions By User Id   # state annotation endpoint + timeline pattern
    - Tools/Slack                                                  # channel directory + Post format footer spec for #eng-budget post (--post-to-slack)
    - Compliance/Privacy                                           # UID truncation rule for rendered output
---

# Report Top Conversation Users

Cross-provider ranking sibling to:

- [`eng:report-openai-costs`](../report-openai-costs/SKILL.md) — daily snapshot, OpenAI only
- [`eng:report-elevenlabs-costs`](../report-elevenlabs-costs/SKILL.md) — daily snapshot, ElevenLabs only
- [`eng:analyze-openai-cost-per-user`](../analyze-openai-cost-per-user/SKILL.md) — OpenAI deep-dive per-user
- [`eng:analyze-elevenlabs-cost-per-user`](../analyze-elevenlabs-cost-per-user/SKILL.md) — ElevenLabs deep-dive per-user

The four siblings each cover one provider × one dimension. **This skill answers a question none of them can alone**: who burned the most across both providers combined? Runaway users frequently dominate one provider and look ordinary on the other — until you sum them, you can't see the worst offenders. Discovered on 2026-05-17: the top OpenAI spender (\$36) and top ElevenLabs spender (\$76) were the same UID; combined (\$112) they were 22% of the org's entire day-1 conversation cost.

## Step 0 — Check prerequisites

```bash
# Required
test -n "$OPENAI_ADMIN_API_KEY"     && echo "OK openai admin"     || echo "MISSING OPENAI_ADMIN_API_KEY (required)"
test -n "$ELEVENLABS_ADMIN_API_KEY" && echo "OK elevenlabs admin" || (test -n "$ELEVENLABS_API_KEY" && echo "OK elevenlabs (fallback)" || echo "MISSING elevenlabs key (required)")

# Strongly recommended (degrades gracefully)
test -n "$GCP_KEY" && echo "OK GCP_KEY (ledger attribution)" || echo "MISSING GCP_KEY (falls back to stored chat completions for attribution)"
test -n "$AURA_API_TOKEN" && echo "OK aura token (state annotation)" || echo "MISSING AURA_API_TOKEN (top-10 will show UIDs without state)"
```

If a required key is missing, walk the user through adding it (same setup as `eng:analyze-openai-costs` for the OpenAI key, `eng:analyze-elevenlabs-costs` for ElevenLabs). `GCP_KEY` reads the Intelligence Service LLM-call ledger from BigQuery (the primary OpenAI attribution source); without it attribution falls back to the stored-chat-completions walk (chat-API-only). For `AURA_API_TOKEN`, see Tools/Aura API in AI Knowledge — it's the team dev token (`system` role) for the PaymentService subscription endpoint (state annotation); without it the state column is dropped.

Other prereqs: `python3` with `requests`.

## Step 1 — Resolve the window

Default is **yesterday Pacific** (`--window yesterday-pt`). The PT alignment matters because ElevenLabs ConvAI's 10-day retention window is naturally per-conversation and `eng:analyze-elevenlabs-cost-per-user` already uses Pacific yesterday — matching it here keeps overlap stats comparable.

Accepted windows:

- `--window yesterday-pt` (default) — full previous PT calendar day
- `--window today-pt` — partial; flags incompleteness in the header
- `--window last-7d` — rolling 7d ending now (UTC, since multi-day windows are insensitive to TZ alignment)
- `--since YYYY-MM-DD --until YYYY-MM-DD` — custom (interpreted as PT, half-open)

For windows >10 days, ElevenLabs ConvAI conversation details may be pruned (retention) — the skill warns and degrades to "(retention-pruned)" on the ElevenLabs side.

### Why PT-aligned (and what that costs)

For yesterday PT, the window spans two OpenAI UTC buckets:

- Span 1: `yesterday_PT_start_utc → today_utc_midnight` (17h, in yesterday's UTC Costs API bucket)
- Span 2: `today_utc_midnight → today_PT_start_utc` (7h, in today's UTC Costs API bucket)

The skill fetches BOTH buckets from `/v1/organization/costs`, walks completions across BOTH UTC bucket spans, filters to the PT window, and allocates $ from each bucket proportionally by per-bucket token share. See [`references/allocation.md`](references/allocation.md) for the math.

**Caveat — today UTC bucket may not have propagated yet.** OpenAI's Costs API typically lags 1–4h on the most recent bucket. If the report runs before today's bucket propagates, the 7h tail attributes \$0 — the skill surfaces this in the header banner ("evening PT usage may be undercounted on OpenAI side"). Mitigation: run after ~10am UTC (~3am PT) for clean numbers; or accept undercount of the tail.

## Step 2 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-top-conversation-users/scripts/report.py \
  --window yesterday-pt \
  --json /tmp/top_conv_users.json
```

Other forms:

```bash
# Rolling 7-day total
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-top-conversation-users/scripts/report.py --window last-7d

# Custom range
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-top-conversation-users/scripts/report.py \
  --since 2026-05-15 --until 2026-05-17

# Suppress state annotation (faster — skips 10 PaymentService calls)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-top-conversation-users/scripts/report.py --no-state

# Different top-N
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-top-conversation-users/scripts/report.py --top 25

# Post a summary to #eng-budget after rendering
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-top-conversation-users/scripts/report.py --post-to-slack
```

The script:

1. Fetches authoritative $ per project for both UTC buckets from `/v1/organization/costs`
2. Builds per-(uid) token weights per bucket. **Post-epoch slices (≥ 2026-07-23T03:25Z)** read the Intelligence Service LLM-call ledger first (BigQuery `aura-main.llm.llm_call`, one paged walk per bucket, grouped by project key). **Pre-epoch slices** — and any post-epoch slice where the ledger call fails — paginate `/v1/chat/completions` with `OpenAI-Project: <pid>` header instead. Filters to the PT window inside the loop either way
3. Allocates each project's UTC-bucket $ across users proportional to token share
4. Walks ElevenLabs `/v1/convai/conversations` in the PT window, fetches each call's detail for `duration_secs` and `metadata.charging.is_burst`, computes $ via `duration_secs × \$0.09/min in-plan, \$0.19/min burst` (Business plan rates; from `Tools/ElevenLabs`)
5. Merges by Firebase UID, sums OpenAI $ + ElevenLabs $ → total $, ranks descending
6. For the top-N (default 10), calls Aura `POST /subscriptions/list/:userId` with `{"includeDeleted": true}` to derive a current state label per `eng:report-elevenlabs-costs`'s pattern. Deleted rows are classified into `AI Trial Expired` / `AI Refunded` / `AI Revoked` via the `_classify_deletion` heuristic (see ENG-540) — these are meaningfully different cohorts and shouldn't be lumped together.
7. Renders four sections + optional Slack payload + JSON output

### Attribution source — Intelligence Service LLM-call ledger first, stored completions as failover

The OpenAI token-weight attribution reads the IntelligenceService per-call LLM ledger first: BigQuery `aura-main.llm.llm_call` (auth: `GCP_KEY`; docs: [LLM Calls](https://app.notion.com/p/aurahealth/LLM-Calls-3a675e23af208127b217e53af47524e8)). One row per OpenAI call — **both Chat Completions and Responses APIs** — with `userId`, `project`, `inputTokens`/`outputTokens`, `aiPremiumState`, and `openaiId` as top-level columns.

- **Ledger epoch: 2026-07-23T03:25Z** (Intelligence Service Release 0.285.0). There are no ledger rows before this moment, so any window slice before the epoch **always** uses the OpenAI stored-chat-completions walk — it's the only source for pre-epoch data.
- Post-epoch slices use the ledger as primary. If the ledger read fails (missing `GCP_KEY`, BigQuery error), the script prints a stderr warning and falls back to the stored-completions walk for that slice. **The fallback is chat-API-only and cannot see Responses-API traffic** — Responses-API calls still count in the authoritative Costs-API $ but sit in the unattributed remainder.
- Results from the two slices are merged, deduped defensively on the OpenAI object id (`openaiId` == stored completion `id`) at the epoch boundary.
- The rendered report annotates which source(s) fed the attribution: `Attribution source (OpenAI token weights): LLM ledger (…)` / `stored chat completions (…)` / `mixed (window straddles ledger epoch 2026-07-23T03:25Z)`. The same string lands in `--json` under `window.attribution_source`.
- Unlike OpenAI stored completions, the ledger has no retention cutoff going forward.
- **Dollar methodology is unchanged.** The Costs API stays the $ authority; the ledger only replaces where the token weights come from. CLI flags and output table shapes are untouched.

**Walltime.** ElevenLabs ConvAI detail fetch dominates (~30-60s for ~150 yesterday conversations). OpenAI side ~60-120s — the script walks the FULL UTC bucket per project (not just the PT slice) so we can compute the PT-share denominator for the actuals-only allocation (see ENGBACK-1087 / `references/allocation.md`). Subscription state ~5s for the top-10. Total ~100-180s expected.

## Step 3 — Show the report inline

Print the script's stdout directly. The format is short enough to fit one screen — don't paginate, don't reformat.

**Actuals only.** The report shows only what was actually billed in the window — no projected averages, no `+X% vs 7d avg` comparison, no anomaly flags built off a stitched-together baseline. Same posture as [`eng:analyze-elevenlabs-costs`](../analyze-elevenlabs-costs/SKILL.md) after its 2026-05 actuals-only rework, and [`eng:analyze-openai-costs`](../analyze-openai-costs/SKILL.md). If you need trend analysis, re-run the report against multiple windows and compare those actuals directly.

Sample output:

```
# Top combined conversation users — Yesterday PT (May 16)

**\$384.91 combined**
(\$161.83 OpenAI + \$223.08 ElevenLabs)

## Top 10

| Rank | UID | State | OAI $ | OAI calls | ELabs $ | ELabs min | Total $ | Cumul% |
|---:|---|---|---:|---:|---:|---:|---:|---:|
| 1 | XGxAMqr1… | AI Trial | \$33.50 | 2,575 | \$76.43 | 849.2 | \$109.93 | 36.0% |
| ...

## Overlap

- 66 users on both providers
- ~2,000 OpenAI-only (text chatters)
- 0 ElevenLabs-only ✅

## Provider mix

- OpenAI: \$161.83 (42%)
- ElevenLabs: \$223.08 (58%)
```

## Step 4 — Ask what to do next

ALWAYS use `AskUserQuestion` to offer routing. Don't auto-route. Options:

1. **Drill into the top user** — chain to [`data:analyze-user-conversations`](../../../data/skills/analyze-user-conversations/SKILL.md) with the top user's UID for transcripts + friction analysis
2. **Per-provider deep-dive** — chain to [`eng:analyze-openai-cost-per-user`](../analyze-openai-cost-per-user/SKILL.md) or [`eng:analyze-elevenlabs-cost-per-user`](../analyze-elevenlabs-cost-per-user/SKILL.md) for that provider's distribution stats
3. **Post a Slack summary** — re-run with `--post-to-slack` (or, if you already saw the report, ship the same payload manually). When posting via `shared:post-to-slack`, pass `--footer eng:report-top-conversation-users --footer-extra prod` so it stamps the versioned footer (`_Generated by `eng:report-top-conversation-users:<version>` · <YYYY-MM-DD> · prod_`) — don't hand-type one in the payload.
4. **Compare windows** — re-run for a different window (week-over-week, last-30d, custom)
5. **Nothing, just show it** — stop here

## When to use this vs the sibling skills

| Question | Skill |
|---|---|
| "Who burned the most across BOTH providers yesterday?" | **`report-top-conversation-users`** (this) |
| "What did we spend yesterday total (by prompter, by tier)?" | `eng:report-openai-costs` + `eng:report-elevenlabs-costs` |
| "Who are the top 20 OpenAI spenders this month?" | `eng:analyze-openai-cost-per-user --window month-to-date --top 20` |
| "Who are the top 20 ElevenLabs spenders this week?" | `eng:analyze-elevenlabs-cost-per-user --window last-7d --top 20` |
| "How does Trial vs Paid spend compare on OpenAI?" | `eng:analyze-openai-cost-per-subscription-state` |

Use this skill specifically when **runaway-user investigation** is the goal — when "we have a heavy user, who is it" needs an answer that crosses providers.

## Privacy posture

Per [`Compliance/Privacy`](https://www.notion.so/35f75e23af20813ea4e7f4165c9e0844):

- **Firebase UIDs truncated to first 8 chars + `…`** in every rendered markdown surface (top-N table, overlap section, sample output). Full 28-character UID appears only in `--json` output (the documented exception for downstream tooling).
- **`givenName` is never read or surfaced.** The script doesn't even fetch it from the subscription endpoint.
- **Don't paste full UIDs from `--json` into Slack, Notion, or anywhere else.** Re-render through the markdown path or apply truncation manually.

## Subscription-state annotation

Reuses the pattern from [`eng:report-elevenlabs-costs`](../report-elevenlabs-costs/SKILL.md). Calls `POST https://services.aurahealth.io/subscriptions/list/:userId` with `Authorization: Bearer ${AURA_API_TOKEN}` and `{"includeDeleted": true}`. `includeDeleted=true` is intentional — without it, refunded/revoked rows are invisible and a user with a refunded trial looks identical to a never-premium user.

State labels (single short label for the table):

- `AI Lifetime` — `type=ai`, `lifetime=true`, no `deletedAt`
- `AI Paid` — `type=ai`, `autoRenew=true`, `trial=false`, currently active
- `AI Trial` — `type=ai`, `autoRenew=true`, `trial=true`, currently active
- `AI Trial (cancelled)` — `type=ai`, `autoRenew=false`, `trial=true`, currently active
- `AI Cancelled (active)` — `type=ai`, `autoRenew=false`, not trial, currently active (will expire)
- `AI Expired` — `type=ai` history, no active row, no deletions
- `AI Trial Expired` — `type=ai` row with `deletedAt` within 5 min of `expirationAt`, `autoRenew=false`, `trial=true`. **Standard end-of-trial flow** — user let trial run out naturally after disabling auto-renew. (ENG-540: this is *not* a refund.)
- `AI Refunded` — `type=ai` row with `deletedAt` materially before `expirationAt` (>5 min). Implies Apple/Google/Aura processed a refund before the period would have ended. This is the "refund-then-keep-using" / chargeback cohort.
- `AI Revoked` — `type=ai` row with `deletedAt` but neither of the above (admin/CS action, payment-failure cleanup). Rare.
- `Content-only (Aura)` / `Content Expired (Aura)` — only `type=content` rows
- `Never premium` — empty array

See `Tools/Aura API/Subscriptions/List Subscriptions By User Id` → "Subscription-timeline pattern" for the canonical write-up + the reusable trio (`fetch_subscription_rows` + `derive_state` + `derive_timeline`).

**Full subscription timeline in `--json`.** Every top-N user's `--json` entry includes a `subscription_timeline` array sorted by `createdAt` ascending with per-row `status` (`active` / `cancelled-active` / `expired` / `refunded`). Suitable for downstream agents that want the full subscription story.

## Edge cases

**Allocation is service-tier aware** ([ENG-1106](https://linear.app/aura-health/issue/ENG-1106)).
The `bucket_$ × user_pt_tokens / full_day_tokens` split scales each call's tokens by its
service tier: OpenAI prices Flex at exactly **50%** of Standard and Fast (renamed from
`priority` on 2026-07-30) at **2×**, so `flex`/`batch` weigh ×0.5, `default`/`auto`/`scale`
×1.0, and `fast`/`priority` ×2.0. Per-tier list prices live in `Tools/OpenAI` → "Per-tier
list prices". Both `user_pt_tokens` and `full_day_tokens` accumulate from the same scaled
value in the same loop, so numerator and denominator stay on one basis — scaling only one
would be worse than scaling neither. A null tier weighs 1.0.

- **The stored-completions fallback resolves nothing for most projects (measured 2026-08-04).** What matters is whether a project's completions are **stored and listable**, not which API it calls — `Feed` still spends daily but has had no stored completion since **2026-07-23**. On a post-2026-07-23 window the ledger is effectively the only attribution source: every production project except `Conversation` has nothing stored, and `Conversation` itself is only **partially** covered — the `aiResponseAPI` A/B (since 2026-07-31) serves its treatment arm on the Responses API, so one conversation can span both surfaces. If the ledger is unreachable (or `GCP_KEY` is unset) this run does not degrade gracefully: dollars still come from the Costs API, so the report looks plausible while attribution collapses into the untagged bucket. **Treat a post-2026-07-23 run whose ledger call failed as unusable, not degraded** — re-run it rather than reading the numbers. Canonical per-project detail: `Tools/OpenAI` → "Responses API migration — reading stored data"; don't restate it here. [ENG-1049](https://linear.app/aura-health/issue/ENG-1049)
- **A post-2026-07-23 ledger outage now fails closed (exit 2) — [ENG-1054](https://linear.app/aura-health/issue/ENG-1054).** The caveat above says such a run is unusable rather than degraded; this is the code that enforces it, rather than a stderr warning that scrolls past. Pass `--allow-degraded-attribution` (or set `AINATIVE_ALLOW_DEGRADED_ATTRIBUTION=1`) to accept the degraded attribution deliberately. **Unaffected:** pre-epoch slices (the ledger has no rows before 2026-07-23T03:25Z, so stored completions are the correct source), projects the ledger doesn't serve, and the roll-out-ramp union — none of those is a ledger *failure*.

- **Today UTC bucket not yet propagated** — header warning, 7h tail attributes \$0 on OpenAI side
- **Window > 10 days** — ElevenLabs conversation details pruned (10-day retention); skill warns and degrades the ElevenLabs side
- **OpenAI window straddling 2026-05-12 metadata-tagging rollout** — state annotation missing for pre-rollout calls; same banner the existing cost-cohort skills show
- **Empty result** — friendly "no usage in window" rather than empty table
- **`GCP_KEY` missing** — post-epoch attribution falls back to stored chat completions (stderr warning; chat-API-only, Responses-API traffic unattributed). **`AURA_API_TOKEN` missing** — top-10 shows without state column; warn once at top, never a crash
- **Ledger endpoint down / non-200** — same per-slice fallback to stored chat completions with a stderr warning; pre-epoch slices are unaffected (they never touch the ledger)
- **Single-provider day** — if either provider has zero usage in the window, the other carries the entire ranking and the overlap section flags this
- **Cowork sandbox** — `mcp__workspace__bash` strips host env (PROD-1305). Use the per-session paste-export workaround documented in AINative `CLAUDE.md` for each of the three required tokens.

## Comparison with the cost-snapshot skills

`report-openai-costs` and `report-elevenlabs-costs` answer **what did we spend?** This skill answers **who spent it?** — across both providers. The two cost-snapshot skills lead with prompter/model breakdowns and tier mini-summaries; this skill leads with the user ranking. They share the proportional-allocation methodology but produce different reports.

Run all three for the full morning-standup view of conversational AI cost:
1. `eng:report-openai-costs` — total + per-prompter + by-tier (~30s)
2. `eng:report-elevenlabs-costs` — total + by-model + by-product-type (~60s)
3. `eng:report-top-conversation-users` (this) — who across both (~75s)

Or use this single skill if all you care about is the runaway-user view.
