# Sizing Mixpanel alert thresholds

Counts only. Never print or store user ids or emails. All calls use the service account
(`-u "$MIXPANEL_SERVICE_ACCOUNT_USERNAME:$MIXPANEL_SERVICE_ACCOUNT_SECRET"`) and
`project_id=$MIXPANEL_PROJECT_ID`. The Query API allows about 60 calls an hour, so batch.

**Dates are Pacific.** Mixpanel validates `from_date`/`to_date` on the project calendar
(US/Pacific for project 963821), and hourly buckets come back in Pacific time. Compute the
dates there, never from the host or UTC clock:

```bash
TODAY=$(TZ=America/Los_Angeles date +%F)
YESTERDAY=$(TZ=America/Los_Angeles date -v-1d +%F 2>/dev/null || TZ=America/Los_Angeles date -d yesterday +%F)
```

`<yesterday>` and `<N days ago>` below mean Pacific dates.

## Daily counts for many events in one call

```bash
curl -s -u "$MIXPANEL_SERVICE_ACCOUNT_USERNAME:$MIXPANEL_SERVICE_ACCOUNT_SECRET" \
  "https://mixpanel.com/api/query/events?project_id=$MIXPANEL_PROJECT_ID&type=general&unit=day&from_date=<28 days ago>&to_date=<yesterday>&event=<url-encoded JSON list of event names>"
```

Per event: mean, min and max per day. A spike threshold sits about 1.5–2x the 28-day max.
Skip the current day: it is partial.

## Hourly counts split by a property

```bash
curl -s -u "$MIXPANEL_SERVICE_ACCOUNT_USERNAME:$MIXPANEL_SERVICE_ACCOUNT_SECRET" --get \
  "https://mixpanel.com/api/query/segmentation" \
  --data-urlencode "project_id=$MIXPANEL_PROJECT_ID" --data-urlencode "event=<event>" \
  --data-urlencode "from_date=<14 days ago>" --data-urlencode "to_date=<yesterday>" \
  --data-urlencode "unit=hour" --data-urlencode "type=general" \
  --data-urlencode 'on=properties["provider"]'
```

For each split value, slide windows of 6, 8, 12 and 24 hours over the series and count
windows that summed to zero, plus the lowest window total. The stop rule's window is the
shortest with zero zero-windows; prefer one whose lowest total is about 10 or more.

Worked result (PROD-8892, Nov 6–19 2026): Aura `Subscription Renewed` stripe 2.6/h and
apple 2.1/h had no zero 6-hour window (lowest 12-hour totals 15 and 13), so 6 h is the
early rule and 12 h the critical one. Aurie `AI Subscription Renewed` stripe at 0.9/h had
three zero 6-hour windows and none at 8 hours, so 12 h early and 24 h critical. Google and
Aurie Apple renewals (about 0.1–0.2/h) were too thin for any stop rule.

## Events are state transitions

Backend subscription events (`Subscription Paid`, `Subscription Renewed`,
`Subscription Payment Pending`…) fire on the transition into a state. A failed renewal
shows up as `Payment Pending`, not as a missing `Paid`. See AI Knowledge
`Tools/Mixpanel` for the event vocabulary.
