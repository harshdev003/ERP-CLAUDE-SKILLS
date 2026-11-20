---
name: report-intelligence-prompters
description: >
  Prints a single markdown table of every prompt defined in IntelligenceService's
  `src/config.js` with columns Prompter, API, Model, verbosity, reasoningEffort and
  temperature, plus a summary block counting how many prompts are on the OpenAI Response API
  vs Chat Completions vs unset. Answers "where are we?" on the per-prompt rollout without
  grepping config.js. Reads the live config module directly rather than by regex, so the table
  reflects whatever is currently on disk. Trigger on "report intelligence prompters", "list
  prompts and their API", "show intelligence service prompt config", "which prompts are on
  response API", "audit intelligence prompters", "show prompter table", "what API is each
  prompt using", "what model does each prompt use", "intelligence service prompt inventory",
  or "where are we on the response API rollout". Do NOT trigger for setting or modifying a
  prompt, or for OpenAI cost analysis (`eng:analyze-openai-costs`).

metadata:
  version: "3.0.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
---

# Report Intelligence Prompters

## Goal

Produce a single, deterministic snapshot of the prompt inventory in IntelligenceService — one row per prompt, alphabetical, with the fields that matter for the Response API rollout (ENGBACK-1043):

- **API** — `openaiAPI` field value: `responses`, `chatCompletions`, or `(unset)`.
- **Model** — the OpenAI model id (or `gemini-…` for non-OpenAI prompts).
- **verbosity**, **reasoningEffort**, **temperature** — the tuning knobs.

After the table, print a summary block:

- count on Response API
- count on Chat Completions
- count unset (where `openaiAPI` is absent — these fall through to the runtime default in `src/services/openai.js`)
- count non-OpenAI (e.g. `score` runs on Gemini and is listed separately so it's clear it's not missing)

## How it works

The skill ships a tiny Node script ([scripts/report.js](scripts/report.js)) that `require()`s the IntelligenceService `src/config.js` module directly and iterates `config.prompt`. No regex parsing — the table reflects the actual exported config, so it stays correct across refactors of the config file's formatting.

`config.js` destructures `process.env` at the top and only enforces required keys inside `config.validate()`, which the script never calls. Loading the module with no env set is therefore safe — empty / undefined fields just stay undefined, and we never touch postgres / redis / pubsub etc.

## Workflow

1. Resolve the IntelligenceService checkout path:
   - `process.env.INTELLIGENCE_SERVICE_PATH` if set, otherwise
   - `~/Development/Aura/Services/IntelligenceService`.
2. Confirm `src/config.js` exists at that path. If not, fail with a clear "run this from a machine with the IntelligenceService checkout, or set `INTELLIGENCE_SERVICE_PATH`" message.
3. Run:
   ```bash
   node plugins/eng/skills/report-intelligence-prompters/scripts/report.js
   ```
   (or from the skill folder: `node scripts/report.js`)
4. The script prints the markdown table and the summary block to stdout. Paste it directly into chat, a PR comment, or the ENGBACK-1043 ticket.

## Output shape

```
| Prompter | API | Model | verbosity | reasoningEffort | temperature |
|---|---|---|---|---|---|
| `assistantGuardrail` | responses | gpt-5.4-nano | — | — | 1 |
| `chat` | chatCompletions | gpt-5.4-mini | — | — | 1 |
| ...

### Summary
- On Response API: 11
- On Chat Completions: 11
- Unset (fall through to runtime default): 0
- Non-OpenAI: 1 (`score` — gemini-2.5-flash)
```

`—` is used for fields that don't exist on a given prompt (most prompts don't set `verbosity` or `reasoningEffort`).

## Failure modes

- **No checkout found** — script prints the path it tried and exits non-zero. Set `INTELLIGENCE_SERVICE_PATH` or clone the repo.
- **Config require() throws** — usually means the IntelligenceService config has a hard dependency at module load (new `require` of something not in the repo). Surface the error verbatim; do not catch and swallow.

## Out of scope (for v1)

- **Slack auto-post** — the v1 prints inline only. A future revision can take `--slack-channel` and route the table to `#eng-budget` or similar.
- **Diff vs previous snapshot** — could compare two checkouts (e.g. master vs a feature branch) to show what's changing in a PR. Out of scope for v1.
- **Filtering** — could take `--api responses` to show only Response-API prompts, or `--tier` to group. Out of scope for v1; the full table is short enough to scan.

## Why a script, not LLM parsing

`config.js` is a regular Node module with destructuring, defaults, and computed keys. A regex-based parser would drift the first time someone reformats the file. `require()`ing the module gives us the exact same object the running service sees — the report is by definition correct.
