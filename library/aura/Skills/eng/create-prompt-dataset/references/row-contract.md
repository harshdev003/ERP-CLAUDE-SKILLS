# Row contract

One Braintrust dataset row per case, as a JSON array. `scripts/check_rows.py` refuses rows
missing a required field; `scripts/deidentify.py` rewrites them; `data:save-to-braintrust-dataset
--bulk-insert` loads them, deduping on `metadata.case_id`. The example shows a row before
de-identification.

The shape is the same as every conversation dataset (`Guardrail Trigger Test`, the rows
`data:save-to-braintrust-dataset` builds): turns in `input`, the context block in
`metadata.contextInstructions`. So a Braintrust prompt references `{{metadata.contextInstructions}}`
and `{{input}}` the same way for any prompter, and scorers can be shared across datasets.

```json
{
  "input": [],
  "expected": {
    "rubric": "<slice criteria>\n\nExpected item count: 0 to 3.\n\n<common criteria>",
    "items": {"min": 0, "max": 3}
  },
  "metadata": {
    "contextInstructions": "## Input Data\n\n### User Information\n…",
    "structured": {
      "asOf": "2026-09-20T07:26:06.712Z",
      "userInformation": "…",
      "summaries": [{"started": "…", "ended": "…", "summary": "…"}],
      "runningMemories": [{"memory": "…", "recall": "…", "createdAt": "…"}],
      "cards": {"open": [], "completed": [], "removed": []}
    },
    "asOf": "2026-09-20T07:26:06.712Z",
    "case_id": "steerAway_2_8vQoALm1",
    "mustNotRepeat": ["…"],
    "pattern": "steerAway",
    "pendingDecision": null,
    "renderedFor": "provisional (pre-prompter)",
    "severity": "medium",
    "source": {"feedOpenaiId": "resp_…", "insightsCompilerOpenaiId": "resp_…"},
    "source_date": "2026-09-20",
    "steerAwayFrom": ["…"],
    "userId": "…",
    "variant": "A"
  },
  "tags": ["{prompterKey}", "steerAway"]
}
```

## Fields

- **`input`** is the conversation turns, a list of `{role, content}`, oldest first. A
  single-response prompter has no turns, so it is `[]`, never `null` or an object: the
  bulk-insert validator accepts only a message list or an object, and an object here is
  the old shape.
- **`metadata.contextInstructions`** is the context block exactly as the prompter puts it
  into the prompt, rendered from `structured` in the prompter's section order and formats.
  When the prompter isn't on `origin/master` yet, mirror the plan's sections and set
  `metadata.renderedFor` to `provisional`. Re-render from `structured` when it lands;
  nothing else changes.
- **`metadata.structured`** is the source of truth behind it: one key per context input, in
  the shape the prompter's builder consumes, plus `asOf`. Every key is present; `null`
  means "not filled yet" and blocks the insert, an empty list means "the user had none".
  Rename `cards` to the feature's own item name. It is kept so rows can be re-rendered
  without being rebuilt; the prompt never sees it.
- **`expected`** is an object, so a scorer reads everything it grades against from one place:
  - `rubric`: the judge text from [`slice-catalog.md`](slice-catalog.md);
  - `items`: `{min, max}`, the slice's expected item count, for a code scorer.
- **`metadata.case_id`** is `{slice}_{n}_{first 8 chars of userId}`. It is also the Braintrust
  row id, which makes inserts idempotent.
- **`metadata.mustNotRepeat` / `steerAwayFrom`** copy the texts of the open-plus-completed and
  removed items, so a code scorer can check repeats without parsing the context.
- **`metadata.source`** holds the OpenAI ids each block came from, for tracing a row back.
- **`metadata.pendingDecision`** names an open product rule the rubric assumes, or `null`.
- **`metadata.userId`** is the full id while the rows are assembled (Step 5's SQL needs it)
  and its first 8 characters after de-identification.
- **`metadata.deidentified`** (`{model, replaced}`) is written by `deidentify.py`; a row
  without it never reaches Braintrust (`check_rows.py --require-deidentified`).
- **`metadata.variant`** is the variant whose inputs the row carries. Rows for different
  variants of the same case share `structured` keys and differ only in the variant's extras.

## Rendering conventions

- Empty lists render the way the builder renders them (`Empty.`, `No past conversation
  found.`), not as missing sections. The prompt has to cope with exactly that text.
- History items render as `- [{topic}] {text} *(created {date})*`, newest first, in separate
  open, completed and removed sections.
- Report the rendered context's median and max length in the preview; a max far above the
  median usually means an uncapped input.

## Migrating rows in the old shape

Rows built before this contract carry `input: {contextInstructions, structured}`, a string
`expected` and `metadata.expectedCards`. `check_rows.py` names each of those. Move the two
`input` keys into `metadata`, set `input` to `[]`, and fold the string and `expectedCards`
into `expected.rubric` / `expected.items`. Then de-identify and load with
`--replace-existing`: the `case_id`s are unchanged, so each row is overwritten in place.
