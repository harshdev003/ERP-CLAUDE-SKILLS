---
name: analyze-listens
description: >
  Report meditation listen counts (the `Meditation done single` Mixpanel event) broken down by
  app — Aura mobile, Aurie mobile, Web — over a chosen window: today, week-to-date,
  month-to-date (default), previous month, last 7 days, last 30 days, or a custom range.
  Optional `--by-day` for a time-series trend. Output is a markdown table with rows per app
  plus total and share of total, and a one-line headline (listens MTD, per-day run rate, Aurie
  share). Pulls data via the Mixpanel MCP so the report is reproducible from inside Claude.
  Optionally routes a summary to Slack #eng-budget or #ai-product. Trigger on "analyze
  listens", "listens report", "how many listens this month", "listen counts by app",
  "meditation done single breakdown", "aurie vs aura listens", "web vs mobile listens", "MTD
  listens", "weekly listens", "daily listens", or a question about how many meditations were
  completed across our apps. Do NOT use for per-content or per-user breakdowns, or
  cost-per-listen analysis.

metadata:
  version: "3.0.2"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/Mixpanel  # event/property taxonomy — confirms the breakdown key for `Meditation done single`
    - Tools/Slack     # channel directory + Post format footer spec for #eng-budget / #ai-product post
---

# Analyze Listens

Counts the `Meditation done single` Mixpanel event in the consumer project, broken down by app (Aura mobile, Aurie mobile, Web), over a chosen time window. Renders a markdown table inline.

Mirrors the time-window and routing conventions of the sibling skill [`eng:analyze-openai-costs`](../analyze-openai-costs/SKILL.md) — same window flags, same Step 5 routing menu, same headline-first formatting.

## Step 0 — Check prerequisites

- The **Mixpanel MCP** must be connected — this skill calls `mcp__3330833b-…__Run-Query` and `mcp__3330833b-…__Get-Properties`. If the MCP is missing, tell the user and stop.
- `python3` is on the path (no third-party packages required — the helper script uses stdlib only).
- The consumer project ID is **`963821`** (Aura Health). Confirm with the user if they want a different project; otherwise hardcode this value.

## Step 1 — Resolve the time window

Ask the user which window they want. Presets:

- **Today** — UTC start-of-day → now (daily snapshot)
- **Week-to-date** — Monday 00:00 UTC → now
- **Month-to-date** (default) — 1st of current calendar month UTC → now
- **Previous month** — full prior calendar month (UTC)
- **Last 7 days** — rolling 7d ending now
- **Last 30 days** — rolling 30d ending now
- **Custom range** — explicit `--from YYYY-MM-DD --to YYYY-MM-DD` (UTC, half-open)

If the user names a month directly ("April", "2026-04"), translate to a `--from` / `--to` pair covering that full calendar month.

Resolve via the helper script so the dates are deterministic:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-listens/scripts/resolve_window.py --window mtd
# → {"from": "2026-05-01", "to": "2026-05-13", "label": "month to date (May 2026)", "days_in": 13}
```

Other window forms:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-listens/scripts/resolve_window.py --window today
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-listens/scripts/resolve_window.py --window wtd
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-listens/scripts/resolve_window.py --window prev-month
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-listens/scripts/resolve_window.py --window last-7d
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-listens/scripts/resolve_window.py --window last-30d
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-listens/scripts/resolve_window.py --from 2026-04-01 --to 2026-05-01
```

Keep the resolved `from` / `to` / `label` / `days_in` values — Step 2's Mixpanel query and Step 4's headline both reuse them.

## Step 2 — Breakdown rule (confirmed)

Split by **two** properties: `Source App` (Aurie vs Aura) plus `$browser` (mobile vs web within Aura — note the raw system-property name, see Step 3). Four canonical buckets:

| `Source App` | `Browser` | Bucket |
| --- | --- | --- |
| `aurie mobile` | any | **Aurie (mobile)** |
| `aurie desktop` | any | **Aurie (web)** |
| not set | not set | **Aura (mobile)** |
| not set | set (any value) | **Aura (web)** |

`Source App` is only populated by the Aurie product — Aura events leave it null, and `Browser` is the proxy for mobile vs web within Aura. Full rationale and edge cases live in [`references/breakdown-key.md`](references/breakdown-key.md) and the cross-referenced [`Tools/Mixpanel`](https://www.notion.so/34c75e23af20817baddaf32213eba8d5) page (look for "App-surface bucketing"). Confirmed with Yawar (Data Analyst) on 2026-05-13 in [`#engineering-mixpanel`](https://auramobile.slack.com/archives/C030BQ2H3QS/p1778697768929349).

If a new `Source App` value appears (e.g. `aura mobile`), update `BUCKET_RULES` in `scripts/format_report.py` + the table above + the breakdown-key reference in the same commit. Unknown values route to **Other / unknown** until then.

## Step 3 — Run the Mixpanel query

Build an Insights report with the `Meditation done single` event, **total count**, broken down by `Source App` then `$browser`, over the window from Step 1.

> **`$`-prefix gotcha.** Mixpanel system properties (auto-collected by the SDKs — browser, OS, device, etc.) are stored under a `$`-prefixed **raw** name but shown in the Mixpanel UI under their **display** name. The Insights API requires the raw name. Passing the display name (e.g. `"Browser"`) returns a query that succeeds but breaks down to a single `undefined` row — the breakdown is silently empty. Use `$browser` here, not `Browser`. The general rule (and a table of common system properties) lives at [`Tools/Mixpanel`](https://www.notion.so/34c75e23af20817baddaf32213eba8d5) → "System property naming (`$`-prefix gotcha)". When in doubt, call `Get-Properties` with the display name to confirm the raw name.

Minimal `report` payload for `Run-Query` (note: each breakdown item is wrapped in a `metric` object — that wrapper is mandatory per the live `Get-Query-Schema`):

```json
{
  "name": "Listens by app",
  "metrics": [
    {
      "eventName": "Meditation done single",
      "measurement": { "type": "basic", "math": "total" }
    }
  ],
  "breakdowns": [
    { "metric": { "type": "property", "propertyName": "Source App" } },
    { "metric": { "type": "property", "propertyName": "$browser" } }
  ],
  "chartType": "table",
  "dateRange": {
    "type": "absolute",
    "from": "<from from Step 1>",
    "to": "<to from Step 1>"
  }
}
```

If the user asked for `--by-day`, change `chartType` to `"line"` and add `"unit": "day"` so the response returns a per-day series under each (Source App, `$browser`) pair instead of a single total.

Call:

```text
Run-Query(project_id=963821, report_type="insights", report=<payload above>)
```

If the schema doesn't match (Mixpanel rejects the payload), fetch the full schema first:

```text
Get-Query-Schema(report_type="insights")
```

Save the raw response to a temp JSON file so the formatter can parse it without re-running the query:

```bash
# Write the Run-Query JSON output to /tmp/listens_<from>_<to>.json
```

## Step 4 — Format the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-listens/scripts/format_report.py \
  --input /tmp/listens_<from>_<to>.json \
  --label "<label from Step 1>" \
  --days-in <days_in from Step 1>
```

The formatter:

- Maps each `(Source App, Browser)` pair to one of the four canonical buckets — **Aurie (mobile)**, **Aurie (web)**, **Aura (mobile)**, **Aura (web)** — plus an **Other / unknown** catch-all for `Source App` values not in `BUCKET_RULES` (so nothing is silently dropped).
- Normalises Mixpanel's "not set" sentinels (`$non_property_value`, `(not set)`, empty string, etc.) to a single canonical "not set" before bucketing.
- Sorts rows by `DISPLAY_ORDER` (Aurie first because most reports lead with its share).
- Computes share of total per bucket. The headline's "Aurie share" combines both Aurie buckets (mobile + web).
- Adds a one-line headline above the table with total, run-rate, and Aurie share.

Output shape:

```markdown
**Listens — <label>**

<X> listens · <Y/day run rate> · Aurie share <Z>%

| App | Listens | % of total |
| --- | ---: | ---: |
| Aurie (mobile) | … | … |
| Aurie (web) | … | … |
| Aura (mobile) | … | … |
| Aura (web) | … | … |
| Other / unknown | … | … |
| **Total** | … | 100% |
```

Show this verbatim inline so the user sees the breakdown immediately.

### `--by-day` trend view

When the user asked for a daily trend, run the formatter with `--by-day`:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-listens/scripts/format_report.py \
  --input /tmp/listens_<from>_<to>.json \
  --label "<label>" --days-in <days_in> --by-day
```

This appends a second table with one row per day and one column per app, plus a `Total` row at the bottom.

## Step 5 — Ask where to route it

After the report lands, ask the user what to do next:

1. **Post a summary to Slack** *(optional, requires Slack MCP)* — compact message with the headline + per-app table. Two channel framings (IDs from `Tools/Slack` in AI Knowledge):
   - `#eng-budget` (`C05PC18APLH`) for cost-flavoured framings
   - `#ai-product` (`C04R4L67676`) for product-flavoured framings

   Ask the user which framing before posting — do not silently default. Post via `shared:post-to-slack` with `--footer eng:analyze-listens --footer-extra prod` — the helper stamps the versioned footer for either channel; don't hand-type one in the body.
2. **Save a Markdown summary** to a path the user provides.
3. **Nothing, just show it** — stop here.

Do NOT route automatically; always wait for the user's pick.

## Slack message format (Step 5 option 1)

```markdown
**Listens — <label>**

- **Total:** <X> listens across <N> apps (<days_in> days in)
- **Run-rate:** ~<Y>/day
- **Top app:** <name> (<share>% of total)

| App | Listens | % of total |
| --- | ---: | ---: |
| Aurie (mobile) | … | … |
| Aurie (web) | … | … |
| Aura (mobile) | … | … |
| Aura (web) | … | … |

Mixpanel views: [mobile](https://mixpanel.com/project/963821/view/30768/app/insights/#csk7JLjKjZdL) · [web](https://mixpanel.com/project/963821/view/30768/app/insights/#6yrJpHUGzF55)
```

Notes:

- Cap the table at the four canonical buckets. If `Other / unknown` is above ~5% of total, include it as a fifth row and flag it in the headline so it can be investigated; otherwise drop it from the Slack version (it still appears in the inline chat output).
- Lead with the share number that's most informative to the channel — for `#ai-product` that's usually Aurie share; for `#eng-budget` it's the total.

## Notes & edge cases

- **Numbers may lag the Mixpanel UI by up to a few hours** for the current day's bucket. Mention this if the user is comparing to a value they just saw in the Mixpanel dashboard.
- **Empty windows.** If `Run-Query` returns zero buckets for the window (e.g. a future date range), the formatter prints `No listens recorded in this window.` and exits 0 — not an error.
- **Property values change.** If a new `Source App` value appears (e.g. a future Aura release starts tagging `aura mobile` / `aura web`), the formatter routes it to `Other / unknown`. Tell the user and extend `BUCKET_RULES` in `scripts/format_report.py` plus the canonical tables in [`references/breakdown-key.md`](references/breakdown-key.md) and [`Tools/Mixpanel`](https://www.notion.so/34c75e23af20817baddaf32213eba8d5) in the same commit.
- **Multiple Mixpanel projects.** Only project `963821` (Aura Health) is in scope. If the user asks about a different project, stop and confirm before proceeding — there's no automatic discovery.
- **Cowork sandboxes** strip host env, so Slack/Mixpanel MCP connections may not be available there. If `Run-Query` errors with a connection issue, fall back to telling the user to run the skill outside Cowork.

## References

- [`references/breakdown-key.md`](references/breakdown-key.md) — confirmed bucketing rule, rationale, and edge cases. Cross-referenced from [`Tools/Mixpanel`](https://www.notion.so/34c75e23af20817baddaf32213eba8d5) → "App-surface bucketing".
- Mixpanel views to mirror:
  - Mobile (Aura + Aurie): <https://mixpanel.com/project/963821/view/30768/app/insights/#csk7JLjKjZdL>
  - Web (Browser): <https://mixpanel.com/project/963821/view/30768/app/insights/#6yrJpHUGzF55>
- Sibling skill: [`eng:analyze-openai-costs`](../analyze-openai-costs/SKILL.md) — same window flags, same routing pattern.
- Parent ticket: [ENG-509](https://linear.app/aura-health/issue/ENG-509/create-enganalyze-listens-skill) (sub-issue of ENG-497 Cost Analysis).
