# Deriving the mechanism metric from the ticket

The primary rate says whether an experiment moved the business. The mechanism
metric says whether the change did the thing it was built to do. Both are
needed: without the mechanism metric, a flat primary cannot be told apart from
a variant that never reached users.

## Method

1. Read the experiment's Linear ticket — description and acceptance criteria.
2. Write down, in one sentence, what the user-visible behaviour change is.
3. Ask: which event in the lexicon fires more, or less, if that change works?
4. Ask the harder question: what does the ticket **also** touch that nobody
   listed as a metric? Relocations and redesigns carry side effects.
5. Search the lexicon for that event family before assuming it does not exist.

Step 4 is where the value is. A ticket that says "add a bookmark button" and
also says "move Affirmations into the bookmarks section" has two effects, and
only the second one can regress an existing behaviour.

## Worked examples

| Change | Mechanism metric | Why |
|---|---|---|
| Paywall close button delayed, restyled or unpinned | paywall dismissal rate — modal-closed ÷ modal-seen | The change exists to reduce dismissal. If dismissal does not move, nothing shipped |
| Paywall copy rewritten | % annual subscriptions | Copy shifts which plan is chosen before it shifts whether anyone buys |
| Price ladder | plan mix and revenue per exposed user | Conversion can be flat while the mix collapses; a conversion-only readout reports a null |
| Feature moved into a new tab | completion rate of the **moved** feature | Relocation costs the old surface its traffic |
| Chat or conversation UI redesign | per-message rating rate, split by mode | Redesigns change what is reachable, not just what is pretty |
| A list replaces another list | events from **both** lists | Confirms the swap is clean and only one fires per arm |
| Anything gated to a build | assignment coverage vs the whole cohort | A gated experiment has a different population and its rates are not comparable |

## Event families to search

Search by feature noun, not by guessing full names. Families that exist:
subscription modal seen/open/closed, conversation session started, message
rated, session rated, journal and bookmarks screens, affirmation seen and
completed, feed item tapped and completed, daily practice setup/started/
completed, limited-free limit reached, voice-mode tooltips.

Useful splits: `Current Conversation Mode` (voice vs text), `Paywall Type`,
`Sent From`, `Modal Screen Order`, `period`.

Names confirmed against the Aurie lexicon, for the surfaces that carry live
experiments:

| Surface | Entry step | Downstream |
|---|---|---|
| Journal | `AI Journals Screen Seen`, `AI Journal Creation Screen Seen` | `AI Journal Created`, `AI Journal Edited`, `AI Journal Deleted`, `AI Journal Details Seen` |
| Daily practices | `AI Daily Practices Setup Started`, `AI Daily Practice Sheet Seen` | `AI Daily Practices Setup Completed`, `AI Daily Practices Setup Skipped`, `AI Daily Practice Started`, `AI Daily Practice Completed` |
| Affirmations | `AI Affirmation Screen Seen` | `AI Affirmation Completed` |
| Home | `AI Home Screen Seen` | — |
| Conversation done | `AI Conversation Session Completed` (split on `Conversation Session Mode`) | `AI Conversation Session Rated` |
| Quick Process done | `AI Quick Process Session Completed` | `AI Quick Process Session Rated` |
| Self-commitment (onboarding) | `AI Self Commitment Screen Seen` | `AI Self Commitment Screen Completed`, `AI Self Commitment Completed` |
| Review request | `AI Review Modal Seen` | — |

The Aura family-plan offer runs `Family Plan View` → `Family Plan Payment Page
Seen` → `Family Plan Subscription Upsell`, with outcomes on `Family Plan
Subscription Paid` and `Family Plan Subscription Cancelled`.

`AI Daily Practices Seen`, `AI Daily Practices Setup Seen` and `AI Daily Practice
Tapped` are hidden in the lexicon, and a substring search of the lexicon skips
hidden events. Look a hidden event up by its exact name before concluding it does
not exist, and meter its volume before using it as an entry step.

Chat image upload has no event, and `AI Conversation Message Sent` carries no
attachment property, so an image-upload experiment's mechanism row is
`uninstrumented`.

No orb or animation event exists. An experiment on the reactive orb has no
mechanism metric, and the readout says so rather than reaching for
`AI Home Screen Seen`.

## Check the event fires project-wide before reading it per arm

A mechanism metric that reads zero in every arm is usually an instrumentation
fault rather than a result, and the per-arm cut cannot tell you which. Meter the
event across the whole project first, then widen the window to classify it:

- **Never fired** — the event was never wired to the surface.
- **Has historical volume and has gone silent** — a regression shipped with the
  change under test. Date the silence against the release that carried it.

The sharpest shape is a surface whose **downstream events fire while its entry
steps do not**. Measured on `dailyPractices2` over 2026-08-24 to 08-26: setup
completed 8, practice started 33, practice completed 14, against zero for
`AI Daily Practices Seen`, `AI Daily Practices Setup Seen` and `AI Daily
Practice Tapped` — where those three carried 464, 12 and 117 events across
2026-07-01 to 08-26, all of it before the retest shipped. Practices were being
used and no event recorded anyone arriving at them, so an experiment whose arms
are defined by *when the picker appears* had no way to show that the picker
appeared.

## Report an absent event as a finding

If the metric the ticket implies has no event, that is an instrumentation gap
and belongs in the readout. Two failure shapes worth naming explicitly:

- **A funnel with no entry step** — downstream events fire while the
  screen-seen event never does, so adoption is uncomputable.
- **A metric that only exists in one arm** — correct for a new feature, but say
  there is no control comparison rather than presenting a bare number.

Do not substitute a nearby event and present it as the mechanism metric.
