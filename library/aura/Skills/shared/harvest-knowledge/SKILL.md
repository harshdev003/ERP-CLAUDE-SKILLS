---
name: harvest-knowledge
description: >
  Sweeps upstream canonical knowledge sources, finds what's changed since a chosen window, and
  **updates the AI Knowledge teamspace** so the L4 layer stays in sync. Runs on demand at any
  cadence (weekly, monthly, after a big project, whenever). Defaults to Notion (canonical
  teamspaces like Engineering, Prompts, Product, QA), and is designed to extend to Slack,
  Linear, Drive, and other sources over time. Classifies changes by the knowledge types
  defined on the Guide page, presents a write plan for approval, then applies create/update
  edits inside AI Knowledge. Writes only ever land in AI Knowledge — canonical sources are
  read-only. Complements `ingest-knowledge` (which ingests a single source on demand);
  `harvest-knowledge` is the periodic sweep. Trigger when the user says "harvest knowledge",
  "run the knowledge harvest", "sync AI Knowledge with upstream", "what changed in Notion
  since {date}", "pull latest from Notion for agents", or asks to refresh the L4 layer against
  upstream.

metadata:
  version: "3.6.0"
knowledge:
  # Cross-cutting: classifies diffs against the live taxonomy; target section decided per-item.
  reads:
    - Guide/Index            # baseline routing aid (universal)
    - Guide/Placement Rules  # explicit so it's pre-fetched even with the wildcard
    - Tools/Notion           # explicit — REST + MCP access patterns this skill leans on
    - "*"                    # cross-cutting: reads anywhere in the teamspace
  writes: ["*"]

---

# Harvest Knowledge

Periodic sweep that **updates AI Knowledge content**. Finds what changed in upstream canonical sources, classifies each item against the current AI Knowledge taxonomy, proposes writes to the L4 layer, waits for approval, then applies them. The sole deliverable is an updated AI Knowledge teamspace.

**Scope today:** Notion (AI Knowledge teamspace + optionally adjacent canonical teamspaces).
**Scope tomorrow:** Slack canvases, Linear docs, Drive, and other durable sources as they become relevant. The skill is structured around **sources** so new ones can be added without rewriting the core flow.

**Core contract:**
- **Plan first, always.** No writes before the user approves the plan — same discipline as `ingest-knowledge`.
- **Every run leaves an audit page.** One child page under `Guide › Harvest`, named `YYYY-MM-DD-runN`, capturing what was scanned, what was applied, what was deferred. Past run pages are immutable historical records. The `Harvest` parent page is the rolling index. See [Run pages under Guide › Harvest](#run-pages-under-guide--harvest).
- **Prior-run aware.** Each run reads the most recent run page first and respects its decisions: skip re-proposing rejected items, carry forward deferred ones.
- **Incremental by window.** Every run uses a `since` date. Stateless — no local state file; prior-run context is reconstructed from Notion itself.
- **No duplication.** If information already lives somewhere in L4, cross-link instead of copy-pasting.
- **Guide-compliant.** All writes follow the Authoring rules on the [AI Knowledge Guide](https://www.notion.so/34975e23af20810bac17c192eed68134): `# Sources` section on every page, voice-playbook compliance, and the "AI scope of edits" rule (writes only inside the AI Knowledge teamspace; canonical upstream docs are read-only). The Guide is canonical — if it changes, the skill honours the new rules on the next run. Step 2 fetches it fresh.

---

## Inputs

- `since=YYYY-MM-DD` (optional, default **7 days ago**) — start of the window.
- `sources=notion[,slack,linear,…]` (optional, default `notion`) — which source adapters to run. Unknown sources are skipped with a note.
- `mode=plan|apply` (optional, default `plan`) — `plan` stops after presenting the plan; `apply` executes after the user approves it in chat. You can also pass `mode=plan` then re-invoke with the same args plus `mode=apply` once ready.

The skill is stateless across runs — no local files, no `.last-harvest`. The cadence is whatever the user chooses: weekly, monthly, after a project — just pass `since`.

---

## Step 1: Resolve inputs

1. `since`: argument → 7 days ago.
2. `sources`: argument → default `notion`. For each requested source, check that the required MCP is installed. If not, skip the source and mention it in the final summary (don't block the whole run).
3. `mode`: argument → default `plan`.

Future sources plug in at the same layer: each source produces a list of `{ source, title, url, section, last_edited, snippet }` records that flow into the classify + plan steps unchanged.

---

## Step 2: Learn the current taxonomy (Notion)

**Do this fresh every run. Do not hardcode structure — the teamspace taxonomy can evolve.**

Only one Notion ID is hardcoded:

```
AI_KNOWLEDGE_ABOUT_PAGE = "9ee75e23af20830aa49101a40466a789"
```

This is the About (router) page. If it's ever renamed or moved, update this constant. (Same hardcoded entry point as `ingest-knowledge` — keep them in sync.)

### Notion access — REST-first by necessity

This skill is on the [API-first by necessity](../../../../CLAUDE.md) list — it sweeps
the entire AI Knowledge teamspace each run, which requires REST pagination (the
`notion-search` MCP caps at 25 results with no cursor). Every Notion read and write is
**REST-first when `NOTION_TOKEN` is set**, using [`scripts/notion_rest.py`](scripts/notion_rest.py):
`get_page` / `get_block_children` for reads, `create_page` + the `block_*` builders for
new pages, `update_block` for in-place edits, [`scripts/insert_section.py`](scripts/insert_section.py)
`add_section` for a new section or `# Sources` line on an existing page, and
`search(query="", sort=…)` + `root_page_of` for the upstream scan (Step 4). MCP
(`notion-fetch`, `notion-create-pages`, `notion-update-page`, `notion-get-teams`,
`notion-search`) is the fallback in Cowork (host env stripped, PROD-1305) or without
the token.

**Step 4 runs two legs with different jobs.** REST `search` is the only Notion API that can order by `last_edited_time` and page past 25 results, so it is the one path that can enumerate a changed-since window — it stays the coverage scan. MCP `notion-search` is semantic and capped at 25 with no cursor and no edit-time sort, so it cannot establish coverage; what it *can* do is scope to a teamspace server-side, which makes it immune to this teamspace's own writes and a genuinely different view. It runs alongside as a cross-check. See [references/upstream-scan.md](references/upstream-scan.md) for what each tool actually supports and how the two legs reconcile.

From About, discover the current structure and classification rules (REST `get_block_children`, MCP `notion-fetch` fallback):

1. **About page** — resolve the top-level sections from **the links in About's body**, not from its child pages. See [references/about-sections.md](references/about-sections.md) — read it before implementing this step.
2. **Guide page** — read the **current** knowledge types and "AI scope of edits" rule. Use these for classification; honour whatever the Guide currently says.
3. **Guide/Placement Rules page** — read the placement policy. **Step 5's classification and Step 6's plan MUST honour this page** (no root-level pages, prefer the most specific existing subsection, propose new subsections rather than file at section root). If Placement Rules disagrees with this SKILL.md, Placement Rules wins.
4. For each section resolved from About, read its children in parallel to enumerate direct children.
5. For sections whose children are themselves indexes (Products › <app>, Procedures › Engineering, Learnings), go one level deeper.
6. Capture the `collection://` URL of **every** database found, not only those under
   Learnings. A section that holds a database is a section whose content lives in rows:
   `Products › Aurie › Screens` is one row per screen. Treating it as a leaf means the
   harvest proposes a child page, which shadows the rows while appearing to succeed.

Build an in-memory map: `{ section_name → { page_id, children: [{ title, page_id }], databases: [collection_url] } }` plus the classification rules pulled from the Guide.

A database in that map is a **write target owned by another skill** as often as it is a place to append. `Products › Aurie › Screens` is projected from the app repo by `data:sync-screens`; the repo is the system of record, so anything harvested into those rows by hand is overwritten on the next sync. Propose the change to the repo instead.

### Resolving About's sections — body links, not child pages

**The sections are inline links in About's body rich_text, not child pages of About.** A `child_page` walk returns exactly one result (`Test Page - Delete`) and zero sections, handing every downstream step an empty taxonomy while looking like it succeeded.

Full recipe, the verified section list, and the hard-error rule: **[references/about-sections.md](references/about-sections.md)**. Read it before implementing Step 2 — an empty section map aborts the run rather than continuing.

---

## Step 2b: Read the prior run page (decision carry-forward)

Before scanning upstream, fetch the most recent run page under `Guide › Harvest` so this run doesn't re-propose things a previous run already handled.

1. Resolve the `Harvest` parent page under Guide (same discovery as Step 2 for Guide children). If the parent doesn't exist yet, this is the first run — skip this step; the parent index + first run page will be created in Step 6/7.
2. List children of `Harvest`. Filter to pages matching the `YYYY-MM-DD-runN` pattern. Pick the most recent: latest date first, then highest `runN` within that date.
3. Read that page (REST `get_block_children`, MCP `notion-fetch` fallback) and parse its **Decision log** table plus each finding's **Status** line.

Apply this to the current run:

- **Rejected** findings — skip re-proposing them unless the upstream source has meaningfully changed since the prior run (different `last_edited`, different headline content).
- **Deferred** findings — carry them into the new plan with a `Recurring from <prior run link>` note, unless their "deferred until" date is still in the future.
- **Approved / applied** findings — informational context only; they're already reflected in L4.

If parsing fails (schema drift on an older run page), log a note in Step 8's summary and continue without carry-forward — never block the run on a malformed prior page.

---

## Step 3: Index existing L4 pages (for matching only)

Using the map from Step 2, build a lightweight index of every page currently in AI Knowledge: `{ section → [{ title, page_id, url, sources: [urls from # Sources section] }] }`. This index is **only** used in Step 4 to decide whether an upstream change already has an L4 mirror. No output, no digest — the skill is not a "what changed in L4" reporter.

---

## Step 4: Harvest upstream canonical sources

Discover the canonical source teamspaces at runtime — do not hardcode teamspace IDs. The match target is "the teamspaces that look like canonical L4 sources" (typical names: Engineering, Prompts, Product, QA — match by name, be tolerant of renames).

**The "changed since" predicate is `last_edited_time >= since`** — applied client-side in **both** paths below. Do **not** rely on a created-date filter alone: a created-date scan only catches *newly created* upstream pages and silently misses *edits* to existing docs, which is exactly the kind of change harvest exists to mirror.

**Two legs with different jobs** — what each API actually supports, the own-writes ratio, and how the legs reconcile into a coverage verdict: **[references/upstream-scan.md](references/upstream-scan.md)**.

1. **REST enumeration — the window scan.** `search(query="", sort=last_edited_time desc)`, paged **all the way** to the first hit older than `since`, then post-filtered to the source teamspaces via `root_page_of`. Reaching the cutoff is what makes the scan complete; stopping short leaves a tail that is indistinguishable from an empty one.
2. **MCP teamspace probe — the cross-check.** `notion-get-teams`, then `notion-search` per teamspace with `teamspace_id` and topic query terms. Server-side scoping makes it immune to our own writes, but it is semantic and capped at 25, so it can find things REST's noise buried and can never prove nothing changed.

If neither the Notion MCP nor `NOTION_TOKEN` is available, skip the Notion source and note it in the Step 8 summary.

For each surviving hit (either path), match against the L4 index from Step 3:

- **Mirrors an existing L4 page** (by title overlap or by appearing in that page's `# Sources`) → propose an **update** to the L4 page's relevant section and/or `# Sources`.
- **No L4 mirror yet** → propose a **new** L4 page under the right section, referencing the canonical as its source.
- **Too narrow / transient to warrant an L4 page** → skip silently (don't clutter the plan with non-actions).

**Establish drift against blocks, not rendered lines.** A line-level text diff between an
upstream page and its L4 mirror over-reports, and it over-reports *silently* — every false
positive reads exactly like a real gap, so a plan built from one proposes rewriting content
the mirror already carries. The two pages routinely express the same content in different
block structure, and the mirror also applies the house voice on top. Before a candidate
becomes a write-plan entry, fetch the mirror's own blocks and confirm the content is
genuinely absent. Measured false-positive rate, the check to run, and the drift this does
catch: [references/upstream-scan.md](references/upstream-scan.md) → *Establishing drift on a
mirrored page*.

**Never edit canonical docs.** Per the Guide's "AI scope of edits" rule, agents may only modify content inside the AI Knowledge teamspace.

---

## Step 4b: Run source adapters

Source adapters extend the harvest with changes that live outside Notion — GitHub repos, internal service configs, etc. Run each adapter after Step 4 finishes; fold any changes they surface into the write plan before Step 6.

### Adapters

Each adapter is invoked via the `Skill` tool (never a direct script path — `${CLAUDE_PLUGIN_ROOT}` can't address another plugin's scripts, and cross-plugin file references violate Architecture Principle 1). Every adapter is **non-blocking**: an error is logged in the Step 8 summary and the harvest continues.

Today's adapter is `eng:sync-feedback-config`, which checks the Intelligence Service feedback config files against `Tools/Aura API/Dialogue Feedback Labels`. How to invoke it, how to read its JSON, and which results become write-plan entries: **[references/source-adapters.md](references/source-adapters.md)**.

---

## Step 5: Classify each item

Use the classification rules from the Guide (Step 2). At time of writing the Guide defines six types — Identity, Domain, Procedural, Learnings, Signals, Ephemeral — but honour whatever the Guide currently says.

- Items matching a current Guide type → place in that bucket.
- Ephemeral content found inside AI Knowledge → flag as misplaced (sprint status etc. should live in Linear/Slack, not L4).
- Items fitting no current type → list under **"New knowledge type candidate?"** for the user to decide whether to extend the Guide.

### Placement preconditions (from `Guide/Placement Rules`)

Once classified, every proposed *new* page must also pass these hard rules:

- **No root pages.** A new page MUST land under one of the top-level sections resolved from About in Step 2 — today: Compliance, Domain, Guide, Identity, Learnings, Procedures, Products, Signals, Team, Tools. Use the resolved set rather than this list, which is only its current value; About itself is the router page, not a section, so it is never a valid parent. Never propose a write whose parent is the workspace or teamspace root.
- **Pick the most specific existing subsection.** Don't drop a page at a section root if a more specific home (existing sub-page or database) exists.
- **Propose a new subsection rather than filing at section root.** If nothing fits, draft a subsection page (with the standard `At a glance:` / `What lives here vs elsewhere` / `# Sources` skeleton) and include it in the same plan; add a one-line entry to `Guide/Index` in Step 7b.

If a candidate would otherwise land at the workspace root or a section root, surface it under *Open questions* in Step 6 and ask before writing — don't auto-default to the section root.

---

## Step 6: Produce the write plan (and draft the run page)

Output the plan in chat for the user to approve or redirect. The same content is the draft body for this run's audit page under `Guide › Harvest` — see [Run pages under Guide › Harvest](#run-pages-under-guide--harvest) for the on-page format. Chat format (compact):

```markdown

# Knowledge harvest plan — {today}

**Window:** {since} → {today}
**Sources scanned:** {notion[, slack, …]}
**Mode:** {plan|apply}

## Taxonomy discovered (live from Guide)
- Types: {list}
- Sections: {list with any drift flagged}

## Proposed writes inside AI Knowledge
| Upstream source | Classification | L4 target | Operation |
|---|---|---|---|
| [<canonical page>](<url>) | Procedural › Engineering | [<existing L4 page>](<url>) | Update §… |
| [<canonical page>](<url>) | Identity › Brand | NEW under Identity / Brand | Create |

### Diff preview (for updates)
- **<L4 page>** — adding section "<x>" before # Related; appending new `# Sources` line pointing to <canonical>

## New knowledge type candidates?
- [<upstream item>](<url>) — <why it fits none of the current types>

## Skipped sources
- <source>: <reason, e.g. MCP not installed>

## Open questions
- <classification ambiguities, duplicate mirrors, schema gaps>
```

If `mode=plan`, stop here and wait for the user to approve, redirect individual items, or re-invoke with `mode=apply`. If the user redirects the placement of any item, re-plan that item and re-present. No run page is written in `plan` mode — the audit page is created in Step 7 as part of `apply`.

---

## Step 7: Apply (when `mode=apply` and the user has approved)

Execute the approved writes in parallel where independent. Write scope is strictly the AI Knowledge teamspace.

**Before applying L4 edits, create this run's audit page under `Guide › Harvest`.**

1. Ensure `Guide › Harvest` parent exists. If not, create it with the [parent index template](references/run-pages.md#parent-index-template) and present the proposed creation in chat first so the user can redirect the parent location.
2. Compute the run page title using the naming rule:
   - Today's date in `YYYY-MM-DD` (UTC or the user's local — pick one and be consistent; UTC is recommended).
   - List existing children of `Harvest`. Find all titled `<today>-run*`. Take the highest `N`. Create `<today>-run{N+1}`. If none exist for today, use `run1`.
   - **Never reuse or overwrite an existing run page.**
3. Create the run page under `Harvest` (REST `notion.create_page` with the `block_*` builders, MCP `notion-create-pages` fallback) using the [run page template](references/run-pages.md#run-page-template) populated from the plan (sources scanned, findings with Open status, carry-forward notes, counts).
4. Append a new row to the top of the parent index's Run log table, linking to the new run page (REST `append_block_children` / `update_block`, MCP fallback).

Then execute the L4 edits. As each edit completes, update the run page's **Edits applied this run** section and flip the corresponding finding's Status to `Approved — <page created/updated>`, `Rejected — <reason>`, or `Deferred — <until>`. On failure, record `Deferred — retry next run` with the error.

### Naming rule examples

```
First run on 2026-04-22       → 2026-04-22-run1
Second run on 2026-04-22      → 2026-04-22-run2   (same day, increment N)
Third run on 2026-04-22       → 2026-04-22-run3
First run on 2026-04-23       → 2026-04-23-run1   (date changed, reset to run1)
```

`N` is a 1-indexed counter scoped to the **same date**, not a global counter.

**New L4 pages (REST):** build the body with the `block_*` builders and create in one call (MCP `notion-create-pages` fallback):

```python
from notion_rest import notion, block_callout, block_heading, block_paragraph, block_bullet, block_table

blocks = [block_callout("At a glance: …"), block_heading("<section>", 2), block_paragraph("…"), …]
page = notion.create_page("<section-or-subpage-id>", "<title>", blocks)
```

```

# MCP fallback
notion-create-pages(
  parent: { type: "page_id", page_id: "<section-or-subpage-id>" },
  pages: [{ properties: { title: "…" }, content: "…" }]
)
```

Body template (same house style as `ingest-knowledge`):

```
**At a glance:** <one paragraph>.

<body sections>

# Related
- [<parent>](<url>) — parent
- <cross-links>

# Sources
- [<canonical source title>](<url>) — human-maintained Notion doc
```

**Updating existing L4 pages (REST):** fetch the page's blocks, locate the target section, and patch surgically (MCP `notion-update-page` fallback):

```python
from notion_rest import notion, block_paragraph, block_bullet

blocks = notion.get_block_children("<target>")        # current body
notion.update_block("<block-id>", {"paragraph": {"rich_text": [...]}})   # replace one block
```

**A new section or `# Sources` line goes through [`scripts/insert_section.py`](scripts/insert_section.py), never
`append_block_children`**, which always appends at the end of the page and so puts a section below
Sources. `add_section` reads the page once, inserts the section after the block just before the
Sources heading, and adds the source lines after the last block of the list, skipping one already
listed. With no Sources heading it appends the section and a new Sources section. It raises
`ValueError` (defer the edit) when Sources is the page's first block or a batch is over 100 blocks.

```python
from insert_section import add_section

add_section(notion, "<target>", [block_heading("<New section>", 2), block_paragraph("…")],
            [block_bullet("[<canonical title>](<url>) — human-maintained Notion doc")])
add_section(notion, "<target>", [], [block_bullet("…")])   # a Sources line only
```

```

# MCP fallback
notion-update-page(
  page_id: "<target>",
  command: "update_content",
  content_updates: [{ old_str: "…", new_str: "…" }]
)
```

Fetch the target page immediately before the update to get exact matches. Be surgical: replace only the section being added/changed. For `# Sources` maintenance: add new lines at the end of the existing list (`add_section`'s `sources`, never a "Sources:" paragraph inside the new section), add the section at the bottom if it doesn't exist, deduplicate if the source is already listed.

**`# Sources` line formats** (same as `ingest-knowledge`):

| Source | Line format |
|---|---|
| Notion | `- [<title>](<url>) — human-maintained Notion doc` |
| PDF | `- PDF: "<filename>" (ingested <YYYY-MM-DD>)` |
| Web | `- Web: [<title>](<url>) — fetched <YYYY-MM-DD>` |
| Slack | `- Slack: [<thread subject>](<url>) — <channel>` |

---

## Step 7b: Regenerate `Guide/Index`

`Guide/Index` is the Path → Description map `search-knowledge` and `retrieve-knowledge` route against, so it has to describe the **live tree**. Full procedure — tree walk, exclusions, description derivation, page body shape: **[references/guide-index.md](references/guide-index.md)**. The walk is `scripts/walk_tree.py`, which opens toggles, columns and other blocks a page can sit inside, and refuses a collapsed walk.

Decide by running these two checks in order. Regenerate if **either** says so, and record which one fired on the run page:

1. **Staleness.** Read the `Last regenerated: <YYYY-MM-DD>` line off the current `Guide/Index`. Regenerate if it is more than 30 days old, or if the line is missing or unparseable.
2. **Tree drift.** Walk the tree per the reference and compare the resulting path set against the paths the current page lists. Regenerate if they differ in either direction.

Skip only when both checks pass, and record on the run page the age and the compared path counts that justified the skip.

**Both checks read the live tree.** The index drifts from any page created, renamed or moved anywhere in the teamspace, and most of those never pass through a harvest — sibling skills write pages, and people create them by hand in Notion. The tree is therefore the only thing that answers whether the index is current, and it gives the same answer however much or little this run wrote. A missed sweep self-heals on the next one.

---

## Step 7c: Invalidate the local cache

After every applied edit (Step 7) and after the `Guide/Index` regeneration (Step 7b), **delete** the corresponding cache files. Delete-only — the next read repopulates lazily. Spec: [../retrieve-knowledge/references/cache.md](../retrieve-knowledge/references/cache.md).

- For each touched page (created, updated, or renamed), delete `${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/<slug>.md` where slug = path with `/` → `__`, lowercased.
- Always delete `${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/guide__index.md` after Step 7b — `Guide/Index` is the hot path for `search-knowledge` and a 24h-stale copy hurts.
- For renames, delete the old slug's cache file (the new slug doesn't exist in cache yet — first read will populate it).

If the cache root or a specific file doesn't exist, skip silently. Cache invalidation must never fail the harvest.

---

## Step 8: Close with a summary

After writes complete (or after the plan if `mode=plan`):

- New L4 pages created — titles + URLs
- Updated L4 pages — titles + URLs + short note on what changed
- New-type candidates (user decision)
- Skipped sources and why
- Link to this run's audit page under `Guide › Harvest` (e.g. `2026-04-22-run1`)
- Suggested `since` for the next run (today's date)

---

## Run pages under Guide › Harvest

Every `apply` run produces one immutable audit page as a child of `Guide › Harvest`. The parent page is a rolling index; per-run detail lives on the child pages.

Both templates — parent index and run page — are in **[references/run-pages.md](references/run-pages.md)**. Carry-forward items from a prior run appear inline under the relevant Findings subsection with a trailing `Recurring from [<prior run>](<url>)` note.

---

## Rules the skill enforces

Authoring rules (`# Sources` discipline, voice-playbook compliance, AI scope of edits) live on the [AI Knowledge Guide](https://www.notion.so/34975e23af20810bac17c192eed68134) and are fetched fresh in Step 2 — the skill honours whatever the Guide currently says. The rules below are skill-specific operational discipline on top of that.

1. **Plan first, always.** Never write before the user approves the plan.
2. **Source-agnostic framing.** Notion is the first source, not the only one. New sources slot in at Steps 2–4 and flow through Steps 5–8 unchanged.
3. **No duplication.** If information already lives in L4, cross-link instead of copy-pasting.
4. **Ephemeral stays out.** Never create an L4 page from time-bound upstream content (sprint updates, weekly status, live incidents) — those belong in Linear/Slack.
5. **Surgical updates.** When updating an existing L4 page, fetch it immediately before the write and use targeted `old_str`/`new_str` replacements; never overwrite the whole body.
6. **Idempotent reruns.** If run twice with the same `since`, the second run should produce a near-empty plan.
7. **Graceful degradation, except at the entry point.** Missing MCP, 404 page, or renamed section → note and continue where possible; don't abort the run. **One exception:** if Step 2 resolves zero sections from About, abort. Degrading covers one source or one page failing while the rest of the run stays meaningful; an unresolvable taxonomy leaves nothing downstream meaningful, so continuing manufactures a false clean result. See [references/about-sections.md](references/about-sections.md).
8. **Ask before creating a new Learnings DB schema.** Default to a standalone page under Learnings if no existing DB fits.
9. **Run pages are immutable.** Once an `apply` run finalizes its audit page under `Guide › Harvest`, never overwrite or rename it. Subsequent runs always create a new `YYYY-MM-DD-runN` page with the next available `N` for the date. The only in-place edit allowed on a past run page is a clerical correction (e.g. fixing a broken link) — never rewriting history.
10. **Respect prior-run decisions.** Before scanning, read the most recent run page's Decision log and skip rejected items / carry forward deferred ones (see Step 2b). If prior-run parsing fails, note it in the summary and continue.
11. **No Linear URLs in any `# Sources` block harvest writes.** Linear cards are ephemeral by design — content moves or closes without warning — so `Guide` → Authoring rules bans card links in AI Knowledge content, `# Sources` included. The `# Sources` line-format table at "Step 7 — Apply" therefore carries no Linear row, and the run page's "Upstream sources read" line lists only canonical-doc URLs (Notion pages, Slack threads referencing source content, web pages). When a Linear ticket is the source — including when one triggered the run — capture its durable claim inline (in the page body, or `# Notes for next run` / `# Edits applied this run` on a run page) and name the ticket as text, not a link. `healthcheck-knowledge` detector #8 flags a card link as a finding and scores one in `# Sources` as its own subcategory; product URLs (the workspace root, `/settings/*`, `/docs/*`, a saved view) are durable and are not findings.
12. **Never report an incomplete upstream scan as a clean one.** "Nothing changed upstream" is a claim only a completed REST enumeration can support — the MCP probe is semantic and 25-capped, so its silence is never evidence of absence. If the REST leg did not reach `since`, the window is *unknown*: say so in the plan and the summary, report any MCP hits as real findings alongside it, and make re-running the enumeration to completion the next step. A high own-writes ratio on a scan that *did* reach `since` is a cost signal, not an incomplete one — see [references/upstream-scan.md](references/upstream-scan.md).

---

## Edge cases

**Very large change set.** If > 40 items, group Learnings DB rows into a single line per database ("12 new Ad Campaigns entries") and offer a staged plan (highest-value chunks first) for the user to approve one stage at a time.

**About page renamed/moved.** The About page ID is the only hardcoded Notion ID. If it 404s, stop and ask the user to update `AI_KNOWLEDGE_ABOUT_PAGE` in this SKILL.md. Fall back to `notion-search` for a page titled "About" in the AI Knowledge teamspace to suggest a replacement ID.

**Conflicting updates.** If a canonical source contradicts existing L4 content, surface the conflict in the plan and ask which wins. Don't silently overwrite.

**Classification ambiguous.** Propose the best-fit bucket with a cross-link note, or list under "New knowledge type candidates?" if truly unclassifiable.

**Re-ingesting a source already mirrored.** Detect by scanning L4 target pages' `# Sources` for the source URL. If found, frame the plan as an *update pass* — which sections changed since the last mirror.

**Notion MCP missing.** Skip Notion and note in the summary; if Notion was the only requested source, stop and redirect to `setup-ainative`.

---

## No bundled knowledge

Everything this skill *knows* (taxonomy, sections, classification rules, "AI scope of edits") is fetched fresh from Notion every run (Step 2). No local state — the skill is a thin orchestrator over the live Notion workspace.

The bundled files are execution detail, not knowledge:

| File | What it holds |
|---|---|
| `scripts/notion_rest.py` | REST helper |
| `scripts/walk_tree.py` | The Guide/Index tree walk (opens toggles, columns and other blocks) and its collapse guard |
| `references/about-sections.md` | how to resolve the taxonomy entry point (Step 2) |
| `references/upstream-scan.md` | what each search API supports, and how the two Step 4 legs reconcile |
| `references/source-adapters.md` | per-adapter invocation and result handling (Step 4b) |
| `references/guide-index.md` | `Guide/Index` regeneration procedure (Step 7b) |
| `references/run-pages.md` | parent-index and run-page templates |

---

## Relationship to `ingest-knowledge`

Both skills write into AI Knowledge. The difference is shape:

- `ingest-knowledge` — **on-demand, single-source**. User hands it a Notion URL, PDF, Slack thread, etc., and it routes that one source into the right L4 page(s).
- `harvest-knowledge` — **periodic, multi-source sweep**. User picks a window (`since=…`) and the skill scans every configured source, builds one combined plan covering everything that moved, and applies it.

They share the same planning discipline, the same house style, the same `# Sources` rules, and the same "AI scope of edits" boundary.
