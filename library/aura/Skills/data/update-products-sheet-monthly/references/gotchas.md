# Gotchas

Learned on the first run, 2026-11-17 (Oct-26), when the update was done by hand through the
browser pane and the connectors.

## Writing

- **Do not type into the Sheets grid from the browser pane.** Keystrokes were dropped and
  merged: the Name box ignored Enter, and three values ended up concatenated in one cell
  (`9/26/202620707`). Screenshots lagged several actions behind the real state. Write through
  the Google Sheets connector's `batch_update`; it is atomic and reads back cleanly.
- **Clipboard paste does not work in the pane.** Copying the previous column has to be a
  `copyPaste` request, not Ctrl+C / Ctrl+V.
- **The header is a date.** The history was typed as `Oct-26`, which Sheets stored as 26 Oct
  of the current year, shown through an `mmm-yy` format. The planner writes the first of the
  month and keeps the copied format, so it shows the same. A header written as a bare number
  without that format shows `46321`; the copy request is what carries the format.
- **File > Make a copy opens a new tab, which the pane blocks, so no copy is made.** Use Drive
  `copy_file` for a dry-run copy.

## Reading

- **Count headers, not `columnCount`.** Product Metrics reports 99 columns but only 95
  headers; the extra ones are empty.
- **`read_rows` trims trailing blanks**, so a short row is not an error. The planner treats
  anything past a row's end as empty.
- **Cell comments are invisible to the Sheets connector.** They are the rule book (B2, B8,
  B19, CF33, CF34). Read them in the browser pane: open the sheet, then the comments icon.
- **The B-column links are not the source.** Several open an edited view of a saved report
  that belongs to someone else (Steve's MAPU report, opened through the DAPU link, shows a
  daily view). The B33 link returned 0% for every month until it was fixed. The board's
  reports were each checked against the Oct-26 values.

## Mixpanel

- **Get-Report values come back rounded to 2 dp.** Tracks/week read 7.36 from Get-Report
  where the UI computed 7.3656 (7.37). Expect +/-0.01 on decimal rows. That is precision, not
  an error.
- **Same-day drift.** Values pulled a day apart differed by 2-5 users on WAPU Paid only, MAPU
  and DAPU. The verify step flags those four cells when you compare a plan against a sheet
  filled earlier, which is expected.
- **Saving a view to the board from the UI.** If a link opens a *modified* saved report, the
  Save button **overwrites the original**. Use Save as New, which drops the copy on the
  original's board, then use ⋯ → Move to → "Products sheet reports". Open the copy by its
  `#report/<id>` URL before moving it, so the original is never moved. Set the pane viewport
  to 1440x900 or the Save button is off-screen.
- **The Mixpanel connector's Run-Query has no cohort filter.** Views that filter on a cohort
  (WAPU Paid only and DAPU use cohort 618972 "Premium Paid start all") cannot be rebuilt
  through the connector. Save them from the UI as above.
