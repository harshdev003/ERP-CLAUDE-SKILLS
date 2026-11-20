#!/usr/bin/env python3
"""Cloud Functions + Cloud Run warnings and errors on firebase-auratech16, against a baseline day.

  check_functions.py baseline [--date YYYY-MM-DD]   build the per-hour baseline (default: 7 days ago)
  check_functions.py window [--minutes 10]          findings for the last N minutes

Signals, grouped by (source, function, severity, message template):
  - app logs (resource.type=cloud_function), severity >= WARNING
  - Cloud Run function revisions: severity >= ERROR and HTTP 5xx
A template is flagged NEW (absent from the baseline day) or HIGH (> 3x its baseline rate + 3).
  alert  authUserClaim timeouts >= 3 in the window (users can't sign in); a NEW/HIGH ERROR
         template with >= 5 entries; Cloud Run 5xx >= 200 in the window
  note   every other flagged template
Messages are templated (emails, IPs, long ids and digits replaced) before anything is printed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import common
import gcloud_sa

FILTER = ('((resource.type="cloud_function" AND severity>=WARNING) OR '
          '(resource.type="cloud_run_revision" AND resource.labels.service_name!="" AND '
          '(severity>=ERROR OR httpRequest.status>=500)))')
TIMEOUT = re.compile(r"time(d)? ?out|deadline exceeded|timeout", re.I)


def template(m) -> str:
    m = re.sub(r"[\w.+-]+@[\w.-]+", "<email>", str(m))
    m = re.sub(r"\b\d{1,3}(\.\d{1,3}){3}\b", "<ip>", m)
    m = re.sub(r"[A-Za-z0-9_:-]{20,}", "<id>", m)
    m = re.sub(r"\d+", "N", m)
    return m.splitlines()[0][:70] if m else "?"


def read(start: str, end: str, limit: int = 10000) -> Counter:
    q = f'{FILTER} AND timestamp>="{start}" AND timestamp<"{end}"'
    out = gcloud_sa.run(["logging", "read", q, f"--project={common.PROJECT}",
                         f"--limit={limit}", "--format=json"], capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    if out.returncode:
        raise RuntimeError(f"logging read failed as {gcloud_sa.credential()}: {out.stderr.strip()[:160]}")
    entries = json.loads(out.stdout or "[]")
    if len(entries) >= limit:
        raise RuntimeError(f"logging read hit the {limit}-entry cap for {start}..{end}: counts would be incomplete")
    c = Counter()
    for e in entries:
        r = e.get("resource", {}).get("labels", {})
        if e["resource"]["type"] == "cloud_function":
            src, fn = e.get("logName", "").split("/logs/")[-1], r.get("function_name", "?")
            msg = (e.get("jsonPayload") or {}).get("message") or e.get("textPayload") or "?"
        else:
            src, fn = "run", r.get("service_name", "?")
            st = (e.get("httpRequest") or {}).get("status")
            msg = f"HTTP {st}" if st else ((e.get("jsonPayload") or {}).get("message") or e.get("textPayload") or "?")
            if st:
                c[(src, fn, e.get("severity", "?"), msg)] += 1
                continue
        c[(src, fn, e.get("severity", "?"), template(msg))] += 1
    return c


def key(k) -> str:
    return "|".join(k)


def baseline(date: str) -> None:
    day, hourly = Counter(), Counter()
    for h in range(24):
        for k, v in read(f"{date}T{h:02d}:00:00Z", f"{date}T{h:02d}:59:59Z", limit=20000).items():
            day[key(k)] += v
            hourly[f"{key(k)}@{h:02d}"] += v
    out = {k: v / 144 for k, v in day.items()}       # per 10-min window, whole day
    out.update(hourly)                               # whole hour: daily jobs burst inside it
    out["_date"] = date
    (common.state_dir() / "functions-baseline.json").write_text(json.dumps(out), encoding="utf-8")
    print(f"baseline {date}: {len(day)} templates, {sum(day.values())} entries")


def window(minutes: int) -> None:
    path = common.state_dir() / "functions-baseline.json"
    if not path.exists():
        common.emit("functions", "fail", "functions-no-baseline",
                    "no Functions baseline yet: run check_functions.py baseline first")
        return
    base = json.loads(path.read_text(encoding="utf-8"))
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(minutes=minutes)
    try:
        c = read(start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ"))
    except RuntimeError as e:
        common.emit("functions", "fail", "functions-read", str(e))
        return
    scale = minutes / 10
    span = f"{start:%H:%M}-{end:%H:%M}Z"
    timeouts = sum(n for k, n in c.items() if k[1] == "authUserClaim" and TIMEOUT.search(k[3]))
    if timeouts:
        common.emit("functions", "alert" if timeouts >= 3 else "note", "functions-claim-timeouts",
                    f"authUserClaim: {timeouts} timeout(s) {span} (sign-ins fail while it times out)")
    run5xx = Counter()
    for k, n in c.most_common():
        hours = {f"{start:%H}", f"{end:%H}"}
        hourly = [base[f"{key(k)}@{h}"] / scale for h in hours if f"{key(k)}@{h}" in base]
        b = max(hourly) if hourly else base.get(key(k))
        flag = "NEW" if b is None else ("HIGH" if n > 3 * b * scale + 3 else "")
        if k[0] == "run" and k[3].startswith("HTTP 5"):
            run5xx[k[1]] += n
        if not flag or (k[1] == "authUserClaim" and TIMEOUT.search(k[3])):
            continue
        b_txt = "none" if b is None else f"{b * scale:.1f}"
        text = f'{n} {k[0]}/{k[1]} {k[2]} "{k[3]}" {span} (baseline {b_txt}, {flag})'
        level = "alert" if k[2] in ("ERROR", "CRITICAL", "ALERT", "EMERGENCY") and n >= 5 and k[0] != "run" else "note"
        common.emit("functions", level, f"functions-{k[1]}-{k[2]}-{k[3]}", text)
    for svc, n in run5xx.items():
        if n >= 200:
            common.emit("functions", "alert", f"functions-run5xx-{svc}", f"Cloud Run {svc}: {n} HTTP 5xx {span}")
    common.emit("functions", "note", "functions-total", f"{sum(c.values())} warning/error entries {span}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["baseline", "window"])
    ap.add_argument("--date", default=(datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d"))
    ap.add_argument("--minutes", type=int, default=10)
    a = ap.parse_args()
    baseline(a.date) if a.cmd == "baseline" else window(a.minutes)


if __name__ == "__main__":
    main()
