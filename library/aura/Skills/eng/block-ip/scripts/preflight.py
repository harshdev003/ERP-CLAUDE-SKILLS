#!/usr/bin/env python3
"""Check ranges before an incident block, and write the plan armor_blocklist.py add reads.

    preflight.py --incident <Security Incident Log row URL> [--ranges <ip>,<cidr>] \\
        [--days 7] --plan /tmp/block-ip-plan.json

Without --ranges the ranges are the row's `IP addresses` (its overflow page included). Each range
gets a verdict:

  refuse  not public, too broad, or one of our own addresses (a static external address of the
          project or a Cloud Monitoring uptime-check source)
  warn    its network is a Known Team Networks row, or it also got 2xx from our app routes or
          webhook routes in the window (a shared, NAT or CGNAT address may carry real users)
  ok      none of the above

Nothing printed names an address: a range is `#k` plus its prefix length, and the plan file
(mode 600) carries the ranges to `armor_blocklist.py add --ranges-file`. Exit 0 when every range is
ok or warn, 3 when any is refused, 2 when the incident row can't be read.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import ipaddress
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import armor_blocklist as ab  # noqa: E402
import gcloud_sa  # noqa: E402

KNOWN_DB = "40475e23-af20-8102-b20f-d4339a365f14"  # Known Team Networks (Security)
IP_TOKEN = re.compile(r"(?<![\w.:])((?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?|[0-9a-fA-F:]{2,39}:[0-9a-fA-F]{0,4}(?:/\d{1,3})?)(?![\w.:])")
OVERFLOW = re.compile(r"… (\d+) more in Notion page ([0-9a-f-]{32,36})")
APP_ROUTE = re.compile(r"^/(api|auraServices|users|v\d+)(/|$)")
WEBHOOK = re.compile(r"webhook|/hooks?/|/stripe|/apple/|/revenuecat|/iterable", re.I)


def _plain(prop):
    return "".join(x.get("plain_text", "") for x in (prop or {}).get("rich_text", []) or [])


def incident_ips(page, call=ab.notion):
    """Addresses in the row's `IP addresses`, and in its overflow child page when it has one."""
    text = _plain(page.get("properties", {}).get("IP addresses"))
    tokens = IP_TOKEN.findall(OVERFLOW.sub("", text))
    m = OVERFLOW.search(text)
    if m:
        # The overflow page is JSON holding `ips` and `uas`: read the `ips` keys only, never scan
        # the user agents (a dotted quad inside one is not a source address).
        code, cursor = "", None
        while True:
            q = call("GET", f"/blocks/{m[2]}/children" + (f"?start_cursor={cursor}" if cursor else ""))
            for b in q.get("results", []):
                if b.get("type") == "code":
                    code += "".join(x.get("plain_text", "") for x in b["code"]["rich_text"])
            if not q.get("has_more"):
                break
            cursor = q["next_cursor"]
        try:
            ips = json.loads(code).get("ips")
        except (ValueError, AttributeError):
            ips = None
        if not isinstance(ips, dict):
            # Never block a partial list: the rest of the row's addresses are on that page.
            raise RuntimeError("the row's overflow page has no readable `ips` list; pass --ranges")
        tokens += list(ips)
    out = []
    for tok in tokens:
        try:
            net = str(ipaddress.ip_network(tok, strict=False))
        except ValueError:
            continue
        if net not in out:
            out.append(net)
    return out


def own_addresses(run=gcloud_sa.run):
    """Our static external addresses and the uptime-check sources, as networks. Raises on a read
    failure: a block is never checked against an empty list by accident."""
    nets = []
    a = run(["compute", "addresses", "list", "--project", ab.PROJECT, "--format", "json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if a.returncode:
        raise RuntimeError(f"addresses list failed as {gcloud_sa.credential()}: {(a.stderr or '').strip()[-200:]}")
    nets += [ipaddress.ip_network(x["address"]) for x in json.loads(a.stdout or "[]") if x.get("address")]
    u = run(["monitoring", "uptime", "list-ips", "--format", "json"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if u.returncode:
        raise RuntimeError(f"uptime list-ips failed as {gcloud_sa.credential()}: {(u.stderr or '').strip()[-200:]}")
    for x in json.loads(u.stdout or "[]"):
        try:
            nets.append(ipaddress.ip_network(x.get("ipAddress") or x.get("ip_address"), strict=False))
        except (TypeError, ValueError):
            pass
    return nets


def team_asns(call=ab.notion):
    """{asn: status} from Known Team Networks (`Network` starts with `AS<n>`)."""
    out, body = {}, {"page_size": 100}
    while True:
        q = call("POST", f"/data_sources/{_known_ds(call)}/query", body)
        for r in q.get("results", []):
            p = r.get("properties", {})
            net = "".join(x.get("plain_text", "") for x in (p.get("Network") or {}).get("title", []))
            status = ((p.get("Status") or {}).get("select") or {}).get("name") or ""
            if net.startswith("AS"):
                asn = net.split()[0][2:]
                # Not a teammate on any row of the network wins: it is not ours.
                if out.get(asn) != "Not a teammate":
                    out[asn] = status
        if not q.get("has_more"):
            return out
        body["start_cursor"] = q["next_cursor"]


_KNOWN_DS = {}


def _known_ds(call):
    if "id" not in _KNOWN_DS:
        db = call("GET", f"/databases/{KNOWN_DB}")
        _KNOWN_DS["id"] = db["data_sources"][0]["id"]
    return _KNOWN_DS["id"]


def asns_of(nets, timeout=30):
    """{range: ASN or None} from Team Cymru's bulk whois (one connection for every range's first
    address). An unreachable whois leaves every ASN None: the team-network check is then skipped,
    and the report says so."""
    import socket
    nets = list(nets)
    ips = [str(ipaddress.ip_network(n).network_address) for n in nets]
    out = {n: None for n in nets}
    if not ips:
        return out
    try:
        with socket.create_connection(("whois.cymru.com", 43), timeout=timeout) as s:
            s.sendall(("begin\nnoheader\n" + "\n".join(ips) + "\nend\n").encode())
            data = b""
            while chunk := s.recv(65536):
                data += chunk
    except OSError:
        return out
    by_ip = {}
    for line in data.decode("utf-8", "replace").splitlines():
        cols = [c.strip() for c in line.split("|")]
        if len(cols) >= 2 and cols[0].isdigit():
            by_ip[cols[1]] = cols[0]
    for n, ip in zip(nets, ips):
        out[n] = by_ip.get(ip)
    return out


def traffic(nets, since, run=gcloud_sa.run, limit=20000, chunk=10):
    """{range: counts} from the LB logs since `since`: statuses, app-route 2xx, webhook 2xx, edge
    denies. One read per `chunk` ranges; each entry is counted against every range holding its
    address. Paths and addresses are bucketed in memory, never kept or printed."""
    nets = list(nets)
    out = {n: collections.Counter() for n in nets}
    for i in range(0, len(nets), chunk):
        part = nets[i:i + chunk]
        who = " OR ".join(f'ip_in_net(httpRequest.remoteIp, "{n}")' for n in part)
        flt = f'resource.type="http_load_balancer" timestamp>="{since}" ({who})'
        res = run(["logging", "read", flt, "--project", ab.PROJECT, "--limit", str(limit), "--format",
                   "json(httpRequest.remoteIp,httpRequest.status,httpRequest.requestUrl,jsonPayload.statusDetails)"],
                  capture_output=True, text=True, encoding="utf-8", errors="replace")
        if res.returncode:
            raise RuntimeError(f"logging read failed as {gcloud_sa.credential()}: {(res.stderr or '').strip()[-200:]}")
        entries = json.loads(res.stdout or "[]")
        parsed = [(ipaddress.ip_network(n), n) for n in part]
        for e in entries:
            r = e.get("httpRequest") or {}
            try:
                ip = ipaddress.ip_address(r.get("remoteIp") or "")
            except ValueError:
                continue
            status = int(r.get("status") or 0)
            path = re.sub(r"^https?://[^/]+", "", r.get("requestUrl") or "").split("?")[0] or "/"
            for net, key in parsed:
                if ip.version != net.version or ip not in net:
                    continue
                c = out[key]
                c["requests"] += 1
                c[f"{status // 100}xx"] += 1
                if (e.get("jsonPayload") or {}).get("statusDetails") == "denied_by_security_policy":
                    c["edge_denied"] += 1
                if 200 <= status < 300 and APP_ROUTE.match(path):
                    c["app_2xx"] += 1
                if 200 <= status < 300 and WEBHOOK.search(path):
                    c["webhook_2xx"] += 1
        if len(entries) >= limit:
            for n in part:
                out[n]["capped"] = 1
    return {n: dict(c) for n, c in out.items()}


def verdict(net, own, asns, team, counts):
    """(level, reasons) for one range."""
    n = ipaddress.ip_network(net)
    reasons, level = [], "ok"
    if any(n.version == o.version and n.overlaps(o) for o in own):
        return "refuse", ["overlaps one of our own addresses (static external or uptime check)"]
    asn = asns.get(net)
    status = team.get(asn) if asn else None
    if status and status != "Not a teammate":
        level = "warn"
        reasons.append(f"AS{asn} is a Known Team Networks row ({status}): a shared ISP, or one of ours?")
    if counts.get("app_2xx"):
        level = "warn"
        reasons.append(f"{counts['app_2xx']} 2xx on app routes: may be a shared, NAT or CGNAT address")
    if counts.get("webhook_2xx"):
        level = "warn"
        reasons.append(f"{counts['webhook_2xx']} 2xx on webhook routes: a webhook source?")
    if counts.get("capped"):
        level = "warn"
        reasons.append("traffic not fully read (the log read hit its limit): older requests unchecked")
    return level, reasons


def label(i, net):
    return f"#{i} /{ipaddress.ip_network(net).prefixlen} IPv{ipaddress.ip_network(net).version}"


def main(argv=None, call=ab.notion, run=gcloud_sa.run, asn_lookup=asns_of, now=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--incident", required=True, help="Security Incident Log row URL")
    p.add_argument("--ranges", help="comma-separated IPs or CIDRs (default: the row's IP addresses)")
    p.add_argument("--days", type=int, default=7, help="traffic window (default 7)")
    p.add_argument("--plan", required=True, help="write the plan (mode 600) for armor_blocklist.py add")
    a = p.parse_args(argv)
    try:
        incident = ab.page_id(a.incident)
        page = ab.check_incident(incident)
    except SystemExit as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    try:
        raw = a.ranges.split(",") if a.ranges else incident_ips(page, call)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not raw:
        print("ERROR: the incident row lists no addresses; pass --ranges", file=sys.stderr)
        return 2
    now = now or dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(days=a.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        own, team = own_addresses(run), team_asns(call)
    except (RuntimeError, KeyError, ValueError) as e:
        print(f"ERROR: cannot check our own addresses: {e}", file=sys.stderr)
        return 2
    rows, ok_ranges, refused, valid = [], [], 0, {}
    for i, item in enumerate(raw, 1):
        try:
            (valid[i],) = ab.check_ranges([item])
        except SystemExit as e:
            rows.append({"n": i, "level": "refuse", "reasons": [str(e).replace("range #1", f"range #{i}")]})
            refused += 1
    try:
        seen = traffic(valid.values(), since, run)
    except RuntimeError as e:
        seen = {n: {"error": str(e)} for n in valid.values()}
    asns = asn_lookup(valid.values())
    if valid and not any(asns.values()):
        print("# whois gave no ASNs: the Known Team Networks check was skipped", file=sys.stderr)
    for i, net in valid.items():
        counts = seen[net]
        level, reasons = verdict(net, own, asns, team, counts)
        if "error" in counts:
            level = "refuse" if level == "refuse" else "warn"
            reasons.append(f"traffic not checked: {counts['error']}")
        rows.append({"n": i, "label": label(i, net), "level": level, "reasons": reasons, "counts": counts})
    rows.sort(key=lambda r: r["n"])
    for r in rows:
        if r["level"] != "refuse":
            ok_ranges.append(valid[r["n"]])
        elif r["n"] in valid:  # one of ours; a malformed range was counted above
            refused += 1
    print(f"block-ip preflight: incident {incident}, {len(raw)} range(s), traffic since {since}")
    for r in rows:
        c = r.get("counts") or {}
        tally = ", ".join(f"{k} {c[k]}" for k in ("requests", "2xx", "3xx", "4xx", "5xx", "edge_denied") if c.get(k))
        tally += " (capped)" if c.get("capped") else ""
        print(f"  {r.get('label', '#%d' % r['n'])}  {r['level'].upper()}" + (f"  ({tally})" if tally else ""))
        for why in r["reasons"]:
            print(f"    - {why}")
    rules = (len(ok_ranges) + ab.MAX_RANGES - 1) // ab.MAX_RANGES
    fd = os.open(a.plan, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"incident": incident, "ranges": ok_ranges[:ab.MAX_RANGES],
                   "rest": ok_ranges[ab.MAX_RANGES:], "checked": rows}, fh, indent=1)
    print(f"plan: {a.plan} ({min(len(ok_ranges), ab.MAX_RANGES)} range(s) for this rule"
          + (f"; {len(ok_ranges) - ab.MAX_RANGES} more need {rules - 1} more rule(s)" if rules > 1 else "") + ")")
    return 3 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
