---
name: monitor-all-experiments
description: >
  Daily monitor for every A/B experiment currently RUNNING across Aurie Mobile, Aura Mobile
  and Aura Web. Resolves the running set from the Aura API rather than spreadsheet cell
  colours, computes each experiment's primary conversion rate, guardrail and supporting
  metrics from Mixpanel with the correct per-app event contract, pulls mechanism metrics
  implied by each Linear ticket, republishes the shared Artifact dashboard in place, and
  closes with a mandatory retro so the skill improves every run. Trigger on "monitor all
  experiments", "run the experiment monitor", "check all running experiments", "which
  experiments need my attention", "what is running right now", "daily experiment monitor",
  "experiment status check", "update the experiment dashboard", "refresh the experiment
  artifact", "how are the live experiments doing", "any experiment need a decision", or "give
  me the experiment board". Do NOT use for an ENDED experiment's readout
  (`data:analyze-experiment`) or toggling one (`eng:set-user-experiment`).

metadata:
  version: "1.13.0"
knowledge:
  reads:
    - Guide/Index
    - Tools/Mixpanel
    - Tools/Aura API
    - Tools/Slack
    - Tools/Linear
    - Tools/Google Sheets
    - Learnings/Experiments
---

# Monitor all experiments

## Goal

One readout, and one refreshed dashboard, covering every experiment currently
splitting traffic — with each experiment judged on the metric its own change is
supposed to move, and every reading carrying the denominator and confidence it
was computed from.

## What this skill owns, and what it does not

| Owns | Does not own |
|---|---|
| Experiments still running (`endedAt: null`) | Ended experiments — `data:analyze-experiment` |
| The live Artifact dashboard | `Learnings/Experiments` rows — `data:analyze-experiment` |
| Per-run status, verdicts, decision queue | Notion catalog import — `data:sync-experiments` |
| Its own gradual improvement (Step 8) | Turning experiments on/off — `eng:set-user-experiment` |

## Inputs

Nothing required. Optional narrowing:

- **A surface** — "monitor the Aura Web experiments".
- **One experiment** — "monitor `aiIncreasePlusPricing`". Still runs the full
  contract, just scoped.
- **`--no-publish`** — compute and report, skip the dashboard republish.

## Workflow

### Step 0 — Load the metric contract

Read [`references/metric-definitions.md`](references/metric-definitions.md)
before writing a single query. It carries the per-app event names, the
denominator for each rate, the funnel windows, the rule for segmenting by
experiment, and fourteen traps that silently return wrong numbers. Do not infer
any of it from event names.

Pull `Learnings/Experiments` (recent rows) for cross-experiment priors — an
experiment repeating an idea that already lost elsewhere is worth saying so.

### Step 1 — Resolve the running set (deterministic)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/monitor-all-experiments/scripts/resolve_running.py --json
```

The script fetches the whole `GET /experiments` catalog in a single
`?limit=10000` request, keeps rows with `endedAt: null` and `deletedAt: null`,
and classifies each into a **surface**. This is the authoritative running set.
It leaves out feature flags (a record with `featureFlag: true`, or whose `notes` (or `description`) starts with "FEATURE FLAG") and prints `skipped.featureFlag N: <keys>` on stderr;
report that count, and never analyse those keys.

**Surface, not app, selects the metric contract.** `targetApp` alone is not
enough: `ai` covers both Aurie Mobile and Aurie Web, and the contract only
covers the former. Surface is `targetApp` plus `targetPlatform` — the same
mapping `data:sync-experiments` uses — and `--surface` filters on it. Because
`targetPlatform` is not always populated (`aurieNoTrialWeb` is a web experiment
with a null platform), a `web`-prefixed or `-web`-suffixed id counts as a second
signal. Classify from the API first and use the web sheet tabs as a third
check; never let sheet absence decide that something is mobile.

`?limit=N` is the only query knob that reliably returns everything. Walking
`?page=` drops experiments — the catalog contains repeated pages, so a loop
that stops on the first page with no unseen ids misses every page after it.

**Never derive the running set from spreadsheet cell colours.** The colour is
not readable through the Sheets API, and a stale sheet has repeatedly
disagreed with the API. The API is source of truth; the sheets supply
description, type and variant labels only.

**`endedAt: null` is necessary but not sufficient.** Two further gates:

- **`active` must be multi-letter.** A single-letter `active` (`a`, `b`, `c`)
  means the app ships that one variant — traffic is not splitting and the
  experiment is over in everything but the record. Several hundred legacy Aura
  rows sit in this state.
- **The arms must have users.** An experiment can carry `active: ab` and serve
  nobody. Query assigned users per arm before analysing; anything in single
  digits gets classified, not measured — but read it against the release date
  below before calling it unramped, because a build that shipped yesterday
  looks identical to one that never shipped.

**`minVersion` gates on the version a user joined on, not the build they run.**
ExperimentService compares it to `aiJoinedVersion` (Aurie) or `joinedVersion` (Aura),
so a feature experiment that existing users reach assigns only users who signed up on
or after that build, and everyone who joined earlier sits in no arm. For every live
experiment whose surface existing users reach, break the last seven days of the app's
active-user event down by the `Experiment: <name>` user property, and where more than
about a quarter of active users carry no value, cross them by the app's joined-version
user property:

| App | Active-user event | Joined-version user property |
|---|---|---|
| Aurie | `AI Conversation Session Started` | `AI Joined Version` |
| Aura Mobile | `Meditation done single` with event `mp_lib` in `iphone`, `android` | `Joined version` (lowercase v) |

`Meditation done single` also fires from Aurie (`mp_lib = swift`) and Aura Web
(`mp_lib = web`), and those users carry no Aura Mobile assignment, so the Aura count
is scoped to the Aura mobile libraries before the unassigned share is read.

Report the experiment as testing new users only, with the unassigned count. Onboarding
and first-paywall experiments are new-user by design and skip this check.

**The start date is the release date, not `createdAt`.** An experiment gated on
`minVersion` serves nobody until that build reaches users, so `createdAt` — the
moment the record was created — dates the row rather than the run. Resolve every
start date from the release announcements in `#releases` (`C0B056LKRAB`), where
each release lists the experiments it carries by `ab:`/`abc:` prefix, and fall
back to the sheet's start date only when no release names the experiment.
Invoke [`shared:read-slack`](../../../shared/skills/read-slack/SKILL.md) for the
history rather than calling Slack directly:

```
shared:read-slack history --channel C0B056LKRAB --newest 40
```

Two failures follow from skipping this, and they point opposite ways. Dating
`mobileYearlyPricing` from its `createdAt` of 2026-07-08 rather than the Aura
v3.19.140 release on 2026-07-30 put it at day 42 instead of day 20, halving its
apparent accrual rate and doubling the projected time to significance. In the
other direction, `aiTestimonials` and `aiCtaArrow` shipped in Aurie v1.27.4 on
2026-08-19 and carried 17 and 9 assigned users the next morning, which reads as
defined-but-not-ramped when it is simply day 0.

The release notes also carry two things the sheets do not: which experiments a
release **retired** by baking a winner in, which explains single-letter `active`
rows still sitting at `endedAt: null`, and bug entries that bear on
interpretation — Aurie v1.25.12 records the Apple yearly SKU still at \$69.99
against Stripe's \$79.99, which is why a price experiment reads differently per
payment rail.

### Read all four sheets, and diff in both directions

| Surface | Spreadsheet | Tab | Measured |
|---|---|---|---|
| Aurie Mobile | `1XsxniLks_r5wELsrcFjujZha1qeMAnFuMQTprLXoFB8` | `Aurie Mobile` | yes |
| Aurie Web | `1XsxniLks_r5wELsrcFjujZha1qeMAnFuMQTprLXoFB8` | `Aurie Web` | **diff only** |
| Aura Mobile | `18IH4SsYzK9QbJvbcmE4uFq7EjgR46fML1L2rbUiQS0I` | `Mobile (2026)` | yes |
| Aura Web | `18IH4SsYzK9QbJvbcmE4uFq7EjgR46fML1L2rbUiQS0I` | `Web app (2026)` | yes |

**Nothing on the `aurie-web` surface is measured.** The metric contract covers
Aurie Mobile and Aura only, and the Aurie client events it names are scoped to
`Source App = Aurie Mobile` — the sole value that event carries. Applying that
contract to a web experiment returns empty or mis-scoped rates.

The rule attaches to the **surface from Step 1**, not to whether a sheet row
exists. An Aurie Web experiment missing from a stale sheet is still Aurie Web,
and the diff branch below that says "report it and analyse it" does not override
this. Report every `aurie-web` row as **detected, not measured**, say the
contract does not cover it, and do not substitute the Aurie Mobile scope to
produce a number.

Both directions of the diff are findings, and the second one is the dangerous
one:

- **In the API, absent from a sheet** → an experiment nobody is tracking.
  Report it, and analyse it **if its surface is covered by the contract**. An
  `aurie-web` row stays reported-not-measured however it was found.
- **On a sheet as ended, still splitting in the API** → a *zombie*: the team
  has written a result and moved on while users keep being served a variant
  under nobody's supervision.

  Confirm it by counting **new** assignees, and define new by the registration
  event firing **inside the window** — `AI Sign Up Login` for Aurie Mobile,
  `Sign up login` for Aura Mobile and Aura Web. A funnel from registration to
  the outcome does this natively. Tenure properties do not: `AI Day N` exists
  only on Aurie, so filtering on it makes every Aura zombie look inactive and
  skips exactly the check this step exists for. It can corroborate an Aurie
  Mobile result; it must never be the only method.

  If both arms carry fresh registrations, assignment is ongoing and the sheet's
  readout describes a different population from the one being served today.
  Measure the experiment on that restricted cohort and flag it for shutdown.

  **An Aurie Web row cannot be confirmed or measured this way.** Its
  registrations do not arrive on `AI Sign Up Login`, which carries `Source App
  = Aurie Mobile` and nothing else, and the contract has no web event set. So a
  zombie the Aurie Web diff surfaces is reported as **detected, unconfirmed**:
  name it, say the API still splits it while the sheet calls it ended, and say
  the skill cannot size it. Reaching for the mobile registration event or the
  mobile scope to fill that gap produces a number about the wrong population —
  worse than reporting none.

A zombie's original readout is not evidence about current traffic. Where the
contract covers the surface, recompute on the restricted cohort. Where it does
not — Aurie Web — report the readout as superseded and leave it unsized. Either
way, do not carry the old conclusion forward as if it still described what users
are being served.

### Step 2 — Confirm exposure quality

For each experiment, one event-level query proves the `Experiment: <name>`
property attaches at the experimental surface and not at sign-up. Filter with
`resource: event` and meter the registration event: a non-trivial count means
the property is being stamped pre-exposure and every rate below it is diluted.

Skip only for experiments whose change lands **before** registration. Those
need an event-level cut on the onboarding events instead — a user-property cut
cannot see anyone who never registered, so the population is truncated and
registration itself is not a valid outcome. Say so in the readout.

### Step 2b — Derive each experiment's metric plan before querying

Every experiment is judged on its own plan — decision metrics, exposure gate,
mechanism, population, guardrail and call rule, each with its source — derived
before the first query from what the change actually is, and carried on its
dashboard row between runs. Recover the stored `plan` with the dashboard's `D`
array and reuse it; derive a plan for any experiment that has none, and change a
field only when a new source arrives. A `population` still marked provisional is
re-derived on every run until the experiment's seventh day, then persists.

Derive it by answering the six questions in
[`references/metric-plans.md`](references/metric-plans.md) — who is assigned and
where, where the change is seen, what it literally does, which event proves
exposure, what exists in every arm, and what the call rule is. The method is the
same for an experiment launched today as for one that has run for a month; the
reference holds no per-experiment rules. Then check each field against the
record — Yawar's guidance in `#abtest-monitoring`, the Linear ticket, the sheet
row — and let the record win where it speaks. On an experiment's first run, print
the derived plan in the readout so it can be corrected before it decides
anything.

Where the daily Experiment Monitor's plan names a different decision metric or
gate for the same experiment, report both and name the stronger source.

### Step 3 — Compute the metric set

Every experiment gets a **revenue per exposed user** figure, taken from the
`Premium Revenue` user property and denominated at the exposure point — the paywall event
for a paywall change, the feature's entry event for a feature change. A conversion-only
readout cannot distinguish a variant that sold more from one that sold better. The rule, the
summing method and the plan-mix trap it avoids are in
[`references/metric-definitions.md`](references/metric-definitions.md).

Per-app contracts and the rules governing the core metric set live in
[`references/metric-definitions.md`](references/metric-definitions.md). Every
experiment gets, in this order: **the plan's decision metrics, computed from its
gate → the app's core metrics → exposure → second-class context**.

**Every experiment targeting new users carries the app's full core set on every
run**, whatever its plan — the plan decides which metrics drive the call, never
which core metrics get computed. An experiment is existing-user only when its
catalog flag says so (`preventNewUsers: true`), its name carries an `OldUsers`,
`ExistingUsers` or `Existing` token, or none of its assigned users registered on or
after its start date across its first seven days — the order and the tie-break are
in
[`references/metric-plans.md`](references/metric-plans.md). Existing-user
experiments skip the acquisition steps (trial start, paid start) as not
applicable; every other experiment is a new-user experiment. A core metric that
sits upstream of the plan's gate is labelled context, not dropped. Key metrics belong in the primary
tier, shown even at low n with n stated; a surface only some arms have is
descriptive for those arms and never compared. Report each metric with its
numerator, denominator and a two-proportion confidence, except the ratios and
retention figures the contract marks directional. A rate whose denominator is not
stated is not reportable, and a denominator upstream of the assignment point is not
a denominator.

Exclude internal accounts on the `Internal` **user** property. Cut each experiment
on its `Experiment: <name>` **user** property — the event-level property of the same
name reaches only part of the population and is absent from the backend subscription
events entirely. The reasoning is in the metric contract.

Where a verdict rests on a figure the team will check against the Mixpanel boards,
reconcile it and report the mechanical cause of any gap:
[`references/dashboard-reconciliation.md`](references/dashboard-reconciliation.md).
Board numbers and skill numbers differ by a known offset that leaves lifts intact
and shifts absolute rates.

**Every run recomputes every live row's metrics from Mixpanel.** A row's `plan`
carries forward between runs; its `mm` never does. A row whose numbers come from an
earlier run, or from the `#abtest-monitoring` monitor, shows the experiment as it was
on that day while its verdict describes today, and the two drift apart without any
error. Replace each metric in place rather than stacking a dated copy beside it: the
day-over-day trail lives in the history array (Step 7). A monitor reading enters a row
only once this skill's own query reproduces it; one that cannot be reproduced (the
monitor's Day 2 return, say) carries the monitor and its date in the metric name.

Existing-subscriber and cancel-flow experiments carry their own metric set (trial
cancel, cancel after the offer screen, refunds), defined in the metric contract.
Registrations and any-event user counts are not a reading for them.

### Step 4 — Add the mechanism metric from the ticket

Read each experiment's Linear ticket and ask what the change is *supposed* to
move. That metric goes in the readout in the primary tier, even when no sibling
experiment has it, and it comes with the spillover metric for the surface it touches
and the breakdown dimension the effect would run through.
A change with no metric for the thing it literally changes cannot distinguish
"no effect" from "never shipped".

**This matters most before day 7, not least.** A young experiment has no readable
conversion rate, so the mechanism metric is the only thing about it that can be
read at all. A row carrying nothing but a registration count says the experiment
is assigning and nothing about whether the variant renders — "too early" and
"shipped broken" look identical on it, and stay identical until day 14 when the
primary finally resolves into a null that nobody can interpret. Every experiment
gets a mechanism reading on its first run, however small the sample.

Confirm the mechanism event is firing **project-wide** before reading it per arm.
An event at zero across the whole project is an instrumentation fault, not an
arm difference, and it needs a wider window to classify: an event with historical
volume that has gone silent is a regression shipped with the change, while one
that has never fired was never wired up. Both are findings; they go to different
people.

Worked examples, and the Mixpanel event families to draw from:
[`references/mechanism-metrics.md`](references/mechanism-metrics.md).

### Step 5 — Assign a status, and say what the rule was

| Status | Rule |
|---|---|
| Needs a decision | Actionable: day 14+ and settled, or a mechanism already clear enough that more time will not change the call |
| Watch | Directional but unsettled |
| Too early | Before day 14 with nothing above threshold |

Every `Needs a decision` row carries one reason — **ran too long, neutral**,
**metrics significant**, **possible bug** or **already decided, still live** —
and the call follows the general decision rules: consistent harm at ≥90% across
reads is a turn-off before 95%, % annual decides price and paywall tests, a tie
after about 30 days is a preference call, and a bug-fix experiment ships unless it
is significantly harmful. The full set is in
[`references/metric-plans.md`](references/metric-plans.md). A `possible bug` row
names its owner and the one check that settles it.

Day counts run from the release date resolved in Step 1, not from `createdAt`.

Flag arm balance for every experiment (chi-square against equal split). A
sample ratio mismatch makes every rate on that experiment provisional.

Compare against the `#abtest-monitoring` daily monitor by **invoking
[`shared:read-slack`](../../../shared/skills/read-slack/SKILL.md)** with a bound
— that skill owns the connection, so don't call Slack directly and don't
paginate the channel:

```
shared:read-slack history --channel C05TX32DJVA --newest 20
```

The monitor posts one Aurie and one Aura summary per day with the detail in
thread replies, so twenty messages covers about a week. Read the latest Aurie and
Aura threads: their replies carry the monitor's counts per experiment, and every row
this skill also measures is reconciled against them. The monitor applies a cohort
exclusion this skill cannot, so its counts run about 2–4% lower with lifts intact. A
larger gap, or a lift in the opposite direction, is a definition difference to name
before publishing.

Where this skill's status disagrees with the monitor, say so and name which read
is more conservative. Never silently overwrite the other verdict. The two use
different denominators and, on Aura, sometimes different populations, so a
disagreement is usually a definition difference rather than an error.

### Step 6 — Show the plan before publishing

Before touching the dashboard, print: the experiments in scope, each status,
each primary reading, and anything that changed status since the last run.
Wait for approval. The republish is a side effect on a surface the team reads.

### Step 7 — Republish the dashboard in place

The data shape, the tier rules, the multi-arm `order` field and the run-history
contract are in
[`references/dashboard-contract.md`](references/dashboard-contract.md).

Republish to the **same file path** so the URL survives; from a session that
did not publish it, pass the existing URL. A fresh URL breaks every link
already circulated.

Append this run to the dashboard's history array rather than overwriting it.
The day-over-day confidence trail is what distinguishes a real effect from a
decaying one, and it only exists if every run adds to it.

Record on every row the arm its lift was computed against. A trail that stores
only `{metric, lift, confidence}` renders one arm's result under another arm's
panel on any multi-arm experiment, which reads as a finding about the wrong
variant.

### Step 8 — Retro (mandatory, never skipped)

The run is not finished when the dashboard is published. Close every run by
working through [`references/retro.md`](references/retro.md), which asks four
questions:

1. **What did this run get wrong?** Compare today's readings against the
   stored history. A metric that moved a lot without new data means a
   definition changed, not the product.
2. **What did the contract miss?** Any metric computed from an event or
   property not described in `references/metric-definitions.md` is an
   undocumented dependency — the next run will not reproduce it.
3. **What was decided on incomplete evidence?** Name any verdict that rested
   on a metric flagged lagged, unscoped or below threshold.
4. **What should change in this skill?** One concrete, minimal edit.

Report the retro in chat every run. When it finds a **repeatable** defect —
one that would recur on the next run rather than a one-off — route the fix
through `shared:update-aura-skill`, which owns the version bump and the PR.
A retro that produces no finding for several consecutive runs is itself a
finding: the checks have gone stale and need sharpening.

## Output

1. A decision queue — one line per experiment needing a call: the experiment,
   the action (turn off, turn on arm X, ship X), its reason, and one clause of
   evidence.
2. One block per decision first, then one block per remaining experiment:
   status, day, the plan's decision metrics with their gate, guardrail and
   mechanism metrics with denominators and confidence, arm balance, and a
   verdict. Experiments with nothing to decide get one short line each.
3. Cross-cutting patterns across the batch.
4. The dashboard URL.
5. The Step 8 retro, always.

## Edge cases

- **Mixpanel MCP unavailable** → fall back to the REST segmentation API with
  the service-account credentials; if neither is reachable, stop and say so
  rather than reporting a partial board as complete.
- **An experiment has no Linear ticket** → derive the mechanism from the
  experiment id, its API `notes` field and the release announcement that carried
  it, and flag the missing ticket. Where those do not identify a surface either,
  the row still carries a mechanism entry marked `uninstrumented` per the
  dashboard contract, naming what could not be determined. A missing ticket
  never downgrades a row to registrations only; do not invent a metric to fill
  the gap.
- **Two experiments return identical aggregates** → cross-tab their properties
  before reporting. Identical numbers are either coincidence on overlapping
  populations or collinear assignment, and only the cross-tab distinguishes
  them.
- **Concurrent experiments on one surface** → arms are not independent and
  effects may compound. Report the overlap; do not present the arms as clean.
- **Re-run in the same day** → recompute and republish to the same URL,
  replacing that day's history entry rather than appending a duplicate.

## References

- [`references/metric-definitions.md`](references/metric-definitions.md) — per-app event contract, denominators, windows, traps.
- [`references/metric-plans.md`](references/metric-plans.md) — deriving each experiment's plan: the six questions, checking against the record, core metrics on new-user experiments, decision reasons and rules, the pre-gate balance check.
- [`references/mechanism-metrics.md`](references/mechanism-metrics.md) — deriving the per-experiment metric from its ticket.
- [`references/dashboard-contract.md`](references/dashboard-contract.md) — Artifact data shape, tiers, history.
- [`references/dashboard-reconciliation.md`](references/dashboard-reconciliation.md) — matching the Mixpanel boards: report ids, what the API exposes, the cohort-exclusion offset.
- [`references/retro.md`](references/retro.md) — the Step 8 checklist and known defect classes.
