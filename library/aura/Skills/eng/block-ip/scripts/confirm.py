#!/usr/bin/env python3
"""After a block: confirm the edge denies at the new rule, and note it on the Incident Log row.

    confirm.py --priority 100 --since 2026-10-30T20:00:00Z            # deny count only
    confirm.py --priority 100 --since … --incident <row URL> --note   # also append to the row's Notes

Counts `enforcedSecurityPolicy` DENY at that priority in the LB logs since the rule was created.
Exit 0 when at least one deny is logged, 10 when none yet (the range may simply not have come back:
re-run later, never treat it as a failure of the rule), 1 on a read or write failure. The note names
the rule, its expiry and how many Blocklist rows it has; never an address.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import armor_blocklist as ab  # noqa: E402
import gcloud_sa  # noqa: E402


def denies(priority, since, run=gcloud_sa.run, limit=1000):
    flt = (f'resource.type="http_load_balancer" timestamp>="{since}" '
           f'jsonPayload.enforcedSecurityPolicy.name="{ab.POLICY}" '
           f'jsonPayload.enforcedSecurityPolicy.priority={priority} '
           'jsonPayload.enforcedSecurityPolicy.outcome="DENY"')
    out = run(["logging", "read", flt, "--project", ab.PROJECT, "--limit", str(limit),
               "--format", "json(timestamp)"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode:
        raise RuntimeError(f"logging read failed as {gcloud_sa.credential()}: {(out.stderr or '').strip()[-200:]}")
    return len(json.loads(out.stdout or "[]"))


def note_text(priority, expires, rows, blocklist_url, today=None):
    today = today or ab.utc_today().isoformat()
    return (f"{today}: blocked at the edge by {ab.POLICY} rule {priority}, expires {expires or '?'}, "
            f"{rows} Blocklist row(s): {blocklist_url}")


def append_note(incident, text, call=ab.notion):
    page = call("GET", f"/pages/{incident}")
    notes = (page.get("properties", {}).get("Notes") or {}).get("rich_text") or []
    runs = [{"type": "text", "text": {"content": r.get("plain_text", ""), "link": (r.get("text") or {}).get("link")},
             "annotations": r.get("annotations")} for r in notes]
    for r in runs:
        if r["text"]["link"] is None:
            del r["text"]["link"]
        if r["annotations"] is None:
            del r["annotations"]
    runs.append({"type": "text", "text": {"content": ("\n" if runs else "") + text[:1990]}})
    if len(runs) > 100:  # Notion's cap per property: refuse rather than drop older notes
        raise RuntimeError("the row's Notes already has 100 text runs; add the note by hand")
    call("PATCH", f"/pages/{incident}", {"properties": {"Notes": {"rich_text": runs}}})


def main(argv=None, run=gcloud_sa.run, call=ab.notion):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--priority", type=int, required=True)
    p.add_argument("--since", required=True, help="when the rule was created, ISO UTC")
    p.add_argument("--incident", help="Security Incident Log row URL (with --note)")
    p.add_argument("--note", action="store_true", help="append the block to the row's Notes")
    a = p.parse_args(argv)
    if not ab.FIRST <= a.priority <= ab.LAST:
        print(f"ERROR: incident blocks use priorities {ab.FIRST}-{ab.LAST}", file=sys.stderr)
        return 1
    try:
        n = denies(a.priority, a.since, run)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"rule {a.priority}: {n} enforced deny(s) logged since {a.since}"
          + ("" if n else " (none yet: the range may not have come back; re-run later)"))
    if a.note:
        if not a.incident:
            print("ERROR: --note needs --incident", file=sys.stderr)
            return 1
        try:
            rule = next((r for r in ab.incident_rules() if r["priority"] == a.priority), None)
            rows = [r for r in ab.blocklist_rows() or [] if r["priority"] == a.priority and r["status"] == "Active"]
            url = (f"https://app.notion.com/p/{rows[0]['id'].replace('-', '')}" if rows
                   else f"https://app.notion.com/p/{ab.BLOCKLIST_DS.replace('-', '')}")
            append_note(ab.page_id(a.incident), note_text(a.priority, rule and rule["expires"], len(rows), url), call)
        except (RuntimeError, SystemExit) as e:
            print(f"ERROR: the note was not written: {e}", file=sys.stderr)
            return 1
        print("noted on the Incident Log row")
    return 0 if n else 10


if __name__ == "__main__":
    sys.exit(main())
