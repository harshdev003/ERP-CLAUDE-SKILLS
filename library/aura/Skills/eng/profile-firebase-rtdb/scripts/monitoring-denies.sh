#!/bin/bash
# Usage: monitoring-denies.sh <project> <startISO> <endISO>
# Rule evaluations per 10 minutes from Cloud Monitoring (firebasedatabase.googleapis.com/rules/evaluation_count),
# grouped by result (ALLOW/DENY) and request_method (READ/WRITE). Counts only; the metric carries no path.
PROJECT=$1
TOKEN=$(python3 "$(dirname "$0")/gcloud_sa.py" auth print-access-token) || { echo "no gcloud token (see the line above for the credential)"; exit 1; }
UA=$(python3 "$(dirname "$0")/gcloud_sa.py" --user-agent)
curl -s -G -A "$UA" "https://monitoring.googleapis.com/v3/projects/${PROJECT}/timeSeries" \
  -H "Authorization: Bearer $TOKEN" \
  --data-urlencode 'filter=metric.type="firebasedatabase.googleapis.com/rules/evaluation_count"' \
  --data-urlencode "interval.startTime=$2" --data-urlencode "interval.endTime=$3" \
  --data-urlencode 'aggregation.alignmentPeriod=600s' \
  --data-urlencode 'aggregation.perSeriesAligner=ALIGN_SUM' \
  --data-urlencode 'aggregation.crossSeriesReducer=REDUCE_SUM' \
  --data-urlencode 'aggregation.groupByFields=metric.label.result' \
  --data-urlencode 'aggregation.groupByFields=metric.label.request_method' \
  | jq -r '.error.message // empty, (.timeSeries[]? | (.metric.labels.request_method + "/" + .metric.labels.result) as $k | .points[] | [.interval.endTime, $k, .value.int64Value] | @tsv)' | sort
