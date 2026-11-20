#!/usr/bin/env python3
"""Weekly review of the Cloud Armor incident blocks (aura-web-edge rules 100-149).

    review.py [--days 7] [--extend-days 30] [--post]          # review, propose; changes nothing
    review.py --expire 101,104 --apply                        # remove these expired rules (on the go)
    review.py --extend 102 --expires 2026-12-15 --apply       # push one rule's expiry (on the go)

Cross-checks the live rules against the Notion Cloud Armor Blocklist and flags Active rows with no
rule, rules with no Active row, and rules with no expiry. For each expired rule it counts the last
`--days` of traffic from its ranges: still attacking → propose an extension, quiet → propose
expiring it. Counts only, never an address. `--post` sends the summary to #alerts-security.
Exit 0 when nothing needs a decision, 10 when something does, 1 on a read or write failure.
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

CHANNEL = "C0C72QYDJQK"  # #alerts-security


def hits(rule, since, run=gcloud_sa.run, limit=5000):
    """{requests, edge_denied, 4xx} from the rule's ranges since `since`."""
    nets = " OR ".join(f'ip_in_net(httpRequest.remoteIp, "{n}")' for n in rule["ranges"])
    flt = f'resource.type="http_load_balancer" timestamp>="{since}" ({nets})'
    out = run(["logging", "read", flt, "--project", ab.PROJECT, "--limit", str(limit),
               "--format", "json(httpRequest.status,jsonPayload.statusDetails)"], capture_output=True, text=True,
              encoding="utf-8", errors="replace")
    if out.returncode:
        raise RuntimeError(f"logging read failed as {gcloud_sa.credential()}: {(out.stderr or '').strip()[-200:]}")
    es = json.loads(out.stdout or "[]")
    return {"requests": len(es), "capped": len(es) >= limit,
            "edge_denied": sum(1 for e in es if (e.get("jsonPayload") or {}).get("statusDetails")
                               == "denied_by_security_policy"),
            "4xx": sum(1 for e in es if 400 <= int((e.get("httpRequest") or {}).get("status") or 0) < 500)}


def review(live, rows, today, since, extend_days, count=hits):
    """The findings, as a dict. `rows` is ab.blocklist_rows() (None: the join was skipped)."""
    active = [r for r in rows or [] if r["status"] == "Active" and (r.get("policy") in (None, ab.POLICY))]
    live_p = {r["priority"] for r in live}
    row_p = {r["priority"] for r in active}
    out = {"rules": len(live), "active_rows": len(active), "joined": rows is not None,
           "rows_without_rule": sorted({r["priority"] for r in active if r["priority"] not in live_p},
                                       key=lambda p: (p is None, p or 0)),
           "rules_without_row": sorted(live_p - row_p) if rows is not None else [],
           "no_expiry": sorted(r["priority"] for r in live if not r["expires"]),
           "expired": []}
    new_expiry = (today + dt.timedelta(days=extend_days)).isoformat()
    for r in sorted(live, key=lambda r: r["priority"]):
        if r["expires"] and r["expires"] < today.isoformat():
            try:
                h = count(r, since)
            except RuntimeError as e:
                out["expired"].append({"priority": r["priority"], "expires": r["expires"], "error": str(e),
                                       "proposal": "decide by hand: traffic not read"})
                continue
            attacking = h["requests"] > 0
            out["expired"].append({"priority": r["priority"], "expires": r["expires"], "ranges": len(r["ranges"]),
                                   **h, "proposal": f"extend to {new_expiry}" if attacking else "expire"})
    out["needs_decision"] = bool(out["rows_without_rule"] or out["rules_without_row"]
                                 or out["no_expiry"] or out["expired"])
    return out


def summary(f, days):
    lines = [f"*Cloud Armor incident blocks*: {f['rules']} live rule(s), {f['active_rows']} Active Blocklist row(s)"]
    if not f["joined"]:
        lines.append("Blocklist not read (NOTION_TOKEN unset): row checks skipped.")
    if f["rows_without_rule"]:
        lines.append(f"Active rows with no rule: rule(s) {', '.join(map(str, f['rows_without_rule']))}")
    if f["rules_without_row"]:
        lines.append(f"Rules with no Active row: {', '.join(map(str, f['rules_without_row']))}")
    if f["no_expiry"]:
        lines.append(f"Rules with no expiry: {', '.join(map(str, f['no_expiry']))}")
    for e in f["expired"]:
        what = (f"traffic not read ({e['error']})" if "error" in e else
                f"{e['requests']}{'+' if e.get('capped') else ''} requests in {days}d, "
                f"{e['edge_denied']} denied at the edge")
        lines.append(f"Rule {e['priority']} expired {e['expires']}: {what}. Proposal: {e['proposal']}.")
    if not f["needs_decision"]:
        lines.append("Nothing to decide.")
    return "\n".join(lines)


def extend(priority, expires, apply):
    """Push one rule's expiry: its description and its Active Blocklist rows."""
    live = {r["priority"]: r for r in ab.incident_rules()}
    rule = live.get(priority)
    if not rule or not rule["row"]:
        raise ab.Refused(f"no incident rule {priority} with an Incident Log id in its description")
    when = dt.date.fromisoformat(expires)
    today = ab.utc_today()
    if not today < when <= today + dt.timedelta(days=ab.MAX_DAYS):
        raise ab.Refused(f"--expires must be within the next {ab.MAX_DAYS} days")
    desc = f"incident exp={when.isoformat()} {rule['row']}"
    print(f"rule {priority}: expiry {rule['expires']} -> {when.isoformat()}")
    if not apply:
        print("dry run; pass --apply to change it")
        return
    ab.gcloud("rules", "update", str(priority), "--security-policy", ab.POLICY, "--description", desc)
    # The Change Log right after the rule changes, so a Blocklist error below can't cost the audit row.
    ab.change_log("extended", priority, rule["row"], f"expiry moved to {when.isoformat()}", expires=when.isoformat())
    n = ab.mark_rows(priority, {"Expires (UTC)": {"date": {"start": when.isoformat()}}})
    print(f"rule {priority} updated; {n} Blocklist row(s) updated")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--days", type=int, default=7, help="traffic window for expired rules (default 7)")
    p.add_argument("--extend-days", type=int, default=30, help="proposed extension (default 30)")
    p.add_argument("--post", action="store_true", help="post the summary to #alerts-security")
    p.add_argument("--expire", help="comma-separated expired rule priorities to remove")
    p.add_argument("--extend", type=int, help="rule priority whose expiry to push")
    p.add_argument("--expires", help="new expiry for --extend, YYYY-MM-DD")
    p.add_argument("--apply", action="store_true")
    a = p.parse_args(argv)
    try:
        if a.extend is not None:
            if not a.expires:
                p.error("--extend needs --expires")
            extend(a.extend, a.expires, a.apply)
            return 0
        if a.expire:
            today = ab.utc_today().isoformat()
            live = {r["priority"]: r for r in ab.incident_rules()}
            for prio in (int(x) for x in a.expire.split(",") if x.strip()):
                r = live.get(prio)
                if not r or not r["expires"] or r["expires"] >= today:
                    raise ab.Refused(f"rule {prio} is not an expired incident block")
                ab.remove(prio, "Expired", a.apply, r["row"], r["expires"])
            if not a.apply:
                print("dry run; pass --apply to remove them")
            return 0
        today = dt.datetime.now(dt.timezone.utc)
        since = (today - dt.timedelta(days=a.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        f = review(ab.incident_rules(), ab.blocklist_rows(), today.date(), since, a.extend_days)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    text = summary(f, a.days)
    print(text)
    if a.post:
        try:
            import slack_post
            r = slack_post.post_message(CHANNEL, text, footer_skill="eng:review-ip-blocks")
        except (Exception, SystemExit) as e:  # the helper exits on a missing token
            r = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if not r.get("ok"):
            print(f"ERROR: Slack post failed: {r.get('error')}", file=sys.stderr)
            return 1
        print("posted to #alerts-security")
    return 10 if f["needs_decision"] else 0


if __name__ == "__main__":
    sys.exit(main())
