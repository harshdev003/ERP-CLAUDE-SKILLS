"""
Cloud Armor outcomes from the load-balancer logs, as counts only.

Reads the security-policy fields the LB writes on every response it evaluated:
`jsonPayload.enforcedSecurityPolicy` / `previewSecurityPolicy` (outcome, priority,
configuredAction, preconfiguredExprIds, matchedFieldType, rateLimitAction.{key,outcome}) and
`jsonPayload.statusDetails` (`denied_by_security_policy` when the edge answered,
`response_sent_by_backend` when the request reached the app).

VERBOSE logging adds `matchedFieldValue` / `matchedFieldName`, which can hold user input and
cookies, and a per-IP rate-limit key is the client's address: neither is ever kept. Paths are
masked to two short lowercase segments, a rate-limit key is only counted, and a Blocklist row's
Range (its title) is never read out. Same counting as AuraInfra bin/armor-report.py.
"""

from __future__ import annotations

import collections
import hashlib
import re

POLICY = "aura-web-edge"
BLOCKLIST_DB = "3ccdfd67-e389-4b8e-89cf-41916c7d875f"  # Notion "Cloud Armor Blocklist" (Security)
INCIDENT_RULES = range(100, 150)  # per-incident block rules in aura-web-edge
OWN_UA = re.compile(r"^(AuraConsumerApp|Aura|Aurie)/")
EXCEED = ("RATE_LIMIT_THRESHOLD_EXCEED", "BAN_THRESHOLD_EXCEED")
KINDS = (("enforced", "enforcedSecurityPolicy"), ("preview", "previewSecurityPolicy"))

# Denies, and rate limits past their threshold. A conforming request is not read: on prod
# that is most of the traffic a rate rule sees.
ARMOR_LQL = (
    '(jsonPayload.enforcedSecurityPolicy.outcome="DENY" OR jsonPayload.previewSecurityPolicy.outcome="DENY" '
    'OR jsonPayload.enforcedSecurityPolicy.rateLimitAction.outcome=~"EXCEED" '
    'OR jsonPayload.previewSecurityPolicy.rateLimitAction.outcome=~"EXCEED")'
)


def _priority(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _hashed(key):
    return hashlib.sha1(str(key).encode()).hexdigest()[:12] if key else None


def policy(sp):
    """One security-policy block with the safe fields only; None when the LB wrote none."""
    if not isinstance(sp, dict) or not sp:
        return None
    rl = sp.get("rateLimitAction") or {}
    return {
        "outcome": sp.get("outcome"),
        "priority": _priority(sp.get("priority")),
        "action": sp.get("configuredAction"),
        "signature": ",".join(sp.get("preconfiguredExprIds") or []) or "-",
        "field": sp.get("matchedFieldType") or "-",
        "rate": rl.get("outcome"),
        "rate_key": _hashed(rl.get("key")),  # an IP for a per-IP rule: hashed, only ever counted
    }


def fields(payload):
    """The Cloud Armor part of one LB entry's jsonPayload."""
    p = payload if isinstance(payload, dict) else {}
    return {"status": p.get("statusDetails") or "-",
            "enforced": policy(p.get("enforcedSecurityPolicy")),
            "preview": policy(p.get("previewSecurityPolicy"))}


def edge(armor):
    """blocked (the edge answered), reached (the backend answered) or other (disconnects, …)."""
    if armor["status"] == "denied_by_security_policy" or (armor["enforced"] or {}).get("outcome") == "DENY":
        return "blocked"
    if armor["status"] == "response_sent_by_backend":
        return "reached"
    return "other"


def mask_path(path):
    segs = [s for s in (path or "").split("?")[0].split("/") if s][:2]
    return "/" + "/".join(s if re.fullmatch(r"[a-z_]{1,20}", s) else ":x" for s in segs)


def backend_name(name):
    m = re.search(r"aura-(?:web|services)-(.+?)-\d+-[0-9a-f]{8}$", name or "")
    return m.group(1) if m else (name or "-")


def _rows(counter, names, top):
    return [dict(zip(names, k), n=n) for k, n in counter.most_common(top)]


def summarise(entries, coverage=None, confirmed_ips=frozenset(), blocklist=None, top=40):
    """Counts over ARMOR_LQL entries: {"denies", "rate", "alerts", "own", "incident_rules", "coverage"}.

    entries: dicts with ip, ua, path, backend and armor (fields()). coverage: (backend, has_policy)
    pairs from a sample of all responses. confirmed_ips: own-UA deny IPs that also had a success
    elsewhere in the window. blocklist: {priority: row summary} from blocklist_rows().
    """
    denies = {k: collections.Counter() for k, _ in KINDS}
    rate = collections.Counter()
    rate_keys = collections.defaultdict(set)
    own = collections.Counter()
    own_ips = collections.defaultdict(set)
    own_confirmed = collections.defaultdict(set)
    incident = collections.Counter()
    for e in entries:
        backend, path = backend_name(e.get("backend")), mask_path(e.get("path"))
        app = bool(OWN_UA.match(e.get("ua") or ""))
        for kind, _ in KINDS:
            sp = e["armor"].get(kind)
            if not sp:
                continue
            if sp["outcome"] == "DENY":
                denies[kind][(sp["priority"], sp["signature"], sp["field"], backend, path)] += 1
                if kind == "enforced" and sp["priority"] in INCIDENT_RULES:
                    incident[sp["priority"]] += 1
                if app:
                    k = (kind, sp["priority"], sp["signature"], backend)
                    own[k] += 1
                    own_ips[k].add(e["ip"])
                    if e["ip"] in confirmed_ips:
                        own_confirmed[k].add(e["ip"])
            if sp["rate"] in EXCEED:
                k = (kind, sp["priority"], sp["rate"], backend)
                rate[k] += 1
                if sp["rate_key"]:
                    rate_keys[k].add(sp["rate_key"])
    rate_rows = [dict(zip(("kind", "priority", "outcome", "backend"), k), n=n, keys=len(rate_keys[k]))
                 for k, n in rate.most_common(top)]
    alerts = []
    by_rule = collections.defaultdict(lambda: {"n": 0, "keys": set()})
    for k, n in rate.items():
        kind, priority, outcome, _backend = k
        if kind == "enforced":
            a = by_rule[("ban" if outcome == "BAN_THRESHOLD_EXCEED" else "throttle", priority)]
            a["n"] += n
            a["keys"] |= rate_keys[k]
    for (what, priority), a in sorted(by_rule.items(), key=lambda x: (x[0][0], x[0][1] or 0)):
        alerts.append({"key": f"{what}|{priority}", "type": what, "priority": priority,
                       "requests": a["n"], "keys": len(a["keys"])})
    own_rows = [dict(zip(("kind", "priority", "signature", "backend"), k), n=n, ips=len(own_ips[k]),
                     confirmed=len(own_confirmed[k])) for k, n in own.most_common(top)]
    rules = []
    for priority in sorted(set(incident) | set(blocklist or {})):
        row = (blocklist or {}).get(priority)
        note = None
        if blocklist is None:
            pass  # the join was skipped: no row is unknown, not missing
        elif row is None:
            note = "no Blocklist row for this rule"
        elif incident.get(priority) and row.get("status") != "Active":
            note = f"still denying while its Blocklist row is {row.get('status') or 'unset'}"
        elif not incident.get(priority) and row.get("status") == "Active":
            note = "Active, no hits in this window"
        rules.append({"priority": priority, "n": incident.get(priority, 0), "row": row, "note": note})
    cov = collections.defaultdict(lambda: [0, 0])
    for backend, has_policy in coverage or ():
        c = cov[backend_name(backend)]
        c[0] += 1
        c[1] += bool(has_policy)
    return {
        "denies": {kind: _rows(c, ("priority", "signature", "field", "backend", "path"), top)
                   for kind, c in denies.items()},
        "deny_totals": {kind: sum(c.values()) for kind, c in denies.items()},
        "rate": rate_rows,
        "alerts": alerts,
        "own": own_rows,
        "incident_rules": rules,
        "coverage": [{"backend": b, "responses": n, "with_policy": w, "share": round(w / n, 3)}
                     for b, (n, w) in sorted(cov.items(), key=lambda x: -x[1][0])],
    }


def _plain(prop):
    t = (prop or {}).get("type")
    if t == "select":
        return ((prop.get("select") or {}).get("name"))
    if t == "date":
        return ((prop.get("date") or {}).get("start"))
    if t == "number":
        return prop.get("number")
    if t == "relation":
        return [r["id"] for r in prop.get("relation") or []]
    return None


def blocklist_rows(call):
    """{rule priority: {status, expires, incidents}} for the aura-web-edge rows. The Range title
    and the free-text Reason may carry an address, so neither is read."""
    out, cursor = {}, None
    while True:
        body = {"filter": {"property": "Policy", "select": {"equals": POLICY}}, "page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        q = call("POST", f"/databases/{BLOCKLIST_DB}/query", body)
        for r in q.get("results", []):
            p = r.get("properties", {})
            priority = _priority(_plain(p.get("Rule priority")))
            if priority is None or r.get("archived"):
                continue
            row = {"status": _plain(p.get("Status")), "expires": _plain(p.get("Expires (UTC)")),
                   "incidents": [f"https://app.notion.com/p/{i.replace('-', '')}"
                                 for i in _plain(p.get("Incident")) or []],
                   "page": f"https://app.notion.com/p/{r['id'].replace('-', '')}"}
            # Two rows on one rule: keep the Active one.
            if priority not in out or row["status"] == "Active":
                out[priority] = row
        if not q.get("has_more"):
            return out
        cursor = q["next_cursor"]


def report(s, n_entries, limited=False, coverage_n=0):
    """Printable lines, counts and masked paths only."""
    out = [f"\nCloud Armor ({POLICY}): {n_entries} deny / over-limit entries"
           + (" (read limit hit: counts are lower bounds)" if limited else "")]
    for a in s["alerts"]:
        what = "IP ban" if a["type"] == "ban" else "throttle saturated"
        out.append(f"  ALERT {what}: rule {a['priority']}, {a['requests']} requests, {a['keys']} distinct keys")
    for kind in ("enforced", "preview"):
        rows = s["denies"][kind]
        out.append(f"  {kind} denies: {s['deny_totals'][kind]} (top by priority  signature  field  backend  path)")
        for r in rows[:10]:
            out.append(f"    {r['n']:>6}  {r['priority']}  {r['signature']}  {r['field']}  {r['backend']}  {r['path']}")
    for r in s["rate"][:10]:
        out.append(f"  rate {r['kind']}: rule {r['priority']} {r['outcome']} {r['backend']}: "
                   f"{r['n']} requests, {r['keys']} distinct keys")
    for r in s["own"][:10]:
        verdict = (f"{r['confirmed']} of {r['ips']} IPs also succeeded elsewhere: likely false positive"
                   if r["confirmed"] else f"{r['ips']} IPs, app user agent only (a label, not proof)")
        out.append(f"  own caller? {r['kind']} rule {r['priority']} {r['signature']} {r['backend']}: "
                   f"{r['n']} denies, {verdict}")
    for r in s["incident_rules"]:
        row = r["row"] or {}
        line = f"  incident rule {r['priority']}: {r['n']} denies"
        if row:
            line += f", Blocklist {row.get('status')}, expires {row.get('expires') or '-'}"
            if row.get("incidents"):
                line += f", incident {row['incidents'][0]}"
        if r["note"]:
            line += f"  ! {r['note']}"
        out.append(line)
    if s["coverage"]:
        out.append(f"  policy coverage (newest {coverage_n} responses): " + ", ".join(
            f"{c['backend']} {c['share']:.0%} of {c['responses']}" for c in s["coverage"][:12]))
    return out
