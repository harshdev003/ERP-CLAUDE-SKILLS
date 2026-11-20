# The matcher: four passes, and why each exists

The whole skill turns on one question: **can any app version still name this experiment?**
Get it wrong in the permissive direction and users on an affected build silently fall back
to the control arm — `chooseAll` skips a name it cannot find in the node, so the screen
renders its default. No error, no log, no Bugsnag event. The only symptom is a metric moving.

So the matcher is built to over-keep. Every pass below was added because a real experiment
slipped past the passes before it.

## Pass 1 — read as an experiment

```
experiments.X   experiments?.X   experiments['X']   experiments/X   assign_experiment=X
experiments.chosen?.[SOME_CONSTANT]        <- bracket access by IDENTIFIER
```

**The last form is not optional either, and it was missed on the first cut.** AIWeb reads
its 3-day-trial experiment only as
`experiments.chosen?.[AB_EXPERIMENTS.AI_THREE_DAY_TRIAL_WEB] === 'a'`. The key never appears
as a literal anywhere near `experiments`, so passes 1-3 saw nothing and pass 4 recorded the
constant as merely *named* rather than *read*. `aiThreeDayTrialWeb` was consequently
classified as an unreferenced string and came one step from being archived, which would have
reverted that paywall silently.

The fix pairs the two passes: an identifier used to index `experiments` is captured here,
and pass 4 maps it back to whatever key the constant holds. Re-running the corrected matcher
over a set already hand-reviewed promoted **11** rows from "unreferenced string" to "read" —
including the entire web `pricing*` family, which had by then been within one step of
archiving twice.

The obvious form, and the only one that is unambiguous evidence. On its own it is far too
narrow: at HEAD across all five clients it matches ~35 keys, because most names reach the
resolver through a registration array rather than a direct property read.

## Pass 2 — registered for assignment

A quoted literal within 40 lines of `chooseAll(` / `chooseOnboarding(` /
`chooseTargetedOnboarding(` / `allExperiments` / `experimentsToAssign` / `expNames`.

`Dashboard.js` is the canonical shape: an `allExperiments` array of quoted names, filtered,
then handed to `Experiment.chooseAll`. The names never appear as `experiments.X` anywhere.

## Pass 3 — quoted literal in a file that mentions "experiment"

The backstop for helper modules that take `experiments` as a parameter and compare names
inside — `UserController.canShowCommunityFeatures(experiments)`, `CoachesStore.isDirectMessages(...)`.
The key is quoted, but nowhere near a registration marker.

Gating on the file mentioning "experiment" is what makes this pass usable. Without the gate
it matches every content category in the app: `'sleep'`, `'paywall'`, `'series'`, `'mood'`,
`'breathwork'` are all real experiment names **and** ordinary strings in a meditation app.

## Pass 4 — resolved through a named constant

```js
// data/enums.js
export const AURA_DESIGN_CHANGE = 'auraDesignChange2'
// screens/Dashboard.js
const allExperiments = [ ..., AURA_DESIGN_CHANGE, ... ]
```

**This pass is not optional, and it is the one most likely to be dropped as an optimisation.**
The string exists in exactly one file, which may be a constants module with no other
experiment content; the registration array contains an identifier, not a name. Passes 1–3
can all miss it.

It matters *more* for ended experiments, not less: nothing prompts anyone to delete a
constant when an experiment stops splitting, so the constant routinely outlives the inline
string it replaced.

Adding this pass pulled 20 rows back out of an archive set that had already been reviewed,
including the entire web `pricing*` family behind `PRICING_*` constants, plus `paymentCTACopy`,
`webLandingPageFooter`, `webQuizBackButton`, `getAppAuraInspired` and `aiThreeDayTrialWeb`.
Archiving those would have reverted the web paywall for anyone on an affected version.

`GENERIC_IDENTS` excludes bindings like `id:`, `key:`, `category:` — a track list containing
`{ id: 'breathSleep' }` is content data, not a reference to the `breathSleep` experiment.

## Known limits — both are false negatives

A false negative archives a row that is still in use, so these are the failure modes to
close before a tranche runs, not after.

- **A computed name defeats all four passes.** `experiments[\`exp${n}\`]` or
  `experiments[someVariable]` resolves at runtime. Grep for template literals and variable
  indexing against the experiments object before the first tranche of any run.
- **Constant resolution is single-hop.** `A = B` where `B = 'key'` is not followed. Two-hop
  indirection has not been observed in these repos, but it has not been ruled out either.
- **Generational names share a prefix.** `aurieAnimations` / `aurieAnimations2`,
  `auraDesignChange` / `auraDesignChange2`, `webViewPaywall` / `webViewPaywall2`. Every
  pattern here is word-bounded, but an ad-hoc `grep` for the gen-1 name will match the gen-2
  successor and read as a live reference when it is not (and, worse, the reverse). Bound any
  manual check.

## History is scanned exactly, never sampled

The four passes above run against each client's **current** tree. History is a separate,
cruder, and *exact* scan: one `git log -p` pass per client over the paths that ever held
experiment references, keeping any key seen as a quoted literal or an experiment property
read across every added, removed and context line in every commit.

**Sampling was tried first and was wrong in the direction that breaks users.** Checking the
Aura app every 6 months for 4 years found 1,496 keys; the exact scan finds 1,999. Adding the
other four clients takes the keep-set to 2,206. Measured against one real run, sampling would
have archived **490 rows that some build could still ask for** — every one an experiment that
lived and died between two samples.

Cost was the only argument for sampling and it does not hold: the Aura app's full patch
stream for `src` + `app.js` is ~175 MB and scans in ~9 seconds; all five clients together
take ~31 seconds.

The history pass is deliberately crude — quoted literals and property reads, no file gating,
no constant resolution. It over-keeps heavily, and that is the point. Over-keeping costs
savings; under-keeping drops users onto the control arm with no error. When the two pull
against each other, keep.

## What the matcher cannot tell you

Whether anyone still *runs* a version that names the key. There is no read counter on an
RTDB child, so code is the only available evidence — and code says which versions *could*
ask, never which versions are installed. That gap is closed by the Bugsnag version-share
floor in [preconditions.md](preconditions.md), not by the matcher.
