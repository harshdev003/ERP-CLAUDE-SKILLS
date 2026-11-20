"""Which flagged IPs are on a Confirmed Known Team Networks row: those clients are staff.

The IP decides, never the user agent: AuraClaude/... and AuraCLI/... are labels anyone can send.
A network is ours when its ASN (Team Cymru whois) has a Confirmed row and no row, in any country,
says "Not a teammate" (the rule monitor-firebase's check_networks.py applies). An IP whose lookup
fails is not staff: it stays an outside client, and the failure is counted in the plan.

Token: NOTION_TOKEN (read-only query of the Known Team Networks database).
"""

from __future__ import annotations

import subprocess

KNOWN_DB = "40475e23-af20-8102-b20f-d4339a365f14"  # Known Team Networks (Security)
CONFIRMED, NOT_TEAMMATE = "Confirmed", "Not a teammate"


def _text(prop):
    return "".join(x.get("plain_text", "") for x in (prop or {}).get("title", (prop or {}).get("rich_text", [])))


def confirmed_asns(call) -> set[str]:
    """ASNs (digits only) with a Confirmed row and no Not a teammate row."""
    rows, body = [], {"page_size": 100}
    while True:
        d = call("POST", f"/databases/{KNOWN_DB}/query", body)
        rows += d.get("results", [])
        if not d.get("has_more"):
            break
        body = {**body, "start_cursor": d["next_cursor"]}
    confirmed, denied = set(), set()
    for r in rows:
        p = r.get("properties", {})
        net = _text(p.get("Network"))
        if not net.startswith("AS"):
            continue
        asn = net.split()[0].removeprefix("AS")
        status = ((p.get("Status") or {}).get("select") or {}).get("name")
        (confirmed if status == CONFIRMED else denied if status == NOT_TEAMMATE else set()).add(asn)
    return confirmed - denied


def cymru_asn(ip: str) -> str | None:
    """The IP's ASN from Team Cymru whois, or None when the lookup fails."""
    try:
        out = subprocess.run(["whois", "-h", "whois.cymru.com", f" -v {ip}"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=20).stdout
        asn = out.strip().splitlines()[-1].split("|")[0].strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        return None
    return asn if asn.isdigit() else None


def staff_ips(ips, call, asn_of=cymru_asn) -> tuple[set[str], dict]:
    """(the IPs on a Confirmed team network, counts). Any failure leaves an IP outside, counted."""
    stats = {"checked": 0, "staff": 0, "lookup_failed": 0, "error": None}
    try:
        ours = confirmed_asns(call)
    except Exception as e:  # noqa: BLE001 — no list: nobody is staff, and the plan says why
        stats["error"] = f"Known Team Networks unreadable: {type(e).__name__}"
        return set(), stats
    staff = set()
    for ip in sorted(set(ips)):
        stats["checked"] += 1
        asn = asn_of(ip)
        if asn is None:
            stats["lookup_failed"] += 1
        elif asn in ours:
            staff.add(ip)
    stats["staff"] = len(staff)
    return staff, stats
