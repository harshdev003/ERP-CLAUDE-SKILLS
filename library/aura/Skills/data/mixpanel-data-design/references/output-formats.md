# Output formats

Exact templates for each output mode. Always confirm product (Aura vs Aurie)
before producing any of these — see SKILL.md Step 1.

## `linear` — new instrumentation spec

Default mode when the user asks to design events. Format: one checkbox per
event so engineers can tick events off as they ship; properties as nested
plain bullets with `(type)` and an example value inline; events separated by
horizontal rule.

```
- [ ] `AI Object Action` — fires when [plain-language description of the trigger]
   - `Property One` (string) — example: `value`
   - `Property Two` (number) — example: `42`
   - `Property Three` (boolean) — example: `true`
---
- [ ] `AI Other Action` — fires when …
   - `Property One` (string) — example: `value`
---
```

Annotation rules (applied silently — don't surface as a separate section
unless asked):

- For any Aurie frontend event, include `Source App` as a property with the
  expected value (`Aurie Mobile` / `Aurie Web` / `Aurie Desktop`)
- For any Aurie `AI Subscription *` event that's backend (everything except
  `AI Subscription Purchased` and `AI Subscription Modal Seen`), note
  `[backend — no Source App]` inline after the description
- For any property that could carry user input, replace with an enum or
  categorical and add an inline comment, e.g.
  `Conversation Mode (string) — example: "open" | "guided" — never raw text`

If the spec also adds a property value to an existing event, append a
section headed `## Property value additions to existing events`, one line
per event with the new value and the existing values beside it:

```
## Property value additions to existing events

No new events and no shape change; only a new value on a property these events already send.

- [ ] `AI Conversation Done Screen Completed` — add `Action: Find My Support` when the done-screen banner CTA is tapped (existing values: `End The Conversation`, `Start A New Chat`)
- [ ] `AI Conversation Session Started` — add `Sent From: Care Confirmation Screen` when Talk to Aurie opens the existing conversation
```

Existing events never appear as checklist items among the new events.

## `audit` — table

Mode when the user pastes events and asks "are these OK?" or "audit this
naming". Format: a markdown table with one row per issue.

```
| Event Name | Issue | Recommendation |
|---|---|---|
| `ai_coach_message_sent` | snake_case in event name | Rename to `AI Coach Message Sent` |
| `Sent From: settings` | Screen-typed value missing `" Screen"` suffix | `Sent From: Settings Screen` |
| `Coach Id` | Near-duplicate of `Coach ID` | Normalize to `Coach ID` (standard property) |
```

Include property types inline in the Recommendation column when type
information is part of the fix, e.g. `Add Duration (number)`.

If an event passes audit, list it under a "Passes audit" subheading after the
table — don't pad the table with empty issues.

## `gap` — what's missing

Mode when the user asks "what's missing from the [screen] flow" or "gap
analysis for [feature]". Format: a short summary paragraph naming what's
missing and why, followed by the same checklist format as `linear` for the
proposed new events.

```
The journaling intro screen currently tracks `AI Journal Intro Seen` but
nothing else — there's no way to measure scroll depth, time to first tap,
or which CTA the user picks. Adding the events below closes the loop:

- [ ] `AI Journal Intro CTA Tapped` — fires when the user taps any CTA on the journaling intro screen
   - `CTA` (string) — example: `Start Journal` | `Maybe Later`
   - `Source App` (string) — example: `Aurie Mobile`
---
```

## `notion` — fuller spec for a Notion page

Mode when the user wants to document a feature's tracking in AI Knowledge or
another Notion page. Format: H2 per event, H3 sub-sections for Properties
and Notes, property table with Type / Description / Example columns.

```
## `AI Object Action`

Fires when [plain-language description].

### Properties

| Name | Type | Description | Example |
|---|---|---|---|
| `Source App` | string | Platform the event was emitted from | `Aurie Mobile` |
| `Property One` | string | What this property captures | `value` |

### Notes

- Backend / frontend status
- PII considerations
- Related events (cross-references to other tracked events)
```

Use this mode only when the user is producing documentation — for Linear
tickets, prefer `linear`.

## Always include in any output

- Annotate Aurie subscription events with backend/frontend status (since
  the user can't tell from the name alone)
- For new properties, mention whether a standard property from §3 of the
  data-design doc would have worked instead
- For any new cross-app shared event, an explicit "flag for review before
  merging" line — don't ship it silently
