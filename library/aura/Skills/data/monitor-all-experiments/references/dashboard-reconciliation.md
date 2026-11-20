# Reconciling against the Mixpanel boards

The team reads the `Aurie Experiments (Mobile)` and `Aura Experiments (Mobile)`
boards. This skill computes its own numbers. When the two disagree, the readout
is only useful if it can say *why* — this reference is how to find out.

Run a reconciliation when a verdict rests on a number someone will check against
their screen, when a metric moves without a matching change in sample, or when
the caller asks whether a figure matches theirs.

## Board IDs

| Board | ID |
|---|---|
| Aurie Experiments (Mobile) | 11246881 |
| Aura Experiments — Mobile (2026) | 11422584 |
| Aura Experiments — Web app (2026) | 2869659 |

## What the API gives you, and what it does not

`Get-Dashboard` with `include_layout=True` returns the full cell layout, and every
report cell carries its bookmark id, name and description. That is the report
inventory.

`Get-Report` with `skip_results=False` returns each report's **saved output**. The
result key names the events the report actually queries — `"Conversion Rate of AI
Subscription Modal Seen through AI Subscription Paid"` — which is how you recover a
definition.

Neither call returns the query definition itself, and there is no parameter for the
dashboard-level filter, breakdown or time override that a board URL carries. So a
`Get-Report` result is the unfiltered whole-population figure, never the
per-experiment cut. To match a filtered board view, rebuild the query with
`Run-Query`, applying the same filter, breakdown and window by hand.

Read the board's own description before rebuilding. The Aurie board's says: *Scope:
Source App = Aurie Mobile. AI Subscription Paid is a backend event shown
unfiltered.* That is a per-step scope, not a global filter — a global
`Source App` filter would eliminate the backend step and return an empty funnel.

## Descriptions drift from definitions

`Paid Subscriptions - Weekly` (90507523) is described as "Unique AI Subscription
Purchased per week" and queries `AI Subscription Paid`. Trust the result key over
the description; the description is how someone reaches for `Product Price` on the
client event and inherits trap 7.

## The exclusion gap

Board views carry an `Exclude` control holding a cohort exclusion. It cannot be
reproduced through this MCP connection: `List-Cohorts` returns `requires scopes:
cohorts`, so the cohort is neither readable nor applicable as a query filter.

Its effect on Aurie boards is a consistent offset of roughly 8 users per arm on
paid-subscriber denominators and roughly 20 per arm on paywall-seen, in the same
direction in every arm. Skill figures therefore run about 2% above the board's.
Excluding the `Internal` user property closes about a third of that and is not a
substitute.

Lifts and confidences survive the offset because it applies evenly across arms.
Absolute rates do not. Say which is which when reporting a reconciliation, and
never quietly adjust a number to close the gap.

The durable fix is a product change, not a skill change: move the exclusion off a
cohort and onto a user property, so the same rule is expressible in the UI and in a
query. Report it as a finding rather than working around it.

## Reporting a mismatch

State the board figure, the skill figure and the mechanical cause. A mismatch is
one of these, in rough order of likelihood:

1. The board's cohort `Exclude`, per above.
2. A different date window — board views default to rolling ranges, and this skill
   dates each experiment from its release.
3. Internal-account handling, which the boards do not apply as a property filter.
4. Per-step `Source App` scoping on the client event.
5. Funnel gating — a funnel's step-1 count is not the same population as a plain
   unique-user count of the same event.

Metrics on small denominators disagree hardest: a refund rate of 12/367 against
13/375 is one event, and shows up as a 7-point swing in lift. Prefer reporting the
counts over the lift whenever the numerator is below about 30.

## Reports that do not reconcile

`% Of Paid Users Reaching Min 50 Messages` (90510340) has no message event in it.
Its saved output is a formula returning 50, with both operands being *Total Events
of AI Subscription Paid* at 2,636 and 1,318. It measures nothing about messages.
Report it as broken instrumentation and do not reproduce it.

Retention reports (90507525, 90507526, 90507531) use the retention query type and
need `Run-Query` with `report_type: "retention"`, which takes exactly two events —
an initial action and a returning action.
