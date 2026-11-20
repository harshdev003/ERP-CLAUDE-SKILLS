# Scoring detail (Phase 4 — Score)

The full scorer registry, per-scorer grading semantics, and the report-contents layout for the qa-prompt scoring phase. The core Phase 4 steps (pick a scorer, run `self_critique.py`, write the report) stay in SKILL.md; this file is the lookup detail behind them.

## Scorers are a guide, not the verdict

Before running anything, internalize how scores are meant to be used. The GPT
rubrics are a **first-pass signal, not the source of truth.** Two standing rules:

- **`expected` columns are never exact-match ground truth.** Aura's prompts are
  judgment tasks (memory sorting, classification, intent routing) — the dataset
  `expected` is one plausible answer among several a careful reviewer would
  accept, not the only correct output. Grade on whether the output is *defensible*
  given **(a) the prompt's own written rules** and **(b) the testing goals the
  user described at setup** — using `expected` only as a reference example. A
  divergence from `expected` is a problem only when it violates the prompt's rules
  or the stated goal, never just because it differs. (The `structured-plan-match`
  scorer is built around this; see its registry row.)

- **Always sanity-check the scorer, and read the prompt yourself.** A scorer score
  is never a substitute for examining the prompt and reasoning about whether the
  output is actually good. After a scoring pass:
  1. Spot-check a few graded rows against the scorer's rationale. If it flagged a
     *defensible* judgment call as an error (or rubber-stamped a real defect),
     the rubric is miscalibrated — say so in the report and **suggest a concrete
     revision** to the scorer template (`scorers/<slug>.md`) rather
     than reporting the number at face value.
  2. Anchor your own qualitative read on the prompt's written intent and
     the user's goals — the scorer is input to that judgment, not a replacement
     for it.

## Scorer registry

Templates live in [`scorers/`](scorers/).

| Slug | When to use | Output shape |
|---|---|---|
| `aurie-conversation` *(default)* | Drives that test an Aurie dialogue prompt (coach, companion, chat, journal). 4-axis rubric across `in_character`, `tool_usage`, `coherence`, `brand_voice` plus a one-paragraph summary. | `{in_character, tool_usage, coherence, brand_voice, summary}` |
| `candidate-selection` | Memory-extraction prompts. Did the model pick the right memory-worthy candidates from the recent analysis window, without storing low-value residue? | `{score, rationale, failure_tags}` |
| `granularity-compression` | Memory-extraction prompts. Is each memory stored at the right unit of personalization — neither over-atomized nor over-compressed into a transcript summary? | `{score, rationale, failure_tags}` |
| `object-quality` | Memory-extraction prompts. Are the memory objects well-formed and retrieval-friendly (`memory`, `recall`, `type`, `expiryAt`)? Includes type-assignment + timestamp checks. | `{score, rationale, failure_tags}` |
| `redundancy` | Memory-extraction prompts. Internal overlap check — does the output duplicate the same user reality across multiple memories? | `{score, rationale, failure_tags}` |
| `structured-plan-match` | Any prompt whose final assistant message is a JSON object — memory-sorting plans (`{delete, store}`), classifiers, intent routers, decision-output prompts. Judgment-graded: decides whether the output is a *defensible* result against the prompt's own written rules + the run's stated testing goals, using `state.dataset_row.expected` as ONE reference example (not exact-match ground truth). Penalizes objective defects only (information loss, stale duplicates, dropped memory-worthy candidates, spurious stores, recall not phrased per the prompt's spec); accepts valid enum/type/paraphrase/count variation. | `{score, valid, rationale, failure_tags}` |

## Running all four memory scorers

Each `self_critique.py` call appends to `state.critiques[<slug>]` and mirrors the most recent grade to `state.critique` for back-compat. The report renders every scorer that was run, so the user gets a single document with all chosen rubrics side by side. Example loop for all four memory scorers:

```bash
for s in candidate-selection granularity-compression object-quality redundancy; do
  python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/self_critique.py \
    --state-file <state.json> --scorer "$s"
done
```

## Per-scorer grading semantics

The memory scorers evaluate the model's *output* (the memory objects it
emitted as the final assistant text or via a tool call). They assume the
prompt's job is to produce memory objects from the conversation in
`state.messages` / `state.turns`. If the prompt emits memories via a tool
call, set that tool's default to `auto-mock` or `canned` so the call
succeeds and the arguments end up in the turn record.

The `structured-plan-match` scorer is different: rather than scoring a
conversation, it parses the model's final assistant text as JSON and judges
whether it is a **defensible** result for the prompt. It does NOT diff
field-by-field against `state.dataset_row.expected`. Instead it grades against
the prompt's own written rules (it's given the full, untruncated system prompt)
and the testing goals from the run summary, treating `expected` as one
reference example — not ground truth. Use it for any prompt whose output is a
structured plan or decision — the Memory Sorting Prompt's `{delete, store}`
plan, a classifier's tag, an intent router's branch choice, etc. It penalizes
objective defects only (information loss, stale duplicates, dropped
memory-worthy candidates, spurious stores, recall not phrased per the prompt's
spec, fabricated content, invalid JSON) and explicitly accepts valid
enum/type/paraphrase/count variation. It returns `valid` (would a careful
reviewer accept this?) alongside `score`. If a row has no `expected`, it still
grades on the prompt's rules rather than refusing.

## Standalone transcript

To produce the standalone conversation transcript on its own (e.g. for a partial run you don't want a full report for), run `write_transcript.py`:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/write_transcript.py \
  --state-file <state.json> \
  --output-dir outputs \
  --label <optional-variant-or-scenario>
```

It prints `TRANSCRIPT: <path>`.

## Report contents

`write_report.py` emits `outputs/qa-prompt-<slug>-<timestamp>.md` (or
`outputs/qa-prompt-<slug>-<label>-<timestamp>.md` with `--label`) with:

1. Header — prompt URL, model, params, mode, scenario, context source, user_id.
2. Resolved context — seed message count, contextInstructions presence, warnings.
3. Conversation transcript — paired user/assistant turns in a readable format (this is what you'd skim first).
4. Tool registry + chosen defaults — what the prompt declared and how each tool was answered.
5. Rendered messages — full OpenAI-shape history (system + tool messages included).
6. Run timeline — turn by turn, including tool calls/responses and simulated user turns.
7. Token usage — per-turn prompt / cached / completion / reasoning / total tokens (from OpenAI's `usage` block) + cache hit rate.
8. Final assistant message.
9. GPT scorer grades — one block per scorer that was run against this drive (4-axis table for `aurie-conversation`; single-score table + failure tags for the memory scorers).
10. Claude analysis (from `--analysis-file`).
11. Sources.
