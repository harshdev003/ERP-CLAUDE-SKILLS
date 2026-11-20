---
name: setup-weekly-cycle
description: >
  Automates the weekly Linear cycle rollover for the Product/Design team. For each standard
  tracker issue, creates the missing current-cycle and next-cycle parents, moves active
  sub-issues forward from the previous-cycle parent, routes backlog sub-issues to the
  next-cycle parent instead, then marks previous-cycle parents Done and files them in the
  previous cycle bucket. Also rolls over the nested trackers that hang off a top-level parent,
  and sweeps current-cycle parents for backlog sub-issues that belong in the next cycle.
  Trigger when someone says "setup weekly cycle", "do the cycle rollover", "roll over Linear
  cycles", "prepare Linear for this week", "setup the new cycle", "cycle rollover", or
  "transition to the new cycle".

metadata:
  version: "4.1.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/Linear  # cycle + issue model, MCP access conventions
---

# Setup Weekly Cycle

Automates the full weekly Linear cycle rollover for the Product/Design team in one pass:
creates the trackers for the current and next cycle, redistributes unfinished work from the
cycle that just ended, and closes that cycle out.

Access Linear through the Linear MCP server. Every step below is an MCP call.

**Page every `list_issues` call until it is exhausted.** `limit: 250` caps one page, it does
not promise the whole set — an active cycle and a long-lived tracker both routinely exceed it.
Whenever a response reports `hasNextPage: true`, re-issue the same call with its `cursor` and
append the results, repeating until `hasNextPage` is false. This applies to every call in
Steps 2, 4, 5, and 7 without exception. A truncated read is the one failure that corrupts this
workflow silently: a tracker that exists reads as missing and gets created twice, and a
sub-issue that exists is never migrated.

---

## Step 0: Read the Roster

Read [`references/trackers.md`](references/trackers.md) before anything else. It holds the
team and project IDs, the 11 top-level tracker stems with their metadata, and the 5 nested
trackers with their parent stems. Every later step keys off those tables.

---

## Step 1: Fetch Cycles

Fetch all three cycles for both teams in parallel:

```
list_cycles(teamId: "<Product/Design team id>", type: "previous")
list_cycles(teamId: "<Product/Design team id>", type: "current")
list_cycles(teamId: "<Product/Design team id>", type: "next")
list_cycles(teamId: "<Engineering team id>", type: "previous")
list_cycles(teamId: "<Engineering team id>", type: "current")
list_cycles(teamId: "<Engineering team id>", type: "next")
```

Store each cycle's `id`, `number`, and `title`. The Product/Design cycle `number` determines
the `[Cycle N]` suffix in every issue title, nested trackers included. Call these
`<prev_num>`, `<curr_num>`, `<next_num>`.

Build a title→id map for the Engineering cycles (`"Cycle 39"` → `edd9a116-…`). Only the
`Update Knowledge and Skills` tracker reads from it, keyed by the Product/Design cycle's
title.

---

## Step 2: Inventory the Three Cycles

Build the complete picture from three calls — one per cycle — rather than searching per
stem:

```
list_issues(cycle: "<prev cycle id>", team: "Product/Design", limit: 250,
            fields: ["id", "title", "statusType", "archivedAt", "parentId", "cycleId"])
list_issues(cycle: "<curr cycle id>", team: "Product/Design", limit: 250,
            fields: ["id", "title", "statusType", "archivedAt", "parentId", "cycleId"])
list_issues(cycle: "<next cycle id>", team: "Product/Design", limit: 250,
            fields: ["id", "title", "statusType", "archivedAt", "parentId", "cycleId"])
```

Page each of the three calls to exhaustion per the pagination rule above. The completeness
claim below rests entirely on it.

For each of the 11 stems, scan the combined inventory for a title containing both the stem
and `[Cycle <N>]`, case-insensitively, for each of the three N values. Match the em-dash
stem on the ASCII fragment `Aurie AI CRO`.

Record, per stem: the previous-cycle tracker id, whether a current-cycle tracker exists, and
whether a next-cycle tracker exists.

Once every cycle is fully paged, a stem absent from the inventory genuinely has no tracker
there — the membership list is complete, so absence is a fact rather than a search miss. Two
conditions in the inventory need care before you act on them:

- A tracker whose title says `[Cycle <prev_num>]` but whose `cycleId` is the **current**
  cycle is a mis-titled current-cycle tracker. Reconcile it per
  [`references/edge-cases.md`](references/edge-cases.md) → *Title disagrees with cycle
  membership* before treating the stem as missing a current-cycle parent.
- A previous-cycle tracker whose `statusType` is `duplicate`, `canceled`, or which is
  archived does not get a state change in Step 8. Flag it now.

Nested trackers on Engineering do not appear in a Product/Design inventory. Step 5 resolves
those separately.

---

## Step 3: Create Missing Parent Issues

For each stem missing a current-cycle or next-cycle parent, create it from the roster
metadata:

```
save_issue(
  title: "<stem> [Cycle <N>]",
  team: "Product/Design",
  project: "<project-id>",      // omit for stems with no project
  labels: ["<label>"],
  priority: <value>,            // omit for stems with no priority
  assignee: "<assignee>",
  cycle: "<current or next cycle id>"
)
```

Create the missing current-cycle batch in parallel, then the missing next-cycle batch.

**Both batches must land before Step 6.** Active sub-issues move to current-cycle parents
and backlog sub-issues move to next-cycle parents, so both generations are migration targets.

**Metadata inheritance.** For a stem whose roster row carries no project or no priority,
`get_issue` its previous-cycle tracker and mirror the live `project`, `labels`, `priority`,
and `assignee`. Leave `projectMilestone` unset on every created tracker. Rationale in
[`references/edge-cases.md`](references/edge-cases.md) → *Metadata for stems whose roster row
is thin*.

---

## Step 4: Fetch and Classify Sub-Issues

For each previous-cycle parent found in Step 2, fetch its sub-issues in parallel:

```
list_issues(parentId: "<prev-cycle-parent-id>", limit: 250,
            fields: ["id", "title", "statusType", "archivedAt", "team", "cycleId"])
```

Classify every sub-issue into one of four buckets:

| Bucket | Condition | Destination |
|--------|-----------|-------------|
| **Nested tracker** | Title matches a nested stem + `[Cycle <prev_num>]` under its roster parent stem | Step 5 owns it — excluded from Step 6 |
| **Active** | `statusType` is `unstarted` or `started`, and `archivedAt` is null | Current-cycle parent |
| **Backlog** | `statusType` is `backlog`, and `archivedAt` is null | **Next**-cycle parent |
| **Skip** | `statusType` is `completed`, `canceled`, or `duplicate`, or `archivedAt` is set | Left in place |

Backlog work is not scheduled for the cycle now starting, so parking it on the current-cycle
parent overstates that cycle's scope. Routing it one cycle further out keeps it tracked
without booking it against work in flight.

Sub-issues on other teams are in scope and migrate normally — see
[`references/edge-cases.md`](references/edge-cases.md) → *Sub-issues on other teams*.

---

## Step 5: Roll Over the Nested Trackers

Apply the same rollover logic one level down, once per nested stem in the roster. The five
nested trackers are independent of each other; run them in parallel.

For each nested stem:

1. **Find the previous-cycle nested tracker.** Use the Step 4 classification. If its parent
   stem's previous-cycle tracker was not found, or has no matching sub-issue, search directly
   on the nested tracker's own team:

   ```
   list_issues(query: "<nested stem> Cycle <prev_num>", team: "<nested tracker team>")
   ```

   If neither finds it, run sub-steps 2 and 3 so the current- and next-cycle trackers exist,
   skip sub-steps 4 and 5, and note the miss.

2. **Find existing current- and next-cycle nested trackers.** Match on title containing both
   the stem and `[Cycle <N>]`, case-insensitively.

3. **Create whichever is missing**, parented to the corresponding cycle's parent-stem issue
   from Step 2/3:

   ```
   save_issue(
     title: "<nested stem> [Cycle <N>]",
     team: "<nested tracker team>",
     project: "<project-id>",      // omit for stems with no project
     labels: ["<label>"],
     priority: <value>,            // omit for stems with no priority
     assignee: "<assignee>",
     parentId: "<parent stem [Cycle <N>] issue id>",
     cycle: "<cycle id — see below>"
   )
   ```

   **Cycle id by team.** A nested tracker on Product/Design takes the Product/Design cycle id
   directly. `Update Knowledge and Skills` is on Engineering, so it takes the Engineering
   cycle whose `title` matches the Product/Design cycle's title, from the Step 1 map.

4. **Migrate its sub-issues**, using the same four-bucket classification as Step 4 — active
   to the current-cycle nested tracker, backlog to the next-cycle one. Runs only when
   sub-step 1 found a previous-cycle nested tracker:

   ```
   list_issues(parentId: "<prev-cycle nested tracker id>", limit: 250,
               fields: ["id", "title", "statusType", "archivedAt", "team"])
   save_issue(id: "<sub-issue-id>", parentId: "<current- or next-cycle nested tracker id>")
   ```

5. **Close out the previous-cycle nested tracker.** It stays parented to its own cycle's
   parent-stem issue — a nested tracker closes out under the parent for the cycle it belongs
   to, not the cycle now starting. Runs only when sub-step 1 found one:

   ```
   save_issue(
     id: "<prev-cycle nested tracker id>",
     state: "Done",
     cycle: "<previous cycle id for that tracker's team>"
   )
   ```

   A nested tracker whose `statusType` is already `duplicate` or `canceled`, or which is
   archived, keeps its state — set `cycle` only and report the state, exactly as Step 8 does
   for top-level parents. That state carries information, and `Done` would erase it.

---

## Step 6: Migrate Sub-Issues

For each sub-issue classified in Step 4 — excluding nested trackers, which Step 5 owns —
set its `parentId` to the corresponding tracker for its destination cycle. Run all updates in
parallel:

```
save_issue(id: "<sub-issue-id>", parentId: "<destination-parent-id>")
```

The corresponding tracker is the one with the same stem as the previous-cycle parent the
sub-issue came from: the **current**-cycle tracker for an active sub-issue, the
**next**-cycle tracker for a backlog sub-issue.

---

## Step 7: Sweep for Backlog Sub-Issues

Backlog work accumulates under trackers this run is not otherwise rewriting, so sweep both
open generations for it.

For every **current-cycle** tracker — all 11 stems plus the 5 nested trackers, whether it
was created in Step 3 or already existed — fetch its sub-issues and move any `backlog`
child to the matching next-cycle tracker:

```
list_issues(parentId: "<curr-cycle-parent-id>", limit: 250,
            fields: ["id", "title", "statusType", "archivedAt", "team"])
save_issue(id: "<backlog-sub-issue-id>", parentId: "<next-cycle-parent-id>")
```

**Exclude nested trackers from the sweep, exactly as Step 6 does.** A nested tracker is itself
a child of a top-level parent, so a sweep that treats it as ordinary backlog work reparents the
tracker under the next-cycle stem and leaves the current cycle with no nested parent to hold
its sub-issues. Nested trackers are owned by Step 5 at every cycle boundary; match each child
against the nested stems in [`references/trackers.md`](references/trackers.md) and skip the
matches regardless of their state.

Then repeat the sweep over the **previous-cycle** trackers, with the same exclusion. A
previous-cycle parent closed by an earlier run can still hold backlog children; they surface
here and move to the next-cycle tracker. Archived children stay where they are.

Run the sweep after Step 6 so a sub-issue is not moved twice in one pass.

---

## Step 8: Close Out the Previous-Cycle Parents

For each previous-cycle parent, move it into the previous cycle and mark it Done:

```
save_issue(
  id: "<prev-cycle-parent-id>",
  state: "Done",
  cycle: "<previous cycle id>"
)
```

This removes it from the active cycle view.

**Parents flagged in Step 2 as `duplicate`, `canceled`, or archived keep their state.** Set
`cycle` only, and report the state in the summary — see
[`references/edge-cases.md`](references/edge-cases.md) → *Previous-cycle parent in a terminal
non-Done state*.

---

## Step 9: Confirm and Summarise

Print a structured summary:

```
✅ Weekly cycle rollover complete — Cycle <prev> → Cycle <curr>

**New issues created**
• <stem> [Cycle <curr>] — <PROD-XXXX> (if created)
• <stem> [Cycle <next>] — <PROD-XXXX> (if created)
• <nested stem> [Cycle <curr>] — <ID> (nested under <parent stem>, if created)
...

**Active sub-issues moved to Cycle <curr> parents** (<N> total)
• <stem>: <n> moved
  - <sub-issue-id>: <title>
  ...

**Backlog sub-issues moved to Cycle <next> parents** (<N> total)
• <stem>: <n> moved — from Cycle <curr> parent | from Cycle <prev> parent
  - <sub-issue-id>: <title>
  ...

**Previous-cycle parents closed** (Cycle <prev>)
• <PROD-XXXX> — <stem> [Cycle <prev>] → Done ✓
• <ID> — <nested stem> [Cycle <prev>] → Done ✓
...
```

Report a stem with nothing to move as "0 sub-issues to move" rather than omitting it, so the
summary covers all 11 stems and all 5 nested trackers.

Call out anything that needed a judgment call or was left undone:

```
⚠️ Could not find previous-cycle parent for "<stem> [Cycle <prev>]" — skipped sub-issue migration for this stem.
⚠️ Renamed <PROD-XXXX> "<stem> [Cycle <prev>]" → "[Cycle <curr>]" — title disagreed with its cycle and sub-issue membership.
⚠️ <PROD-XXXX> "<stem> [Cycle <prev>]" is <duplicate|canceled> — moved to Cycle <prev>, state left unchanged.
⚠️ No Engineering cycle titled "Cycle <N>" — created "Update Knowledge and Skills [Cycle <N>]" without a cycle; needs manual assignment.
```

---

## Edge Cases

See [`references/edge-cases.md`](references/edge-cases.md).
