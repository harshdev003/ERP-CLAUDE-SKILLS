# Row rules

Human-readable mirror of [`../scripts/registry.json`](../scripts/registry.json), which is what
the scripts read. **Edit both together**: nothing enforces that they agree.

Every report lives on Mixpanel board **6624751** "Products sheet reports". Each card there is
named `PM row N · …` or `C&M row N · …` and its description repeats the rule. The links in the
sheet's column B are the *old* sources. Several of them are edited views of other people's
reports, and B33's returned 0% for every month. Read by report id from the board.

## Pick rules

| Rule | Meaning |
|---|---|
| `monthly` | The report's bucket for the month (`YYYY-MM-01`). |
| `daily_max` | Highest single day in the month (B8 comment). All days of the month must be present. |
| `weekly_second_highest` | Weeks start on Sunday. A week belongs to the month that holds most of its seven days, which is the month of its Wednesday. Take the second-highest of those weeks (B2 comment, "top 75% (eg second highest)"). Every one of those weeks must have finished. |
| `cohort` | Monthly-cohort retention. The value goes in the **cohort month's** column, `cohort_lag_months` back. It is filled only when the cohort's last day + (W+1) weeks has passed, and only if the cell is empty. The `refund_row` below it gets `=<cell>*1.15`. |

The two ways the weekly rule could be read were tested against the hand-entered history.
"Majority of days" with the second-highest week reproduces Aug-26 and Sep-26 for WAPU (inc.
Trial) and Aug-26 for tracks/week exactly. "Weeks starting in the month" does not.

## Formats

| Format | Written | Shown |
|---|---|---|
| `integer` | rounded half-up | `20,707` |
| `percent` | fraction, 2 dp | `21%` |
| `percent_points` | report gives 46.83, written 0.47 | `47%` |
| `decimal2` / `decimal1` | 2 / 1 dp | `7.37` / `29.7` |

## Product Metrics

| Row | Label | Report | Rule | Notes |
|---|---|---|---|---|
| 1 | header | - | first of the month as a date; the copied `mmm-yy` format shows `Oct-26` | |
| 2 | WAPU (inc. Trial) | 94121125 | weekly_second_highest | B2 comment |
| 3 | Growth rate | formula | `=(new-prev)/prev`, copied | |
| 4 | WAPU (Paid only) | 94121086 | weekly_second_highest | same rule as row 2; filters cohort 618972 |
| 5 | Growth rate | formula | | |
| 6 | MAPU (Paid only) | 94121126 | monthly | |
| 7 | Growth rate | formula | | |
| 8 | DAPU (Paid only) | 94121095 | daily_max | B8 comment |
| 9 | Growth rate | formula | | |
| 10-12 | DAU/MAU, DAU/WAU, WAU/MAU | formula | `=r8/r6`, `=r8/r4`, `=r4/r6`, copied | |
| 13 | New subscribers | 51696417 | monthly | |
| 14 | Growth rate | formula | | |
| 15 | Notes | - | left blank | the user's free text |
| 16 | PMF Survey % Very Disappointed | 94121154 | monthly, **Feb/May/Aug/Nov only** | blank in other months |
| 17-18 | Onboarding (mobile / web) | - | blank | empty since they were added |
| 19 | Conversion rate Sign up > Paid (iOS US Organic) | 51695141 | monthly | backfill 2 months (B19) |
| 20 | Conversion rate Sign up > Paid (iOS US All Traffic) | 51695283 | monthly | backfill 2 months |
| 21 | Engagement freq min. 3 days/week (iOS) | 94121128 | weekly_second_highest | |
| 22 | Engagement freq min. 5 days/week (iOS) | 94121098 | weekly_second_highest | |
| 23 | Tracks finished/week (iOS Premium) | 94121106 | weekly_second_highest | 2 dp |
| 24 | Avg duration/listen | 94121108 | monthly | 1 dp |
| 25 / 26 | Paid Retention W4 (iOS organic) / excl refunds | 51696804 | cohort, lag 1, W4 | 26 = x1.15 |
| 27 / 28 | Paid Retention W12 (iOS organic) / excl refunds | 51695412 | cohort, lag 3, W12 | 28 = x1.15 |
| 29 / 30 | Paid Retention W4 (iOS All) / excl refunds | 51695781 | cohort, lag 1, W4 | 30 = x1.15 |
| 31 / 32 | Paid Retention W12 (iOS All) / excl refunds | 51695993 | cohort, lag 3, W12 | 32 = x1.15 |
| 33 | Cancellation within 14 days (iOS US organic mobile only) | 51696108 | monthly | backfill 1 month; provisional until month end + 14 days |
| 34 | Cancellation within 14 days (iOS All) | 51696124 | monthly | backfill 1 month |
| 35 | MAU | 94121144 | monthly | |

## Content & Marketplace

| Row | Label | Report | Rule |
|---|---|---|---|
| 1 | header | - | as above |
| 2 | Active tracks (w/o music) | 94121157 | monthly |
| 3, 5, 7, 9, 11 | Growth rate | formula | copied |
| 4 | Active tracks (w/ music) | 94121163 | monthly, series `Formula` (A+B) |
| 6 | Total premium listens/mo | 94121171 | monthly |
| 8 | Minutes listened/mo (no music) | 94121173 | monthly |
| 10 | Minutes listened/mo inc. music | 94121174 | monthly, rounded to a whole number |
| 12 | Active coach uploaders | 94121177 | monthly |
| 13 | Unique coach listened to | 94121178 | monthly |
| 14-15 | Coach retention (login / upload) | - | blank, as in recent months |

## Why the new column is cleared before it is filled

The planner copies the previous month's column (to carry formulas and formats), then writes
every non-formula row explicitly: a value, or a blank. Without the blanks, the copy would carry
the previous month's notes, PMF value or retention numbers into the new month.
