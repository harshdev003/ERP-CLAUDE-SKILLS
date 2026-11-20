# Confounders

Before any move is attributed to performance, rule out the things that moved it
on purpose or by defect. Step 4 of `SKILL.md` runs this file.

The output of this step is a list, including the confounders **ruled out and
why** — a check that ran and found nothing is worth reporting, because it stops
the same question being asked next week.

## 1. Live experiments

Resolve what was actually splitting traffic during the target week. Do **not**
read experiment status from spreadsheet cell colours.

- **Running set** — the Aura API is the source of truth (`endedAt: null`).
  `data:monitor-all-experiments` already resolves this; reuse its output rather
  than re-deriving.
- **What shipped mid-week** — Linear issues completed inside the window. Search
  the `Product/Design` team for the app's project (`Aura - Web`,
  `Aura - Core App`, `Aurie AI - Mobile`, `Aurie AI - Desktop/Web`) with
  `completedAt` inside the week, and read the `ab:` prefixed ones.

### Price and pricing-mix tests are the loudest confounder

A live price test moves **counts and attach rates by design**. When one is
running:

- The sub **count** and the **attach percentage** are the wrong metrics to read.
- Pair them with **ASP for the same week** — a halved count with a raised ASP is
  the test working, not a regression.
- Say explicitly that the result needs **another one to two weeks** before it can
  be read properly, and that the affected columns are noisy until then.

Worked case: Aura web family subs fell 62 → 33 and family attach fell 25.3% →
15.8% (the lowest all year) in the week of 2026-08-23. Read alone that is an
alarming collapse. `ab:webFamilyPlanPricing` had raised the web family price by
~23% from 2026-08-19–21, and web ASP rose $91.8 → $98.7 in the same week. The
count was the wrong metric.

Related traps in the same family: a **mobile** version of a web test landing on a
different date (so the two surfaces are not comparable within one week), and a
test whose control price has since become the new baseline (which makes a
historical comparison against the old price meaningless).

### Single-variant experiments

An experiment with only one variant active in Firebase emits **no assignment
property**, so the Mixpanel `Experiment: *` property is undefined for it. That is
expected, not an instrumentation gap. Validate the arm from the price or product
ID instead, and never flag the missing tag as a defect.

## 2. Open and recently-closed bugs

Query Linear for the `Bugs` label with `createdAt` inside or just before the
window, then filter to the app's projects. For each candidate, establish three
things before treating it as an explanation:

1. **Which app.** See §3 — this is where most errors happen.
2. **Whether it was actually live during the week.** A ticket *created* during
   the week may describe errors from months earlier. Read the description, not
   the creation date. A backlog investigation opened on the 1st about July
   timeouts explains nothing about the week in question.
3. **Whether it was on a shipped build.** An App Store *rejection* blocks a
   release; the broken build never reached users, so it cannot explain that
   week's numbers. It is still worth a line as a forward-looking risk.

Also sweep the daily funnel-health posts in the app's analytics channel for the
window. Those often carry a same-week diagnosis (a traffic-mix artifact, a
provider outage, an install leak) with a confidence level already attached, and
citing one is cheaper and more honest than re-deriving it.

## 3. Never inherit another product's exclusion window

Aura and Aurie share people, dashboards and a Mixpanel project, and their
automated reports exclude dates for their own defects. **Those exclusions do not
transfer.**

Before applying any excluded date to a number, open the Linear ticket and check
its **`project`** field.

Worked case: an Aurie report excluded 2026-08-27 as "paywall bug day", citing
PROD-5116. That ticket is `aurieCustomWebPaywall` arm a in the
`Aurie AI - Mobile` project — iOS yearly purchases handed a Stripe web price ID.
It has no bearing on Aura's numbers, and carrying the exclusion into an Aura
reading would have dropped a clean day for no reason.

The reverse holds too: an Aura web defect does not license an exclusion in the
Aurie note.

## 4. Measurement changes

Cheapest to miss, because nothing breaks:

- **Filter and cohort changes** the author made to their own reports. If the note
  is about organic and the organic filters were changed recently, the movement
  may be the filter. The author usually knows — ask rather than assume.
- **A filter that silently does nothing.** A property absent from the events being
  filtered excludes nothing and raises no error. Symptom: filtered and unfiltered
  counts are identical. Verify a filter changes the number before trusting it.
- **Internal and test accounts.** Aggregate segmentation often cannot exclude
  them. If a cohort exclusion could not be applied, say so rather than implying
  the number is clean.
- **A definition change in the sheet.** A metric that moves sharply with no
  corresponding change in its denominator is usually a formula edit, not a
  product change. Check the prior weeks of the same column for a step.
- **An incomplete cost store.** Aurie's LLM and voice cost cells come from the
  daily cost store (`eng:store-aurie-user-daily-cost`), which lags a day and is
  re-stored for a date range after a capture defect. A cost column that falls
  while its usage driver rises — voice costs down 20% in a week conversers rose
  15% — is a completeness question before it is a margin finding. Confirm the
  cells were written after the latest re-store; if that cannot be confirmed, the
  post carries the sheet's margin and says the owner is confirming the costs are
  complete, and the card gets a follow-up to restate it.

## 5. Calendar

- **US holidays** inside the window depress weekday-shaped volume; a Labor Day or
  Thanksgiving week is not comparable to its neighbours.
- **A partial week.** Only complete Sun–Sat weeks are reportable. If the tab has a
  partial row, do not include it in the band either.

## 6. Test and QA traffic

An automated end-to-end suite signs up and converts like a real user, and its
events land in whatever bucket has no install source — usually the organic one.
Symptom: one day inside the week carrying several times its normal web signups,
concentrated in `$direct` referrer and absent from every prior week.

On 2026-09-07 an E2E run put roughly 169 signups and 198 trials into Aura's web
organic bucket. Read alone that is a 39% organic-web jump and a headline finding;
it was test traffic. Check the day-level shape of any organic jump before
reporting it, and exclude the `e2etest` bucket from the organic definition.

## 7. A diagnosis from chat is a lead, not a fact

A confident explanation found in a Slack thread — especially one relayed through a
summary rather than read first-hand — has no data behind it yet. It is a
hypothesis to check against the source system, and the thread that produced it
often revises it hours later.

The 2026-09-07 Aurie thread ranked an App Store click-to-install failure at 85%
and retracted it entirely the same evening. A UGC pause reported in the same
channel turned out to be a partial pause in a week when UGC spend actually rose,
which inverted the reading built on it. Carry the claim into Step 4b and let the
person holding the ad account settle it.

## 8. Known incidents first

A move with a documented cause gets that cause, not a fresh theory. Before
drafting the mechanism for any off-band cost, revenue or margin move — and before
putting one to Viktor — search for the incident that already explains it:

- **Slack:** `#ai-cost` for LLM and voice cost steps, `#ai-analytics` and
  `#data-analysis` for the owner's own notes on the sheet, both bounded to the
  report week and the week before.
- **Linear:** issues updated in the last two weeks matching the metric
  (`LLM cost`, `voice cost`, `App Store`, the column name), on both the
  `Product/Design` and `ENG` teams.

Take the mechanism from what you find, carry its card in the chat output, and put
only the remaining open questions to Viktor. An arithmetic coincidence in the
cells does not outrank a documented incident. In the week of 10/11–10/17 the Aurie
LLM total read $8.7k, and the 4b round produced a column-level theory for it.
The cause was already on record: from 10/7 `chat-latest` used far more tokens per
conversation (prompt-cache hit 89% -> ~50%, longer conversations), in PROD-7290,
PROD-7343 and Jerome's `#ai-cost` posts. The author's margin bullet names that
cause.

## Working with Viktor

He owns the ad accounts, the Meta and Google mechanics and the raw Mixpanel
exports, so Step 4b is where a mechanism claim gets tested. What works:

- **Through the shared Slack layer.** Post with `shared:post-to-slack --as-user`
  and read the thread with `shared:read-slack`, the same path the note itself
  takes.
- **Numbered claims with their figures, never the draft note.** He answers
  point-by-point and stays on mechanism; handed the draft he comments on wording.
- **Three to five claims**, each one whose answer would change a bullet. Claims
  whose mechanism is already documented (§8) go to him with the incident cited, as
  a sizing question, not as an open "why".
- **His theories are leads too.** A mechanism he proposes gets the same §7
  treatment as one from chat: check it against the incident record before it
  reaches the draft or the chat summary as the explanation.
- **Patience.** He acknowledges within seconds and returns the analysis several
  minutes later. Poll the thread rather than timing out on him.
- **Sheet IDs, tabs and column letters.** He reads the sheets himself, so relaying
  figures on his behalf adds a round trip and hides the definitional gaps.
- **His confidence labels are part of the answer.** He marks findings high, medium
  or inconclusive and says what he is blocked on. Anything low-confidence stays
  open in the chat output, not settled.
- **His answers shape the draft, not the post's content.** They decide which sheet
  lines survive and how each is worded; his ad-account and Mixpanel figures stay
  in chat. A move he cannot confirm is dropped from the post rather than hedged in
  it.
- **Check a revision caveat against the sheet.** When he says the newest week will
  revise, compare the previous run's quoted newest-week figures with what those
  cells hold now before repeating it. On the 2026-09-28 run both apps' newest-week
  channel figures from the prior Monday had not moved, so the caveat was dropped.

Where his figures and the sheet's disagree that is itself a finding, and the note
is read against the sheet — so say which source a number came from rather than
silently preferring one.

## How to report

In the chat output, not the post. List each confounder with the metric it
touches and how long until the affected numbers can be read, then the ones
checked and ruled out, and why. The post is built from growth-sheet data only
(`note-conventions.md`), so a confounder reaches it only when the author asks for
one — and then as a single closing bullet, separate from the metric bullet it
explains.
