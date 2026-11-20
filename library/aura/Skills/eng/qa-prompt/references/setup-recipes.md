# Setup recipes (Phase 1 — Gather)

Dataset-row input-shape lookup and the interactive-mode `setup_run.py` invocation recipes. The canonical dataset-mode invocation stays in SKILL.md; this file is the supporting detail.

## Dataset row input shape — auto-detected

Each row's `input` can be one of:

- **Chat messages** (list of `{role, content}`) — original / common case. The
  template's `{{input}}` placeholder gets spliced with these messages.
- **Structured input** (JSON object) — for prompts whose template references
  per-field variables instead of a conversation, e.g. the Memory Sorting
  Prompt with `{{candidates}}` + `{{similarStoredMemories}}`, or a classifier
  with `{{user_message}}` + `{{recent_topics}}`. Each top-level key on the
  row's `input` dict becomes a `{{<key>}}` template variable. Non-string
  values (dicts, lists) are JSON-encoded into the rendered string. No
  `{{input}}` splicing happens for structured-input rows.

The row's `expected` field (if present) is persisted at
`state.dataset_row.expected` so the `structured-plan-match` scorer can use it
as a reference example (not exact-match ground truth — see Phase 4).

## Interactive mode invocations

For interactive mode with a real seed context (uses the metadata from a
dataset row):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/setup_run.py \
  <braintrust-prompt-url> \
  --mode interactive \
  --scenario "stressed parent, just woke up, looking for a 3-minute meditation" \
  --context dataset:<slug>:<row-id>                 # used for metadata.contextInstructions only
  --state-file outputs/qa-prompt-<slug>-<ts>/state.json
```

For interactive mode where the prompt doesn't need real seed context (most
free-form QA scenarios — no dataset row, just a persona description), use
`--no-context`. It synthesizes an empty paste payload internally so
`{{metadata.contextInstructions}}` renders to an empty string:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/setup_run.py \
  <braintrust-prompt-url> \
  --mode interactive \
  --scenario "stressed parent, just woke up, looking for a 3-minute meditation" \
  --no-context \
  --state-file outputs/qa-prompt-<slug>-<ts>/state.json
```

`--context` and `--no-context` are mutually exclusive; one is required.

In interactive mode the context source's `input` messages are discarded
(Claude composes user turns instead). The metadata still flows through — so
`{{metadata.contextInstructions}}` substitutes as it would in prod.
