#!/usr/bin/env python3
"""Re-classify Security Incident Log lines the network check could not judge (ENG-1756).

  reclassify.py --rows <incident-rows.json> --out <reclassify.json> [--untagged-since 2026-11-03T02:30Z]

A pass whose Known Team Networks read failed logs allowed reads with their addresses, each line ending
"(unclassified: <reason>)" (ENG-1755). On a later pass where Notion answers, this finds those lines in the
Firebase RTDB rows (incident-log.js rows), looks up each address's network (Team Cymru whois, as
check_networks.py does) and matches it to Known Team Networks:

  teammate          a Confirmed or Likely row for the network, and no Not a teammate row
  not-a-teammate    a Not a teammate row for the network (also an ALERT finding)
  unknown           no row, or a row of any other status

--untagged-since also takes allowed whole-node lines (Group key Firebase RTDB|node-read|...) from that time
on that carry no tag: reads logged before the tag existed, such as 2026-11-03 from 02:33Z.

Writes {"ips": {ip: {verdict, network, who}}, "lines": [{page_id, line, ip}]} mode 600 for
plan-incident-log.js --reclassify, which replaces each tag with "(classified <date>: <verdict text>)".
Findings (common.py) carry counts only. Nothing changes when Known Team Networks can't be read: a FAIL,
and the lines stay unclassified for the next pass.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

import check_networks as cn
import common

TAG = re.compile(r" \(unclassified: [^)]*\)$")
# "<date> HH:MM[-HH:MM]Z <ip>: N allowed ..." (the date only when it differs from the row's Detected)
LINE = re.compile(r"^(?:(\d{4}-\d{2}-\d{2}) )?(\d{2}:\d{2})(?:-\d{2}:\d{2})?Z (\S+): \d+ allowed ")


def _plain(prop: dict | None) -> str:
    return "".join(x.get("plain_text", x.get("text", {}).get("content", ""))
                   for x in (prop or {}).get("rich_text", (prop or {}).get("title", [])))


def candidates(rows: list[dict], since: datetime | None) -> list[dict]:
    out = []
    for r in rows:
        p = r.get("properties", {})
        group = _plain(p.get("Group key"))
        detected = ((p.get("Detected (UTC)") or {}).get("date") or {}).get("start") or ""
        for line in _plain(p.get("What they did")).split("\n"):
            m = LINE.match(line)
            if not m:
                continue
            if TAG.search(line):
                out.append({"page_id": r["id"], "line": line, "ip": m[3]})
            elif since and group.startswith("Firebase RTDB|node-read|") and "(classified " not in line:
                day = m[1] or detected[:10]
                try:
                    t = datetime.fromisoformat(f"{day}T{m[2]}:00+00:00")
                except ValueError:
                    continue
                if t >= since:
                    out.append({"page_id": r["id"], "line": line, "ip": m[3]})
    return out


def verdict(kn: list[dict], asn: str, cc: str) -> tuple[str, str, str]:
    """(verdict, detail, name). The name comes from the same row as the verdict, so Actor never names a
    person from another place on the network: blank when that row has no Who."""
    net = [r for r in kn if r["asn"] == asn]
    if any(r["status"] == "Not a teammate" for r in net):
        return "not-a-teammate", "", ""
    here = [r for r in net if r["cc"] == cc] or net
    row = next((r for r in here if r["status"] in ("Confirmed", "Likely")), None)
    if row:
        name = cn._local(row["who"]) if row["who"] else ""
        return "teammate", f"{row['status']}" + (f", {name}" if name else ""), name
    return "unknown", "", ""


def main(argv=None, lookup=None, known=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--untagged-since")
    a = ap.parse_args(argv)
    since = datetime.fromisoformat(a.untagged_since.replace("Z", "+00:00")) if a.untagged_since else None
    with open(a.rows, encoding="utf-8") as f:
        j = json.load(f)
    rows = [r for r in (j if isinstance(j, list) else j.get("results", []))
            if ((r.get("properties", {}).get("System") or {}).get("select") or {}).get("name") == "Firebase RTDB"]
    lines = candidates(rows, since)
    result = {"ips": {}, "lines": lines}
    if lines:
        try:
            kn, _ = (known or cn.known)()
        except (RuntimeError, KeyError) as e:
            common.emit("networks", "fail", "networks-reclassify",
                        f"{len(lines)} unclassified Incident Log line(s) not re-classified: Known Team Networks "
                        f"unreadable ({'Notion rate-limited' if isinstance(e, common.RateLimited) else e})")
            result["lines"] = []
            lines = []
        else:
            for ip in sorted({x["ip"] for x in lines}):
                asn, cc, owner, _ok = (lookup or cn.network)(ip)
                if asn == "?":
                    continue  # stays unclassified for the next pass
                v, detail, name = verdict(kn, asn, cc)
                result["ips"][ip] = {"verdict": v, "network": f"AS{asn} {owner}, {cc}", "who": detail, "name": name}
            done = [x for x in lines if x["ip"] in result["ips"]]
            counts = {v: sum(result["ips"][x["ip"]]["verdict"] == v for x in done)
                      for v in ("teammate", "not-a-teammate", "unknown")}
            common.emit("networks", "alert" if counts["not-a-teammate"] else "note", "networks-reclassify",
                        f"re-classified {len(done)} of {len(lines)} unclassified Incident Log line(s): "
                        f"{counts['teammate']} teammate, {counts['not-a-teammate']} Not a teammate, "
                        f"{counts['unknown']} unknown network")
            result["lines"] = done
    fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(result, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
