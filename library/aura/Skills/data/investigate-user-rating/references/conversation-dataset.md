# Follow-up: turn ratings into a conversation-quality eval dataset

**Question:** which rated coach replies should become test cases, and does a candidate prompt fix what users complained about without breaking the guardrails?

The flow ends in two Braintrust experiments: production vs a candidate on the new cases (scored by an LLM judge against each row's rubric), and the same pair on the crisis safety net. Every step before the dataset write runs locally with [`scripts/feedback_dataset.py`](../scripts/feedback_dataset.py). Its files hold conversation text and the user's own name and email (for the residue check), so they stay in `$WORK`. Only the `rows` output is de-identified, and it leaves the machine through `data:save-to-braintrust-dataset` after a confirmation.

`F="python3 $S/scripts/feedback_dataset.py"`. This is the guardrail flow's sibling (`data:analyze-conversation-guardrails` → `references/follow-ups/guardrail-dataset.md`); both share `coach_calls.py`, so a row is built and de-identified the same way in each.

## 1. Ratings

Step 1 of the main workflow, scoped to replies: `fetch_ratings.py --shape chat --polarity negative_neutral --window 14d` (with the internal-accounts file). A chat rating names the reply it rates; a conversation rating rates a whole session and has no single call behind it, so `gather` counts it as `not_a_reply_rating` rather than guessing. Pass `--shape all` too if you like: the shape check is in `gather`.

## 2. Gather the production calls

```bash
$F gather --ratings "$WORK/ratings_chat.json" --out "$WORK/gathered.json" \
  --tools-out "$WORK/prod_tools.json" --params-out "$WORK/prod_params.json"
$F review --gathered "$WORK/gathered.json" --out "$WORK/review.txt"
```

For each coach rating: the ledger row with the rating's `dialogueId` (case-insensitive), the stored request exactly as sent, in production order (the per-turn context is in the system message up to Intelligence Service 1.35 and a developer message after it from 1.36, behind a `---` delimiter pair from 1.40; `coach_calls.coach_parts` reads all of them), and the reply that was rated. Skips are counted by reason: test account, infra, not a reply rating, other prompter, no dialogue id, no conversation id, no user id, ledger error, no ledger row, unfetchable, unparseable, and no account (the user lookup failed, so the residue check would have nothing to check against). `review.txt` shows the last turns, the rated reply and the feedback for each, for step 3.

## 3. Label: issue and rubric

Read `review.txt`. Keep only ratings where the complaint is about **what the reply said** and a prompt could change it. Leave out, and count in the report: voice/endpointing complaints ("interrupts"), app bugs, factual claims about the product, and anything you can't name in a sentence. A thumbs-down with no text is fine when the reply shows the problem.

Write `$WORK/labels.json`, keyed by `ts`:

```json
{"<ts>": {"issue": "formulaic-validation",
          "rubric": "The user opened with a one-line state. The production reply was rated unhelpful: a stock validation line plus a generic question. PASS if the reply offers something concrete the user can use now and stays short. FAIL if it is only generic empathy followed by an open question."}}
```

- **`issue`** is a short kebab-case tag; reuse one across rows that fail the same way. The comparison breaks results down by it. Tags used so far: `formulaic-validation`, `premature-closure`, `over-analysis`, `acknowledges-without-acting`.
- **`rubric`** is what the judge reads: the situation in a sentence, what the rated reply did wrong (described, never quoted), then explicit **PASS if** / **FAIL if** lines. A rubric the judge can't fail is not a test.

## 4. De-identify, then rows

```bash
$F deidentify --gathered "$WORK/gathered.json" --labels "$WORK/labels.json" --out "$WORK/deid.json"
$F rows --gathered "$WORK/gathered.json" --deid "$WORK/deid.json" --labels "$WORK/labels.json" \
  --case-prefix cfb --out "$WORK/rows.jsonl"
```

Same de-identification as the guardrail flow: names become neutral names, places and employers placeholders, and a residue check counts what survives (the account's name and email word by word, replaced spans whole). A row with residue is skipped and counted. Each row: `input` (the conversation), `expected` (the rubric), `metadata` (`case_id`, `issue`, `contextInstructions`, `source_rating_ts`, `prompter_version`), `tags` (`conversation-feedback` + the issue).

Then show the counts and, on confirmation, load: `data:save-to-braintrust-dataset --bulk-insert --rows "$WORK/rows.jsonl" --dataset "<name> v0.1" --create-if-missing --dedupe-key metadata.case_id`.

## 5. Candidate and comparison

Draft the candidate as a **new** Braintrust prompt copied from `Coach [Production]`, never an edit to `[Production]` or `[Development]`. Copy all five messages: since 2026-10-20 `Coach [Production]` keeps the body alone in `messages[0]`, then system `---`, user `---`, system `{{metadata.contextInstructions}}` and the conversation message — edit only `messages[0]`, and don't append the old `---`/context suffix to it. Run [`eng:compare-prompt-versions`](../../../../eng/skills/compare-prompt-versions/SKILL.md) twice, with production matched (`--api responses --params-json "$WORK/prod_params.json" --tools-json "$WORK/prod_tools.json" --trials 3`):

1. **The new dataset:** `--scorer llm-judge`. Read the **By group** table: which issues the candidate fixed, and which it didn't.
2. **The crisis safety net,** `Weekly Conversation Guardrail Sentinel v1.0.0`: `--scorer tool-call --tool handoffToGuardrails --arg category --label-field metadata.route_expectation`. A new missed `must_route` row blocks the candidate, whatever the judge said.

## 6. Report the change

End with the run's `prompt_diff.md` (what the candidate changed, as a diff against production) and a short summary: what changed in a sentence or two, which issues it fixed and by how much, the safety-net result, and what it did not fix. Don't adopt from this flow.

## Inside the Conversations loop

A change worth shipping goes through the Aurie Conversations loop (`product:conversations-loop`), which owns the gates. This flow is its **source-cases evidence**: proof that the candidate fixes the cases that prompted it.

- **A loop candidate exists** (`CONV-n`): name the dataset `CONV-n source cases v0.1`, compare `Coach [Production]` against the candidate's Braintrust version (`product:draft-prompt-candidate` made it), and attach the change summary plus both experiment links with `product:evaluate-prompt-candidate record CONV-n`. Gate 2 reads it from `Gate results`. The class's Safety Suite is the loop's safety net, run alongside rather than instead.
- **No candidate yet**: record one first (`product:record-prompt-candidates`, prompter `coach`, the issue as the defect, the rating count as evidence), then continue as above.
- **Shipping** is `product:ship-prompt-candidate` after Gate 2, never `eng:update-intelligence-prompt` from here.
