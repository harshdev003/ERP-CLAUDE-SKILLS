---
name: save-to-braintrust-dataset
description: >
  Save rows to a Braintrust dataset so real or hand-built cases become runnable eval cases.
  One production conversation (from a `conversation_id`, a k8bot rating Slack URL, or a row
  JSON file) goes into the `Feedback Conversation` dataset by default, with a preview,
  prompts for the load-bearing metadata and an idempotency check. A bulk mode loads a JSONL
  file of tagged rows into a named dataset in any project, creating the dataset if it does
  not exist and skipping rows already present. Trigger on "save this conversation to the
  feedback dataset", "add this to the Braintrust dataset", "save to feedback conversation",
  "make this an eval case", "turn this rating into an eval", "upload this dataset to
  Braintrust", "create a Braintrust dataset from these rows", or a pasted Braintrust
  conversation URL or k8bot rating URL with the same intent. Do NOT use to run prompts
  against a dataset (`eng:compare-prompt-versions`) or to edit a prompt template.
metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Guide/Index                       # baseline routing aid (universal)
    - Tools/Braintrust                  # row contract, recipes, auth, dataset registry
    - Tools/Slack                    # Slack post/read conventions + footer format
    - Compliance/Privacy                # PII rule for bulk-loaded real conversations
---

# save-to-braintrust-dataset

Turns one production conversation into a runnable Braintrust eval case in a chosen dataset within the `Conversation` project. The row carries the conversation turns (`input`), the runtime context block (`metadata.contextInstructions`), the pass criteria (`expected`), and bookkeeping metadata (`case_id`, `pattern`, `severity`, source link).

The skill is **plan-first**: it always builds a preview, surfaces it to the user, prompts for the load-bearing fields, and asks for confirmation before writing to Braintrust.

**Default dataset:** `Feedback Conversation` (slug `feedback-conversation`, id `5b5d02b5-5d10-434a-bd3a-1aed88cc5cfe`). Pass `--dataset <name-or-slug>` to target a different dataset in the same project. Row contract documented on [AI Knowledge / Tools / Braintrust › Datasets for evals](https://www.notion.so/34c75e23af2081968f7bf7ee43e1442a).

## Step 0 -- Check prerequisites

```bash
test -n "$BRAINTRUST_API_KEY" && echo "OK: key set" || echo "MISSING"
```

If missing: same playbook as the rest of the data plugin -- the key lives in `~/Development/.claude/settings.json` under `env`, scoped to `Aura Health 1`. Hand off to `update-config` if needed and have the user restart Claude Code. Do not proceed without a key.

Other prereqs: `python3` with `requests`. `pip3 install requests` if the script errors on import.

## Step 1 -- Resolve the input mode

Pick exactly one. Ask if ambiguous.

### Mode A -- Slack URL

User pasted a Slack permalink (e.g. `https://auramobile.slack.com/archives/C07MB0TMF5J/p1778228018407599`).

1. Resolve `channel_id` + `message_ts` from the URL. The `p<ts>` segment is microseconds; insert the decimal point: `1778228018407599` → `1778228018.407599`.
2. Read the parent message by **invoking [`shared:read-slack`](../../../shared/skills/read-slack/SKILL.md)** (its `replies` mode) for that `channel_id` + `ts`. That skill owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback).
3. Detect the k8bot shape -- recognised headers (same set as `analyze-user-conversations`):
   - `*CONVERSATION RATED*` (coach-prompter)
   - `*QUICK PROCESSING RATED*` (quick-processing-prompter)
   - `*COACHING RATED*` (coach-prompter)
   - `*CHAT MESSAGE RATED*` (typically guardrails-handler-prompter)
   - `*New Cancellation Feedback*` (no conversation_id -- not usable here; abort and tell the user to use `analyze-user-conversations` instead)
4. From the footer, extract `conversation: <UUID>` → `conversation_id`. Also pull `:+1: Positive` / `:-1: Negative` / `:neutral_face: Neutral` → `observed_rating`; this becomes a default override the user can edit later.
5. The Slack permalink itself becomes the default `--source`.
6. Fall through to Mode B with the extracted `conversation_id`.

If the message doesn't match any shape, surface to the user: "Got a Slack message but the header isn't a k8bot shape I know how to parse. Want to give me a conversationId directly?"

### Mode B -- Direct conversation_id

User pasted a Braintrust conversation UUID, or a Braintrust UI URL (extract the UUID from it).

Go straight to Step 2.

### Mode C -- Pre-built row file

User has a JSON file already shaped as a dataset row (e.g. produced by a previous `--build` run they edited, or synthesised from scratch for a non-Braintrust case).

Skip Steps 2 and 3, go to Step 4 (validation + idempotency) using the file directly.

### Mode D -- Bulk load a file of rows

User has many prepared rows: a Claude-built eval set, a CSV converted to JSONL, rows tagged with an expected outcome. Each line is `{input, expected?, metadata, tags?}`, where `input` is a `{role, content}` messages list (or an object for structured prompts) and `metadata` carries a unique key (default `metadata.case_id`). This mode skips Steps 2–6.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/save-to-braintrust-dataset/scripts/save_row.py" \
  --bulk-insert --rows <file.jsonl> --dataset "<exact dataset name>" \
  [--project Conversation] [--create-if-missing --description "<what the set tests>"] \
  [--dedupe-key metadata.case_id] [--replace-existing] --dry-run
```

`--dataset` is required here (no default), so nothing lands in `Feedback Conversation` by omission. The dry run prints `{dataset_exists, rows_in_file, existing_rows, insert, replace, skipped_existing, errors}`. Show it to the user and re-run without `--dry-run` only on a go. Rows whose key already exists are **skipped and counted** by default; pass `--replace-existing` to overwrite them. Any row error (bad `input`, missing or repeated key) aborts the run before anything is written. If the rows are real user conversations, apply `Compliance/Privacy`: no names, emails, or other PII in `input` or `metadata`.

To run prompt versions against the loaded dataset, hand off to `eng:compare-prompt-versions`.

## Step 2 -- Build the draft row

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/save-to-braintrust-dataset/scripts/save_row.py" \
  --build \
  --conversation-id "<UUID>" \
  --out /tmp/dataset_row_draft.json
```

The script defaults to the `Feedback Conversation` dataset (slug `feedback-conversation`). To target a different dataset in the same project, add `--dataset <name-or-slug>` (display name or slug both work, case-insensitive).

Optional flags to pre-fill the metadata if the caller already knows them (skip the corresponding prompt in Step 3):
- `--case-id <id>`
- `--pattern <pattern>`
- `--severity low|medium|high`
- `--observed-rating positive|negative|neutral|unknown` (default: auto-detected from the conversationRating span)
- `--source <url>` (Slack permalink from Mode A is the natural default)
- `--source-date YYYY-MM-DD` (default: today)
- `--expected "<criteria>"` (the LLM-judge pass criteria)
- `--tag <t>` (repeatable; auto-tags include `prompterName` and pattern slug)
- `--dataset <name-or-slug>` (default: `feedback-conversation`)

The script prints a JSON summary to stdout with the draft path, conversationId, prompterName, observed_rating, turn count, contextInstructions length, the resolved dataset, and a list of `placeholders` -- the fields that still need to be filled in before insert.

## Step 3 -- Prompt the user for the load-bearing fields

Read the draft from `/tmp/dataset_row_draft.json`. Show the user a compact preview:

```
Drafted row from conversation <UUID>:
  dataset       = <Feedback Conversation | other>
  prompter      = <prompterName>
  observed      = <positive|negative|neutral>
  turns         = N
  context block = M chars
  draft saved   = /tmp/dataset_row_draft.json

Need to fill before insert:
  case_id       : <empty>
  pattern       : <empty>
  severity      : <empty>
  source        : <empty or pre-filled from Slack URL>
  source_date   : <today by default>
  expected      : <empty>
```

Then ask **one question per missing field**, in this order. Use `AskUserQuestion` when several have natural multiple-choice answers; otherwise plain text. Default suggestions where you can:

1. **case_id** -- propose a slug from `<pattern>_v1_<conversationId-prefix>` once pattern is known. Editable.
2. **pattern** -- short snake_case name (`qp_entry_on_exit_tokens`, `recommend_tracks_intent`, `coach_question_saturation`, etc.). Suggest from the existing dataset's `metadata.pattern` values if you've seen one that fits.
3. **severity** -- `low` / `medium` / `high`. Default `medium` unless the user signals otherwise.
4. **source** -- pre-filled in Mode A. In Mode B, ask for the Slack permalink or Braintrust UI URL that motivated this case.
5. **source_date** -- pre-filled today. Edit only if the case is being filed for an older event.
6. **expected** -- the LLM-judge pass criteria. This is the load-bearing field. Ask: *"What must the assistant do (or not do) on this turn for the eval to pass?"* Capture the user's answer verbatim; do not paraphrase. Multi-line is fine. If composed from `data:analyze-user-conversations`, the "Hypothesis to validate" / "What didn't" sections are good starting drafts -- show them and ask for confirm/edit.

Apply the answers by rewriting the file. Use `jq` or Python -- do not hand-edit with `sed` (the `contextInstructions` string contains regex specials).

Idiomatic pattern:
```bash
python3 -c "
import json, sys
p = '/tmp/dataset_row_draft.json'
row = json.load(open(p))
row['metadata']['case_id']     = '<value>'
row['metadata']['pattern']     = '<value>'
row['metadata']['severity']    = '<value>'
row['metadata']['source']      = '<value>'
row['metadata']['source_date'] = '<value>'
row['expected']                = '<value>'
# Re-stamp auto-tags from the new pattern.
row['tags'] = sorted(set(row['tags'] + [row['metadata']['pattern'].lower().replace(' ', '-')]))
json.dump(row, open(p, 'w'), indent=2, ensure_ascii=False)
"
```

## Step 4 -- Idempotency check

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/save-to-braintrust-dataset/scripts/save_row.py" \
  --check-dupe --conversation-id "<UUID>" [--dataset <name-or-slug>]
```

If `existing_count == 0`: proceed to Step 5.

If `existing_count >= 1`: show the user the existing row(s) (id + case_id + pattern + prompterName) and ask:
- **skip** -- abort; print the existing row URL(s)
- **update** -- replace the chosen existing row (use `--update <row-id>` in Step 5)
- **insert anyway** -- create a new row alongside (use `--allow-duplicate` in Step 5)

Default to `skip` if the existing row covers the same `pattern` -- duplicates of the same pattern on the same conversation dilute the eval signal.

## Step 5 -- Insert

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/save-to-braintrust-dataset/scripts/save_row.py" \
  --insert --row /tmp/dataset_row_draft.json \
  [--dataset <name-or-slug>] \
  [--update <row-id> | --allow-duplicate]
```

The script:
1. Resolves the target dataset (default `feedback-conversation`).
2. Validates the row against the contract (input shape, required metadata fields, expected non-placeholder).
3. Refuses if the conversationId already exists and neither `--update` nor `--allow-duplicate` was passed.
4. Inserts (or replaces, with `--update`).
5. Prints `{ row_id, operation, url, dataset }` on success.

If validation fails, the script lists every failed field. Fix them in the JSON file, re-run.

## Step 6 -- Confirm + clean up

Show the user the final row URL and a one-line summary:

```
Saved → <url>
dataset = <Feedback Conversation>  case_id = <id>  pattern = <pattern>  severity = <severity>  observed = <rating>
```

Delete `/tmp/dataset_row_draft.json` once the user confirms it's in.

## Composition from other skills

When invoked from `data:analyze-user-conversations` or `data:profile-user`:

1. The caller already has `conversation_id`, `observed_rating`, and the Slack `source` URL in scope -- pass them as `--build` flags so the user doesn't get asked twice.
2. The caller already has an analysis with "Hypothesis to validate" / "What didn't" sections -- pass that text as `--expected` (the user still confirms or edits before insert in Step 3).
3. The caller's framing (e.g. "this is a `qp_exit_tokens` pattern") becomes the `--pattern` default.
4. If the caller wants to target a non-default dataset (e.g. a brand-voice-focused sibling dataset), pass `--dataset` through.

The skill remains plan-first even under composition: the user always sees Step 3's preview and explicitly confirms Step 5's insert.

## Notes

- The `--build` step sources `input` + `metadata.contextInstructions` from the latest **parent task span that carries both** -- named `streamChat` on Chat Completions, `streamResponse` on the Responses API. The name is only a preference among spans carrying both fields, never the qualifying test, so a span renamed by a future API migration still resolves to a usable one. If no span carries both (telemetry anomaly), `--build` exits naming what is missing -- flag that to the user as a data-quality issue worth investigating in the source conversation before locking it in as an eval case.
- The auto-generated `tags` include `prompterName` and a slug of `pattern`. The user can add more with `--tag` flags.
- System-role messages in the production log's `input` are stripped before saving -- the eval prompt template re-injects the system instructions from `{{metadata.contextInstructions}}` itself.
- The skill does not (yet) wire the row to a scorer. Scorer definition lives in the Braintrust UI's experiment config, not in the dataset row.
- `--dataset` is resolved case-insensitively against both the dataset display name (`Feedback Conversation`) and its slug (`feedback-conversation`). The single-row modes reach only the `Conversation` project; `--bulk-insert` takes `--project` and matches the dataset name exactly.
