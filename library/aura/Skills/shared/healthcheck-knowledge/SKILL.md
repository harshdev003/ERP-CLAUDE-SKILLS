---
name: healthcheck-knowledge
description: >
  Periodic (or on-demand) health check over the AI Knowledge Notion teamspace. Detects
  contradictions, stale claims, orphan pages, missing concepts, data gaps, duplicates, and
  outdated links. Writes findings to a fresh per-run child page under `Guide > Health Check`,
  preserving every prior run as an immutable audit trail, and reads the most recent prior run
  before flagging so deferred or rejected findings aren't re-surfaced. Then walks findings
  interactively with the user (apply / apply alternative / reject / defer), records decisions,
  and applies approved fixes. Complements `ingest-knowledge` (on-demand single-source write)
  and `harvest-knowledge` (periodic upstream sync). Trigger when the user says "check AI
  Knowledge health", "run knowledge healthcheck", "audit AI Knowledge", "find contradictions
  in AI Knowledge", "/wiki-healthcheck", "scan the knowledge base for drift", "what's stale in
  the wiki", or asks to check the knowledge base for orphans, duplicates, or broken links.

metadata:
  version: "3.7.6"
knowledge:
  # Cross-cutting: audits the whole teamspace and applies approved fixes on any content page.
  reads:
    - Guide/Index            # baseline routing aid (universal)
    - Guide/Placement Rules  # explicit so it's pre-fetched even with the wildcard
    - Tools/Notion           # explicit — REST + MCP access patterns this skill leans on
    - Tools/Slack            # channel directory + Post format footer spec for the optional Step 8 summary post
    - "*"                    # cross-cutting: reads anywhere in the teamspace
  writes: ["*"]
---

# Healthcheck Knowledge

Periodic audit of the AI Knowledge teamspace. Surfaces drift, duplication, and stale claims so humans can fix them. The skill produces a fresh per-run page under `Guide › Health Check` and — guided by the user — applies approved fixes to content pages. Every decision is logged on the run page so the next run can respect it.

**Core contract:**
- **One page per run.** Every run creates a new child page under `Guide › Health Check` named `YYYY-MM-DD-runN`. Prior run pages are never overwritten — they are the audit trail.
- **Prior-run awareness.** Before detectors run, the skill reads the most recent prior run page and respects its Decision log (rejected findings are suppressed; deferred/open findings are carried forward as "Recurring").
- **Two-phase edits.** Unambiguous formatting-only fixes (e.g. adding an empty `# Sources` heading, `## Sources` → `# Sources`) are auto-applied without approval. Anything involving content judgement requires an interactive per-finding decision.
- **Guide-compliant.** Authoring rules (`# Sources`, voice-playbook, AI scope of edits) come from the Guide at runtime. If the Guide changes, detectors honour the new rules on the next run.
- **Fail open on detectors, fail closed on the entry point.** One broken detector doesn't abort the run — it's noted on the run page and the rest continues. The single exception is Step 2 resolving zero sections from About: every detector would then run against an empty crawl and report a healthy teamspace, so the run aborts instead (rule 5).
- **Stateless between runs except via Notion.** No local state; the bundled files are execution detail rather than knowledge (`scripts/notion_rest.py`, `references/about-sections.md`, `references/detectors.md`). Continuity comes from reading the prior run page.

---

## Inputs

- `severity=all|high|medium` (optional, default `all`) — filter report to findings at or above this severity.
- `slack=true|false` (optional, default `false`) — post a short summary to Slack after the report is written. Recipient/channel is a first-use TODO (see Step 8).
- `mode=plan|apply` (optional, default `apply`) — `plan` previews the run-page content in chat without writing to Notion or editing content pages; `apply` creates the run page and runs the interactive review loop.
- `interactive=true|false` (optional, default `true`) — when `false`, skip the per-finding review loop (report lands, no human-approved edits applied). Pre-review auto-applied edits still run.

---

## Detection categories

Thirteen detectors, in priority order. Detectors 1–5 are the baseline; 6 is a stretch goal. Each runs against the per-page capture from Step 4 and is independent — one erroring is logged and the rest continue.

| # | Detector | Severity |
|---|---|---|
| 1 | Contradictions | High |
| 2 | Stale claims | Medium |
| 3 | Orphan pages | Medium |
| 4 | Missing concepts | Low |
| 5 | Data gaps (missing `# Sources`, skeleton pages) | High / Medium |
| 6 | Duplicate content *(stretch)* | Low |
| 7 | Outdated links (four arms, split by decidability) | Medium |
| 8 | Linear **card** links in knowledge content (product URLs are fine) | Medium |
| 9 | `Guide/Index` drift | Medium / Low |
| 10 | Placement violations | High / Medium |
| 11 | Broken internal links (`[[Path]]`) | Medium |
| 12 | Skill ↔ `Tools/Slack` declaration gap | Medium / Low |
| 13 | Repeated workaround note across database rows | High / Medium |

Full definitions — what each matches, how it decides, its exclusions and suggested fixes: **[references/detectors.md](references/detectors.md)**. Read it before running Step 5; the heuristics carry the exclusions that keep the report signal-bearing.

### Severity rubric

| Severity | Examples |
|---|---|
| **High** | Contradictions; broken internal Notion links; pages missing `# Sources` when the Guide's authoring rules require it; the same workaround note in three or more rows. |
| **Medium** | Stale claims; orphan pages; data gaps; broken external URLs; wiki-link syntax (`[[Path]]` doesn't render as a link). |
| **Low** | Missing-concept hints; suspected duplicates below strong-match threshold. |

---

## Step 1: Resolve inputs

As above. Stateless — no local state file. Continuity with prior runs is read from Notion in Step 3.

---

## Step 2: Learn taxonomy + rules from the Guide

Only one Notion ID is hardcoded — same entry point as `ingest-knowledge` and `harvest-knowledge`. Keep them in sync.

```
AI_KNOWLEDGE_ABOUT_PAGE = "9ee75e23af20830aa49101a40466a789"
```

1. `notion-fetch(AI_KNOWLEDGE_ABOUT_PAGE)` → resolve the top-level sections from **the links in About's body**, not from its child pages. See [references/about-sections.md](references/about-sections.md) — read it before implementing this step.
2. `notion-fetch(<Guide page-id>)` → read the current knowledge types and authoring rules (`# Sources` discipline, voice-playbook, AI scope of edits). The detectors honour whatever the Guide currently says. New authoring rules on the Guide are picked up automatically on the next run.
3. `notion-fetch(<Guide/Placement Rules page-id>)` → read the current placement policy. Detector #10 (Placement violations) compares every page's `parent` against this. If the page is missing, fail open: log a note in Step 8's Detector status and skip detector #10 for this run.
4. For each section resolved from About, walk its subtree **to the leaves**, in parallel per section. Do **not** stop at direct children, and do not special-case which branches get depth — a fixed depth with hand-picked exceptions is what silently drops whole families of pages from every detector's input (`Tools/Aura API/*` is three levels deep, `Procedures/AI Native/*` two). Bound the walk at **depth 6** from the section root as a runaway guard, and record any page that hits it under Detector status rather than dropping it silently.
   **Exclusions, applied by path rather than by depth:** individual dated `Signals/<topic>/<...>` snapshot child pages (the parent lists them) and `Guide/Harvest/*` / `Guide/Health Check/*` run pages (immutable audit records — see the detector-#8 note on why flagging them produces unactionable findings).

   **Match a snapshot's date anywhere in the title, not as a prefix.** The producing skills name these `<Producer> — YYYY-MM-DD snapshot` (`Aurie Conversation Report — 2026-08-24 snapshot`, `Braintrust Topics — 2026-08-24 snapshot`, `Aurie Weekly Cost — 2026-08-23 snapshot`), so an anchored `^\d{4}-\d{2}-\d{2}` matches none of them:

   ```python
   SNAPSHOT = re.compile(r"(^|\s)\d{4}-\d{2}-\d{2}(\s|$)")
   is_snapshot = page.section == "Signals" and page.depth >= 2 and SNAPSHOT.search(page.title)
   ```

   A missed exclusion here is not a crawl-size detail — it feeds 26 immutable snapshot pages into every detector, and their findings are unactionable by the same rule that protects run pages. It also moves the headline page count, so two runs stop being comparable.
5. Resolve the `Health Check` parent page under Guide (the index page). If it does not exist, present the proposed creation in chat (first-run plan) and create it with the parent-index template in Step 7.

Build an in-memory map: `{ section_name → { page_id, children: [...], databases: [collection_url] } }`, plus `health_check_parent_id`.

### Resolving About's sections — body links, not child pages

**The sections are inline links in About's body rich_text, not child pages of About.** A `child_page` walk returns exactly one result (`Test Page - Delete`) and zero sections, handing every downstream step an empty taxonomy while looking like it succeeded.

Full recipe, the verified section list, and the hard-error rule: **[references/about-sections.md](references/about-sections.md)**. Read it before implementing Step 2 — an empty section map aborts the run rather than continuing.
---

## Step 3: Read the most recent prior run

This step fetches the run pages **directly** and is deliberately unaffected by Step 2's crawl exclusion of `Guide/Health Check/*`. The exclusion keeps run pages out of the *detectors'* input (they are immutable, so findings against them are unactionable); it does not hide them from the skill. Do not "reconcile" the two by re-including run pages in the crawl.

List the children of the `Health Check` parent page. Parse page titles against the `YYYY-MM-DD-runN` pattern and pick the latest (latest date first; within the same date, highest `N`). Call this the **prior run page**.

If a prior run page exists, `notion-fetch` it and parse its **Decision log** table into a map keyed by `(page_url, category, subcategory)`:

```
prior_decisions = {
  (page_url, category, subcategory): { decision, action, severity, finding_title, run_url }
}
```

Where `decision ∈ {Approved + Fixed, Rejected, Deferred, Open}`. If the run page is missing the Decision log (corrupt or mid-migration), log a warning under Detector status and treat `prior_decisions` as empty.

If no prior run page exists, `prior_decisions = {}` (this is the first run).

---

## Step 4: Crawl the teamspace

Walk every page discovered in Step 2. For Learnings databases, sample recent rows (cap e.g. 20 per DB to keep the crawl bounded).

**The 20-row cap bounds the *page* crawl and does not bound detector #13.** `Learnings/Experiments` holds 3,087 rows; a 20-row sample could not have seen its 13-row workaround-note cluster even in principle, and an empty result under the cap reads exactly like a clean database. So build #13's corpus separately — its own bounded pre-filter is fine (rows edited since the prior run, or rows in a terminal `Status`), as long as the bound is **declared** as `scanned` / `total` and the run reports **PARTIAL** rather than clean. The corpus supplies each row as **blocks carrying their `hrefs`** (Step 4 already captures those) — #13 acquits a note that links its card, and in this teamspace a card link lives only in the href, so a flat-text corpus reports `acquittal: BLIND` rather than a clean-looking zero. It also declares the `database` it came from (`{path, url}`), because #13's findings anchor on the database rather than on a member row — a row anchor moves the moment the cluster gains a row that sorts first, and the prior run's decision is then keyed to a page this run no longer names. See [references/detectors.md](references/detectors.md) #13.

Capture per page:

```
{ title, url, section, path, depth, parent_id, parent_title, last_edited,
  outbound_links[], has_sources_section, bolded_terms[], child_count,
  blocks: [ { id, type, heading, text, hrefs[] } ],
  body }
```

`parent_id` / `parent_title` come from the page's `<ancestor-path>` (immediate parent only). Empty `<ancestor-path>` means the parent is the workspace or teamspace root — those are the High-severity placement violations detector #10 catches. `child_count` is the number of children the page has **in Notion**, counted from the live child listing — *not* the number that survived Step 2's crawl exclusions. A `Signals/<topic>` snapshot parent has its dated children excluded from the crawl but is emphatically not a leaf. Detector #10's leaf gate depends on that distinction: counting only crawled children makes every snapshot parent look childless, and it is then flagged as a misfiled leaf when it is nothing of the kind. `path` is the full slash-separated path used by every finding.

**Capture blocks, not a rendered body string.** Fetch with `get_block_children(..., recursive=True)` and keep one entry per block carrying four things a flat body string destroys.

**`table_row` keeps its content somewhere else.** A table row has no `rich_text` key — its content is `table_row.cells`, an array of cells each of which is its own rich_text array. A capture that reads `block[type].rich_text` alone sees every table as empty, so both `text` and `hrefs[]` come back blank for it. Flatten the cells into the block's `text` and `hrefs[]`. This is not hypothetical: linked Linear tickets live in tables on `Tools/OpenAI` and `Tools/Mixpanel/Event Approval` today, and a rich_text-only scan reports zero for both.

**Pass an explicit `max_depth`.** `get_block_children(..., recursive=True)` defaults to `max_depth=3`, so blocks nested deeper — a bullet inside a toggle inside a column — are dropped without a signal, taking their headings and `hrefs[]` with them. Pass `max_depth=6` and, when a block at the bound still reports `has_children`, record the page under Detector status as partially captured rather than treating its capture as complete.

**Skip `child_page` subtrees while walking the blocks.** `recursive=True` descends into child pages too, so without the skip a parent absorbs its children's links, headings and `Sources` content — every ref on `Tools/Aura API/Packages` would also be attributed to `Tools/Aura API`, inflating counts and pointing findings at the wrong page. The page tree walk (Step 2) already visits children as pages in their own right; this walk is strictly about *this* page's body. Same trap [`references/about-sections.md`](references/about-sections.md) documents for About.


| Field | Why a detector needs it |
|---|---|
| `type` | `# Sources` detection must test `block.type == 'heading_1'`, never a regex over rendered text — Notion strips the `#` (detector #5's documented trap) |
| `heading` | the nearest preceding heading, carried down as you walk in document order. This is the **only** way to say which section of a page a hit sits in |
| `text` | `rich_text` plain text, for prose matching |
| `hrefs[]` | every link the block carries, from **three** places: `rich_text[].href`, `rich_text[].mention.page.id` for page mentions, and `block.link_to_page.page_id` for `link_to_page` blocks (which store the target on the block, not in any rich_text — the same shape [`references/about-sections.md`](references/about-sections.md) collects from About). **Store every id un-hyphenated.** The API returns `link_to_page.page_id` and `mention.page.id` as hyphenated UUIDs while an id embedded in an `href` is not, so a reverse search for a bare page id matches one form and misses the other unless both sides are normalised at capture time. **A link's URL is usually not in the text at all** — a bullet reading `PROD-2365 — Create PVC in Cartesia` carries `linear.app/...` only in its href. A text-only scan misses ~97% of the Linear references in this teamspace |

Deriving `heading` is the whole fix for section attribution. Walk **every** block in document order — including blocks nested under toggles, indented list items and columns — carrying a `current_heading` that updates whenever `type` starts with `heading` and is stamped on every block until the next one. Nested blocks **inherit** the heading in force at their position; headings themselves only occur at top level, so a nested bullet under `# Sources` is a `Sources` block and must not fall through as prose.

`body` is retained as a derived convenience field — the block texts joined in document order — for detectors that legitimately want whole-page text (#3's reverse search, #6's Jaccard, #11's `[[Path]]` scan). Derive it from `blocks[]`; never fetch it separately, or the two can disagree.

---

## Step 5: Run detectors + reconcile with prior run

Run each detector against the crawl. Detectors are independent — one failing detector (e.g., WebFetch rate-limited) is logged and skipped; the rest continue.

**Detectors #13 and #14 are scripts, not judgement calls** — run them and take their findings verbatim:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/healthcheck-knowledge/scripts/workaround_notes.py \
  --corpus <rows.json> --json

python3 ${CLAUDE_PLUGIN_ROOT}/skills/healthcheck-knowledge/scripts/declared_paths.py \
  --plugins <AINative checkout>/plugins --json
```

Each emits findings in the shape below plus a `coverage` block; carry `coverage.note` into Detector status.

**#14 takes its own corpus and its own resolution** — it reads the AINative manifests off disk and walks the teamspace itself, so it does not depend on Step 4's crawl or its 20-row sample cap. It needs `NOTION_TOKEN`; without it the script exits 2 and the detector is reported as skipped with coverage `NONE` rather than as clean. Point `--plugins` at a checkout of this repo — when the healthcheck runs from an install cache there is no `plugins/` tree beside it, so pass the path explicitly rather than relying on the cwd.

Merge all findings. De-dupe by `(page_url, category, subcategory)` — same page + same category + same subcategory counts once, but take the highest severity if detectors disagree. **`subcategory` is part of the key on purpose:** a page carrying both a `Sources` and a body-prose Linear reference has two findings with different fixes and very different cost, and de-duping on `(page_url, category)` alone would collapse them back into one — the costing failure detector #8's split exists to prevent. Apply the `severity` input filter.

Now reconcile against `prior_decisions`. For each finding, look up `(page_url, category, subcategory)`:

- Prior **Approved + Fixed** → drop from main findings unless the detector still fires (regression). If regressed, tag the finding `Regression from <prior run link>` and keep it.
- Prior **Rejected** → move to the **Previously rejected** section on the run page (visibility only, not counted in the headline totals). Exception: if severity has escalated (e.g. Medium → High), re-surface in main findings tagged `Re-raised — severity escalated from <prior run link>`.
- Prior **Deferred** or **Open** → keep in main findings, annotate `Recurring from <prior run link>`.
- No prior entry → new finding, no annotation.

The reconciled list is the **current findings**.

---

## Step 6: Pre-review auto-apply (formatting-only fixes)

Before creating the run page, apply unambiguous formatting-only fixes that require no content judgement. These have a fixed allow-list — do not expand it without updating this SKILL.md.

Currently permitted:

- Add an empty `# Sources` heading at the end of a content page missing one (leave the body empty — `ingest-knowledge` / humans fill it).
- Promote a `## Sources` heading to `# Sources` (heading level normalisation only; do not touch bullets).
- Normalise bullet style under `# Sources` from `* ` to `- ` (purely mechanical).

Everything else — including populating empty `# Sources` bullets, rewording, merging duplicates, restructuring stubs — is **not** pre-review eligible. It must go through the human-approved loop in Step 9.

Record each pre-review edit as `{ page_url, page_title, edit_type, before_snippet, after_snippet }` for the run page's "Edits applied this run › Pre-review" section.

In `mode=plan` or `interactive=false`, still preview the pre-review list but only apply them when `mode=apply`.

---

## Step 7: Create the run page

Determine the run identifier:

1. List children of the `Health Check` parent page.
2. Filter to titles matching `^<today>-run(\d+)$` where `<today>` is today's date in `YYYY-MM-DD` (skill-local — typically the user's date).
3. Let `N = max(existing) + 1`. If none exist for today, `N = 1`.
4. The new page title is `<today>-run<N>`.

**Never reuse or overwrite an existing run page.** If a concurrent run created `<today>-run<N>` between listing and writing, re-list and increment again.

Create the page with `notion-create-pages` under the `Health Check` parent using the run-page template below. At this point, pre-review edits from Step 6 are already listed (applied); Decision log rows start as `Open` and get updated by Step 9.

### Run-page template

```markdown
**At a glance:** Automated health check of the AI Knowledge teamspace, run <YYYY-MM-DD-runN> at <ISO timestamp>. {total} findings: {high} high / {medium} medium / {low} low. Prior run: [<YYYY-MM-DD-runN>](<url>) (or: first run).

# Findings

## High
### <title> — <category>[ · <subcategory>]
- Page: [<title>](<url>)
- Description: <what the detector found>
- Suggested fix: <one-line actionable hint>
- Related: [<other page>](<url>) (if applicable)
- Status: Open

## Medium
…

## Low
…

## Previously rejected
(findings re-detected this run but rejected in a prior run — informational only, not counted in headline totals)
- <title> — <category>[ · <subcategory>] — [prior decision](<prior run url>)

# Detector status
- ✅ contradictions, stale-claims, orphans, missing-concepts, data-gaps
- ⚠️ outdated-links — skipped: <reason>

# Edits applied this run

## Pre-review (formatting-only)
- [<page title>](<url>) — <edit_type> (e.g. added empty `# Sources` heading)

## Human-approved
(filled in by the interactive review loop in Step 9)
- [<page title>](<url>) — <short description of change>

# Decision log
| Finding | Kind | Severity | Decision | Action |
|---|---|---|---|---|
| <finding title> — <category> | <subcategory, or —> | High | Open | — |

# Sources
- Native to this teamspace (automated output; not distilled from a canonical source).
```

**`Kind` is its own column, not a suffix on `Finding`.** The next run reconstructs the de-dupe key by parsing this table, and several category names contain a slash themselves (`Guide/Index drift`, `Skill ↔ Tools/Slack declaration gap`), so a `<category>/<subcategory>` cell cannot be split back apart unambiguously. Write `—` in `Kind` when the detector sets no subcategory.

**Reading a prior log that predates the `Kind` column:** a row with no `Kind` (or `—`) matches **any** subcategory for that `(page_url, category)`. Without that rule a previously rejected Linear finding re-enters as brand new the first time the split runs, since its old row has no subcategory half to match on.

Statuses in the Decision log use the vocabulary: `Open`, `Approved + Fixed`, `Approved + Fix alternative`, `Rejected`, `Deferred`. The `Action` column is a one-line summary of what was actually done (e.g. "Added empty `# Sources` to <page>", "User rejected — stale-date detector false positive", "Deferred to next run").

Before the run page is created, print the At-a-glance line + Findings counts in chat so the user can redirect (`mode=plan` stops here without writing).

---

## Step 8: Ensure the parent index page is current

The `Health Check` parent page is an index, not a findings container. After creating the run page, update the parent so it reflects the new run.

### Parent-index template

```markdown
**At a glance:** Rolling index of AI Knowledge health checks. Each run is its own child page; prior runs are immutable.

Cadence: run on demand or on the scheduled cadence (see `healthcheck-knowledge` skill). Every run creates `YYYY-MM-DD-runN` under this page.

# Run log
| Run | Findings | High | Medium | Low | Edits applied |
|---|---|---|---|---|---|
| [2026-04-22-run2](<url>) | 14 | 1 | 8 | 5 | 3 |
| [2026-04-22-run1](<url>) | 18 | 2 | 9 | 7 | 1 |
| [2026-04-15-run1](<url>) | 21 | 3 | 10 | 8 | 0 |

# About this index
- Newest run on top. Trim the visible table to the last 24 rows; older runs remain as child pages.
- Run pages contain findings + Decision log. **Do not add findings content here.**
- Authoring rules live on the [AI Knowledge Guide](https://www.notion.so/34975e23af20810bac17c192eed68134).

# Sources
- Native to this teamspace.
```

Update the `# Run log` table by prepending a row for the new run (use `notion-update-page` with `content_updates`). Keep the rest of the parent body untouched.

---

## Step 9: Interactive review loop

Skip this step entirely when `interactive=false` or `mode=plan`.

Walk the current findings grouped by severity (High → Medium → Low). Batch findings in groups of up to five and present each as a multiple-choice:

```
Finding 3/18 — High — Contradictions
Page: <title> (<url>)
Description: ...
Suggested fix: ...

Options:
  a) Apply suggested fix
  b) Apply alternative (you describe)
  c) Reject (won't re-surface next run)
  d) Defer (keep open, re-surface next run)
```

For each finding:

- **Apply** → make the edit via `notion-update-page` on the target page (using targeted `content_updates` with `old_str`/`new_str`); record the change under "Edits applied this run › Human-approved" on the run page; set Decision log row to `Approved + Fixed` with a one-line action summary.
- **Apply alternative** → ask the user for the text/structure they want, apply it, record it as `Approved + Fix alternative`.
- **Reject** → no page edit; set Decision log to `Rejected` with the user's reason (one line).
- **Defer** → no page edit; set Decision log to `Deferred` (prior-run reconciliation in Step 5 will re-surface next run).

After each batch, update the run page's Decision log and Edits-applied sections via `notion-update-page`. Don't wait until the end — incremental writes mean an interrupted session still leaves a partial audit trail.

**Scope limits during edits:**
- Never edit pages outside the AI Knowledge teamspace, even if a finding involves them (flag in the description instead).
- Never bulk-apply across many pages without per-finding consent. Each finding = one decision.
- If a suggested fix requires restructuring into subpages, present the structure plan first and confirm before creating pages.

---

## Step 9b: Invalidate the local cache

For each page edited in Step 9 (apply / apply alternative paths), delete the corresponding cache file so `retrieve-knowledge` picks up the fix on the next read. Spec: [../retrieve-knowledge/references/cache.md](../retrieve-knowledge/references/cache.md).

- For each touched page, slugify its path (`/` → `__`, lowercased) and delete `${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/<slug>.md`.
- The Health Check run page itself (`Guide/Health Check/<date>-runN`) is not cached — agents don't read individual run pages — so no invalidation needed for it. But its parent `Guide` page's `# Run log` table is updated incrementally, so delete `${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/guide.md` once at the end of the run.

If the cache root or any file doesn't exist, skip silently. Cache invalidation must never fail the healthcheck.

---

## Step 10: Slack ping (optional)

If `slack=true`, post one message with: run identifier, total counts by severity, counts of pre-review + human-approved edits applied, link to the run page.

Post via `shared:post-to-slack` with `--footer shared:healthcheck-knowledge` — it stamps the versioned footer (`_Generated by `shared:healthcheck-knowledge:<version>` · <YYYY-MM-DD>_`) from `metadata.version` automatically; don't hand-type it. Trailing source link to the run page is optional (pass it as `--footer-extra`).

Recipient/channel is a first-use TODO — ask the user on first invocation with `slack=true` and store the answer via a follow-up update to this SKILL.md (so the next invocation knows where to post).

---

## Step 11: Summarise in chat

- Run identifier + link to the run page
- Counts by severity (H / M / L), plus count of recurring vs new
- Pre-review edits applied (count + page list)
- Human-approved edits applied (count)
- Findings deferred / rejected this run
- Any detectors that were skipped and why

---

## Rules the skill enforces

Authoring rules live on the [AI Knowledge Guide](https://www.notion.so/34975e23af20810bac17c192eed68134) (fetched in Step 2). Skill-specific operational rules:

1. **Per-run pages are immutable.** Never reuse, overwrite, or rename an existing `YYYY-MM-DD-runN` page. New data goes in a new page.
2. **Two-phase edits.** Only the pre-review allow-list (Step 6) is auto-applied. All other edits require explicit per-finding approval in the Step 9 loop.
3. **Respect the prior Decision log.** Rejected findings don't re-enter main findings unless severity escalates. Deferred findings surface as Recurring.
4. **Plan-first for first run.** If no `Health Check` parent exists, present the proposed creation (parent + first run page) in chat before writing.
5. **Detectors fail open, but the entry point fails closed.** One detector erroring doesn't abort the run — log it under Detector status and continue. **One exception:** if Step 2 resolves zero sections from About, abort without creating a run page. Failing open covers one detector losing its answer; an unresolvable taxonomy means every detector is handed an empty crawl, and a run page reporting zero findings is then indistinguishable from a healthy teamspace. See [references/about-sections.md](references/about-sections.md).
6. **Respect AI scope of edits.** Never edit canonical docs outside the AI Knowledge teamspace even if a finding involves them.
7. **Graceful degradation, except at the entry point.** Missing MCP, 404 index page, renamed section, corrupt prior Decision log → note and continue where possible. This does **not** extend to About resolving zero sections — see rule 5. A renamed *section* is survivable; an unresolvable *taxonomy* is not.

---

## No bundled knowledge

Everything the skill *knows* (taxonomy, authoring rules, section structure, prior decisions) is fetched fresh from Notion every run — no local state. The bundled files are execution detail rather than knowledge: `scripts/notion_rest.py` (byte-identical to the copy `harvest-knowledge` and `ingest-knowledge` ship), `references/about-sections.md` (how to resolve the taxonomy entry point, also byte-identical across the three), `references/detectors.md` (the detector catalogue), `scripts/workaround_notes.py` (detector #13, whose clustering is deterministic and therefore belongs in code rather than in a prompt), and `scripts/declared_paths.py` (detector #14, same reason — resolving a declared path is a lookup, not a judgement).

---

## Relationship to other knowledge skills

| Skill | Shape |
|---|---|
| `ingest-knowledge` | On-demand, single source in → writes L4 |
| `harvest-knowledge` | Periodic upstream → L4 sync (writes L4) |
| `healthcheck-knowledge` | Periodic L4 audit → writes a per-run page; applies approved fixes interactively (this skill) |

Natural weekly loop: run `harvest-knowledge` to pull upstream changes, then run `healthcheck-knowledge` to audit the result.

---

## Notion access (MCP-first, REST where the MCP can't)

Per the repo convention ([CLAUDE.md → Tokens are soft requirements](../../../../CLAUDE.md#tokens-are-soft-requirements)),
**use the Notion MCP first for every operation it can do** — single-page `notion-fetch`,
`notion-create-pages`, `notion-update-page`, and small scoped `notion-search` lookups. That covers
most of the skill: reading individual pages, creating the run page, updating the Decision log, and
applying approved fixes.

Drop to the bundled [`scripts/notion_rest.py`](scripts/notion_rest.py) helper (reads `NOTION_TOKEN`)
**only for what the MCP can't do** — enumerating/paginating the teamspace past `notion-search`'s
semantic, 25-result, no-cursor cap (the **Step 4 crawl** and any database row sweep). There,
`notion.search(query, sort=…)` with cursor pagination + a teamspace-ancestry post-filter
(`notion.root_page_of`) is the only way to see every page. If `NOTION_TOKEN` isn't available —
e.g. inside Cowork sandboxes, where it's stripped ([PROD-1305](https://linear.app/aura-health/issue/PROD-1305)) —
fall back to the MCP even for the crawl, accepting the cap, and note the truncation under Detector status.

| Operation | Primary | Use the REST helper when |
|---|---|---|
| Fetch one page / its blocks | `notion-fetch(page_id)` (MCP) | MCP unavailable → `notion.get_page` + `notion.get_block_children` |
| Create the run page | `notion-create-pages(...)` (MCP) | MCP unavailable → `notion.create_page(...)` |
| Update run page / apply a fix | `notion-update-page(...)` (MCP) | MCP unavailable → `notion.update_page_props` / `append_block_children` / `update_block` |
| Enumerate/paginate the teamspace (Step 4 crawl, DB sweeps) | **REST helper** — the MCP can't (25-result cap, no cursor) | always when `NOTION_TOKEN` set → `notion.search(sort=…)` + `root_page_of` post-filter; MCP only as a capped fallback |

Other tools:
- Slack MCP — only if `slack=true`
- WebFetch — detector #7 arm (d) only (public docs). Never for `github.com`: every AuraHealth repo is private, so an anonymous fetch of a live URL 404s exactly like a dead one — arm (c) uses `gh` or reports itself not run.
- `gh` CLI — detector #7 arm (c), GitHub link resolution

---

## Future: adapter layer

A shared adapter layer at `AINative/shared/adapters/` is planned (it will give agents a structured read surface over AI Knowledge). Once it lands, detectors should migrate to the adapters so the healthcheck sees the same data consumer agents see — catching problems adapters would hit. Until then, v1 reads via the Notion MCP, using the REST helper (`scripts/notion_rest.py`) only for the teamspace-pagination sweep the MCP can't do (and as the all-around fallback when the MCP isn't loaded).
