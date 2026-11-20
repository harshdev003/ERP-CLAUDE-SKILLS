---
name: update-growth-roi-sheet
description: >
  Pulls each report from the Mixpanel "Growth ROI sheet" dashboard and writes the weekly
  conversion-rate columns of the Aura Growth ROI tracker spreadsheet. No files needed from
  the user. Covers columns AM-AX today (mobile and web onboarding, registration-to-trial,
  trial-to-paid, trial-to-cancellation, paid-to-cancellation, registration-to-mobile-login,
  trial-to-upsell), driven by an extensible column registry so more of the sheet can be
  added a row at a time. Handles the per-metric maturity lag, the one-week attribution
  shift on paid-to-cancellation, and Mixpanel's 200-bucket breakdown cap that silently
  truncates wide-ranged reports. Trigger on "update the growth ROI sheet", "fill the CR
  columns", "update the growth tracker from Mixpanel", "refresh AM-AX", "write this week's
  conversion rates", or "the growth sheet columns are stale". Do NOT use to draft the
  weekly insight note (that is data:report-growth-roi), or to set up Sheets access
  (data:setup-google-sheets).
metadata:
  version: "1.2.1"
knowledge:
  reads:
    - Guide/Index
    - Tools/Google Sheets
    - Tools/Google Sheets/Google Sheets (API access)
    - Tools/Mixpanel
---

# Update Growth ROI sheet

Write weekly conversion rates into the Aura **Growth ROI tracker** from the Mixpanel
**Growth ROI sheet** dashboard, one column per saved report.

**Targets** (confirm both before writing — see [`references/column-registry.md`](references/column-registry.md)):

| | |
|---|---|
| Spreadsheet | `1WFSWn9PgM4oYjyLzr3w-meyww7zXQFeyCDVQGfx3LQY` |
| Tab | `Overall by channel weekly (22-26)` |
| Mixpanel dashboard / project | `6594279` / `963821` |
| Row anchor | row 3 = week of **2021-12-26**, one row per week, ascending |

`row = 3 + weeks_since(2021-12-26)`. Never assume a row number — derive it, then confirm
against column A, whose labels read `8/30 - 9/5`.

## The two rules that make this non-obvious

Read these before touching anything; they are the reason a naive load is wrong.

1. **Maturity.** A just-closed week is not readable for every metric. Trial-to-paid needs
   7-8 days; trial-to-cancellation and trial-to-upsell need the 7-day trial to elapse;
   paid-to-cancellation needs ~2 weeks. Leave immature cells **blank** — never write a
   partial number, because it reads as a collapse in conversion.
2. **Attribution shift on paid-to-cancellation.** A Mixpanel `Subscription Paid` to
   `Subscription Cancelled` export is bucketed by the **paid** week, but the sheet's row is
   the **trial-start** week, which is one week earlier. Those columns are written one row
   up. Every other column maps straight across. Verified empirically — see
   [`references/gotchas.md`](references/gotchas.md).

Both facts live per-column in the registry as `shift_weeks` and `maturity_weeks`, so you
apply them by reading the table, not by remembering them.

## Workflow

### Step 1 — Resolve scope

Unless the user named columns or weeks, the scope is **every column in the registry, each
written at its own next empty cell** — the first blank row below its last filled one. That is
the default; resolve it and state what you landed on rather than asking.

The columns are filled to different depths, because `shift_weeks` and `maturity_weeks` differ
per column, so "the latest week" is not one row. A routine run lands in three groups:

| Registry shape | Columns | Target row |
|---|---|---|
| `shift 0`, `maturity 0` | AM AN AR AS AW | last closed week |
| `shift 0`, `maturity 1` | AO AP AT AU AX | one week earlier |
| `shift 1`, `maturity 1` | AQ AV | two weeks earlier |

Resolving this needs the sheet, so take the Step 4 snapshot now — the full-width read from
row 3 — and keep it for the write and the verify. Derive each target from the registry, then
confirm it against that read: **the target cell must be empty and the cell directly above it
filled.** That single pairing proves the column is neither being overwritten nor left with a
hole, and it is worth more than any restatement of the mapping.

Hold for an answer only when the pairing fails:

- a target cell is **not empty** — that is an overwrite, not a fill;
- the cell above a target is **empty** — history has a gap, and filling the target strands it;
- a column is more than one week behind the rest, so its target is not the contiguous next cell.

If the user names a metric rather than a letter ("trial to paid"), resolve it through the
registry and say which letter you landed on.

### Step 2 — Pull the reports from the dashboard

Read each column's report straight from Mixpanel. No files from the user, no pasting.

For every column in scope, take its `report_id` from the registry and call:

```
Get-Report(project_id=963821, bookmark_id=<report_id>, skip_results=false)
```

A full funnel result is large and the tool writes it to a file rather than returning it
inline. Save or copy each one into a working directory named by its column letter —
`reports/AM_report.json`, `reports/AR_report.json`, and so on. The build script reads that
directory; it accepts the raw result or the `{result: "..."}` wrapper either way.

**A current-year report is usually small enough to come back inline instead**, despite the
size warning above — all twelve did on 2026-09-20. When that happens there is no file to
point `--report-dir` at, and the counts have to be hand-copied into the `--csv-dir` shape.
That is the one transcribed step in this skill, so check it rather than trusting it: each
computed rate must reproduce Mixpanel's own `overall_conv_ratio` for that week to two
decimals. That field sits in the same row, is not used in the computation, and so tests both
counts at once for free.

**The reports must be date-ranged to stay under Mixpanel's 200-bucket cap.** Mixpanel
returns at most 200 breakdown values per report, chosen by volume, so a report spanning the
tracker's full 246 weeks silently drops ~46 of them — the lowest-volume ones, which on a
declining series are the most recent. A current-year start is about 37 weekly buckets and
safe; a 2022 start is not. The build script refuses a column with interior holes rather
than writing a series that looks complete and is not.

Because a current-year report has no 2022-2025 buckets, the build only touches the rows
those reports cover and leaves earlier history alone. It says so in its output.

**CSV fallback.** If a report cannot be narrowed — you need history the report no longer
spans — export that column's CSV and pass `--csv-dir` instead. Same shape as the dashboard
export: a `$time` column, an `$overall` row to skip, then one row per week with the raw step
counts. The two paths agree to within late-arriving-event noise, not exactly; see
[`references/gotchas.md`](references/gotchas.md).

### Step 3 — Build the column values

```bash
python3 scripts/build_columns.py --report-dir ./reports --out ./final.json --report
```

The script maps each week to its row, computes `step2 / step1`, applies `shift_weeks` and
blanks `maturity_weeks` worth of trailing rows, and fails loudly on a duplicate week, an
off-grid date, or a rate outside 0-100%. Read its report before continuing — it prints
per-column coverage and every blank it introduced.

**Maturity is measured against the calendar, not against how far the data happens to run.**
The grid ends at the last week that had fully elapsed as of today, so a mid-week pull
contributes an in-progress bucket that is ignored rather than treated as the newest closed
week. Pass `--as-of` to reproduce an older run. Sources of slightly different lengths are
fine — a long one no longer stretches the grid and false-fails every other column.

The window starts where the reports actually begin. Pass `--from-week` to narrow it
further; the rows before it are never touched.

Two different failures wear the same mask, and the script names which one it hit: missing
weeks in a contiguous tail mean a **stale source**; interior holes mean the **200-bucket
cap**. Refresh or narrow the report either way — do not reach for `--last-week` to paper
over a cap.

### Step 4 — Preview, then write

Show the tail of each column (last 3-4 weeks), the row range, and a count of cells to be
written versus left blank — then write. A fill into empty cells that passed Step 1's pairing
check is the routine case and needs no sign-off; the preview puts the numbers on the record,
it is not a gate. Hold for an answer only on a Step 1 stop condition, or when Step 6 turns up
something that changes what the numbers mean.

You took the full snapshot in Step 1; keep it, so a revert is exact and the Step 5 spill
check has its baseline.

Write one column per call. The range runs from the build output's `start_row`
for as many rows as it produced — do not hardcode it. Write percent strings so Sheets
stores a real number carrying the column's existing format:

```
update_cells(range_a1="'Overall by channel weekly (22-26)'!AM213:AM248",
             values_json=[["45.87%"], ["46.60%"], ...],   # one row each, "" for blanks
             value_input_option="USER_ENTERED")
```

### Step 5 — Verify (not optional)

Read the block back **full width from column A, starting at row 3** with
`has_headers=false` — the spill check needs the untouched columns, and the script offsets
the build's `start_row` against the read-back's first row. A wide read overflows into a
file, which is fine: point `--sheet` at it. If you start the read-back somewhere other than
row 3, say so with `--first-row`; the script refuses rather than comparing the wrong rows.

```bash
python3 scripts/verify_columns.py --sheet ./readback.json \
    --expected ./final.json --before ./snapshot.json
```

Pass `--before` with the pre-write snapshot or the spill check silently does not run. If
you deliberately read a narrower range, tell the script where it starts with
`--first-column`.

**When you write a subset of rows rather than whole columns** — which the Step 1 default
always does — do not pass the build's `final.json` as `--expected`. Its arrays describe only
the rows the build produced, so untouched history is never compared. Build `--expected`
instead as the pre-write snapshot with just the target cells swapped in, spanning every row of
each target column. The comparison then covers the whole column: the cells you wrote *and* the
history you did not, which is what would catch a stray write into an old row.

Then check all five:

- every written cell equals the intended value;
- nothing outside the target columns changed;
- an independent recompute straight from the CSVs agrees;
- values are stored as numbers, not text, and as fractions rather than x100 — a one-cell
  temporary formula proves it: `COUNT` equals `COUNTA`, and `COUNTIF(range,">1")` is `0`;
- the populated-cell count equals columns x weeks minus the blanks the build reported.

Delete the temporary formula cell afterwards. Report the counts; do not assert "verified"
without them. Row 1122 sits well below the data and has served as the scratch cell — confirm
it is empty before using it.

**The tab has other writers, and they surface as spill.** Ad spend and channel-attributed
volume refresh on their own schedule, so `--before` can report cells changed outside the
target columns that have nothing to do with this run. Do not call the verification failed, and
do not revert them. Tell them apart by what they are:

- a genuine spill is a **percentage**, in a column adjacent to one you wrote, at a row you wrote;
- an independent refresh is **dollar amounts and raw counts**, often at rows you never touched.

Re-read the range; if the values are stable and none land in a registry column, name them and
move on. Observed 2026-09-20: 17 cells across B/C/K/L/M and AY-BD moved between snapshot and
read-back, including `\$0 -> \$11,975` at row 249 and a raw count at row 246, where nothing was
written.

### Step 6 — Sanity-check the source

Cross-check columns that share a funnel step (registry `step1_event` / `step2_event`):
counts for the same event should agree within 1-2 users. A larger gap means a bad
transcription or a genuinely different filter — say which before writing. Flag any week
whose denominator is far off its neighbours; a backfilled event inflates the base and
pushes the rate down without looking like an error.

**A denominator regime change is reported, not withheld.** When a base moves by a multiple
rather than a few percent, the rate is still the correct arithmetic on the current data —
write it, and say in the readout that the level break is the denominator rather than
behaviour, so the weekly note does not read it as a collapse. Decide which it is first, using
the other report that shares the step: if both report the base identically, the event stream
changed and the number is real; if they disagree, one report carries a stray filter, and that
is a bug to fix before writing anything.

Observed 2026-09-20: web `Sign up login` ran 126 -> 409 -> 627 over three weeks, dragging AS
and AW from ~50% to ~26-28%. Both reported the base identically, and the new level matched
AR's in-funnel login count (627 vs 623), which had been ~500-650 throughout — so the base had
been under-counting and was fixed. Both were written, with the mechanism noted.

**Watch the boundary on the 8-day columns.** AO and AT close their window eight days after a
trial starts, so on a Sunday run the target week's last cohort matures *that day*. The value is
a floor and may tick up by under a point. Write it — but if the readout leans on AO or AT
being down week-over-week, say it is worth re-reading the next day.

## Extending to more of the sheet

Add a row to the registry table and nothing else. The scripts read it; no code changes.
[`references/column-registry.md`](references/column-registry.md) documents each field and
has a worked example for adding a spend or CAC column.
