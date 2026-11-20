---
name: compare-conversations
description: >
  Compares Aurie conversations between groups (prompt variants, experiment arms, models) from
  a live prompter experiment, a dataset run against OpenAI, or QA comparison runs: metrics,
  reply style against the change's goals, and a blinded review. Read-only. Trigger on "compare conversations", "compare the prompt arms",
  "compare a and b", "compare these two prompts", "compare the dataset run", "compare the
  OpenAI runs", "which prompt is better", "is the new prompt better", "how do the arms
  compare", "check the prompt experiment", "check all users on a and b", "blind review the
  conversations", "any difference between the variants", "monitor the prompt A/B", or an
  experiment name with a request to compare. Do NOT use for Mixpanel conversion or
  retention readouts (data:analyze-experiment), one user's conversations
  (data:analyze-user-conversations), or running the simulated QA drive itself
  (eng:qa-conversation / eng:qa-prompt).
metadata:
  version: "1.2.1"
knowledge:
  reads:
    - Guide/Index                          # baseline routing aid (universal)
    - Compliance/Privacy                   # PII rule — no user text, names or identifying facts in any output
    - Tools/Postgres/IntelligenceService   # experiment source: read-only prod access via kubectl port-forward; dialogue / llmCall / userExperiment shapes
    - Tools/Aura API/LLM Calls             # experiment source: llmCall semantics (one row per OpenAI call, none on failure)
    - Tools/OpenAI                         # openai-responses source: stored responses, project-scoped keys
---

# compare-conversations

Compare conversations between groups and say whether a change is doing what it was meant to.
A **group** is whatever is being compared — `a`/`b` arms of a live experiment, a new prompt vs
the current one replayed on a dataset, two models. A **source** is where the conversations come
from. Sources only differ in how they load; everything after loading is the same.

It produces evidence, never a verdict. In the Aurie Conversations loop it serves both sides of a
release: before (dataset or QA runs, with `product:evaluate-prompt-candidate`) and after
(live arms, before `product:learn-prompt-candidate`).

Read [`references/interpreting.md`](references/interpreting.md) before writing the report — it
holds the data facts (tool-call turns, joins, context turns, exclusions) that change how numbers read.

## Inputs

Resolve these first; ask for any that are missing.

| Input | Notes |
|---|---|
| Source and its locator | see the table below |
| Group names | which is the change and which is the baseline (Aurie experiments: arm `b` is control) |
| **Goals of the change** | always ask — the style comparison is measured against them |
| Users to exclude | experiment source only: internal / QA accounts forced into an arm |
| Review size | default 6 conversations per group × channel, or 20 pairs |

## Sources

All loaders write `{scratch}/conversations.jsonl` in the format in
[`references/conversation-format.md`](references/conversation-format.md). `S=scripts/compare.py`.

| Source | When | Command | Paired? |
|---|---|---|---|
| `experiment` | live IS prompter experiment, real users | `python3 $S load experiment --experiment coachPrompterWarmth --exclude-user {uid} --out-dir {scratch}` | no |
| `openai-responses` | a dataset run directly against OpenAI with `store: true` | `python3 $S load openai-responses --group new=new_ids.txt --group current=current_ids.txt --api-key-env {KEY_ENV} --out-dir {scratch}` | yes, via the optional TAB pair key per id |
| `qa-transcripts` | an `eng:qa-conversation` multi-turn run (`transcripts.md`) | `python3 $S load qa-transcripts --file transcripts.md --out-dir {scratch}` | yes, by scenario |
| `qa-state` | `eng:qa-prompt` comparison runs (one `state.json` per variant per run) | `python3 $S load qa-state --group a='runs/*/a/state.json' --group b='runs/*/b/state.json' --out-dir {scratch}` | yes, by run directory |
| `jsonl` | anything already normalized | `python3 $S load jsonl --file convs.jsonl --out-dir {scratch}` | if it carries `pair_key` |

Source prerequisites:

- **experiment** — the prod IS Postgres port-forward on `127.0.0.1:5432` and `psql`. Connection:
  `IS_DATABASE_URL`, else the last `POSTGRES_DATABASE_URI` in the IS `.env` (`--env-file`). See
  `Tools/Postgres/IntelligenceService`. Read-only. The `llmCall` side (latency, tokens, platform)
  comes from BigQuery `aura-main.llm.llm_call` with `GCP_KEY` (ENG-1657), joined here on `dialogueId`.
- **openai-responses** — the response ids per group (one per line; optional `TAB pair_key`, the
  dataset row id, to pair the same input across groups), and the key of the **project that stored
  them**: stored responses are project-scoped, and the admin key cannot read them. See `Tools/OpenAI`.

## Workflow

### Step 1 — Map goals to metrics

Turn each goal into the metric that would move, so the report can say met / not met / moved the
wrong way:

| Goal | Metric (`metrics`) | Review dimension |
|---|---|---|
| fewer "yeah" / interjection openers | `open_yeah`, `open_ah_mmm` | `opener_variety` |
| less affirm-then-question | `ends_question`, `two_plus_questions` | `question_pattern` |
| less directive | `directive` | `non_directive` |
| more relational / empathic | — | `relational_warmth`, `empathy` |
| longer / shorter arcs | reply chars (no-tool), replies per conversation | — |
| tracks only on request | `track_mention` | `tracks_on_request` |

### Step 2 — Load

Run the loader for the source (table above). Check its summary line: conversations per group,
and `with_pair_key` if you intend a paired review.

### Step 3 — Health (experiment source only)

```bash
python3 $S health --experiment coachPrompterWarmth --exclude-user {uid}
```

Stop and report before going further if an arm serves more than one prompt file or the base file
(fallback), if there are `<empty>` assignments, empty replies, or turns with no `llmCall` row.
Other sources have no serving layer to check; their failures show as empty replies in `metrics`.

### Step 4 — Metrics

```bash
python3 $S metrics --input {scratch}
```

Volume, latency and tokens (when the source carries them), length, and reply style per group ×
channel. Compare latency and length on the **no-tool** columns. A style metric moving against a
goal is the headline, not a footnote. Trust gaps that are large and consistent across channels.

### Step 5 — Blinded review

Pick the mode:

- **Paired** — the groups answered the **same inputs** (dataset rows, QA scenarios). Stronger:
  each item is judged side by side.
  `python3 $S sample --input {scratch} --out-dir {scratch} --paired --groups {change},{baseline}`
- **Single** — different users or inputs per group (live experiments).
  `python3 $S sample --input {scratch} --out-dir {scratch}`
  For live conversations add `--min-turns 8` so the reviewer sees real arcs.

Then:

1. Spawn a **fresh general-purpose subagent** with the matching prompt from
   [`references/reviewer-prompt.md`](references/reviewer-prompt.md), pointed at the transcripts
   file only. Never give it the key, the group names, or which side is the change. Do not read the
   transcripts yourself first — that unblinds the orchestrator.
2. Save its reply to `{scratch}/reviewer_scores.json`, then
   `python3 $S unblind --key {scratch}/blind_key.json --scores {scratch}/reviewer_scores.json`
3. Map the reviewer's clusters (single mode) onto groups using the key.

### Step 6 — Report

Lead with the answer — is the change doing what it was meant to? — then:

1. Health (experiment source) with a one-line verdict.
2. Latency / cost: no-tool vs tool turns and the tool-call share.
3. Style, each goal marked met / not met / wrong way.
4. Blinded result: mean scores and clusters (single), or win counts per dimension (paired).
5. Caveats: sample size, topic confound (single mode), keyword metrics are directional, source
   limits (no latency for dataset runs; simulated users for QA runs).
6. Incidental defects the reviewer saw — their own card, not part of the verdict.

Privacy — see `Compliance/Privacy`: no user text, no names, no identifying facts; assistant
phrasing only, under 15 words, names as `[name]`; users by UID prefix at most. Posting the report
anywhere is a separate, confirmed step.

### Step 7 — Clean up (always, even after a failure)

```bash
python3 $S cleanup --out-dir {scratch}
```

Also delete anything else in `{scratch}` that holds conversation text (e.g. id lists you wrote).

## Re-running

Loaders are read-only and recompute every run; re-run on a schedule while an experiment is live.
`--seed` changes the review sample.
