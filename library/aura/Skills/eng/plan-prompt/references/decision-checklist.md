# Decisions that change a prompt

Walk this list against the requirements before planning. Each item is a question whose
answer changes the prompt text, its inputs, its config or its cost. Raise only the ones
the requirements don't already settle, and phrase each as a concrete question with the
options and your recommendation.

## Trigger and cadence

- **What triggers generation?** On demand in a request, at conversation end, or a cron job.
  This decides the tier (awaited in a request → standard; cron or un-awaited → flex) and
  which inputs exist at run time.
- **How often, and in which timezone?** A local-time job changes what "today" means for
  date-scoped builders.
- **What counts as "nothing new"?** Which signals (new conversation, new memory, profile
  change) must exist for a run to be worth an LLM call, and do those signals have a
  timestamp to check against?

## Output shape and volume

- **How many outputs per run, and can it be zero?** Zero must be a valid schema output if
  the feature only fires "when relevant".
- **Fixed categories or free-form?** If fixed, are they tags only, or must every category be
  filled?
- **What does each output contain?** Every user-visible field (headline, subtext, opener
  message, category) and who reads it.
- **What happens on tap or use?** Does the output open a chat, and with which first message?
  Does that need its own `firstMessageContext` branch?

## Lifecycle

- **Do outputs accumulate or replace?** Accumulating outputs need no-repeat input.
- **Expiry, completion, hiding.** Does a used output disappear? Do unused ones expire? This
  decides the stored fields and what the prompt sees as "previous outputs".

## Audience and gating

- **Free vs paid, experiment arms.** Each gate changes run volume and therefore cost.
- **New users with no history.** Does the feature run, return nothing, or need an empty state
  the client draws?

## Content

- **Voice.** Aurie speaking to the user, or the user's own words? A reused prompt in the wrong
  voice is the most expensive wrong choice to discover late.
- **Sensitivity.** Is the output shown without the user asking, or on a surface others can
  see? That calls for rules on which topics may be resurfaced and how much detail.

## Contradictions

Compare every source pairwise: card description vs comment answers vs Figma notes vs PRD.
A design annotation and a PM answer that disagree (for example "cards disappear once used" vs
"old cards stay") is a decision, not a detail. Quote both and ask which one holds.
