---
name: compare-prompt-versions
description: >
  Compare two or more Braintrust prompt versions over a whole Braintrust dataset and log
  one Braintrust experiment per version, so the Braintrust compare view shows per-row
  diffs and regressions. Scoring is in code by default: a tool-call scorer checks whether the
  prompt called a tool when the row's label says it should, and with the right argument
  (e.g. coach `handoffToGuardrails` + `category`), reporting recall, precision, category
  accuracy, a confusion matrix and the rows where versions disagree. An LLM-judge rubric
  scorer covers outputs code can't grade. Trigger on "compare these prompt versions on a
  dataset", "run a Braintrust experiment for this prompt", "A/B the coach prompt against
  production", "does the new prompt trigger the guardrail better", "eval the new prompt
  version on the dataset", or "compare prompt A vs B across the whole dataset". Do NOT
  use for a single conversation or persona run (`eng:qa-prompt`) or to ship a prompt
  (`eng:update-intelligence-prompt`).
metadata:
  version: "1.2.4"
knowledge:
  reads:
    - Guide/Index           # baseline routing aid (universal)
    - Tools/Braintrust      # prompt, dataset and experiment endpoints
    - Tools/OpenAI          # Evaluations project key, service tiers
---

# Compare Prompt Versions

Runs a Braintrust dataset through 2+ prompt versions and logs **one Braintrust experiment
per version** on that dataset. Claude plans the run, confirms it, executes one script, and
reads the results back. The model calls and scoring are deterministic code; Claude's job
is choosing the scorer and labels up front and interpreting the readout at the end.

## Prerequisites

- `BRAINTRUST_API_KEY` and `OPENAI_EVALUATIONS_API_KEY` in `~/.claude/settings.json` `env`.
  The OpenAI key is the `Evaluations [Production]` service-account key, so eval spend never
  lands on product projects ([ENG-1171](https://linear.app/aura-health/issue/ENG-1171)).
  Both are hard requirements: there is no MCP that runs a prompt version or writes an
  experiment.
- `python3 -m pip install --user openai requests`.
- OpenAI models only, on Chat Completions or the Responses API (`--api`). A Gemini or Claude
  prompt is refused at plan time.

## Step 1: Gather inputs

Ask only for what's missing:

1. **Prompt versions.** Each is `[label=]ref[@version]`, where `ref` is a Braintrust prompt
   URL, UUID or slug, and `@version` pins a `_xact_id` (from the prompt page's version
   dropdown). **The first one is the baseline.** For "what's in production", use the
   `[Production]` slug (the coach prompt is `coach-production` in `Conversation`). The run
   always records the exact version it fetched, so an unpinned ref is still reproducible.
2. **Dataset**: name or UUID, plus `--project` if it isn't in `Conversation`. If it doesn't
   exist yet (e.g. a Claude-built file of tagged rows), create and load it first with
   `data:save-to-braintrust-dataset --bulk-insert --create-if-missing`.
3. **Scorer.** Pick it from what the prompt is being judged on:
   - **`tool-call`**: the outcome is "did it call tool X, with argument Y". Needs `--tool`,
     optionally `--arg`, and `--label-field`, the dotted path to each row's label (default
     `expected`; e.g. `expected.category`, `metadata.route_expectation`).
   - **`llm-judge`**: the outcome is the reply's content. The rubric is each row's
     `expected` (or `--label-field`), and/or a `--rubric-file` applied to every row. A
     rubric names what the production reply did wrong and ends in explicit **PASS if** /
     **FAIL if** lines. When in doubt, prefer `tool-call`: it's free, exact and repeatable.
   - Rows carrying `metadata.issue` (or `--group-field`) get a **By group** table in the
     readout: the score per issue, so it shows which issues a candidate fixed.

Label vocabulary for `tool-call` (case and space/hyphen folded):

| Label | Means |
|---|---|
| a value, e.g. `crisis` | must call `--tool` with `--arg` = that value |
| `trigger` / `must_route` / `true` | must call `--tool`; argument not scored |
| `no_trigger` / `none` / `must_not_route` / `false` / empty | must not call `--tool` |

| `either` / `route_or_safe_in_conversation` | either outcome is fine: run and logged, counted, not scored |

If the tool's argument is an enum, a label outside it is recorded as a row error before any
model call, so a typo in the dataset can't read as a prompt regression.

4. **Production match.** For a prompt production runs, pass what production sets outside
   Braintrust: `--api` (the coach uses `responses`), `--params-json` (reasoning effort,
   verbosity, `top_p`) and `--tools-json` (production's tool definitions). Without them the
   experiment tests a different call. How to pull all three from a stored production response,
   and how to check the match: [`references/matching-production.md`](references/matching-production.md).
5. **Trials.** `--trials 3`–`5` for any decision; 1 is a smoke run.

## Step 2: Plan (dry run), then confirm

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/compare-prompt-versions/scripts/run_comparison.py \
  --prompt "prod=coach-production" --prompt "new=<url-or-slug>@<version>" \
  --dataset "<dataset>" --scorer tool-call \
  --tool handoffToGuardrails --arg category --label-field expected.category \
  --out <scratchpad>/compare-<slug> --dry-run
```

The plan JSON shows the resolved versions (`_xact_id`, model, params, tools), the API, the
params override, where the tools come from, trials, the row count, the **label
distribution**, `labels_outside_tool_enum`, and an upper bound on model calls. Show the user the versions, rows, label distribution and call bound, and **wait for
a go** before the real run: it spends tokens and writes experiments to the shared
Braintrust project. Stop if:

- a version lacks the target tool (the script refuses),
- `labels_outside_tool_enum` or `unreadable_labels` is non-zero (fix the dataset, or accept
  those rows being counted as errors),
- the label distribution has no `no_trigger` rows (then precision can't be measured; say so).

Use `--limit 5` for a smoke run when the dataset or prompt is new.

## Step 3: Run

The same command without `--dry-run`, plus `--name "<what is being compared>"` (the
experiment prefix; the default is `compare-<date>`). Per version, it:

1. Renders the prompt template against each row (`{{input}}` = the row's messages,
   `{{metadata.*}}` = the row's metadata).
2. Calls the model (`--api`) with the prompt's own model and params, `--params-json` merged
   over them, and `--tools-json` if given, on the run's service tier (flex
   by default; a model without flex runs on `auto` and the script says so once).
3. Answers any **non-target** tool call with `--stub` (default `{"status": "ok"}`), for up
   to `--max-hops` round-trips, and stops as soon as the target tool is called.
4. Scores the row and logs it as an experiment event linked to its dataset record
   (`dataset_record_id`). Large row metadata (`contextInstructions`) is not copied; the
   dataset row still holds it.

A row that errors is logged with `error` and counted under `Errors`. It stays in the
experiment rather than disappearing from it. Exit `3` means every row of a version failed.

## Step 4: Read the results

The script prints `summary.md` and writes `results.json` + `summary.json` to `--out`.
Report, in this order:

1. **Missed triggers** (recall) per version, and the line **New missed triggers in X vs
   baseline**. Treat each one as a potential missed crisis: open the row in the Braintrust
   compare view and read it, don't just count it.
2. **False alarms** (precision).
3. **Argument accuracy** and the confusion matrix (expected → actual). It's scored only on
   rows that should trigger **and** did, so a miss isn't counted twice.
4. **Rows where versions disagree**, with both outcomes.
5. Both experiment links; the second is already summarized against the baseline.

For a judge run, lead with the mean per version and the **By group** table, then the rows that
flipped. A judge score is a reading of one rubric by one model: open a few flipped rows in the
compare view before trusting it.

`summarize.py results.json [--baseline <label>]` re-renders the readout without re-running.

The run also writes **`prompt_diff.md`**: each candidate against the baseline (the system-message
diff, tools added or removed, params that differ).

## Step 5: Decide

The new version wins when it adds **no new missed triggers** and isn't worse on precision or
argument accuracy. A judge run alone never clears a coach candidate: run the crisis safety net
(`Weekly Conversation Guardrail Sentinel v1.0.0`, `--scorer tool-call --tool handoffToGuardrails
--arg category --label-field metadata.route_expectation`) on the same pair as well.

End with a **change summary** built from `prompt_diff.md`: what the candidate changed (a sentence
or two, plus the diff), what it fixed and by how much (per group), the safety-net line, and what it
did not fix. That is the hand-off. For a coach change it is a Conversations loop candidate's
source-cases evidence: attach it with `product:evaluate-prompt-candidate record CONV-n`; Gate 2
reads it there and `product:ship-prompt-candidate` ships the winner. Outside the loop, the
release goes to `eng:update-intelligence-prompt`. If it adds even one
new miss on a crisis-class row, report it as a blocker regardless of the aggregate numbers.
Confirm such a miss with more trials first: a single miss can be noise in either direction.

## Notes

- Same-version noise: running a version against itself (two labels, same ref) gives you the
  run-to-run flip rate. Do this before reading a small difference as a real one.
- With `--trials`, each row gets one experiment event per trial (all linked to the same dataset
  record), rates are per call, and "versions disagree" compares each row's majority outcome.
- Scripts: `run_comparison.py` (orchestration + Braintrust/OpenAI I/O), `scorers.py`
  (pure scoring), `summarize.py` (pure aggregation), `prompt_diff.py` (the change against the
  baseline). `braintrust_prompt.py`,
  `render_template.py` and `service_tier.py` are copies of `eng:qa-prompt`'s (canonical
  there; see `docs/canonical-sources.md`).

## Pairs with

- [`data:save-to-braintrust-dataset`](../../../data/skills/save-to-braintrust-dataset/) creates
  and fills the dataset.
- Datasets come from two procedures: guardrail alerts
  ([`data:analyze-conversation-guardrails`](../../../data/skills/analyze-conversation-guardrails/references/follow-ups/guardrail-dataset.md))
  and conversation ratings
  ([`data:investigate-user-rating`](../../../data/skills/investigate-user-rating/references/conversation-dataset.md)).
- [`eng:qa-prompt`](../qa-prompt/) is for one conversation or persona, driven turn by turn.
- [`eng:update-intelligence-prompt`](../update-intelligence-prompt/) ships the winner.
