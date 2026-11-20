# Comparing execution cards with the plan

Execution cards are written once and then go stale: the PM answers a question in a comment
thread, the designer adds an action, a value gets confirmed. Step 10 finds every place an
existing card no longer matches the plan, so the cards can be corrected before anyone builds
from them.

## What to compare

Read each execution card's description in full, **and its comments**, then check every item
below that the card covers. Look for the value, not the wording.

A comment this skill posted earlier counts as part of the card: every such comment starts with
the marker line `plan-prompt: differences from the plan`. If a mismatch already appears in one
of those comments, don't list it as new. Show it as "already flagged in the comment of {date}",
and don't offer it again. A mismatch that has changed since that comment (the plan moved again)
is new.

| Area | Typical mismatch |
|---|---|
| Output schema | Enum values (categories, topics), field names, `minItems` / `maxItems` |
| Stored columns and states | A new state the product added (removed, hidden, archived), a column the plan dropped |
| Lifecycle | When an item is done, what hides it, caps on what the client shows |
| Prompt inputs | A new input (e.g. removed items as a negative signal), caps, windows, the "open" filter |
| Config | Model id, `reasoningEffort`, tier, OpenAI project |
| Gating | Experiment id and arm names, free vs paid |
| API | New or changed routes, filters, defaults |
| Prompt content and safety rules | A category whose definition changed, a safety rule pending an answer |
| Open questions | A card that still says "to confirm" for something now settled |

## Table format

```markdown
| Card | Area | Card says | Plan says | Source of the change |
|---|---|---|---|---|
| PROD-7541 | Output schema | topic enum `sleep, work, mood, …` | `relationships, stress, workPurpose, …` | Topic screenshot in PROD-7482 description, 10-15 |
| PROD-7545 | API | no remove route | `PUT /:id/remove` | Steve, PROD-7482 comment 10-15 |
```

- One row per mismatch; a card with three mismatches gets three rows.
- Quote the card's current text in "Card says" so the user can find it.
- "Source of the change" names the comment, screenshot or user answer, with its date.
- Sort by card, in execution order.

## Updating the cards (Step 11, on a yes)

- **Fetch each description fresh right before editing.** Linear rewrites markdown on save (`-`
  bullets become `*`, bold markers move, `~` gets escaped), so text you wrote earlier won't match
  what is stored.
- Every edit is anchored to a stored line, and fails rather than guesses when that line matches
  zero or several times:
  - **Replace**: a wrong value. Replace the whole line that holds it.
  - **Insert**: something the card is missing (a scenario, an input, a route). Insert the new
    lines directly after a named anchor line in the right section, such as the last bullet of
    the list it belongs to. If the card has no suitable section, add a new `## Plan updates`
    section, placed by the first rule that applies:
    1. directly before the `Acceptance criteria` heading, if it appears exactly once;
    2. else directly before the `Resources` heading, if it appears exactly once;
    3. else at the end of the description.
    The section fallback always lands somewhere, so a confirmed insert never fails for lack of
    a heading.
- Show each edit as old text → new text (for an insert: anchor line, then the added lines) before
  applying it.
- Change only the mismatched lines; leave the rest of the card untouched.
- Cards **In Progress** or later get a comment instead of a description edit. The comment starts
  with the marker line `plan-prompt: differences from the plan`, followed by that card's rows of
  the table, so a later run recognises them.
- Re-run the comparison after the edits; it should report no differences, with commented
  mismatches shown as already flagged.
