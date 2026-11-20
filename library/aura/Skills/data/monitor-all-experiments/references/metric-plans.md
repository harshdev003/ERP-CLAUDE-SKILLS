# Deriving each experiment's metric plan

The metric contract in `metric-definitions.md` is the floor every experiment
shares. On top of it, each experiment is judged on a **plan** derived from what
its change actually is: what decides it, what shows the change reached anyone,
who counts, and what must not break. The plan comes from a method — the same six
questions answered for every experiment, including one launched today — never
from a list of past experiments. A method produces proper metrics for an
experiment nobody has seen before; a list only covers the ones already on it.

## Plan fields

| Field | Content |
|---|---|
| `decision` | One to three metrics that decide the call, each written as a funnel or rate with exact event names, window and filters |
| `gate` | The exposure event the decision metrics start from, with its filter — always a named event: the registration event when the change is seen at sign-up, the paywall event (`AI Subscription Modal Seen`, `Subscription Modal Open`) when it is seen at the paywall |
| `mechanism` | The event that proves the change rendered or was used |
| `population` | Denominator and cut: new or existing users, paid-only, US or all-country |
| `guardrail` | One metric that must not break |
| `rule` | What result turns this experiment on or off |
| `src` | Where each field came from: `D` (derived by the method), `Y <date>` (Yawar's guidance, with link), `L <ticket>`, `S` (sheet row) |

## The six questions

**1. Who is assigned, and where does assignment land?** New or existing users,
read from three signals in order:

1. The catalog row's audience flags, which `resolve_running.py` emits:
   `preventNewUsers: true` means existing users only; `preventOldUsers: true`
   means new users only; neither set means new users are bucketed alongside
   existing ones.
2. The experiment's name: an `OldUsers` or `ExistingUsers` token, or a
   camel-case `Existing` token (`stripeExisting…`), marks an existing-user
   experiment.
3. The assigned population itself: when **no** assigned user registered on or
   after the start date — the registration event firing inside the window,
   launch day included — across the experiment's first seven days, it is
   existing-only whatever the record says. This catches a renewal or
   CRM-assigned experiment that carries neither a flag nor a token. Fresh
   registrations in the arms make it a new-user experiment, mixed experiments
   included — pre-start registrants sit in every experiment that buckets
   existing users alongside new ones. Before seven days of registrations exist,
   this signal cannot mark an experiment existing-only: treat it as new-user and
   re-check on each run, so a plan written on day 0 never freezes the wrong
   call.

An experiment is a new-user experiment unless a signal says existing-only. Where
the signals disagree, report the disagreement and let the population settle it:
any fresh registrations in the arms mean new-user, none mean existing-only. Then
note where the assignment lands: before sign-up, at registration, at the paywall,
after paying, or at a feature. That fixes the population and which core metrics
apply.

**2. Where is the change seen?** The event for that surface is the gate. Decision
metrics start at the gate; a metric upstream of it is context, because users who
never reached the gate could not have been affected. When assignment lands after
payment, a registration- or modal-based funnel shows payers outnumbering
registrations — that is the population, not an instrumentation fault (trap 10 in
`metric-definitions.md`), and it disappears on a funnel that starts from payment.

The gate must exist in **every** arm. When the treatment removes, adds or
reshapes the surface — a done screen dropped in the variant — the surface's own
event fires in one arm only, and cohorting on it compares control users who
reached the screen against a variant that has no screen. Take instead the **last
event both arms share before the branch**: the session completion that precedes
the done screen, not the done screen itself. Never cohort on an event the
treatment removes or whose rate it changes.

Two cuts then hang off that shared gate:

- **Paid rate**, when the change can be seen before paying: keep only users who
  had not paid when they reached the gate — on Aurie, the gate event filtered to
  `Premium AI = False` — and measure payment afterwards. A user who paid before
  ever meeting the change cannot show its effect on paying.
- **Cancellation**: payers who went through the experience, or plausibly could
  have, at least once — counted from the first qualifying point. Use the attempt
  or reach marker when it fires alike in every arm; fall back to eligibility when
  the treatment itself changes whether users reach the experience.

**3. What does the change literally do?** The shape of the change picks the
decision metrics:

| Change | Decision metrics | Mechanism |
|---|---|---|
| Price or plan offer for new subscriptions | Revenue per paywall viewer from payers only, % annual, conversion per paywall viewer, and cancellation of the whole subscription — a price on one plan can push users to cancel altogether, which a funnel on the tested plan alone never sees | Payers charged the test price; plan mix |
| Price on renewal for existing subscribers | Net renewal revenue per assigned user with the break-even cancel rate; realised renewal revenue once billed | Cancellations before the renewal date |
| Paywall copy or layout | Conversion per paywall viewer, % annual | Paywall dismissal |
| A surface reached after paying (done screens, post-session flows) | Paid → the last step both arms share before the surface → cancellation at 7 and 30 days, sessions per user, retention | The surface's completion or rating, where every arm has it |
| A feature behind a behavioural gate | Behaviour on the feature's own events after the gate, and post-gate cancellation | Gate reach, feature entry |
| A voice or transport engine | From first voice use: paid, cancellation, return; % annual; cost per minute | Session volume and success; same-configuration arm pairs as the noise floor, already-paid split from free at first use |
| An onboarding screen before sign-up | Registration from onboarding starters, cut at event level; then paid; then cancellation | Screen seen → screen completed |
| A feature for existing users | Cancellation, retention, login or return, engagement; treatment arms compared on the new surface with control as the zero-check | Surface opened, surface tapped |
| A UI element with no event | Paid and cancellation on a proxy population that guarantees the surface was seen, such as a repeat visit to the screen | `uninstrumented` |
| A feature only the treatment arm has | The general metrics over the surface both arms share; adoption is descriptive and never compared | Adoption in the treatment arm |
| A bug fix | Outcomes on the fixed surface | The fixed surface firing |

**4. Which event proves exposure?** Look it up in the lexicon by **exact name** —
a substring search skips hidden events — and meter it project-wide before reading
it per arm. Where no event marks the change, the mechanism is `uninstrumented`,
logged once to the owning engineer. Many experiments change an experience rather
than fire an event of their own; reconstruct who went through it, or tried to,
from the product path both arms share, and never require an event only the
treatment emits.

**5. What can be compared?** Only metrics that exist in every arm. Then the
dimension the effect would run through — voice against text, platform, plan,
entry point — broken down wherever the property exists in all arms.

**6. What is the call rule?** Taken from the decision rules below, stated in
advance so the verdict follows the rule rather than the reading.

## Check the derivation against the record

After deriving the plan, check each field against what the team has already said
about the experiment, and let the record win where it speaks: Yawar's guidance in
`#abtest-monitoring` (`C05TX32DJVA`) first, then the Linear ticket's
pre-registered plan, then the sheet row and its `Experiments dashboard` column.
On an experiment's **first run**, print the derived plan in the readout so it can
be corrected before it decides anything; a correction outranks the derivation
and is recorded in `src`.

The daily Experiment Monitor in `#abtest-monitoring` derives its own plan by the
same method. Where its decision metric or gate differs from this plan, report
both and name the stronger source; never overwrite either silently.

## Persist the plan

The plan is stored on the experiment's dashboard row as `plan` (see
`dashboard-contract.md`). Recover it with the rest of `D` at the start of every
run and reuse it. Change a field only when a new source arrives, and record that
source in `src`. A plan rebuilt from scratch every run drifts back to the general
metrics.

One field is provisional by design: `population`, while the experiment is inside
its first seven days and no catalog flag or name token has settled it. Mark it
`provisional` in `src` and re-derive it on every run until day seven; the
re-derivation is the new source, so a row that turns out existing-only on day
seven is rewritten then, with the day recorded in `src`. After that it persists
like every other field.

## Core metrics stay on every new-user experiment

Every experiment that targets new users carries the **full core set** from its
app's table in `metric-definitions.md` on every run, whatever its plan says. The
plan decides which metrics **drive the call**, never which core metrics get
computed. Where a core metric sits upstream of the plan's gate, it is labelled
context rather than dropped.

Existing-user experiments skip the acquisition steps — trial start and paid
start — as not applicable, and keep cancellation, retention, engagement and
revenue.

## Decision reasons

Every `Needs a decision` row carries exactly one reason, and the readout leads
with it:

| Reason | Holds when |
|---|---|
| **ran too long, neutral** | Day 14 or later (Aura paid metrics on the matured cohort) and flat on every decision metric |
| **metrics significant** | A decision metric at ≥95%, or worse at ≥90% on three consecutive reads — direction stated |
| **possible bug** | Instrumentation impossible on the plan's own funnel, an arm frozen, the mechanism event silent, the treatment surface firing in the control arm, or a pre-gate imbalance |
| **already decided, still live** | Yawar called it in `#abtest-monitoring` and the API still serves it |

A `possible bug` row names the owner and the one check that would settle it.
An `already decided, still live` row stays in the queue for as long as the call
is still being served, whatever the metrics say. It carries an extra note when
the plan's decision metrics now contradict the call; a second-class metric on its
own — retention on a monetisation test — never adds that note or reopens a call.

## Decision rules

- **Consistent harm counts before 95%.** A decision metric worse at ≥90% on three
  consecutive reads is a turn-off.
- **Positive with no downside, after enough runtime, is a turn-on** even below
  95%.
- **% annual decides price and paywall tests** beside conversion; annual plans
  carry more value per subscriber.
- **Tied after about 30 days is a preference call**, or a turn-off when nothing
  is positive.
- **A bug in the variant means turn off and retest.**
- **A bug-fix experiment ships unless it is significantly harmful.**
- **Cost breaks a tie** when product metrics are flat.
- **Counts sit next to every rate.** A lower cancellation count can mean a
  smaller upstream funnel rather than a better arm.
- **US and all-country are shown separately** wherever the experiment's scope is
  not settled by its record.

## Pre-gate balance check

When the arms are identical up to the gate, each arm reaches the gate at the same
rate. A gate-reach gap that holds above 95% across reads therefore has to be
explained before either arm is read: check when the assignment is stamped,
whether the stamp lands on every arm alike, and whether a second event that
claims to mark the same gate agrees. Until it is explained the row is
`possible bug` — not a product effect, and not noise.
