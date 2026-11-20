# gcloud command reference

All commands target `--project=firebase-auratech16` and run through the skill's `scripts/gcloud_sa.py`
(the `GCP_KEY` service account; your gcloud login only when `GCP_KEY` is unset).

## 1. Verify the signal (always first)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py logging read '<filter>' \
  --project=firebase-auratech16 --limit=2 --freshness=1d --format=json
```

Inspect the returned entry for the payload field and resource labels:

```bash
# quick shape check
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py logging read '<filter>' --project=firebase-auratech16 --limit=1 --freshness=1d --format=json \
  | python3 -c "import sys,json; e=json.load(sys.stdin)[0]; \
print('textPayload:', repr(e.get('textPayload'))[:160]); \
jp=e.get('jsonPayload') or {}; print('jsonPayload.message:', repr(jp.get('message'))[:160]); \
print('labels:', e.get('resource',{}).get('labels'))"
```

## 2. Filter recipes

Base, by log source — take it from the Step 1 entry, never assume:

**GKE container** (services in `aura-prod-cluster-a`):
```
resource.type="k8s_container"
resource.labels.cluster_name="aura-prod-cluster-a"
resource.labels.namespace_name="aura-ai"
```

**Cloud Function** (Firebase Functions, e.g. FirebaseAuth triggers):
```
logName="projects/firebase-auratech16/logs/<log name from the entry>"
resource.type="cloud_function"
resource.labels.function_name="<function>"
```
The function name is the disambiguator; there is no `container_name` or
`serviceContext.service`. The policy condition filter must then use
`resource.type="cloud_function"` too — the templates ship with `k8s_container`.

**HTTP(S) load balancer** (`http_load_balancer` logs): the metric filter uses
`resource.type="http_load_balancer"`, but the metric's time series are written on
`l7_lb_rule`, so the policy condition filter must use `resource.type="l7_lb_rule"`. A
condition on `http_load_balancer` never matches and the alert never fires.

Then the disambiguator + message match, per shape (GKE):

**textPayload (print/stdout services):**
```
resource.labels.container_name="<container>"
textPayload:"<stable substring>"
```
Combine multiple substrings with `AND` when the line has variable content:
```
textPayload:"Published" AND textPayload:"keys to /mnt/nfs/activation-model/"
```

**jsonPayload (structured loggers):**
```
jsonPayload.serviceContext.service="<service>"
jsonPayload.message="<exact message>"
```

## 3. Create the metric

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py logging metrics create <NAME> \
  --project=firebase-auratech16 \
  --config-from-file=/tmp/<name>-metric.json
```

Check for a name clash first:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py logging metrics list --project=firebase-auratech16 --format="value(name)" | grep -i <NAME>
```

## 4. Create the alert policy

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py monitoring policies create \
  --project=firebase-auratech16 \
  --policy-from-file=/tmp/<name>-policy.json
```

## 5. Verify

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py logging metrics describe <NAME> --project=firebase-auratech16
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py monitoring policies list --project=firebase-auratech16 \
  --filter='displayName ~ "<name>"' \
  --format="table(displayName, enabled, conditions[0].conditionAbsent.duration, notificationChannels.len())"
```

## 6. (Optional) trigger a CronJob run to populate the metric

```bash
TS=$(date +%m%d%H%M)
kubectl -n aura-ai create job <cronjob>-manual-$TS --from=cronjob/<cronjob>
```

## Notification channels

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py beta monitoring channels list --project=firebase-auratech16 \
  --format="table(name, displayName, type)"
```

Current Aura AI/prod channels (default set):
- `projects/firebase-auratech16/notificationChannels/11467163257391956365`
- `projects/firebase-auratech16/notificationChannels/9276031353074528112`
- `projects/firebase-auratech16/notificationChannels/3690673794603392095`
