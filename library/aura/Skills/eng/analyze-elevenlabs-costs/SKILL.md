---
name: analyze-elevenlabs-costs
description: >
  Workspace-level ElevenLabs spend report — answers "how much have we burned this period?",
  "are we already over quota?", "which model dominates spend?". Actuals only, no end-of-period
  extrapolation. The default view pairs billing-period-to-date (auto-aligned to the
  workspace's actual renewal date, not the calendar month) with last-7-days, broken down by
  model. Reports credit consumption per window, actual plan cost, live overage if non-zero,
  and quota used so far as a fraction. The retail-equivalent USD figure is intentionally NOT
  rendered — it reads like a bill but isn't what we owe. For the per-user drill-down use
  `eng:analyze-elevenlabs-cost-per-user`. Trigger on "analyze elevenlabs costs", "elevenlabs
  spend report", "elevenlabs bill", "how much are we spending on elevenlabs", "tts costs",
  "11labs spend", "voice costs this month", "elevenlabs cost breakdown", "elevenlabs budget",
  "weekly elevenlabs cost", "month to date elevenlabs usage", "elevenlabs quota", or "are we
  over our elevenlabs plan".

metadata:
  version: "3.0.5"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/Slack   # channel directory + Post format footer spec for #eng-budget post
    - Tools/ElevenLabs                   # ElevenLabs API access (ELEVENLABS_API_KEY)
---

# Analyze ElevenLabs Costs

Pulls workspace usage from [`GET /v1/usage/character-stats`](https://elevenlabs.io/docs/api-reference/usage/character-stats) and the subscription from `GET /v1/user/subscription`, then renders a markdown report.

By default the skill produces **two views side by side**:

- **Billing-period-to-date** — auto-aligned to the workspace's actual subscription cycle (read from `next_character_count_reset_unix` and stepped back one calendar month). For a workspace that renews on the 9th this is May 9 → today, NOT May 1 → today.
- **Last 7 days** — rolling 7d ending now.

…plus a **Subscription** block on top showing the plan tier, monthly cost (from `next_invoice.amount_due_cents`), renewal date, live `current_overage.amount` if non-zero, and quota-used-so-far as a fraction. The only `$` figures in the rendered report are the actual monthly plan cost and `current_overage.amount` — i.e. what we actually owe — never the retail-equivalent figure.

### Why actuals only

Past versions of this skill projected end-of-period burn (`burn_per_day × period_days_total`) and converted projected-overage-credits into dollars via a credits/min ratio. Two problems with that approach:

1. **The projection is by definition a guess** about future burn. Straight-line extrapolation from current burn-per-day under-shoots ramp-ups and over-shoots tapering periods, and gives no signal at all about feature-launch spikes.
2. **The credits→dollars conversion depended on a credits/min ratio** that ElevenLabs derives from plan packaging (`character_limit / plan_minutes` ≈ 1,562.72 on Business) but doesn't actually use for our workload's billing. Empirical for Aurie's ConvAI is roughly half that, so the converted dollar figure was misleading.

The current skill reports only what has already happened. If you need a forward-looking signal, look at the **daily burn rate** the report already prints alongside each window — the user can extrapolate by eye, or run the skill repeatedly through the period to track the trajectory.

### Why we don't render `fiat_units_spent`

The Usage API returns `fiat_units_spent` per bucket — the consumption value of the credits burned, **returned in cents**. The script divides it by 100 (in `run_window`) so it reconciles within ~1% of the ElevenLabs dashboard "Cost before overage surcharge" tile — the same figure `eng:report-aurie-weekly-costs` uses for its voice column. It is still **NOT the invoice**: actual bill = flat plan price + any genuine `current_overage`. So the markdown report keeps hiding it (to avoid it reading as the bill), but the **corrected USD** value is preserved in the `--json` payload (under `cost_usd` / `total_cost_usd`) for downstream tooling. (Historical note: an earlier "\$87k consumption value while owing \$1,320" warning was the un-divided cents artifact — ÷100 that's ~\$870, comfortably under the plan floor. The cents bug was fixed in PROD-2477.) Surfacing the now-correct consumption $ in markdown — clearly labelled "consumption value, not the invoice" — is a reasonable follow-up.

A breakdown by **model** is included by default. Other breakdown axes (`voice`, `api_keys`, `user`, `product_type`, `source`) are available via `--breakdown`.

## Step 0 — Check prerequisites

```bash
if [ -n "$ELEVENLABS_ADMIN_API_KEY" ]; then
  echo "OK: admin key set"
elif [ -n "$ELEVENLABS_API_KEY" ]; then
  echo "WARN: only ELEVENLABS_API_KEY set — workspace totals may be limited to this key's scope"
else
  echo "MISSING"
fi
```

If both are missing the skill **cannot run**. Walk the user through setup:

1. **Pick the right key type.** For local Claude usage, use a **developer API key** on your own account — it's the simplest path. Switch to a service-account key only if this skill ever moves to CI or a shared environment.
   - Developer API key: <https://elevenlabs.io/app/developers/api-keys>
   - Service account API key (alternative, for non-personal use): <https://elevenlabs.io/app/developers/service-accounts>
2. **Required scopes** (whichever key type) — when creating the key, choose **Restrict Access** and grant exactly these — leave everything else at *No Access*:
   - **Workspace Analytics → Access** *(required — gates `/v1/usage/character-stats`, the core usage endpoint. Without it the call returns 401.)*
   - **User → Read** *(strongly recommended — gates `/v1/user/subscription`, which the skill uses to auto-derive the billing-period-to-date window, the plan name, the monthly cost, the quota meter, and the live `current_overage.amount`. Without it, the window default falls back to calendar MTD and the subscription block is dropped entirely.)*
   - **Workspace → Read** *(optional — only needed if you want `--breakdown api_keys` to show key **names** instead of raw IDs, which calls `/v1/workspace/api-keys`. Safe to omit otherwise; the script degrades gracefully to IDs.)*

   That's it — leave TTS, Voices, History, Agents, Projects, etc. all at *No Access*. The skill only reads usage analytics and subscription metadata; it never generates speech, lists voices, or hits anything else.
3. Store the key in **Claude global env only** — `~/.claude/settings.json` under the `env` block as `ELEVENLABS_ADMIN_API_KEY`. Hand off to the `update-config` skill to add it without clobbering existing entries.
4. The user must **restart Claude Code** for it to load — confirm before retrying.

If the user already has `ELEVENLABS_API_KEY` (used by `investigate-elevenlabs-conversations`), you can use it as a fallback, but it's likely scoped narrower (no `Workspace Usage Metrics`) and will under-count. Prefer setting `ELEVENLABS_ADMIN_API_KEY` properly.

Other prereqs: `python3` with `requests`. Install with `pip3 install requests` if the script errors on import.

## Step 1 — Resolve the time window(s)

**Default**: produce the **combined view** — billing-period-to-date **and** last 7 days in a single report. This is what the user almost always wants for an "are we on budget?" check, and the billing-period anchor matches the UI's quota meter exactly.

If the user explicitly asks for a single window, honour it:

- **Billing-period** *(default)* — auto-aligned to the subscription cycle (renewal date - 1 month → now)
- **Calendar MTD** — 1st of current calendar month at Pacific midnight → now (use this only when the user explicitly asks for the calendar view, e.g. for cross-tool consistency with `eng:analyze-openai-costs`)
- **Previous month** — full prior Pacific calendar month
- **Last 7 days** — rolling 7d ending now
- **Last 30 days** — rolling 30d ending now
- **Custom range** — explicit `--since YYYY-MM-DD --until YYYY-MM-DD` (Pacific midnight, half-open)

All Pacific-anchored windows match the ElevenLabs dashboard (which uses viewer's browser TZ) and the `report-aurie-weekly-costs` voice column. See `Tools/ElevenLabs` § Timezone anchoring for the rationale.

If the user names a month directly ("April", "2026-04"), translate it to a `--since` / `--until` pair covering that full calendar month.

## Step 2 — Run the report

### Combined view (default)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-elevenlabs-costs/scripts/report.py \
  --combined \
  --breakdown model \
  --json /tmp/elevenlabs_costs.json
```

`--combined` runs `billing-period` (auto-derived from the subscription) and `last-7d` back-to-back, prepends the subscription block (plan, quota usage, live overage if any), and merges the two reports into a single markdown output. Use this as the default. If the key lacks `User: Read` scope and the subscription can't be fetched, `--combined` falls back to `calendar-mtd` + `last-7d` and drops the subscription block.

### Single-window forms

```bash
# Billing-period-to-date (default)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-elevenlabs-costs/scripts/report.py --window billing-period

# Calendar month-to-date (use for cross-tool consistency)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-elevenlabs-costs/scripts/report.py --window calendar-mtd

# Previous calendar month
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-elevenlabs-costs/scripts/report.py --window previous-month

# Rolling 30 days
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-elevenlabs-costs/scripts/report.py --window last-30d

# Custom range
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-elevenlabs-costs/scripts/report.py \
  --since 2026-04-01 --until 2026-05-01
```

### Other flags

- `--breakdown {model,voice,api_keys,user,product_type,source,none}` — secondary group-by. Defaults to `model`. Pass `none` to skip.
- `--no-zero` — suppress rows with 0 credits in this window.
- `--json <path>` — write structured JSON alongside the markdown. The JSON retains `credits`, `cost_usd` (`fiat_units_spent` ÷ 100 → real USD), and per-bucket breakdowns. Useful for any downstream tooling. Note: the `raw` field holds the unmodified API response, where the cost metric is still in **cents**.

The script prints a formatted markdown report on stdout and (with `--json`) writes structured data.

## Step 3 — Show the report inline

Show the script's stdout directly so the user can see both views immediately. Lead with a one-line headline that pairs the two windows, e.g.:

> _"Aurie has consumed **10.7M credits** this billing period so far (~1.61M/day) with **11.1M credits** in the last 7 days (~1.58M/day) — `convai_v0` is 99.4% of weekly volume. Quota usage is 49.7% with no overage so far on the \$1,320 plan."_

If the same model or voice dominates both windows, call it out. If the weekly daily run-rate is materially above the billing-period daily average, flag that — it suggests an acceleration worth investigating. The only dollar amounts you should quote are the plan cost and `current_overage.amount` from the subscription — never the retail-equivalent figure, and never a projection.

## Step 4 — Ask where to route it

After the report, ask the user what to do next:

1. **Post a summary to Slack** *(optional)* — compact message with the two-window headline + per-model totals, then **invoke [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** to send it to `#eng-budget` (`C05PC18APLH`, canonical ID in `Tools/Slack`); pass `--footer eng:analyze-elevenlabs-costs --footer-extra prod` so it stamps the versioned footer (don't hand-type one in the body). That skill owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback) and the unfurl-off default — don't call Slack directly here. If it reports neither Slack MCP nor a token is available, tell the user and skip — don't treat Slack as a prerequisite.
2. **Save a Markdown summary** to a path the user provides.
3. **Nothing, just show it** — stop here.

Do NOT route automatically; always wait for the user's pick.

## Slack message format (option 1)

Use this shape, substituting the right numbers:

```markdown
**ElevenLabs Spend — billing-period-to-date + last 7 days** (actuals only)

- **Plan:** <tier> ($<plan_monthly>/mo) · renews <renewal_date>  · *include `current overage: $<overage>` only when non-zero*
- **Quota used so far:** <used> / <limit> credits (<used_pct>% used)
- **Billing period to date:** <bptd_credits> credits across <days_in> days (~<bptd_daily>/day)
- **Last 7 days:** <wk_credits> credits (~<wk_daily>/day)
- **Run-rate signal:** weekly daily-avg is <pct>% of BPTD daily-avg → <on pace / accelerating / slowing>

**Top models (last 7 days)**

| Model | Credits | Share |
|---|---:|---:|
| <model> | <n> | <pct>% |
| ...top 5... |

**Headline**

<one paragraph: which model is the standout, supporting credit-volume evidence, hypothesis>

_Caveat: actuals only — no end-of-period projection._

ElevenLabs usage dashboard: https://elevenlabs.io/app/usage
```

Notes:

- Send this message by **invoking [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** with channel `#eng-budget` (`C05PC18APLH`) and `--footer eng:analyze-elevenlabs-costs --footer-extra prod` — the helper stamps the versioned footer; it owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback) and the unfurl-off default. Don't put a footer in the body or call Slack directly here.
- Cap the top-models table at 5 rows. Below ~10k credits absolute volume per row, signal is too noisy.
- For `previous-month` runs, drop the run-rate line — the window is closed.
- The only `$` figures are the plan cost and current overage. Don't quote retail-equivalent dollars anywhere in the Slack post.

## Notes & edge cases

- **Consumption-value USD is hidden from markdown by design, but correct in JSON.** ElevenLabs returns both `credits` and `fiat_units_spent` per bucket; `fiat_units_spent` is the consumption value **in cents**, divided by 100 by the script → `cost_usd` / `total_cost_usd` in the JSON. It reconciles within ~1% of the dashboard "Cost before overage surcharge" tile but is **not** the invoice (= plan price + `current_overage`), so the markdown shows credits only and surfaces the real cost in the subscription block. The `raw` field keeps the unmodified API response (cost still in cents). Fixed in PROD-2477; previously the JSON reported cents-as-dollars (100× high).
- **Workspace vs personal scope.** ElevenLabs gates cross-workspace usage numbers behind the **Workspace Analytics: Access** scope on the API key (developer or service-account). A key without that scope returns 401 on `/v1/usage/character-stats`. If the totals look suspiciously low compared to the ElevenLabs dashboard, the key was probably created without it — re-issue with the right scope rather than trying to guess from the data.
- **API key names.** `breakdown_type=api_keys` returns key IDs, not names. The script does a best-effort second call to `GET /v1/workspace/api-keys` to map ID → name — that endpoint needs **Workspace: Read**. If the call 401/403s, keys are surfaced by ID with no footnote (graceful degrade).
- **Numbers may lag the dashboard by minutes.** The Usage API aggregates with a small delay. Mention this if the user is comparing to a value they just saw in the ElevenLabs UI.
- **Currency.** The plan-cost and overage amounts the report renders are USD (read from the subscription's `next_invoice` and `current_overage`). If ElevenLabs ever returns another currency it would surface in the response — the script does not currently translate.
- **Rate limits.** The Usage API is generously rate-limited but the script still retries with exponential backoff on 429.
- **Aggregation interval.** The script uses `aggregation_interval=day` and sums client-side so windows that span partial days behave correctly. `cumulative` is not used because we want the per-day totals available if a follow-up call wants a daily run-rate chart.
- **`investigate-elevenlabs-conversations` uses `ELEVENLABS_API_KEY`.** That key may also work here as a fallback, but it's typically a per-agent key without workspace-usage scope, so the totals it returns will under-count. Prefer `ELEVENLABS_ADMIN_API_KEY`.
- **Per-user drill-down lives in a sibling skill.** This skill answers the workspace-level question ("how much have we burned? are we already over?") using the aggregate Usage API. For the per-Firebase-UID question ("who's driving cost? which users are heavy?"), use [`eng:analyze-elevenlabs-cost-per-user`](../analyze-elevenlabs-cost-per-user/SKILL.md) — it walks the Conversational AI conversations endpoint and aggregates by the `userId` dynamic variable our integration sets on every call. The two reports are intentionally separate: this one is fast (~2 API calls) and answers budget questions; the per-user drill-down is slow (one detail fetch per conversation, cached) and answers attribution questions.
- **No projection.** Every number on the report reflects what has already happened — no end-of-period extrapolation, no estimated overage cost. See "Why actuals only" up top for the rationale.

See [references/elevenlabs-usage-api.md](references/elevenlabs-usage-api.md) for the raw response shapes the script handles.
