---
name: report-aurie-usage
description: >
  Report daily Aurie usage (DAU) over a chosen window — last 7 complete days (default), last
  30 complete days, week-to-date, month-to-date, previous month, yesterday, today, the most
  recent complete Sun-Sat, or a custom `--from`/`--to` range. Splits DAU three ways per day:
  any Aurie session, conversational only, and Quick Process only. Output is a markdown table
  with a one-line headline (avg DAU + % conversational), pulled live from the Mixpanel MCP.
  Optional Slack post. Trigger on "how many daily Aurie users", "aurie DAU", "aurie usage
  report", "how many conversational users", "daily aurie users by session type", "aurie usage
  by day", "aurie weekly DAU", "report aurie usage", "aurie engagement", or a question about
  how many people used Aurie yesterday / this week / this month. Do NOT use for per-user cost
  (`eng:report-top-conversation-users`), weekly cost totals (`eng:report-aurie-weekly-costs`),
  or content analysis (`data:report-conversation-patterns`).

metadata:
  version: "3.0.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/Mixpanel  # event taxonomy — confirms the canonical Aurie session-start event names
    - Tools/Slack     # channel directory + Post format footer spec for the optional Slack post
---

# Report Aurie Usage

Counts unique daily Aurie users in Mixpanel project `963821` (Aura Health) across the three canonical Aurie touchpoints. Renders the per-day table inline.

This is the aggregate-DAU sibling of:

- [`eng:report-aurie-weekly-costs`](../../../eng/skills/report-aurie-weekly-costs/SKILL.md) — same domain, cost flavour.
- [`eng:report-top-conversation-users`](../../../eng/skills/report-top-conversation-users/SKILL.md) — same domain, per-user flavour.
- [`eng:analyze-listens`](../../../eng/skills/analyze-listens/SKILL.md) — same shape (Mixpanel MCP + window resolver + Python formatter + Step 4 routing menu).

## Events (confirmed via the Mixpanel MCP `Get-Events`, 2026-05-18)

| Column          | Mixpanel event                                       | What it represents                                |
| --------------- | ---------------------------------------------------- | ------------------------------------------------- |
| Any Aurie       | `AI Conversation/Quick Process Session Started`      | Umbrella — fires for either session type          |
| Conversational  | `AI Conversation Session Started`                    | Voice/text dialogue session start (post-onboarding) |
| Quick Process   | `AI Quick Process Session Started`                   | Guided-journal session start                      |

`Chat Message Sent` (the deprecated text-only chat path) runs 0–4 unique users/day and is intentionally **not** reported here.

## Step 0 — Check prerequisites

- The **Mixpanel MCP** must be connected — this skill calls `mcp__3330833b-…__Run-Query` and (optionally) `mcp__3330833b-…__Get-Query-Schema`. If the MCP is missing, tell the user and stop.
- `python3` is on the path (no third-party packages — the helper scripts are stdlib only).
- The consumer project ID is **`963821`** (Aura Health). If the user explicitly names a different project, confirm before proceeding.

## Step 1 — Resolve the time window

Ask the user which window they want. Presets, in order of likelihood:

- **Last 7 complete days** (`last-7d-complete`, **default**) — the seven most recent full days ending at 00:00 UTC today. Never includes today's running total.
- **Most recent complete Sun–Sat week** (`--weekly`) — full weekly cadence.
- **Last 30 complete days** (`last-30d-complete`) — same idea, 30-day window.
- **Yesterday** (`yesterday`) — single complete day.
- **Week-to-date** (`wtd`) — Monday-anchored, includes today's running total.
- **Month-to-date** (`mtd`)
- **Previous month** (`prev-month`)
- **Today** (`today`) — current day's running total (call out as partial).
- **Last 7 / 30 days rolling** (`last-7d`, `last-30d`) — include today's partial bucket.
- **Custom range** — explicit `--from YYYY-MM-DD --to YYYY-MM-DD` (UTC, both **inclusive**). Pass the same date on both flags for a single-day window.

If the user names a month directly ("April", "2026-04"), translate to a `--from`/`--to` pair covering the full calendar month.

Resolve via the helper so the dates are deterministic:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-aurie-usage/scripts/resolve_window.py
# default → {"from": "...", "to": "...", "label": "last 7 complete days (...)", "days_in": 7}

python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-aurie-usage/scripts/resolve_window.py --weekly
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-aurie-usage/scripts/resolve_window.py --window yesterday
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-aurie-usage/scripts/resolve_window.py --from 2026-04-01 --to 2026-04-30
```

Keep `from`, `to`, `label`, and `days_in` — Step 2's Mixpanel payload and Step 3's headline both need them.

## Step 2 — Run the Mixpanel query

Build an Insights report with three `unique`-math metrics over a daily line chart:

```json
{
  "name": "Aurie DAU by session type",
  "metrics": [
    { "eventName": "AI Conversation/Quick Process Session Started", "measurement": { "type": "basic", "math": "unique" } },
    { "eventName": "AI Conversation Session Started",               "measurement": { "type": "basic", "math": "unique" } },
    { "eventName": "AI Quick Process Session Started",              "measurement": { "type": "basic", "math": "unique" } }
  ],
  "chartType": "line",
  "unit": "day",
  "dateRange": {
    "type": "absolute",
    "from": "<from from Step 1>",
    "to":   "<to from Step 1>"
  }
}
```

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
# Write the Run-Query JSON output to /tmp/aurie_usage_<from>_<to>.json
```

> **Why three metrics instead of one breakdown?** The three Aurie session-start events have no shared property to break on — `Conversation` and `Quick Process` are different event names, not values of a shared property. The umbrella event `AI Conversation/Quick Process Session Started` is a Mixpanel custom event that already unions them.
> **Why `unique` math?** We want DAU (people), not session count. A user who starts three Aurie sessions in a day counts once.

## Step 3 — Format the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-aurie-usage/scripts/format_report.py \
  --input /tmp/aurie_usage_<from>_<to>.json \
  --label "<label from Step 1>" \
  --days-in <days_in from Step 1>
```

The formatter:

- Maps each Mixpanel metric series (keyed like `"A. AI Conversation Session Started [Unique Users]"`) to one of the three canonical columns. Unknown metrics are skipped silently — extra series in the payload don't break the table.
- Renders one row per day (most recent last), in calendar order.
- Adds an `Avg` footer row averaging across all days in the window (simple mean of per-day DAU). Days missing from a series count as 0 for that column.
- Detects the trailing day as `*(partial)*` if its values look noticeably below the prior days' mean (across at least two columns), then **excludes it from both the `Avg` row and the headline averages**. A `*Partial day (YYYY-MM-DD) excluded from Avg.*` footnote prints under the table when this happens.
- Headline = average any-Aurie DAU + conversational share (`Conversational avg / Any Aurie avg`) + Quick Process avg, all computed on complete days only. The conversational share is a user-overlap ratio, not a session ratio.

Output shape:

```markdown
**Aurie DAU — <label>**

~<X> DAU avg · conversational share <Y>% · Quick Process avg ~<Z>

| Day | Any Aurie | Conversational | Quick Process |
| --- | ---: | ---: | ---: |
| Mon May 11 | 177 | 164 | 27 |
| ...
| **Avg** | **160** | **149** | **26** |
```

Show this verbatim inline so the user sees the breakdown immediately.

If the user only wants the headline (for compact Slack copy/paste), pass `--headline-only`.

## Step 4 — Ask where to route it

After the report lands, ask the user what to do next. **Do NOT route automatically.**

1. **Post a summary to Slack** *(optional, requires Slack MCP)*. Two channel framings (IDs from `Tools/Slack` in AI Knowledge):
   - **`#eng-budget`** (`C05PC18APLH`) — usage in the cost-budget context.
   - **`#ai-product`** (`C04R4L67676`) — usage in the product context.

   Ask the user which framing before posting — don't silently default. Post via `shared:post-to-slack` with `--footer data:report-aurie-usage --footer-extra prod` — the helper stamps the versioned footer for either channel; don't hand-type one in the body.

2. **Save a Markdown summary** to a path the user provides.

3. **Nothing, just show it** — stop here.

## Slack message format (Step 4 option 1)

```markdown
**Aurie DAU — <label>**

- **Avg DAU:** ~<X> across all Aurie touchpoints (<days_in> days)
- **Conversational share:** <Y>% of Aurie DAUs started a dialogue session
- **Quick Process avg:** ~<Z>/day

| Day | Any Aurie | Conversational | Quick Process |
| --- | ---: | ---: | ---: |
| ...

[Open in Mixpanel](https://mixpanel.com/project/963821)
```

For `#ai-product` lead with conversational share; for `#eng-budget` lead with the avg DAU total.

## Notes & edge cases

- **Numbers may lag the Mixpanel UI by up to a few hours** for the current day's bucket. Mention this if the user is comparing to a value they just saw in the Mixpanel dashboard.
- **`Chat Message Sent` is excluded.** Text-only chat is effectively zero (0–4 unique users/day) and the event itself is on a deprecation path. If the user explicitly asks for chat, run a separate one-off query — don't reshape this skill.
- **`Conversational + Quick Process > Any Aurie` is expected.** A user who does both a conversation *and* a Quick Process in the same day counts once in `Any Aurie` but in both of the other columns. The `conversational share` headline normalises against `Any Aurie`, which is the right denominator for "what fraction of Aurie users had a real conversation".
- **Empty windows.** If `Run-Query` returns zero days, the formatter prints `No Aurie sessions in this window.` and exits 0 — not an error.
- **Multiple Mixpanel projects.** Only `963821` (Aura Health) is in scope. If the user asks about Project Aura Dev (`970535`) or Aura Pre Prod (`3971139`), stop and confirm before changing the `project_id`.
- **Cowork sandboxes** strip host env (PROD-1305), so the Slack MCP may not be available there. If a Step 4 Slack post fails with a connection issue, tell the user to run the skill outside Cowork.

## References

- Sibling: [`eng:analyze-listens`](../../../eng/skills/analyze-listens/SKILL.md) — same Mixpanel-MCP shape (window resolver + formatter + routing menu).
- Sibling: [`data:fetch-conversation-topics`](../fetch-conversation-topics/SKILL.md) — Step 4 routing pattern.
- Parent ticket: [PROD-1933](https://linear.app/aura-health/issue/PROD-1933/add-datareport-aurie-usage-skill) (sub-issue of [PROD-1928](https://linear.app/aura-health/issue/PROD-1928/update-knowledge-and-skills-cycle-27)).
