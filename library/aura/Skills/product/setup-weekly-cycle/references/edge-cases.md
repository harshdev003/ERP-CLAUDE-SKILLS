# Edge cases

Conditions the rollover hits in practice, and the resolution for each. Every one of these
ends with something surfaced in the Step 9 summary — never a silent skip.

---

## Missing or unmatched trackers

**Previous-cycle parent not found for a stem.** Skip sub-issue migration for that stem and
note it in the summary. Continue with the remaining stems — the current- and next-cycle
parents are still created.

**Current- or next-cycle parent already exists.** Skip creation for that stem and use the
existing issue as the migration target.

**No incomplete sub-issues for a stem.** Report "0 sub-issues to move" in the summary. The
parent creation and close-out still run.

**Previous-cycle nested tracker not found.** Nothing to migrate or close for that nested
stem. Still create its current- and next-cycle trackers, skip its migration and close-out,
and note the miss. This is the expected path on the first run after a nested tracker is
introduced.

**Parent stem missing when rolling a nested tracker.** The direct search still finds the
previous-cycle nested tracker, but there is no parent to attach the new one to. Create it
unparented, note it in the summary, and let the user re-parent.

**Inconsistent titles in a past week.** The previous-cycle number from `list_cycles` is the
source of truth for the `[Cycle N]` suffix. A tracker titled inconsistently in a past week
does not match and is reported as not found.

---

## Title disagrees with cycle membership

A tracker whose title says `[Cycle N-1]` while its `cycle` field is Cycle N, and whose live
sub-issues also sit in Cycle N, is a **mis-titled Cycle N tracker** — the title carries a
typo from the run that created it. The cycle field and sub-issue membership are the
authority, not the title.

**Rename it to `[Cycle N]` and treat it as the current-cycle tracker for that stem.** Do
not create a second current-cycle tracker; that splits one stem's work across two parents.

Confirm before renaming that the stem's genuine `[Cycle N-1]` tracker exists separately and
is already closed — two open trackers with the same title and different cycles is a
different problem, and needs the user to say which one survives.

---

## Previous-cycle parent in a terminal non-Done state

A previous-cycle parent whose `statusType` is `duplicate` or `canceled` is already closed
out, and its state carries information. **Leave the state alone** — forcing it to `Done`
misrepresents it. Move its `cycle` to the previous cycle if it is still sitting in the
active cycle, and note the state in the summary.

An **archived** previous-cycle parent has nothing to migrate and no close-out to perform.
Note it and move on. Its sub-issues are archived with it and are out of scope.

---

## Metadata for stems whose roster row is thin

Several roster rows carry no project and no priority. When creating a parent for one of
those stems, read the previous-cycle tracker with `get_issue` and mirror its actual
`project`, `labels`, `priority`, and `assignee` rather than substituting a default. The
roster table records the intended shape; the live issue is the authority on what the team
is actually using.

**Do not carry `projectMilestone` forward.** A long-lived tracker often points at a closed
milestone, and copying that forward files new work under a stale milestone. Leave milestone
unset on created trackers and let the team set it.

---

## Sub-issues on other teams

Sub-issues routinely live on Backend, Mobile, Web, Engineering, Data/DS, and Prompt while
their tracker parent lives on Product/Design. Re-parenting across teams works normally and
these are in scope for migration exactly like same-team children. Do not filter them out.

---

## Backlog items stranded under a closed parent

A previous-cycle parent closed by an earlier run can still hold `backlog` children. Step 7
sweeps previous-cycle parents as well as current-cycle ones for this reason — the stranded
items surface there and move to the next-cycle parent.

An **archived** backlog child stays where it is; archived issues are always out of scope.

---

## Cycle resolution

**No Engineering cycle matches the Product/Design cycle title.** Create or close
`Update Knowledge and Skills` without setting `cycle`, and surface it in the summary as
needing a manual cycle assignment. Never fall back to matching on cycle `number` —
Engineering and Product/Design numbering diverge.

---

## Wrong team

This skill is hardcoded for the Product/Design team. If the user asks to run it for a
different team, ask for the target team name and resolve its cycle IDs and issue stems at
runtime instead of using the roster in
[`references/trackers.md`](trackers.md).
