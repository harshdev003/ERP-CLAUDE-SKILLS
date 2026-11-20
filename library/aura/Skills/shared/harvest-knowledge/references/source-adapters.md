# Source adapters (Step 4b)

Adapters extend the harvest with changes that live outside Notion. Each runs after Step 4 and folds what it finds into the write plan before Step 6. All are non-blocking: an adapter that errors is logged in the Step 8 summary and the harvest continues.

## Adapter: `eng:sync-feedback-config`

Checks whether the Intelligence Service feedback config files (`config/dialogue-feedback.json`, `config/journal-feedback.json` in `AuraHealth/IntelligenceService`) have drifted from the AI Knowledge page `Tools/Aura API/Dialogue Feedback Labels`. Invoke it via the `Skill` tool in `--mode diff` (JSON-only output, no interactive prompt):

```
Skill: eng:sync-feedback-config
Args: --mode diff
```

**Why via `Skill` tool, not a direct script path.** `${CLAUDE_PLUGIN_ROOT}` resolves to the `shared` plugin root inside a `shared` skill — it cannot address scripts in `plugins/eng/`. Direct cross-plugin file references violate Architecture Principle 1. The `Skill` tool invokes at the harness level so the plugin loader resolves `eng:sync-feedback-config` correctly with its own `${CLAUDE_PLUGIN_ROOT}`.

**Auth requirement.** The adapter reads from `AuraHealth/IntelligenceService` via the `gh` CLI. `gh auth status` must be authenticated; if it isn't, note the miss in the Step 8 summary (`source adapter eng:sync-feedback-config skipped — gh not authenticated`) and continue without blocking the harvest.

**Interpreting the result.** The skill emits a JSON object:

```json
{ "changed": true|false, "notion_source": "live"|"baseline", "diff": { ... }, "configs": { ... }, "as_of": "YYYY-MM-DD" }
```

- **`changed: false`** → no-op; note `eng:sync-feedback-config: no drift detected` in the Step 8 summary.
- **`changed: true` and `notion_source: "live"`** → add an entry to the write plan:
  - **Target:** `Tools/Aura API/Dialogue Feedback Labels`
  - **Action:** update the page to reflect the diff (added / removed / relabelled labels per section)
  - **Context:** include `diff` and `as_of` from the JSON as the change rationale
  - **How to apply in Step 7:** write the update directly via the Notion REST helper (which harvest already uses for all Step 7 writes). The JSON carries `notion_page_id`, `diff` (added/removed/relabelled per section), and `configs` (full current label lists) — use these to update the page's label-section blocks. **Fetch the page with `get_block_children(..., recursive=True)`** — label text lives in nested bullets under section headings; a shallow (non-recursive) fetch misses them. Harvest's Step 7 write path already handles cache invalidation for any page it touches. Do **not** re-invoke `eng:sync-feedback-config` in interactive mode — that skill runs its own plan-and-confirm loop which conflicts with harvest having already obtained user approval in Step 6.
  - This entry joins the Step 6 write plan as a proposed **update**, subject to user approval like any other plan item.
- **`changed: true` and `notion_source: "baseline"`** → skip the write-plan entry; the diff was computed against a hardcoded baseline because Notion was unreachable, so it may not reflect true drift. Note in the Step 8 summary: `eng:sync-feedback-config: diff against baseline only (Notion unreachable) — write plan entry skipped to avoid false positives`.

**Non-blocking.** Any error from the adapter (GitHub unreachable, Notion token missing for the diff baseline, unexpected output shape) is caught here: log the error in Step 8 summary and continue. The adapter is a supplement to the main harvest, not a gate on it.
