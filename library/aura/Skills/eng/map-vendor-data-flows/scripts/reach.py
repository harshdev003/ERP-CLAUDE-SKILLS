#!/usr/bin/env python3
"""Unique users per event and per app (mp_lib), from Mixpanel's own analytics. Counts only.

    python3 reach.py --out DIR [--days 90] [--since 2015-01-01] [--windows 90d,all] [--refresh]

Reads DIR/scan.json for the event names, writes DIR/reach.json. Each window is one JQL query
that groups by user inside Mixpanel and returns only counts per (event, mp_lib), plus one query
for the users per mp_lib (the denominator). No user id, distinct_id or property value leaves
Mixpanel. A window already in reach.json is kept unless --refresh. A window whose query fails is
written as unmeasured with the reason, never as zero. A cached window is reused only when it
measured every event name the current scan found; otherwise it is measured again.

Auth: MIXPANEL_SERVICE_ACCOUNT_USERNAME / _SECRET; project MIXPANEL_SERVICE_ACCOUNT_PROJECT_ID (default 963821).
"""
import argparse
import base64
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import guard  # noqa: E402

JQL_URL = "https://mixpanel.com/api/2.0/jql"

EVENTS_JQL = """function main() {
  return Events({from_date: params.from, to_date: params.to, event_selectors: params.selectors})
    .groupByUser([function (e) { return e.name; }, function (e) { return e.properties.mp_lib || "(none)"; }],
                 mixpanel.reducer.null())
    .groupBy([function (r) { return r.key[1]; }, function (r) { return r.key[2]; }], mixpanel.reducer.count());
}"""

TOTALS_JQL = """function main() {
  return Events({from_date: params.from, to_date: params.to})
    .groupByUser([function (e) { return e.properties.mp_lib || "(none)"; }], mixpanel.reducer.null())
    .groupBy([function (r) { return r.key[1]; }], mixpanel.reducer.count());
}"""


class ReachError(Exception):
    pass


def project_today():
    """Today on the Mixpanel project's calendar (Pacific for 963821), not the host's: Mixpanel
    validates from_date / to_date against the project day, so a UTC host is a day ahead every
    US evening."""
    from zoneinfo import ZoneInfo
    tz = os.environ.get("MIXPANEL_PROJECT_TIMEZONE", "America/Los_Angeles")
    return datetime.datetime.now(ZoneInfo(tz)).date()


def event_names(scan):
    names = set()
    for r in scan["rows"]:
        if r["vendor"] != "mixpanel" or r["kind"] != "event" or not r["event"] or r["event"].startswith("<"):
            continue
        names.update(n.strip() for n in r["event"].split(" | "))
    return sorted(names)


def run_jql(script, params, post=None):
    """POST a JQL script; returns rows. `post` replaces the HTTP call in tests."""
    user = os.environ.get("MIXPANEL_SERVICE_ACCOUNT_USERNAME")
    secret = os.environ.get("MIXPANEL_SERVICE_ACCOUNT_SECRET")
    project = os.environ.get("MIXPANEL_SERVICE_ACCOUNT_PROJECT_ID") or os.environ.get("MIXPANEL_PROJECT_ID") or "963821"
    if post is None and not (user and secret):
        raise ReachError("MIXPANEL_SERVICE_ACCOUNT_USERNAME / _SECRET are unset")
    body = urllib.parse.urlencode({"script": script, "params": json.dumps(params), "project_id": project}).encode()
    if post is not None:
        return post(script, params)
    auth = base64.b64encode(f"{user}:{secret}".encode()).decode()
    for attempt in range(6):
        req = urllib.request.Request(JQL_URL, data=body, headers={"Authorization": f"Basic {auth}"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and attempt < 5:
                time.sleep(int(e.headers.get("Retry-After") or 0) or 15 * (attempt + 1))
                continue
            detail = guard.mask(e.read().decode("utf-8", "replace"))[:200]
            raise ReachError(f"JQL HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise ReachError(f"JQL unreachable: {e.reason}") from e
    raise ReachError("JQL retries exhausted")


def measure(names, start, end, post=None):
    """{"events": {event: {lib: users}}, "totals": {lib: users}} for one window."""
    params = {"from": start, "to": end}
    totals = {}
    for row in run_jql(TOTALS_JQL, params, post):
        lib, n = row["key"][0], row["value"]
        totals[guard.mask(str(lib))] = int(n)
    events = {}
    for i in range(0, len(names), 500):
        chunk = names[i:i + 500]
        sel = [{"event": n} for n in chunk]
        for row in run_jql(EVENTS_JQL, {**params, "selectors": sel}, post):
            ev, lib = guard.mask(str(row["key"][0])), guard.mask(str(row["key"][1]))
            events.setdefault(ev, {})[lib] = int(row["value"])
    return {"from": start, "to": end, "names": [guard.mask(n) for n in names], "events": events, "totals": totals}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--since", default="2015-01-01", help="start of the all-time window")
    ap.add_argument("--windows", default="90d,all")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    with open(os.path.join(a.out, "scan.json"), encoding="utf-8") as f:
        scan = json.load(f)
    path = os.path.join(a.out, "reach.json")
    reach = {}
    if os.path.exists(path) and not a.refresh:
        with open(path, encoding="utf-8") as f:
            reach = json.load(f)
    names = event_names(scan)
    today = project_today()
    end = (today - datetime.timedelta(days=1)).isoformat()
    starts = {"90d": (today - datetime.timedelta(days=a.days)).isoformat(), "all": a.since}
    for w in a.windows.split(","):
        if w in reach and "events" in reach[w] and set(names) <= set(reach[w].get("names", [])):
            continue  # a cached window is reused only when it measured every event this scan found
        try:
            reach[w] = measure(names, starts[w], end)
        except ReachError as e:  # a failed window is unmeasured, never zero
            reach[w] = {"from": starts[w], "to": end, "unmeasured": str(e)}
        guard.write_json(path, reach)
    print(json.dumps({w: ({"events": len(v["events"]), "libs": v["totals"]} if "events" in v else v["unmeasured"])
                      for w, v in reach.items()}))


if __name__ == "__main__":
    main()
