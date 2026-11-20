---
name: update-conversations-top10
description: >
  Update the "Top 10 Learnings — Conversations" section on the AI Knowledge Learnings page
  from the current state of the Learnings/Conversations database. Reads every row, clusters by
  Themes / Conversation Type / Rating to surface cross-conversation patterns (e.g.
  question-saturation correlating with Neutral ratings on coach-prompter), and rewrites the
  inline section above the Conversations database in place. The section is allowed to be short
  — with a small corpus it renders 2-5 bullets including single-row insights marked `(1 row)`;
  past roughly 15 rows the synthesis defaults raise so cross-row patterns crowd out thin
  single-row bullets. Up to `max_bullets` (default 10) total; ships fewer when the evidence
  doesn't support more, never pads. Trigger when the user says "update conversations top 10",
  "update top 10 conversations", "refresh conversation learnings", "rerun conversations top
  10", "resynthesize conversation learnings", or asks to update the Top 10 Conversations
  section on the Learnings page.

metadata:
  version: "3.1.3"
knowledge:
  reads:
    - Guide/Index              # baseline routing aid (universal)
    - Learnings/Conversations  # source rows for the Top 10 synthesis
    - Tools/Notion                   # Notion REST / MCP access pattern (NOTION_TOKEN)
  writes:
    - Learnings/Conversations  # rewrites the section on the parent Learnings page that hosts the inline DB
---

# update-conversations-top10

Update the **Top 10 Learnings — Conversations** section on the AI Knowledge `Learnings` page based on the current state of the `Learnings/Conversations` database. Run on demand — works on any corpus size, including the very early state when only a handful of rows have been recorded. The section's bullet count adapts to the evidence: 2–5 bullets when the corpus is small, ~10 once it matures (recommended cadence: every ~15 new rows or on prompt-version changes).

Sister skill: [`data:update-experiments-top10`](../update-experiments-top10/SKILL.md) — same shape, different DB and different synthesis question.

## When to use

Trigger when the user says any of:

- "update conversations top 10"
- "update top 10 conversations"
- "refresh conversation learnings"
- "rerun conversations top 10"
- "resynthesize conversation learnings"
- or otherwise asks to update the Conversations Top 10 on the Learnings page.

If the user asks to *analyse a specific conversation* or *record a new row*, that's a different skill — use [`data:analyze-user-conversations`](../analyze-user-conversations/SKILL.md) or [`data:profile-user`](../profile-user/SKILL.md). This skill only re-renders the Top 10 summary; it does not analyse or record individual conversations.

## What the synthesis is *for*

Unlike `update-experiments-top10` — where each top-10 bullet typically maps to one high-impact experiment — **Conversations top-10 bullets are cross-row patterns**, not individual conversations. A single conversation's analysis already lives in its row; the value of the synthesis is what shows up *across* rows.

The synthesis question is:

> "What recurring patterns across rated Aurie conversations should change how we build the product, write the prompt, or train the next iteration?"

A Conversations top-10 bullet looks more like:

> **Question-saturation correlates with Neutral ratings on `coach-prompter` (5 rows).** When ≥80% of Aurie turns end with `?` and there's no "stay here" affordance, users disengage post-final-reply (median silence ≥5 min). Sources: [tWL0ZSuz… — Neutral after 35m affair](https://www.notion.so/35d75e23af2081db8064e327d450222a), [xiF2Q… — Neutral grief](https://www.notion.so/35d75e23af20812198fccc1a4bbaae0b), …

…not like an Experiments top-10 bullet. Read the [quality bar](#quality-bar) carefully before drafting.

## Inputs

None required. Optional flags the user might pass:

- `dry_run: true` — produce the new section text and diff but skip the Notion write.
- `min_sources: <int>` — minimum row count per bullet cluster (default `1`). Raise to `2` or `3` once the corpus is large enough that single-row insights crowd out genuine cross-row patterns; while the corpus is small (≤ ~15 rows) the default of `1` lets row-level insights surface as their own bullets rather than getting filtered out.
- `max_bullets: <int>` — cap on the number of bullets to render (default `10`). The section is allowed to be shorter — if only 4 patterns clear the synthesis bar, ship 4. Don't pad with weak bullets to hit 10.
- `rating_filter: rated` — restrict to rows where `Rating ∈ {Positive, Negative, Neutral}` (drop `N/A`). Use when the synthesis should reflect *user-signalled* friction, not analyst observations on unrated conversations.

## Workflow

### Step 1 — Pull and classify every row from the Conversations database

Run the bundled classifier. It enumerates the data source over Notion REST, fetches
every row's body, splits it into canonical sections, and decides which rows carry a
real analysis:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/update-conversations-top10/scripts/classify_rows.py \
  --out /tmp/conversations-rows.json --summary
```

Optional flags: `--rating-filter rated` (see Inputs), and the three gate thresholds
(`--min-body-chars`, `--min-hypothesis-chars`, `--max-boilerplate-sections`) for the
rare case where a corpus shift makes a default wrong. Exit `1` means no row survived —
stop and tell the user; there is nothing to synthesise. Exit `2` means `NOTION_TOKEN`
is missing.

**Notion access is REST-first here, by necessity.** This is a full-database
enumeration; `notion-search` is semantic, capped at 25 results, and has no cursor, so
it cannot answer "every row." The MCP path is not a fallback for this step.

**Why a script owns the filter.** The rows this skill must exclude are not
*structurally* incomplete — the conversation harvest sweep writes every canonical
heading and then a templated one-liner under each ("User explored their feelings with
Aurie." / "None notable." / "None."). A heading-presence check passes them into the
clustering pool with nothing to cluster on. The four gates and their rationale are
documented in the script's module docstring; the short version is that the row must
carry a real `## Hypotheses to validate`, must not be mostly boilerplate, and must
clear a narrative-length floor — and each gate reports its own count so a future
divergence between them is visible rather than silently resolved.

**Read these fields off the script's JSON** rather than re-deriving them:

- `rows[]` with `verdict: "synthesisable"` — the clustering pool. Each carries its
  parsed `sections`, so Step 2 needs no second fetch.
- `synthesisable` / `template_stubs` / `missing_sections` / `dropped_by_rating_filter`
  — the split for Step 6's diff summary and Step 5's byline.
- `byline` — the ready-to-paste parenthetical for Step 5. Use it verbatim.
- `rows_passing_each_gate` — the four gates' independent counts. Compare them **to
  each other**, not to `synthesisable`: they describe the whole corpus, so with
  `--rating-filter rated` they legitimately exceed the selected pool. If the four
  disagree with each other, say so in Step 6 and let the user decide before writing —
  that means the corpus shape moved.

**The optional rating filter applies after classification**, so a stub is always
counted as a stub. Filtering first would attribute an unrated stub to the rating
filter and quietly shrink the stub count, which is the number that makes the
templated-row problem visible in the first place.

**There is no minimum row count.** Even with 3–5 rows there are usually one or two findings worth pinning at the top of the Learnings page. The synthesis adapts to the corpus size:

- **≤ 5 rows** — render whatever genuine patterns or row-level insights exist; expect 2–5 bullets. Single-row insights are allowed (they wouldn't be in a mature corpus, but they are now). Mark them as `(1 row)` in the bullet so readers know the evidence base is thin.
- **6–15 rows** — `min_sources` default `1` still applies, but cross-row clusters (when they exist) take priority over single-row insights. Expect 4–8 bullets.
- **> 15 rows** — synthesise raises naturally with the corpus. Consider passing `min_sources: 2` or `3` so weak single-row insights stop crowding out genuine cross-row patterns. Expect ~10 bullets.

If zero rows survive the filters, stop and tell the user — there's nothing to synthesise. Don't write an empty section.

### Step 2 — Cluster rows into pattern candidates

Every field below is already on each `synthesisable` row in the Step 1 JSON — read it
from there rather than re-fetching:

- `Conversation ID` (UUID)
- `Title`
- `Rating` (Positive / Negative / Neutral / N/A)
- `Source Type` (Cancellation Feedback / Quick Processing Rated / Coaching Rated / Conversation Rated / Chat Message Rated / Manual)
- `Conversation Type` (Quick Processing / Coaching / Other)
- `Prompt Version` (e.g. `coach-prompter:0.235.4`)
- `Vertical`
- `Themes` (multi-select)
- Body sections: `## Headline`, `## What worked`, `## What didn't`, `## Hypotheses to validate`, `## Recommended follow-up`
- Notion URL (canonical form: `https://www.notion.so/<id-without-dashes>`)

**Cluster the corpus along these axes** — the question is "which patterns recur across rows?":

1. **Themes clusters** — rows sharing 2+ `Themes` tags (`question_saturation` + `reframe_misfire`, `recommendation_misfire` + `closure_misfire`, etc.). The strongest signal: a multi-theme cluster that spans ≥3 rows of the same `Conversation Type` or same `Prompt Version`.
2. **What-didn't repetition** — paraphrased clusters of the `## What didn't` bullets. Look for the same friction shape stated different ways across rows ("ends every turn with a question" / "no stay-here affordance" / "validate → reframe → ask cadence runs N-for-N" → all the same underlying pattern). Cluster paraphrases together; cite all source rows.
3. **Hypothesis cross-validation** — rows whose `## Hypotheses to validate` overlap. A hypothesis stated in one row and *evidenced* in another (e.g. row A hypothesises "question-saturation kills ratings", row B shows it) is a strong top-10 candidate — it's a hypothesis that has *survived* a second observation.
4. **Rating-skew clusters** — patterns that only appear in `Negative` or `Neutral` rows but never in `Positive` ones (or vice versa). These are the highest-signal candidates because the rating contrast is the ground truth.

**Reject candidates with `< min_sources` supporting rows.** Default `min_sources` is `1`, so single-row insights are allowed by default. Raise it once the corpus grows past ~15 rows and cross-row patterns become the more interesting signal (`min_sources: 2` or `3`). When a bullet is backed by a single row, append `(1 row)` to the lead sentence so the reader knows the evidence base is thin.

### Step 3 — Rank the cluster pool

Rank surviving clusters by **decision impact** for product / prompt / system work:

- **Top tier** — clusters that suggest a specific prompt or product change (e.g. "add a `stay-here` affordance every Nth coaching turn"). Cross-row support is preferred but a single sharp row-level insight still earns a bullet on a small corpus.
- **Mid tier** — clusters that suggest a Braintrust eval to add, an instrumentation gap to close, or a feature flag worth running.
- **Lower tier** — clusters that reflect a stable strength (what's working) worth preserving when iterating the prompt.

Aim for a mix in the final cut. Targets scale with corpus size:

- **What's working** bullets drawn from `## What worked` clusters — protects strengths during prompt iteration. Aim for ~25% of the bullets.
- **Friction / hypothesis** bullets with concrete next steps. Aim for ~50–60% of the bullets.
- **Cross-tier clusters that span multiple `Conversation Type`s** (a pattern in both Coaching and Quick Processing is a system-level finding, not a prompt-level one) — include when present, don't manufacture.
- Don't repeat the same theme more than twice — if `question_saturation` shows up everywhere, lift one strong bullet and leave the rest as row-level detail.

### Step 4 — Write up to `max_bullets` bullets

The section can be as short as 2 bullets when the corpus is small and as long as `max_bullets` (default 10) when the corpus is mature. Ship the bullets the evidence supports — never pad.

Each bullet has four parts on a single line:

1. **Bold lead sentence** — the pattern itself, ≤25 words, falsifiable, includes the row count in parentheses (e.g. "**Question-saturation correlates with Neutral ratings on coach-prompter (5 rows)**", or "**First-conversation neutral ratings cluster around closure misfires (1 row)**" when only one row backs it).
2. One short clause on the **mechanism** — what specifically Aurie does, or fails to do, that produces the pattern.
3. One short clause on the **decision implication** — what this changes about the next prompt version / eval / feature.
4. `Sources: [<title 1>](<url 1>), [<title 2>](<url 2>), …` — every source row that backs the cluster (always at least one).

**Quality bar.**

Each bullet should be at the level of:

> **Question-saturation correlates with Neutral ratings on coach-prompter (5 rows).** When ≥80% of Aurie turns end with `?` and there's no "stay here" affordance, users disengage post-final-reply (median silence ≥5 min). Add a Braintrust eval case for `stay-here` cadence and prototype a non-question every Nth turn. Sources: [tWL0ZSuz… — Neutral affair](https://www.notion.so/35d75e23af2081db8064e327d450222a), [xiF2Q… — Neutral grief](https://www.notion.so/35d75e23af20812198fccc1a4bbaae0b), …

If you can't write a bullet that crisp, drop it — even a small corpus produces a stronger surface with 3 sharp bullets than 8 mushy ones.

### Step 5 — Build the replacement section

The full block to write back into the Learnings page:

```markdown
## Top 10 Learnings — Conversations
*Synthesized YYYY-MM-DD by `data:update-conversations-top10` from `<N>` of `<T>` rows below. Refreshed as new conversation analyses land, and on every `data:run-conversations-harvest-cadence` sweep.*

1. **<bullet 1 — pattern + row count>.** <mechanism>. <decision implication>. Sources: [<title>](<url>), …
2. **<bullet 2>.** <mechanism>. <decision implication>. Sources: [<title>](<url>), …
... (up to `max_bullets` total — ship fewer when the corpus is small)
```

Use today's date for `YYYY-MM-DD`. **Take `from <N> of <T> rows below …` verbatim from
the script's `byline` field** — don't retype the numbers. `<N>` is the count of rows that
survived Step 1's filter; `<T>` is the total row count in the database before filtering.

**The byline names the maintaining skill, and both counts.** A reader who finds the section stale needs to know what to run without going and grepping for it — so the skill id goes in the byline, not just "by agent". Both counts go in for a related reason: `<N>` alone reads as the corpus size, so a section synthesised from a filtered handful looks authoritative over a database it barely sampled. The section this replaced said "from 3 rows" while the database held 482, and nothing on the page made that visible.

**The counts are script-derived — don't recompute or paraphrase them.** When `<N>` is a
small fraction of `<T>`, the byline says why, and the script's `byline` field is where
that sentence comes from. It accounts for every row in `<T>` — stubs, structurally
incomplete rows, and rating-filter drops, each named with its own count — so the buckets
reconcile by construction. A byline worked out by hand is correct only for the run that
worked it out; the next run would have to derive the same split again, and nothing would
catch it getting the denominator wrong.

**Section heading.** Keep `## Top 10 Learnings — Conversations` even when the rendered list has fewer than 10 bullets — the heading names the surface, not the count, and matches the sister sections (`Top 10 Learnings — Experiments`, `— Growth Readings`, `— Ad Campaigns`).

**No Linear references in the body.** The byline reports what the synthesis is and when it ran — it does NOT cite the Linear ticket that introduced the skill. AI Knowledge content stays Linear-free (page bodies, row bodies, synthesis bylines, `# Sources` blocks). Linear lives in the commit / PR / SKILL.md "Related" section, not in Notion.

### Step 6 — Show the diff and require approval

Before writing to Notion, present the user with:

1. The count of rows considered, broken down by `Conversation Type`, `Rating`, and `Prompt Version`.
2. **The script's split, verbatim:** `<N> synthesisable / <M> template stubs / <I> missing sections / <D> rating-dropped` out of `<T>` total. Flag any disagreement in `rows_passing_each_gate`.
3. The set of clusters that survived `min_sources` and the size of each.
4. The new section, rendered (however many bullets it has).
5. A request for explicit approval: *"Approve writing this back to the Learnings page?"*

**Never write silently.** AI Knowledge writes always require user approval.

### Step 7 — Replace the existing section

Use `notion-update-page` with `command: update_content` against the Learnings page (id `34975e23-af20-8164-950c-c24797c2c625`).

Anchor the find-and-replace on the **trailing Conversations database tag**, which is stable:

- `old_str` starts at `## Top 10 Learnings — Conversations\n*<placeholder or prior synthesis line>*` and runs through to (but **not** including) the next line, which is the database tag:
  `<database url="https://www.notion.so/977a74fcb9c24057af622ddab7bdd663" inline="false" data-source-url="collection://2895c7d1-a921-49ac-ad16-91b3ac70951f">Conversations</database>`
- `new_str` is the freshly built section from Step 5, followed by the same trailing newline so the database tag stays exactly where it was.

Practical pattern — include the database tag as a **sentinel suffix** in both `old_str` and `new_str`. That keeps the match unambiguous and guarantees the database block isn't disturbed:

```
old_str:
  <existing Top 10 Conversations section>
  <database url="https://www.notion.so/977a74fcb9c24057af622ddab7bdd663" inline="false" data-source-url="collection://2895c7d1-a921-49ac-ad16-91b3ac70951f">Conversations</database>

new_str:
  <new Top 10 Conversations section>
  <database url="https://www.notion.so/977a74fcb9c24057af622ddab7bdd663" inline="false" data-source-url="collection://2895c7d1-a921-49ac-ad16-91b3ac70951f">Conversations</database>
```

If `old_str` doesn't match (section header renamed, database tag moved, page restructured), stop and surface the error to the user — do not guess the structure or re-anchor on something different.

### Step 7b — Invalidate the local cache

After the Notion write succeeds, delete `${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/learnings.md` so the next `retrieve-knowledge` read on `Learnings` picks up the new Top 10 section. Spec: [`shared:retrieve-knowledge/references/cache.md`](../../../shared/skills/retrieve-knowledge/references/cache.md).

If the cache root or the file doesn't exist, skip silently. Cache invalidation must never fail the write.

### Step 8 — Confirm and report

After the write succeeds, tell the user:

- The page URL: `https://www.notion.so/34975e23af208164950cc24797c2c625`
- The script's split (synthesisable / stubs / missing sections / rating-dropped / total).
- The number of clusters that survived `min_sources` and how many made it into the final cut.
- The final bullet count (e.g. "shipped 4 bullets — corpus is still small at 5 rows; expect that to grow on the next refresh once we have ~15 rows").
- Any 1–2 noteworthy shifts vs the previous Top 10 — new clusters that hit `min_sources` this run, or prior clusters that dropped out because new contradicting evidence landed.

## Edge cases

**Multi-version prompt comparisons.** If rows span ≥2 prompt versions (e.g. `coach-prompter:0.235.4` and `coach-prompter:0.240.x`), surface that in Step 6's diff summary — a pattern that was strong on v0.235 but weak on v0.240 is a *signal that the patch worked*, not a Top 10 bullet. Flag it for the user to decide whether to retire that learning.

**Most of the database is template stubs, and that is normal.** The synthesisable share
is small — 23 of 482 rows on 2026-08-10 — because the conversation harvest sweep records
a row per rated conversation while only hand-analysed conversations get a five-section
write-up. A run reporting a low `synthesisable` count is working correctly; say the split
plainly rather than raising it as a problem, and do not lower the gate thresholds to grow
the pool. Widen the pool by analysing more conversations (`data:analyze-user-conversations`
/ `data:profile-user`), which is what writes a real row.

**The gates disagree.** If `rows_passing_each_gate` does not report the same number four
times, the corpus shape has moved — most likely the sweep template changed. Surface the
divergence to the user before writing, and run `--dump-vocabulary` to see which section
values are unmatched (`*`-flagged) so the phrase list can be refreshed in the skill. A
one-off sentence appearing once is not worth adding; a new value appearing hundreds of
times is the new template.

**Theme drift.** New `Themes` options get added to the Notion multi-select on the fly (by design — see `Learnings/Conversations` schema). When clustering, treat semantically-related themes as one (`question_saturation` + `reframe_misfire` both signal "forward-motion via question") even if they're separate Notion options. Do *not* propose a Themes-cleanup as part of this skill; that's a separate maintenance task.

**Conflicting Hypotheses across rows.** If row A's hypothesis and row B's hypothesis are *opposite* (one predicts X, the other predicts not-X), do **not** merge into a single cluster. Either:
- Surface both as separate clusters with their row counts so the contrast is visible, or
- Drop the lower-evidence side and note the contradiction in Step 8's reporting so the user knows it's unresolved.

Resolution of the contradiction is a follow-up analysis task, not a synthesis call.

**Rows backfilled from Slack threads.** The earliest rows in the Conversations DB have `Aura API user profile — unavailable` in their Sources block. They're valid for the synthesis but their Vertical / Day in Trial were inferred, not fetched live. Don't weight them lower for that reason alone — the analyses themselves were produced from full Braintrust transcripts and stand on their own evidence.

**Cache stale after a recent record-knowledge write.** If `Learnings/Conversations` was written to less than a minute ago, the data source enumeration may still show stale state. Wait ~30s and re-fetch before clustering, or accept the staleness explicitly — re-running this skill is idempotent (no state change unless the write fires) so a re-run later is cheap.

**The page structure has changed.** If `notion-fetch` on the Learnings page reveals that the Conversations DB embed has moved or the Top 10 section header has been edited, **stop** and surface the structural drift. Do not silently re-anchor — the user may have intentionally restructured. Ask before proceeding.

## Out of scope

- This skill does **not** analyse or record individual conversations. Use [`data:analyze-user-conversations`](../analyze-user-conversations/SKILL.md) or [`data:profile-user`](../profile-user/SKILL.md) for that.
- This skill does **not** modify the Conversations database itself — only the Top 10 section on the parent Learnings page.
- This skill does **not** touch the Experiments / Growth Readings / Ad Campaigns sections of the Learnings page, the `# Sources` block at the bottom, or any sibling page (Ideas, UX Priors).
- This skill is **not** scheduled / auto-fired. It's on-demand only, matching the existing `update-*-top10` pattern.

## Related

- Sister skill: [`data:update-experiments-top10`](../update-experiments-top10/SKILL.md) — same shape; the Experiments synthesis is row-impact-ranked, the Conversations synthesis is cross-row-pattern-clustered.
- Sister skill: [`marketing:update-growth-readings-top10`](../../../marketing/skills/update-growth-readings-top10/SKILL.md) — third instance of the same shape.
- Source DB: [`Learnings/Conversations`](https://www.notion.so/977a74fcb9c24057af622ddab7bdd663) — schema in [`shared:record-knowledge/references/schemas/conversations.md`](../../../shared/skills/record-knowledge/references/schemas/conversations.md).
- Row filter: [`scripts/classify_rows.py`](scripts/classify_rows.py) — the four gates, the boilerplate vocabulary, and the `--dump-vocabulary` refresh mode. Behaviour is covered by [`tests/test_conversations_row_filter.py`](../../../../tests/test_conversations_row_filter.py).
- Linear: [PROD-1702](https://linear.app/aura-health/issue/PROD-1702) (this skill), [ENG-1116](https://linear.app/aura-health/issue/ENG-1116) (the script-derived row filter), [PROD-1701](https://linear.app/aura-health/issue/PROD-1701) (the Conversations DB), [PROD-1686](https://linear.app/aura-health/issue/PROD-1686) (parent improvement effort).
