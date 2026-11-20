# Column registry

The single source of truth for which Mixpanel report feeds which spreadsheet column, and
how its weeks line up with the sheet's rows.

`scripts/registry.json` is the source of truth — it is what the scripts read. The table
below is a human-readable mirror of it. **Edit both together**; nothing enforces that they
agree, so a stale table here is a trap for the next person.

## Fields

| Field | Meaning |
|---|---|
| `column` | Target column letter on the tab. |
| `report_id` | Mixpanel bookmark id on dashboard 6594279. |
| `report_name` | The report's name, as it appears on the dashboard. |
| `step1_event` / `step2_event` | The funnel's two steps. Used for cross-checks between columns that share an event. |
| `shift_weeks` | Rows to move the value **up** (earlier) so the export's bucket lands on the sheet's cohort week. `0` for almost everything; `1` for paid-to-cancellation. |
| `maturity_weeks` | Trailing weeks that cannot be filled yet. `0` fills through the last closed week. |
| `platform` | `mobile` or `web`. Only used for grouping in output. |

## Current registry (AM-AX)

| Column | Report | id | Step 1 | Step 2 | shift | maturity |
|---|---|---|---|---|---|---|
| AM | Mobile onboarding CR by week (US) | 91831828 | Sign up start | Sign up login | 0 | 0 |
| AN | Mobile Registartion -> Trial CR (US) | 91831965 | Sign up login | Subscription Trial Started | 0 | 0 |
| AO | Mobile Trial -> Paid CR (8D) (US) | 91832315 | Subscription Trial Started | Subscription Paid | 0 | 1 |
| AP | Mobile Trial -> Cancellation (7D) (US) | 91832482 | Subscription Trial Started | Subscription Cancelled | 0 | 1 |
| AQ | Mobile Paid -> Cancellation (7D) (US) | 91832493 | Subscription Paid | Subscription Cancelled | 1 | 1 |
| AR | Web onboarding CR by week (US) | 91831880 | Sign up start | Sign up login | 0 | 0 |
| AS | Web Registartion -> Trial CR (US) | 91831980 | Sign up login | Subscription Trial Started | 0 | 0 |
| AT | Web Trial -> Paid CR (8D) (US) | 91832024 | Subscription Trial Started | Subscription Paid | 0 | 1 |
| AU | Web Trial -> Cancellation (7D) (US) | 91832442 | Subscription Trial Started | Subscription Cancelled | 0 | 1 |
| AV | Web Paid -> Cancellation (7D) (US) | 91832494 | Subscription Paid | Subscription Cancelled | 1 | 1 |
| AW | Web Registration -> Mobile Login (US) | 91832595 | Sign up login | Login | 0 | 0 |
| AX | Web Trial -> Upsell (US) | 91832396 | Subscription Trial Started | Web Subscription Upsell Processed | 0 | 1 |

Two report names carry a typo upstream ("Registartion"). They are reproduced verbatim so a
name lookup against the dashboard matches — do not correct them here.

`maturity_weeks: 1` means the most recently closed week stays blank. Combined with
`shift_weeks: 1`, the paid-to-cancellation columns leave the **last two** rows blank: the
value for the newest cohort week would need a paid week whose 7-day cancellation window
has not closed.

### Open question: AX maturity

`AX` is set to `maturity_weeks: 1`, but its step 2 fires about **28 seconds** after trial
start (`avg_time` on the step-2 row, checked 2026-09-20), so its conversion window is never
open at read time and the newest closed week looks fully mature. As set, the column leaves a
usable week blank on every run.

This has deliberately not been changed: it is a registry decision, not a run-time one. Do not
flip it mid-run. Raise it once, with the `avg_time` evidence, and if the answer is yes set
`maturity_weeks: 0` here and in `scripts/registry.json` together.

## How a column maps to rows

```
sheet_row = 3 + weeks_since(2021-12-26, csv_week_start) - shift_weeks
```

So for AQ, the CSV bucket `Aug 30, 2026 - Sep 5, 2026` (paid week) is written to the row
whose label is `8/23 - 8/29` (the trial-start week those paids came from).

## Adding a column

1. Add a row to the table above and the matching object to `scripts/registry.json`.
2. Set `shift_weeks` deliberately. If the export's bucket is keyed on an event that happens
   *after* the cohort-defining event, the value belongs on an earlier row. When unsure,
   measure it rather than guessing — see the alignment test in
   [`gotchas.md`](gotchas.md#proving-alignment).
3. Set `maturity_weeks` from how long the metric's window is. Anything with a conversion
   window that can still be open at read time is at least `1`.
4. Build that column alone (`--columns {letter}`) against a known week and confirm the tail
   matches what the dashboard shows before writing the full history.

### Worked example: a spend column

A spend column is not a funnel — it is a single weekly total. The registry supports this
with `"kind": "value"` instead of the default `"kind": "ratio"`; the CSV then has one count
column rather than two, and the script writes it as a number rather than a percentage.

```json
{
  "column": "B",
  "report_name": "Weekly spend (all channels)",
  "kind": "value",
  "format": "currency",
  "shift_weeks": 0,
  "maturity_weeks": 0
}
```

Everything else — row mapping, blanks, verification — is unchanged.
