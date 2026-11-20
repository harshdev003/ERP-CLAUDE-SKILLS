# Dashboard contract

The dashboard is a self-contained HTML Artifact. It holds the run history, so
it is both the output surface and the durable store for day-over-day trails.

## Publishing

Write the HTML to a stable file path and publish with the Artifact tool.
Republishing the **same file path** keeps the URL. From a session that did not
publish it, pass the existing URL explicitly — otherwise a new URL is minted
and every circulated link goes stale.

### Recover the locator before writing anything

A session's scratchpad path is per-session, so the file path alone is not a
durable locator and a fresh run cannot rely on it. Recover the URL first, in
this order, and stop at the first hit:

1. **List the user's artifacts** and match on title — the artifact is titled
   `Running experiments — Aurie & Aura`. This is the normal path.
2. **Ask the user for the link** if the listing does not surface it.

Then fetch that URL to recover the existing `D` array and run history, rebuild
the page with today's counts appended, and publish **passing the recovered URL**
so it updates in place.

Publishing without a recovered locator mints a second dashboard: the team keeps
reading the old link while the new data lands somewhere nobody has, and the
history trail restarts from empty. Treat a failed recovery as a reason to ask,
not a reason to publish fresh.

Keep the favicon and title stable across runs. Viewers find the tab by its
icon; a changed favicon reads as a different page.

## Data shape

Two arrays drive everything. The page computes each rate, lift and confidence
itself, so emit **counts, not derived numbers** — there is nothing to keep in
sync and the arithmetic cannot drift from the readout.

`TABS` — one entry per surface: `{id, label, sheet, lede}`. An `all` tab
aggregates and must exclude the Aura Web rows, which share properties with
their Aura Mobile twins and would double-count.

`D` — one entry per experiment:

| Field | Meaning |
|---|---|
| `tab` | which surface it belongs to |
| `id` | experiment name, matching the Aura API |
| `st` | `decide` \| `watch` \| `early` |
| `start`, `days` | start date and days elapsed |
| `tick` | Linear ticket id, or null |
| `note` | what the change is, and the one thing worth knowing |
| `vd` | optional hand-written verdict, overriding the generated one |
| `srm` | optional sample-ratio-mismatch warning |
| `ctl`, `arms`, `tags` | control key, treatment keys, human labels per arm |
| `order` | optional column order for multi-arm comparison |
| `why` | on a `decide` row, its reason: `neutral`, `significant`, `bug` or `decided` |
| `plan` | the experiment's metric plan — `{decision, gate, mechanism, population, guardrail, rule, src}` per `metric-plans.md`. Carried forward from the previous run's row and changed only with a new source |
| `mm` | the metric list |

The page need not render `plan`; it is stored on the row so the next run
recovers it with `D` instead of rebuilding it.

`mm` is the opposite: every run rebuilds it from Mixpanel and never carries it
forward. Each metric appears once, replaced in place; earlier readings live in the
run history, not as dated copies in `mm`.

Each metric is `{nm, kind, num, den, inv, note, flag}`:

- `kind` — `rate` (num ÷ den as a percentage), `avg` (num ÷ den as a ratio),
  `raw` (a count), `pct` (num is already a percentage). Use `pct` for a figure that
  arrives as a rate rather than as a numerator over a denominator — retention is the
  case that matters, and because `pct` carries no denominator the page computes no
  confidence for it, which is the correct behaviour for a metric on overlapping
  cohorts.
- `inv` — true when lower is better. Only cancellation and dismissal metrics
  set this. Getting it wrong inverts the colour and reads as the opposite
  verdict.
- `flag` — `lagged`, `unscoped` or `uninstrumented`, rendered as a chip and
  explained in the footer only when the selected experiment has one. `lagged`
  means the conversion window has not matured. `unscoped` means the metric is
  real and measured, but without a scoping the contract would otherwise require.
  `uninstrumented` means no event exists for the thing being named, so there is
  no number at all — the three are not interchangeable, and the footer defines
  whichever ones the selected experiment carries.

## Metric order determines tiering

Tiering is derived, not tagged, from position relative to `Registrations`:
`Registrations` itself is exposure, every metric above it is primary — labelled
`primary · lower is better` where `inv` is set — and every metric below it is
second-class context. So **the order of `mm` is load-bearing**: put the decision
metrics and the experiment's own key metrics above `Registrations`, and everything
that merely explains them below.

A metric whose control arm holds `null` renders as `descriptive · one arm` and
carries no lift or confidence whatever its position. That is the both-arms rule
enforced in the render rather than left to the author: a surface only one arm has
cannot be compared.

**`null` and `0` mean different things and the distinction is load-bearing.** Use
`null` for a surface that does not exist in that arm, and `0` for a surface that
exists and was measured at zero. A measured zero is a comparison — a control that
converts nobody against a treatment that converts someone is a result, and the
largest lift the board can carry — so it keeps its lift and confidence. Writing `0`
where the surface is absent understates the arm; writing `null` where it was
measured suppresses a real finding.

## Early rows carry a mechanism metric

An `early` or `unramped` row whose `mm` holds only `Registrations` renders as a
bare assignment count, and on the board "too early to read" and "shipped broken"
then look the same. Give every such row at least one mechanism metric from the
experiment's own surface — the screen-seen or action event the change is supposed
to move — as a `raw` count beside the registration count.

Where the surface has no event, the row still carries the entry: name the metric
after the surface the ticket describes ("Orb animated", "Practice picker seen"),
set `kind` to `raw`, give every arm `null`, flag it `uninstrumented`, and use the
`note` to say which event is missing. That renders as a named row with no number,
which is the honest shape — a row that states its mechanism is unmeasurable is a
finding, a row showing only registrations is silence.

Do not reach for `unscoped` here. That flag marks a metric that was measured
without a scoping the contract asks for; a mechanism with no event was never
measured at all.

## Multi-arm experiments

Arm pills select one treatment against control and render exactly like a
two-arm experiment. A `compare all` pill renders the full matrix, one column
per arm.

Set `order` on ladder experiments so columns read in the ladder's own order
rather than alphabetically by arm letter. A monotonic trend across three price
points is stronger evidence than any single pairwise comparison, and it is
invisible when the columns are shuffled.

A monotonic trend is not a formal trend test. Report it as a stronger prior and
say that no trend test was run.

Every history row on a multi-arm experiment names the arm its lift belongs to —
see [Run history](#run-history).

## Run history

Each run appends `{date, experiment, arm, metric, lift, confidence, status}` rows.
Recover the existing history by fetching the published artifact before
rewriting it; a run that overwrites history destroys the trail that makes
decaying signals visible. Re-running on the same date replaces that date's
entry instead of appending a duplicate.

`arm` is the treatment key the lift was computed against — the same letter the
verdict quotes — and it is required on every row carrying a lift. A count-only
row sets it to `null`.

**The trail renders one row per metric *and* arm, labelled with its arm.** The
field is not optional metadata: the trail sits inside the per-arm detail panel,
so a row without an arm reads as though it belonged to whichever arm the reader
has selected, and on a multi-arm experiment that is the wrong one. A cancellation
guardrail on `aiCtaArrow` arm d runs +58.2% to +66.5% across eight runs while arm
c's own cancellation lift is -12.6% — an unlabelled row turns a benign arm
into a failing one. Dim the rows belonging to arms other than the selected one
rather than hiding them, so the full trail stays readable.

A row whose `arm` is absent cannot be attributed after the fact, so render it with
an explicit `arm not recorded` marker on multi-arm experiments instead of letting
it inherit the selection. On a single-treatment experiment the arm is unambiguous
and needs no label.

## Presentation invariants

- Column headers share one grid definition with the data rows, so alignment
  cannot drift.
- Placeholder cells for absent values hide together with the column they sit in
  at narrow widths.
- Every rate shows its numerator beside it.
- Status counters and the status rule are stated on the page, including where
  the rule is analyst judgement rather than mechanical.
- Both light and dark themes are styled through tokens.
