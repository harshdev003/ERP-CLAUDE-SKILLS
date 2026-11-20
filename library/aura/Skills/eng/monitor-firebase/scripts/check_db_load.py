#!/usr/bin/env python3
"""RTDB database load (Cloud Monitoring io/database_load) over the last N minutes.

  check_db_load.py [--minutes 10] [--threshold 0.70]

Prints findings (common.py). alert when the max load reaches the threshold: at 100% the
database queues every request and Functions time out (2026-10-23 15:51Z).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

import common
import gcloud_sa


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=10)
    ap.add_argument("--threshold", type=float, default=0.70)
    a = ap.parse_args()
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(minutes=a.minutes)
    try:
        d = common.curl_json(
            f"https://monitoring.googleapis.com/v3/projects/{common.PROJECT}/timeSeries",
            headers={"Authorization": f"Bearer {common.access_token()}"},
            params={"filter": 'metric.type="firebasedatabase.googleapis.com/io/database_load"',
                    "interval.startTime": start.isoformat().replace("+00:00", "Z"),
                    "interval.endTime": end.isoformat().replace("+00:00", "Z"),
                    "aggregation.alignmentPeriod": "60s",
                    "aggregation.perSeriesAligner": "ALIGN_MAX",
                    "aggregation.crossSeriesReducer": "REDUCE_SUM"},
            user_agent=gcloud_sa.user_agent())
    except RuntimeError as e:
        common.emit("db-load", "fail", "db-load-read", f"database_load read failed: {e}")
        return
    pts = [(p["interval"]["endTime"], p["value"].get("doubleValue", 0.0))
           for s in d.get("timeSeries", []) for p in s.get("points", [])]
    if not pts:
        common.emit("db-load", "fail", "db-load-empty", f"no database_load points in the last {a.minutes} min")
        return
    t, peak = max(pts, key=lambda x: x[1])
    text = f"RTDB load max {peak:.0%} at {t[11:16]}Z (last {a.minutes} min)"
    if peak >= a.threshold:
        common.emit("db-load", "alert", "db-load-high", text + f", at or above {a.threshold:.0%}")
    else:
        common.emit("db-load", "note", "db-load", text)


if __name__ == "__main__":
    main()
