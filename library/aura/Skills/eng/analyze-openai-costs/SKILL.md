---
name: analyze-openai-costs
description: >
  Generate an OpenAI spend report for the Aura organization, broken down by project, over a
  chosen window — month-to-date (default), previous month, last 7 days, last 30 days, or
  custom. `--by-model` back-calculates Aura's empirical per-token rates per model from costs
  divided by usage. `--by-prompter` ranks spend by prompter name using cost-shaped
  token-weight allocation, and flags output-heavy prompters whose average output per call is a
  large multiple of the median. After the project view lands it offers drill-downs by
  subscription state, by top users, or by prompter, passing the same resolved window through.
  Trigger on "analyze openai costs", "openai spend report", "openai bill", "how much are we
  spending on openai this month", "openai cost breakdown", "openai budget", "cost per token",
  "per-model openai rates", "refresh openai pricing", "cost per prompter", "spend by
  prompter", "which prompter is most expensive", "cost per renderer", or "which prompt is
  burning tokens".

metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/OpenAI  # known per-model rate table; consulted on every run for new-model detection
    - Tools/Slack   # canonical Channel IDs (for skills) — resolves #eng-budget → C05PC18APLH for the optional Step 4 post
    - Tools/Notion                   # Notion REST / MCP access pattern (NOTION_TOKEN)
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
  writes:
    - Tools/OpenAI  # rate table refreshed when a new model is detected or --refresh-rates passed
---

# Analyze OpenAI Costs

Pulls per-project spend from the [OpenAI Costs API](https://platform.openai.com/docs/api-reference/usage/costs) for the Aura organization and renders a markdown report in chat. Optionally lists API-key token usage within each project as a secondary breakdown.

The dollar figures come straight from `/v1/organization/costs` — same numbers OpenAI bills off. The per-API-key view comes from `/v1/organization/usage/completions` and reports **token counts, not dollars** (per-key dollar attribution isn't exposed by the API).

## Step 0 — Check prerequisites

```bash
test -n "$OPENAI_ADMIN_API_KEY" && echo "OK: admin key set" || echo "MISSING"
```

If missing, the skill **cannot run** — there is no MCP fallback for OpenAI org-level endpoints and a regular `OPENAI_API_KEY` can't reach `/organization/*`. Walk the user through setup:

1. The key must be an **Admin API key** (`sk-admin-...`), not a regular `OPENAI_API_KEY` (`sk-proj-...` / `sk-...`).
2. Create one at <https://platform.openai.com/settings/organization/admin-keys> — requires Owner role on the OpenAI org.
3. Store it in **Claude global env only** — `~/.claude/settings.json` under the `env` block. Hand off to the `update-config` skill to add `OPENAI_ADMIN_API_KEY` there without clobbering existing entries.
4. The user must **restart Claude Code** for it to load — confirm before retrying.

Other prereqs: `python3` with `requests`. Install with `pip3 install requests` if the script errors on import.

For `--by-prompter`, also check `GCP_KEY`:

```bash
test -n "$GCP_KEY" && echo "OK: GCP_KEY set" || echo "MISSING (by-prompter will fall back to stored completions)"
```

Post-epoch attribution reads the IntelligenceService per-call LLM ledger (BigQuery `aura-main.llm.llm_call`) with this key. If it's missing or the ledger read fails, the script warns on stderr and falls back to the stored-chat-completions walk; it does not crash.

## Step 1 — Resolve the time window

Ask the user which window they want. Presets:

- **Month-to-date** (default) — 1st of current calendar month UTC → now
- **Previous month** — full prior calendar month (UTC)
- **Last 7 days** — rolling 7d ending now
- **Last 30 days** — rolling 30d ending now
- **Custom range** — explicit `--since YYYY-MM-DD --until YYYY-MM-DD` (UTC, half-open)

If the user names a month directly ("April", "2026-04"), translate it to a `--since` / `--until` pair covering that full calendar month.

## Step 2 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-costs/scripts/report.py \
  --window month-to-date \
  --json /tmp/openai_costs.json
```

Other window forms:

```bash
# Previous calendar month
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-costs/scripts/report.py --window previous-month

# Rolling 30 days
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-costs/scripts/report.py --window last-30d

# Custom range
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-costs/scripts/report.py \
  --since 2026-04-01 --until 2026-05-01
```

By default the script:

- **Includes the per-API-key usage breakdown.** Pass `--no-api-keys` to skip (faster — saves one extra call per project).
- **Zero-fills every active project** so the table doubles as an inventory check. Projects the Costs API didn't return rows for (e.g. the org's "Default project") appear at the bottom of the table at `\$0.00`. The summary line counts them separately, e.g. `Total: \$1,832 across 24 active project(s) (+ 18 zero-cost in this window)`. Archived projects only appear if the Costs API surfaced them naturally — they don't get zero-filled.
- Pass `--no-zero` to suppress all rows with `amount == 0` — useful when posting the comparison block to Slack, where every row already gets filtered to a meaningful Δ threshold.

The script prints a formatted table on stdout and (with `--json`) writes structured data — each `by_project` entry includes `project_id`, `project_name`, `status`, and `amount`.

## Step 2.5 — (Optional) Per-model token rates

Run **with `--by-model`** when the user asks for cost-per-token, a model rate breakdown, or to refresh Aura's documented OpenAI pricing. This adds one extra call each to `/organization/costs?group_by=line_item` and `/organization/usage/completions?group_by=model`, then back-calculates per-1M-token rates per model from `$ ÷ tokens` — the rates reflect Aura's **actual** billing. Whether that sits above or below OpenAI list is a question for `Tools/OpenAI` → "Per-model rates", not a number to restate here.

```bash
# Just show the per-model rate table inline
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-costs/scripts/report.py \
  --window month-to-date --by-model
```

**Two things distort a back-calculated rate now.** `cache writes` is a fourth billing
dimension OpenAI emits for the gpt-5.6 family; its dollars count toward the model's
`Spend` but yield no per-1M rate, because `/organization/usage/completions` exposes no
cache-write token count to divide by. And because the org bills Flex at half rate, a model
run on both tiers back-calculates to a **blended** rate that matches neither list nor flex —
read the table as "what Aura actually paid per token", not as a price list.

### Detect new models and refresh `Tools/OpenAI`

The page [`Tools/OpenAI`](https://www.notion.so/34c75e23af20812cbac8e0fcb0eefe1e) carries Aura's canonical per-model rate table. To know whether a refresh is needed, pass the currently-documented models in via `--known-models`:

1. Retrieve `Tools/OpenAI` (via `retrieve-knowledge` or the Notion MCP) and extract the model names from the existing rate table.
2. Run the script with those names:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-costs/scripts/report.py \
     --window month-to-date --by-model \
     --known-models "gpt-5.2-chat-latest,gpt-5.4-2026-03-05,gpt-5.4-mini-2026-03-17,gpt-5.5-2026-04-23,..."
   ```
3. If the script prints `**Refresh needed.** New model(s) detected: ...`, propose updating `Tools/OpenAI` with the printed rate table. **Do not write to Notion without the user's go-ahead.** When approved:
   1. Use the Notion MCP `notion-update-page` (command `update_content`) to replace the existing "Per-model rates" section. Bump the **Last refreshed** date and window.
   2. Bust the local `retrieve-knowledge` cache for the page so the next read picks up the change: `/refresh-knowledge Tools/OpenAI`.

Refresh policy:

- **Auto-detect, never auto-write.** The script flags new models; the agent surfaces the diff and asks. This keeps Notion writes deliberate.
- **User-requested refresh.** Phrases like "refresh openai rates", "update the OpenAI pricing", or "the table is stale" → run `--by-model`, then update `Tools/OpenAI` after confirming.
- **No refresh needed.** When the user only asks for the cost report, skip `--by-model` entirely — the basic per-project view doesn't need rates.

### Limits

- Covers chat completions + responses only. Image generation, embeddings, audio, vector stores, etc. each have their own `/organization/usage/<kind>` endpoints with different metering (per-image, per-second, etc.) and are filtered out of the rate table.
- The rate is empirical: tiny-volume models (<\$1 spend in the window) are dropped because their derived rate is noisy. Re-run with a wider window if you need rates for low-volume models.

## Step 2.6 — (Optional) Per-prompter spend allocation

Run **with `--by-prompter`** when the user asks which **prompter** (e.g. `coach-prompter`, `memory-prompter`, `feed-prompter`) is driving spend, or asks for a breakdown by `metadata.name`.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-costs/scripts/report.py \
  --window month-to-date --by-prompter
```

The intelligence-service tags every chat completion with `metadata.name` (the prompter that ran the call). OpenAI doesn't expose per-call dollars, so the script:

1. Pulls authoritative per-project totals from `/v1/organization/costs` (same numbers as the basic view — the Costs API stays the $ authority).
2. Gathers per-call token weights per project, grouped by prompter name. **The primary source is the Intelligence Service per-call LLM ledger** (BigQuery `aura-main.llm.llm_call`, auth `GCP_KEY` — [docs](https://app.notion.com/p/aurahealth/LLM-Calls-3a675e23af208127b217e53af47524e8)): one row per OpenAI call across **both** the Chat Completions and Responses APIs, with `prompterName` carrying the same value as `metadata.name`. If the ledger request fails (missing token, non-200, timeout), the script warns on stderr and **automatically falls back** to the previous `/v1/chat/completions` stored-completions walk for that slice — note the fallback is chat-API-only and cannot see Responses-API traffic. Windows (or window slices) **before the ledger epoch 2026-07-23T03:25Z** (ledger go-live, Intelligence Service 0.285.0) always use the stored-completions walk — there are no ledger rows before that moment. Unlike stored completions (~30-day practical retention), the ledger has no retention limit going forward, so older post-epoch windows stay attributable. Results from the two sources are merged and deduped on the OpenAI object id.
3. Token-weight allocates each project's $ across the prompters that ran in it — same backbone as `eng:analyze-openai-cost-per-user`, just grouped on a different metadata field.

The output includes an `Attribution source:` line stating which source (LLM ledger / stored chat completions / mixed across the epoch) produced the token weights.

The result is a ranked table:

```
| Rank | prompterName | Spend | Share | Tokens | Calls | Users | Conversations | Top project |
```

- **Users** counts distinct `metadata.user` (Firebase UID) values that triggered this prompter in the window — the audience size for the prompter.
- **Conversations** counts distinct `metadata.conversationId` values — only set by conversational prompters (`coach-prompter`, `quick-processing-prompter`, etc.). Non-conversational prompters (`memory-prompter`, `feed-prompter`, `summary-prompter`, …) intentionally leave it unset and render as `—`.
- The two together let the reader sanity-check per-prompter unit economics: `$ / user` for audience cost, `$ / conversation` for conversational prompters specifically.

### Limits

- **Slower than the basic view.** The ledger path is fast (time-paged, 1000 rows/request), but any slice on the stored-completions path costs one `/chat/completions` walk per non-zero project; expect ~30s per project for a full day, longer for wider windows.
- **Calls with no prompter name fall into `(untagged)`.** Surfaced as a separate line with its share of the org total (missing `prompterName` on the ledger, or missing `metadata.name` on stored completions). A non-trivial untagged slice is a signal that some code path isn't passing the metadata.
- **Per-project allocation, not per-call.** A prompter that runs in multiple projects shows the sum of its allocated $ across those projects, plus a `Top project` column for the project where it spent the most.
- **Transient 5xx on one project is non-fatal** — the script retries with backoff and, if it still fails, attributes that project's $ to `(untagged)` rather than killing the run. Skipped projects are logged to stderr.
- **The stored-completions fallback is chat-API-only.** If the ledger is unavailable for a post-epoch slice, Responses-API calls in that slice are invisible to the fallback walk and their tokens fold into `(untagged)` weighting. The stderr warning calls this out.
- **The ledger only records prod Intelligence Service traffic.** `[Development]` and other non-production OpenAI projects always attribute via the stored-completions walk, even post-epoch.

### Cost-shaped weighting, and why not a flat token split ([ENG-1046](https://linear.app/aura-health/issue/ENG-1046))

`--by-prompter` splits each project's authoritative dollars across its prompters by
**cost-shaped** token weight: uncached input ×1.0, cached ×0.1, output ×8.0
(`--w-input` / `--w-cached` / `--w-output` to override).

Those weights approximate OpenAI's pricing **shape**, not its rates — actual per-model
rates live in `Tools/OpenAI` → "Per-model rates" and are deliberately not duplicated
here.

**Why the weights are not equal.** Output costs far more per token than input, and cached
input is heavily discounted, so weighting every token equally under-attributes an
output-heavy prompter. The gap is large: on 2026-08-02
`inner-tensions-renderer-prompter` allocates **\$98.13** cost-shaped and **\$35.90** with
all three weights set to `1` — a 2.7× difference on the prompter driving that day's
Insights spike ([PROD-4163](https://linear.app/aura-health/issue/PROD-4163)), and the same
under-attribution that lets a verbose renderer hide inside `ambient_cost`.

Equal weights *are* the flat total-token split, via the identity
`(input − cached) + cached + output == input + output`, so
`--w-input 1 --w-cached 1 --w-output 1` is a clean sensitivity check rather than a
different metric.

**The weight is scaled by the call's service tier** ([ENG-1106](https://linear.app/aura-health/issue/ENG-1106)).
OpenAI prices Flex at exactly **50%** of Standard and Fast (renamed from `priority` on
2026-07-30) at **2×**, so the ledger's `serviceTier` multiplies each row's weight —
`flex`/`batch` ×0.5, `default`/`auto`/`scale` ×1.0, `fast`/`priority` ×2.0. Per-tier list
prices live in `Tools/OpenAI` → "Per-tier list prices"; they are not duplicated here.

A **tier-homogeneous** project cancels the factor out under normalization, so this changes
nothing for a project that runs one tier throughout — it matters where tiers mix, which
they do in two ways: different prompters in one project run different tiers, and a prompter
that *requests* flex falls back to `auto` under capacity pressure. A row whose `serviceTier`
is null (written before the [ENGBACK-1315](https://linear.app/aura-health/issue/ENGBACK-1315)
deploy) weighs 1.0, reproducing the previous allocation exactly.

**Reasoning tokens are never added on top of output.** The ledger's `outputTokens` (and
a completion's `completion_tokens`) already include reasoning; verified against 128 live
ledger rows, where `totalTokens == inputTokens + outputTokens` exactly and
`outputTokens >= reasoningTokens` in every one. Adding a reasoning field would
double-count it.

**The `Out/call` column and the ⚠️ flag** mark prompters whose average output per call is
≥3× the median across the ranking. Renderers run 1:1 with each other, so call counts are
near-identical and tell you nothing; output per call is what separates a cheap prompter
from an expensive one. The median is taken across the whole ranking rather than
per-project, because a prompter can span projects and a per-project median would give one
prompter several verdicts. It annotates, never filters — a legitimately verbose prompter
is a finding to explain, not an error to hide.

## Step 3 — Show the report inline

Show the script's stdout directly so the user can see the breakdown immediately. Lead with a one-line headline: total spend and top project, e.g. _"Aurie spent \$4,287.13 on OpenAI this month — 72% on the `aurie-prod` project."_

If a project shows a sharp delta vs. the previous comparable window, call it out — but only when the delta is meaningful (≥20% and ≥\$100). Don't speculate about causes; just surface the number.

## Step 4 — Offer drill-downs

After the project view lands, ask the user whether to dig further before routing anything. Most cost investigations follow the same pattern — see the project breakdown → spot an outlier → ask "who or what is driving this?" — and three drill-downs answer that question along three natural axes. Present the menu as a one-line prompt:

> _Want to drill down before we route this? **(s)** by subscription state · **(u)** by top users + distribution · **(p)** by prompter (`metadata.name`) · **(b)** both s+u · **(skip)** just route the project view (default)_

The window is already resolved by Step 1 — pass the same `--window` / `--since` / `--until` straight through so the user doesn't re-pick.

### Option (s) — by subscription state

Chain to [`eng:analyze-openai-cost-per-subscription-state`](../analyze-openai-cost-per-subscription-state/SKILL.md). It splits the same total spend across the five subscription states (Premium Paid / Premium Trial / Premium Free / Premium Payment Pending / Non Premium, plus an untagged catch-all) using `metadata.aiPremium` + `metadata.aiPremiumState` and `metadata.premium` + `metadata.premiumState` tags from ENG-498 / ENG-500. Two cuts in one run: AI premium and content premium.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-cost-per-subscription-state/scripts/report.py \
  --window <same as Step 1>
```

Surface its stdout inline below the project view. Headline framing: which state is the largest share, whether any "Premium Free" cost stands out (those users have Premium access without paying — comped, redemption code, partnership).

### Option (u) — by top users + distribution

Chain to [`eng:analyze-openai-cost-per-user`](../analyze-openai-cost-per-user/SKILL.md). It ranks individual Firebase UIDs by allocated cost, with a distribution-stats block (mean / median / p90 / p95 / p99 / max / stddev) so the headline can frame top spenders against the long tail.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-cost-per-user/scripts/report.py \
  --window <same as Step 1>
```

Default `--top 5`; the user can ask for `--top 25` for a deeper rank. Headline framing: top user as Nx the median, any Premium Free / Non Premium users in the top set (paying \$0, costing like Paid).

### Option (p) — by prompter

Inline `--by-prompter` (no sibling skill — see Step 2.6 above). Ranks spend by `metadata.name` for the same window.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-costs/scripts/report.py \
  --window <same as Step 1> --by-prompter --no-api-keys
```

Pass `--no-api-keys` to skip the per-API-key block (already shown in the basic view) and keep the output focused on the prompter table. Headline framing: which prompter has the largest share, whether the top two prompters dominate (the typical pattern is conversational prompters like `coach-prompter` + memory writes), and whether any prompter's $/call is an outlier vs siblings (suggesting a long-context or model-routing issue).

### Option (b) — both s + u

Run option (s), show its output, then run option (u), show its output. They share the same allocation backbone so the numbers reconcile — `tagged_amount` from the per-user report ≈ sum of non-untagged states from the per-state report. If the user wants all three cuts (s + u + p), run them in that order.

### Option (skip) — default

Proceed to Step 5 (route) with just the project view.

Do NOT chain automatically; always wait for the user's pick. If the user says something like "show me the breakdown" or "by tier" or "who's costing us the most" or "by prompter", interpret that as (s) / (u) / (p) / (b) without re-asking — the menu names the natural follow-ups, but Claude shouldn't be pedantic about the letter choice.

## Step 5 — Ask where to route it

After the report (and any drill-downs from Step 4) land, ask the user what to do next:

1. **Post a summary to Slack** *(optional)* — compact message with the headline + per-project totals, then **invoke [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** to send it to `#eng-budget` (`C05PC18APLH`, canonical ID in `Tools/Slack`); that skill owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback) and the unfurl-off default — don't call Slack directly here. If it reports neither Slack MCP nor a token is available, tell the user and skip — don't treat Slack as a prerequisite.
2. **Save a Markdown summary** to a path the user provides.
3. **Nothing, just show it** — stop here.

If the user drilled down, ask whether the Slack/file summary should include the drill-downs or stay project-only. Default is project-only for Slack (drill-down tables have UID rows that shouldn't go to public channels by default — same posture as `eng:analyze-openai-cost-per-user`); include everything for file saves.

Do NOT route automatically; always wait for the user's pick.

## Slack message format (Step 5 option 1)

This is the polished shape that worked well on first live run — use it verbatim, substituting the right numbers:

```markdown
**OpenAI Spend — <window label>**

- **Total so far:** $<mtd_total> across <N> projects (<days_in> days in)
- **<comparison_window> full month:** $<prev_total> ($<prev_daily>/day) → **<window_label> daily run-rate: ~$<curr_daily>/day (<pct>%)**
- **Projected <window_label> total at current pace:** ~$<projection> (<pct> vs <comparison_window>)

**Top movers (projected vs <comparison_window>)**

| Project | <comparison_window> | <window_label> proj. | Δ |
|---|---:|---:|---:|
| <project> | $<prev> | ~$<proj> | <±pct%> |
| ...top 6–8 by absolute Δ... |

**Headline**

<one paragraph: which project is the standout, supporting token-volume evidence, hypothesis about cause>

_Caveat: projection is a naive linear extrapolation from <days_in> partial days._

OpenAI usage dashboard: https://platform.openai.com/settings/organization/usage
```

Notes:

- **Footer attribution.** Don't compose a footer in the message body — the canonical `Tools/Slack` footer is stamped by the post helper (next bullet), which auto-resolves the version. Spec + worked examples live on the `Tools/Slack` page in AI Knowledge (`Post format` section, pre-fetched via `knowledge.reads`).
- Send this message by **invoking [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** with channel `#eng-budget` (`C05PC18APLH`) and `--footer eng:analyze-openai-costs --footer-extra prod` — the helper stamps the versioned footer (`_Generated by `eng:analyze-openai-costs:<version>` · <YYYY-MM-DD> · prod_`) from `metadata.version` automatically, owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback), and defaults unfurl-off; don't call Slack directly here. Slack accepts standard markdown tables — keep them. Don't pre-format as plain text.
- Only include the **comparison block** when running month-to-date (where projection makes sense). For `previous-month`, drop the projection — the window is already closed; just report totals + top movers.
- Cap the top-movers table at 6–8 rows. Below ~\$50 absolute spend, the noise floor swamps the signal.
- Lead the "Headline" paragraph with the highest-confidence anomaly, not the highest total. A project that 5×ed at moderate $ matters more than a project that 1.4×ed at high $.

## Notes & edge cases

- **The stored-completions fallback resolves nothing for most projects (measured 2026-08-04).** What matters is whether a project's completions are **stored and listable**, not which API it calls — `Feed` still spends daily but has had no stored completion since **2026-07-23**. On a post-2026-07-23 window the ledger is effectively the only attribution source: every production project except `Conversation` has nothing stored, and `Conversation` itself is only **partially** covered — the `aiResponseAPI` A/B (since 2026-07-31) serves its treatment arm on the Responses API, so one conversation can span both surfaces. If the ledger is unreachable (or `GCP_KEY` is unset) this run does not degrade gracefully: dollars still come from the Costs API, so the report looks plausible while attribution collapses into the untagged bucket. **Treat a post-2026-07-23 run whose ledger call failed as unusable, not degraded** — re-run it rather than reading the numbers. Canonical per-project detail: `Tools/OpenAI` → "Responses API migration — reading stored data"; don't restate it here. [ENG-1049](https://linear.app/aura-health/issue/ENG-1049)
- **A post-2026-07-23 ledger outage now fails closed (exit 2) — [ENG-1054](https://linear.app/aura-health/issue/ENG-1054).** The caveat above says such a run is unusable rather than degraded; this is the code that enforces it, rather than a stderr warning that scrolls past. Pass `--allow-degraded-attribution` (or set `AINATIVE_ALLOW_DEGRADED_ATTRIBUTION=1`) to accept the degraded attribution deliberately. **Unaffected:** pre-epoch slices (the ledger has no rows before 2026-07-23T03:25Z, so stored completions are the correct source), projects the ledger doesn't serve, and the roll-out-ramp union — none of those is a ledger *failure*.

- **Numbers may lag the dashboard by up to 24h.** The Costs API aggregates daily buckets and the most recent bucket isn't always closed yet. Mention this if the user is comparing to a value they just saw in the OpenAI UI.
- **Per-API-key dollars are not available** from OpenAI. The per-key view is token counts only — a *relative* signal (which key is hottest in a project), not a billing reconciliation. The reference at [`references/openai-usage-api.md`](references/openai-usage-api.md) explains why.
- **Project names + zero-cost projects** are fetched once via `/v1/organization/projects`. Every active project is zero-filled into the report so the table doubles as an inventory check; archived projects only appear if the Costs API returned rows for them. If a project somehow appears with no name (shouldn't happen with `include_archived=true`), the script falls back to the bare project ID.
- **Currency.** All amounts are returned in USD by the API; the script formats accordingly. If OpenAI ever returns another currency the script will surface it in the row.
- **Cowork sandboxes** strip host env (PROD-1305), so `OPENAI_ADMIN_API_KEY` won't reach `mcp__workspace__bash` automatically. In Cowork, the user must paste-export the value at the start of the session (see CLAUDE.md → "Cowork constraints"). There is no MCP fallback for this skill — the workaround is the only option there.
- **Rate limits.** The Costs API caps at 60 req/min for the org. The script paginates and respects 429 with backoff.
