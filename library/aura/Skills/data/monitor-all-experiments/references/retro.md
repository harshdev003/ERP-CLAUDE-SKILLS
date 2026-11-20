# Step 8 — the retro

Every run ends here. The point is not to praise the run; it is to find the one
thing that would make the next run more correct, and to make that change cheap
enough that it actually happens.

## The four questions

**1. What did this run get wrong?**

Compare today's readings against the stored history. A metric that shifted
sharply without a corresponding change in sample is a definition change, not a
product change — find which. A signal that was above threshold last run and is
not now was probably noise; say so, and if it was reported as a finding,
withdraw it by name.

**2. What did the contract miss?**

List every event and property used this run. Any one not described in
`metric-definitions.md` or `mechanism-metrics.md` is an undocumented
dependency: the next run will not reproduce it, and a reader cannot audit it.
Either add it to the reference or stop using it.

**3. What was decided on incomplete evidence?**

Name every verdict that rested on a metric flagged lagged or unscoped, on a
denominator below threshold, or on an experiment with a sample ratio mismatch.
A verdict is allowed to rest on thin evidence — it is not allowed to do so
silently.

**4. What should change in this skill?**

One concrete, minimal edit. "Be more careful" is not an edit. "Add the trial
flag trap to `metric-definitions.md`" is.

## Routing the finding

| Finding | Action |
|---|---|
| Would recur next run | Route to `shared:update-aura-skill` with the specific edit |
| One-off, environment or data hiccup | Report in chat, no PR |
| Instrumentation gap in the product | Report it; the fix is a product ticket, not a skill change |
| Disagreement with the daily monitor | Report both readings and which is more conservative; never silently overwrite |
| Names a defect that is still live | The entry carries a card link or a closure note — a retro entry is not a record |

**A retro entry that names a live defect is not a record of it.** A retro is read
by a human, later, about that one run; a card is worked. The
`testAurieOnboarding` entry below is the case — the correct guard was written
here and the row went on assigning real users for months. So an entry naming
something still live carries **either** a card link **or** a closure note saying
it is fixed and where. Neither present means this entry is the only place the
defect exists, which is the failure the entry is describing. See CLAUDE.md,
*A finding about the tool belongs in the tool, not in the output*.

## Known defect classes

Patterns already observed. Check each run against them.

**Metric built on the wrong event.** A plausible event name that is not the one
the team's dashboards use. Symptom: numbers that disagree with the board by a
wide margin. Guard: the contract reference, not intuition.

**A filter that silently does nothing.** A property absent on the events being
filtered passes without error and excludes nothing. Symptom: filtered and
unfiltered counts identical. Guard: verify a filter changes the number before
trusting it.

**An outlier-sensitive aggregate.** A handful of malformed records moving a
total by tens of percent. Guard: check the value distribution before reporting
any sum.

**A signal that decays.** High confidence on a small denominator that softens
every day. Guard: the history trail, and a minimum-runtime rule.

**Selection on the maximum.** Picking each experiment's worst or best arm and
then remarking that they all point one way. Guard: state the selection rule
when reporting a cross-experiment pattern.

**A stated rule the data does not follow.** Printing a status rule while
assigning statuses by judgement. Guard: either derive the status from the rule
or say plainly that it is analyst-assigned.

**An exposure-only row.** A young experiment reported with a registration count
and nothing else. It reads as "too early", which is indistinguishable from a
variant that never rendered, and the ambiguity survives to day 14. Guard: every
experiment gets a mechanism reading on its first run, and an absent mechanism
event is reported as an instrumentation gap.

**A whole class of records excluded by name.** Rows dismissed as scaffolding from
their ids — `testExp1`, `targetAppTest9`, `testAurieOnboarding` — and dropped from
the board without being queried. One of the three was assigning real users to a
live onboarding split seven months on. Guard: classification is a query result,
not a reading of the name. Status: the row was ended
([PROD-6200](https://linear.app/aura-health/issue/PROD-6200)); the mechanism that
served an Aurie experiment to the entire Aura intake is open at
[ENG-1333](https://linear.app/aura-health/issue/ENG-1333).

**Revenue read on the wrong basis.** A revenue figure taken from an event `revenue`
property rather than from `Premium Revenue`. Symptom: a lift that shrinks by half when
recomputed on whole-plan value, because plan mix differs from transaction value. Guard: the
revenue rule at the top of `metric-definitions.md`, and a check that the reported share of
buyers the change reaches is stated.

**An activity count standing in for an assignment count.** Symptom: a sample ratio mismatch
on an experiment whose treatment arm receives an action and whose control arm does not.
Guard: widen the window and see whether the gap closes before reporting an imbalance.

**A derived presentation base.** Normalising unequal arms to a per-hundred figure. Symptom:
a clean-looking comparison whose underlying arms differ by more than the effect. Guard:
report real arm counts.

**Coverage that varies by experiment.** Computing a metric for some experiments
and not others, then presenting one table. Guard: if a metric is missing
somewhere, say where.

**The general set standing in for the plan.** An experiment read on the app's
default rates while its change sits behind a gate, after the paywall or on
existing users — a done-screen change judged on per-registration paid start, an
existing-user test judged on trial start. Symptom: a verdict that contradicts
Yawar's own read of the same experiment in `#abtest-monitoring`. Guard: derive
the plan by the six questions in `metric-plans.md` and check it against the
record; ask each run whether any verdict rested on a metric its plan marks as
context. When a correction to a plan would have been caught by a better question,
the fix belongs in the method, not in a note about that experiment.

**A gate-reach gap read as a product effect.** A significant difference in how
often arms reach an exposure gate the arms share unchanged. Guard: the pre-gate
balance check in `metric-plans.md` — explain the gap before reading either arm.

**An unvalidated statistical helper.** A hand-written normal-CDF or
significance function that looks right and is wrong. An extra factor in a
polynomial approximation inflated every confidence figure on the dashboard —
57.4% rendered as 64% — and nothing about the output looked anomalous. Guard:
assert the helper against known values (Φ(0)=0.5, Φ(1)=0.8413, Φ(1.645)=0.95,
Φ(1.96)=0.975, Φ(2.576)=0.995) and cross-check at least three live figures
against an independent implementation. Do this whenever the helper changes, not
once.

**A subagent conclusion accepted without audit.** A parallel agent inferred from
a five-user experiment that the internal-account filter also drops undefined
values, which would have meant every population count was an undercount. A
direct breakdown disproved it — the filter keeps undefined and drops only the
genuinely flagged accounts. Guard: a subagent's *counts* are data; its
*inferences* are hypotheses. Re-derive any inference that would change a
denominator before acting on it.

**A row carried forward.** Metric rows kept from an earlier run while only the
verdict text is rewritten from the monitor. Symptom: day-0 counts on a day-30
experiment, several dated snapshots of one metric stacked in a row, or a verdict the
row's own numbers contradict. Guard: every run recomputes every live row's `mm`
(Step 3), and a monitor claim enters a row only after a query reproduces it.

**A version gate read as low traffic.** A feature experiment whose `minVersion`
excludes every user who joined on an older build, read as a small or slow experiment.
Guard: the joined-version check in Step 1.

## When the retro finds nothing

Several consecutive clean retros mean the checks have gone stale, not that the
skill is finished. Sharpen one: add a defect class from a recent surprise, or
tighten a threshold. Record that as the run's finding.
