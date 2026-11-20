#!/usr/bin/env python3
"""Tracker for the job-hunt skill. Standard library only.

All data lives in the job-hunt data folder (default C:\\Users\\Harshit\\job-hunt,
override with JOB_HUNT_DIR). Nothing here is ever written inside a Git repo.

Commands:
  init                                   create the data folder and missing files
  key --company C --title T --location L print the dedupe key
  seen --company C --title T --location L [--job-id ID --portal P]
                                         exit 0 if already in applied.csv, 1 if new
  add --json '{"portal":..., ...}'       append one row (date and dedupe_key auto)
  today                                  applies today per portal vs the daily cap
  summary [--days N]                     status counts, caps, top-10 gaps
"""
import argparse
import collections
import csv
import datetime as dt
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("JOB_HUNT_DIR") or os.path.join(os.path.expanduser("~"), "job-hunt")
CSV_PATH = os.path.join(DATA, "applied.csv")
FIELDS = ["date", "portal", "job_id", "url", "company", "role", "location", "score",
          "resume_file", "status", "notes", "dedupe_key"]
STATUSES = {"applied", "failed", "manual", "needs-input", "skipped"}
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SUFFIXES = r"\b(private|pvt|limited|ltd|llp|inc|incorporated|corp|corporation|co|company|technologies|technology|tech|solutions|services|india|global)\b"


def now():
    return dt.datetime.now(IST)


def norm(text, company=False):
    t = (text or "").lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    if company:
        t = re.sub(SUFFIXES, " ", t)
    return re.sub(r"\s+", " ", t).strip()


def dedupe_key(company, title, location):
    loc = norm((location or "").split(",")[0])  # "Bengaluru, Karnataka, India" -> "bengaluru"
    loc = {"bangalore": "bengaluru", "gurgaon": "gurugram", "bombay": "mumbai"}.get(loc, loc)
    return "|".join([norm(company, company=True), norm(title), loc])


def load_config():
    path = os.path.join(DATA, "config.json")
    src = path if os.path.exists(path) else os.path.join(HERE, "..", "config.example.json")
    with open(src, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["lookback_days"] = min(int(cfg.get("lookback_days", 3)), 5)  # hard max 5
    return cfg


def rows():
    if not os.path.exists(CSV_PATH):
        return []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def cmd_init(_):
    os.makedirs(os.path.join(DATA, "resumes"), exist_ok=True)
    created = []
    cfg = os.path.join(DATA, "config.json")
    if not os.path.exists(cfg):
        shutil.copy(os.path.join(HERE, "..", "config.example.json"), cfg)
        created.append("config.json")
    if not os.path.exists(CSV_PATH):
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(FIELDS)
        created.append("applied.csv")
    state = os.path.join(DATA, "run-state.md")
    if not os.path.exists(state):
        with open(state, "w", encoding="utf-8") as f:
            f.write("# Job hunt — run state\nUpdated: %s\nStatus: not started\n"
                    "Next step: /job-hunt <resume-path> to analyze the resume\n" % now().isoformat(timespec="minutes"))
        created.append("run-state.md")
    log = os.path.join(DATA, "refresh-log.md")
    if not os.path.exists(log):
        with open(log, "w", encoding="utf-8") as f:
            f.write("# Naukri profile refresh log\n")
        created.append("refresh-log.md")
    print("data folder: %s" % DATA)
    print("created: %s" % (", ".join(created) or "nothing (all files exist)"))


def cmd_key(a):
    print(dedupe_key(a.company, a.title, a.location))


def cmd_seen(a):
    key = dedupe_key(a.company, a.title, a.location)
    for r in rows():
        if r.get("dedupe_key") == key or (a.job_id and r.get("job_id") == a.job_id
                                          and (not a.portal or r.get("portal") == a.portal)):
            print("seen: %s (%s, %s)" % (r.get("dedupe_key"), r.get("status"), r.get("date")))
            return 0
    print("new: %s" % key)
    return 1


def cmd_add(a):
    data = json.loads(a.json)
    status = data.get("status", "")
    if status not in STATUSES:
        sys.exit("status must be one of %s" % sorted(STATUSES))
    for need in ("portal", "company", "role"):
        if not data.get(need):
            sys.exit("missing field: %s" % need)
    data.setdefault("date", now().isoformat(timespec="minutes"))
    data["dedupe_key"] = dedupe_key(data["company"], data["role"], data.get("location", ""))
    if not os.path.exists(CSV_PATH):
        cmd_init(None)
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore").writerow(data)
    print("added: %s -> %s" % (data["dedupe_key"], status))


def applied_today():
    today = now().date().isoformat()
    c = collections.Counter(r["portal"] for r in rows()
                            if r.get("status") == "applied" and r.get("date", "").startswith(today))
    return c


def cmd_today(_):
    cfg = load_config()
    cap = int(cfg.get("max_applies_per_portal_per_day", 40))
    c = applied_today()
    for p in cfg.get("portals", []):
        n = c.get(p, 0)
        print("%-10s %3d / %d%s" % (p, n, cap, "  CAP REACHED" if n >= cap else ""))


def cmd_summary(a):
    cfg = load_config()
    cap = int(cfg.get("max_applies_per_portal_per_day", 40))
    all_rows = rows()
    since = (now() - dt.timedelta(days=a.days)).date().isoformat() if a.days else ""
    sel = [r for r in all_rows if r.get("date", "") >= since]
    print("period: %s" % ("last %d day(s)" % a.days if a.days else "all time"))
    print("jobs: %d" % len(sel))
    st = collections.Counter(r.get("status") for r in sel)
    for s in ["applied", "skipped", "manual", "needs-input", "failed"]:
        print("  %-12s %d" % (s, st.get(s, 0)))
    reasons = collections.Counter()
    for r in sel:
        if r.get("status") == "skipped":
            reasons[(r.get("notes") or "").split("gaps:")[0].strip(" ;,") or "(no reason)"] += 1
    if reasons:
        print("top skip reasons:")
        for k, v in reasons.most_common(5):
            print("  %3d  %s" % (v, k))
    print("today (cap %d per portal):" % cap)
    c = applied_today()
    for p in cfg.get("portals", []):
        print("  %-10s %d" % (p, c.get(p, 0)))
    gaps = collections.Counter()
    for r in sel:
        m = re.search(r"gaps:\s*(.+)", r.get("notes") or "", re.I)
        if m:
            for g in re.split(r"[;,]", m.group(1)):
                g = g.strip().lower()
                if g:
                    gaps[g] += 1
    print("top 10 missing keywords:")
    for k, v in gaps.most_common(10):
        print("  %3d  %s" % (v, k))
    if not gaps:
        print("  (none logged)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    for name, fn in (("key", cmd_key), ("seen", cmd_seen)):
        p = sub.add_parser(name)
        p.add_argument("--company", required=True)
        p.add_argument("--title", required=True)
        p.add_argument("--location", default="")
        if name == "seen":
            p.add_argument("--job-id", default="")
            p.add_argument("--portal", default="")
        p.set_defaults(fn=fn)
    p = sub.add_parser("add")
    p.add_argument("--json", required=True)
    p.set_defaults(fn=cmd_add)
    sub.add_parser("today").set_defaults(fn=cmd_today)
    p = sub.add_parser("summary")
    p.add_argument("--days", type=int, default=0)
    p.set_defaults(fn=cmd_summary)
    a = ap.parse_args()
    rc = a.fn(a)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
