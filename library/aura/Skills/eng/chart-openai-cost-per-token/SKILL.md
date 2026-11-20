---
name: chart-openai-cost-per-token
description: >
  Chart what Aura actually pays per token on OpenAI, one bar per day: billed dollars from the
  Costs API divided by tokens from the Usage API, as $ per 1M tokens. Scope it to one model
  (for example chat-latest), optionally one project, or the whole org. Pass --highlight-from
  to colour the days from a change onward in red against the earlier average, and get the
  extra spend those days cost. Use it to spot a cache collapse, a price change or a model
  swap, since the effective rate jumps even when volume is flat. Trigger on "chart cost per
  token", "cost per 1M tokens over time", "effective token price per day", "did our price per
  token change", "chart chat-latest cost per token", "cost per token since the cache broke",
  or "show the blended token rate daily". Not for per-model rate tables
  (`eng:analyze-openai-costs --by-model`), project spend totals
  (`eng:chart-openai-project-cost`), token counts without dollars
  (`eng:analyze-openai-tokens`), or why a prompt misses the cache (`eng:diagnose-prompt-cache`).
metadata:
  version: "1.0.0"
knowledge:
  reads:
    - Guide/Index    # baseline routing aid (universal)
    - Tools/OpenAI   # Admin key, project ids, Costs/Usage API notes, prompt-caching incident
    - Tools/Slack    # only when posting the chart (via shared:post-to-slack)
---

# Chart OpenAI Cost per Token

One number per day: **what we paid per 1M tokens**. Billed dollars for the scope (every Costs
API line item: cached input, input, output, and any cache-write or other line item) divided
by all tokens for the same scope (input including cached, plus output).

Cost and tokens move together when traffic changes, so their ratio stays flat. It only moves
when **what each token costs** changes:
- the share of input served from cache drops (cached input is billed at 10% of uncached)
- a price change
- a model swap
- a shift between input-heavy and output-heavy traffic

Built for PROD-7290 (Oct 2026). Coach `chat-latest` held at ~\$1.14 per 1M tokens for weeks,
then hit \$2.88 on Oct 8 when OpenAI's prompt cache broke. That was the clearest single view of
the incident.

## Step 0 — Prerequisites

```bash
test -n "$OPENAI_ADMIN_API_KEY" && echo "OK: admin key" || echo "MISSING: OPENAI_ADMIN_API_KEY"
python3 -c "import requests, matplotlib" && echo "OK: python deps" || echo "pip3 install requests matplotlib"
```

There is no MCP for OpenAI's Admin `/v1/organization/*` endpoints, so the key is a hard
requirement. If it's missing, stop and point the user at the Admin key setup in
`Tools/OpenAI`.

## Step 1 — Resolve the scope with the user

| Input | Default | Notes |
|---|---|---|
| `--model` | all models | As OpenAI reports it: `chat-latest`, `gpt-5.4-2026-03-05`, … Matches the Costs line-item prefix and the Usage `model` field exactly |
| `--project` | whole org | Name (exact or unique substring) or `proj_…` id |
| window | last 30 full UTC days | `--days N`, or `--since`/`--until` (until is exclusive) |
| `--highlight-from` | none | Date the change started. Days from it are red, earlier days give the baseline average |

If the user names a prompter rather than a model ("coach"), map it first. The prompter → model →
project map is in `Tools/OpenAI` (coach = `chat-latest` in Conversation [Production]).

## Step 2 — Run

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/chart-openai-cost-per-token/scripts/cost_per_token.py" --model chat-latest --project "Conversation [Production]" \
  --days 30 --highlight-from 2026-10-07 --out /tmp/cost_per_token.png --json /tmp/cost_per_token.json
```

It prints a daily table (cost, tokens, % of input cached, $/1M) and writes the PNG. With
`--highlight-from` it also prints the extra spend: billed dollars from that date against the
same tokens at the baseline rate.

Other scopes:

```bash
# Every model in one project, combined into one bar per day
python3 "${CLAUDE_PLUGIN_ROOT}/skills/chart-openai-cost-per-token/scripts/cost_per_token.py" --project "Conversation [Production]" --highlight-from 2026-10-07
# One line per model (top 6 by spend): shows which model moved while the others stayed flat
python3 "${CLAUDE_PLUGIN_ROOT}/skills/chart-openai-cost-per-token/scripts/cost_per_token.py" --by-model --project "Conversation [Production]" --highlight-from 2026-10-07
python3 "${CLAUDE_PLUGIN_ROOT}/skills/chart-openai-cost-per-token/scripts/cost_per_token.py" --by-model --top 8            # whole org
```

Use `--by-model` when the question is "is it one model or all of them". Models sit at very
different rates (e.g. `gpt-5.4-mini` ~\$0.30 vs `gpt-5.5` ~\$7 per 1M in Oct 2026), so read
each line against its own history, not against the others. With `--by-model`, the
`--highlight-from` date is drawn as a red line, and no extra-spend figure is printed.

## Step 3 — Report

Show the chart. Lead with the baseline rate, the peak day and rate, and the extra spend if
highlighted. Add the cached-input % column when the rate moved, because the cache share is
usually the reason.

Caveats to state when relevant:
- **The Costs API lags about a day**, so today and often yesterday are missing. Days without
  both cost and token data are dropped, not shown as zero.
- **"Extra spend" here differs from a cache-only estimate.** This one prices every token at the
  baseline blended rate. A cache-only estimate (uncached input above the baseline hit rate ×
  \$4.50/1M) isolates the cache effect. Expect them to differ by a few percent when the
  output/input mix also moved.
- **Every service tier is included.** Flex and priority line items carry a tier prefix
  (`flex | gpt-5.2-…, input`) that the script strips. A model moving between tiers shifts its
  rate on its own (flex is about half price), so check the tier mix before blaming caching.
- **Aggregate dollars and token counts only.** No user, key or prompt data is read, so the
  output is safe to share.

Posting the PNG to Slack: compose `shared:post-to-slack` and use its `upload` subcommand
(`--channel`, `--file`, `--comment`, `--thread-ts`), always with
**`--footer eng:chart-openai-cost-per-token --footer-skill-md <this skill's base directory>/SKILL.md`**.
The helper only stamps the versioned footer when those flags are set; never type the footer
into the comment. Afterwards, confirm the footer reads `eng:chart-openai-cost-per-token:<version>`.
Post into a thread as a plain reply unless the user asks for it to go to the channel too.
