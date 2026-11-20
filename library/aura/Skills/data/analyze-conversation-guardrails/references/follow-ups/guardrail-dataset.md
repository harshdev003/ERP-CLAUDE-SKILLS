# Follow-up: turn alerts into a guardrail eval dataset

**Question:** which alerts should become test cases for the coach prompt's guardrail, and does a candidate prompt fix them without missing real crises?

The flow ends in two Braintrust experiments: production vs a candidate on the new cases, and the same pair on a crisis safety net. Every step before the dataset write runs locally with [`scripts/reproduce.py`](../../scripts/reproduce.py). Its files hold conversation text, so they stay in `$WORK`. Only the `rows` output is de-identified, and it leaves the machine through `data:save-to-braintrust-dataset` after a confirmation.

`R="python3 $S/scripts/reproduce.py"`. Steps 1–2 of the main workflow come first: `alerts.json`, `turns.json` and the `$WORK/conversations` cache.

## 1. Verdict per alert

Judge each alert against the **coach prompt's own Guardrails section**: fetch the production prompt body and read it. Don't use the reason list for this; a reason says why it fired, not whether it should have. Write `$WORK/labels.json`, keyed by `ts`:

```json
{"<ts>": {"expected": "no_trigger", "pattern": "substance-historical", "note": "past drinking in a family story"},
 "<ts>": {"expected": "crisis", "pattern": "self-harm-current", "note": "current self-harm thought"}}
```

`expected` is `no_trigger` for a false positive, or the category production *should* have used. Leave ambiguous alerts out: a contested label measures the labeller, not the prompt. Safety fails toward keeping the guardrail: when in doubt on a crisis-class alert, it is a true positive.

## 2. Fetch (or rebuild) the production call

```bash
$R rebuild --cache-dir "$WORK/conversations" --out "$WORK/rebuilt.json" \
  --tools-out "$WORK/prod_tools.json" --params-out "$WORK/prod_params.json"
```

The llmCall ledger has one row per tool-call loop, under its last call, so the coach call that ends in a handoff has no row of its own. Each alert takes one of two paths, and prints which:

- **`exact`**: the guardrails handler's row has a `callChain` (Intelligence Service 1.32.2 on). The coach call is its last `coach-prompter` entry before the handler, fetched from OpenAI as it was sent. Its system message is the one that fired, so it prints `0s old`. The category production chose is read off the call itself, so `replay` and `rows` don't depend on `gcloud` for these alerts. A chained call that didn't call `handoffToGuardrails` is an error, not a row. Tool calls from an earlier hop in the same turn are left out of the request, and their count is printed.
- **`rebuilt`**: older rows have no chain. Model, params, tools and system message come from the conversation's previous coach call, and the conversation (ending in the message that fired) from the handler's input. Its context block is per turn, so a copied system message many minutes old is a weaker reproduction. An alert with no earlier coach call can't be rebuilt and is counted.

The summary counts `exact`, `rebuilt` and `not_rebuilt`, and each dataset row carries `request_source`. `prod_tools.json` / `prod_params.json` are production's tool definitions and runtime config, which the experiment needs (step 6).

## 3. Replay: does it reproduce?

```bash
$R replay --rebuilt "$WORK/rebuilt.json" --turns "$WORK/turns.json" --trials 3 --out "$WORK/replay.json"
```

The same request on the Evaluations key (`OPENAI_EVALUATIONS_API_KEY`), on production's own tier. **Reproduced** = it fired with production's category on a majority of trials. Expect noise: a single call is not evidence either way.

## 4. De-identify, then replay again

```bash
$R deidentify --rebuilt "$WORK/rebuilt.json" --cache-dir "$WORK/conversations" \
  --ts <reproduced ts,comma-separated> --out "$WORK/deid.json"
$R replay --rebuilt "$WORK/rebuilt.json" --turns "$WORK/turns.json" --deid "$WORK/deid.json" \
  --trials 3 --out "$WORK/replay_deid.json"
```

A model lists names, places, employers, contacts and dates. They are replaced deterministically: people get neutral names, the rest placeholders. A residue check then counts what survives, matching the account's own name and email word by word and replaced spans whole. It prints counts only. The second replay confirms the edit didn't change the outcome.

## 5. Rows, then the dataset

```bash
$R rows --rebuilt "$WORK/rebuilt.json" --replay "$WORK/replay.json" --deid "$WORK/deid.json" \
  --deid-replay "$WORK/replay_deid.json" --labels "$WORK/labels.json" --turns "$WORK/turns.json" \
  --case-prefix gfp --out "$WORK/rows.jsonl"
```

A row is written only for an alert that was rebuilt, reproduced, de-identified with zero residue, **and** still reproduced after de-identification. Every other labelled alert is counted under `skipped` by reason, never silently dropped. Each row carries the conversation as `input`, the context block as `metadata.contextInstructions`, the `### Current Time` lines as `metadata.developerInstructions` (Intelligence Service 1.35 on), and `expected.category`.

`coach_calls.coach_parts` reads every coach layout: the context inside the system message (up to 1.35), or as a developer message after it (1.36 on, after a developer `---` + user `---` cache-delimiter pair from 1.40). The delimiters and developer messages are not conversation; `rebuild` keeps them in the request in production order, so a replay is the call production made. That shape renders back to the production request through the Braintrust template — `Coach [Production]`'s delimited layout (since 2026-10-20) or an older eval copy's appended one; `eng:compare-prompt-versions --layout production` handles both and adds no second `### Current Time` message when the input already ends with one.

Load with [`data:save-to-braintrust-dataset`](../../../save-to-braintrust-dataset/SKILL.md) Mode D (`--bulk-insert --create-if-missing`, dry run first). Include true positives as well as false positives, so a candidate that simply stops triggering fails rather than passes.

## 6. Compare in Braintrust

Draft the candidate as a **new** Braintrust prompt, never an edit to `[Production]` or `[Development]`. Run [`eng:compare-prompt-versions`](../../../../../eng/skills/compare-prompt-versions/SKILL.md) twice, with production matched:

```text
--api responses --params-json $WORK/prod_params.json --tools-json $WORK/prod_tools.json --trials 5
```

1. **The new dataset:** `--label-field expected.category`. Did the false positives stop, and did the true positives stay?
2. **The crisis safety net,** `Weekly Conversation Guardrail Sentinel v1.0.0`: `--label-field metadata.route_expectation`. A new missed `must_route` row blocks the candidate. Confirm any such miss with more trials before reporting it.

Without the three production flags the experiment tests a different call. In the 2026-10-15 run, five reproduced false positives fired 3/5 on Braintrust's tools and 23/25 on production's.

## 7. Report the change

End with the run's `prompt_diff.md` (what the candidate changed, as a diff against production) and a short summary: what changed in a sentence or two, which false positives stopped, whether the true positives held, and the safety-net line. Don't adopt from this flow.

**Inside the Conversations loop.** A guardrail change ships through `product:conversations-loop` as a `safety` candidate, and this flow is its source-cases evidence. With a candidate `CONV-n`: name the dataset `CONV-n source cases v0.1`, compare production against the candidate's Braintrust version, and attach the change summary plus the experiment links with `product:evaluate-prompt-candidate record CONV-n`; its blocking Safety Suite runs alongside the Sentinel net. Without one, record it first with `product:record-prompt-candidates`. It ships through `product:ship-prompt-candidate` after Gate 2.

The conversation-quality sibling of this flow, from ratings rather than alerts, is `data:investigate-user-rating` → `references/conversation-dataset.md`. Both build rows with the same `coach_calls.py`.
