# Testing an alert end to end, and renaming a metric

Both procedures write to prod monitoring or logging. Show the user the exact commands and
get a go-ahead before running them.

## Testing an alert with a synthetic entry

A new metric has no data until the real signal fires, which for a rare warning can be days.
To prove the whole chain (filter → metric → policy → notification channel) works now, write
one synthetic entry that matches the metric filter exactly.

**Rules for the test entry:**

- Same `logName`, `resource.type`, resource labels and `severity` as the real entries — the
  metric filter matches on all of them.
- Same `jsonPayload.message` (or `textPayload` substring) as the filter, and a value for
  every field a label extractor reads (e.g. `status`), so the point lands in the series a
  condition filters on (`metric.label.status="ambiguous"`).
- `"test": true` in the payload, and **no real user data** — no UID, email or name. Use
  obviously synthetic values.
- Enough entries to cross the threshold of the condition you are testing (one is enough for
  a `> 0` condition; a `> 5 in a day` condition needs six).

**Write it** — `logging write` sets the monitored resource, so the entry looks like the
function's own log (Cloud Functions example, matching `UserClaimUnresolved`):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py logging write firebase-auth \
  '{"message":"User claim unresolved","status":"ambiguous","provider":"password","test":true,"note":"synthetic alert test"}' \
  --payload-type=json --severity=WARNING \
  --monitored-resource-type=cloud_function \
  --monitored-resource-labels=function_name=authUserClaim,region=us-central1,project_id=firebase-auratech16 \
  --project=firebase-auratech16
```

For a GKE source use `--monitored-resource-type=k8s_container` with `cluster_name`,
`namespace_name`, `container_name`, `pod_name` and `location` labels, and the container's
own log name (`stdout` / `stderr`). The `entries:write` REST endpoint does the same thing
when a field `logging write` cannot set is needed.

**Confirm each hop, in order:**

1. The entry is in Logging: `logging read '<metric filter> AND jsonPayload.test=true' --freshness=10m`.
2. The metric counted it: Metrics Explorer on `logging.googleapis.com/user/<Name>`, or wait
   for the incident.
3. The policy opened an incident (Monitoring → Incidents), and the notification landed.
   Confirm it by **invoking [`shared:read-slack`](../../../../shared/skills/read-slack/SKILL.md)**
   (it owns the connection — MCP-first, `SLACK_BOT_TOKEN` script fallback) on the alert
   channel, bounded to the test:
   ```
   shared:read-slack history --channel <alert channel id> --oldest <epoch seconds when the test entry was written>
   ```
   Read the channel's history rather than searching: Slack search indexes new messages
   minutes late, so an empty search result does not mean nothing was posted.

**Timing — wait before concluding it failed:**

- **Create the policy, then wait a few minutes before writing the test entry.** A policy
  created seconds before the entry can miss it — the first evaluation window starts after
  the policy exists.
- **A new metric takes up to ~10 minutes to register.** Until then, creating or updating a
  policy that references it fails with `404 Cannot find metric(s)`. Retry after a few
  minutes; this is not a filter error.
- Alignment period matters: a condition with `alignmentPeriod 86400s` evaluates on a
  1-day bucket, but the incident still opens within minutes of the threshold being crossed.

Close the test incident afterwards (or let `autoClose` do it) and tell the user it was a
test, so nobody acts on it.

## Renaming a metric

Log-based metric names cannot be renamed (`logging metrics update` changes the filter,
description and labels, not the name). To correct a name — e.g. a snake_case
`user_claim_unresolved` to the PascalCase `UserClaimUnresolved`:

1. **Create the new metric** with the identical filter, `labelExtractors` and
   `metricDescriptor.labels` as the old one:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py logging metrics describe <OldName> \
     --project=firebase-auratech16 --format=json > /tmp/<old>-metric.json
   # keep description, filter, labelExtractors, metricDescriptor.{labels,metricKind,unit,valueType}; drop name/createTime/updateTime/resourceName
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py logging metrics create <NewName> \
     --project=firebase-auratech16 --config-from-file=/tmp/<new>-metric.json
   ```
2. **Wait for it to register** (up to ~10 minutes — see Timing above).
3. **Repoint every policy condition** that references `logging.googleapis.com/user/<OldName>`
   to `<NewName>`: export the policy (`monitoring policies describe <policy> --format=json`),
   replace the metric type in each `conditions[].condition*.filter`, and apply it with
   `monitoring policies update <policy> --policy-from-file=…`. Find every referencing policy
   first — `monitoring policies list --format=json` and search for the old metric type.
4. **Verify** no policy references the old name any more, then **delete the old metric**
   (`logging metrics delete <OldName>`).

The new metric is forward-only: it has no history from the old one. Expect a gap in the
chart, and expect an absence condition on it to need one real data point before it is
fully effective.
