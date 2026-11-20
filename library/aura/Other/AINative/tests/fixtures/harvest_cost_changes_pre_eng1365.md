---
name: harvest-cost-changes
description: >
  Sweeps the AI cost-reduction work streams — the Linear "AI Cost Review and Changes" issue
  and children, merged IntelligenceService prompt-release PRs, and Slack
  #ai-prompt-engineering-cost — and updates the Learnings/Aurie Cost Engineering page: appends
  rows to its Cost Changes ledger, flips rows' Status when work ships, refreshes headline
  metrics, and stamps a "Last harvested" watermark so runs stay incremental. Always shows a
  write plan before touching Notion. Trigger on "harvest cost changes", "update the cost
  engineering page", "update the Aurie Cost Engineering page", "sweep cost reduction work",
  "what cost work shipped since the last harvest", "refresh the cost-change ledger", "daily
  cost harvest", "run the cost harvest", or "add this cost change to the ledger". Do NOT use
  for reporting $ amounts (`eng:report-aurie-daily-costs`), per-user cost
  (`eng:report-user-costs`), generic AI Knowledge sweeps (`shared:harvest-knowledge`), or
  releasing a prompt change (`eng:update-intelligence-prompt`).

metadata:
  version: "3.0.2"
knowledge:
  reads:
    - Guide/Index                       # baseline routing aid (universal)
    - Tools/Linear                      # cycle issue sweep via GraphQL
    - Tools/Slack                       # #ai-prompt-engineering-cost history
    - Tools/Notion                      # REST ledger writes
    - Tools/GitHub                      # merged-PR sweeps via gh CLI
    - Learnings/Aurie Cost Engineering  # current ledger state for dedup
  writes:
    - Learnings/Aurie Cost Engineering  # ledger rows, metrics blocks, watermark
---

# Harvest Cost Changes

## Goal

Keep the [Aurie Cost Engineering](https://app.notion.com/p/aurahealth/Aurie-Cost-Engineering-37b75e23af208189b4e2de18e56546e0)
page in AI Knowledge current with near-daily, incremental harvests of the AI
cost-reduction work happening across Linear, GitHub, and Slack. The page's
**Cost Changes** ledger is the deliverable — finance's "Cash flow 2026" page
reads from it (this skill never writes the finance page).

Full page contract, ledger schema, plan JSON schema, and classification
rubric: [references/page-structure.md](references/page-structure.md). Read it
before classifying.

## API-first rationale

This skill leads with API tokens (see `/CLAUDE.md` → "API-first by
necessity"): Notion REST for ledger DB queries/appends and positioned block
edits (MCP `notion-search` can't enumerate or write a database reliably);
Linear GraphQL for the cycle-issue + 50-children + comments sweep in one call;
Slack Web API for paginated channel history + thread replies. GitHub goes
through the `gh` CLI (its own auth). Each source degrades independently — a
missing token skips that source with a note, and the run continues on
whatever remains.

## Workflow

### Step 1 — Fetch sources and current page state

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/harvest-cost-changes/scripts/fetch_sources.py
```

Defaults: all sources, watermark read from the page footer ("Last harvested:
YYYY-MM-DD", falling back to "Compiled" date, then 7 days back). Useful flags:
`--since YYYY-MM-DD` (override window), `--issue PROD-XXXX` (pin the Linear
cycle issue, e.g. right after a cycle rollover), `--sources linear,github`
(subset). Output lands in `/tmp/cost_harvest.json`; stdout prints counts plus
any `DEGRADED:` notes — surface those in the final readout's Sources.

Read the JSON with the Read tool. It contains: the page block map (headline
bullet + footer block IDs), all current ledger rows, the cycle issue with
children/comments, merged PRs per repo, and Slack threads with replies.

### Step 2 — Classify and synthesize (the reasoning step)

Apply the [classification rubric](references/page-structure.md#classification-rubric)
to everything fetched. Build candidates in three buckets:

1. **New ledger rows** — cost-relevant changes not yet in the ledger. Dedup
   against `ledger.rows` by Refs token and by title meaning (not just exact
   string — "Reduce context instructions" and "Trimmed context instructions"
   are the same row).

   **Refs carries identifiers; URLs belong in Impact.** Only identifier-shaped
   tokens are used for dedup — Linear keys (`PROD-4078`, `ENGBACK-1252`) and
   GitHub refs (`IS#409`, `AINative#712`). A Slack permalink or any other URL
   in `Refs` contributes nothing to dedup, so put the link in `Impact` (or in
   the row body) and keep `Refs` to the ticket and PR identifiers plus a short
   channel/date marker like `#ai-cost 2026-08-02`. A row whose `Refs` carries
   no identifier is deduped on its title alone, which is the intended fallback,
   not a failure. Verify the tokenizer with
   `python3 scripts/apply_updates.py --self-test`.
2. **Row updates** — existing `Idea` / `In progress` rows where a source shows
   the work shipped or was reverted: flip `Status`, set `Date`, append `Refs`.
   Prefer an update over a near-duplicate new row.
3. **Headline metric refreshes** (only when the daily CSV stores are present
   locally — see the
   [reference](references/page-structure.md#headline-metrics-refresh-opt-in-step);
   never guess numbers) and **narrative proposals** (rare — only when a sweep
   surfaces a genuinely new arc worth a chapter).

Slow down before drafting: a Slack thread is ledger-worthy only if it
describes a shipped change or a concrete decision. When unsure whether
something is cost-relevant, leave it out and mention it under "Not included"
in the plan instead.

### Step 3 — Present the plan and wait for approval

Show the user a compact plan before any write:

- a table of proposed new rows (Change / Area / Status / Date / Refs / Impact)
- proposed row updates (existing row title → what changes)
- proposed block edits (old text → new text)
- a "Not included" list of borderline items, one line each
- the watermark date that will be stamped

**Do not write anything until the user approves.** On approval, write the plan
JSON (schema in the
[reference](references/page-structure.md#plan-json-schema-apply_updatespy---plan))
to `/tmp/cost_harvest_plan.json` with the Write tool.

### Step 4 — Apply

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/harvest-cost-changes/scripts/apply_updates.py \
  --plan /tmp/cost_harvest_plan.json
```

The script validates Area/Status enums (aborts on unknown values), re-checks
every new row against the live ledger (skips title/Refs collisions, so a
re-run of an applied plan is a no-op), applies rows / row updates / block
edits, stamps `Last harvested: <date>` on the page footer, and deletes the
local AI Knowledge cache file for the page. `--dry-run` previews without
writing.

Each `block_update` is pre-checked against the live page: a block a parallel
session archived/replaced since the fetch is **skipped** with a
`BLOCK SKIP (… re-fetch sources and re-plan)` note rather than crashing the run
(the watermark stamp + cache invalidation always run). A skip means the fetch
was stale — re-run Step 1 and rebuild that edit against the fresh block id.

### Step 5 — Confirm and summarize

Report: rows added / updated / skipped, blocks updated, the new watermark,
the page link, and any degraded sources. If nothing cost-relevant turned up,
say so and still stamp the watermark (empty plan) so the next run's window
stays tight.

## Edge cases

- **No watermark on the page** (first run, or footer edited away) → window
  defaults to 7 days; say so and offer `--since` for a deeper backfill.
- **Cycle rollover** — the fetch picks the most recently *updated* "AI Cost
  Review and Changes" issue. In the first days of a new cycle the previous
  cycle's issue may still win; pass `--issue` to pin the right one.
- **Missing tokens** — each source degrades independently (`DEGRADED:` notes).
  For Linear/Slack, MCP one-off lookups can partially fill the gap; never
  block the whole harvest on one source. Walk the user through `update-config`
  only if they ask.
- **Ambiguous classification** — put it in "Not included" with one line of
  reasoning; the user can promote it during approval.
- **Re-run after apply** — idempotent: dedup guards skip everything already
  written; only the watermark moves.
- **`BLOCK SKIP` in the apply output** — a parallel session archived/replaced
  the target block since the fetch (this page is co-curated). The run still
  completes and stamps the watermark; re-run Step 1 to get the live block id and
  re-apply just that edit. Seen 2026-06-12 ([PROD-2695](https://linear.app/aura-health/issue/PROD-2695)).
- **Select-option drift** — if the team adds a new Area/Status option in
  Notion, update `AREAS` / `STATUSES` in `apply_updates.py` and the reference
  doc in the same PR.

## Output

A chat readout: plan table(s) → approval → apply summary (counts + page link
+ degraded-source notes). All writes land only on
`Learnings/Aurie Cost Engineering`.
