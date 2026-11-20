---
name: create-mixpanel-alert
description: >
  Prepare Mixpanel alerts for Aura and Aurie: size thresholds from real counts, build the saved
  Insights report behind each alert on one Mixpanel board with the service account, verify every
  report through the Insights query API, and hand a person a one-line-per-alert checklist to add
  the alert in the Mixpanel UI (the service account cannot create alerts). Handles "zero events
  in N hours" rules with a rolling window and "more than N a day" spike rules. Trigger phrases:
  "create a Mixpanel alert", "add Mixpanel alerts", "alert in Mixpanel when X stops",
  "Mixpanel alert on subscriptions", "set up Mixpanel alerts for renewals / refunds / payment
  pending", "build the reports for these Mixpanel alerts". This is the Mixpanel complement to
  `eng:create-gcp-logs-alert` (log-based alerts). Do NOT use it to read or analyse Mixpanel data
  (`data:analyze-user-behavior`, `eng:search-aura-events`), to design events
  (`data:mixpanel-data-design`), or for log-based alerts (`eng:create-gcp-logs-alert`).
metadata:
  version: "1.0.0"
knowledge:
  reads:
    - Guide/Index
    - Tools/Mixpanel
    - Tools/Slack                    # checking a live alert's first post in its channel
---

# Create Mixpanel Alert

## Goal

Turn an alert idea ("tell us when Aura renewals stop") into saved Mixpanel reports that a
person can put an alert on in one click each, with thresholds backed by data and every
report proven to run.

## Why the split: reports by script, alerts by hand

The Mixpanel service account (`MIXPANEL_SERVICE_ACCOUNT_*`, role **consumer** on project
963821) can save reports and boards but has no alert permission, and there is no public
alerts API. So this skill does everything up to the alert, and a person adds the alert.
Its alert listing also returns only its own alerts (none), so existing alerts are found
from the Mixpanel app's posts in Slack (Step 1), not from the API.

The Mixpanel MCP cannot save reports, so this skill leads with the service-account API
(best-practices principle 11, API-first by necessity).

## Inputs

- What to alert on: events, filters, and whether it is a **stop** ("no X for N hours") or a
  **spike** ("more than N a day").
- Where the alerts post, and which ones are critical.
- The Linear card for the work (find or file it first).

## Workflow

### Step 1 — Check what exists

1. **Existing alerts.** Read the last 60 days of #alerts-mixpanel (`C05BW32AY9E`, public)
   through [`shared:read-slack`](../../../shared/skills/read-slack/SKILL.md) and count posts
   by the Mixpanel app (app id `AKMP1USA3`) per alert name. The Mixpanel app posts there
   without being a member. An alert that already covers the rule is reused, not duplicated.
   Ask whoever owns Mixpanel to confirm in the UI as well.
2. **Slack prerequisites.** For each private target channel, confirm the **Mixpanel** app
   (`AKMP1USA3`) is a member, through `shared:read-slack` or the channel's member list in
   Slack. "Mixpanel Agent" is a different app and does not post alerts.
3. **Events.** Confirm each event name and filter value exists
   (`/api/query/events/names`, then a segmentation by the filter property). A typo
   silently counts zero, and a zero rule on a zero series fires forever.

### Step 2 — Size the thresholds from counts

Counts only, never user ids or emails. Use [`references/sizing.md`](references/sizing.md):

- **Stop rules:** hourly counts over 14 days; the window N is the shortest one in which the
  series never summed to zero (and preferably not below ~10). Report the lowest N-hour total.
- **Spike rules:** daily counts over 28 days; threshold about 1.5–2x the 28-day max.
- **Critical vs normal:** the long stop windows and broad failure bursts are critical;
  early-warning windows and daily spikes are normal.

### Step 3 — Write the spec and show the plan

Write a spec JSON (shape: [`assets/spec-example.json`](assets/spec-example.json), the
PROD-8892 set). Each alert has `key`, `name`, `event`, optional `filters`, `unit`
(`hour`/`day`), optional `rolling` (periods), `condition`, `check`, `channels`,
`severity`. A filter value that is a JSON boolean (`"isInitialPeriod": true`) becomes an
is-true filter; any other value is an equals filter. A second example,
[`assets/spec-activation-example.json`](assets/spec-activation-example.json), holds the
new-subscription stop rules (Aurie paid starts, Aura trial starts, per store). Then:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-mixpanel-alert/scripts/mixpanel_reports.py plan --spec spec.json
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-mixpanel-alert/scripts/mixpanel_reports.py checklist --spec spec.json
```

The spec is refused if a filter names a person (email, user id, distinct id) or a rolling
rule uses "below 1" (see the rolling rule below).

Show the plan, thresholds, channels and checklist, and get a go. **Saving reports and
creating a board are separate writes**: a go for reports does not cover a board. Every
saved report must sit on a board, so ask for both.

### Step 4 — Build and verify

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-mixpanel-alert/scripts/mixpanel_reports.py apply --spec spec.json [--create-board]
```

`apply` creates the board only with `--create-board`, creates or updates each report by
name (re-running updates, never duplicates; nothing is deleted), re-attaches any report
that came back off the board, and runs each through the Insights query API. Exit 1 if any
report failed validation or is off the board. `verify` re-runs only the checks.

### Step 5 — Hand over the checklist

Send the checklist with each report's link (`apply`/`verify` print `url`). For each line
the person opens the report, clicks the bell / **Create Alert**, sets the condition and
check frequency, and picks the Slack channels. Alert names are the report names: never put
an id or email in them.

The board's tile layout cannot be set by the service account (`INVALID_LAYOUT`), so the
board may show no tiles; the report links always work.

### Step 6 — After the alerts are live

Check each channel for the Mixpanel app's first posts (counts only), report the list to
the card, and log the change in the Security Change Log the same day if the alert is a
monitoring threshold. Put a retune date (about 2 weeks) on the card.

## The rolling rule

Mixpanel's **Rolling** is a rolling **average**, set as `displayOptions.analysis =
"rolling"` + `displayOptions.rollingWindowSize = N` (`measurement.rolling` must stay null;
any key there fails validation). "No events in N hours" is therefore the average at 0,
and the alert condition is **below 0.01**. One event in the window averages 1/N (0.042 at
N = 24), so the threshold must sit under 1/N; "below 0.01" would fire on a single event in a
24-hour window, and "below 1" whenever there are fewer than N events. The spec check refuses
any rolling "below" threshold of 1/N or more. The last point is the current, partial hour.

## Output

- The board link, and one row per report: id, link, created/updated, on board, query OK
  with min/max/last.
- The one-line-per-alert checklist.

## Edge cases

- **"Dashboard doesn't exist"** on save → no `dashboard_id`; a board is required.
- **Report saved but `dashboard_id` null** → a create's reply can name the board while the
  stored report has none. `apply` always PATCHes `dashboard_id` after a create and trusts only a
  fresh read; a re-run adopts a same-named report with no board instead of creating a copy.
- **Query error "Extra inputs are not permitted at … measurement.rolling…"** → the rolling
  window was put in `measurement`; it belongs in `displayOptions`.
- **Thin series** (under ~1 event per window) → no stop rule; say why, or move it to a
  longer window or a daily rule.
- **No test-user cohort** → Aura has no canonical one; backend billing events barely carry
  test accounts. Say so rather than filtering on ids.

## References

- [`references/sizing.md`](references/sizing.md) — the counting recipes for Step 2.
- [`assets/spec-example.json`](assets/spec-example.json) — the PROD-8892 spec (13 alerts).
- AI Knowledge `Tools/Mixpanel` — project ids, service account, API quirks.
