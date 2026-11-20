---
name: update-products-sheet-monthly
description: >
  Adds the newest closed month to Aura's monthly "Product Metrics, Content & Marketplace"
  sheet, both tabs, from the Mixpanel board "Products sheet reports" (6624751). Writes one
  new column per tab right after the last filled month and picks each value by the rules in
  the sheet's cell comments: second-highest week for WAPU and the weekly rows, highest day for
  DAPU, the month's bucket elsewhere, PMF only in Feb/May/Aug/Nov. Also fills what matured
  since the last run: conversion rates two months back, cancellation one month back, W4
  cohort retention for last month, W12 for three months back, with the excl-refund rows as
  x1.15 formulas. A script plans every cell; preview, write one batch after approval,
  verify. Trigger on "update products sheet monthly",
  "update the monthly products sheet", "update the product metrics sheet", "add October to
  the products sheet", "fill the monthly sheet", or "update the Content & Marketplace tab".
  Do NOT use for the weekly Growth ROI tracker (data:update-growth-roi-sheet).
metadata:
  version: "1.0.0"
knowledge:
  reads:
    - Guide/Index
    - Tools/Google Sheets
    - Tools/Google Sheets/Google Sheets (API access)
    - Tools/Mixpanel
---

# Update products sheet monthly

Add one month to the **Product Metrics** and **Content & Marketplace** tabs, exactly the way
the Oct-26 column was done by hand on 2026-11-17. That run is this skill's test fixture: the
planner reproduces it cell for cell.

| | |
|---|---|
| Spreadsheet | `1UczBaxq2-zxKiwzBDu-5775FtUyTgGgRSxZeaEsE3Ck` |
| Tabs | `Product Metrics` (rows 1-35), `Content & Marketplace` (rows 1-15) |
| Source | Mixpanel board **6624751** "Products sheet reports", project `963821` |
| Row map | [`scripts/registry.json`](scripts/registry.json), mirrored in [`references/row-rules.md`](references/row-rules.md) |

Read [`references/gotchas.md`](references/gotchas.md) once before the first run: it holds the
traps that cost time when this was done by hand.

## What a run changes, and nothing else

1. **One new column per tab**, directly after the last filled month. One month per run. If
   two months are missing, do two runs, oldest first.
2. **These older cells, and only these** (each is a rule from the sheet's comments or its
   fill pattern):

| Rows (Product Metrics) | How far back | Why |
|---|---|---|
| 19, 20 Conversion rate | previous **2** months, only where the value changed | B19 comment: "Continue updating for 2mo after" |
| 33, 34 Cancellation within 14 days | previous **1** month, only where changed | CF33/CF34 comments: "Needs to be updated in couple weeks" |
| 25, 29 Paid Retention **W4** | the cohort column **1** month back, only if empty | W4 needs ~5 weeks after the cohort month ends |
| 27, 31 Paid Retention **W12** | the cohort column **3** months back, only if empty | W12 needs ~13 weeks |
| 26, 28, 30, 32 "Excl refunds 15%" | same cell as the retention value above | always the formula `=<cell above>*1.15` |

Everything else in older columns is history and stays as it is. Mixpanel restates past months
a little (MAPU, DAPU and WAPU Paid only read 0.5-2% lower months later); that is expected
and is **not** a reason to rewrite them.

## Workflow

### Step 1 — Resolve the month and read the sheet

The month is the **last closed calendar month** unless the user names one. Say which month
you landed on; do not ask.

Use the Google Sheets connector. `get_spreadsheet_info` gives each tab's row-1 headers: the
last header is the last filled month (`columnCount` includes empty columns, so count the
header list, not `columnCount`). It must read as the month before the target (e.g. `Sep-26`
for an Oct-26 run). If the target month already has a column, stop: this skill adds a month,
it does not redo one.

Read each tab from **three columns before the last filled one** through two past it, row 1 to
the tab's last template row, with `has_headers=false`:

```
read_rows(sheet_name="Product Metrics",       cell_range="CN1:CS35", has_headers=false)  -> pm_before.json
read_rows(sheet_name="Content & Marketplace", cell_range="CB1:CG15", has_headers=false)  -> cm_before.json
```

(Letters shown are for the Oct-26 run; shift them by one each month.) Save the results
verbatim. They are the planner's input and the spill-check baseline.

### Step 2 — Pull every report from the board

First `Get-Dashboard(project_id=963821, dashboard_id=6624751)` and check that the report ids
on the board are the ids in `registry.json`. If someone swapped or added a card, stop and
ask before going on: the registry is what the planner trusts.

Then, for every `report_id` in the registry (26 of them):

```
Get-Report(project_id=963821, bookmark_id=<id>, skip_results=false)
```

Save each result verbatim as `reports/<id>.json`. They normally come back inline; copy them
exactly, or pull them from the session transcript (match strings starting `{"id":` that
contain `"results"`). Never retype numbers.

### Step 3 — Plan

```bash
python3 scripts/plan_update.py --month 2026-10 --reports-dir reports \
    --pm-read pm_before.json --pm-first-col CN \
    --cm-read cm_before.json --cm-first-col CB --out plan.json
```

The planner applies every rule in `registry.json` and refuses to plan when:

- the target month has not closed yet, or a weekly row's last week has not finished;
- the last filled header is not the month before the target;
- a report is missing, or lacks the month's bucket or a full month of days.

It prints the preview: every new-column cell, every backfill with its old and new value, and
warnings. Cohorts that are not mature yet are skipped with a warning and get filled next run.
A W4 or W12 cell that already holds a different number is reported, never overwritten.

### Step 4 — Preview, then wait for a go-ahead

Show the user a short preview: the new column for each tab as a table, then the backfills
(`old -> new`) and any warnings. This is a shared team sheet, so **wait for an explicit
go-ahead before writing**. Mark these values as provisional: conversion rates (they will be
re-pulled for two more months) and cancellation when the run is under 14 days after month end.

If the user wants a dry run first, `copy_file` the spreadsheet into their My Drive, then run
the same plan against the copy. Sheet ids are kept in a copy.

### Step 5 — Write in one batch

Send `plan.json`'s `requests` as **one** `batch_update` call through the Google Sheets
connector. It is atomic: the copy of the previous column (formulas, formats, the growth-rate
and ratio rows), the new values over it, the explicit blanks, and the backfills land together
or not at all.

Do not type values into the sheet through the browser pane. Keystrokes into the Sheets grid get
dropped and merged (see gotchas).

### Step 6 — Verify

Re-read the same two ranges into `pm_after.json` / `cm_after.json` and run:

```bash
python3 scripts/verify_update.py --plan plan.json \
    --pm-read pm_after.json --pm-first-col CN --cm-read cm_after.json --cm-first-col CB \
    --pm-before pm_before.json --cm-before cm_before.json
```

It checks that every planned cell shows the planned text, every formula row in the new column
has a value, the planned blanks are empty, and nothing else in the read range moved. Report
its line. Do not say "verified" without it.

### Step 7 — Readout

Keep it short: one table per tab for the new month, one line for the backfills, one line
naming the provisional rows. Do not add notes or comments to the sheet. The month-header
comments ("FB Ads were turned off for 2 weeks", "Critical web bug on paywall") are event notes
the user writes; offer to add one only if they mention an event.

### Step 8 — Learn from the session (mandatory, never skipped)

The run is not finished when the sheet verifies. Close every run by looking back over the
whole session and answering four questions in chat:

1. **What did the user correct?** A value they changed, a rule they restated, a row they asked
   to skip or re-pull, a wording change in the readout. Every correction is a rule this skill
   did not encode yet. It is the highest-signal input the step has.
2. **What did the run get wrong or nearly wrong?** A planner refusal, a verify mismatch that
   was not drift, a report that came back without a bucket, a cell the preview showed and the
   user questioned.
3. **What changed outside the skill?** A sheet comment, a new row or tab, a report swapped on
   board 6624751, a link the user fixed in column B, a connector that behaved differently.
4. **What one concrete edit would make the next run more correct?**

Route each **repeatable** finding (one that would recur next month, not a one-off) to where it
belongs:

| Finding | Goes into |
|---|---|
| A pick rule, a backfill window, a row added or removed | `scripts/registry.json` **and** `references/row-rules.md`, plus a test in `tests/test_update_products_sheet_monthly.py` |
| A trap: a tool quirk, a misleading link, a UI behaviour | `references/gotchas.md` |
| A workflow or readout change | this `SKILL.md` |
| A report replaced on the board | the `report_id` in `registry.json`, after a Get-Report check that it reproduces last month's value |

Make the edit through `shared:update-aura-skill`, which owns the version bump and the PR. Show
the user the proposed edit and wait for their yes before opening it. Report the step in chat
every run, including "no findings". Several runs in a row with no findings is itself a
finding: the checks have gone stale and need sharpening.

## When the rules change

The rules come from the sheet's cell comments: B2 (WAPU, top 75% = second-highest week), B8
(DAPU, highest day; the "easier targeting" link), B19 (conversion, update for 2 months), and
CF33/CF34 (cancellation, update later). Comments are not readable through the Sheets
connector. If the user says a comment changed, open the sheet in the browser pane's comments
panel, then change `registry.json` **and** `references/row-rules.md` together. The same goes
for a new row: add a report to board 6624751 and add a registry entry. No code change is needed
unless a new pick rule appears.
