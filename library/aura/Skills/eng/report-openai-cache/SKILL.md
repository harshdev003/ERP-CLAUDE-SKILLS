---
name: report-openai-cache
description: >
  Report an Aura prompter's OpenAI prompt-cache rate and what it cost, day by day: one row per
  UTC day since a date plus a Total, with requests, input tokens, cached share, the input cost,
  the cost at a baseline cached share, the difference and tokens per request. Reads the
  IntelligenceService LLM-call ledger (BigQuery llm.llm_call), read-only. Trigger on "report the
  OpenAI cache", "daily cache cost", "what did the cache misses cost", "prompt cache cost since
  October", "cache rate and dollars by day", "coach cache cost table", or "how much did caching
  save". Do NOT use to find why a prompter misses the cache (eng:diagnose-prompt-cache), for the
  whole OpenAI bill (eng:analyze-openai-costs), or for raw token counts (eng:analyze-openai-tokens).
metadata:
  version: "1.1.1"
knowledge:
  reads:
    - Guide/Index        # baseline routing aid (universal)
    - Tools/OpenAI       # the model's input and cached-input rates, passed to the script
    - Tools/Aura API     # LLM-call ledger semantics (read from BigQuery llm.llm_call)
    - Tools/BigQuery     # the ledger table and GCP_KEY access
---

# Report OpenAI Cache

## Goal

Show, per UTC day, how much of a prompter's input OpenAI served from the prompt cache and what
that cost against a baseline cached share. This skill is about **rate and dollars over time**;
for **why** misses happen, use `eng:diagnose-prompt-cache`.

## Inputs

- **Since** (required): a UTC date or timestamp, e.g. `2026-10-01`. **Until** defaults to now.
- **Prompter**: the ledger's `prompterName`; default `coach-prompter`.
- **Baseline**: the cached share in percent to compare against, e.g. the share before a known
  change. Ask if the user didn't give one; don't guess it.
- **Rates**: the model's input and cached-input prices per 1M tokens, from `Tools/OpenAI`
  (canonical). Never type them from memory: read the page, and say which model row you used.

## Step 0 — Prerequisites

`GCP_KEY` (the ledger is BigQuery `aura-main.llm.llm_call`). No OpenAI key is needed.

## Step 1 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-openai-cache/scripts/report.py \
  --since 2026-10-01 --prompter coach-prompter --baseline 88.96 \
  --uncached-per-m <input rate> --cached-per-m <cached-input rate>
```

`--json <file>` also writes the rows. The days are summed in BigQuery, so a long window is one
small query.

Columns, one row per UTC day and a bold **Total** row:

| Column | Meaning |
|---|---|
| Day | UTC date |
| Requests | ledger rows (calls) that day |
| Input (M tokens) | input tokens, millions |
| Cached % | cached ÷ input tokens |
| Input cost | (input − cached) × uncached rate + cached × cached rate |
| Cost at baseline % | the same input at the baseline cached share |
| Difference | input cost − cost at baseline (positive: it cost more than the baseline) |
| Tokens per request | input tokens ÷ requests |

## Step 2 — Report

Lead with the Total row: the cached share, the dollar difference against the baseline, and the
days that moved it most. Name the rates and the `Tools/OpenAI` row they came from. If the cached
share dropped, offer `eng:diagnose-prompt-cache` for the cause.

## Edge cases

- **No rows**: the script exits saying so. Check the `prompterName` spelling; it is exact-match.
- **Today is partial**: the last row runs to `--until` (default now). Say so when it is in the table.
- **A model change inside the window**: one rate pair prices every day. If the prompter's model
  changed, split the window at the change and run once per model.
