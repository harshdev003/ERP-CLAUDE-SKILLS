#!/usr/bin/env python3
"""
Export candidate Cloud Armor inputs from the open Security Incident Log group rows.

Read-only: prints IPs, /24 CIDRs and user agents with counts and last seen, as JSON or
CSV. It never creates or changes a Cloud Armor policy, and never writes to Notion.
Our own traffic (attack_rules.OWN_TRAFFIC user agents) and any --exclude-ip are left out.

Token: NOTION_TOKEN.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import attack_rules as rules  # noqa: E402
import incident_log as log  # noqa: E402


def own_ua(ua):
    return any(ua_re.search(ua) for _name, ua_re, _host, _route in rules.OWN_TRAFFIC)


def collect(rows, min_severity="Info", group_key=None, min_requests=0, exclude_ips=(), call=None):
    """IPs, /24s and UAs across the rows that pass the filters. A count the row never had
    (older comma-separated IP lists) stays null and passes only --min-requests 0."""
    ips, uas, kept = {}, {}, []
    for row in rows:
        pr = row["properties"]
        gk = log.plain(pr.get("Group key"))
        sev = ((pr.get("Severity") or {}).get("select") or {}).get("name")
        if rules.rank(sev) < rules.rank(min_severity) or (group_key and group_key not in gk):
            continue
        kept.append(gk)
        _child, r_ips, r_uas = log.row_ban_data(row, call)
        for ip, v in r_ips.items():
            if ip in exclude_ips:
                continue
            cur = ips.setdefault(ip, {"requests": None, "last_seen": None, "groups": []})
            if v["n"] is not None:
                cur["requests"] = (cur["requests"] or 0) + v["n"]
            if v.get("last") and (not cur["last_seen"] or v["last"] > cur["last_seen"]):
                cur["last_seen"] = v["last"]
            cur["groups"].append(gk)
        for ua, n in r_uas.items():
            if own_ua(ua):
                continue
            cur = uas.setdefault(ua, {"requests": None, "groups": []})
            if n is not None:
                cur["requests"] = (cur["requests"] or 0) + n
            cur["groups"].append(gk)
    if min_requests:
        ips = {k: v for k, v in ips.items() if (v["requests"] or 0) >= min_requests}
        uas = {k: v for k, v in uas.items() if (v["requests"] or 0) >= min_requests}
    nets = {}
    for ip, v in ips.items():
        n = nets.setdefault(log.cidr(log.net24(ip)), {"ips": [], "requests": 0, "last_seen": None})
        n["ips"].append(ip)
        n["requests"] += v["requests"] or 0
        if v["last_seen"] and (not n["last_seen"] or v["last_seen"] > n["last_seen"]):
            n["last_seen"] = v["last_seen"]
    return {
        "groups": kept,
        "ips": [{"ip": k, **v} for k, v in sorted(ips.items(), key=lambda kv: -(kv[1]["requests"] or 0))],
        "cidrs": [{"cidr": k, **v} for k, v in sorted(nets.items(), key=lambda kv: (-len(kv[1]["ips"]),
                                                                                    -kv[1]["requests"]))],
        "user_agents": [{"user_agent": k, **v}
                        for k, v in sorted(uas.items(), key=lambda kv: -(kv[1]["requests"] or 0))],
    }


def to_csv(out):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["type", "value", "requests", "last_seen", "ips", "groups"])
    for x in out["ips"]:
        w.writerow(["ip", x["ip"], x["requests"], x["last_seen"], "", ";".join(sorted(set(x["groups"])))])
    for x in out["cidrs"]:
        w.writerow(["cidr", x["cidr"], x["requests"], x["last_seen"], len(x["ips"]), ""])
    for x in out["user_agents"]:
        w.writerow(["user_agent", x["user_agent"], x["requests"], "", "", ";".join(sorted(set(x["groups"])))])
    return buf.getvalue()


def main(argv=None, call=None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--min-severity", choices=rules.SEVERITIES, default="Info")
    p.add_argument("--group-key", help="only rows whose Group key contains this, e.g. 'Web|' or '|refused'")
    p.add_argument("--min-requests", type=int, default=0)
    p.add_argument("--exclude-ip", action="append", default=[], help="leave this IP out (repeatable)")
    p.add_argument("--format", choices=["json", "csv"], default="json")
    args = p.parse_args(argv)
    call = call or log.notion
    out = collect(log.open_rows(call), args.min_severity, args.group_key, args.min_requests,
                  set(args.exclude_ip), call)
    sys.stdout.write(to_csv(out) if args.format == "csv" else json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
