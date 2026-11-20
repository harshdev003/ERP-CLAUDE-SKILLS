#!/usr/bin/env python3
"""RTDB shallow REST gets (Cloud Monitoring network/api_hits_count, rest_shallow_get) over the last N minutes.

  check_shallow_gets.py [--minutes 10]

Prints findings (common.py): a note with the count, every pass. The profiler's raw ops don't carry
?shallow=true: on 2026-11-09 the metric counted 103 shallow gets in the 13Z hour while the profile
of the same minutes counted 0. So the count comes from the metric the GCP alert fires on, and the
pass's `allowed REST reads` note attributes it (op, path shape, client).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

import common
import gcloud_sa

METRIC = "firebasedatabase.googleapis.com/network/api_hits_count"
SHALLOW = "rest_shallow_get"


def count(series: list[dict]) -> tuple[int, str] | None:
    """(shallow gets, latest point time) across every operation type; None when nothing came back.
    A window with no shallow gets has no rest_shallow_get series, so the other types prove the read worked."""
    n, last = 0, ""
    for s in series:
        for p in s.get("points", []):
            last = max(last, p["interval"]["endTime"])
            if s.get("metric", {}).get("labels", {}).get("operation_type") == SHALLOW:
                n += int(p["value"].get("int64Value", 0))
    return (n, last) if last else None


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=10)
    a = ap.parse_args()
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(minutes=a.minutes)
    try:
        d = common.curl_json(
            f"https://monitoring.googleapis.com/v3/projects/{common.PROJECT}/timeSeries",
            headers={"Authorization": f"Bearer {common.access_token()}"},
            params={"filter": f'metric.type="{METRIC}"',
                    "interval.startTime": start.isoformat().replace("+00:00", "Z"),
                    "interval.endTime": end.isoformat().replace("+00:00", "Z"),
                    "aggregation.alignmentPeriod": "60s",
                    "aggregation.perSeriesAligner": "ALIGN_SUM",
                    "aggregation.crossSeriesReducer": "REDUCE_SUM",
                    "aggregation.groupByFields": "metric.label.operation_type"},
            user_agent=gcloud_sa.user_agent())
    except RuntimeError as e:
        common.emit("shallow-gets", "fail", "shallow-gets-read", f"api_hits_count read failed: {e}")
        return
    got = count(d.get("timeSeries", []))
    if got is None:
        common.emit("shallow-gets", "fail", "shallow-gets-empty", f"no api_hits_count points in the last {a.minutes} min")
        return
    n, last = got
    common.emit("shallow-gets", "note", "shallow-gets",
                f"RTDB shallow REST gets ({SHALLOW}): {n} in the last {a.minutes} min (metric to {last[11:16]}Z)"
                + ("; see the allowed REST reads note for op, path and client" if n else ""))


if __name__ == "__main__":
    main()
