#!/usr/bin/env python3
"""Render the data map: a dated table per vendor, the SDK-settings flags, and a full CSV.

    python3 render.py --out DIR [--all-rows]

Reads DIR/scan.json (and DIR/reach.json when present), writes DIR/data-map.md and
DIR/data-map.csv. The Markdown lists sensitive properties per vendor; --all-rows lists every
property. Field names and counts only: every file passes guard.check() before it is written.
"""
import argparse
import csv
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import guard  # noqa: E402

REFS = os.path.join(os.path.dirname(HERE), "references")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def collapse(rows):
    """One row per (vendor, app, event, property); files folded into the first file plus a count."""
    out = {}
    for r in rows:
        key = (r["vendor"], r["app"], r["event"] or "", r["property"] or "")
        cur = out.get(key)
        if cur is None:
            out[key] = dict(r, files=[r["file"]])
        else:
            cur["files"].append(r["file"])
            if r.get("added") and (not cur.get("added") or r["added"] < cur["added"]):
                cur["added"] = r["added"]
    return list(out.values())


def reach_cell(event, app, window, libs):
    """('12,345', '4.1%') for an event in one window, over the libs that belong to the app."""
    if not window:
        return "", ""
    if "unmeasured" in window:
        return "unmeasured", ""
    if not event or event.startswith("<"):
        return "n/a", ""
    names = [e.strip() for e in event.split(" | ")]
    if "names" in window and not set(names) <= set(window["names"]):
        return "unmeasured", ""  # the window predates this event: never print it as 0
    mine = [lib for lib, a in libs.items() if a == app]
    users = sum(window["events"].get(n, {}).get(lib, 0) for n in names for lib in mine)
    total = sum(window["totals"].get(lib, 0) for lib in mine)
    if not mine:
        return "n/a", ""
    pct = f"{100 * users / total:.1f}%" if total else ""
    return f"{users:,}", pct


def render(scan, reach, sinks, classes, all_rows=False):
    libs = sinks.get("libs", {})
    labels = {k: v["label"] for k, v in sinks["vendors"].items()}
    clabels = {k: v["label"] for k, v in classes["classes"].items()}
    rows = collapse(scan["rows"])
    every = {}  # properties a wrapper adds to every event: one line per (vendor, app, property)
    for r in rows:
        if r["via"] == "wrapper" and r["property"] and r["kind"] == "event":
            every.setdefault((r["vendor"], r["app"], r["property"]), set()).add(r["event"])
    lines = [f"# Vendor data map, {scan['generated']}", "",
             "Field names and counts only: no property value, user id or email is read or shown.",
             "Code at each repo's default branch: " + ", ".join(f"{r['repo']} {r['sha']}" for r in scan["repos"]) + ".", ""]
    for w, v in (reach or {}).items():
        if "unmeasured" in v:
            lines.append(f"- Reach {w}: unmeasured ({v['unmeasured']}).")
        else:
            lines.append(f"- Reach {w}: {v['from']} to {v['to']}, unique users per event from Mixpanel by mp_lib. "
                         "An app with two libs (iOS and Android) adds them, so its % is an upper bound.")
    if scan.get("errors"):
        lines.append("- Repos not scanned: " + ", ".join(f"{e['repo']} ({e['error']})" for e in scan["errors"]))
    dyn = sum(1 for r in rows if r["event"] == "<dynamic>")
    lines += [f"- Calls whose event name is built at run time: {dyn} (listed as `<dynamic>`).",
              f"- Calls with a property map the scan could not read: {scan.get('unresolved_calls', 0)} (column `unresolved`).",
              f"- Strings masked as value-like: {scan.get('masked') or 'none'}.", ""]
    csv_buf = io.StringIO()
    cw = csv.writer(csv_buf)
    head = ["vendor", "app", "repo", "event", "property", "class", "sensitive", "file", "more_files", "added",
            "users_90d", "pct_90d", "users_all", "pct_all", "via", "unresolved"]
    cw.writerow(head)
    for vendor in sinks["vendors"]:
        vrows = [r for r in rows if r["vendor"] == vendor]
        if not vrows:
            continue
        shown = [r for r in vrows if all_rows or r["sensitive"]]
        shown = [r for r in shown if not (r["via"] == "wrapper" and (r["vendor"], r["app"], r["property"]) in every
                                          and len(every[(r["vendor"], r["app"], r["property"])]) > 20)]
        events = {(r["app"], r["event"]) for r in vrows if r["kind"] == "event"}
        lines += [f"## {labels[vendor]}", "",
                  f"{len(events)} events, {len({(r['app'], r['property']) for r in vrows if r['property']})} property names, "
                  f"{sum(1 for r in vrows if r['sensitive'])} sensitive (event, property) pairs."
                  + ("" if vendor == "mixpanel" else " Users are the same event's Mixpanel reach, a proxy for this vendor."), ""]
        wide = sorted({(a, p) for (v, a, p), evs in every.items() if v == vendor and len(evs) > 20})
        if wide:
            lines.append("Sent with more than 20 events through our wrappers (one line here, not a row per event): " + "; ".join(
                f"{a}: `{p}` ({clabels.get(next((r['class'] for r in vrows if r['property'] == p), 'other'), 'other')})"
                for a, p in wide) + ".")
            lines.append("")
        if shown:
            lines += ["| App | Event | Property | Class | File | Added | Users 90d | % 90d | Users all time | % all |",
                      "|---|---|---|---|---|---|---|---|---|---|"]
            for r in sorted(shown, key=lambda r: (r["app"], r["class"] or "", r["event"] or "", r["property"] or "")):
                u90, p90 = reach_cell(r["event"], r["app"], (reach or {}).get("90d"), libs)
                ua, pa = reach_cell(r["event"], r["app"], (reach or {}).get("all"), libs)
                more = f" (+{len(r['files']) - 1})" if len(r["files"]) > 1 else ""
                event = r["event"] or ("(user profile)" if r["kind"] == "props" else "(identify)" if r["kind"] == "id" else "")
                lines.append(f"| {r['app']} | {event} | {r['property'] or ''} | "
                             f"{clabels.get(r['class'], r['class'] or '')} | `{r['file']}`{more} | {r.get('added') or ''} | "
                             f"{u90} | {p90} | {ua} | {pa} |")
            lines.append("")
        for r in vrows:
            u90, p90 = reach_cell(r["event"], r["app"], (reach or {}).get("90d"), libs)
            ua, pa = reach_cell(r["event"], r["app"], (reach or {}).get("all"), libs)
            cw.writerow([vendor, r["app"], r["repo"], r["event"] or "", r["property"] or "", r["class"] or "",
                         r["sensitive"], r["file"], len(r["files"]) - 1, r.get("added") or "", u90, p90, ua, pa,
                         r["via"], "; ".join(r["unresolved"])])
    if scan.get("removed"):
        lines += ["## Removed since the previous run", "", "| Vendor | App | Event | Property | Class | Removed |",
                  "|---|---|---|---|---|---|"]
        for r in scan["removed"]:
            lines.append(f"| {labels.get(r['vendor'], r['vendor'])} | {r['app']} | {r['event'] or ''} | "
                         f"{r['property']} | {clabels.get(r['class'], r['class'])} | {r['removed']} |")
        lines.append("")
    lines += ["## SDK settings", "", "| Vendor | App | Flag | Setting | Value in code | File |", "|---|---|---|---|---|---|"]
    apps = {r["repo"]: r["app"] for r in scan["repos"]}
    for s in sorted(scan.get("settings", []), key=lambda s: (s["vendor"], s["flag"], s["repo"])):
        lines.append(f"| {labels.get(s['vendor'], s['vendor'])} | {apps.get(s['repo'], s['repo'])} | {s['flag']} | "
                     f"`{s['setting']}` | {s['value'] or 'not a literal'} | `{s['repo']}/{s['file']}` |")
    flagged = {(s["vendor"], s["flag"]) for s in scan.get("settings", [])}
    missing = [(v, f) for v, f in [("mixpanel", "IP geolocation"), ("mixpanel", "session replay"),
                                   ("mixpanel", "consent gating"), ("mixpanel", "automatic events")] if (v, f) not in flagged]
    if missing:
        lines += ["", "Not set anywhere in code (the SDK default applies): "
                  + "; ".join(f"{labels[v]} {f}" for v, f in missing) + "."]
    return "\n".join(lines) + "\n", csv_buf.getvalue()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--all-rows", action="store_true")
    a = ap.parse_args()
    scan = load(os.path.join(a.out, "scan.json"))
    rp = os.path.join(a.out, "reach.json")
    reach = load(rp) if os.path.exists(rp) else None
    md, table = render(scan, reach, load(os.path.join(REFS, "sinks.json")), load(os.path.join(REFS, "classes.json")),
                       a.all_rows)
    guard.write_text(os.path.join(a.out, "data-map.md"), md)
    guard.write_text(os.path.join(a.out, "data-map.csv"), table)
    print(os.path.join(a.out, "data-map.md"))


if __name__ == "__main__":
    main()
