---
name: aurie-web-experiment-history
description: >
  Pull historical experiment context for Aurie Web (a.k.a. AIWeb / AI Web)
  ideation and analysis — two slices of the Learnings/Experiments Notion DB:
  (1) Aurie Web rows for dedup baseline and direct precedent, (2) Aura Web
  rows for transferable pattern priors (theme-tagged synthesis, never
  row-level reuse). Categorizes by Status, extracts Won/Lost themes, surfaces
  under-tested screens (when given a flow_map), and identifies recurring
  winning + failing patterns. Returns experiment_state + experiment_history
  for any caller skill (ideation, portfolio review, retros). Trigger when
  the user wants to see what Aurie Web experiments have been tried, what
  patterns won/lost, or what Aura Web learnings might transfer to Aurie Web.
metadata:
  version: "3.0.3"
knowledge:
  reads:
    - Guide/Index                       # baseline routing aid (universal)
    - Learnings/Experiments             # both Aurie Web + Aura Web slices
    - Tools/Notion                   # Notion REST / MCP access pattern (NOTION_TOKEN)
---

# aurie-web-experiment-history

A read-only skill that returns historical experiment context for Aurie Web. Other skills (`aurie-web-onboarding-experimentation-ideas`) call this one to ground new ideas in prior outcomes — and to enforce dedup against work that's already been done.

**Scope:** Reads `Learnings/Experiments` Notion DB (`2cbe45a3246040658b9f20559b5b452b`). Two slices:

1. **Aurie Web rows** (Platform == "Aurie Web") — primary source for dedup + direct precedent. Used as the dedup baseline by ideation.
2. **Aura Web rows** (Platform == "Aura Web") — transferable pattern priors only. Theme-tagged synthesis; **never** row-level reuse (Aura Web ≠ Aurie Web — different funnels, different brand voice).

**What this skill is NOT:**

- **Not an analyzer.** It does not compute new metrics from Mixpanel — that's `data:analyze-experiment`. This skill returns rows the team has already analyzed (and synthesizes patterns from them).
- **Not a writer.** It never creates or updates rows in `Learnings/Experiments`. The ideation skill (`aurie-web-onboarding-experimentation-ideas`) owns writes; this skill is read-only.
- **Not Aura-Web-canonical.** Aura Web rows are read for *transferable patterns only*. Aura is a different surface; this skill never proposes that an Aura experiment "applies as-is" to Aurie.

## When to use

Trigger when the user says any of (`Aurie Web`, `AIWeb`, and `AI Web` all mean the same thing — match on any):

- "what Aurie Web experiments have we tried"
- "Aurie Web experiment history"
- "what Aura Web wins might transfer to Aurie Web"
- "show me Aurie Web experiment patterns"
- "which Aurie Web screens are under-tested"
- "what's the Aurie Web experiment state"

Also runs as a sub-step when called by `aurie-web-onboarding-experimentation-ideas`.

## Inputs

The skill takes no required arguments. Optional flags:

- `aurie-only` — skip the Aura Web slice entirely. Faster, useful when you only need dedup data.
- `aura-only` — skip the Aurie Web slice. Useful when you want pattern priors without dedup context.
- `flow_map=<json>` — when called as a sub-skill with a flow_map already in memory, pass it through. Used to compute `under_tested_screens` (Aurie Web screens with zero prior experiments). When standalone, this comparison is skipped.
- `limit=<N>` — cap rows fetched per slice. Default `200`.

Default: fetch both slices, no flow_map cross-reference.

## Prerequisites

### Hard requirements

- **Notion access** to `Learnings/Experiments` (DB id `2cbe45a3246040658b9f20559b5b452b`, data source id `c0209685-17ac-49e2-849a-85fa8178decd`). At least one of:
  - `NOTION_TOKEN` set in `~/.claude/settings.json` env — **strongly preferred**. The REST path enumerates the DB with property filters + cursor pagination; required for accurate dedup at scale. See root [CLAUDE.md → Notion REST integration](../../../../CLAUDE.md#notion-rest-integration).
  - **Notion MCP** connected — fallback only. `notion-search` is semantic, capped at 25 results per query, with no cursor. Best-effort enumeration via diverse queries + de-dupe by page id; the dedup baseline will be incomplete. The skill must mark the run output `dedup_completeness: partial` in this mode.
- The skill ships its own copy of [`scripts/notion_rest.py`](scripts/notion_rest.py) (canonical at `plugins/shared/skills/retrieve-knowledge/scripts/notion_rest.py`; keep in sync per the convention in root CLAUDE.md).

### Soft requirements

None. This skill is purely a read against one Notion DB.

### Cowork caveat

`mcp__workspace__bash` strips host env ([PROD-1305](https://linear.app/aura-health/issue/PROD-1305)), so `NOTION_TOKEN` won't reach Cowork sandboxes. In that case the skill degrades to the MCP semantic-search path documented in Steps 1 and 2; surface the `dedup_completeness: partial` warning in the run output.

## Workflow

### Step 0 — Entry banner

The very first thing this skill does on every invocation is generate a fresh `run_id` and print an entry banner. This banner is how callers (orchestrator, user) verify the skill actually ran instead of being silently shortcut by an agent reusing prior context.

Generate `run_id = "ehs-" + random 8-char hex` (fresh on every invocation — never reuse a `run_id` from a prior turn).

Print:

```
🔧 aurie-web-experiment-history starting (run_id: <id>)
   invoked_by: <caller-skill-name | "user">
   invoked_at: <ISO timestamp>
   inputs: { aurie-only: <bool>, aura-only: <bool>, flow_map: <bool>, limit: <N> }
```

The `invoked_at` timestamp must be the actual current time at the start of this run — not lifted from a prior run. The `run_id` proves a fresh invocation.

### Step 1 — Read Aurie Web slice (dedup baseline)

Pull current `Learnings/Experiments` rows scoped to Aurie Web. This is what dedup decisions in ideation operate against.

**Skip this step if** the user passed `aura-only` — set `experiment_state = null` and continue.

**REST path — preferred when `NOTION_TOKEN` is set.** One filtered call paginated to completion returns every Aurie Web row. Run from `python3` using the bundled `notion_rest.py`:

```python
import os, sys
sys.path.insert(0, f"{os.environ['CLAUDE_PLUGIN_ROOT']}/skills/aurie-web-experiment-history/scripts")
from notion_rest import notion, MCPFallback, title_text, select_value, date_start, rich_text_value

DS_ID = "c0209685-17ac-49e2-849a-85fa8178decd"  # data source id for Learnings/Experiments

if not notion.available:
    raise MCPFallback("NOTION_TOKEN unavailable; use the MCP path documented below")

rows = list(notion.query_data_source(
    DS_ID,
    filter={"property": "Platform", "select": {"equals": "Aurie Web"}},
    sorts=[{"property": "Start Date", "direction": "descending"}],
    page_size=100,
))
```

The helper paginates internally on `has_more`/`next_cursor` until exhausted — you receive every row in one call. Notion's per-response `page_size` max is **100** (do not hand-set higher). For each page, use the accessors from `notion_rest`: `title_text(page, "Title")`, `select_value(page, "Status")`, `select_value(page, "Platform")`, `date_start(page, "Start Date")`, `rich_text_value(page, "Hypothesis")`.

**MCP fallback — when `NOTION_TOKEN` is unavailable (e.g. Cowork sandbox).** `notion-search` against the data source URL is semantic and capped at 25 results per query with no cursor — a single call cannot enumerate the DB. Run multiple diverse queries (e.g. `paywall`, `quiz`, `landing`, `loader`, `signup`, `trust`, `email gate`) and union the surfaced page ids; follow up with `notion-fetch` on each unique id for properties:

```yaml
notion-search:
  query: "Aurie Web onboarding"
  data_source_url: "collection://c0209685-17ac-49e2-849a-85fa8178decd"
  page_size: 25
  max_highlight_length: 0
```

This produces a partial baseline (semantic relevance, 25-result cap × N queries) — surface `dedup_completeness: partial` in the run output so callers understand some rows may have been missed.

For each returned row, capture:

- `Title` (= `Experiment Name`)
- `Status` (one of `Running`, `Won`, `Lost`, `Inconclusive`, `Turned off`, or blank)
- `Start Date`, `End Date`
- `Experiment Type`
- `Hypothesis`
- The page body — specifically the `## What was tested` section, since for ideate-generated rows the idea-specific fields (`Target Screen`, `Target Metric`, etc.) live there as `<key>: <value>` markers.

Build an in-memory `experiment_state` indexed by `Title.toLowerCase()` for O(1) dedup lookups by callers.

> **Used downstream for variant decision.** When a caller passes `flow_map=<json>` later in the same run, R1 in `aurie-web-flow-snapshot`'s `flow-extract.md` filters this `experiment_state` for rows whose `Title` matches the literal `^webOnboarding[A-Z][A-Za-z0-9]*$` regex (the team uses the exact `AB_EXPERIMENTS` constant value as the Notion title). The most recent `Won` row by `End Date` desc names the active variant.

Categorise by `Status` for the user-visible summary, mapping to pipeline phases (per `aurie-web-onboarding-experimentation-ideas/references/idea-schema.md`):

| Notion `Status` | Pipeline phase |
|---|---|
| blank + `Phase: Idea` body marker | Idea |
| blank + `Phase: In Review` body marker | In Review |
| blank + no marker | Idea (default for legacy bulk-imported rows) |
| `Running` | Running |
| `Won` | Passed |
| `Lost` | Failed |
| `Inconclusive` | Complete |
| `Turned off` | Archived |

#### Print

```
Experiment state (Learnings/Experiments, Platform = Aurie Web):
  Total rows: 47
  Idea: 12   In Review: 4   Running: 3   Passed: 8   Failed: 11   Complete: 6   Archived: 3
```

### Step 2 — Read Aura Web slice (transferable pattern priors)

Build pattern priors from Aura Web rows. This slice powers Step 5 ideation grounding (transferable Aura Web wins applied with Aurie brand voice).

**Skip this step if** the user passed `aurie-only` — set `experiment_history.aura_web = null` and continue.

Query `Learnings/Experiments` again with the same REST pattern from Step 1, swapping the filter value to `"Aura Web"`:

```python
rows = list(notion.query_data_source(
    DS_ID,
    filter={"property": "Platform", "select": {"equals": "Aura Web"}},
    sorts=[{"property": "Start Date", "direction": "descending"}],
    page_size=100,
))
```

MCP fallback follows the same diverse-query + page-id-union pattern as Step 1. The Aura Web slice has **~330+ rows** as of 2026-05 — REST returns every one in ~5 paged calls; MCP fallback typically surfaces only the most recent 50-100. Under MCP fallback, mark the run `pattern_priors_completeness: partial`. Pattern synthesis below operates on whatever was returned, but the partial flag is required so downstream ideation knows winning-theme ratios may be skewed by the relevance bias.

For each row, capture `Title`, `Status`, `Hypothesis`, and the `## Learning` body section (where applicable).

#### Synthesize patterns

1. Group rows by inferred theme (`paywall`, `copy/text`, `social proof`, `loader/animation`, `personalization`, `sleep flow`, `get-app`, `results-page`, etc.) using `Title` + `Hypothesis` + `## Learning` body keywords.
2. Within each theme, count `Won` / `Lost` / `Inconclusive` / blank.
3. Surface the top **winning** patterns (themes with ≥2 wins and a positive win-loss ratio) and recurring **failure** patterns (themes with ≥3 losses, or losses outnumbering wins by 2× or more).
4. Cap to the top 3 winning + top 2 losing themes — anything below that line isn't load-bearing for ideation.

> **Aura Web ≠ Aurie Web.** Use Step 2's output for **transferable pattern priors only**, not for dedup. Dedup runs against Step 1's slice only — never auto-drop an Aurie Web idea because a similarly-named Aura Web row exists; the funnels and brand voice differ enough that overlap is acceptable.

### Step 3 — Aurie Web pattern synthesis (from Step 1's rows)

Use Step 1's rows (already in memory) to extract Aurie-Web-native winning and losing patterns. No second query.

1. Categorize Step 1 rows by `Status`: `Won`, `Lost`, `Inconclusive`, `Turned off`, `Running`, blank.
2. Extract winning patterns from `Won` rows — read the `## Learning` body section.
3. Extract failure patterns from `Lost` and `Turned off` rows.
4. **If `flow_map` was passed in:** cross-reference `Target Screen` (from native field if present, else from the body's `Target Screen:` marker) against `flow_map.screen_name` to identify under-tested screens (zero prior experiments).

### Step 4 — Return artifact

Return:

```yaml
experiment_history_run:
  run_id: <ehs-<8-char-hex> from Step 0 entry banner>
  invoked_by: <caller-skill-name | "user">
  invoked_at: <ISO timestamp from Step 0>
  read_at: <ISO timestamp — when Notion queries actually returned; this is the freshness signal>
  experiment_state:                          # from Step 1; null if aura-only
    rows: [...]                              # full row objects
    by_title_index: { ... }                  # lowercase Title → row, for O(1) dedup
    by_phase:
      Idea: <int>
      "In Review": <int>
      Running: <int>
      Passed: <int>
      Failed: <int>
      Complete: <int>
      Archived: <int>
    total: <int>
  experiment_history:
    aurie_web:                               # from Step 3; null if aura-only
      winning_patterns: [...]                # one entry per recurring win theme
      failure_patterns: [...]
      under_tested_screens: ["<screen_name>", ...]   # only when flow_map was passed
    aura_web:                                # from Step 2; null if aurie-only
      total_rows: <int>
      winning_themes: [...]                  # top 3, each "<theme> (<X>W / <Y>L)"
      losing_themes: [...]                   # top 2
```

After the slice readouts (whether standalone or as a sub-call), always print the exit banner:

```
✅ aurie-web-experiment-history complete (run_id: <same id from Step 0>)
   notion_query_at: <ISO timestamp — last successful Notion query>
   aurie_rows: <int>   aura_rows: <int>
   elapsed_ms: <int>
```

When invoked standalone (not as a sub-call), also print the slice summary:

```
Historical experiments (Aurie Web — Learnings/Experiments where Platform == "Aurie Web"):
  Total: 23   Won: 7   Lost: 8   Inconclusive: 4   Turned off: 2   Running: 2

  Winning patterns:
    - <recurring win pattern 1>
    - <recurring win pattern 2>

  Failure patterns:
    - <recurring loss pattern 1>

  Under-tested screens (0 prior experiments): <list — only if flow_map was passed>

Aura Web pattern priors (Learnings/Experiments where Platform == "Aura Web"):
  Total: <N> rows queried
  Recurring wins: <top 3 themes — each "<theme> (<X>W / <Y>L)">
  Recurring losses: <top 2 themes — each "<theme> (<X>W / <Y>L)">
```

## Output format

When invoked **as a sub-call**, return the `experiment_history_run` object in memory. The caller passes `experiment_state.by_title_index` to its dedup logic and `experiment_history.aura_web.winning_themes` to its grounding logic.

When invoked **standalone**, print the per-slice summary above. Do not print the full row list — it can be hundreds of rows. If the user wants a specific row, ask for the Title and print just that row's body.

## Notes and gotchas

- **One Notion DB, two filters.** Both slices read the same database (`2cbe45a3246040658b9f20559b5b452b`). The `Platform` property is the only thing that differs between queries.
- **Aura Web rows are theme-tagged, not directly reusable.** The `experiment_history.aura_web.winning_themes` are *categories* (e.g. "social_proof at decision points"), not concrete experiment definitions. Callers that paste an Aura Web Title into an Aurie Web hypothesis are misusing this output.
- **`under_tested_screens` requires `flow_map`.** When called standalone, the cross-reference is skipped — the skill can't know which Aurie Web screens exist without one. Callers wanting under-tested signal must pass `flow_map`.
- **Sibling to `data:analyze-experiment`.** Both read `Learnings/Experiments`. The split: `analyze-experiment` *creates / enriches* rows (post-mortem flow); this skill *reads* rows for ideation context. Don't merge them.
- **Caching across runs.** Same-session callers (e.g. `aurie-web-onboarding-experimentation-ideas` after a previous audit run in the same Cursor session) can pass through the previous `experiment_state` instead of re-querying. The skill returns identical data for the same DB state — re-querying is just slower.

## References

### Notion knowledge

- **`Learnings/Experiments`** — DB id `2cbe45a3246040658b9f20559b5b452b`. Two slices read: `Platform == "Aurie Web"` (dedup) and `Platform == "Aura Web"` (pattern priors). URL: <https://www.notion.so/2cbe45a3246040658b9f20559b5b452b>.

### Sibling skills

- `aurie-web-flow-snapshot` — independent peer; provides `flow_map` for the optional under-tested-screens cross-reference.
- `aurie-web-onboarding-audit` — independent peer; both feed ideation.
- `aurie-web-onboarding-experimentation-ideas` — top-level orchestrator that calls this skill in its main flow.
- `data:analyze-experiment` — sibling that *writes* to the same DB (post-mortem analysis); this skill is read-only.
