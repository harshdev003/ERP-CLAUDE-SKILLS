# Working the Sheets API once access is live

Patterns for using the `google-sheets` MCP tools well. Setup lives in `SKILL.md`; this file is
about the API's sharp edges.

## Discovery discipline — never write blind

Three steps, in order, every time:

1. **`get_spreadsheet_info`** — returns each tab's name, numeric **gid**, size, and row-1 values.
   Build a `{tab_name: gid}` lookup from it and reference that everywhere.
2. **Sample-read** the top of the target tab (`read_rows` with `cell_range="A1:Z5"`,
   `has_headers=false`) and look at what came back. Real sheets routinely open with a legend
   row, a title row, or merged banners, so **row 1 is frequently not the header row**.
3. **Match header text exactly** when writing dicts — case- and whitespace-sensitive. A header of
   `"Total Spend "` with a trailing space is a different key from `"Total Spend"`. Copy header
   strings from the read response rather than retyping them; `add_rows` rejects a key it cannot
   find in the header row rather than dropping the value silently.
4. **Pass `header_row` to `add_rows`** whenever step 2 showed the headers are not in row 1. It
   defaults to `1`, which is exactly the assumption step 2 exists to disprove — a dict append
   left on the default against a legend-row sheet aligns every value to the wrong column.

Two failure modes this prevents:

- **Wrong-tab writes.** API-created tabs receive arbitrary gids, so a hardcoded `0` targets a
  tab that may not exist. The lookup dict makes this impossible.
- **Garbage columns.** Keying a write off a legend row produces headers like `""` and
  `"Cell color:"` instead of the real field names.

## GridRange — 0-based and end-exclusive

Every `batch_update` request that targets cells uses a `GridRange`. It is the biggest source of
off-by-one errors because it does not match A1 notation.

```json
{ "sheetId": 185086061, "startRowIndex": 0, "endRowIndex": 1,
  "startColumnIndex": 0, "endColumnIndex": 10 }
```

`sheetId` here is the **tab's numeric gid**, not the spreadsheet ID. Indices are 0-based and the
end is **exclusive** — think Python slicing, `[start:end)`.

| A1 notation | GridRange |
|---|---|
| `A1` | row `[0,1)`, col `[0,1)` |
| `A1:J1` | row `[0,1)`, col `[0,10)` |
| `D2:D100` | row `[1,100)`, col `[3,4)` (column D = index 3) |
| `B:B` | col `[1,2)`, row indices omitted |

Omitting `startRowIndex`/`endRowIndex` means all rows — useful for whole-column formatting that
should also apply to rows added later.

A GridRange is a single contiguous rectangle, so **non-adjacent columns need separate requests**.
Formatting columns D and I means two `repeatCell` requests, not one spanning D–I.

## Field masks — be specific or lose formatting

`repeatCell` is the workhorse, and its `fields` mask decides which parts of the cell format get
replaced:

```json
{ "repeatCell": {
    "range": { "sheetId": 0, "startRowIndex": 1, "startColumnIndex": 3, "endColumnIndex": 4 },
    "cell": { "userEnteredFormat": { "numberFormat": { "type": "CURRENCY", "pattern": "$#,##0.00" } } },
    "fields": "userEnteredFormat.numberFormat" } }
```

`"fields": "userEnteredFormat.numberFormat"` touches only the number format. Writing
`"fields": "userEnteredFormat"` **wipes every format property absent from the payload** — bold,
background, borders. Always name the narrowest path, and use the parenthesised form for several
siblings: `userEnteredFormat(textFormat,backgroundColor)`.

Start data formatting at `startRowIndex: 1` so header text is not coerced into a number or
currency format.

## One call to create and format a tab

A later request in the same `batch_update` cannot reference a gid the same batch is generating —
unless the gid is supplied rather than discovered. `addSheet` honours a caller-chosen
`properties.sheetId`, so pick an unused integer and reference it immediately:

```json
[ { "addSheet": { "properties": { "sheetId": 424242, "title": "Weekly Summary" } } },
  { "repeatCell": { "range": { "sheetId": 424242, "startRowIndex": 0, "endRowIndex": 1 },
      "cell": { "userEnteredFormat": { "textFormat": { "bold": true } } },
      "fields": "userEnteredFormat.textFormat" } },
  { "updateSheetProperties": { "properties": { "sheetId": 424242,
      "gridProperties": { "frozenRowCount": 1 } }, "fields": "gridProperties.frozenRowCount" } } ]
```

Create plus all formatting collapses into a single atomic call. Choose a value that cannot collide
with an existing tab. Without a preassigned gid the sequence needs two round trips: create, read
the gid from `replies[0].addSheet.properties.sheetId`, then format.

## Formulas and cross-tab references

`add_rows` and `update_cells` default to `USER_ENTERED`, which parses input the way typing into
the cell would — so `"=SUMIFS(...)"` becomes a live formula. `RAW` stores the same string as
literal text, which is the usual reason a written formula shows up as inert text.

Quote tab names containing spaces, and anchor source ranges so they survive being copied:

```
=SUMIFS('Campaign Data'!$D$2:$D$99,'Campaign Data'!$A$2:$A$99,$A2)
```

For "the row with the highest metric in a group", scope first and then sort, rather than matching a
global maximum:

```
=INDEX(SORT(FILTER({'Campaign Data'!$B$2:$B$99,'Campaign Data'!$J$2:$J$99},
                   'Campaign Data'!$A$2:$A$99=$A2), 2, FALSE), 1, 1)
```

`FILTER` narrows to the group, `SORT(..., 2, FALSE)` orders by the metric descending, and
`INDEX(..., 1, 1)` takes the label from the top row. `INDEX/MATCH(MAXIFS(...))` misfires when two
groups share the same maximum value, because `MATCH` finds the first occurrence anywhere.

Pipeline order matters: write the source data **before** the formulas that aggregate it, or the
formulas evaluate against empty ranges. Run `autoResizeDimensions` last, after content exists, so
it measures real widths.

## Number formats

`PERCENT` multiplies by 100 for display, so a ROAS of `1.82` renders as `182%`. For multiples use a
custom pattern instead:

```json
{ "numberFormat": { "type": "NUMBER", "pattern": "0.00\"x\"" } }
```

## Reading cell colours

Colour is formatting, so the values API returns nothing about it — `read_rows` cannot see a
status conveyed by fill colour. Use **`read_cell_formats`**, which requests grid data with
`effectiveFormat.backgroundColor` and returns `{v, bg}` per cell with components normalised to 0–1.

`bg` is **`null`** for a cell with no background formatting at all — not `[0,0,0]`. Default fill
is white, so treating an absent colour as zeroes would classify every plain cell as black. Filter
`bg is null` out before comparing against legend colours; a `[0,0,0]` you do see is a real black fill.

**Calibrate against the sheet's own legend instead of guessing.** Many trackers put a colour key in
the top row ("Running", "Won", "Lost"). Read those legend cells, take their exact `bg` values as
ground truth, then compare data cells against them. Guessing that "orange" means some particular
RGB triple is how misclassification happens.

Two checks that make a colour-based answer trustworthy:

- **Account for every coloured cell.** If the per-colour counts sum to the number of populated
  rows, nothing was silently skipped or double-counted.
- **Corroborate with values.** A row whose colour says "still running" should also look unfinished
  in its data — typically a blank end date or empty result column. Agreement between an implicit
  signal and an explicit one is much stronger evidence than either alone.

Watch for **two legend entries sharing one colour** — then colour alone cannot separate them, and
the classification needs another column. Say so rather than picking one.

## Limits and batching

| Limit | Value |
|---|---|
| Cells per spreadsheet | 10 million |
| Read requests | 300/min per project |
| Write requests | 60/min per project per user |
| Practical rows per append | ~500–1000 |

`values.append` is atomic per call, so a chunk either lands entirely or not at all. That makes
moderate chunks natural transaction boundaries: chunk large loads by a meaningful key (a month, a
week) so a failure is retryable without leaving a half-written range, and so a retry does not
duplicate rows already committed.

Appends land after the last row containing data, so concurrent writers can interleave. Write all
chunks of one job in quick succession.
