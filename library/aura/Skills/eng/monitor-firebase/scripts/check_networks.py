#!/usr/bin/env python3
"""Allowed root / whole-node reads (Firebase console, admin credentials) from networks the team doesn't know.

  check_networks.py --detail <probe-detail.jsonl> [--verdicts <verdicts.json>] [--rest-bursts <file>]

Reads the profiler's --probe-detail file (it holds IPs: never print them), keeps the allowed
attempts, looks up each address's ASN and country (Team Cymru whois, retried once, then the
ipgeolocation ASN when whois still fails) and matches them to the
Notion "Known Team Networks" DB (NOTION_TOKEN). Covers root reads and allowed reads of one whole
real node (probe detail tag "rule exists"). Our own sessions (AuraClaude user agent) are noted.

A browser read is matched to the RTDB ADMIN_READ audit log (ListDatabaseInstances /
GetDatabaseInstance within 2 minutes, browser user agent only, so the profiler's own FirebaseCLI
calls never match). That names the console account, which decides the outcome:
  note   the account is on the allow-list (the DB's "Console accounts" options) and a row for this
         network lists it: "known: <account>, <city>"; Last seen is updated. Whatever the Status.
  note   allow-listed, but no row lists it: the account is added to the network's row, or a row is
         created (Network = the ASN, Status Likely). Never a duplicate row.
  alert  the account isn't allow-listed, or none was found, or the network's row is "Not a
         teammate" (always, even for an allow-listed account).
  note   no console account at all, 0 bytes, and the network has a Confirmed row and no "Not a
         teammate" row: a teammate testing the app ("teammate read").
  note   no console account at all, a 0-byte root listener-listen and nothing else, from a desktop
         browser, and the network has a Confirmed or Likely row listing an allow-listed console
         account, and no "Not a teammate" row: an open console tab reconnecting ("probable console
         reconnect"). A reconnect makes no new ListDatabaseInstances, so there is no account to find.
         The address goes in `console`. Bytes, a REST read, any other path, an Unconfirmed or new
         network, or an audit log that could not be read still alert.
A whole-node listener whose address made a SettingsService.GetFirebaseTokens call (Cloud Audit Log)
within the hour before it is the console's data viewer, which fetches a token and then opens
listeners; an open tab reconnects on the same token, valid for an hour, without a new call. A note,
browser or not, unless the network is "Not a teammate". The fetch can also land a few seconds after
the viewer's first read (2026-11-06 14:30Z: guessed names read 30 s before the token call), so a
console-shaped read (a desktop browser, allowed, 0 or few bytes, listens and reads only) matches a
fetch up to 5 minutes after it too, and on any node, not only a whole-node listener. No account is named.
When a browser read under 5 minutes old has neither a token call nor a console account, the audit log is
read once more after 150 s: the call may land after the pass's first read. On an Incident Log
row, a Known Team Networks teammate makes Actor "Staff", with "likely teammate <name>, not confirmed" in
Notes and the name in What they did (Jerome, 2026-11-10); an Actor already set is kept.
Any other allowed read keeps the Status rule: note on Confirmed / Likely, alert otherwise.
Rows match on network (ASN) + country + City + Region, so one network seen in two places has two
rows; the account decides the rule above. The teammate checks (a Confirmed row, or a row listing a
console account) take a Confirmed row on the ASN alone: whois's country for an address can differ
from the country its network serves. Country, Region, City and
ISP come from ipgeolocation.io (the bundled ip_location.py, IPGEOLOCATION_API_KEY) and are written
on create, and on a Last seen update only where the field is still blank: a value a person set is
never overwritten. A lookup in a new place makes a new row (allow-listed account) rather than editing
the old one; it never makes an alert by itself. Without the key they stay blank and rows match on
network + country. Never an address in Notion.

--verdicts writes, for monitor-rtdb-probes' incident planner (mode 600: it holds IPs), the addresses
whose read was attributed to an allow-listed account on a Confirmed or Likely row (`attributed`: never
an incident), those with a console account in the audit log within 2 minutes (`console`), and the
0-byte reads from a Confirmed network (`teammate`: a teammate's read, never logged, counted),
and every address this check raised an ALERT on (`alert`): an unconfirmed read stays in the Security
Incident Log with its address, so the planner logs that address's whole-node reads too (Jerome,
2026-10-30). Every address it could not classify at all (`fail`: the whois or Known Team Networks read
failed; our own sessions excluded) is logged the same way. Every address on a Confirmed row
(`confirmed`): a read of theirs that is still logged shows "see Known Team Networks" in place of the
address (Jerome, 2026-11-09). It is written on every run, empty when nothing was classified.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

import common
import gcloud_sa
import ip_location

KNOWN_DB = "40475e23-af20-8102-b20f-d4339a365f14"


NOTION = "https://api.notion.com/v1"
WHOIS_RETRY_S = 3
# Notion 429s (other sessions share the NOTION_TOKEN integration): retries before giving up.
NOTION_RETRIES = 6


def _no_who(ua: str) -> str:
    """Drop the person from AuraClaude/<who>/<plugin>:<skill>: Slack names the skill, never a teammate."""
    return re.sub(r"^AuraClaude/[^/\s:]+/(?=\S)", "AuraClaude/", ua)


def _notion(path: str, *, data: dict | None = None, method: str | None = None) -> dict:
    tok = os.environ.get("NOTION_TOKEN")
    if not tok:
        raise RuntimeError("NOTION_TOKEN unset")
    return common.curl_json(f"{NOTION}{path}", data=data, method=method, retries_429=NOTION_RETRIES,
                            headers={"Authorization": f"Bearer {tok}", "Notion-Version": "2022-06-28"})


def _local(who: str) -> str:
    """An email reduced to its local part (`rose@`): the most a report or Slack line may carry."""
    return re.sub(r"([A-Za-z0-9._%+-]+@)[A-Za-z0-9.-]+\.[A-Za-z]{2,}", r"\1", who or "")


def _text(prop: dict) -> str:
    return "".join(x["plain_text"] for x in prop.get("title", prop.get("rich_text", [])))


COLUMNS: set[str] = set()


def known() -> tuple[list[dict], set[str]]:
    """The Known Team Networks rows, and the console-account allow-list (the multi_select options).
    Also records which columns exist, so a write never names one the DB doesn't have."""
    schema = _notion(f"/databases/{KNOWN_DB}")
    COLUMNS.clear()
    COLUMNS.update(schema["properties"])
    allow = {o["name"].lower() for o in schema["properties"]["Console accounts"]["multi_select"]["options"]}
    rows, body = [], {"page_size": 100}
    while True:
        d = _notion(f"/databases/{KNOWN_DB}/query", data=body)
        rows += d.get("results", [])
        if not d.get("has_more"):
            break
        body["start_cursor"] = d["next_cursor"]
    out = []
    for r in rows:
        p = r["properties"]
        net = _text(p["Network"])
        out.append({
            "id": r["id"], "network": net,
            "asn": net.split()[0].removeprefix("AS") if net.startswith("AS") else "",
            "cc": (p["Country"]["select"] or {}).get("name", ""), "city": _text(p["City"]),
            "region": _text(p["Region"]) if "Region" in p else "",
            "isp": _text(p["ISP"]) if "ISP" in p else "",
            "status": (p["Status"]["select"] or {}).get("name", ""), "who": _text(p["Who"]),
            "accounts": {o["name"].lower() for o in p["Console accounts"]["multi_select"]},
        })
    return out, allow


def _same_place(row: dict, geo: dict) -> bool:
    """A row is at this location unless its City or Region is set and differs from the lookup's.
    A blank field on either side matches (an older row, or a lookup with no key or credits)."""
    for key in ("city", "region"):
        mine, seen = (row.get(key) or "").strip().lower(), (geo.get(key) or "").strip().lower()
        if mine and seen and mine != seen:
            return False
    return True


def classify(rows: list[dict], allow: set[str], asn: str, cc: str | None, account: str | None,
             geo: dict | None = None) -> tuple[str, str | None, dict | None]:
    """(level, action, row) for one browser read. action: touch | add | create | None.
    Rows match on network + country + City + Region: the same network seen in another place gets
    its own row. `Not a teammate` anywhere on the network always alerts. A row's Country matches
    either the lookup's country or whois's (`cc`): they can disagree (a VPN whose ASN is registered
    in NL, located in San Jose), and a row stored under either must not be duplicated. With no
    whois country (`cc` None: whois was down) a row in any country matches."""
    countries = {cc, (geo or {}).get("country_code") or cc}
    net = [r for r in rows if r["asn"] == asn and (cc is None or r["cc"] in countries)]
    barred = next((r for r in net if r["status"] == "Not a teammate"), None)
    if barred:
        return "alert", None, barred
    # Rows in the lookup's own country first, so an add lands on the row for where the read came from.
    here = sorted((r for r in net if _same_place(r, geo or {})),
                  key=lambda r: r["cc"] != (geo or {}).get("country_code"))
    if account and account in allow:
        hit = next((r for r in here if account in r["accounts"]), None)
        if hit:
            return "note", "touch", hit
        return ("note", "add", here[0]) if here else ("note", "create", None)
    return "alert", None, (here or net or [None])[0]


def _place(geo: dict, cc: str, row: dict | None = None) -> dict:
    """Country, Region, City and ISP for the columns the DB has, only where the row's field is
    blank: a value a person set is kept (geo-IP puts many ISPs at their head office). Never an
    address."""
    row = row or {}
    rt = lambda v: {"rich_text": [{"text": {"content": v}}]}
    out = {}
    for col, key in (("Region", "region"), ("City", "city"), ("ISP", "isp")):
        if geo.get(key) and col in COLUMNS and not row.get(key):
            out[col] = rt(geo[key])
    if cc and "Country" in COLUMNS and not row.get("cc"):
        out["Country"] = {"select": {"name": cc}}
    return out


def record(action: str, row: dict | None, account: str, asn: str, cc: str, geo: dict,
           t: str, rows: list[dict]) -> None:
    """Write the network row for an allow-listed console read, and keep `rows` in step so a later
    address in the same pass sees it (no duplicate row, no lost account). Never an address.
    Country comes from the same lookup as City and Region (whois only when the lookup has none);
    a new row whose whois country differs says so in Notes."""
    day = t[:10]
    country = geo.get("country_code") or cc
    seen = {"Last seen": {"date": {"start": day}}, **_place(geo, country, row)}
    if action == "touch":
        _notion(f"/pages/{row['id']}", method="PATCH", data={"properties": seen})
    elif action == "add":
        accounts = sorted(row["accounts"] | {account})
        _notion(f"/pages/{row['id']}", method="PATCH", data={"properties": {
            **seen, "Console accounts": {"multi_select": [{"name": a} for a in accounts]}}})
        row["accounts"].add(account)
    elif action == "create":
        made = _notion("/pages", data={"parent": {"database_id": KNOWN_DB}, "properties": {
            **seen,
            "Network": {"title": [{"text": {"content": f"AS{asn}"}}]},
            "Who": {"rich_text": [{"text": {"content": _local(account)}}]},
            "Console accounts": {"multi_select": [{"name": account}]},
            "Status": {"select": {"name": "Likely"}},
            "First seen": {"date": {"start": day}},
            **({"Notes": {"rich_text": [{"text": {"content": f"whois country: {cc}"}}]}}
               if cc and cc != country and "Notes" in COLUMNS else {})}})
        rows.append({"id": made.get("id", ""), "network": f"AS{asn}", "asn": asn, "cc": country,
                     "city": geo.get("city", ""), "region": geo.get("region", ""), "isp": geo.get("isp", ""),
                     "status": "Likely", "who": _local(account), "accounts": {account}})
    if row is not None:
        for key in ("region", "city", "isp"):
            row[key] = row.get(key) or geo.get(key, "")


AUDIT = ('protoPayload.serviceName="firebasedatabase.googleapis.com" AND '
         '(protoPayload.methodName:"ListDatabaseInstances" OR protoPayload.methodName:"GetDatabaseInstance")')
MATCH_S = 120
AUDIT_LIMIT = 1000


def _epoch(t: str) -> float:
    return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()


def console_accounts(t0: str, t1: str) -> list[tuple[float, str]]:
    """(time, email) of every browser call to the RTDB admin API in [t0 - 2 min, t1 + 2 min]."""
    lo = datetime.fromtimestamp(_epoch(t0) - MATCH_S, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hi = datetime.fromtimestamp(_epoch(t1) + MATCH_S, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = gcloud_sa.run(["logging", "read", f'{AUDIT} AND timestamp>="{lo}" AND timestamp<="{hi}"',
                         f"--project={common.PROJECT}", f"--limit={AUDIT_LIMIT}", "--format=json"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode:
        raise RuntimeError(f"audit log read failed as {gcloud_sa.credential()}")
    entries = json.loads(out.stdout or "[]")
    if len(entries) >= AUDIT_LIMIT:
        # A full page may have cut off the matching call; say so rather than "no account".
        raise RuntimeError(f"audit log read hit the {AUDIT_LIMIT}-entry cap for {lo}..{hi}")
    found = []
    for e in entries:
        p = e.get("protoPayload") or {}
        ua = (p.get("requestMetadata") or {}).get("callerSuppliedUserAgent") or ""
        email = (p.get("authenticationInfo") or {}).get("principalEmail") or ""
        if ua.startswith("Mozilla/") and "@" in email:
            found.append((_epoch(e["timestamp"]), email.lower()))
    return found


TOKEN_METHOD = (f'logName="projects/{common.PROJECT}/logs/cloudaudit.googleapis.com%2Factivity" AND '
                'protoPayload.serviceName="mobilesdk-pa.googleapis.com" AND '
                'protoPayload.methodName:"SettingsService.GetFirebaseTokens"')
TOKEN_S = 3600  # the console's Firebase token lifetime
TOKEN_AFTER_S = 300  # the console can fetch its token just after its first read (ENGBACK-2377)
CONSOLE_BYTES = 1024  # a console viewer's read of a missing or tiny node returns next to nothing
CONSOLE_OPS = {"listener-listen", "rest-read"}
# The pass reads the audit log right after the profile, so the console's token or admin call for a read in
# its last minutes may not be there yet (2026-11-09 14:45Z and 16:24Z: a staff console logged as an incident).
TOKEN_RETRY_S = 150


def token_calls(t0: str, t1: str) -> list[tuple[float, str]]:
    """(time, caller address) of every GetFirebaseTokens call in [t0 - 1 h, t1 + 5 min]. Kept in memory only."""
    lo = datetime.fromtimestamp(_epoch(t0) - TOKEN_S, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Second precision: round up, or a call in the listener's own second is cut off.
    hi = datetime.fromtimestamp(_epoch(t1) + TOKEN_AFTER_S + 1, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = gcloud_sa.run(["logging", "read", f'{TOKEN_METHOD} AND timestamp>="{lo}" AND timestamp<="{hi}"',
                         f"--project={common.PROJECT}", f"--limit={AUDIT_LIMIT}", "--format=json"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode:
        raise RuntimeError(f"token-call audit read failed as {gcloud_sa.credential()}")
    entries = json.loads(out.stdout or "[]")
    if len(entries) >= AUDIT_LIMIT:
        raise RuntimeError(f"token-call audit read hit the {AUDIT_LIMIT}-entry cap for {lo}..{hi}")
    found = []
    for e in entries:
        p = e.get("protoPayload") or {}
        ip = (p.get("requestMetadata") or {}).get("callerIp")
        if ip and not (p.get("status") or {}).get("code"):
            found.append((_epoch(e["timestamp"]), ip))
    return found


def token_near(calls: list[tuple[float, str]], ip: str, times: list[str], after: bool) -> bool:
    """A token call from this address within the hour before any of these reads, or, when `after` (a
    console-shaped read), up to 5 minutes after one."""
    return any(cip == ip and (0 <= _epoch(t) - ts <= TOKEN_S or (after and 0 <= ts - _epoch(t) <= TOKEN_AFTER_S))
               for ts, cip in calls for t in times)


def console_shaped(g: dict) -> bool:
    """What the console's data viewer sends: a desktop browser (the profiler's label, as the incident planner
    reads it, so the planner honours the verdict), allowed, listens and reads only, 0 or few bytes."""
    return desktop_browser(g["client"]) and g["bytes"] <= CONSOLE_BYTES and g["ops"] <= CONSOLE_OPS


def accounts_near(found: list[tuple[float, str]], t: str) -> list[str]:
    at = _epoch(t)
    return sorted({who for ts, who in found if abs(ts - at) <= MATCH_S})


def cymru(ip: str) -> tuple[str, str, str]:
    try:
        o = subprocess.run(["whois", "-h", "whois.cymru.com", f" -v {ip}"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20).stdout
        cols = [c.strip() for c in o.strip().splitlines()[-1].split("|")]
        owner = cols[6].removesuffix(f", {cols[3]}")
        return cols[0], cols[3], owner
    except (OSError, subprocess.SubprocessError, IndexError):
        return "?", "?", "?"


def network(ip: str) -> tuple[str, str, str, bool]:
    """ASN, country, owner and whether whois answered: whois, once more after a pause, then the
    ipgeolocation ASN. ("?", reason, "?", False) when both fail."""
    for attempt in range(2):
        if attempt:
            time.sleep(WHOIS_RETRY_S)
        asn, cc, owner = cymru(ip)
        if asn != "?":
            return asn, cc, owner, True
    geo = ip_location.lookup(ip)
    if geo.get("asn") and geo.get("country_code"):
        return geo["asn"], geo["country_code"], f"{geo.get('isp') or '?'} (whois down, from ipgeolocation)", False
    return "?", geo.get("reason") or "no asn", "?", False


def on_row(r: dict, asn: str, cc: str | None) -> bool:
    """Whether a row matches this network: a Confirmed row on the ASN alone (whois can place an address
    outside the country its network serves: a Nayatel PK address in SC), any other row on ASN plus
    country. cc None (whois was down): any country."""
    return r["asn"] == asn and (cc is None or r["status"] == "Confirmed" or r["cc"] == cc)


def confirmed_row(rows: list[dict], asn: str, cc: str | None) -> dict | None:
    """The network's Confirmed row, unless any of its rows, in any country, is Not a teammate."""
    if any(r["asn"] == asn and r["status"] == "Not a teammate" for r in rows):
        return None
    net = [r for r in rows if on_row(r, asn, cc)]
    return next((r for r in net if r["status"] == "Confirmed"), None)


def console_row(rows: list[dict], allow: set[str], asn: str, cc: str | None) -> dict | None:
    """The network's Confirmed or Likely row that lists an allow-listed console account, unless any of
    its rows, in any country, is Not a teammate. Matching as in `on_row`."""
    if any(r["asn"] == asn and r["status"] == "Not a teammate" for r in rows):
        return None
    net = [r for r in rows if on_row(r, asn, cc)]
    return next((r for r in net if r["status"] in ("Confirmed", "Likely") and r["accounts"] & allow), None)


def desktop_browser(client: str) -> bool:
    """The profiler's client label for a desktop browser, as monitor-rtdb-probes reads it."""
    return client.startswith("desktop/") and client.endswith(" browser")


def write_verdicts(path: str | None, attributed: set[str], console: set[str], teammate: set[str],
                   alert: set[str] = frozenset(), fail: set[str] = frozenset(),
                   rest_teammate: set[str] = frozenset(), fail_reason: str = "",
                   confirmed: set[str] = frozenset()) -> None:
    if not path:
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"attributed": sorted(attributed), "console": sorted(console),
                   "teammate": sorted(teammate), "alert": sorted(alert), "fail": sorted(fail),
                   "rest_teammate": sorted(rest_teammate), "confirmed": sorted(confirmed),
                   "fail_reason": fail_reason}, f)


def read_bursts(path: str | None) -> list[dict]:
    """evaluate_profile.py's REST-burst sources, our own sessions left out (they are never logged)."""
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]
    except (OSError, ValueError):
        return []
    return [b for b in lines if b.get("ip") and not str(b.get("ua", "")).startswith("AuraClaude/")]


def burst_teammates(bursts: list[dict], kn: list[dict], lookup=None) -> set[str]:
    """REST-burst sources on a Confirmed Known Team Networks row: a teammate, never logged to the Incident
    Log. Any other source, a failed lookup included, keeps its IP there."""
    lookup = lookup or network
    mates: set[str] = set()
    for ip in sorted({b["ip"] for b in bursts}):
        asn, cc, owner, whois_ok = lookup(ip)
        if asn == "?":
            continue
        mate = confirmed_row(kn, asn, cc if whois_ok else None)
        if mate:
            mates.add(ip)
            common.emit("networks", "note", f"networks-rest-burst-AS{asn}-{cc}",
                        f"REST burst source from AS{asn} {owner}, {cc}: Known Team Networks status Confirmed"
                        + (f" ({_local(mate['who'])})" if mate["who"] else "") + ", not logged as an incident")
    return mates


def read_audit(browser: list[str], window: list[str]) -> tuple:
    """(console accounts, their read error, token calls, their read error) for these read times."""
    try:
        found = console_accounts(min(browser), max(browser)) if browser else []
        lookup = None
    except (RuntimeError, ValueError, OSError) as e:
        found, lookup = [], str(e)
    try:
        tokens = token_calls(min(window), max(window)) if window else []
        token_err = None
    except (RuntimeError, ValueError, OSError) as e:
        tokens, token_err = [], str(e)
    return found, lookup, tokens, token_err


def late_candidate(ip: str, g: dict, found: list, tokens: list) -> bool:
    """A browser read that may be the console but has neither a token call nor a console account yet."""
    if g["ua"].startswith("AuraClaude/") or not (g["ua"].startswith("Mozilla/") or g["listens"]):
        return False
    shaped = console_shaped(g)
    times = g["listens"] + (g["times"] if shaped else [])
    if times and token_near(tokens, ip, times, after=shaped):
        return False
    return not (g["ua"].startswith("Mozilla/") and accounts_near(found, g["t"]))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", required=True)
    ap.add_argument("--verdicts")
    ap.add_argument("--rest-bursts")
    a = ap.parse_args()
    attributed: set[str] = set()
    console: set[str] = set()
    teammate: set[str] = set()
    alerted: set[str] = set()
    failed: set[str] = set()
    confirmed: set[str] = set()
    write_verdicts(a.verdicts, attributed, console, teammate)
    try:
        rows = [json.loads(l) for l in open(a.detail, encoding="utf-8") if l.strip()]
    except FileNotFoundError:
        rows = []
    allowed = [r for r in rows if str(r.get("verdict", "")).lower() == "allowed"]
    bursts = read_bursts(a.rest_bursts)
    if not allowed and not bursts:
        return
    try:
        kn, allow = known()
    except (RuntimeError, KeyError) as e:
        # The planner logs these reads with their addresses, each line tagged "unclassified: <reason>".
        reason = "Notion rate-limited" if isinstance(e, common.RateLimited) else "Known Team Networks unreadable"
        unread = {r["ip"] for r in allowed if not str(r.get("ua", "")).startswith("AuraClaude/")}
        common.emit("networks", "fail", "networks-notion",
                    f"Known Team Networks read failed ({reason}): {len(allowed)} allowed read(s) and "
                    f"{len(bursts)} REST-burst source(s) not classified, logged as unclassified: {e}")
        write_verdicts(a.verdicts, attributed, console, teammate, fail=unread, fail_reason=reason)
        return
    rest_mates = burst_teammates(bursts, kn)
    if not allowed:
        write_verdicts(a.verdicts, attributed, console, teammate, rest_teammate=rest_mates)
        return
    browser = [r["t"] for r in allowed if str(r.get("ua", "")).startswith("Mozilla/")]
    listens = [r["t"] for r in allowed if r.get("op") == "listener-listen" and r.get("tag") == "rule exists"]
    # A browser's read on any node may be the console too, its token fetched just before or after it.
    window = listens + [r["t"] for r in allowed if desktop_browser(str(r.get("client", "")))]
    found, lookup, tokens, token_err = read_audit(browser, window)
    groups: dict = {}
    for r in allowed:
        g = groups.setdefault(r["ip"], {"t": r["t"], "paths": set(), "ops": set(), "bytes": 0, "listens": [],
                                       "times": [], "client": r.get("client", "?"), "ua": r.get("ua", "")})
        g["times"].append(r["t"])
        g["paths"].add(r.get("node", "?"))
        g["ops"].add(r.get("op", "?"))
        g["bytes"] += int(r.get("bytes") or 0)
        if r.get("op") == "listener-listen" and r.get("tag") == "rule exists":
            g["listens"].append(r["t"])
    if any(late_candidate(ip, g, found, tokens) for ip, g in groups.items()) \
            and time.time() - max(_epoch(t) for t in browser + window) < TOKEN_AFTER_S:
        time.sleep(TOKEN_RETRY_S)
        again = read_audit(browser, window)
        # A failed second read keeps the first one's answer.
        if again[1] is None:
            found, lookup = again[0], None
        if again[3] is None:
            tokens, token_err = again[2], None
    for ip, g in groups.items():
        paths = ",".join(sorted(g["paths"]))
        ops = "+".join(sorted(g["ops"]))
        if g["ua"].startswith("AuraClaude/"):
            common.emit("networks", "note", f"networks-internal-{paths}",
                        f"{g['t'][11:16]}Z allowed read of {paths} by our own session ({_no_who(g['ua'].split(' ')[0])})")
            continue
        asn, cc, owner, whois_ok = network(ip)
        if asn == "?":
            common.emit("networks", "fail", f"networks-whois-{paths}",
                        f"{g['t'][11:16]}Z allowed read of {paths}: network lookup failed (whois twice, "
                        f"ipgeolocation {cc}), not classified")
            failed.add(ip)
            continue
        # A staff address: if its read is still logged, the Incident Log shows "see Known Team Networks".
        if confirmed_row(kn, asn, cc if whois_ok else None):
            confirmed.add(ip)
        key = f"networks-AS{asn}-{cc}-{paths}"
        barred = any(r["asn"] == asn and r["status"] == "Not a teammate" for r in kn)
        shaped = console_shaped(g)
        times = g["listens"] + (g["times"] if shaped else [])
        if times and not barred and token_near(tokens, ip, times, after=shaped):
            console.add(ip)
            common.emit("networks", "note", key,
                        f"{g['t'][11:16]}Z allowed {ops} of {paths} from AS{asn} {owner}, {cc}: console data viewer "
                        f"(token fetch from the same address within the hour before, or 5 minutes after, the read)")
            continue
        if not g["ua"].startswith("Mozilla/"):
            # A network can have one row per place: Not a teammate on any of them wins, then a
            # Confirmed / Likely row, then whatever else is there.
            # Without whois the country is the lookup's, and a row may sit under the whois one.
            net = [r for r in kn if on_row(r, asn, cc if whois_ok else None)]
            row = next((r for r in kn if r["asn"] == asn and r["status"] == "Not a teammate"), None) \
                or next((r for r in net if r["status"] in ("Confirmed", "Likely")), None) \
                or (net[0] if net else None)
            status = row["status"] if row else "Unknown"
            text = (f"{g['t'][11:16]}Z allowed {ops} of {paths} ({g['bytes']} bytes) from AS{asn} {owner}, {cc}, "
                    f"client {g['client']}: Known Team Networks status {status}"
                    + (f" ({_local(row['who'])})" if row and row["who"] else ""))
            if g["bytes"] == 0 and confirmed_row(kn, asn, cc if whois_ok else None):
                teammate.add(ip)
            level = "note" if status in ("Confirmed", "Likely") else "alert"
            if level == "alert":
                alerted.add(ip)
            common.emit("networks", level, key, text)
            continue
        names = accounts_near(found, g["t"])
        if names:
            console.add(ip)
        account = names[0] if len(names) == 1 else None
        # A lookup is a credit: only an allow-listed read can lead to a write.
        geo = ip_location.lookup(ip) if account and account in allow else {}
        level, action, row = classify(kn, allow, asn, cc if whois_ok else None, account, geo)
        who = (", ".join(n.split("@")[0] + "@" for n in names) if names
               else f"account not found ({lookup})" if lookup else "no console account in the audit log")
        if action == "touch":
            text = f"{g['t'][11:16]}Z {ops} of {paths}: known: {account.split('@')[0]}@, {row['city'] or geo.get('city') or cc}"
        else:
            text = (f"{g['t'][11:16]}Z allowed {ops} of {paths} ({g['bytes']} bytes) from AS{asn} {owner}, {cc}, "
                    f"client {g['client']}: Known Team Networks status {row['status'] if row else 'Unknown'}"
                    + (f" ({_local(row['who'])})" if row and row["who"] else "") + f"; console read by {who}")
            if action == "add":
                text += f"; added {account.split('@')[0]}@ to the {row['network']} row"
            elif action == "create":
                text += f"; new Known Team Networks row (Likely) for {account.split('@')[0]}@"
            elif row and row["status"] == "Not a teammate":
                text += "; this network is marked Not a teammate"
            elif account:
                text += f"; {account.split('@')[0]}@ is not on the console-account allow-list"
        # No console account (and the audit log did answer), nothing returned, a Confirmed network:
        # a teammate testing the app, not the console.
        mate = confirmed_row(kn, asn, cc if whois_ok else None) \
            if level == "alert" and not names and not lookup and g["bytes"] == 0 else None
        if mate:
            level = "note"
            teammate.add(ip)
            text = (f"{g['t'][11:16]}Z allowed {ops} of {paths} (0 bytes) from AS{asn} {owner}, {cc}, "
                    f"client {g['client']}: teammate read, Known Team Networks status Confirmed"
                    + (f" ({_local(mate['who'])})" if mate["who"] else "") + ", no console account")
        # An open console tab reconnecting: no new audit-log call, so no account to name. Only a 0-byte
        # root listen, nothing else, from a desktop browser on a row that already lists a console account.
        tab = console_row(kn, allow, asn, cc if whois_ok else None) \
            if (level == "alert" and not names and not lookup and g["bytes"] == 0
                and g["ops"] == {"listener-listen"} and g["paths"] == {"/"} and desktop_browser(g["client"])) \
            else None
        if tab:
            level = "note"
            console.add(ip)
            text = (f"{g['t'][11:16]}Z allowed {ops} of {paths} (0 bytes) from AS{asn} {owner}, {cc}, "
                    f"client {g['client']}: probable console reconnect, Known Team Networks status {tab['status']}"
                    + (f" ({_local(tab['who'])})" if tab["who"] else "")
                    + ", console account on the row: " + ", ".join(sorted(a.split("@")[0] + "@" for a in tab["accounts"] & allow))
                    + "; no console account in the audit log")
        if action:
            try:
                record(action, row, account, asn, cc, geo, g["t"], kn)
            except RuntimeError as e:
                text += f"; Known Team Networks write failed ({e})"
            if (row["status"] if row else "Likely") in ("Confirmed", "Likely"):
                attributed.add(ip)
        if level == "alert" and (g["listens"] or console_shaped(g)) and token_err:
            text += f"; token-call lookup failed ({token_err})"
        if level == "alert":
            alerted.add(ip)
        common.emit("networks", level, key, text)
    write_verdicts(a.verdicts, attributed, console, teammate, alerted, failed, rest_mates, confirmed=confirmed)


if __name__ == "__main__":
    main()
