---
name: rollover-linear-cycle
description: >
  Splits a Linear parent card across two cycles when work continues into the next sprint
  Renames the card with a previous-cycle suffix, creates a current-cycle sibling, and
  re-parents every not-done descendant to it. A no-args mode surfaces every previous-cycle
  umbrella still holding not-done children. Trigger on "rollover this card to next cycle",
  "split this Linear card across cycles", "continue this card in the new cycle", "any cards to
  roll over", "start of cycle cleanup", "what needs rolling over", "roll over everything from
  last cycle", "flatten it", "don't keep the parents", "flatten the structure / no sub-parents
  this cycle", "keep done-this-week on the new card", "close the epics and start fresh", or a
  pasted parent card URL. Also triggers on "are there cards to move to the new cycle", "do the
  same for {linear URL}". Do NOT use for creating single issues (`shared:add-linear-issues`),
  moving one card to a different cycle (edit the field directly), or velocity reports
  (`eng:summarize-weekly-output`).

metadata:
  version: "3.0.2"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/Linear  # cycles, issue tree, save_issue / GraphQL access pattern
---

# Rollover Linear Cycle

## Goal

Cleanly split a Linear parent card across two cycles: keep the original (renamed
`[Cycle N-1]`) in the previous cycle as the historical record of what shipped, and
create a new `[Cycle N]` sibling in the current cycle that owns the not-yet-done
work going forward.

When the parent has direct children that are themselves parents of subtasks (a
two-level epic structure), auto-detect and split those too — the new `[Cycle N]`
top-level card gets fresh nested parents that mirror the originals.

## Inputs

Two modes:

**Targeted mode** — a single Linear parent card reference:

| Input | Example |
|---|---|
| Identifier | `PROD-1741` |
| Linear URL | `https://linear.app/aura-health/issue/PROD-1741/ai-cost-review-and-changes` |
| URL with `[Cycle N-1]` already in the slug | `https://linear.app/aura-health/issue/PROD-1741/...-cycle-26` (rare — means the card was renamed but the split was abandoned) |

**Discovery mode** — no parent card. Triggered when the user invokes the skill with phrases like "any cards to roll over", "start of cycle cleanup", "roll over everything from last cycle". Scans Linear for `[Cycle N]` umbrellas (Step 0 below), then drops into targeted mode for each one the user picks.

If the user's intent is ambiguous (no parent passed, no discovery phrasing), ask. **Do not guess.**

## Definitions

- **Cycle N (current)**: the team's current cycle. The new card lands here.
- **Cycle N-1 (previous)**: the team's most recently completed cycle. The renamed original lands here.
- **"Done"**: a Linear status whose `statusType` is `completed`, `canceled`, or `duplicate`. These stay with `[Cycle N-1]`.
- **"Not done"**: every other `statusType` — `unstarted` (Todo, Backlog), `started` (In Progress, Eng In Progress, In Review, Blocked), `triage`. These move to `[Cycle N]`.
- **Nested parent**: a direct child of the input card that itself has at least one child issue.

## How this skill talks to Linear

Prefer the Linear MCP connector (`mcp__linear__get_issue`, `mcp__linear__list_issues`, `mcp__linear__list_cycles`, `mcp__linear__save_issue`) when available — fewer hops, no token to manage.

When MCP isn't reachable (Cowork sandboxes, host sessions without the connector loaded), fall back to the Linear GraphQL API directly using `LINEAR_API_KEY` from `~/.claude/settings.json`. The sibling `shared:add-linear-issues` skill ships a `linear_api.py` helper covering reads + writes; this skill mirrors its query shapes via `curl` or copies of that helper. Discovery mode (Step 0) is API-only by necessity — the MCP `list_issues` regex/title-pattern filter isn't reliable enough for the sweep.

## Workflow

### Step 0 — Discovery mode (only when no parent_issue is provided)

Skip this step when the user passed a specific parent. Otherwise:

1. **Sweep top-level cycle umbrellas.** Query Linear for issues whose title matches `\[Cycle \d+\]` and whose `parent` is null:

    ```graphql
    query {
      issues(
        filter: {
          title: { containsIgnoreCase: "[Cycle " }
          parent: { null: true }
        }
        first: 100
        orderBy: updatedAt
      ) {
        nodes {
          id identifier title url
          team { id key }
          cycle { number }
          state { type }
          children(first: 1) { nodes { id state { type } } }
        }
      }
    }
    ```

    Extract the cycle number from each title with regex `\[Cycle (\d+)\]`. Cards whose suffix doesn't parse cleanly are ignored.

2. **Resolve each team's previous cycle once.** For every distinct team in the result set, call `mcp__linear__list_cycles(teamId=…, type="previous")` (or the GraphQL `cycles` filter). Build `team_id → previous_cycle.number`.

3. **Filter candidates.** Keep an umbrella iff:
   - its `[Cycle N]` suffix's N matches its team's previous-cycle number (the cycle just ended), AND
   - it has at least one child that would **move** under the intended mode (default: any not-done child; under Variant A: any not-done child **or** any child completed on/after the cutoff — otherwise an umbrella whose only remaining work shipped this cycle is wrongly skipped). Re-run the child fetch from Step 2 — be cheap: `first: 100` is enough, AND
   - its state isn't itself `completed`/`canceled`/`duplicate` (an already-closed umbrella shouldn't roll over).

4. **Surface to the user** as a numbered list, with not-done child count and team:

    ```
    Candidates to roll over (Cycle 28 → Cycle 29):

    1. PROD-2186 — Aura Bugs and Fixes [Cycle 28]   (PROD, 7 not-done)
    2. ENG-XXX  — Eng Backlog [Cycle 28]            (ENG, 3 not-done)

    Pick one (e.g. `1`), pass a comma list (`1,2`), or say `all`. Anything else cancels.
    ```

5. **For each pick**, run Steps 1–8 sequentially (don't parallelise across candidates — each gets its own plan + confirm + write). Re-print Step 8's report between candidates so the user sees the running tally.

**Empty result.** If the sweep returns no candidates, print `No cycle umbrellas need rolling over (no [Cycle N-1] cards with not-done children).` and exit. Mid-cycle this is the expected outcome.

### Step 1 — Resolve the parent

Parse the user's input into a Linear issue identifier (e.g. `PROD-1741`). Then fetch the parent:

```
mcp__linear__get_issue(id=<identifier>, includeRelations=false)
```

Capture from the response: `id`, `title`, `teamId`, `cycleId`, `projectId`, `projectMilestone.id`, `labels`, `priority.value`, `assigneeId`, `description`.

**Guard:** if the title already ends with ` [Cycle <N>]` matching the current cycle, the rollover may have already happened. Show the title and ask the user to confirm before proceeding.

### Step 2 — Fetch descendants (one level deep + nested parents)

Get direct children:

```
mcp__linear__list_issues(parentId=<parent.id>, limit=100)
```

For every direct child where the child also has children of its own, fetch the grandchildren:

```
mcp__linear__list_issues(parentId=<child.id>, limit=100)
```

A child is a "nested parent" if its grandchildren list is non-empty.

**Variant fields.** Also capture each descendant's `completedAt` — Variant A (below) classifies on it. Under Variant B/C (flatten), a tree can be deeper than two levels; keep recursing into every nested parent so no descendant is missed, since all of them will be re-parented flat. Under **Variant D** also capture each descendant's `canceledAt` and its `team` key, and **always recurse the full tree** (not just B/C) — the per-cycle bucketing reads the terminal date of every done descendant and the close-sweep needs every parent's full subtree.

**Pagination.** `list_issues` returns at most `limit` rows with no cursor via MCP — a wide umbrella (>100 direct children, or a nested parent with >100 children) truncates silently, which misclassifies the missing rows. For any parent that hits the cap, page through the full set: via GraphQL loop on `children(first: 100, after: <endCursor>)` using `pageInfo.hasNextPage` / `endCursor`; via MCP re-query until the returned count is < `limit`. This matters most under Variant B/C, where a truncated fetch drops movers from the flat list (the PROD-881 run had 419 descendants).

**Guard:** if the parent has zero direct children, exit with `"<parent.id> has no children — nothing to roll over."`. Do not create an empty `[Cycle N]` sibling.

### Step 3 — Resolve cycles

The top-level parent and each nested parent may belong to different teams (e.g. PROD-1741 sits on Product/Design but has an ENG-497 child on Engineering). For every distinct team encountered across the parent + nested parents, resolve both cycles:

```
mcp__linear__list_cycles(teamId=<teamId>, type="previous")
mcp__linear__list_cycles(teamId=<teamId>, type="current")
```

Cache the `(teamId → {previous, current})` mapping so each team is looked up once. Under Variant A, also keep the current cycle's `startsAt` — it's the completion cutoff.

Under **Variant D**, fetch each team's **full cycle list** (`team.cycles`), not just previous/current — done work can bucket into cycles older than previous (e.g. a card that skipped a rollover has Cycle N-2 done items), and each per-cycle done card is create-then-updated into that older cycle's id. Keep every cycle's `number`, `id`, and `startsAt` per team; the weekly bucketing floors `(terminal date − currentCycle.startsAt)` into 7-day windows against that team's own anchor. `team.cycles` is a paginated connection — page it (`cycles(first: 100, after: <endCursor>)` until `pageInfo.hasNextPage` is false) so an older cycle a done item buckets into isn't missed. (Bucketing is arithmetic off `currentCycle.startsAt`, so a missing older cycle only bites at the create-then-update step, when you need that cycle's id.)

**Guard:** if either previous or current returns empty for a team that needs a rename or a new sibling, stop and report — the team probably doesn't have cycles enabled.

### Step 4 — Classify descendants

**If any variant is active, read the [Variants](#variants-opt-in) section first — it overrides the classification and destination rules in this step and Steps 5 + 7.** The default (no variant) is:

For each direct child and grandchild, classify by `statusType`:

- `completed`, `canceled`, or `duplicate` → **stays** under `[Cycle N-1]` (no change — these are dead-end states).
- everything else → **moves** to `[Cycle N]` under the matching new parent.

Build a tree of moves. For the top-level case (no nested parents), every not-done direct child moves under the new top-level. For the nested case, not-done grandchildren of nested parent X move under "new X `[Cycle N]`"; nested parents themselves are renamed (if they have any done children) and new `[Cycle N]` siblings are created (if they have any not-done children).

Under **Variant A** the split is by `completedAt` vs the cutoff, not `statusType` alone. Under **Variant B/C** movers are re-parented flat and (Variant C) nested parents are retired instead of recreated — plus apply the **rescue rule** so a parent that changes side doesn't drag other-bucket children with it.

### Step 5 — Build the plan

Print a plan with three sections. Use Markdown so each card identifier is a clickable link.

**A. Renames (in place — title + cycle change)**

| Card | New title | Move to |
|---|---|---|
| `PROD-1741` | `AI Cost Review and Changes [Cycle 26]` | Cycle 26 (Product/Design) |
| `PROD-1824` | `AI Cost Improvement [Cycle 26]` | Cycle 26 (Product/Design) |
| `ENG-497` | `Cost Analysis [Cycle 26]` | (already in Cycle 26 ✓) |

Skip nested parents that have zero done children (no point keeping them in `[Cycle N-1]` if nothing landed there). Skip cycle moves that would be a no-op.

**B. New cards to create (in `[Cycle N]`)**

For each card being renamed, create a sibling `<title> [Cycle N]`. Top-level card includes a description linking back to the renamed original. Nested new parents are children of the new top-level. Each new card mirrors `projectId`, `projectMilestone.id`, `labels`, `priority`, `assigneeId` from the original.

**C. Re-parentings (move not-done descendants)**

Every "moves" descendant from Step 4 gets its `parentId` set to the matching new parent. Children keep their own cycle assignment — only the umbrella parent label signals the cycle.

**Skip nothing.** If a section is empty, write `(none)` rather than omitting it. The user should be able to read the plan and predict the final shape exactly.

**Variant A alone doesn't change the plan's shape** — it only changes which descendants land in the "moves" set (Step 4), so nested parents are still recreated as in the default. **Variant B or C** does change the shape: plan section **B** creates only the one new top-level card (no recreated nested parents); **C** adds a "retire + close epics" section; **C**'s rescue re-parentings and **B**'s flat re-parentings both live in **C (Re-parentings)**. Mirror whatever the [Variants](#variants-opt-in) section prescribes so the plan still predicts the exact final shape.

### Step 6 — Confirm

End the plan with a single question: `Proceed?` Wait for an explicit yes (or any standard affirmative). On anything else, stop without writing.

### Step 7 — Execute

Run writes via `mcp__linear__save_issue`. Order matters:

1. **Renames + cycle moves** (independent — run in parallel).
2. **Create new top-level `[Cycle N]` card** (must complete first to get its ID).
3. **Create new nested parents** as children of the new top-level (parallel — independent of each other). *Under Variant B/C, skip this — no nested parents are recreated.*
4. **Re-parent movers** to the matching new parent (parallel — independent). *Under Variant B/C this is a flat re-parent to the one new top-level card.*
5. **(Variant C only) Retire epics — after step 4 completes.** This must run *after* the actionable children have left the epics, not in parallel with step 4: move each epic shell under `[Cycle N-1]`, `Cancel` still-open ones, and run the rescue re-parentings. If the shell moved first, its not-yet-re-parented actionable children would ride along to the historical side. Honour the [cross-team cycle gotcha](#cross-team-cycle-gotcha) when setting the shell's cycle.

Capture the new card IDs as they come back. If any step fails, halt and report what landed vs what didn't.

**Past-cycle cards need create-then-update.** A newly *created* issue cannot be placed in an already-ended cycle — Linear silently assigns it to the team's current cycle regardless of the `cycle` field on the create call. An *update* to a completed cycle does stick (that's how the renamed original moves back to `[Cycle N-1]`). So when a new card must live in a past cycle — the historical `[Cycle N-1]` card in the default flow lands in `[Cycle N]`, or any of the per-cycle done cards under [Variant D](#variant-d--per-cycle-date-routed-done-cards) — create it first (it lands in the current cycle), then issue a follow-up `save_issue` setting its `cycle` to the past cycle id, and verify the returned `cycleId` matches. Do not trust the create response's `cycleId`.

### Step 8 — Report

Print the final structure mirroring the plan, but with the new IDs filled in. Example:

```
**Cycle 26 (renamed):**
- PROD-1741 "AI Cost Review and Changes [Cycle 26]" — keeps 17 done children
- PROD-1824 "AI Cost Improvement [Cycle 26]"      — keeps 6 done children
- ENG-497  "Cost Analysis [Cycle 26]"            — keeps 17 done children

**Cycle 27 (new):**
- PROD-1922 "AI Cost Review and Changes [Cycle 27]"
  ├── PROD-1923 "AI Cost Improvement [Cycle 27]" — 5 not-done children re-parented
  └── ENG-547  "Cost Analysis [Cycle 27]"       — 3 not-done children re-parented
```

## Variants (opt-in)

The workflow above is the default: split by done vs not-done, recreate nested parents in
`[Cycle N]`. Apply a variant only when the user asks for it; otherwise the default holds.
Variants compose — a big evergreen umbrella typically uses A + B + C together.

### Variant A — "Done-this-week" completion cutoff

Default classification is binary (done → stays, not-done → moves). At start of cycle the
user often wants the new card to own the *active* cycle's work, including what already
shipped this cycle. Reclassify by `completedAt` against a cutoff:

- **Cutoff** = the current cycle's `startsAt` (from `list_cycles(type="current")`). Cycles
  are weekly here, so "this week" is the current cycle window. Compare each descendant's
  `completedAt` against the cutoff **for that descendant's own team** — Step 3 already caches
  `startsAt` per `teamId`, and cross-team descendants have different cycle boundaries, so a
  single global cutoff would misclassify them.
- `completed`/`canceled` with `completedAt` **before** the cutoff → **stays** in `[Cycle N-1]`.
- `completed` with `completedAt` **on/after** the cutoff → **moves** to the new card (shipped
  this week — belongs with the active card).
- not-done → **moves** (as in the default).

Everything else is unchanged. Call out in the plan that done-this-week items are moving, so
the user isn't surprised the new card has completed children.

### Variant B — Flatten mode

When the user says "flatten", "don't keep the sub-parents", "flat list this cycle":

- Do **not** recreate nested parents. Every **moving** descendant — at any depth — is
  re-parented **flat** as a direct child of the single new top-level card.
- The `[Cycle N-1]` side keeps its existing structure for staying items (don't flatten
  history unless asked).
- This makes the rescue rule below load-bearing — read it before executing.

### Variant C — Retire-and-close epics (evergreen umbrellas)

An evergreen umbrella (a standing bucket like "Aura Improvements", not itself a `[Cycle N]`
card) can still roll over: rename it `[Cycle N-1]` and create a fresh sibling for `[Cycle N]`
as usual. With flatten mode, retire its sub-epics rather than dragging their shells onto the
flat new card:

- The epic **containers** don't move to the new card. Move each epic **shell** back under
  `[Cycle N-1]` and **close** it — set still-open epics to `Canceled` (superseded/emptied,
  not completed); leave already-Done epics as-is.
- The epic's **actionable** children (not-done + done-this-week under Variant A) flatten onto
  the new card.
- The epic's **done-before-cutoff** children stay nested under it — because the epic itself
  now lives under `[Cycle N-1]`, they need no rescue.

### Variant D — Per-cycle date-routed done cards

For a long-lived card that spans **more than two cycles** of completed work (a rollover was
skipped, so one card accumulated several weeks of done items), the user may want each done
task filed on a done card for the cycle-week it was actually completed in — not all lumped
under one `[Cycle N-1]` label. Trigger on phrasings like "route the done tasks to the cycle
they were done in", "a done card per cycle", "each cycle is a week — split by when it was done".

- **Bucket by completion week.** Cycles are weekly; anchor on the current cycle's `startsAt`
  and floor `(completedAt|canceledAt) - anchor` into 7-day buckets — this yields a **week
  offset** (0 = current cycle, -1 = previous, -2 = two ago…), not a cycle number. Map it to
  the real cycle number with `currentCycle.number + offset` (equivalently, look the offset up
  in the team's fetched cycle list by `startsAt`). Do this per the descendant's own team
  ([cross-team boundaries](#cross-team-cycle-gotcha)), since each team has its own current
  cycle number and anchor.
- **One historical card per non-empty bucket.** Rename the original to the cycle matching its
  *origin* (e.g. a card created as the Cycle N-1 continuation → `[Cycle N-1]`, kept in place so
  its same-cycle done work needs no re-parenting), and create a new `[Cycle K]` done card for
  each other cycle that has completed work. Create-then-update every one of these into its past
  cycle (see [Step 7](#step-7--execute)'s past-cycle note).
- **Route nested epics by their children's work, not the container's checkbox.** An epic whose
  children all completed in cycle K belongs on the `[Cycle K]` card as an intact subtree, even
  if the container's own `completedAt` is a later week (a container is often ticked days after
  its work lands). Move the whole subtree; don't split it by per-child date. A still-open epic
  with genuinely open children goes to the ongoing card intact (its done children ride along as
  epic history). A mixed-cycle epic that is all-done stays intact on the most-recent cycle it
  touched.
- **Then close completed parents (all children terminal → Done).** After re-parenting, sweep
  every parent bottom-up: a parent with ≥1 child and all descendants terminal
  (`completed`/`canceled`/`duplicate`) → set **Done**; an emptied container whose work all moved
  away → `Cancel` (superseded). This cascades: once the sub-epics close, the historical cycle
  umbrellas themselves become all-terminal and close too. A parent with any open child stays open.

This composes with Variant A's cutoff (the current cycle's bucket is the "done-this-week" set →
those stay with the ongoing card). Call out in the plan which cycle each done card represents and
flag any container routed against its own `completedAt`.

### Rescue rule (whenever a nested parent changes side)

When a nested parent lands in a different bucket than some of its children, those children
would silently follow the parent to the wrong cycle. Re-parent them explicitly:

- A **moving** nested parent with **staying-bucket** children (default: done/canceled/duplicate;
  under Variant A: done-before-cutoff) → re-parent those children **up to `[Cycle N-1]`** so they
  stay on the historical side. *(Seen with ENG-880 "LiveKit go-live": moved to the new card while
  its 12 done-in-cycle children were rescued back to `[Cycle N-1]`.)*
- A **staying** nested parent with **moving** children → re-parent those children out to the
  new card (flat, under flatten mode).

### Cross-team cycle gotcha

`cycleId` is **team-scoped** — a card can only be assigned to a cycle owned by its own team.
Never set a PROD cycle id on an ENG/ENGMOB card (the write no-ops or errors). Only set the
cycle on same-team cards (the umbrella and same-team nested parents); for cross-team
descendants and retired epics, leave their cycle alone — the title suffix on the umbrella
carries the cycle signal.

## Hardcoded workspace values

Avoid runtime lookups for known IDs — they don't change. Source: `shared:add-linear-issues`.

### Teams

| Team | ID |
|---|---|
| Product/Design | `7e95fa4a-7510-447d-adc7-c663612e70c6` |
| Engineering | `ffcc6973-8ebf-4f92-b3b2-5568decbbae8` |
| Backend | `e25556d5-9f7f-4758-8ac3-4505102aa550` |
| Prompt | `8b9add50-f86d-4719-8567-0afc8a063fbb` |
| Data/DS | `aa5190e7-50c9-4de8-8dd6-a252fd5b78c7` |

### Status type semantics

| `statusType` | Classification |
|---|---|
| `completed` | Stays in `[Cycle N-1]` |
| `canceled` | Stays in `[Cycle N-1]` |
| `duplicate` | Stays in `[Cycle N-1]` (dead-end state — semantically closed) |
| `unstarted` (Todo, Backlog) | Moves to `[Cycle N]` |
| `started` (In Progress, Eng In Progress, In Review, Blocked) | Moves to `[Cycle N]` |
| `triage` | Moves to `[Cycle N]` |

## Title-suffix rules

- The cycle suffix goes at the very end, preserving any pre-existing brackets.
  `"Conversation - Improve latency [Iteration 2]"` becomes `"Conversation - Improve latency [Iteration 2] [Cycle 26]"`, **not** `"Conversation - Improve latency [Cycle 26] [Iteration 2]"`.
- If the title already ends in `[Cycle <N>]` matching the resolved Cycle N-1, treat the rename as a no-op (already labelled). If it matches Cycle N, the card was probably already rolled over — stop and ask.
- Use the cycle's **number**, not its name. `Cycle 26` is right; `Sprint 26` is wrong even if the team uses the word "sprint" colloquially.

## New-card description template

The new top-level `[Cycle N]` card gets a description that links back to the renamed
original. Other new parents (nested) don't need the boilerplate — being a child of
the new top-level is signal enough.

```markdown
Continuation of [<ORIGINAL-ID> — <ORIGINAL TITLE> [Cycle N-1]](<ORIGINAL-URL>) into Cycle N.

Cycle N-1 retained all completed work; Cycle N picks up the remaining items.
```

If the original card had no description, the new top-level still gets the boilerplate
above (a one-line continuation pointer). Don't try to summarise the work — that lives
in the children.

## Edge cases

- **Parent has no children.** Exit early: `"<parent.id> has no children — nothing to roll over."`. Do not create an empty `[Cycle N]` sibling.
- **All children are done.** The work doesn't need to roll over. Tell the user: `"All <N> children are done — no remaining work to move. Nothing to do."` and exit without writes. **Exception — Variant A:** if some of those done children were completed *this cycle* (`completedAt` ≥ the cutoff), they still move to the new card, so don't take this early exit; only exit when every child is done *before* the cutoff.
- **All children are not done.** Nothing stays in `[Cycle N-1]`. The rename + cycle move on the original still happens (so the historical name pins to the cycle the work was *attempted* in) and every child re-parents to the new `[Cycle N]` card. Mention this in the plan so the user understands the original will be a renamed-but-empty parent.
- **A nested parent's title already has `[Cycle N-1]`.** Skip the rename for that one; still create its `[Cycle N]` sibling.
- **Original is currently in Cycle N (auto-rolled by Linear).** Still rename + move back to Cycle N-1. Linear auto-rolls in-progress cards forward; the rollover skill un-rolls and labels them.
- **Children belong to multiple teams.** Each child keeps its own team's cycle assignment when re-parented. The `[Cycle N]` / `[Cycle N-1]` labels are just title suffixes on the umbrella parents — they don't dictate the child's team or cycle.
- **A "not done" child has been canceled but the user expected it to roll forward.** Trust the status. If the user disagrees, they can re-open the card or override during the confirmation step.
- **Re-run on already-rolled-over state.** A second run on the same input should detect the `[Cycle <current>]` suffix on the original parent (or find that all not-done work has already moved away) and exit with a no-op message. Don't double-create.
- **`mcp__linear__save_issue` fails on a re-parent.** Halt the batch, report what succeeded and what failed, and surface the error message so the user can fix manually (usually a permissions or stale-cache issue — re-running after a refresh tends to resolve it).

## Output

A markdown summary printed in chat with:

1. The renames that landed (with their new titles + cycle).
2. The new cards created (with new IDs, as Markdown links).
3. The re-parentings (count per new parent, with the list of re-parented IDs).
4. Any failures (if Step 7 partially succeeded).

No file writes, no Slack posts, no Notion writes. This skill is Linear-only.

## Naming and conventions

- Skill is **idempotent**: re-running on an already-rolled-over parent reports a no-op rather than duplicating cards. The detection signal is the `[Cycle <current>]` suffix on the title (or, if the user accepted the rename but the writes failed mid-batch, the partial-failure path in Step 7).
- Skill is **read-side-then-plan-then-write**: no `mcp__linear__save_issue` calls run before Step 6's confirmation.
- All `save_issue` calls in Step 7 that don't depend on each other run in parallel.

## References

- [PR/example reference: PROD-1741 → PROD-1922 split (2026-05-18)](https://linear.app/aura-health/issue/PROD-1741) — the canonical two-level case (renames + 3 new cards + 8 re-parentings).
- [PR/example reference: PROD-694 → PROD-1924 split (2026-05-18)](https://linear.app/aura-health/issue/PROD-694) — single-level case with 4 re-parentings.
- [PR/example reference: PROD-2186 → PROD-2284 split (2026-06-01)](https://linear.app/aura-health/issue/PROD-2186) — Aura Bugs and Fixes umbrella, 7 not-done re-parentings; the manual run that motivated Step 0's discovery mode.
- [PR/example reference: PROD-2878 → PROD-3330 split (2026-07-07)](https://linear.app/aura-health/issue/PROD-2878) — Variant A (done-this-week cutoff) + Variant B (flatten) + the ENG-880 rescue rule: 16 flat movers, 12 done-in-cycle children rescued back to `[Cycle 33]`.
- [PR/example reference: PROD-881 → PROD-3332 split (2026-07-07)](https://linear.app/aura-health/issue/PROD-881) — Variants A + B + C on a 419-descendant evergreen umbrella: 114 flat movers, 6 sub-epics retired to `[Cycle 33]` (3 Canceled), cross-team cycle gotcha in play.
- [PR/example reference: PROD-3332 Aura Improvements split (2026-07-20)](https://linear.app/aura-health/issue/PROD-3332) — Variant D on a 187-descendant evergreen that had skipped a rollover: done work date-routed to `[Cycle 34]` (renamed original, 102) + a new `[Cycle 35]` card (26, incl. an all-done epic moved intact) + a new evergreen keeping sub-epics (54); completed parents cascade-closed, empty Features container Canceled; both new `[Cycle 35]`/done cards create-then-updated into the past cycle.
- [PR/example reference: PROD-3330 LiveKit Integration split (2026-07-20)](https://linear.app/aura-health/issue/PROD-3330) — Variant D on a 44-descendant feature: subtrees routed intact by their children's cycle (Bugs epic kept on `[Cycle 34]` despite a c35 checkbox date; QA epic intact on `[Cycle 35]`), only ENG-880 "go-live" moved to the ongoing card, 3 re-parents total.
- Sibling skill: [`eng:summarize-weekly-output`](../summarize-weekly-output/SKILL.md) — read-only weekly summary; chain after a rollover to confirm Cycle N-1 numbers.
- Sibling skill: [`shared:add-linear-issues`](../../../shared/skills/add-linear-issues/SKILL.md) — single-card creation; ships `linear_api.py`, which this skill mirrors when Linear MCP isn't available.
