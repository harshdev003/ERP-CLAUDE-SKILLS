# Metric contract per app

Aurie and Aura share one Mixpanel project but use entirely different event
names. Picking from the wrong set returns zero rows silently. Project id,
workspace and event descriptions are canonical in `Tools/Mixpanel`; the
per-experiment dashboards that define these funnels are the
`Aurie Experiments (Mobile)` and `Aura Experiments (Mobile)` boards.

## App detection

`targetApp == "ai"` → Aurie. `targetApp == null` → Aura. Any other value is a
warning, not a default — flag it.

## Segmenting by experiment

Cut every experiment on the `Experiment: <name>` **user** property — `resource:
"user"` — used twice: as the filter (`is set`) and as the breakdown. This is what
the boards do, and it is the only cut that works end to end.

An `Experiment: <name>` **event** property also exists in the lexicon. It is not a
substitute:

- It is stamped on roughly 58% of exposed users. Cross-tabbing both cuts on
  `AI Subscription Modal Seen` for `bookmarkChat` over 2026-08-04 to 08-24 returns
  1,315 users in arm a by user property against 759 by event property, and 1,328
  against 758 in arm b. An event-level breakdown discards the remainder silently.
- `AI Subscription Paid` carries no `Experiment: *` property at all — it is a
  backend event whose properties are `revenue`, `period`, `paid`, `autoRenew`,
  `productId` and billing fields. Every money metric is therefore unsegmentable
  by event property.

The exception is an experiment whose change lands **before** registration. Someone
who never registers has no user profile, so a user-property cut cannot see them:
the population truncates to registrants and registration stops being a valid
outcome. Those need an event-level cut on the onboarding events, and the readout
says so.

The user property holds the current assignment, so a reassignment would
re-attribute that user's historical events to their new arm. Whether the Aura
experiment service ever reassigns is unverified — treat an unexplained mid-run
shift in arm sizes as a reason to check.

## Aurie (Mobile)

Aurie has no trials. Paid conversion is the primary monetisation rate.

| Metric | Definition | Tier |
|---|---|---|
| Paywall → paid, 7d | funnel `AI Subscription Modal Seen` → `AI Subscription Paid`, 7-day window, unique | primary |
| Stripe checkout → paid, 7d | funnel `AI Stripe Checkout Screen Seen` → `AI Subscription Paid`, 7-day window, unique | primary in place of paywall → paid when the change lives on the web paywall or Stripe checkout |
| Paywall → checkout reach | funnel `AI Subscription Modal Seen` → `AI Stripe Checkout Screen Seen` | second-class — context for a checkout experiment |
| Paid → cancel, 7d | funnel `AI Subscription Paid` → `AI Subscription Cancelled`, 7-day window | guardrail, lower is better |
| Paid → cancel, 30d | same funnel over a 30-day window | primary, lower is better — report only when significant |
| Revenue per exposed user | sum of `Premium Revenue` over exposed users ÷ exposed users | primary |
| W1 session retention | `AI Conversation Session Started` to itself, weekly cohorts averaged | primary, directional |
| Total sessions | total `AI Conversation Session Started` | primary |
| Avg sessions per user | total `AI Conversation Session Started` ÷ users with ≥1 session | primary, directional |
| Paid → refund, 30d | funnel `AI Subscription Paid` → `AI Subscription Refunded`, 30-day window | guardrail, lower is better — report only when significant |
| Registrations | unique `AI Sign Up Login` | exposure |
| Conversation reach | unique users with ≥1 `AI Conversation Session Started` ÷ registrations | second-class |
| % annual subscriptions | share of `AI Subscription Paid` where `period = year` | second-class |

Sessions and retention are decision metrics on Aurie, not colour: the product is a
conversation, so a change that costs session volume has cost the thing being sold.

Scope Aurie **client** events to `Source App = Aurie Mobile`. Subscription
events are backend and carry no `Source App` — they are necessarily unscoped.

## Aura (Mobile and Web)

Aura still runs trials, so the trial funnel is primary.

| Metric | Definition | Tier |
|---|---|---|
| Trial start rate | `Subscription Trial Started` ÷ `Sign up login` | primary |
| Trial → cancel, 7d | `Subscription Cancelled` with `trial = true` ÷ trials started, 7-day window | primary, lower is better |
| Trial → paid, 30d | funnel `Subscription Trial Started` → `Subscription Paid`, 30-day window | primary |
| Paid start rate | unique `Subscription Paid` ÷ registrations | primary |
| Paid → cancel, 7d | funnel `Subscription Paid` → `Subscription Cancelled` with `paid = true`, 7-day window | primary, lower is better |
| Paid → cancel, 30d | same funnel over a 30-day window | primary, lower is better — report only when significant |
| Revenue per exposed user | sum of `Premium Revenue` over exposed users ÷ exposed users | primary |
| % annual subscriptions | share of `Subscription Paid` where `period = year` | primary |
| Registrations | unique `Sign up login` | exposure |
| Paywall reach | `Subscription Modal Open` ÷ registrations — diagnostic only, see trap 5 | second-class |

On a trial-led product the trial-cancel step is where a pricing or copy change does
its damage, which is why it is a priority metric rather than a guardrail. Plan mix
carries the same weight: two Aurie pricing experiments were decided on annual share
against flat conversion, so a conversion-only readout on a price change reports a
null it has not earned.

The Aura board is deliberately **not** scoped by `Source App`.

Aura Web rows on the sheet share an experiment property with their Aura Mobile
twin. They are one randomisation reported on two tabs, not two populations —
report them as cross-platform and never sum them.

## Revenue is a decision metric on every experiment

Every experiment carries a revenue figure, not only the ones that change a price. A
conversion-only readout answers whether more people bought and never whether the change
earned more, and the two diverge whenever a variant shifts which plan people buy.

**Take revenue from the `Premium Revenue` user property**, which holds the whole plan the
user actually has. Do not take it from the `revenue` property on `Subscription Activated`,
`Subscription Paid` or `AI Subscription Paid`.

- Sum it as the total over each distinct plan value of that value multiplied by the unique
  users holding it, read from a breakdown on `Premium Revenue`. An aggregate-property sum
  over events double counts, because activation events outnumber activating users.
- Subtract cancelled users at **their own** plan value, never at the arm average.
- Report it per user at the **exposure point** — see the denominator rule below — and state
  the numerator, the denominator and the population, as with any other rate.

The event property sees only the transaction that fired, while a meaningful share of
subscribers hold a Family Plan tier that an individual-plan change never touches. Counting
those users at their real plan value raises the control arm's average and closes most of an
apparent gap. On `mobileYearlyPricing2`, net revenue per paywall view read +29.5% on the
event property against **+10.2%** on `Premium Revenue`, because 14% of subscribers in both
arms held a Family Plan. The same substitution moved trial cancellation from an apparent
+15.9% to a flat +4.1%. Where a change is plan-specific, state what share of buyers it
actually reaches.

`Premium Revenue` is live and correct for new subscriptions. It is **stale for renewal price
changes**, where the profile keeps the old price until the renewal processes; take the plan
price from the user's last `Subscription Paid` or `Subscription Renewed` event there.

## Denominate at the exposure point

The denominator is the surface where the change is seen, and it sits at or below the
assignment point.

| Where the change lives | Denominator |
|---|---|
| The paywall — price, copy, layout | unique users on `Subscription Modal Open` (Aura) or `AI Subscription Modal Seen` (Aurie) |
| The web paywall or Stripe checkout — terms, checkout layout, payment methods, card form | unique users on the checkout's own seen event (`AI Stripe Checkout Screen Seen` on Aurie) |
| One specific step inside checkout | that step's own event, so the funnel starts where the change is first seen |
| A feature surface | that surface's own entry event, per trap 6 |
| Onboarding, before any gate | registrations |
| An existing-subscriber population | assigned users from a profile count; registrations do not apply |

`Subscription Modal Open` under-captures in absolute terms but evenly across arms, so an
arm-to-arm ratio built on it holds while the absolute value per view is understated. Trap 5
rules it out as a denominator for a **trial rate** across experiments of different sizes; it
remains the right exposure denominator **within** one paywall experiment.

**A checkout change is judged from checkout, not from the paywall.** Paywall → paid counts
everyone who saw the paywall, and most of them never open checkout, so a change that lives on
checkout reaches only a fraction of that denominator and its effect is diluted by the rest. On
one Aurie checkout-terms experiment about 31% of paywall viewers reached Stripe checkout while
463 of 465 payers passed through it — the paywall-based rate was diluted roughly threefold. So
the paid funnel starts at the first event where the change is visible: the checkout-seen event
for a change to checkout as a whole, or a later step's own event for a change to that step.
Report paywall → checkout reach beside it as context; it sits upstream of the change and should
be flat across arms, which makes a gap there a sign of a bug rather than an effect. Where an app
has no event for the step that changed, carry the gate as `uninstrumented` rather than falling
back to the paywall denominator.

## An existing-subscriber experiment is judged on retention

Where the population is existing subscribers, the primary metrics are the cancellation rate,
retention, login or return, and engagement. Trial Start Rate and Paid Start Rate are
second-class context there — those steps sit behind the population, and a zero on them is a
measured zero rather than a gap. Compare the non-control arms against each other on the
surface metrics, keeping control as the zero-check.

## Cancel-flow and existing-subscriber metrics (Aura)

| Metric | Definition | Tier |
|---|---|---|
| Trial → trial cancel, 7d | funnel `Subscription Trial Started` → `Subscription Cancelled` with `trial = true` on the cancel step, 7-day window | primary, lower is better |
| Cancel after the offer screen, 7d | funnel `Subscription Discount Screen Seen` → `Subscription Cancelled`, 7-day window, on screens seen at least seven days before the run | primary, lower is better |
| Paid → refund, 30d | funnel `Subscription Paid` → `Subscription Refunded`, 30-day window | primary, lower is better — `lagged` until the cohort is 30 days old |
| Whole-subscription cancel | unique `Subscription Cancelled` ÷ users carrying the property since the start date | primary, lower is better — `unscoped` |

A cancel-flow experiment's population is the people who reach the flow, so
registrations are not a reading for it, and unique users on any event are an activity
count (trap 12) rather than arm sizes.

Iterable-driven experiments write the same `Experiment: <name>` user property, so
their users are visible in Mixpanel and are cut like any other experiment. A row
reading zero users is a measured zero: the journey is not sending or not tagging users,
which is a finding for the journey's owner.

## The core metric set applies to every experiment

Beyond the per-app tables, each experiment carries the metrics its own change is
expected to move. Six rules govern them.

**Key metrics sit in the primary tier.** A metric a variant is expected to move is
a decision metric, not a footnote, so it belongs beside conversion rather than in a
separate mechanism block. Report it even at low n, with n stated.

**Compare only surfaces present in every arm.** Where a variant adds something the
control does not have, that usage is descriptive for that arm and never a head-to-head
number — there is no control to beat. Judge those experiments on the general set.

**Include the spillover.** A change to a surface also gets that surface's downstream
volume, not only its own rate. A screen that improves its own completion while costing
session volume is a loss, and a rate-only readout hides it.

**Report the dimension the effect runs through.** For each experiment ask which
dimension would carry the effect — modality (voice against text), platform, plan,
entry point, new against existing users — and break the metric down on it wherever
that property exists in all arms. `Current Conversation Mode` splits session starts;
`Conversation Session Mode` splits completions. A lift in voice against a drop in text
averages to nothing when it is not split.

**Retention is session → session, on the app's own session event.** Take the event
named in that app's table above, use it as both the born and the return step, average
weekly cohorts and report W1. It measures whether people came back to use the product
rather than whether the app fired any event, and the three candidate definitions
disagree by enough to flip a verdict: on one Aurie experiment session → session read
W1 24% against 25% while a registration-born, any-event definition read 37% against
39% and the daily monitor's own registrant cohort read 26.9% against 29.3%. Weekly
cohorts overlap, so the figure is directional and carries no confidence, and its level
is not comparable to the monitor's — say so rather than reconciling them. **Where the
app's table names no session event, this skill computes no retention figure**; carry
the daily monitor's as its reading and label it that way rather than substituting
another app's event.

**30-day cancellation and paid → refund are reported only when significant, wherever
the app's table defines them.** Both are noisy at the sample sizes these experiments
reach, and a row of sub-threshold cancellation readings trains the reader to ignore the
metric. Compute every metric the app's table carries on every run, and state that a
suppressed one was measured and came back inside noise, so its absence is not read as
a gap. A metric the app's table does not define is not silently borrowed from the other
app's set — the per-app tables are the authority on which events exist, and this
section governs only how the metrics they name are tiered and reported.

## Fourteen traps

**1. Aura cancels put the flag on the cancel step.** Trial cancels are
`Subscription Cancelled` with `trial = true`; paid cancels are the same event
with `paid = true`. `trial = false` **is never emitted**, so filtering for it
returns nothing and looks like zero cancellations. `Subscription Trial
Cancelled` exists as a separate event and does not always agree — the board's
definition is the flagged one.

**2. `Subscription Modal Seen` is the Aurie event and records nothing on Aura.**
Aura's paywall-seen event is `Subscription Modal Open`. Using the Aurie name on
Aura yields an empty funnel with no error.

**3. `Internal` exists as a user property and not on backend events, and the
`is not set` form of the exclusion is unsafe.** Filter `resource: user`. An
event-level filter cannot exclude internal accounts from subscription metrics,
because the property is absent there — the filter passes silently and the exclusion
never happens. Exclude on `Internal` **not being true**, never on it being unset:
the property is stored as a set value on some profiles, so `is not set` drops real
users. On `AIAura Aurie Opened` it dropped all five matching users and produced a
mechanism metric of zero on a surface that was working, which reads exactly like a
variant that never shipped. Where a mechanism metric comes back at or near zero,
re-run it without the exclusion before reporting it: internal accounts run around
one in eight hundred users, so their contamination costs less than a false zero.

**4. Aura's trial → paid lag is about 8 days.** On a cohort younger than that,
`Trial → paid`, `Paid start rate` and `% annual subscriptions` all understate.
Mark them lagged and keep them out of verdicts.

**5. `Subscription Modal Open` is not a valid denominator.** It returns a
ceiling of roughly 660–780 users on every Aura experiment regardless of whether
that experiment has 800 or 1,150 assigned registrations, and trial counts exceed
what that population can account for. Dividing trials by it yields a rate that
clusters around 38% or 75% purely as a function of cohort size rather than
anything the product does. Use registrations as the trial denominator and keep
paywall reach as a diagnostic that flags the instrumentation gap.

**6a. An ordered funnel only gates on exposure when the feature precedes the outcome.**
Inserting the feature event as a middle step is a valid gate only where users reach that
feature before converting. On the Aurie journal redesign, 92.5% and 90.5% of each arm saw
the journal screen at least once while only 33 of 199 and 27 of 214 payers saw it *before*
paying, so the gated funnel collapsed to an unrepresentative sixth of payers and conversion
was never a test of the feature at all. Where the ordering does not hold, bound the metric
instead: remove the unexposed under both extreme assumptions and report the range. Numeric
`buckets` on a frequency-per-user breakdown are silently ignored by the MCP — it returns
default bands whose first one merges "never" with "once" — so derive exposure by subtracting
unique users on the feature event from unique users overall.

**6. A feature-surface experiment needs its own exposure gate.** When the change
lives inside a feature — the conversation, the journal or bookmarks tab, the done
screen — paywall viewers who never reached that feature are unexposed, and
counting them fills the denominator with users the variant could not have
affected. Gate the paid funnel on the feature's own event. For an Aurie
conversation-surface experiment that means inserting `AI Conversation Message
Sent`, filtered to `Premium AI = False` so the step precedes conversion, between
`AI Subscription Modal Seen` and `AI Subscription Paid`. On `bookmarkChat` the
ungated funnel reads 28.51% (256/898) against 32.40% (288/889) — a 12.0% drop at
92.6% confidence — while the gated funnel reads 10.21% (54/529) against 10.89%
(55/505), a 6.3% difference at 28% confidence, which is flat. Paywall copy and
pricing experiments need no extra step, because `AI Subscription Modal Seen` is
already their exposure gate; a change on the web paywall or Stripe checkout starts at
checkout instead, per the exposure-point rule above. Cancellation rates need no gate either: a paid user
has necessarily used the product.

**7. `Product Price` mixes currencies and cannot be summed.** The client event
`AI Subscription Purchased` carries `Product Price` as a raw local-currency amount
with no currency code. Over 2026-08-04 to 08-24 its values include `19900` and
`24900` beside `19.99` and `69.99`; twelve such rows add roughly $258,800 to a sum
whose true scale is five figures, which is enough to reorder the arms. Take revenue
from `revenue` on the backend `AI Subscription Paid` event, which is denominated
consistently. The board's `Total Purchase Revenue - Weekly` and `Average Revenue Per
Paid User - Weekly` tiles are built on `Product Price` and carry this fault.

**8. Plan period is clean on the backend event and dirty on the client one.**
`period` on `AI Subscription Paid` takes exactly `month` and `year`. `Product Period`
on `AI Subscription Purchased` carries `month`, `Monthly`, `year` and `Yearly`, so a
filter written for one casing drops the other. Compute annual share from `period` on
`AI Subscription Paid`. `Product Period` does not exist on `AI Subscription Paid` and
breaks down as all-undefined there.

**9. `AI Subscription Cancelled` and `AI Subscription Paid Cancelled` are the same
event.** Both names return identical unique-user counts — 97 and 101 across the two
`bookmarkChat` arms over 2026-08-04 to 08-24. The boards use the shorter name. When
two readouts of "the same" metric disagree, the cause is almost always a difference
in filters, window or funnel gating rather than the event name; prove the filter is
the cause before rewriting a definition.

**10. Assignment can sit downstream of registration, which invalidates every
registration-denominated rate.** Where an experiment tags users at a surface they
reach after signing up — a post-subscribe upsell, a webview paywall — registrations
are upstream of exposure and cannot be a denominator. The symptom is an arm-size
imbalance that never closes, and the consequence is a manufactured lift: an arm with
fewer registrations and the same numerator reads as a win with no product effect. One
Aura experiment crossed 95% on trial start rate this way while every metric computed
downstream of exposure stayed flat. Establish where assignment happens before
choosing the denominator, and prefer a denominator at or below the assignment point.

**12. An activity count is not an assignment count.** Sizing an arm by unique users on
any event counts who was *active*, not who was *assigned*. On an experiment targeting
existing or deliberately inactive users it undercounts the control arm, because the
treatment arm receives an action that fires events while control does nothing. One Stripe
renewal experiment read 79 against 39 on a four-day window, 79 against 54 on ninety days,
and 79 against 77 on the profile count — the first was published as a sample ratio mismatch
at p=0.0002 and there was no mismatch. Size these arms from a profile count; through the
MCP, widen the window first and watch whether the gap closes, because a gap that shrinks as
the window grows is an activity artifact.

**13. Never normalise to a per-hundred base when arms are unequal.** "Per 100 registrations"
hides the imbalance behind a derived figure and invites a comparison the sample does not
support. Report the real arm counts and let the rates carry the comparison.

**14. A locale scope is a fact to verify, not an assumption to carry.** Check `targetLocale`
on the experiment record, the decision in the thread that launched it, and the data. On
`mobileYearlyPricing2` all three said every country — `targetLocale` null, an explicit
decision to match a web test that ran everywhere, and the test price charged across seven
countries with 41% of those charges outside the United States. Scoping that analysis to one
country would have discarded 57% of the sample. The country value is also
representation-dependent: the raw segmentation API stores the two-letter code while a
breakdown through the MCP returns the display name.

**11. Plan cadence resolves only for the `ai_*` product ids.** Stripe `price_*`
product ids, which most Aurie payers carry, have no cadence mapping, so annual share
covers known-cadence payers only. Report the unknown count alongside it and read the
share as directional.

## Confidence

Two-proportion z-test, reported only where the metric is a proportion. Ratios
of counts to users — sessions per user, registration counts, revenue per paid user —
get no confidence figure; computing one needs per-user values, not aggregates.
Retention gets none either: daily and weekly cohorts overlap, so the same user
appears in several and the independence a z-test assumes does not hold. Report all
of these as directional and say so.

## Denominators are part of the metric

The same numerator over registrations versus over paywall views gives different
answers to different questions, and both are legitimate. Rates are comparable
only against the same denominator. Where a denominator differs from the daily
monitor's, name the difference rather than reconciling the numbers.
