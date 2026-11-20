---
name: mixpanel-event-approval
description: >
  Audit Mixpanel events still marked `hidden: true` in the Aura Lexicon, drop ones with zero
  30-day volume, cross-reference each survivor with its design ticket in Linear, and
  selectively un-hide them with a Slack announcement. Renders a table (Event | 30d Volume |
  Linear Ticket | Description), then an interactive checklist of every candidate — ticketed
  rows pre-checked — whose selection drives which events get un-hidden. Applies the change via
  Mixpanel Bulk-Edit-Events, posts only after explicit confirmation, then records the run
  as a Linear card under the current cycle. Flags but does not
  auto-fix names that drift from Aura conventions. Trigger on "mixpanel event approval",
  "audit hidden mixpanel events", "unhide mixpanel events", "which events are hidden in
  mixpanel", "check hidden events with volume", "clean up hidden lexicon events", "promote
  hidden events to visible", or "approve pending mixpanel events". Do NOT use for syncing
  descriptions into AI Knowledge (`data:sync-mixpanel-events`).

metadata:
  version: "3.8.0"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Tools/Mixpanel               # project_id, MCP usage, event taxonomy
    - Tools/Linear                 # list_issues/get_issue for design-ticket lookup
    - Tools/Slack                  # canonical channel ID for the announcement (posted via shared:post-to-slack)
    - Products/Aura           # brand voice for the Slack announcement
---

# mixpanel-event-approval

Promote ready-to-ship Mixpanel events out of `hidden` so the Aura Lexicon
reflects what the apps are actually firing — without rubber-stamping events
that nobody has wired up yet, and documenting what does get promoted.

## Goal

Take the set of `hidden: true` events in Project Aura, narrow it to the
ones with **real 30-day volume** and a matching Linear design ticket, let
the user pick which to un-hide via an interactive checklist widget, write
the change to Mixpanel via `Bulk-Edit-Events`, announce the result in
Slack channel `C09PECWGKD3`, and record the run as a Linear card so the next
audit can read what was promoted and what was held back.

## Inputs

- No required arguments; everything resolves from the Mixpanel + Linear MCPs.
- The user's checklist-widget selection (or a free-text list of event names)
  drives which events to un-hide.

## Constants

| Constant            | Value                                                        |
|---------------------|--------------------------------------------------------------|
| `project_id`        | `963821` (Project Aura)                                      |
| `volume_window`     | last 30 days                                                 |
| `volume_threshold`  | `> 0`                                                        |
| `slack_channel_id`  | `C09PECWGKD3` (`#…` — see Slack)                             |

## Workflow

Run every step inline with direct tool calls, requesting only the `fields` you need
from `list_issues`. Spawn no subagent (`Agent`) unless a step is impossible without one.

### 1. Pull hidden events from Mixpanel

```
Get-Events(project_id=963821, hidden=true)
```

If the response is empty, tell the user "No hidden events in Project Aura
right now." and exit. No prompt, no Slack post.

### 2. Compute 30-day volume per event

Use `Run-Query` with an Insights total over the last 30 days: **≤20 events per query**
(one `total` metric each) to avoid payload errors, broken down by the `Internal` user
property (`{"metric": {"type": "property", "propertyName": "Internal", "resource":
"user", "propertyType": "boolean"}}`). One pass returns both the volume (sum of an
event's buckets) and the split the external-traffic gate below reads.

Zero-volume rows are dropped from the Insights table response, so an event
absent from a **successfully returned** batch has zero volume — drop it.
Treat absence as zero *only* for events whose batch came back cleanly; a
failed or incomplete batch also leaves names absent, so retry it (see edge
cases) before concluding anything about its events. A failed query must
never silently drop real events. Keep the surviving non-zero events as the
candidate set.

If every event drops out, tell the user "All hidden events have zero
30-day volume — nothing to promote." and exit.

**Then gate on external traffic** from the same buckets: `true` is internal, `false`
and `undefined` are external. Before a verdict, apply [`references/live-traffic.md`](references/live-traffic.md):
unique users and a daily series for small counts, the parent-event ratio for error
events, journey timing, and the per-build split for app events. A candidate with no
real external traffic is held like a superseded one (step 7), with the reason on its
row. Re-measure every run; the verdict is dated.

### 3. Find the Linear design ticket per surviving event

A **design ticket** specifies the event: an event line naming it exactly, with a
firing condition. A card that only mentions the name (an acceptance criterion, a QA,
trigger or bug-fix card) is a mention, not a design ticket.

For each candidate, call `list_issues(query=<event name>)` and read each plausible
match with `get_issue` for that spec line. A card counts only when neither it nor its
parent is `Canceled` — check both statuses; a cancelled design never shipped as written.

- One card specifies it → record `identifier`, `url`, and a 1–2 sentence
  description distilled from its spec line.
- Several do → pick the **most recent completed**; note the others ("Also
  specified in AURA-1234").
- None does → record `No design ticket`, naming any mention-only cards. Do **not**
  invent a ticket. The exception is a data-design card whose only trace of the
  name is a cross-reference ("Same properties as `<name>`"): it may have renamed
  the event in place, so carry it into step 4 as the ticket for the 4.2 check.

### 4. Check for later changes, deprecations and renames

The step 2–3 gate passes events that a **later** card explicitly renamed,
replaced or deleted. The change is invisible to that gate: the event still
has 30-day volume, because it accrued before the change shipped, and it still
has a ticket — the one that created it.

For each candidate that has a ticket:

1. **Enumerate the feature's other cards.** Resolve the design ticket's **own**
   parent and list all of its children, `Canceled` ones and build cards included:

   ```
   get_issue(id=<ticket>)             → parentId
   list_issues(parentId=<parentId>)   → siblings
   ```

   **Page every `list_issues` call in this step to exhaustion** — follow
   `cursor` until `hasNextPage` is false. A truncated first page hides the
   very rename card this step exists to find, and the miss is silent: the
   dead spelling reads as live and gets pre-checked.

   Read every sibling that completed **after** the original data-design card,
   every `Canceled` sibling (a cancelled draft design is what a review renames
   from) and every card that built the events. A rewrite is named after the
   feature, not the event it changed, so `list_issues(query=<event name>)` will
   not surface it; enumeration is reliable, an empty `query=` proves nothing.

2. **Read the previous run's own card.** `list_issues(query="Mixpanel event
   approval")`, then read the most recent. Every run records what it held
   back and why (step 14), which makes this the cheapest supersession check
   available. Treat those entries as **dated evidence to re-verify**, not as
   standing verdicts — a hold placed because a feature had not yet shipped
   expires once it does.

   Then re-read the **current** spec of every design ticket that card cites,
   and of every card step 3 carried in on a cross-reference. A data-design
   card edited in place renames an event without leaving a sibling card for
   4.1 to find: its event line spells the event differently from the
   candidate, often beside a leftover cross-reference such as "Same
   properties as `<old name>`". Record the old name as `superseded by <that
   card> → <new spelling>`.

3. **Confirm the successor exists.** `Get-Events(project_id=963821,
   query=<successor prefix>)` returning the new spelling is proof the rename
   shipped. Returning nothing means it has not, which is its own reason to
   leave the old name hidden.

4. **Read a daily series, not the window total.** A removed event keeps its
   30-day total for up to 30 days after it stops firing. Re-query the family by
   day — and, for app events, by `$app_version_string` — and check it still
   fires this week and on the latest builds.

Record a verdict per event — `live`, or `superseded by <card> → <successor>`.
Superseded events are never promoted: they get their own widget group in step
7 and stay unchecked, with the reason shown in the step 6 table.

### 5. Naming-convention guardrail (flag-only)

Aura conventions:
- **Events:** `AI [Object] [Action]` in Title Case (e.g. `AI Coach Reply
  Sent`).
- **Properties:** Title Case with spaces (e.g. `Reply Latency Ms`).

For each candidate event, check the event name. If it diverges (e.g.
snake_case, missing `AI` prefix, lower case), **flag** it in the
description column as `⚠ naming drift` — do **not** rename. Renames are
their own workflow (and would break dashboards). Same for any property
names that appear in the Slack draft later: surface drift, don't fix.

See `anthropic-skills:aura-instrumentation` for the canonical naming
rules if the user asks why something was flagged.

### 6. Render the candidate table

Output a single markdown table in chat, sorted by `30d Volume` descending:

| Event Name | 30d Volume | Linear Ticket | Description |
|---|---|---|---|
| `AI Coach Reply Sent` | 12,430 | [AURA-1234](https://linear.app/...) | Coach finishes a reply turn in the AI coaching loop. |
| `AI Memory Compaction Run` | 412 | _No design ticket_ | ⚠ naming drift — mentioned in AURA-1301, no spec |

This is **the plan**. Do not write anything to Mixpanel yet.

### 7. Render the selection checklist widget

Render an interactive checklist widget covering **every** candidate event
via the visualization tool — call `mcp__visualize__read_me` (module
`interactive`) once before the first `mcp__visualize__show_widget` call.
This widget, not `AskUserQuestion`, is how the user picks what to un-hide.

Widget shape:

- **One checkbox row per candidate event**, showing the event name in
  monospace, its 30d volume, and the Linear ticket badge where one was
  found.
- **Grouped into four sections** (assign each candidate to exactly one,
  by this precedence so the `REC` / `SYS` / `SUP` / `LEG` arrays never
  overlap):
  1. **Mixpanel system defaults** — any `$`-prefixed name (`$ae_*`,
     `$campaign_link_click`, etc.). Checked **first**, so a system default
     stays here (unchecked, "recommend leaving hidden") even if it happens
     to have a Linear ticket.
  2. **Superseded / changed / not live** — anything step 4 marked superseded, or
     step 2 found without real external traffic. Unchecked, with the reason (the
     superseding card and successor, or the traffic finding) on the row. These carry
     a ticket, so without this group the ticketed precedence below would pre-check them.
  3. **Has a design ticket** — remaining events with a ticket; pre-checked
     (the safe promotion set).
  4. **Legacy / no ticket** — everything else; unchecked.
- **Helper buttons:** "Select ticketed only" (checks just the **ticketed**
  rows — the `REC` array, i.e. the "Has a design ticket" section, never the
  system-defaults section), "Clear all", and a live "Selected: N" count.
- **Submit button** ("Send my selection to Claude") calls `sendPrompt()`
  with the exact list of checked event names, one per line, each prefixed
  with `- `, e.g.:

  ```
  Un-hide exactly these hidden Mixpanel events (validate against the
  candidate table first):
  - AI Usage Limit Modal Seen
  - AI Cancel Monthly Downgrade Seen
  ```

Build the widget body in JS from the group arrays so rows stay consistent — the
proven pattern is in [`references/widget.md`](references/widget.md).

Keep system-default, superseded and legacy rows unchecked so the safe default
(submit straight away) promotes only the live ticketed set. **Keep-hidden is implicit:**
if the user doesn't submit, or tells you to stop / keep them hidden, exit
cleanly with a one-line confirmation and no writes.

The widget can't be edited once rendered, and selections aren't persisted —
if the user later asks for "the events still not approved," re-render the
widget scoped to whatever remains `hidden: true` (re-run steps 1–6 first so
volumes, tickets and supersession verdicts are current).

### 8. Validate the returned selection

When the selection comes back (from the widget's `sendPrompt`, or typed
free-text), parse the event names and validate each against the rendered
candidate table. First **strip the leading `- ` list prefix and surrounding
whitespace** from each line — the widget emits one `- `-prefixed name per
line, and an event name itself never contains that prefix. Then match each
cleaned name against the table. If any name doesn't match exactly,
**re-prompt with the unmatched names listed** rather than silently dropping
them. Loop until the parsed set is a clean subset of the table.

If the returned set is empty, treat it as keep-hidden: exit cleanly with no writes.

### 9. Apply the un-hide via `Bulk-Edit-Events`

Even for a single event, route through `Bulk-Edit-Events` so the
operation is uniform and idempotent (max 50 events per call — chunk if the
selection is larger):

```
Bulk-Edit-Events(
  project_id=963821,
  hidden=false,
  events=[{"name": "AI Coach Reply Sent"}, {"name": "..."}, ...]
)
```

If the call fails for a subset, fall back to per-event `Edit-Event` for
the failures and report which succeeded and which didn't. Do not retry
silently — the user needs to see partial failure.

### 10. Draft the Slack announcement

Build a Slack message scoped to the events that actually got un-hidden,
grouping rows by their Linear ticket (and a final "no design ticket —
promoted on volume only" group). Header copy follows Aura brand voice —
warm, concise, no hype. One short sentence of context, then the grouped
list.

Suggested header — a template; include only the clauses that match the
actual selection. Every promoted event has real volume, but only claim
Linear-ticket backing for events that actually have a ticket:

> Promoted N Mixpanel events from hidden → visible — all with real 30-day
> volume. *(if any are ticketed)* The ticketed events below are wired up in
> code per their Linear design tickets. *(if any are volume-only)* The rest
> are promoted on volume alone.

If the whole selection is volume-only, drop the ticket clause entirely; if
it's all ticketed, drop the "promoted on volume alone" clause.

Render Linear references as Slack hyperlinks. Include the 1–2-sentence
description per row. Keep any `⚠ naming drift` flags so the team sees them —
but state clearly that this skill did not rename anything.

### 11. Confirm the announcement, the descriptions and the run card

Show the user three things together, and take one confirmation for all of
them:

1. The drafted Slack message, verbatim.
2. The description backfill step 13 will write — derive it per step 13 and
   show it as a `Resource | Kind | Description to write` table, so every
   string is read before it reaches the Lexicon.
3. The run card step 14 will write — title, parent, cycle, label, assignee
   and state.

All three are side effects outside this session, and this is the only gate
in front of them. Write all three out in full in the message itself, **before** the
confirmation prompt: a prompt or next-step box ends the turn, so a draft placed after
it — or a recap that only refers to the drafts — never reaches the user.

Require explicit confirmation ("post it", "yes", "send") before any Slack,
Mixpanel-metadata or Linear call. Anything else — including silence, edits,
or "let me look again" — means **do not post, do not write descriptions and
do not write the card**. If the user requests edits, apply them and
re-confirm.

### 12. Post to Slack

On confirmation, **invoke [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** with the drafted message, channel `C09PECWGKD3`, and `--footer data:mixpanel-event-approval` (it stamps the versioned footer; don't hand-type one); that skill owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback) and the unfurl-off default — don't call Slack directly here.

Report the Slack permalink back to the user.

### 13. Backfill missing event and property descriptions

A promoted event is only useful in the Lexicon if a reader can tell what it
means. Fill the gaps for the events just promoted, on the step 11
confirmation — or on the narrower per-write confirmation that the declined-
step-11 case in [`references/edge-cases.md`](references/edge-cases.md)
defines. Never on neither.

**Gap-fill only, never overwrite.** Read the current state first and write
only where the description is empty. An existing description was written by
someone with context this run does not have.

```
Get-Events(project_id=963821, event_names=[<promoted>], include_details=true)
List-Properties(project_id=963821, resource_type="Event",
                events=[<promoted>], attributes=["description"])
```

**Source the text from the design ticket, not from the event name.** Use the
1-2 sentence description distilled in step 3.

**Write that sentence and nothing else.** The step 6 Description column is a
working column: alongside the sentence it carries `⚠ naming drift` flags,
`Also referenced in …` sibling citations and supersession notes. Those
annotate this audit, they do not document the event. Strip them and write the
plain sentence — and note that the backfill never overwrites, so whatever
lands here is what the Lexicon says about that event from then on.

An event promoted on volume alone has no authoritative source: leave its
description empty and say so, rather than paraphrasing the name back into
prose.

Write in chunks of ≤50:

```
Bulk-Edit-Events(project_id=963821,
  events=[{"name": "...", "description": "..."}, ...])

Bulk-Edit-Properties(project_id=963821, resource_type="Event",
  properties=[{"name": "...", "description": "..."}, ...])
```

Pass only `name` and `description` on the event entries. `Bulk-Edit-Events`
also takes uniform fields such as `hidden`; including one here would re-apply
it to every event in the call.

**Properties take their text from the same ticket's property spec.** Aura data
design cards document each event's properties inline — name, type and an
example value. Use that wording. A property the ticket does not document has
no source, so leave it empty exactly as for an unsourced event; never derive a
property description from the event it happened to appear on.

**Properties are project-scoped, not per-event.** `Sent From` is one Lexicon
row shared by every event that carries it, so a description written here
shows up everywhere that property appears. Write what is true of the property
across all its events, never what it means on the one event being promoted —
and check the current value, because a property new to this event is often
long-documented elsewhere. This is why an invented property description is
worse than an absent one: it propagates.

**Flag existing descriptions the promoted event contradicts.** Gap-fill never
overwrites, so a property whose current description no longer fits how a promoted
event uses it (a login-provider description on a `Provider` that carries `apple` /
`stripe`, say) stays wrong. List each one on the run card for a human to fix.

Do **not** reach for `Fill-Event-Metadata`. It is gap-fill only and so looks
like the right tool, but it runs across the **whole project** and would write
AI-generated metadata to every under-documented event in the Lexicon,
including events this run never examined.

Report what was filled and what was left empty for lack of a source.

### 14. Record the run as a Linear card

Write a card so the next run can read what was promoted and what was held
back — step 4.2 depends on this card existing. This write is covered by the
step 11 confirmation, or by the per-write confirmation in
[`references/edge-cases.md`](references/edge-cases.md) when step 11 was
declined. Never call `save_issue` without one of the two.

Resolve the cycle and the parent first. The parent is the `Data Investigation
[Cycle N]` card belonging to the Product/Design team's **active** cycle:

```
list_cycles(teamId="Product/Design", type="current")   → the active cycle
list_issues(query="Data Investigation", team="Product/Design")
```

Page the `list_issues` call to exhaustion as in step 4 — the current cycle's
parent is often not on the first page. Pick the `Data Investigation [Cycle N]`
issue whose `cycleId` matches the active cycle. Then:

```
save_issue(
  team="Product/Design",
  title="Mixpanel event approval",
  description=<the Slack body, plus the permalink>,
  parentId="<the Data Investigation [Cycle N] identifier>",
  cycle="<the active cycle id from list_cycles>",
  addLabels=["Analytics & Data"],
  assignee="me",
  state="Done",
)
```

- **Description** — the same content posted to Slack, as Markdown, with the
  step 12 permalink on its own line. Add the held-back events and the reason
  for each; that section is what step 4.2 reads on the next run.
- **Cycle** — set it explicitly. Parenting does not place a card in a cycle,
  so a card that omits `cycle` lands outside the board even when its parent
  is correct. The field takes a cycle name, number or id; pass the `id`
  `list_cycles` already returned, which is unambiguous across teams.
- **Assignee** — `"me"`, the operator running the skill.
- **State** — `Done`. The work is complete by the time the card is written.

Report the card identifier and URL back to the user.

### 15. Distill the session

Invoke [`shared:distill-session`](../../../shared/skills/distill-session/SKILL.md).
Route anything learned about Mixpanel query shapes, Linear or Slack behaviour,
or the Lexicon itself to AI Knowledge through that skill.

When a learning changes **this workflow** rather than the surrounding facts,
update this skill through
[`shared:update-aura-skill`](../../../shared/skills/update-aura-skill/SKILL.md)
in the same pass. A check that lives only in a Notion page is a check the next
run will not perform.

## Output

- A markdown candidate table (step 6).
- The selection checklist widget (step 7).
- The user's promotion decision and any write results.
- The Slack permalink if a post happened.
- The descriptions backfilled into the Lexicon (step 13).
- The Linear card recording the run (step 14).

## Edge cases

Every handled case — empty inputs, failed batches, ambiguous Linear matches,
superseded events, partial write failures and a declined confirmation — and
what a re-run does are in [`references/edge-cases.md`](references/edge-cases.md).

## Required MCP tools

- **Mixpanel:** `Get-Events`, `Run-Query`, `Bulk-Edit-Events`,
  `List-Properties`, `Bulk-Edit-Properties`, `Edit-Event` /
  `Edit-Property` (fallback only).
- **Linear:** `list_issues`, `get_issue`, `list_cycles`, `save_issue`.
- **Slack:** posting is delegated to [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md) (it owns the connection — MCP-first, `SLACK_BOT_TOKEN` script fallback); this skill never calls a Slack primitive directly.
- **Visualization:** `mcp__visualize__read_me`,
  `mcp__visualize__show_widget` (the selection checklist).
- **Skills:** [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md) (step 12),
  [`shared:distill-session`](../../../shared/skills/distill-session/SKILL.md) (step 15).

## References

- Mixpanel MCP usage patterns: `plugins/data/skills/sync-mixpanel-events/SKILL.md`.
- Confirm → write flow: `plugins/eng/skills/review-pr/SKILL.md`.
- Aura/Aurie event naming rules: `anthropic-skills:aura-instrumentation`.
