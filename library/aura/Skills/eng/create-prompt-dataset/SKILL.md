---
name: create-prompt-dataset
description: >
  Builds the Braintrust eval dataset for a new IntelligenceService prompter from production
  data: designs scenario slices and rubrics, replays real users as of a past date from stored
  OpenAI requests, hands over read-only SQL for gaps, then de-identifies and loads the rows.
  Trigger on "create a dataset
  for this prompt", "build the eval dataset for PROD-123", "make test cases for the {feature}
  prompt", "how many test cases do we need", "dataset for the prompt eval", "create a
  Braintrust dataset from prod data", "build eval cases from production", "mimic production
  memories and summaries in a dataset", "dataset for the new prompter", or a Linear URL with
  a request to build its dataset. Do NOT use to plan the prompt (eng:plan-prompt), save one
  conversation as a row (data:save-to-braintrust-dataset), build cases for an existing prompt
  from guardrail alerts or ratings (data:analyze-conversation-guardrails,
  data:investigate-user-rating), or score a dataset (eng:compare-prompt-versions).
metadata:
  version: "1.3.2"
knowledge:
  reads:
    - Guide/Index        # baseline routing aid (universal)
    - Tools/Linear       # the feature card, its sub-issues and comment threads
    - Tools/Notion       # PRD and linked docs
    - Tools/OpenAI       # project keys and reading stored Responses / completions
    - Tools/Braintrust   # projects and datasets; rows load through data:save-to-braintrust-dataset
    - Tools/Aura API     # llmCall ledger semantics (read from BigQuery llm.llm_call)
    - Products/Aurie     # Aurie's voice, for tone criteria in the rubrics
    - Compliance/Privacy # prod user content in the dataset and in anything posted back
---

# Create Prompt Dataset

You build the eval dataset a prompter is judged on before it ships. For a single-response
prompter there is no conversation to replay: the whole input is the context block the
service builds for one user at one moment. So a case is **one real user, frozen at one past
moment T**, with every context input reconstructed as the prompter would have seen it then.

Most of the work is finding where each input already sits in stored OpenAI requests, and
proving the block you found is really filled in. A block can be present in every request and
empty in all of them. **Measure before you trust, and keep user content out of the chat.**

## Goal

A Braintrust dataset of roughly 60 de-identified rows across named scenario slices, in the
same shape as every conversation dataset: turns in `input` (`[]` for a single-response
prompter), the rendered context in `metadata.contextInstructions`, its structured inputs, a
rubric and item range in `expected`, and enough metadata to trace each row back to the
production calls it came from, loaded only after the user has seen the preview.

## Inputs

- A Linear identifier or URL for the feature (usual), plus any PRD or Notion link.
- An IntelligenceService checkout: `INTELLIGENCE_SERVICE_PATH`, or the path the user gives.
- Env (names only, never print values): `GCP_KEY` (reads the llmCall ledger from BigQuery),
  `BRAINTRUST_API_KEY`, and `OPENAI_<PROJECT>_API_KEY` for each OpenAI project you read
  (`OPENAI_FEED_API_KEY`, `OPENAI_INSIGHTS_API_KEY`, `OPENAI_SUMMARY_API_KEY`, …). Which key
  maps to which project is on `Tools/OpenAI`. `OPENAI_EVALUATIONS_API_KEY` for the
  de-identification calls in Step 9.

## Ground rules

- **No user content in the session.** Scripts read stored requests into files in a scratch
  directory and print counts, ids and headings only. Pick cases by rule from computed
  features, not by reading people's conversations. Follow `Compliance/Privacy` for anything
  that leaves the scratch directory other than the dataset itself.
- **When a permission check blocks a read of user content, stop and hand over.** Don't
  retry through another tool. Give the user the exact command to run and the output you
  need back (counts, not content).
- **Never write to a shared database.** Read-only SQL goes to the user to run (Step 5).
- **Nothing reaches Braintrust un-de-identified.** Only rows that pass the residue check in
  Step 9 are loaded, and only after the preview and a yes (Step 10).
- Scratch files hold real people's data: keep them under the session scratchpad (or
  `mktemp -d`), never inside a repo, and offer to delete them at the end.

## Workflow

### Step 1: Read the feature

1. **Linear.** Linear MCP first (`get_issue`, `list_issues` with `parentId`, `list_comments`),
   falling back to the GraphQL API with `LINEAR_API_KEY`. Read the parent, every sub-issue and
   every comment thread: product answers usually sit in comments, and a dataset or eval
   sub-issue often lists the scenarios to cover already.
2. **Docs.** PRD and linked Notion pages via the Notion MCP.
3. Write a requirements list, one line each with its source:
   - the prompter name and file, and whether it exists on `origin/master` yet;
   - **when it runs** (schedule, trigger, the user's local time). This defines T;
   - every context input with its window, limit, filter and order;
   - the output schema, item-count bounds, and whether "nothing" is a valid answer;
   - safety rules and content rules;
   - any state the feature itself creates and feeds back (open, completed or removed items);
   - variants to compare, if any.

### Step 2: Gate on prompt decisions

List anything still open that would change an input, the output shape or a safety rule.

- **Inputs, output or count undecided, or no prompt plan at all** → stop. Tell the user to
  run `eng:plan-prompt` first and why: a dataset built on an unsettled input has to be rebuilt.
- **A decision that only changes one slice's rubric** (for example a pending content rule) →
  ask whether to proceed with the recommended default. If yes, mark those rows
  `pendingDecision: <topic>` and name it in the report.
- **Variants.** For each variant, confirm it is needed before collecting its extra input.
  Raw user messages or transcripts add privacy weight; a variant the team won't ship is not
  worth collecting.

### Step 3: Design the dataset, then confirm

Build the plan and show it before collecting anything:

1. **Input → source table.** One row per context input: where you expect to find it (which
   prompter's stored request, which section heading), or "SQL" when no log carries it.
2. **Slices and counts** from [`references/slice-catalog.md`](references/slice-catalog.md):
   the card's own coverage list first, then the standard edge slices that apply. Default
   size about 60 cases, each run 3 times when the prompter samples at temperature above 0;
   about 30 is enough for the first prompt iteration.
3. **Rubric per slice**: expected item count range, the slice's pass criteria, plus the
   common criteria every row shares (schema, grounding, safety, voice from `Products/Aurie`).

Wait for the user's go before Step 4.

### Step 4: Find each input in the logs

Follow [`references/log-sources.md`](references/log-sources.md). In short:

1. Read the builders in the IntelligenceService code at `origin/master` to learn each
   input's rendered heading and which prompters call that builder. Read them from a
   read-only worktree, never the user's working tree. Resolve the checkout from
   `INTELLIGENCE_SERVICE_PATH` or the path the user gives; if it isn't a git work tree,
   stop and say so rather than guessing another path:

   ```bash
   IS_DIR="${INTELLIGENCE_SERVICE_PATH:?set INTELLIGENCE_SERVICE_PATH or pass the path the user gave}"
   git -C "$IS_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
     || { echo "Not a git checkout: $IS_DIR"; exit 2; }
   IS_ROOT=$(git -C "$IS_DIR" rev-parse --show-toplevel)
   git -C "$IS_ROOT" fetch origin --quiet
   # drop read-only worktrees left by earlier runs before making a new one
   git -C "$IS_ROOT" worktree list --porcelain | sed -n 's/^worktree //p' \
     | grep -E '/\.claude/worktrees/create-prompt-dataset-[0-9]+$' \
     | while read -r old; do git -C "$IS_ROOT" worktree remove --force "$old"; done
   SRC="$IS_ROOT/.claude/worktrees/create-prompt-dataset-$(date +%Y%m%d%H%M%S)"
   git -C "$IS_ROOT" worktree add --detach "$SRC" origin/master --quiet
   ```

   The worktree lives under the repo's `.claude/worktrees/`, never beside the repo or in a
   temp directory. Remove it when the run ends, at whatever step:
   `git -C "$IS_ROOT" worktree remove "$SRC"`. A run that ends without cleanup is swept by
   the next run.
2. Walk the ledger for each candidate prompter over the window you need:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/create-prompt-dataset/scripts/ledger.py" \
     --prompter feed-prompter --from 2026-07-20T00:00:00Z --to 2026-10-08T00:00:00Z \
     --out "$SCRATCH/feed_ledger.json"
   ```

3. **Probe the blocks before trusting them.** For a random sample of 50 ids:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/create-prompt-dataset/scripts/stored_responses.py" probe \
     --project insights --ledger "$SCRATCH/ic_ledger.json" --sample 50 --cache "$SCRATCH/cache" \
     --after "### Internal Profile"
   ```

   It prints, per heading, how often it appears and how often its body is empty, plus the
   field labels it renders. `--after` starts at the prompt's data section (often
   `## Input Data`) so instruction headings aren't counted. A block that is empty in nearly every request is a broken
   builder, not a quiet user base: don't use it, and tell the user what you found.
4. **Pick the anchor event T** that matches when the new prompter will run (a nightly job
   → another nightly prompter's run time), then pair each other source with that user's
   nearest call in a window before T.
5. Check retention: fetch one id from the oldest week you plan to use.

### Step 5: Hand over SQL for what the logs lack

When an input isn't rendered anywhere, or renders without a field the prompt needs, write a
read-only, as-of-T query from [`references/sql-templates.md`](references/sql-templates.md)
covering every selected case in one statement. Save it to the scratch directory, show the
user where it is, and ask them to run it and save the result as a file. Never run it
yourself. While you wait, carry on with every step that doesn't need it.

### Step 6: Build the candidate pool and pick cases by rule

1. Join the sources into candidates `(userId, T)`, keep one per user, and sample ~500
   across activity levels.
2. Fetch the blocks for the sample and compute features with code: counts per block, text
   lengths, regex tags per slice (crisis, sensitive families, bereavement, endings, small
   talk, off-category, onboarding-survey memories, upcoming events). Print counts only.
3. Fill the narrowest slices first (crisis, sensitive, import-only, grief) and the
   representative slice last, stratified by activity. One case per user, so 60 cases means
   60 people. Write a manifest of `(slice, userId, asOf)` and report wanted / got / pool
   size per slice.

### Step 7: Fill state the feature hasn't produced yet

A new feature has no production history of its own items (open, completed, removed). Build it:

- **From a predecessor prompter's outputs**, when one existed: its stored responses on the
  nights before T are a realistic stream of earlier items for the same user. Deleted prompts
  count; their stored responses outlive the code.
- **Set states by rule**, not by reading content, and record the rule in the row metadata.
- Every other slice starts with no history, which is what launch looks like.

### Step 8: Assemble rows

Follow [`references/row-contract.md`](references/row-contract.md): `input` holds the
conversation turns (`[]` for a single-response prompter, never `null`);
`metadata.structured` holds the structured inputs as the source of truth;
`metadata.contextInstructions` holds the context rendered from them in the prompter's
sections (marked provisional until the prompter exists on `origin/master`); `expected` is
`{rubric, items: {min, max}}`; and metadata carries `case_id`, slice, severity,
must-not-repeat and steer-away lists, `pendingDecision`, source ids and `asOf`. Merge the SQL
export when it arrives.

### Step 9: De-identify and check for residue

Validate the assembled rows, then de-identify them:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/create-prompt-dataset/scripts/check_rows.py" --rows "$SCRATCH/rows.json"
python3 "${CLAUDE_PLUGIN_ROOT}/skills/create-prompt-dataset/scripts/deidentify.py" \
  --rows "$SCRATCH/rows.json" --out "$SCRATCH/rows.deid.json"
```

`deidentify.py` asks an LLM (flex tier) to list the identifying spans in each row's
`metadata.contextInstructions` and `input` turns: people's names,
specific places, employers, schools, contacts, exact birth dates. Code replaces them: people
get a pseudonym that matches no real name in the row, other kinds a neutral placeholder. One
mapping per row covers `input`, `contextInstructions`, `structured` and the item lists, so
the rendered context, the structured inputs and the turns stay consistent, and
`metadata.userId` is cut to 8 characters. It then counts any replaced
span, or the user's given name, still present. A row with residue or a failed call is held
back and listed by `case_id`. Re-run the script once on the held-back rows; drop what still
fails rather than reading it to fix by hand, and report how many were dropped per slice.

### Step 10: Preview, then load

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/create-prompt-dataset/scripts/check_rows.py" \
  --rows "$SCRATCH/rows.deid.json" --require-deidentified
```

Show the user its preview (rows per slice, context size median and max, pending decisions)
with the held-back count from Step 9. After a yes, load the file by invoking
`data:save-to-braintrust-dataset` in bulk mode, dry run first:

```
--bulk-insert --rows {rows.deid.json} --project Feed --dataset "Conversation Starters Feed"
  --create-if-missing --description "{prompter} eval cases, as-of replays of prod users" --dry-run
```

then the same without `--dry-run`. Its dedupe key is `metadata.case_id`: rows already in the
dataset are skipped, and `--replace-existing` overwrites them, which is what a re-render or a
re-run of de-identification needs. Report its inserted, replaced and skipped counts.

The prompter's Braintrust prompt reads the context as `{{metadata.contextInstructions}}` (and
the turns as `{{input}}` when there are any), the same as for a conversation dataset. Check
the prompt does before the first eval run: a prompt still templated on the old
`{{input.contextInstructions}}` renders an empty context without erroring.

### Step 11: Report

- Dataset name and project; inserted, replaced, skipped and held-back counts.
- Slice table: slice, cases, expected count range, severity.
- What is provisional (rendered context before the prompter exists), what is pending a
  decision, and how to re-render (same rows, same ids).
- Suggested runs: 3 per case at the prompter's temperature; compare variants with
  `eng:compare-prompt-versions`.
- Offer to post a summary on the Linear card: counts and slice names only, no user content,
  UID prefixes at most (`Compliance/Privacy`).
- Offer to delete the scratch files and any `.sql` file that lists user ids.
- Remove the read-only IntelligenceService worktree from Step 4.
- Next steps: `eng:compare-prompt-versions --scorer llm-judge` scores prompt versions against
  the rows' rubrics; a code scorer can use `mustNotRepeat` and `steerAwayFrom`.

## Output

A Braintrust dataset in the prompter's project, and a short report in chat. Nothing is
written to the IntelligenceService repo or to any database.

## Edge cases

- **The prompter isn't built yet.** Render the context in the planned section order and
  mark rows `renderedFor: provisional`. When it lands, re-render `contextInstructions` from
  `metadata.structured`, de-identify again and load with `--replace-existing`; ids stay stable.
- **The dataset holds rows in the old shape** (`input: {contextInstructions, structured}`, a
  string `expected`). `check_rows.py` names them; reshape per the row contract's migration
  note and load with `--replace-existing`.
- **A source prompter's block is empty or missing a field** (see Step 4.3). Use another
  prompter that renders the same builder, or hand over SQL.
- **The ledger caps at 1000 rows per call.** `ledger.py` walks back by moving `to`.
- **Responses API calls can't be listed** with an API key; the ledger is the index. Chat
  Completions calls can be, by metadata filter (`Tools/OpenAI`).
- **Braintrust project names are matched exactly** (`Feed`, not `feed`). Check the name in
  `Tools/Braintrust` or the UI before the bulk load.
- **A dataset was loaded before de-identification.** De-identify the same rows and load them
  with `--replace-existing`: the `case_id`s match, so every raw row is overwritten in place.
  Confirm the replaced count equals the dataset's row count.
- **The user pastes SQL output from a GUI client.** Ask them to save it from the clipboard
  with `LANG=en_US.UTF-8 pbpaste > file`; without the locale, macOS writes non-UTF-8 bytes.
  A client export shaped `{"<query>": [{"json_agg": "<json string>"}]}` is unwrapped by
  your assembly code, not reshaped by hand.
- **Re-run on an existing dataset.** New `case_id`s add rows; existing ones are skipped
  unless `--replace-existing` is passed. Say which you did.

## References

- [`references/slice-catalog.md`](references/slice-catalog.md): standard slices, selection
  features, rubric text, counts.
- [`references/log-sources.md`](references/log-sources.md): ledger, stored requests, which
  builders render what, and the traps.
- [`references/sql-templates.md`](references/sql-templates.md): as-of-T read-only queries.
- [`references/row-contract.md`](references/row-contract.md): row shape and rendering.
