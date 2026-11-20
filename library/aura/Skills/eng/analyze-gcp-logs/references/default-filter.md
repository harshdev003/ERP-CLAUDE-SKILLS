# Default noise-exclusion filter

This is the **baked-in default filter** for `analyze-gcp-logs`. It is the curated list of
known-noise signatures that should be stripped from a broad warning/error sweep so the
breakdown shows *signal*, not the recurring background chatter the team has already triaged
away.

`analyze.py` reads the **first fenced code block** in this file at runtime. Each non-blank,
non-comment line is a complete Cloud Logging filter fragment that is `AND`-ed onto the base
query (which already pins `resource.type`, cluster, namespace, severity, and the time window).
Every fragment is a **negation** (`-…`), so it *removes* matching entries.

## How the query is assembled

```
resource.type="k8s_container"
AND resource.labels.cluster_name="aura-prod-cluster-a"
AND resource.labels.namespace_name="aura-services"
AND severity>="WARNING"
AND timestamp>="<now - window>"
AND <each exclusion line below>
```

## Editing this filter

- **Add a new exclusion** when a signature is confirmed-noise and is drowning the breakdown.
  Append a `-jsonPayload.message="..."` (exact) or `-(jsonPayload.message=~"...")` (regex) line
  inside the fenced block.
- **Exact vs regex:** use `="..."` for a stable, complete message; use `=~"..."` (RE2 regex,
  `.` must be escaped as `\.`) when the message has a variable tail (URLs, IDs, timestamps).
- **Comments:** lines beginning with `#` (after trimming) are ignored by the parser — use them
  to group/annotate.
- **Override at runtime** without editing this file: `--filter-file <path>` points at a
  different file, and `--no-default-filter` runs a raw unfiltered sweep (useful to see what the
  filter is currently hiding).

> ⚠️ Keep every line a single, self-contained filter term. The parser does **not** join lines —
> a fragment split across two lines will break the query.

## Exclusions

```text
# --- coach-service: handled by its own dashboards/alerts, excluded wholesale ---
-(jsonPayload.serviceContext.service="@aura/coach-service")

# --- aura-ai predict-model timeouts (recurring infra noise, regex tail = URL/params) ---
-(jsonPayload.message=~"Exception while making a request to 'http://program-model\.aura-ai:3001/predict")
-(jsonPayload.message=~"Exception while making a request to 'http://neural-model-14\.aura-ai:3001/predict")
-(jsonPayload.message="Exception while making a request to 'http://user-preferences-model.aura-ai:3001/predict'")
-(jsonPayload.message="Exception while making a request to 'http://activated-new-model.aura-ai:3001/predict'")
-(jsonPayload.message="Exception while making a request to 'http://serie-model.aura-ai:3001/predict'")
-(jsonPayload.message="Exception while making a request to 'http://activation-model.aura-ai:3001/predict'")

# --- third-party API call noise ---
-(jsonPayload.message=~"Exception while making a request to 'https://onesignal\.com/api/v1/notifications")
-(jsonPayload.message="Exception while making a request to 'https://mixpanel.com/api/2.0/engage/'")

# --- OneSignal / notification delivery chatter ---
-(jsonPayload.message=~"No notification received by OneSignal")
-(jsonPayload.message=~"No notification queued")
-(jsonPayload.message=~"No response from OneSignal")
-jsonPayload.message="No aiMobileToken found for user"
-jsonPayload.message="Could not queue the notification: device unregistered"
-jsonPayload.message="Notification not sent, invalid status"
-jsonPayload.message="Notification not sent successfully"
-jsonPayload.message="User is not subscribed from all notifications"
-jsonPayload.message="Trial reminder notification not sent successfully"

# --- payment expected-states (not errors) ---
-(jsonPayload.message="Charge not created")
-(jsonPayload.message="User auth not found")
-(jsonPayload.message="Subscription not created")

# --- experiment bookkeeping ---
-jsonPayload.message="Mismatch for the experiment already set in postgres"

# --- RSS feed parse warning (regex tail = timestamp/offset) ---
-(jsonPayload.message=~"Error while getting RSS feed: Error: boolean attribute 'data-global-elements-nav-styles' is not allowed\.:((\d{2}):(\d{2})(?::(\d{2}(?:\.\d*)?))?(?:([+-](?:\d{2}):?(?:\d{2})?|Z)?))((?:\d[,.]?)*\d)")
```
