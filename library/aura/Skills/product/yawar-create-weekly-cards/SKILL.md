---
name: yawar-create-weekly-cards
description: >
  Creates Yawar's ten standing Linear cards for the week in one pass, under the Product/Design
  nested trackers that product:setup-weekly-cycle has already rolled over: a Sunday Data
  Monitoring card, five Sunday-to-Thursday Experiment Monitoring cards, the Aura and Aurie
  "Update and report the Growth ROI sheet (week of Sun-Sat)" cards, and the "Viktor weekly
  review [Cycle N]" card. Skips any card whose title already exists, so it is idempotent and
  safe as an unattended Sunday or Monday routine; an interactive run shows the plan first.
  Trigger on "create my weekly cards", "create the weekly cards", "set up this
  week's cards", "make the monitoring cards for the week", "add the experiment monitoring
  cards", "create the growth ROI cards", "weekly Linear cards", "yawar weekly cards", or the
  routine's own invocation. Do NOT use to create the [Cycle N] parents themselves
  (product:setup-weekly-cycle), for ad-hoc cards (shared:add-linear-issues), or for the
  monthly engineering pass (eng:setup-monthly-tasks).
metadata:
  version: "1.2.0"
knowledge:
  reads:
    - Guide/Index    # baseline routing aid (universal)
    - Tools/Linear   # team, cycle and label conventions; MCP access
---

# Create Weekly Cards

Files the ten cards Yawar works from every week, so that Monday starts with the board already
laid out instead of ten hand-made issues. It writes only to Linear, only under three existing
parents, and only cards that are not there yet.

Access Linear through the Linear MCP server. Every step below is an MCP call, except the
date math, which is a bundled script.

**This skill runs unattended.** It is wired as a Sunday or Monday routine, so no one is there
to approve a plan. Two things make that safe: it never creates a parent, and it never
creates a card whose title already exists under its parent. A second run in the same week
reports ten skips and zero writes. When a person is running it, the plan is shown first
anyway (Step 4).

---

## Step 0: Read the roster

Read [`references/roster.md`](references/roster.md) before anything else. It holds the team,
assignee and label IDs, the three parent stems, and the ten cards with their priority,
estimate, project and description template. Every later step keys off it. Three of the
cards carry a description; the bodies are in
[`references/templates.md`](references/templates.md).

---

## Step 1: Resolve the week and the target cycle

Run the date script first. It is deterministic and owns every date that appears in a title:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/yawar-create-weekly-cards/scripts/resolve_week.py
```

It prints JSON with `target_monday`, `sunday_before`, `week_label`, `week_closing`,
`report_week_label`, `report_week_closing`, `report_prev_week_label`,
`monitoring_dates`, and a `cards` list in creation order with every title rendered except
the cycle number, which is still `[Cycle N]`. The rule for the Monday: Monday to Thursday
resolves to this week's Monday, Friday to Sunday to the coming one, so a Sunday-evening run
and a Monday-morning run agree.

Then fetch the Product/Design cycles and pick the one whose `startsAt` falls on
`target_monday`:

```
list_cycles(teamId: "<Product/Design team id>", type: "previous")
list_cycles(teamId: "<Product/Design team id>", type: "current")
list_cycles(teamId: "<Product/Design team id>", type: "next")
```

On a Sunday run the match is the **next** cycle; on a Monday run it is the **current** one.
Keep the cycle immediately before the target as the **previous cycle** for Step 3's Sunday
check: on a Sunday run that is `current`, on a Monday run it is `previous`.
`startsAt` is a UTC timestamp (08:00Z for a Pacific-midnight boundary), so compare the
calendar date of `startsAt` to `target_monday`, not the full timestamp. Match by date,
never by number: the two teams number their cycles independently, and titles have
disagreed with numbers before.

If neither cycle starts on `target_monday`, stop and report it; a week without a matching
cycle means the team's cycle schedule changed and the roster needs a human look.

Record the cycle `id` and `number`, then re-run the script with `--cycle <number>` so every
title, including `Viktor weekly review [Cycle N]` and the three parent titles, is final:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/yawar-create-weekly-cards/scripts/resolve_week.py --cycle 46
```

---

## Step 2: Find the three parents

All three parents live in the target cycle on Product/Design. Read the cycle's membership rather
than searching by text, so an absent parent is a fact and not a search miss:

```
list_issues(cycle: "<target cycle id>", team: "Product/Design", limit: 250,
            fields: ["id", "title", "statusType", "archivedAt", "parentId", "cycleId"])
```

**Page until `hasNextPage` is false.** An active cycle routinely exceeds one page, and a
truncated read turns an existing parent into a missing one.

Match each parent by title, case-insensitively: `Experiment/Data Monitoring [Cycle <N>]`,
`Sheet Update/Creation/Changes [Cycle <N>]` and `Data Investigation [Cycle <N>]`. All three
are children of `Data Analysis [Cycle <N>]`; that is a sanity check, not a requirement.

Also locate `Experiment/Data Monitoring [Cycle <N-1>]` in the previous cycle's membership,
the same way. It is read-only input for Step 3's Sunday check; if it is missing, note it and
carry on, since only the two Sunday cards lose a guard.

**If any of the three parents is missing, stop.** The weekly rollover has not run yet. Do not create
the parent: that is `product:setup-weekly-cycle`'s job, and a parent created here would be
missing the roster metadata and would duplicate the one the rollover creates minutes later.
Report which parent is missing and which cards it would have held, then end. In an
unattended run this is the expected outcome when the routine fires before the rollover; the
next run picks the cards up.

---

## Step 3: Inventory what already exists

Fetch the children of all three parents, paging each to exhaustion:

```
list_issues(parentId: "<Experiment/Data Monitoring id>", limit: 250,
            fields: ["id", "title", "statusType", "archivedAt"])
list_issues(parentId: "<Sheet Update/Creation/Changes id>", limit: 250,
            fields: ["id", "title", "statusType", "archivedAt"])
list_issues(parentId: "<Data Investigation id>", limit: 250,
            fields: ["id", "title", "statusType", "archivedAt"])
```

For each of the ten cards, look for a child of its parent whose title, after trimming and
case-folding, equals the rendered title **or starts with it followed by a space**. A match,
in any state, means **skip**: a card that was already created and finished counts, so does
one that was cancelled, and so does a hand-made variant with a suffix such as
`Experiment Monitoring 9/20 (Partial)`. The suffix records how the day went; it is still
that day's card, and filing a plain twin next to it double-counts the day.

**The two Sunday cards get a second look under the previous cycle's parent.** A Sunday card
made by hand on the Sunday itself lands under the cycle that is still current that evening,
so `Experiment Monitoring 9/20 (Partial)` sat under `Experiment/Data Monitoring [Cycle 44]`
even though 9/20 belongs to Cycle 45's week. Take the **previous cycle** exactly as Step 1
recorded it, the cycle immediately before the target: `current` on a Sunday run, `previous`
on a Monday run. Never read `list_cycles(type: "previous")` for this on a Sunday, since that
is two cycles back and the guard would miss the card. Use the `Experiment/Data Monitoring`
parent Step 2 located in that cycle, list its children, and apply the same match for the two
Sunday-dated titles only. A hit there is a skip too; do not move it.

Everything without a match is the **create** set.

---

## Step 4: Show the plan, and gate only when someone is there

Print the plan as one table, whether or not anyone will read it: the target Monday, the
cycle number and id, the three parent identifiers, and for each of the ten cards its title,
parent, priority, estimate, project, whether it carries a description, and `create` or
`skip (PROD-xxxx)`.

- **Interactive run** (a person invoked the skill in a session): wait for approval before
  Step 5. A misread week files ten cards against the wrong dates.
- **Unattended run** (a routine, a scheduled task, or any context where no one can answer):
  do not wait. The idempotency check in Step 3 is the guard. Say in the report that the gate
  was skipped and why.

If the create set is empty, skip Step 5 and report ten skips.

---

## Step 5: Create the missing cards, in roster order

Create **sequentially, in the roster's order**: `subIssueSortOrder` is not exposed by the
MCP, so creation order is what sets sub-issue order under each parent, and the roster order
is the order the week is worked in.

For each card in the create set:

```
save_issue(
  title: "<rendered title>",
  team: "Product/Design",
  parentId: "<its parent id>",
  cycle: "<target cycle id>",
  assignee: "<Yawar user id>",
  state: "Todo",
  labels: ["Analytics & Data"],
  priority: <0 | 2 | 3 per roster>,
  estimate: 5,
  project: "<AI Native id>",          // Viktor card only; omit otherwise
  description: "<filled template>"    // Growth ROI and Viktor cards only; omit otherwise
)
```

Fill the three descriptions from [`references/templates.md`](references/templates.md) using
`report_week_label`, `report_week_closing`, `report_prev_week_label` and the cycle number.
Those are the **prior** week — the Growth ROI cards report the week that just closed, not the
one the cycle opens. Do not fill them from `week_label`/`week_closing`, which are the cycle's
own week and belong only to the monitoring cards. Pass the body with real newlines. The six monitoring cards carry no
description; do not add one.

Omit `project` on the nine cards that have none, and pass `priority: 0` explicitly on the
two Growth ROI cards rather than leaving it unset, so a later edit to the roster's default
cannot leak in.

Keep every create to one call and read the response: `id`, `title`, `parentId`, `cycleId`.
If a create fails, continue with the rest and list the failure in the report; a partial week
is recoverable by re-running, a silent stop is not.

---

## Step 6: Verify, then report

Re-read all three parents' children and confirm, per card in the create set: it exists once,
under the right parent, in the target cycle, assigned, with the label, priority and estimate
from the roster, and with a description only where the roster says so. A card that appears
twice under its parent means a race with a hand-made card; report it, do not delete either.

Then report, briefly:

```
Weekly cards for Cycle <N> (Mon <target_monday>)

Created (<n>)
- PROD-xxxx  Data Monitoring 9/27
- PROD-xxxx  Experiment Monitoring 9/27
...

Skipped, already present (<n>)
- PROD-xxxx  Viktor weekly review [Cycle 46]

Parents: Experiment/Data Monitoring PROD-xxxx, Sheet Update/Creation/Changes PROD-xxxx, Data Investigation PROD-xxxx
Gate: skipped (unattended run) | approved by user
```

Include every one of the ten cards in one of the two lists, so a reader can see the week is
complete without opening Linear. If Step 2 stopped early, the report is the missing parent
and the cards it would have held.

---

## Edge cases

- **Routine fires before the rollover.** Step 2 stops on the missing parent and reports;
  nothing is written. Re-run after `product:setup-weekly-cycle`, or let the next routine
  run pick it up.
- **Run on a Friday or Saturday.** The script resolves to the coming Monday, so the cards
  are filed for next week, and Step 2 will usually stop because next week's parents do not
  exist yet. That is correct; nothing about the current week is touched.
- **A hand-made card with a suffix.** `Experiment Monitoring 9/20 (Partial)` counts as
  `Experiment Monitoring 9/20` and blocks it, under either the target or the previous
  cycle's parent (first run, 2026-09-22: PROD-6013 under the Cycle 44 parent). Report it as
  a skip with its identifier; do not rename, move or cancel it.
- **Two Sunday cards on the same date.** By design: `Data Monitoring <Sun>` and
  `Experiment Monitoring <Sun>` are different jobs on the same day.
- **A parent exists but is not under `Data Analysis [Cycle N]`.** Use it anyway and note it
  in the report; the rollover owns the parent's placement.
- **Cycle number differs from the cycle title.** Use the `number` field for `[Cycle N]`,
  since that is what `setup-weekly-cycle` uses for the parent titles the children must match.
