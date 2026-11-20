---
name: sync-feedback-config
description: >
  Syncs the Intelligence Service feedback label configs
  (config/dialogue-feedback.json and config/journal-feedback.json) to the
  Tools/Aura API/Dialogue Feedback Labels page in AI Knowledge. Reads the
  live Intelligence Service config via the gh CLI, diffs it against the current Notion page,
  shows a plan, and applies updates on approval. Idempotent — no-op when
  nothing changed.

  Runs standalone or is called by harvest-knowledge as a lightweight source
  adapter (pass --mode=diff for a structured JSON result harvest-knowledge
  can fold into its write plan without the interactive approval loop).

  Trigger when the user says: "sync feedback labels to AI Knowledge",
  "check if Intelligence Service feedback config changed", "update dialogue feedback labels",
  "sync-feedback-config", or when harvest-knowledge calls it as part of a
  periodic sweep.
metadata:
  version: "3.0.2"
knowledge:
  reads:
    - Guide/Index                              # baseline routing aid (universal)
    - Tools/GitHub                             # gh CLI auth + API pattern
    - Tools/Notion                             # REST access pattern for page reads/writes
    - Tools/Aura API/Dialogue Feedback Labels  # the target Notion page this skill maintains
  writes:
    - Tools/Aura API/Dialogue Feedback Labels  # updated when Intelligence Service config diverges from page
---

# sync-feedback-config

Keeps `Tools/Aura API/Dialogue Feedback Labels` in AI Knowledge in sync with the
Intelligence Service source-of-truth config files. The Intelligence Service files are the canonical
definition; the Notion page is a derived artifact for AI skill consumption.

**Core contract:**
- **Deterministic fetch + diff.** All Intelligence Service reads and diffing happen in
  [`scripts/fetch_is_config.py`](scripts/fetch_is_config.py) — no LLM reasoning about
  what changed.
- **Plan before write.** Show the diff and wait for approval before touching Notion.
- **Idempotent.** Running with no Intelligence Service changes produces `"changed": false` and exits
  without prompting.
- **harvest-knowledge callable.** Pass `--mode=diff` for structured JSON output;
  harvest-knowledge folds the result into its own write plan.

## Prerequisites

- **`gh` CLI** authenticated (`gh auth status` should pass). Used to read Intelligence Service config
  files without cloning the repo.
- **`NOTION_TOKEN`** — for reading and updating the Notion page. MCP fallback available
  for reads; REST is used for the write path.

## Inputs

- `mode` (optional, default `interactive`) — `interactive` runs the full plan-and-confirm
  loop; `diff` emits structured JSON and exits (no Notion writes, for harvest-knowledge
  integration).
- `dry_run` (optional, default `false`) — show the plan but skip the Notion write even
  when the user confirms. Useful for verifying the diff logic.

## Step 1 — Fetch Intelligence Service configs (deterministic)

Run the script to read both config files from Intelligence Service and diff against the Notion page:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/sync-feedback-config/scripts/fetch_is_config.py
```

The script outputs JSON to stdout:

```json
{
  "as_of": "YYYY-MM-DD",
  "changed": true,
  "configs": {
    "dialogue": {
      "path": "config/dialogue-feedback.json",
      "sha": "<git sha>",
      "positive": [{"key": "helpful_response", "label": "Helpful response"}, ...],
      "negative": [{"key": "recommendation_not_helpful", "label": "Recommendation not helpful"}, ...]
    },
    "journal": {
      "path": "config/journal-feedback.json",
      "sha": "<git sha>",
      "positive": [...],
      "negative": [...]
    }
  },
  "notion_page_id": "38975e23-af20-816e-9662-e038875662e8",
  "diff": {
    "dialogue_positive": {"added": [], "removed": [], "relabelled": []},
    "dialogue_negative": {"added": [], "removed": [], "relabelled": []},
    "journal_positive":  {"added": [], "removed": [], "relabelled": []},
    "journal_negative":  {"added": [], "removed": [], "relabelled": []}
  }
}
```

**If `changed: false`** — report "Intelligence Service configs match AI Knowledge page — nothing to do."
and stop. No Notion interaction needed.

**In `--mode=diff`** — print the JSON and exit. Harvest-knowledge handles approval +
apply in its own flow.

## Step 2 — Show the plan

When `changed: true` and `mode=interactive`, present a human-readable diff before any
write:

```
Intelligence Service feedback config has changed since the last AI Knowledge update.

Dialogue — positive options
  + New: <label> (key: <key>)
  - Removed: <label>
  ~ Relabelled: <old label> → <new label>

Dialogue — negative options
  ...

Journal — positive / negative
  ...

Target page: Tools/Aura API/Dialogue Feedback Labels
```

If the diff is empty across all four sections after the script runs, confirm "No changes"
and stop — don't prompt for approval on a no-op.

## Step 3 — Apply (on approval)

Wait for the user to confirm. When approved, update the Notion page via `notion_rest.py`:

1. Fetch the current page blocks.
2. Locate the "Dialogue (Quick Processing + Conversation)" and "Journal" sections.
3. Replace the positive/negative bullet lists in each section with the new Intelligence Service values.
4. Update the `# Sources` section's "confirmed" date to today.
5. Invalidate the local cache:
   ```bash
   rm -f "${AI_KNOWLEDGE_CACHE_DIR:-$HOME/.cache/ai-knowledge}/tools__aura_api__dialogue_feedback_labels.md"
   ```

Use the `notion_rest.py` helper bundled in `scripts/`. Do NOT reach into another skill's
scripts directory.

## Step 4 — Confirm

Report:
- Which labels were added / removed / relabelled per section.
- The Notion page URL.
- That the local cache was invalidated (next skill run will re-fetch fresh content).

## harvest-knowledge integration

Harvest-knowledge invokes this skill as a source adapter in its Step 4:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/sync-feedback-config/scripts/fetch_is_config.py \
  --mode diff
```

The script exits 0 with `"changed": false` when nothing changed (harvest skips it),
or exits 0 with `"changed": true` and a diff when labels diverged (harvest includes
it as a proposed update in its write plan, with `Tools/Aura API/Dialogue Feedback
Labels` as the target and the diff as the proposed edit).

Harvest-knowledge owns the approval and apply steps in that flow — `sync-feedback-config`
is a pure reader when called with `--mode=diff`.

## Edge cases

- **`gh` not authenticated.** Script exits non-zero with a clear message. Surface to
  user: "Run `gh auth login` then retry."
- **Intelligence Service repo unavailable / file moved.** Script exits non-zero. Surface path + error.
  The Notion page is not touched — no partial update.
- **`NOTION_TOKEN` unset.** Step 1 diff still runs (script reads Intelligence Service config via `gh`, not
  Notion). The diff is shown. Step 3 is skipped with a note: "Set `NOTION_TOKEN` to
  apply the update, or edit the Notion page manually."
- **Notion page not found.** The script falls back to comparing against a hardcoded
  baseline (the label set as of 2026-06-24). Surface a warning that the page ID may
  have changed.
- **Key changed, label unchanged.** Treat as a relabelled entry (key change = same
  intent; show it in the `relabelled` bucket so it's visible but not alarming).

## Key references

- Intelligence Service config files: `AuraHealth/IntelligenceService/config/dialogue-feedback.json` and
  `config/journal-feedback.json`
- Notion target: `Tools/Aura API/Dialogue Feedback Labels`
  (page ID `38975e23-af20-816e-9662-e038875662e8`)
- harvest-knowledge integration: [`shared:harvest-knowledge`](../../../shared/skills/harvest-knowledge/SKILL.md)
- Linear: [PROD-2981](https://linear.app/aura-health/issue/PROD-2981)
