#!/usr/bin/env python3
"""
Turn a detect.py plan into Security Incident Log group rows and grouped Slack messages.

Every flagged campaign gets a severity (Info, Low, Medium, High) and a Group key
(`Web|<attack-type>|<outcome>`). Campaigns with the same key update the group's one open
row: IPs and user agents are unioned with per-IP counts, requests summed by minute,
severity only raised. Slack follows the same groups: one message per group, its later counts as
threaded replies under it (a posted message is never edited or deleted: counsel, 2026-11-06), a new
post only for a new group or a higher severity, and nothing below --slack-min-severity (default
medium). Rules: references/rules.md. Without --apply it
prints what it would write and post, and changes nothing.

Cloud Armor alerts in the plan (an enforced IP ban or a saturated throttle, per rule) post one
Medium message per rule and UTC day; they have no Incident Log row.

Tokens: NOTION_TOKEN (Notion REST) and SLACK_BOT_TOKEN (chat.postMessage only).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import attack_rules as rules  # noqa: E402
import incident_log as log  # noqa: E402
from incident_log import INCIDENT_DB, notion, parse_ts, plain  # noqa: E402

DEFAULT_CHANNEL = "C0C72QYDJQK"
GAP_HOURS = 3.0
HISTORY_DAYS = 14
VOLUME_JUMP = 10
HOST_SYSTEM = {"www.aurahealth.io": "UserWeb", "aurahealth.io": "UserWeb", "coach.aurahealth.io": "CoachWeb",
               "crm.aurahealth.io": "CRMWeb", "ai.aurahealth.io": "AIWeb"}
TITLE = {"Vulnerability scanner": "Vulnerability scanners", rules.BRUTE_FORCE: rules.BRUTE_FORCE,
         "Scraping / abuse": "Scraping", "Injection / fuzzing": "Injection / fuzzing",
         "Forced browsing": "Forced browsing", "Flood / DoS": "Flood", "Other attack": "Other attacks"}
# The live Category option names, read by main()'s schema check; None writes the current names.
CATEGORY_OPTIONS = None
NOTES = {"refused": "All refused (3xx/4xx) or public pages only; nothing obtained.",
         "obtained": "A non-public route answered 2xx: check what was served.",
         "data-accessed": "A sensitive path answered 2xx with a body: check what was served."}


def system(hosts):
    named = {HOST_SYSTEM.get(h) for h in hosts}
    return named.pop() if len(named) == 1 and None not in named else "Other"


def save_state(path, state):
    """Written after every row and post, so a failure later in the run never orphans one."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# ---- novelty --------------------------------------------------------------------------

def novelty_keys(f):
    return [f"cat:{f['kind']}"] + [f"fam:{x}" for x in sorted(rules.families_of(f))]


def seed_history(old_state):
    """A fresh history starts from the per-campaign rows the 1.1 writer kept in
    state.json, so the first run does not call every known family new."""
    seen = {}
    for rec in (old_state or {}).values():
        if not isinstance(rec, dict) or not rec.get("last") or not rec.get("kind"):
            continue
        for k in novelty_keys(rec):
            seen[k] = max(seen.get(k, ""), rec["last"])
    return {"seen": seen, "slack": {}}


def new_types(f, history, days=HISTORY_DAYS):
    out = []
    first = parse_ts(f["first"])
    for k in novelty_keys(f):
        last = history.get("seen", {}).get(k)
        if not last or (first - parse_ts(last)).total_seconds() > days * 86400:
            what, name = k.split(":", 1)
            out.append(f"{'category' if what == 'cat' else 'path family'} {name}")
    return out


def mark_seen(history, f):
    seen = history.setdefault("seen", {})
    for k in novelty_keys(f):
        seen[k] = max(seen.get(k, ""), f["last"])


# ---- one group's slice of this run ----------------------------------------------------

def new_counts(minutes_by_key, cut_by_key, default_cut):
    """Requests per key in minutes after the last minute already counted for that key.
    Hourly windows overlap by a few minutes; counting by minute never counts one twice."""
    new, last = {}, {}
    for key, minutes in minutes_by_key.items():
        cut = cut_by_key.get(key, default_cut)
        new[key] = sum(c for m, c in minutes.items() if m > cut)
        last[key] = max([cut] + list(minutes))
    return new, last


def add_minutes(acc, maps):
    for m in maps:
        for key, minutes in m.items():
            dst = acc.setdefault(key, {})
            for minute, c in minutes.items():
                dst[minute] = dst.get(minute, 0) + c
    return acc


def group_slice(fs, gstate, default_cut):
    """What this run adds to a group, by minute against the cuts in gstate: requests per IP
    and UA, families and statuses. A retried plan or the overlap of the next window adds
    nothing a second time, to any of them."""
    ip_last = {}
    for f in fs:
        for ip, t in f.get("ip_last", {}).items():
            ip_last[ip] = max(ip_last.get(ip, ""), t)
    new, cut = {}, {}
    for name, field in (("ip", "ip_minutes"), ("ua", "ua_minutes"), ("fam", "family_minutes"),
                        ("status", "status_minutes")):
        minutes = add_minutes({}, (f.get(field, {}) for f in fs))
        new[name], cut[name] = new_counts(minutes, gstate.get(f"{name}_minute", {}), default_cut)
    ip_cuts = gstate.get("ip_minute", {})
    fresh = []
    for f in fs:
        n = sum(c for ip, mins in f.get("ip_minutes", {}).items() for m, c in mins.items()
                if m > ip_cuts.get(ip, default_cut))
        if n:
            fresh.append(dict(f, requests=n))
    return {
        "requests": sum(new["ip"].values()),
        "ips": {ip: {"n": n, "last": (ip_last[ip][:16] + "Z") if ip_last.get(ip) else None}
                for ip, n in new["ip"].items()},
        "uas": dict(new["ua"]),
        "cuts": cut,
        "fresh": fresh,
        "first": min(f["first"] for f in fs), "last": max(f["last"] for f in fs),
        "families": {k: v for k, v in new["fam"].items() if v},
        "statuses": {k: v for k, v in new["status"].items() if v},
        "hosts": sorted({h for f in fs for h in f["hosts"]}),
    }


def merged_cuts(old, sl):
    """The state's cuts after this run: {"ip_minute": …, "ua_minute": …, …}."""
    return {f"{k}_minute": {**(old or {}).get(f"{k}_minute", {}), **v} for k, v in sl["cuts"].items()}


def what_line(f):
    ips = f.get("ips", [f["ip"]])
    fams = sorted(((k, v) for k, v in (f.get("families") or {}).items() if k != rules.OTHER),
                  key=lambda x: -x[1])
    return (f"{f['first'][:10]} {f['first'][11:16]}-{f['last'][11:16]}Z {', '.join(ips[:3])}"
            f"{'…' if len(ips) > 3 else ''}: {f['requests']} req, "
            + (", ".join(k for k, _v in fams[:3]) or "other paths"))


def routes_line(f):
    return "; ".join(f"{c}x {route} -> {s}" for route, s, c in f["top_routes"][:8])


def props_for(gk, fs, sl, sev, reasons, ips, uas, child, old=None):
    """Row properties for a create (old is None) or an update. Returns (props, overflowed)."""
    f0 = fs[0]
    out_tok = gk.split("|")[2]
    ip_prop, ip_over = log.ban_property(log.format_ips(ips), "IP addresses", child or "pending")
    ua_prop, ua_over = log.ban_property(log.format_uas(uas), "user agents", child or "pending")
    lines = "\n".join(what_line(f) for f in sl["fresh"])
    routes = "\n".join(routes_line(f) for f in sl["fresh"])
    if old is None:
        props = {
            "Incident": {"title": [{"text": {"content": f"{TITLE.get(f0['kind'], f0['kind'])} ({out_tok})"}}]},
            "Group key": log.rich(gk),
            "Severity": {"select": {"name": sev}},
            "Detected (UTC)": {"date": {"start": sl["first"]}},
            "Ended (UTC)": {"date": {"start": sl["last"]}},
            "Category": {"select": {"name": log.category_name(f0["kind"], CATEGORY_OPTIONS)}},
            "System": {"select": {"name": system(sl["hosts"])}},
            "Outcome": {"select": {"name": rules.OUTCOME_COLUMN[out_tok]}},
            "Status": {"select": {"name": "Monitoring" if out_tok == "refused" else "Open"}},
            "Requests": {"number": sl["requests"]},
            "Actor": log.rich("Unknown"),
            "Found by": log.rich(log.FOUND_BY),
            "What they did": log.rich(log.tail_lines(lines)),
            "Route / path": log.rich(log.tail_lines(routes, 1)),
            "Notes": log.rich(NOTES[out_tok] + (" " + "; ".join(reasons) if sev in ("Medium", "High") else "")),
        }
    else:
        pr = old["properties"]
        old_sev = (pr.get("Severity", {}).get("select") or {}).get("name")
        new_sev = sev if rules.rank(sev) > rules.rank(old_sev) else old_sev
        note = plain(pr.get("Notes"))
        if new_sev != old_sev:
            note = (note + f"\n{sl['last'][:10]}: severity raised to {sev} ({'; '.join(reasons)}).").strip()
        props = {
            "Severity": {"select": {"name": new_sev}},
            "Ended (UTC)": {"date": {"start": max(sl["last"], log.ended(old) or "")}},
            "Requests": {"number": (pr.get("Requests", {}).get("number") or 0) + sl["requests"]},
            "What they did": log.rich(log.tail_lines((plain(pr.get("What they did")) + "\n" + lines).strip())),
            "Route / path": log.rich(log.tail_lines((plain(pr.get("Route / path")) + "\n" + routes).strip(), 1)),
            "Notes": log.rich(log.tail_lines(note, 1)),
        }
    props["Max IPs"] = {"number": len(ips)}
    props["IP addresses"] = ip_prop
    props["User agent"] = ua_prop
    # Busiest /24s first; the full IP list stays in IP addresses and the overflow page.
    props["Source network"] = log.rich(log.head_lines(log.networks(ips)))
    return props, ip_over or ua_over


def write_group(gk, fs, sl, sev, reasons, row, call=notion, on_created=None):
    """Create or update the group's row. Returns the page id. Raises on a Notion error.

    A new row too big for its columns is created empty first (no requests, no ban data)
    and on_created(page_id) records it, so a failure before the full write leaves a row
    the retry fills completely instead of one it thinks is already counted."""
    if row is None:
        ips, uas, old_child = sl["ips"], sl["uas"], None
        props, over = props_for(gk, fs, sl, sev, reasons, ips, uas, None)
        if not over:
            return call("POST", "/pages", {"parent": {"database_id": INCIDENT_DB}, "properties": props})["id"]
        empty = dict(props, **{"Requests": {"number": 0}, "Max IPs": {"number": 0},
                               "IP addresses": {"rich_text": []}, "User agent": {"rich_text": []},
                               "What they did": {"rich_text": []}, "Route / path": {"rich_text": []}})
        page_id = call("POST", "/pages", {"parent": {"database_id": INCIDENT_DB}, "properties": empty})["id"]
        if on_created:
            on_created(page_id)
    else:
        page_id = row["id"]
        old_child, ips, uas = log.row_ban_data(row, call)
        ips, uas = log.merge_ban(ips, uas, sl["ips"], sl["uas"])
        props, over = props_for(gk, fs, sl, sev, reasons, ips, uas, old_child, old=row)
    child = old_child
    if over:
        # Too long for the columns: the full union goes to a child page the columns link.
        child = log.write_overflow(page_id, ips, uas, call)
        props, _ = props_for(gk, fs, sl, sev, reasons, ips, uas, child, old=row)
    try:
        call("PATCH", f"/pages/{page_id}", {"properties": props})
    except Exception:
        if child and child != old_child:  # the row still points at its previous page
            try:
                call("PATCH", f"/pages/{child}", {"archived": True})
            except Exception:
                pass
        raise
    if old_child and old_child != child:
        # The row already points at the new page; a stale old page is clutter, not a
        # reason to fail the write and count this run again.
        try:
            call("PATCH", f"/pages/{old_child}", {"archived": True})
        except Exception as e:
            print(f"could not archive old ban-data page {old_child}: {e}", file=sys.stderr)
    return page_id


# ---- Slack ----------------------------------------------------------------------------

def label(sev):
    return f"[{sev.upper()}]"


def mention_text(value):
    if not value:
        return ""
    return value if value.startswith("<") else f"<@{value}>"


def slack_text(gk, g, row_url, mention="", escalated_from=None):
    sev = g["severity"]
    fams = sorted(g["families"].items(), key=lambda x: -x[1])
    nets = {log.net24(ip) for ip in g["ips"]}
    top = sorted(g["ips"].items(), key=lambda kv: -kv[1])[:5]
    lines = [
        f"{label(sev)} *{g['title']}* `{gk}`",
        f"{g['first'][:16].replace('T', ' ')}Z → {g['last'][:16].replace('T', ' ')}Z · {g['requests']:,} requests · "
        f"{len(g['ips'])} IP(s) in {len(nets)} /24(s)",
        "Families: " + (", ".join(f"{k} {v:,}" for k, v in fams[:6]) or "none"),
        "Statuses: " + ", ".join(f"{k}:{v:,}" for k, v in sorted(g["statuses"].items())),
        "Top IPs: " + ", ".join(f"`{ip}` ({n:,})" for ip, n in top),
    ]
    if sev != "Info":
        lines.append("Why: " + "; ".join(g["reasons"]))
    if escalated_from:
        lines.append(f"Escalated from {escalated_from}.")
    if row_url:
        lines.append(f"<{row_url}|Security Incident Log row>")
    if mention and sev in ("Medium", "High"):
        lines.append(mention)
    return "\n".join(lines)


class Slack:
    """The two calls this writer makes, behind one seam the tests replace. There is no edit and no
    delete: a message, once posted, is never changed (counsel, 2026-11-06)."""

    def __init__(self, channel):
        self.channel = channel

    @staticmethod
    def _mod():
        import slack_post
        return slack_post

    def post(self, text, thread_ts=None):
        try:
            return self._mod().post_message(self.channel, text, thread_ts=thread_ts,
                                            footer_skill=None if thread_ts else "eng:detect-attacks")
        except SystemExit as e:  # the helper exits on a missing token or a non-JSON reply
            return {"ok": False, "error": f"slack_post exited ({e.code})"}

    def permalink(self, ts):
        try:
            r = self._mod().get_permalink(self.channel, ts)
            return r.get("permalink") if r.get("ok") else None
        except SystemExit:
            return None


def group_open(st, first, gap_hours):
    """A Slack group ends after gap_hours with no hits, and at the UTC day boundary, so one
    message never covers more than a day."""
    if not st:
        return False
    return (parse_ts(first) - parse_ts(st["last"])).total_seconds() <= gap_hours * 3600 \
        and first[:10] == st["last"][:10]


def slack_plan(gk, fs, sev, reasons, st, args):
    """(action, group totals, thread note, open group or None). action: skip | post | update.

    Counts by minute against the Slack group's own cut, not the Incident Log's: a run whose
    post failed after its row was written still brings its requests to the retried post."""
    kind = fs[0]["kind"]
    base = st if group_open(st, min(f["first"] for f in fs), args.gap_hours) else None
    sl = group_slice(fs, base or {}, "")
    raised = not base or rules.rank(sev) > rules.rank(base["severity"])
    g = {
        "title": f"{TITLE.get(kind, kind)} ({gk.split('|')[2]})",
        "first": base["first"] if base else sl["first"],
        "last": max(sl["last"], base["last"]) if base else sl["last"],
        "requests": (base["requests"] if base else 0) + sl["requests"],
        "ips": dict(base["ips"]) if base else {},
        "families": dict(base["families"]) if base else {},
        "statuses": dict(base["statuses"]) if base else {},
        # A group keeps its highest severity until it ends.
        "severity": sev if raised else base["severity"],
        "reasons": reasons if raised else base["reasons"],
        "last_run": sl["requests"],
        **merged_cuts(base, sl),
    }
    for ip, v in sl["ips"].items():
        g["ips"][ip] = g["ips"].get(ip, 0) + (v["n"] or 0)
    for k, v in sl["families"].items():
        g["families"][k] = g["families"].get(k, 0) + v
    for k, v in sl["statuses"].items():
        g["statuses"][k] = g["statuses"].get(k, 0) + v
    if rules.rank(g["severity"]) < rules.rank(args.slack_min_severity.capitalize()):
        return "skip", g, None, base
    if not base or not base.get("ts") or raised:
        return "post", g, None, base
    note = []
    joined = sorted(set(sl["families"]) - set(base["families"]) - {rules.OTHER})
    if joined:
        note.append("new path family in this group: " + ", ".join(joined))
    prev = base.get("last_run") or 0
    if sl["requests"] >= 100 and sl["requests"] >= VOLUME_JUMP * max(prev, 1):
        note.append(f"volume jumped to {sl['requests']:,} requests this run (was {prev:,})")
    return "update", g, ("; ".join(note) or None), base


def slack_send(slack, action, gk, g, st, row_url, mention, note):
    """st is the open Slack group, None once it ended: a closed message is never linked as
    the one this escalates. Post a group's message, or its update as a threaded reply under it.
    Returns the fields to keep ({ts, permalink}), or None on a failure the next run retries. A
    posted message is never edited or deleted (counsel, 2026-11-06)."""
    if action == "update":
        text = slack_text(gk, g, row_url) + (f"\n{note}" if note else "")
        r = slack.post(text, thread_ts=st["ts"])
        # A partial reply still landed: keep the group as it is, or the retry replies twice.
        if not r.get("ok") and not r.get("partial"):
            print(f"Slack thread reply failed for {gk}: {r.get('error')}", file=sys.stderr)
            return None
        return {"ts": st["ts"], "permalink": st.get("permalink")}
    prev = st if st and st.get("ts") else None
    escalated = (f"{label(prev['severity'])} <{prev['permalink']}|earlier message>"
                 if action == "post" and prev and prev.get("permalink")
                 and rules.rank(g["severity"]) > rules.rank(prev["severity"]) else None)
    r = slack.post(slack_text(gk, g, row_url, mention, escalated))
    # A partial post still landed: keep its ts, or the retry posts it a second time.
    if not r.get("ok") and not r.get("partial"):
        print(f"Slack post failed for {gk}: {r.get('error')}", file=sys.stderr)
        return None
    return {"ts": r.get("ts"), "permalink": slack.permalink(r.get("ts"))}


# ---- Cloud Armor alerts ------------------------------------------------------------------

ARMOR_SEVERITY = "Medium"


def armor_text(a, since, until):
    what = "IP ban" if a["type"] == "ban" else "Throttle saturated"
    return (f"{label(ARMOR_SEVERITY)} *Cloud Armor: {what}* on rule {a['priority']}\n"
            f"{a['requests']:,} requests over the limit from {a['keys']} distinct keys, {since}..{until}.\n"
            "Counts only; the rule's key (an address or a fingerprint) is not shown.")


def armor_alerts(plan, history, slack, args, save=None):
    """Post each alert once per rule per UTC day; `save` persists the history after each post, so
    a later failure never re-posts one that went out. Returns the number of failed posts."""
    alerts = (plan.get("armor") or {}).get("alerts") or []
    sent = history.setdefault("armor", {})
    failed = 0
    for a in alerts:
        day = plan["until"][:10]
        if sent.get(a["key"], {}).get("day") == day:
            print(f"cloud armor {a['key']}: already posted on {day}")
            continue
        if rules.rank(ARMOR_SEVERITY) < rules.rank(args.slack_min_severity.capitalize()) or args.no_slack:
            print(f"cloud armor {a['key']}: below the Slack threshold or Slack off; report only")
            continue
        text = armor_text(a, plan["since"], plan["until"])
        if not args.apply:
            print(f"cloud armor {a['key']} -> slack post\n    " + text.replace("\n", "\n    "))
            continue
        try:
            r = slack.post(text)
        except Exception as e:  # one alert's error must not stop the others
            r = {"ok": False, "error": str(e)}
        if not r.get("ok") and not r.get("partial"):
            print(f"Slack post failed for cloud armor {a['key']}: {r.get('error')}", file=sys.stderr)
            failed += 1
            continue
        sent[a["key"]] = {"day": day, "ts": r.get("ts")}
        if save:
            save()
    return failed


# ---- run --------------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("plan", help="JSON written by detect.py --plan")
    p.add_argument("--apply", action="store_true", help="write to Notion and Slack (default: dry run)")
    p.add_argument("--channel", default=DEFAULT_CHANNEL, help=f"Slack channel id (default {DEFAULT_CHANNEL})")
    p.add_argument("--slack-min-severity", choices=["info", "low", "medium", "high"],
                   default=(os.environ.get("DETECT_ATTACKS_SLACK_MIN_SEVERITY") or "medium").lower(),
                   help="lowest severity posted to Slack (env DETECT_ATTACKS_SLACK_MIN_SEVERITY; default medium)")
    p.add_argument("--mention-user", default=os.environ.get("DETECT_ATTACKS_MENTION", ""),
                   help="Slack user id to @mention on Medium and High (env DETECT_ATTACKS_MENTION)")
    p.add_argument("--state-dir", default=os.path.expanduser("~/.cache/detect-attacks"))
    p.add_argument("--gap-hours", type=float, default=GAP_HOURS,
                   help="a Slack group with no hits for this long ends; the next hit starts a new message")
    p.add_argument("--history-days", type=int, default=HISTORY_DAYS,
                   help="a category or family unseen for this long is a new type (Low)")
    p.add_argument("--no-slack", action="store_true")
    return p


def main(argv=None, call=None, slack=None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    call = call or notion
    plan = json.load(open(args.plan, encoding="utf-8"))
    flagged = plan["flagged"]
    if not flagged and not (plan.get("armor") or {}).get("alerts"):
        print(f"no attempts in {plan['since']}..{plan['until']}; nothing to write")
        return
    groups_path = os.path.join(args.state_dir, "groups.json")
    history_path = os.path.join(args.state_dir, "history.json")
    gstates = load_json(groups_path) or {}
    history = load_json(history_path) or seed_history(load_json(os.path.join(args.state_dir, "state.json")))
    global CATEGORY_OPTIONS
    CATEGORY_OPTIONS = None
    has_notion = args.apply or bool(os.environ.get("NOTION_TOKEN"))
    if has_notion:
        try:
            CATEGORY_OPTIONS = log.check_schema(call)
        except log.SchemaError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(2)

    by_group = {}
    for f in flagged:
        sev, why = rules.severity(f, new_types(f, history, args.history_days))
        f = dict(f, severity=sev, severity_reasons=why)
        by_group.setdefault(rules.group_key(f), []).append(f)

    slack = slack or Slack(args.channel)
    mention = mention_text(args.mention_user)
    failed_notion = failed_slack = 0
    for gk, fs in by_group.items():
        sev = max((f["severity"] for f in fs), key=rules.rank)
        reasons = list(dict.fromkeys(r for f in fs if f["severity"] == sev for r in f["severity_reasons"]))[:4]
        row, row_error = None, None
        if has_notion:
            try:
                row = log.open_row(gk, min(f["first"] for f in fs), call)
            except Exception as e:  # the alert must not wait on the log
                row_error = e
        gst = gstates.get(gk) or {}
        if row is None or gst.get("page_id") != row["id"]:
            # A row this writer has not counted yet: only minutes after its Ended are new.
            gst = {}
            default_cut = log.ended(row)[:16] if row else ""
        else:
            default_cut = ""
        sl = group_slice(fs, gst, default_cut)
        action, g, note, open_st = slack_plan(gk, fs, sev, reasons, history.get("slack", {}).get(gk), args)
        print(f"{'update' if row else 'create'} row {gk:<34} [{sev.upper()}] +{sl['requests']} req, "
              f"{len(sl['ips'])} IP(s) -> slack {action}{' + thread reply' if note else ''}")
        for r in reasons if sev != "Info" else []:
            print(f"    ! {r}")
        row_url = log.page_url(row["id"]) if row else None
        if not args.apply:
            if action != "skip":
                print("    " + slack_text(gk, g, row_url, mention if action == "post" else "")
                      .replace("\n", "\n    "))
            continue
        try:
            if row_error:
                raise row_error
            def created(pid, gk=gk):
                # Counted nothing yet: the retry adds every minute to this empty row.
                gstates[gk] = {"page_id": pid}
                save_state(groups_path, gstates)
            page_id = write_group(gk, fs, sl, sev, reasons, row, call, created)
            gstates[gk] = {"page_id": page_id, **merged_cuts(gst, sl)}
            save_state(groups_path, gstates)
            row_url = log.page_url(page_id)
        except Exception as e:
            # Keep going: one bad row must not hold back the alert or the other groups.
            failed_notion += 1
            print(f"Notion write failed for {gk}: {e}", file=sys.stderr)
        if action != "skip" and not args.no_slack:
            try:
                kept = slack_send(slack, action, gk, g, open_st, row_url, mention, note)
            except Exception as e:  # one group's Slack error must not stop the others
                print(f"Slack failed for {gk}: {e}", file=sys.stderr)
                kept = None
            if kept is None:
                # Not marked seen: the next run raises the same severity and retries.
                failed_slack += 1
                continue
            history.setdefault("slack", {})[gk] = {**g, **kept}
        for f in fs:
            mark_seen(history, f)
        save_state(history_path, history)
    failed_slack += armor_alerts(plan, history, slack, args,
                                 save=(lambda: save_state(history_path, history)) if args.apply else None)
    if not args.apply:
        print("\n(dry run: pass --apply to write)")
        return
    print(f"{len(by_group)} group(s); Notion failures {failed_notion}, Slack failures {failed_slack}")
    if failed_slack:
        sys.exit(3)
    if failed_notion:
        sys.exit(4)


if __name__ == "__main__":
    main()
