---
name: mixpanel-data-design
description: >
  Design or audit Mixpanel events, properties and instrumentation specs for Aura (consumer
  wellness) or Aurie (AI emotion processing). Default output is a Linear-card-ready spec — a
  nested checklist per event with type and example per property; also supports audit tables,
  gap analysis and Notion specs. Loads the canonical rule set from `Tools/Mixpanel/Data
  Design` in AI Knowledge on every invocation so it stays in sync with humans editing the doc.
  Always confirms product before designing — the two share a Mixpanel project but use
  different event names, and getting it wrong silently returns zero rows. Trigger on "design
  events for [feature]", "design Mixpanel instrumentation", "write a Linear card for tracking
  [thing]", "spec instrumentation for [screen/flow]", "audit these events", "audit our
  Mixpanel naming", "gap analysis for [screen]", "what events are missing from [flow]",
  "review this instrumentation", "Mixpanel data design", or a dropped feature description or
  Linear URL asking for tracking.

metadata:
  version: "3.4.0"
knowledge:
  reads:
    - Guide/Index                       # baseline routing aid (universal)
    - Tools/Mixpanel/Data Design        # canonical rules — the whole point
    - Tools/Mixpanel                    # companion (IDs, access, quirks)
    - Tools/Linear                      # Step 0.5 pulls card context via Linear MCP
    - Compliance/Privacy                # PII rules applied to property design
    - Products/Aurie               # Aurie product context
    - Products/Aura                # Aura product context
---

# mixpanel-data-design

Design and audit Mixpanel instrumentation for Aura and Aurie. Knowledge-backed
— the rules live in `Tools/Mixpanel/Data Design` in AI Knowledge, not in this
SKILL.md. Update the Notion page once and every future skill run picks it up.

## When to use

Trigger when the user asks any of:

- "Design events for the [feature/flow/screen]"
- "Write a Linear card for tracking [thing]"
- "Spec Mixpanel instrumentation for [feature]"
- "Audit these events: [list]" or "audit our [X] events for naming issues"
- "Gap analysis for the [screen] flow" / "what's missing from [screen]"
- "Review this instrumentation" / "review my event spec"
- "Mixpanel data design for [Aurie/Aura] [feature]"
- Or pastes a feature description / screen mock / PRD and wants tracking
- Drops a Linear issue link or ID (e.g. `ABC-123` or a `linear.app/...` URL)
  and asks for tracking / a data-design spec

Also trigger when the user is writing a Linear ticket and mentions wanting to
include Mixpanel events in the description.

## Step 0 — Load canonical rules

Before doing anything else, retrieve the canonical data-design page:

```
retrieve-knowledge path="Tools/Mixpanel/Data Design"
```

This is the source of truth. Naming conventions, backend-vs-frontend
detection, `Source App` filtering matrix, `user[]` vs `properties[]` API
trap, rate limits, PII rules, denominator rules, Linear-card output format —
all live there. **Do not duplicate the rules in this SKILL.md.** If the page
seems out of date for what the user is asking about, flag it for the user
rather than fabricating an answer.

Also retrieve `Compliance/Privacy` (already in `knowledge.reads`) — needed
for the "no raw user input in property values" rule, especially for Aurie
where chat content can contain mental-health text.

## Step 0.5 — Fetch Linear context (if a Linear card was referenced)

Users frequently invoke this skill while working on a Linear ticket that
already has parent/sibling cards, comments with clarifications, and Figma
links. Pull all of that before designing — don't make the user paste it
manually.

**Detect.** Scan the user's message (and recent conversation) for either:

- A Linear issue ID like `ABC-123`, `ENG-498`, `AUR-1042`
- A full Linear URL: `https://linear.app/<org>/issue/<ID>/...`

If neither is present, **skip this step silently** and go to Step 1.

**Fetch.** When a reference is found, use the Linear MCP:

1. `linear:get_issue` on the referenced card — read title, description,
   labels, state, assignee, project
2. If `parent` is set on the issue, `linear:get_issue` on the parent
   (recurse **one level up only** — do not walk further; sibling cards
   are out of scope unless the user names them)
3. `linear:list_comments` on the main card — read all comments for
   clarifications, scope changes, or links the description doesn't have
4. `linear:list_issues` with `parentId` = the main card, then
   `linear:get_issue` on the engineering, integration and QA sub-issues.
   The decided flow is usually recorded there — as-built behaviour,
   backend field names, QA scenarios per entry point — not on the parent.
   Where a sub-issue and Figma disagree, the sub-issue describes what
   shipped; the spec follows it and notes the delta.

**Get the proper Figma files.** The dev-ready screens sit in the design
sub-issue's section on the Experiments page, named
`PROD NNNN - Design - <feature>` with a Ready-for-Dev status instance on
top. A Figma link on the engineering card can point at an exploration
board instead; confirm the section before reading anything from it.
Then:

- `get_metadata` fails on large sections. Run a read-only `use_figma`
  script that walks the section's `children` and returns type, id, name
  and size per frame (depth 2).
- `get_screenshot` each frame by id (`enableBase64Response: false`),
  download every short-lived URL with `curl -sL` into the scratchpad
  under ordered filenames (`01_…`, `02_…`), and send them with
  `SendUserFile` in flow order, one call per entry point, with a caption
  that maps the numbers to steps.
- Without a Figma MCP, `WebFetch` the URL — public links return frame
  metadata; org-restricted ones return 403, in which case ask the user to
  paste the relevant screen names.

**Walk the flow before designing.** Write out the decided flow screen by
screen per entry point (tab, upsell, in-product card, banner…), including
behaviour that is not on the Figma board (gates, once-per-account rules,
what happens with and without a saved card). Deliver it together with the
ordered screenshots. Event design decisions are made against this walk,
so do not draft events until the user has confirmed it.

**Trace hand-offs in code.** When the flow crosses surfaces — an app
paywall handing off to a web checkout and back, a deeplink, an embedded
WebView — read the hand-off code in every repo involved and record what
fires at each step, with its properties, before drafting. Mixpanel alone
cannot say which code path emitted an event: a breakdown that looks like
the web page's purchases can belong to StoreKit. Add a "what fires today"
column to the flow walk so gaps and duplicates are visible before any
event is named.

**Summarize.** Before moving on, post a brief consolidated picture back to
the user so they can correct misunderstandings cheaply:

> **Linear context loaded.** Main card: `<ID> — <title>` (state, assignee).
> Parent: `<parent ID> — <parent title>` (if any).
> What I read: <2–3 line gist of what the feature is and which screens
> are in scope>.
> Flow as decided: <per entry point, the screen sequence and the rules
> around it — sent with the ordered screenshots>.
> Figma screens identified: `<Screen A>`, `<Screen B>`, …
> Already specced in parent / earlier comments: `<event names>` (if any).
> Net-new for this card: <what the user appears to be designing now>.
>
> Anything I'm missing before I design events?

Wait for either a correction or a "looks good" before continuing. If the
user is in a hurry and tells you to skip the summary, honor that and go
straight to Step 1.

## Step 1 — Confirm product

If the user did not explicitly say Aura or Aurie, **stop and ask**:

> Is this for **Aura** (core wellness / sleep / meditation app) or **Aurie**
> (AI emotion processing companion)?

Do not proceed until confirmed. Aura and Aurie share the Mixpanel project
(`963821`) but use completely different event names — using Aurie names for
an Aura experiment (or vice versa) silently returns zero rows. The
product-name table in `Tools/Mixpanel/Data Design` §1 shows the parallel
event names for the same metrics.

## Step 2 — Determine mode

Pick the output mode based on the user's ask. If ambiguous, ask.

| User says… | Mode |
|---|---|
| "Design events for X", "spec instrumentation", "Linear card for tracking" | `linear` (default for new specs) |
| "Audit these events", "review this naming", "are these events OK" | `audit` |
| "Gap analysis for X", "what's missing from X", "what events does X need that we don't have" | `gap` |
| "Draft a Notion page documenting these events", "spec for the wiki" | `notion` |

## Step 3 — Check the Mixpanel Lexicon

The Lexicon is the live source of truth for what events and properties
exist — many already carry descriptions and display names. Always query it
before inventing new names, claiming something is missing, or writing a
description from scratch. Project ID: `963821`. Browse:
`https://mixpanel.com/project/963821/view/30768/app/lexicon/transformations/events`.

For modes `linear` / `gap` / `notion` (designing new events/properties):

- `Mixpanel:Get-Events` with `query="<keyword>"` and
  `include_details=true` — surface similar existing events and read their
  descriptions before duplicating. If a close match exists, reuse it.
- `Mixpanel:List-Properties` with
  `attributes=["description","display_name","example_value"]` — confirm
  a standard property fits, and crib its existing description/example
  rather than inventing a new shape.
- `Mixpanel:Get-Lexicon-URL` — deep-link to a specific event/property so
  a human can edit display name or description after the spec lands.

**Confirm an event is live before reusing it.** Lexicon presence and a
30-day total are not liveness. When an event you intend to reuse has low
volume, or lower than its surface implies, run two `Mixpanel:Run-Query`
insights reports on the event: total fires by month over 12 months, and
total fires over the same window broken down by the `$app_version_string`
event property. An event whose fires sit on a narrow band of past builds
is deprecated; spec the replacement instead of reviving it.

For `audit` mode: pull the in-scope events from the Lexicon
(`include_details=true`) and compare descriptions to actual fire sites.
Mismatches between a Lexicon description and where the event actually
fires are themselves audit findings worth flagging.

Only skip the Lexicon step if the user explicitly says they've just
queried it themselves.

## Step 4 — Apply the rules

For every event and property in your output, the rules from
`Tools/Mixpanel/Data Design` apply:

- Naming (Title Case events for Aurie with `AI` prefix; un-prefixed for Aura;
  Title Case properties with spaces; screen-typed values end in `" Screen"`;
  no abbreviations outside `ID`, plus `CTA` in `Modal CTA Tapped` and
  its `CTA` property)
- Reuse standard properties from §3 of the doc before inventing
- **New events copy their family.** Take the family's full prefix
  (`AI Conversation Message …`, not `AI Message …`) and every property
  the family's existing events send, in the same value format. List them
  with `Mixpanel:List-Properties` scoped to the family's events and read
  live values with `Mixpanel:Get-Property-Values`. Match the existing
  values rather than proposing changes to existing events. Keep rare but
  live properties; drop only those whose fires sit on past builds. §2
  and §3 of the doc carry the detail. A new feature with no family
  takes one short shared prefix for all its events (Meditation Room:
  `AI Meditation …`, with `Room` only on the home screen event), so one
  Lexicon search finds the feature.
- **Design for every arm and entry point.** Events describe the surface,
  so they fire on control and every variant, for users who never see the
  element under test, and from any future entry point. No event name or
  property is specific to one experiment.
- **App-opened web pages use the server relay** (§4 of the doc), and the
  app passes `Sent From` to the page in the URL.
- Annotate backend-vs-frontend status per event for Aurie subscription
  events (no `Source App` on backend events)
- Expected `Source App` value for any frontend Aurie event
- PII risk flag if a property could carry raw user input
- Cross-app shared events (`Meditation Play`, `Track Skip`) — never rename;
  flag any **new** cross-app shared event for review
- **Onboarding-type flows copy the onboarding family.** When the feature
  is itself a multi-step enrolment — sign-up, waitlist, setup wizard —
  mirror the app onboarding events exactly: `AI <Feature> Sign Up Started`
  on the entry CTA; `AI <Feature> Sign Up Screen Seen` and
  `AI <Feature> Sign Up Screen Completed`, both carrying `Screen`, with
  step answers as properties on Completed (list and count names follow
  `List Of …` / `Number Of …`); `AI <Feature> Sign Up Completed` once per
  user on the API success response. This shape is for onboarding-type
  flows only; other features keep their own families.
- **New tabs copy the tab events.** A new tab gets `AI <Tab> Screen Seen`
  with `Sent From` = the tab the user came from, matching
  `AI Home Screen Seen` / `AI Journals Screen Seen` /
  `AI Profile Screen Seen`. Check that every entry surface has an
  impression event before the first CTA event.
- **One impression event per screen.** Each screen of a feature gets its
  own `AI <Feature> <Screen> Screen Seen`; never merge screens into one
  `Screen Seen` with a `Screen` property. A feature's home or tab screen
  event fires every time the user lands on it (from another tab, or Back
  from a child screen), not on keyboard open or close, an overlay
  closing, or a re-tap. Sheets are `AI <Feature> Modal Seen` +
  `Modal CTA Tapped`; filters are `Filter Opened` / `Filter Applied` /
  `Filter Reset` (§2 of the doc).
- **Prune derivable events.** An event whose answer is already in its
  neighbours is not specced: sheet seen/closed pairs, option-selected
  taps, saved-method confirmations. A dedicated outcome event (for
  example `AI Care Payment Card Added`) replaces a sheet pair. Mobile
  events do not send `Payment Method`.
- **Status enums mirror the backend.** Read the backend sub-issues for
  the stored fields and derive the profile property values from them,
  including the not-yet state (`Not Joined` | `Waitlist` |
  `Priority Access` from a join timestamp plus a priority flag).

## Step 5 — Format output

See [`references/output-formats.md`](references/output-formats.md) for the
exact templates per mode (linear, audit, gap, notion). Worked examples in
[`references/examples.md`](references/examples.md).

The Linear-card format is the §13 nested-checklist from the doc:

```
- [ ] `AI Object Action` — fires when [plain-language description]
   - `Property One` (string) — example: `value`
   - `Property Two` (number) — example: `42`
   - `Property Three` (boolean) — example: `true`
---
```

Keep Linear-card specs concise: a one-or-two-sentence intro plus the event
checklist — no long overview sections. A short **Analysis notes** section
after the checklist is welcome for caveats that affect how the data will
be read (backend-vs-frontend limits, intent-vs-confirmed event semantics,
test-cohort exclusion, out-of-scope event families).

**Changes to existing events go in their own section.** A new value on
a property an existing event already sends (`Action: Find My Support` on
`AI Conversation Done Screen Completed`, `Sent From: Care Confirmation
Screen` on `AI Conversation Session Started`) is listed under a heading
`Property value additions to existing events`, one line per event with
the new value and the existing values beside it. It is never written as
a checklist item among the new events, which would read as a new event
with the existing name.

**Editing an existing card?** Match the format already in it. When a card
was authored by someone else with its own checklist style (`- [ ] Event
Name : X - …` with nested `- [ ] Event Properties : …`), follow that shape
rather than imposing the §13 template — a card carrying two formats reads
as two specs. The same applies to its conventions: if it pairs mirror
events on a single line (`AI Conversation Session Started` / `AI
Conversation Session Ended`), spec new mirror pairs the same way.

**Fold resolved questions back into the spec body.** A card's
review-questions section records decisions; the spec body is what engineers
implement from. When a question resolves into a concrete change — add a
property, un-hide an event, widen scope — make that edit in the body too.
A ticked checkbox on its own produces no instrumentation.

Never include experiment properties (`Experiment: X` user profile
properties) or experiment-segmentation guidance in a spec — not in the
checklist and not in the analysis notes. Experiment analysis is a separate
concern owned by `data:analyze-experiment`.

## Step 6 — Capture novel feedback

Before closing out, scan the conversation for any rule, naming preference,
or anti-pattern the user pushed back on that **isn't already in the
canonical doc** (`Tools/Mixpanel/Data Design`) or this SKILL.md. Examples
of feedback worth persisting:

- A new naming convention or anti-pattern (e.g. "prefer generic event
  family + `Sent From` over screen-specific names")
- A property-shape preference that should apply across the funnel
- A PII clarification or new sensitive-property pattern
- A Lexicon-process tweak

If found, ask the user **once**:

> Worth adding `<rule, in one line>` to the canonical doc / skill so
> future runs don't repeat this?

On yes:
- For canonical-rule additions → edit `Tools/Mixpanel/Data Design`
  directly with `notion-update-page` (`update_content`, smallest exact
  `old_str`, in the right section), add a line to its `# Sources` list,
  and re-read the edited sections. `shared:record-knowledge` writes only
  `Learnings/*` and `Signals/*` and refuses this page.
- For workflow-level rules (anti-patterns, new steps) → invoke
  `shared:update-aura-skill` to PR the SKILL.md change.

On no or silence, drop it — don't push. Never auto-write the doc or open
a PR without explicit confirmation.

## Out of scope

- Building dashboards / reports / funnels in Mixpanel → use
  `anthropic-skills:aurie-mixpanel`
- Pushing event/property descriptions from a CSV data dictionary into Mixpanel
  → use `anthropic-skills:mixpanel-dict-sync`
- Approving newly-shipped events (`#mixpanel-event-approval` workflow) →
  use `data:mixpanel-event-approval`
- Running per-variant experiment analysis → use `data:analyze-experiment`

## Anti-patterns this skill exists to prevent

- snake_case or camelCase in event/property names
- Property values not ending in `" Screen"` when they refer to a screen
- Near-duplicate properties (`Coach Id` vs `Coach ID`)
- Abbreviations in event/property names or dashboard titles (`Conv`,
  `Sub`, `Msg`, `Reg`, `TSR`, …) — only `ID` is allowed, plus `CTA` in
  the modal family
- Raw user input or message text in any property value (PII risk,
  especially for Aurie)
- Inventing new cross-app shared events without flagging for review
- Filtering backend events by `Source App` (silent zero-row trap)
- Using `properties["Experiment: X"]` instead of `user["Experiment: X"]`
  for experiment segmentation (survivorship bias)
- Baking a single surface into an event name when the same UI could
  plausibly appear in other flows. Default to a generic event family +
  `Sent From` (e.g. `AI Subscription Downgrade Seen` with
  `Sent From: Subscription Cancellation`) over surface-specific names like
  `AI Cancel Monthly Downgrade Seen`. Only bake the flow in when the event
  is unambiguously tied to one flow (e.g. `AI Conversation Session
  Started`). See §2 of the canonical doc. This applies to reusable
  surfaces (paywalls, offers, prompts), not to screen impressions, which
  are one event per screen.
- Introducing variant property names (`Current Product *`, `Selected
  Product *`) when the immediately upstream/downstream screens already emit
  the standard `Product *` shape. Funnel filtering on `Product ID`
  end-to-end breaks the moment the column name changes mid-funnel. Only
  diverge for genuinely new dimensions (e.g. `Downgrade Product *` for an
  *offered* product distinct from the user's current one). See §3 of the
  canonical doc.
- Long overview sections in Linear-card specs. A one-or-two-sentence intro
  plus the event checklist, optionally followed by a short Analysis notes
  section, is the whole card — no methodology essays.
- Experiment properties or experiment-segmentation guidance in a
  data-design spec (`Experiment: X` user profile properties,
  `user["…"]` vs `properties["…"]` advice, variant readout notes).
  Data design specs instrumentation; experiment analysis belongs to
  `data:analyze-experiment`.
- Imposing the §13 template on an existing card that already has its own
  checklist format. Match the host card's shape and its pairing
  conventions instead.
- Leaving a resolved review question as a ticked checkbox only. If the
  answer implies a spec change (a new property, an un-hide, a scope
  widening), edit the spec body to match — implementers read the body,
  not the questions.
- Calling an event live on a 30-day total. A hidden event with a handful
  of fires can be one that shipped on a few builds and was removed;
  check by month and app version before reusing it.
- Inventing a bespoke event family for an onboarding-type flow when the
  `AI <Feature> Sign Up …` shape fits, or applying that shape to a
  feature that is not an onboarding.
- Writing changes to existing events inline as checklist items; they
  belong in the `Property value additions to existing events` section.
- Drafting events before the flow walk and ordered screenshots have been
  confirmed by the user.
- Naming a new event with a shorter prefix than its family, or leaving
  off properties the family's other events send.
- Designing a flow that crosses app and web from Mixpanel breakdowns
  alone; read the hand-off code first.
