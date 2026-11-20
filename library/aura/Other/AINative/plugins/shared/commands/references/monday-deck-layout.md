# Monday All-Hands deck — layout, generation and QA

Companion to [`../prepare-monday-deck.md`](../prepare-monday-deck.md), which owns the data
gathering. This file owns everything downstream of it: the content contract, the layout spec,
the chart mechanics and the visual-QA workflow.

## The `deck-content.js` contract

`create-deck.js` reads these keys by exact name. A misspelled key is valid JavaScript, so it
fails silently — a missing header renders as the literal `undefined:` and a wrongly-shaped row
throws at build. Author content against this table, and when the generator already exists in the
working directory, read it before writing content rather than inferring the shape.

| Key | Shape |
|-----|-------|
| `team` | `string[]` — divider-slide roster |
| `userImpact.subtitle` | `string` — the italic totals line |
| `userImpact.production`, `.testflight` | `[{ version, items }]`, `items: (string \| {exp, text})[]` |
| `weeklyFocus.majorFeatures`, `.other`, `.planned` | `(string \| {exp, text})[]` |
| `focusAreas.title`, `.weekOf` | `string`, `string` — the slide title and the week every row reports |
| `focusAreas.groups` | `[{ source, rows }]` — one group per source dashboard, rendered in order |
| `focusAreas.groups[].rows[]` | `{ metric, target, value, unit, delta, dir, status, note }`; `value` keeps any leading `≥`; `delta` is a signed percentage string; `dir` is `"good" \| "bad"` (is THIS move an improvement); `status` is `"on" \| "risk" \| "off"` |
| `prMetrics.*` | backs the **Code Review Metrics** slide. The key stays `prMetrics` — it is a contract `create-deck.js` reads by exact name, and renaming it to match the title would fail silently |
| `prMetrics.weekOf` | `string` — date only; the slide adds the "Week of" prefix |
| `prMetrics.scope` | `string` — the rest of the subtitle, e.g. `"all 51 repos · Monday-start weeks"` |
| `prMetrics.kpis` | `[{ label, value, unit, dir }]` — exactly 5, in slide order; `value` keeps any leading `≥`; `dir` is `"up" \| "down" \| "flat" \| "none"` |
| `prMetrics.kpis[].delta` | `string` — the movement, e.g. `"7.8h WoW"` or `"13.5pt WoW"`; omit for `dir: "flat"` |
| `prMetrics.weeks` | `string[]` — the 8 complete weeks, oldest first, as `"M/D - M/D"` chart categories |
| `prMetrics.coverage` | `{ human, aiOnly, none }`, each `number[]` — percentages, one per week, summing to 100 |
| `prMetrics.firstReview` | `{ human, ai }`, each `(number \| null)[]` — hours, one per week |
| `prMetrics.footer`, `.takeaway` | `string` |
| `velocityTrend.subtitle`, `.weeks` | `string`, `string[]` — the last 13 complete weeks, oldest first, as `"M/D - M/D"` chart categories |
| `velocityTrend.members` | `[{ name, values }]` — display names, one `values` entry per week; the chart plots their per-week sum |
| `velocityTrend.features` | `{ released, handedOff }`, each `{ product, engineering }` of `(number \| null)[]` — one entry per week in `weeks`, from the Product Delivery fact table (read from the published artifact); the charts plot `product`, and `engineering` appears only as a count in the insight lines; `null` is a week the published page cannot report yet |
| `velocityTrend.insights` | `[{ label, value, deltas: [{text, dir}], detail? }]` — exactly 3, in chart order (velocity, released, handed off); `value` is the formatted figure text; `dir` is `"up" \| "down" \| "flat"`; `detail` is an optional second line (the per-member changes on the velocity row) |
| `velocityTrend.takeaway` | `string` — one sentence: did velocity and product features released correspond against their 4-week averages, and if not, why — or, on a gated week, that the comparison waits on a republish |
| `labelEffort.rows` | `[label, effort][]`, full set — the chart takes the top `chartTop` |
| `labelEffort.subtitle`, `.mixLabel`, `.mix` | `string`, `string`, `[{pct, label, muted?}]` |
| `retro` | `null`, or `{ worked: string[], didnt: string[] }` |

## Design Tokens (matching reference All-Hands deck)

`LAYOUT_16x9` (10" × 5.625"), font Arial throughout. Colours: black `000000`, darkGray `333333`, medGray `666666`, white `FFFFFF`, green `1B7A3D` (improvement), red `C0392B` (regression), amber `B7791F` (at risk), band `F7F7F6` (scorecard row fill). Positions: `TITLE_X`/`BODY_X` 0.4, `TITLE_Y` 0.25, `BODY_Y` 0.7, `BODY_W` 9.2, `BODY_H` 4.6. Two-column layouts use column width 4.4 with a 0.4 gap.

## Helper Functions

Build three helpers and use them everywhere: `dashItems(items, opts)` producing dash-prefixed runs (prefix `"-   "`, indented form `"      -   "`, per-item `fontSize`/`color`/`bold`/`indent` overrides, `breakLine` on all but the last, `paraSpaceAfter: 2`); `sectionHeader(text, opts)` for bold headers; and `spacer(fontSize)` emitting an empty run for vertical breathing room.

## Global Style Rules

- **Background**: Plain white on all slides (`slide.background = { color: C.white }`)
- **Font**: Arial throughout — no other fonts
- **Title**: fontSize 26, black, positioned at (TITLE_X, TITLE_Y), not bold
- **Body text**: fontSize 10, black
- **Bullets**: Use dash prefix (`"-   "`) — NOT unicode bullets. This matches the reference deck.
- **Section headers**: Bold, fontSize 10, black, with `paraSpaceAfter: 2`
- **Spacers**: Empty text runs with fontSize 6 between sections for breathing room
- **No background colors, gradients, or decorative elements** — clean and minimal
- **Two-column layouts**: Use column width 4.4 with 0.4 gap; thin gray vertical divider (color "DDDDDD", width 0.5)
- **Experiment keys are their own blue run.** An item on User Impact or Weekly Focus that names an experiment is authored as `{ exp: "ab:aiUserJournal", text: "Journal tab redesign + standalone composer" }`. `dashItems` emits the key in blue `2563EB` followed by `": "` and the description in black; the dash itself stays black so the bullet column reads uniformly. Keys carry their `ab:` / `abc:` / `abcd:` arm prefix exactly as Linear and Slack write it. A plain string item renders with no key, which is correct for work that is not an experiment.

## Chart rules (PptxGenJS)

Three behaviours of the library decide whether a chart renders correctly:

- **Gridlines are `valGridLine` / `catGridLine`.** `valAxisMajorGridLine` is not an option name — it is accepted and silently ignored, leaving default gridlines in place. Pass `{ style: "none" }` to remove them.
- **Category labels need a single-level string cache.** PptxGenJS always writes categories as `<c:multiLvlStrRef>`, which Google Slides cannot resolve — it numbers the axis 1, 2, 3 instead of showing the labels. After `writeFile`, reopen the `.pptx` (JSZip ships with pptxgenjs), and in every `ppt/charts/chart*.xml` rewrite each `<c:multiLvlStrRef>` holding exactly one `<c:lvl>` into the equivalent `<c:strRef>`/`<c:strCache>`, carrying `<c:f>`, `<c:ptCount>` and the `<c:pt>` list across. **Take `<c:ptCount>` from the `<c:multiLvlStrCache>`, not from inside `<c:lvl>`** — PptxGenJS writes it on the cache, outside the level, so a converter that searches only the `<c:lvl>` emits a `<c:strCache>` with no count. Google Slides reads a missing count as zero labels and numbers the axis 1, 2, 3 — the same symptom the conversion exists to fix — while the values, whose caches keep their count, still draw. Fail the build when a converted cache has no `<c:ptCount>` rather than writing it. Leave genuine multi-level categories alone, and log how many charts were converted.
- **Derive `valAxisMaxVal` from the content, never hardcode it.** Set it explicitly — an axis left to auto-scale exaggerates movement — but compute the ceiling from this week's data with headroom past the largest value, so outside-end data labels cannot clip at the plot edge. On a **stacked** chart the largest value is the per-category *sum across series*, not the largest single segment. A literal tuned to one week's numbers passes review that week and silently clips the next time a figure grows, which no error surfaces: the chart renders, the bar is just cut off at the plot edge. Round the derived value up to a whole step so the top gridline stays a round number. The one deliberate exception is a percentage axis, which is pinned (see Slide 5).
- **Declare the shared chart constants above the first slide that uses them.** Slides 5, 6 and 7 all read the `SHEETS` style tokens (and Slide 5 the categorical palette), and a `const` referenced before its declaration line throws on the temporal dead zone rather than reading as `undefined` — so the slide furthest up the file decides where the constants go.
- **Colour each stat delta by its own direction.** The insight lines under the velocity charts take deltas as `{ text, dir: "up" | "down" | "flat" }` and resolve glyph and colour per delta — `▲` + green for a rise, `▼` + red for a fall, medGray with no glyph for flat. Deltas on one line disagree whenever a week beats its prior week but trails its trend, so a helper that hardcodes one direction looks correct only for as long as every delta happens to point the same way.

Charts also need the `dataviz` discipline: pick the form from the data's job, assign categorical hues in fixed order so a colour always means the same series, and keep status colour reserved. Where a validated palette slot falls under 3:1 contrast, ship visible numbers beside the chart so identity never rests on colour alone.

### House chart style (Google Sheets look)

**Every chart in the deck uses one look: a default Google Sheets chart.** A thin blue line on
white, a gray title inside the frame at top-left, light gray horizontal gridlines, gray axis
text, week-range category labels turned vertical, and a thin gray frame around the whole chart.
Build it once as a helper that returns the shared options and spread it into every `addChart`
call, so no slide drifts:

```javascript
const SHEETS = { blue: "4285F4", text: "757575", grid: "E0E0E0", frame: "BDBDBD" };
function sheetsChart(title, { negative = false, legend = false } = {}) {
  return {
    showTitle: true, title, titleAlign: "left", titleFontFace: "Arial",
    titleFontSize: 13, titleColor: SHEETS.text, titleBold: false,
    chartArea: { fill: { color: "FFFFFF" }, border: { pt: 0.75, color: SHEETS.frame } },
    plotArea: { fill: { color: "FFFFFF" } },
    valGridLine: { color: SHEETS.grid, size: 0.75, style: "solid" },
    catGridLine: { style: "none" },
    valAxisLineShow: false,
    catAxisLineShow: true, catAxisLineColor: negative ? "000000" : SHEETS.grid,
    catAxisLabelPos: "low",
    catAxisLabelColor: SHEETS.text, valAxisLabelColor: SHEETS.text,
    catAxisLabelFontFace: "Arial", valAxisLabelFontFace: "Arial",
    catAxisLabelFontSize: 9, valAxisLabelFontSize: 9,
    showLegend: legend, legendPos: "b", legendFontSize: 9, legendColor: SHEETS.text,
  };
}
```

- **Line charts**: `lineSize: 1.5`, `lineDataSymbol: "none"` — a continuous line with no point markers. The first series always takes `SHEETS.blue`; a single-series chart passes `chartColors: [SHEETS.blue]` and no legend.
- **Title inside the frame.** The chart's own `title` replaces any bold header text box above it — the slide carries no separate chart header.
- **Category labels are week ranges, turned vertical.** Author weeks as `"M/D - M/D"` (Monday–Sunday, e.g. `"10/20 - 10/26"`) and set `catAxisLabelRotate: -90`. **The axis shows a compact form**: the generator maps each week through `axisWeek`, which drops the start date's month and the spaces around the dash (`"10/20 - 10/26"` → `"20-10/26"`), so the vertical labels take less of the frame. That is a display transform at chart time only — `deck-content.js` keeps the full `"M/D - M/D"` the contract defines. Week axes take `catAxisLabelFontSize: 8` (set on the shared `LINE` options); size the frame for the vertical labels rather than shrinking the font further.
- **Zero baseline.** When any value can go negative, pass `negative: true`: the category axis crosses at zero and draws as a 1pt black line — the bold zero rule — while `catAxisLabelPos: "low"` keeps the week labels at the bottom of the plot instead of riding the zero line. Set `valAxisMinVal` to a round step below the minimum. On all-positive data the axis line stays light gray and `valAxisMinVal` is 0.
- **Value axis**: gray labels, no axis line, horizontal gridlines only. The `valAxisMaxVal` derivation rule above still applies; pick `valAxisMajorUnit` so the plot carries 4–5 gridlines.
- **Legend only for multi-series charts** (`legend: true`), at the bottom in gray.

## Slide-by-Slide Layout Instructions

### Slide 1 — AI Section Divider
- Centered "AI" in fontSize 52, not bold
- Team members list below in fontSize 20, medGray color, centered
- This slide is static — team members: Steve, Jerome, Rohan, Farhan, Daniel O, Naveed, Ashish, Alyssa, Subin, Yawar

### Slide 2 — User Impact
- Title: "User Impact"
- **Subtitle**: Gray italic summary line below title — e.g. "1 experiment, 2 major features, 7 minor improvements, 10 bug fixes" (from Subagent 1 summary)
  - Position: `(TITLE_X, 0.6)`, fontSize 10, medGray, italic
- **Two-column layout** with vertical divider:
  - Left column `(x: BODY_X, w: 4.4)`: **Production** releases
  - Right column `(x: BODY_X + 4.8, w: 4.4)`: **TestFlight** releases
  - Column top: 1.0, height: 4.2
  - Vertical divider: thin gray line (color "DDDDDD", width 0.5) between columns
- Each column has:
  - Bold section header ("Production:" or "TestFlight:")
  - Version numbers as bold sub-headers (e.g. "Aurie 1.10.0")
  - Dash-prefixed items under each version

### Slide 3 — AI Consumer App - Weekly Focus
- Title: "AI Consumer App - Weekly Focus"
- **Two-column layout** with vertical divider:
  - Left column: **In Progress** items. Classify the displayed cards into **Major Features** (cards carrying the "Major Feature" label) and **Other** (the rest), then render **each non-empty group as its own vertically stacked text box** (a separate `slide.addText(...)` call) so each can be animated independently in Google Slides:
    - **Major Features box**: a bold "Major Features:" sub-header + its cards. Positioned at the column top.
    - **Other box**: a bold "Other:" sub-header + its cards. Positioned directly below the Major Features box.
    - The "In Progress:" header leads whichever box renders first.
    - **Emit a box only when its group has cards** — never a header-only box. If only one group has cards (no card carries the "Major Feature" label, or every displayed card does), render a single In Progress box at the column top and drop the empty group's sub-header.
  - Right column: **Planned** items — a single text box with header "Planned:"
  - Same column dimensions as User Impact slide
  - Column top: 0.75, total height 4.5 split between the left-column boxes
- Each left-column box shares the left column's `x`/`w` (`x: BODY_X`, `w: 4.4`); stack them top-to-bottom — size the upper box's height to fit its content and set the lower box's `y` directly below it so the two read as one continuous column.
- Each box: bold section header + dash-prefixed items.
- **Make the Major Features / Other split visually distinct** — when both boxes are present they must not read as one flat list:
  - Keep the "Major Features:" and "Other:" sub-headers **bold at the column body font size** (do not shrink them below the items).
  - **Indent** the dash items under each sub-header one level (e.g. the indented-dash form) so the sub-headers clearly stand above their items.
- The roll-up rule in Subagent 4 keeps the In Progress column short, so the boxes should fit at the normal body font (~10). Step the left column's body font down as the card count grows — ~9 above about 17 displayed cards, ~8 above about 22 (9pt holds roughly 22 single-line cards in this column, so a 26-card list at 9pt clips) — and size the Major Features box to its actual content so the slack goes to the longer "Other" box. Hold the sub-headers at 10pt bold throughout: the split has to keep reading as a split, so the headers never shrink below the items. Around 28 cards the column is full; if the count climbs past what 8pt holds, say so and ask whether to trim rather than shrinking further or letting the list clip.

### Slide 4 — Team metrics in focus
Source: the three OKR dashboards. The gathering procedure, the targets and the status rules
are in [`team-metrics-in-focus.md`](team-metrics-in-focus.md) — this section owns only how the
figures land on the slide.

- Title: `focusAreas.title`; gray italic subtitle naming the week and that the change is against the week prior
- **A five-column table, hand-laid rather than `addTable`** so each cell carries its own type scale. Column widths from `BODY_X`: metric 3.15, target 1.25, value 1.55, delta 1.45, status 1.8. Derive each column's `x` by accumulating those widths so the header row and every body row share one geometry — two independent lists of positions drift the moment a width changes.
- **Header row** at y 1.12: the five labels at fontSize 8 bold medGray, then a 1pt `rule` line beneath.
- **Grouped rows.** One small caps-ish header per `groups[]` entry (fontSize 8 bold darkGray, `charSpacing` 0.6) naming the source dashboard, then its rows. Row height 0.56, group lead 0.24, plus 0.06 between groups.
  - Each row sits on a `band`-filled rectangle spanning the body width — the eye has to cross five columns, and the band is what keeps it on one row.
  - `metric` at fontSize 12 bold; `note` beneath it at 7.5 italic medGray, allowed to run wider than the metric column since the target column starts clear of it.
  - `value` at fontSize 20 bold with `unit` at 10 in medGray. **A leading `≥` is part of the value** and must not be stripped.
  - `delta` at 11 bold: **glyph from the sign, colour from `dir`**. `▲` when the delta is positive, `▼` when negative; `C.green` when `dir` is `"good"`, `C.red` when `"bad"`. The two disagree routinely — "no review at all" rising is ▲ in red — so a helper that colours by sign passes only while every metric moves the same way.
  - `status` at 11 bold, text label from `{on: "ON TRACK", risk: "AT RISK", off: "OFF TRACK"}` in green / amber / red. **The label carries the state**; colour reinforces it and is never the only cue.
- **Footer** (y 5.24): italic medGray, one line defining the three statuses and what `≥` means.
- The row block is sized so five rows across three groups clear the footer. Past about six rows, shrink `ROW_H` before anything else — the value column is the slide's job and must stay at 20pt.

### Slide 5 — Code Review Metrics
Source: the Review Loop artifact. The gathering procedure, the section→slide mapping and the
direction rules are in [`pr-metrics-from-artifact.md`](pr-metrics-from-artifact.md) — this
section only owns how the numbers land on the slide.

- Title: "Code Review Metrics"
- **Subtitle** (`TITLE_X`, y: 0.66): gray italic, fontSize 10, medGray — "Week of {weekOf} · {scope}". Naming the week and the repo scope is load-bearing: these are Monday-start UTC weeks over every active repo, and the subtitle is where that is stated.
- **KPI row** (y: 1.0, h 0.85): five tiles across `BODY_W`, each 1.84 wide, left-aligned within the tile. Per tile, three stacked runs:
  - `label` at fontSize 8, medGray, uppercase
  - `value` + `unit` at fontSize 22 bold black — the unit at fontSize 11 in medGray. **A leading `≥` is part of the value**; it marks a floor resting on PRs still waiting and must not be stripped.
  - `delta` at fontSize 9 bold, led by the direction glyph
  - **Colour resolves from `dir`, not from the sign of the number.** `"down"` → `▼`, `"up"` → `▲`; whether that glyph is `C.green` or `C.red` depends on the metric, so the content module states it: falling is the improvement for the two timings and for "no review at all", rising is the improvement for "reviewed by a human". `dir: "none"` renders the glyph in **black** — that is "reviewed by AI only", which has no improvement direction. `dir: "flat"` renders "flat" in medGray with no glyph.
- **Two charts side by side** (y: 1.95, h 2.85), column width 4.4 with a 0.4 gap, in the house chart style with `legend: true`; each chart's title sits inside its frame:
  - **Left — "Review coverage (% of PRs merged, sums to 100)"**: line chart, all **three** shares on one chart (human / AI only / no review). **Lines, not a stacked column**: only the bottom band of a stack has a fixed baseline, so the middle series' movement reads as the sum of two changes and cannot be tracked week to week — which is exactly what an 8-week trend panel exists to show. The three still sum to 100%, and the chart title is where that is said, since lines do not show it.
    - `valAxisMaxVal: 100`, `valAxisMinVal: 0`. Fixed, even though the data rarely passes 65 and the top of the plot goes unused — a percentage axis that rescales to the week's maximum exaggerates movement and breaks comparison against last week's slide.
    - Human takes `SHEETS.blue`, AI-only `SERIES[1]`, and **"No review" takes `C.medGray`** — it is the residual of the other two, and no categorical slot carries the right valence (green would read as good, amber as a near-duplicate of the AI orange).
  - **Right — "Time to first review (hours)"**: line chart, two series (human, AI) on **one** chart, y in **hours** to match the KPI tiles so the slide carries a single unit. Human takes `SHEETS.blue`, AI `SERIES[1]` — the same colours as on the coverage chart.
  - The AI line sits near the axis at this scale. That is the finding, not a rendering fault — AI first-response runs in minutes to hours against days for a human — so say it in the takeaway rather than rescaling to hide it.
  - A `null` in `firstReview` is a week with nothing measurable; leave the gap rather than plotting zero.
- **Footer** (y: 4.85): italic medGray — "N PRs became ready · N merged · N drew a human review" plus the still-waiting count whenever any value carries a `≥`.
- **Key takeaway** (y: 5.1): bold header + one line on the week's review trend.
- Both charts run over the **8 complete weeks** in `weeks`; the partial current week never appears. Eight points rather than five because weekly `n` is small — a single stale PR swings a point, and five is too few to tell a trend from a bounce. Eight is also about the ceiling for this width: the vertical week-range labels at fontSize 9 sit about 0.45in apart in a 4.4in frame, so a ninth column starts crowding them.

### Slide 6 — Velocity (WoW)
- Title: "Velocity (WoW)"; gray italic subtitle (`TITLE_X`, y 0.62) naming both sources and their scopes — the Effort measure and what the insights view excludes, then that the feature counts are Product/Design features from the Product Delivery dashboard.
- **Three line charts in one row** (y 0.95, h 2.8), each 2.93 wide with a 0.2 gap — x 0.4, 3.53 and 6.66 across `BODY_W` — in the house chart style, title inside the frame. Read left to right they are the team's input, its output, and the work entering the pipeline:
  - **Left — "Team Velocity per Week"**: a single `SHEETS.blue` series, no legend — the weekly team effort total. The generator derives each week's total by summing `velocityTrend.members[].values` at that index, so the chart cannot disagree with the per-member figures beneath it. One line, not a stack or one line per member: the question is whether the team's output is rising, and a single slope answers it from the back of the room. The per-member split lives in the insight lines, written with display names.
  - **Middle — "Product Features Released"**: `velocityTrend.features.released.product`.
  - **Right — "Product Features Handed Off"**: `velocityTrend.features.handedOff.product`.
  - **Each feature chart is a single `SHEETS.blue` line of product features, no legend.** The question the slide answers is whether velocity turns into product work, so product is what is charted; the engineering count is a number in the insight lines, not a line. The titles drop "per Week" because the longer form overruns a 2.93in frame at fontSize 13.
- **All three share `velocityTrend.weeks` as categories** — the last 13 complete weeks, oldest first — so one week sits at the same x position in every frame and a step in one chart can be read against the same week in the other two. At 13 weeks across 2.93in the vertical labels sit about 0.2in apart: set `catAxisLabelFontSize: 7` on all three and keep every label; skipping labels in one frame breaks the column-to-column reading. Reserve about 0.7in of each frame's height for them.
- **The two feature charts share one value axis**; the velocity chart scales its own. Equal heights in the middle and right frames then mean equal counts, which is what makes "handed off above released" visible at a glance; Effort is a different unit and is never put on that scale. Derive the shared `valAxisMaxVal` from the larger of the two product series, use a whole-number `valAxisMajorUnit` with `valAxisLabelFormatCode: "0"`, and keep `valAxisMinVal` at 0 on all three — none of the series goes negative.
- A `null` in any feature series is a week the published page cannot report yet; leave the gap rather than plotting zero.
- **Insight lines** (x `BODY_X`, y 3.9, w `BODY_W`, h 1.45): one line per chart in chart order, on the model of a GTM update — a bold label, a spaced dash, then the figures — then the takeaway. fontSize 11, black:
  - **Velocity** — `value`, then its two deltas (vs prior week, vs the 4-week trend). Its `detail` — the per-member changes, with display names and **▼/▲ glyphs**, not `-`/`+` signs — runs on the next line, indented, at fontSize 9.
  - **Features released** — `value` as the product and engineering counts, with no total: `13 product, 2 eng`. Then its deltas, which are on product.
  - **Features handed off** — `value` in the same form, then its deltas on product.
  - **The takeaway** — `velocityTrend.takeaway` as a plain sentence of its own, with **no bold label**, set apart from the three figure lines by a gap of about 0.12in (a fontSize 6 spacer run, or `paraSpaceBefore: 8` on its paragraph). It says whether velocity and product features released moved together against their 4-week averages, and when they did not, the reason the dashboard data supports. It may wrap to a second line.
  - **Each delta is coloured by its own direction**, on the same convention as Code Review Metrics: a rise is an improvement for all three — more effort, more shipped, more handed to engineering, the direction the Product Delivery page itself grades handoffs — so `▲` + `C.green`, `▼` + `C.red`. The glyph carries direction as well, so colour is never the only cue. Everything else on the lines stays black: the deltas are the slide's only status colour, and colouring the labels or the takeaway as well flattens the distinction.

### Slide 7 — Effort by Label — Last Week
- Title: "Effort by Label — Last Week"; gray italic subtitle naming the measure and window
- **Left — effort mix** (x `BODY_X`, w 3.6, y 1.05): a bold "Effort mix" header, then one row per category with the percentage large (fontSize ~22, bold) and its name small beside it. The categorisation and row order are owned by the *Effort by Label* steps in [`../prepare-monday-deck.md`](../prepare-monday-deck.md) — three standing categories in fixed order, then any residual label promoted at over 20%, then "Other" last in medGray. The breakdown reads first because it is the summary; the chart beside it is the detail.
- **Right — horizontal bar chart** (x 4.2, y 1.05, w 5.6, h 3.7) in the house chart style, titled **"Effort by Label (top 7)"**: the **top 7 labels only**, largest first. PptxGenJS draws horizontal bars bottom-up, so reverse the rows to get descending order top-down.
  - `barDir: "bar"`, single series in `SHEETS.blue`, no legend, `showValue: true` with `dataLabelPosition: "outEnd"` and data labels at fontSize 11 in `SHEETS.text`.
  - Category labels are the label names, **not** rotated — override `catAxisLabelRotate` to 0 and `catAxisLabelFontSize` to 10 so they read from the back of a room. The value axis keeps its gray labels with light gray gridlines running vertically from it.
  - **Keep every category label short enough for the gutter.** The category column is about 1.95in, which holds roughly 32 characters at fontSize 10; a longer label is silently clipped from the left, so shorten it in `labelEffort.rows` (Linear's "Infrastructure, Reliability & Performance" ships as "Infra, Reliability & Performance"). A label only long enough to clip once it enters the top 7 will pass every earlier week unnoticed — check the rendered replica, not the rank.
- The mix is computed over **all** labels even though the chart shows seven, so a label can appear as a bar while the mix folds it into a category or into "Other". That split is intentional: any bar-to-percentage reconciliation from the floor is answered by naming the category the label sits in.

### Slide 8 — What Worked / Didn't Work
- Title: "What Worked / Didn't Work"
- **Single-column stacked layout** (NOT side-by-side — avoids unbalanced columns)
- **Consolidated thematic bullets, not grouped by individual** — merge each person's points into theme-based bullets and drop the names
- Render the two halves as **separate, vertically stacked text boxes** (distinct `slide.addText(...)` calls) so each can be animated independently in Google Slides:
  - **What Worked Well box**: "What Worked Well:" bold section header → dash-prefixed items. Positioned at the body top.
  - **What Didn't Work Well box**: "What Didn't Work Well:" bold section header → dash-prefixed items. Positioned directly below the What Worked Well box (or at the body top if it is the only box).
  - **Emit a box only when its half has items** — never a header-only box. If the retro has only one half, render that single box at the body top.
- Each box shares the body `x`/`w` (`x: BODY_X`, `w: BODY_W`); size the upper box's height to fit its list, and set the lower box's `y` directly below it so the two read as one continuous stacked column.
- If no retro found, skip this slide entirely

## File Output

Before writing the file, determine today's date in `YYYY-MM-DD` format and construct the output path:

```javascript
const path = require("path");
const fs = require("fs");
const today = new Date().toISOString().slice(0, 10); // e.g. "2026-04-10"
const outputDir = path.join(process.cwd(), "reports", "prepare-monday-deck");
fs.mkdirSync(outputDir, { recursive: true });
const outputPath = path.join(outputDir, `${today}.pptx`);

pres.writeFile({ fileName: outputPath })
  .then(() => console.log(`Created: ${outputPath}`))
  .catch(err => console.error(err));
```

---

## Visual QA

**Skip this section if `outputFormat = "text"`.**

After generating the PPTX, perform visual QA. Verify against what actually rendered — never report the deck as checked on the strength of the script having run without error.

**With LibreOffice available**, convert the whole deck and read the pages:

```bash
soffice --headless --convert-to pdf <deck>.pptx
pdftoppm -jpeg -r 200 <deck>.pdf slide
```

**Without LibreOffice**, render slides one at a time through macOS QuickLook. It previews only the first slide of a file, which is what the `QA_SLIDE` hook is for — build a single-slide deck, then thumbnail it:

```bash
QA_SLIDE=7 QA_DIR=<scratch> node create-deck.js
qlmanage -t -s 2000 -o <scratch> <scratch>/qa-slide-7.pptx
```

Read each image and check: no text overlapping or cut off, columns balanced and dividers rendering, table rows aligned with the change values coloured, font sizes readable, and no empty slides.

Render **every** text slide through the hook rather than spot-checking a couple. A wrong key name in `deck-content.js` reads as valid JavaScript and surfaces only in the raster, as a literal `undefined:` where the section header belongs.

**QuickLook does not rasterize embedded charts** — chart areas come out blank even when the chart is perfectly well-formed, so a blank chart frame is not evidence of a bug. **Three** slides carry charts: Code Review Metrics (5), Velocity (6, three charts) and Effort by Label (7). Verify each by two other means instead:

1. **Inspect the chart XML** in `ppt/charts/chart*.xml`: series names and values, category count and text, `barDir`, `showVal`, axis min/max, and the house style — exactly one `<c:majorGridlines>` block (on the value axis, colour `E0E0E0`), `<c:symbol val="none"/>` on every line series, a `<c:title>` with `algn="l"`, `<c:tickLblPos val="low"/>` with `rot="-5400000"` on a week axis, and the `BDBDBD` chart-area border.
2. **Render a to-scale replica** of the chart frames as static SVG in an HTML file at the same inches-to-pixels scale and font sizes, and thumbnail that with `qlmanage`. This is what catches label collisions, clipped value labels and unreadable category text. Build the replica from `deck-content.js` so it cannot drift from the deck, declare `<meta charset="utf-8">` (or em dashes mangle), and put one slide per file.

**Size the replica to about 1000px wide.** QuickLook renders an HTML page inside its own viewport and clips whatever overflows it, so a replica drawn at full slide resolution loses its entire right-hand side — the Velocity row's third chart and the Effort by Label bars are exactly what a chart replica exists to check. At 100px per inch a 10in slide is 1000px and fits, and `-s 2000` upscales it 2x for reading. Padding the canvas out to a square does not help; the clip is horizontal, against the viewport, not a square crop of the page.

Fix findings in `create-deck.js` and regenerate. If a chart's rendering still cannot be confirmed, say so plainly and ask the user to eyeball those slides — do not describe unverified output as verified.

---
