---
name: report-openai-costs
description: >
  One-pane consolidated daily OpenAI cost snapshot for the Aura org — defaults to yesterday
  UTC, the closest full-day authoritative bucket, and produces a single condensed report: day
  total vs 7-day average, a per-prompter cost table sorted descending, top 5 users across all
  projects with AI premium state annotated, and a by-tier mini-summary. Sits next to the
  `analyze-openai-*` deep-dive skills but optimised for the morning-standup "what happened
  yesterday" question. Uses the same pure proportional-allocation method as the per-state and
  per-user skills — authoritative dollars per project from the Costs API, split by token
  weight across prompter, uid and tier — so no per-model rate table is required. Trigger on
  "summarize openai costs", "report openai costs", "yesterday's openai report", "openai daily
  cost", "openai cost snapshot", "morning openai report", "where did we spend yesterday",
  "what's our openai bill", "cost summary", or "how much did we spend yesterday on openai".

metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/OpenAI  # cost computation methodology, prompter → app mapping, premium-state semantic
    - Tools/Slack   # channel directory + Post format footer spec for #eng-budget post (--post-to-slack)
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
---

# Report OpenAI Costs

Sibling to [`eng:analyze-openai-costs`](../analyze-openai-costs/SKILL.md), [`eng:analyze-openai-cost-per-subscription-state`](../analyze-openai-cost-per-subscription-state/SKILL.md), and [`eng:analyze-openai-cost-per-user`](../analyze-openai-cost-per-user/SKILL.md). Same authoritative dollar source — the OpenAI Costs API at `/v1/organization/costs` — but instead of a multi-page per-dimension deep-dive, this skill produces a **one-screen daily snapshot** combining four cuts:

1. **Day totals** — total $ for the window + comparison to the 7-day rolling average
2. **Per-prompter table** — every prompter across every production project, sorted by spend
3. **Top 5 users across all projects** — Firebase UID + AI premium state + total cost
4. **By-tier mini-summary** — $ split across Premium Paid / Trial / Free / Payment Pending / Never AI Premium / Non Premium

Designed for the morning-standup "what did we spend yesterday?" use case. The existing `analyze-*` skills are better when you want depth on one dimension; this skill is better when you want one screen with everything.

## Step 0 — Check prerequisites

```bash
test -n "$OPENAI_ADMIN_API_KEY" && echo "OK: admin key set" || echo "MISSING admin key"
test -n "$GCP_KEY" && echo "OK: GCP_KEY set" || echo "MISSING GCP_KEY"
```

If the admin key is missing, walk the user through the same setup as `eng:analyze-openai-costs` — Admin API key (`sk-admin-...`), Owner role on the OpenAI org, store in `~/.claude/settings.json` under the `env` block, restart Claude Code. Hand off to `update-config` to add the key without clobbering existing entries.

`GCP_KEY` (the skills service account) authenticates the Intelligence Service LLM-call ledger (BigQuery `aura-main.llm.llm_call`) — the **primary** token-attribution source for the post-epoch part of any window. If it's missing the script does NOT fail: it warns on stderr and falls back to the stored-chat-completions walk (which is chat-API-only and cannot see Responses-API traffic).

Other prereqs: `python3` with `requests` (`pip3 install requests` if missing). No MCP fallback exists for the OpenAI Admin API — missing admin key is a hard fail.

## Step 1 — Resolve the time window

Default is **yesterday UTC** — the most recent full day with authoritative Costs API data. Other forms:

- **Yesterday UTC** (`--window yesterday`, default) — 24h ending at today's UTC midnight
- **Today UTC** (`--window today`) — partial day; the skill flags the incompleteness in the header
- **Last 7 days** (`--window last-7d`) — rolling 7d ending now
- **Last 30 days** (`--window last-30d`) — rolling 30d ending now
- **Custom** — `--since YYYY-MM-DD --until YYYY-MM-DD` (UTC, half-open)

If the user names a date directly ("yesterday", "2026-05-16", "last Monday"), translate to `--since` / `--until` and pass through.

**Why yesterday UTC and not yesterday PT.** The Costs API only returns daily UTC buckets — there is no sub-day granularity. "Yesterday PT" would span two UTC bucket fragments, the later of which usually hasn't propagated yet (OpenAI's Costs API typically lags ~24h on the most recent bucket). Yesterday UTC is the latest window with a fully-propagated dollar total. The skill flags this in the header so readers don't conflate it with calendar-yesterday in their local TZ.

## Step 2 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-openai-costs/scripts/report.py \
  --window yesterday \
  --json /tmp/openai_daily_snapshot.json
```

Other forms:

```bash
# Rolling 7-day total (useful for week-over-week)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-openai-costs/scripts/report.py --window last-7d

# Custom range
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-openai-costs/scripts/report.py \
  --since 2026-05-15 --until 2026-05-17

# Suppress top-users section (Slack-friendlier)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-openai-costs/scripts/report.py --no-top-users

# Post a summary to #eng-budget after rendering
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-openai-costs/scripts/report.py --post-to-slack
```

The script:

- Fetches authoritative $ per project from `/v1/organization/costs` (group_by=project_id) for the window — the Costs API remains the sole $ authority
- Builds the token/attribution weights **ledger-first**: for the slice of the window at or after the ledger epoch (2026-07-23T03:25Z), it reads the IntelligenceService per-call LLM ledger (BigQuery `aura-main.llm.llm_call`, auth `GCP_KEY` — [docs](https://app.notion.com/p/aurahealth/LLM-Calls-3a675e23af208127b217e53af47524e8)) per project, using top-level columns `prompterName` / `userId` / `aiPremiumState` / `inputTokens` / `outputTokens`. The ledger covers **both** Chat Completions and Responses API calls
- For the pre-epoch slice of the window (anything before 2026-07-23T03:25Z), or if the ledger request fails, it falls back to paginating `/v1/chat/completions` with the `OpenAI-Project: <pid>` header, aggregating tokens from `metadata.{name, user, aiPremium, aiPremiumState}` — with a stderr warning on fallback (the completions walk is chat-API-only and cannot see Responses-API traffic). Slice results are merged and deduped on the OpenAI object id
- Allocates each project's authoritative $ proportionally by total token weight across the dimension being rendered
- Renders four sections in order: Day totals → Per-prompter → Top 5 users → By-tier mini, plus a footer line annotating the attribution source (`LLM ledger (...)` / `stored chat completions (...)` / `mixed (window straddles ledger epoch 2026-07-23T03:25Z)`)
- Prints to stdout (markdown); writes structured JSON if `--json` given

Unlike OpenAI stored completions (~30-day retention), the ledger has no retention limit going forward — post-epoch windows stay attributable indefinitely.

**Allocation math.** See [`references/allocation.md`](references/allocation.md). Summary: per-project $ is split across (prompter, uid, tier) proportional to total tokens. No per-model rate table — sidesteps drift when OpenAI ships new models or reprices.

**Why this is accurate.** Verified empirically on 2026-05-16 UTC: the proportional allocation matches OpenAI's Costs API authoritative total to the dollar. Contrast with naive `tokens × per-model-rate` back-calculation, which overstates by 2–3x because `/v1/chat/completions` list endpoint omits `prompt_tokens_details.cached_tokens` — see [`Tools/OpenAI` → Cost computation methodology](https://www.notion.so/34c75e23af20812cbac8e0fcb0eefe1e) for the canonical write-up of that trap.

## Step 3 — Show the report inline

Print the script's stdout directly in the response. The format is short enough to fit a single screen — don't paginate, don't summarise, don't reformat. The header line carries the window + comparison-to-baseline number so the user gets the headline in one glance:

> **Yesterday (2026-05-16 UTC): \$161.39 — down 37% from 7d avg (\$254.83)**

Below that the four sections render in order. Use the script's output verbatim.

## Step 4 — Ask what to do next

ALWAYS use `AskUserQuestion` to offer routing. Don't auto-route. Options:

1. **Drill into a specific tier** — chains to [`eng:analyze-openai-cost-per-user --tier <tier>`](../analyze-openai-cost-per-user/SKILL.md) with the same window resolved here. Pre-fill the tier from the by-tier table's top row if the user wants the most expensive cohort.
2. **Drill into a specific user** — hand the top-ranked UID to [`data:analyze-user-conversations`](../../../data/skills/analyze-user-conversations/SKILL.md) with `--user-id <uid>` so you can see what they were actually doing (transcripts, prompts, friction points).
3. **Post a Slack summary** — re-runs with `--post-to-slack` to emit a condensed payload, then **invoke [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** to send it to `#eng-budget` (that skill owns the connection — MCP-first, `SLACK_BOT_TOKEN` script fallback). See the Slack integration section below.
4. **Compare windows** — re-run for a different window (week-over-week, last-30d, custom range) so the user can see the trend.
5. **Nothing, just show it** — stop here.

## When to use this vs the sibling skills

| Question | Skill |
|---|---|
| "What did we spend yesterday?" | **`report-openai-costs`** (this) |
| "Where's all our spend going this month?" | `eng:analyze-openai-costs` |
| "How does Trial vs Paid cost compare?" | `eng:analyze-openai-cost-per-subscription-state` |
| "Who are our top 20 spenders?" | `eng:analyze-openai-cost-per-user` |
| "Detailed per-model rate breakdown" | `eng:analyze-openai-costs --by-model` |

The deep-dives default to month-to-date and produce multi-page reports. This skill defaults to yesterday and fits one screen.

## Slack integration

`--post-to-slack` makes `report.py` **emit** the Slack payload (it does not post — the script just writes the payload out for the agent to pick up). Post it by **invoking [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** with that payload and channel `#eng-budget` (`C05PC18APLH`, canonical ID in `Tools/Slack`); the slack skill owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback) and the unfurl-off default — don't call Slack directly here. The payload strips the top-users section by default (PII-adjacent) and includes only the day total + per-prompter + by-tier mini.

When posting the emitted payload, pass `--footer eng:report-openai-costs --footer-extra prod` to `shared:post-to-slack` so it stamps the versioned footer (`_Generated by `eng:report-openai-costs:<version>` · <YYYY-MM-DD> · prod_`). The `--post-to-slack` payload carries no footer — don't hand-type one.

## Edge cases

**Allocation is service-tier aware** ([ENG-1106](https://linear.app/aura-health/issue/ENG-1106)).
Each project's dollars are split by token weight scaled by the call's service tier:
OpenAI prices Flex at exactly **50%** of Standard and Fast (renamed from `priority` on
2026-07-30) at **2×**, so `flex`/`batch` weigh ×0.5, `default`/`auto`/`scale` ×1.0, and
`fast`/`priority` ×2.0. Per-tier list prices live in `Tools/OpenAI` → "Per-tier list prices".
Both extraction paths scale — the ledger's `serviceTier` column and the stored completion's
top-level `service_tier`. A tier-homogeneous project cancels the factor out; the skew appears
where tiers mix inside one project. A null tier weighs 1.0.

- **The stored-completions fallback resolves nothing for most projects (measured 2026-08-04).** What matters is whether a project's completions are **stored and listable**, not which API it calls — `Feed` still spends daily but has had no stored completion since **2026-07-23**. On a post-2026-07-23 window the ledger is effectively the only attribution source: every production project except `Conversation` has nothing stored, and `Conversation` itself is only **partially** covered — the `aiResponseAPI` A/B (since 2026-07-31) serves its treatment arm on the Responses API, so one conversation can span both surfaces. If the ledger is unreachable (or `GCP_KEY` is unset) this run does not degrade gracefully: dollars still come from the Costs API, so the report looks plausible while attribution collapses into the untagged bucket. **Treat a post-2026-07-23 run whose ledger call failed as unusable, not degraded** — re-run it rather than reading the numbers. Canonical per-project detail: `Tools/OpenAI` → "Responses API migration — reading stored data"; don't restate it here. [ENG-1049](https://linear.app/aura-health/issue/ENG-1049)
- **A post-2026-07-23 ledger outage now fails closed (exit 2) — [ENG-1054](https://linear.app/aura-health/issue/ENG-1054).** The caveat above says such a run is unusable rather than degraded; this is the code that enforces it, rather than a stderr warning that scrolls past. Pass `--allow-degraded-attribution` (or set `AINATIVE_ALLOW_DEGRADED_ATTRIBUTION=1`) to accept the degraded attribution deliberately. **Unaffected:** pre-epoch slices (the ledger has no rows before 2026-07-23T03:25Z, so stored completions are the correct source), projects the ledger doesn't serve, and the roll-out-ramp union — none of those is a ledger *failure*.

- **Window before the ledger epoch (2026-07-23T03:25Z).** The Intelligence Service LLM-call ledger went live in Release 0.285.0 — there are no ledger rows before that moment, so pre-epoch windows (and the pre-epoch slice of straddling windows) always use the stored-chat-completions walk. The report footer annotates which source(s) were used.
- **Ledger unreachable (BigQuery error, missing `GCP_KEY`).** Warning on stderr + automatic fallback to stored chat completions for the post-epoch slice — never a crash. Caveat: the fallback is chat-API-only, so Responses-API traffic is invisible to it (those dollars still reconcile via proportional allocation over the calls it can see).
- **Custom range straddling the OpenAI tagging rollout (2026-05-12).** Pre-rollout completions have no `aiPremium*` / `premium*` metadata, so the by-tier mini shows everything as `(untagged)` for the pre-rollout portion. The skill prints a banner when this happens (same logic as the sibling skills).
- **Project with zero spend in the window.** Skipped silently — no row in the per-prompter table.
- **Project with zero stored completions but non-zero $ from Costs API.** The $ lands in `(untagged)` rows on the by-tier mini and in a `(no-completions) <ProjectName>` row in per-prompter so the dollar totals still reconcile.
- **OpenAI Costs API lag on most-recent bucket.** Today's bucket commonly lags 1–4 hours. If `--window today` is run and the bucket hasn't propagated, the skill flags "today's data not yet propagated by OpenAI — try in a few hours" and exits gracefully.
- **Cowork sandbox.** `mcp__workspace__bash` strips host env (PROD-1305), so `OPENAI_ADMIN_API_KEY` won't reach the sandbox. Use the per-session paste-export workaround documented in AINative `CLAUDE.md`.

## Comparison baselines

The day-total header includes a 7-day rolling-average comparison (excluding the day being reported). If the day is more than ±20% from the 7-day average, the header flags it ("up 35% vs 7d avg" or "down 42% vs 7d avg") so anomalies surface immediately. The 7-day window is fetched in the same Costs API call (one extra request, no perf cost worth optimising).
