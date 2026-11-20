# Gotchas

Each of these cost a real debugging cycle. They are ordered by how expensive they are to
miss.

## The MCP read and the CSV export do not agree exactly

Both are correct readings of the same funnel; they disagree because they were taken at
different times and Mixpanel keeps receiving events for past weeks.

Measured across 2,400 weeks where a Get-Report pull and a CSV export overlapped, one day
apart: 517 weeks differed, every one by **1-6 users**, and on both steps together — the
signature of late arrivals, not of a parsing bug. In rate terms that is under 0.05pp for
80% of weeks, but a 2-user shift on a week with 31 trials is **5.3pp**.

So: do not treat a small disagreement between the two paths as an error, and do not mix
them within a column. Pick one source per run. Low-volume weeks are where any timing
difference shows up loudest.

## The 200-bucket cap silently truncates saved reports

Mixpanel returns at most **200 breakdown values** per report, chosen by volume. The tracker
is 246 weeks, so reading any of these reports through `Get-Report` drops ~46 weeks — and it
drops the *smallest* ones, which on a declining series means the **most recent** weeks.

There is no error, no flag, and no gap in the returned rows to notice. The tell is that the
returned week set has holes mid-series and stops short of today while `$overall` still looks
healthy.

Narrowing the report's **date range** is the fix, but only if it brings the week count
under 200 — a Jan-1-2022 start is still ~245 buckets. A current-year start is ~37 and safe,
and is how the dashboard's reports are set up.

`build_columns.py` enforces this: it refuses to write a column whose weeks have interior
holes. If a report cannot be narrowed because you need history it no longer spans, fall
back to that column's CSV export, which carries every week regardless of range.

## Get-Report column order is not stable

The funnel result's `rows` are flat arrays described by a sibling `headers` list, and
**the order differs between reports** — across the twelve reports on this dashboard `count`
has been observed at **five distinct positions: 3, 4, 5, 6 and 7** (measured 2026-09-20; AP
put it at 3, AX at 4, AM/AN/AO/AQ at 7). Index by header name, never by position. The parser
does this; anything else reading these results must too.

Indexing by position does not throw — it silently returns `property_sum`, a ratio, or a
timing in seconds, any of which can look like a plausible count. The cheap guard is Mixpanel's
own `overall_conv_ratio` on the step-2 row: recompute `step2 / step1` and it must agree to two
decimals.

A result with more than one series means a breakdown is set on the report. The parser
refuses rather than guessing which series is the one you want.

## Paid-to-cancellation is bucketed on the wrong week

A `Subscription Paid` to `Subscription Cancelled` funnel is bucketed by the week of step 1
— the **paid** week. The sheet's row is the **trial-start** week, one week earlier, because
a 7-day trial that starts in week 1 pays in week 2. Writing the export straight across puts
every value one week late.

### Proving alignment

Do not guess a shift. Measure it against the column's own history: compute the candidate
series at shift 0, -1 and +1, and correlate each against what is already in the column. The
right shift is unambiguous.

Measured on 2026-09-15 across 246 weeks:

| Column | shift 0 | shift -1 | Verdict |
|---|---|---|---|
| AM | 0.999 | 0.851 | direct |
| AO | 0.966 | 0.736 | direct |
| AP | 0.943 | 0.695 | direct |
| AQ | 0.503 | **0.929** | one week back |
| AV | 0.147 | **0.403** | one week back |

Re-measured 2026-09-20 against the sheet's own recent history, with mean absolute error
alongside the correlation — every column's best shift matched its registry value:

| Column | shift 0 | shift 1 | Registry |
|---|---|---|---|
| AQ | -0.331 / 2.46pp | **0.975 / 0.48pp** | 1 |
| AV | 0.405 / 2.65pp | **0.912 / 1.45pp** | 1 |
| AM | **0.998 / 0.05pp** | 0.653 / 2.29pp | 0 |
| AO | **0.953 / 1.32pp** | -0.333 / 4.79pp | 0 |
| AP | **0.915 / 1.44pp** | 0.270 / 4.35pp | 0 |
| AT | **0.971 / 1.69pp** | -0.099 / 10.36pp | 0 |

AM over 37 weeks is the useful control: reproducing existing history to 0.05pp means the whole
chain — parsing, week-to-row mapping, rate computation — is sound, not just the shift.

AV's correlation is weaker in absolute terms because web volumes are small and noisy, but
the direction agrees with AQ, which is the same metric on the other platform.

This test also works in reverse: if a column you are adding correlates at ~0.99 unshifted,
that is confirmation the mapping is right.

## Never write an immature week

Trial-to-paid, trial-to-cancellation, trial-to-upsell and paid-to-cancellation all have a
conversion window that can still be open when you read. A partially-elapsed window produces
a real-looking but badly depressed rate — a trial-to-paid of 8% against a 57% baseline is
the window, not the funnel.

Leave the cell blank. A blank is obviously pending; a wrong number is not.

## Columns that share a funnel step should agree

Use this as a transcription check before writing. Columns whose `step1_event` matches should
report near-identical counts week by week:

- AS step 1 and AW step 1 (web Sign up login) — agreed on 245 of 246 weeks, one week off by 1.
- AO step 1 and AP step 1 (mobile Trial Started) — 68 weeks differ, all by 1-2 users.

That residual is Mixpanel's own boundary jitter between reports with different conversion
windows, and it is expected. A gap of more than a couple of users means a typo or a
different filter.

Two pairs look like they should agree but **should not**, by design:

- AM step 2 vs AN step 1 differ 10-20% throughout: one counts logins reached inside the
  funnel window, the other counts all funnel entries.
- AO step 2 vs AQ step 1 differ substantially: paids within 8 days of a trial versus all
  paids that week.

## Watch the denominator, not just the rate

A backfilled event inflates a week's base and pushes the rate down without looking like an
error. Observed on the week of 2025-11-30, where `Subscription Paid` read 2,441 on mobile
and 274 on web against neighbours of ~150 and ~45 — dragging those cells to 2.34% and 1.46%.

Scan each column's denominator series for outliers before writing, and flag them rather
than quietly publishing the rate.

## Formatting

Write percent **strings** (`"57.76%"`) with `USER_ENTERED`. Sheets parses them into real
numbers carrying the column's percent format. Writing the bare number `57.76` stores 5776%.

Confirm after writing with a one-cell temporary formula: `COUNT` must equal `COUNTA` (every
value numeric, none stored as text) and `COUNTIF(range,">1")` must be `0` (everything is a
fraction). Delete the cell afterwards.
