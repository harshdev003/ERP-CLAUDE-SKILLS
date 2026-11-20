"""
Security Incident Log access for apply.py and blocklist.py: one row per group
(`Group key`), ban data that is never truncated, and the schema guard.

The schema is shared with eng:monitor-rtdb-probes and is never changed from here: a
missing column or option is a refusal (SchemaError), not a migration.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess

INCIDENT_DB = "40175e23-af20-8180-aece-d339a099f573"
NOTION = "https://api.notion.com/v1"
FOUND_BY = "eng:flag-suspicious-traffic (load-balancer logs)"
# A group with no hit for this long is closed (Jerome, 2026-11-03). Shared with monitor-rtdb-probes'
# GROUP_CLOSE_MS, pinned equal by tests/test_incident_group_window.py.
GROUP_OPEN_DAYS = 2
CHUNK = 1990          # Notion caps one rich_text item at 2,000 characters
MAX_ITEMS = 100       # and one property at 100 items
REQUIRED = {"Group key": ("rich_text", None), "Severity": ("select", {"Info", "Low", "Medium", "High"})}
# "Other attack" is renamed in place to OTHER_CATEGORY, spelled exactly as the live option (US "z";
# Jerome, 2026-11-10). A write naming an option the database lacks makes Notion create it, so the old
# name is written while the live list still has it.
OTHER_CATEGORY = "Other unrecognized access"
OLD_OTHER_CATEGORY = "Other attack"
# Staff / Not staff, a select Librarian adds beside Actor (Jerome, 2026-11-11). check_schema() records
# its live options; while the column or an option is missing the property is left out, never created.
STAFF_VALUES = ("Staff", "Not staff")
STAFF_OPTIONS = frozenset()
OVERFLOW = re.compile(r"… (\d+) more in Notion page ([0-9a-f-]{32,36})")


class SchemaError(RuntimeError):
    pass


def http(method, url, body=None, headers=None):
    """HTTPS via requests (bundles certifi) or curl. Never urllib: its default SSL context
    can't find the CA bundle on stock macOS Python."""
    data = json.dumps(body) if body is not None else None
    try:
        import requests
    except ImportError:
        requests = None
    if requests is not None:
        r = requests.request(method, url, data=data, headers=headers or {}, timeout=60)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {url.split('?')[0]} -> {r.status_code}: {r.text[:300]}")
        return r.json()
    cmd = ["curl", "-sS", "-X", method, url, "-w", "\n%{http_code}"]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    if data is not None:
        cmd += ["--data-binary", "@-"]
    out = subprocess.run(cmd, input=data, capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=90)
    text_body, _, code = out.stdout.rpartition("\n")
    if out.returncode or not code.isdigit() or int(code) >= 400:
        raise RuntimeError(f"{method} {url.split('?')[0]} -> {code or out.returncode}: {text_body[:300]}")
    return json.loads(text_body)


def notion(method, path, body=None):
    return http(method, f"{NOTION}{path}", body, {
        "Authorization": "Bearer " + os.environ["NOTION_TOKEN"],
        "Notion-Version": "2022-06-28", "Content-Type": "application/json"})


def page_url(page_id):
    return f"https://app.notion.com/p/{page_id.replace('-', '')}"


def check_schema(call=None):
    """Refuse to write when a column this writer needs is missing (exit 2 in apply.py).
    Returns the live Category option names."""
    global STAFF_OPTIONS
    db = (call or notion)("GET", f"/databases/{INCIDENT_DB}")
    props = db.get("properties", {})
    staff = props.get("Staff") or {}
    STAFF_OPTIONS = frozenset(o["name"] for o in staff.get("select", {}).get("options", [])
                              if staff.get("type") == "select" and o.get("name") in STAFF_VALUES)
    missing = []
    for name, (kind, options) in REQUIRED.items():
        p = props.get(name)
        if not p or p.get("type") != kind:
            missing.append(f"{name} ({kind})")
        elif options and not options <= {o["name"] for o in p[kind].get("options", [])}:
            missing.append(f"{name} options {sorted(options)}")
    if missing:
        raise SchemaError("Security Incident Log is missing " + ", ".join(missing)
                          + ". Someone with access adds it in Notion; this script never changes the schema.")
    return {o["name"] for o in (props.get("Category") or {}).get("select", {}).get("options", [])}


def staff_prop(value, current=None):
    """{"Staff": value} to merge into a write, or {} while the column lacks that option or the row
    already has a value: a value a person set is never overwritten."""
    if value not in STAFF_OPTIONS or ((current or {}).get("select") or {}).get("name"):
        return {}
    return {"Staff": {"select": {"name": value}}}


def category_name(kind, options=None):
    """The Category option a row of this kind is written with, given the live option names."""
    if kind not in (OLD_OTHER_CATEGORY, OTHER_CATEGORY):
        return kind
    if options is not None and OTHER_CATEGORY not in options and OLD_OTHER_CATEGORY in options:
        return OLD_OTHER_CATEGORY
    return OTHER_CATEGORY


# ---- rich text that never loses a line ------------------------------------------------

def chunks(text, limit=CHUNK):
    """Items of at most `limit` characters that concatenate back to `text` exactly (Notion
    joins items with nothing in between), cut at line ends; only a longer line is cut."""
    out, cur = [], ""
    for line in text.splitlines(keepends=True):
        if cur and len(cur) + len(line) > limit:
            out.append(cur)
            cur = ""
        while len(line) > limit:
            out.append(line[:limit])
            line = line[limit:]
        cur += line
    if cur:
        out.append(cur)
    return out


def rich(text):
    return {"rich_text": [{"text": {"content": c}} for c in chunks(str(text))]}


def plain(prop):
    return "".join(x.get("plain_text", "") for x in (prop or {}).get("rich_text", []))


def head_lines(text, max_items=MAX_ITEMS):
    """Last lines dropped until the text fits; for ranked lists whose top matters most."""
    lines = text.split("\n")
    while len(lines) > 1 and len(chunks("\n".join(lines))) > max_items:
        lines.pop()
    return "\n".join(lines)


def tail_lines(text, max_items=MAX_ITEMS):
    """Oldest lines dropped until the text fits; for narrative columns only, never ban data."""
    lines = text.split("\n")
    while len(lines) > 1 and len(chunks("\n".join(lines))) > max_items:
        lines.pop(0)
    return "\n".join(lines)


# ---- ban data: IPs and user agents ----------------------------------------------------

IP_ENTRY = re.compile(r"^(?P<ip>[0-9a-fA-F:.]+)(?: \((?P<n>\d+) req(?:, last (?P<last>[^)]+))?\))?$")
UA_ENTRY = re.compile(r"^(?P<ua>.*) \((?P<n>\d+)\)$")
EMPTY_UA = "(empty user agent)"


def parse_ips(text):
    """{ip: {"n": int|None, "last": str|None}} from `ip (n req, last T)` lines or the older
    comma-separated list (counts unknown, kept as None rather than guessed)."""
    out = {}
    parts = []
    for line in text.split("\n"):
        parts += [line] if "(" in line else re.split(r"[,;]", line)
    for part in parts:
        part = part.strip()
        if not part or part.startswith("…"):
            continue
        m = IP_ENTRY.match(part)
        if not m:
            m = IP_ENTRY.match(part.split(" ")[0])
        if m:
            out[m["ip"]] = {"n": int(m["n"]) if m["n"] else None, "last": m["last"]}
    return out


def parse_uas(text):
    """{ua: count|None} from one `<full UA> (<count>)` per line. Split on newlines only: a
    user agent contains "; " itself. The trailing ` (N)` is the count; a line without one
    keeps no count. `(empty user agent)` is the empty string."""
    out = {}
    for line in text.split("\n"):
        line = line.strip()
        if not line or OVERFLOW.search(line):
            continue
        m = UA_ENTRY.match(line)
        ua, n = (m["ua"], int(m["n"])) if m else (line, None)
        out["" if ua == EMPTY_UA else ua] = n
    return out


def format_ips(ips):
    def key(kv):
        return (-(kv[1]["n"] or 0), kv[0])
    lines = []
    for ip, v in sorted(ips.items(), key=key):
        if v["n"] is None:
            lines.append(ip)
        else:
            lines.append(f"{ip} ({v['n']} req" + (f", last {v['last']})" if v.get("last") else ")"))
    return "\n".join(lines)


def format_uas(uas):
    def name(u):
        return u or EMPTY_UA
    return "\n".join(name(u) if n is None else f"{name(u)} ({n})"
                     for u, n in sorted(uas.items(), key=lambda kv: (-(kv[1] or 0), kv[0])))


def net24(ip):
    return ".".join(ip.split(".")[:3]) if "." in ip else ip.rsplit(":", 4)[0]


def cidr(net):
    return f"{net}.0/24" if net.count(".") == 2 else f"{net}::/64"


def networks(ips):
    nets = {}
    for ip, v in ips.items():
        n = nets.setdefault(net24(ip), {"ips": 0, "requests": 0})
        n["ips"] += 1
        n["requests"] += v["n"] or 0
    return "\n".join(f"{cidr(k)}: {v['ips']} IPs, {v['requests']} req"
                     for k, v in sorted(nets.items(), key=lambda kv: (-kv[1]["ips"], -kv[1]["requests"])))


def merge_ban(ips, uas, add_ips, add_uas):
    """Union, never replace: counts add, last-seen is the latest, unknown counts stay known
    as soon as there is one."""
    ips = {k: dict(v) for k, v in ips.items()}
    for ip, v in add_ips.items():
        cur = ips.setdefault(ip, {"n": None, "last": None})
        if v["n"]:
            cur["n"] = (cur["n"] or 0) + v["n"]
        if v.get("last") and (not cur.get("last") or v["last"] > cur["last"]):
            cur["last"] = v["last"]
    uas = dict(uas)
    for ua, n in add_uas.items():
        if n:
            uas[ua] = (uas.get(ua) or 0) + n
        elif ua not in uas:
            uas[ua] = None
    return ips, uas


def ban_property(text, label, child_id):
    """The column's rich_text. Past 100 items, the first 99 stay and the last item points
    at the child page holding everything."""
    items = chunks(text)
    if len(items) <= MAX_ITEMS:
        return {"rich_text": [{"text": {"content": c}} for c in items]}, False
    rest = len(text.splitlines()) - len("".join(items[:MAX_ITEMS - 1]).splitlines())
    marker = f"\n… {rest} more in Notion page {child_id} ({label})"
    return {"rich_text": [{"text": {"content": c}} for c in items[:MAX_ITEMS - 1]]
            + [{"text": {"content": marker}}]}, True


def read_overflow(row_props, call=None):
    """Ban data kept in the row's child page, if a column points at one."""
    call = call or notion
    for name in ("IP addresses", "User agent"):
        m = OVERFLOW.search(plain(row_props.get(name)))
        if m:
            text, cursor = "", None
            while True:
                q = call("GET", f"/blocks/{m[2]}/children" + (f"?start_cursor={cursor}" if cursor else ""))
                for b in q.get("results", []):
                    if b.get("type") == "code":
                        text += "".join(x.get("plain_text", "") for x in b["code"]["rich_text"])
                if not q.get("has_more"):
                    break
                cursor = q["next_cursor"]
            data = json.loads(text)
            return m[2], data.get("ips", {}), data.get("uas", {})
    return None, {}, {}


def write_overflow(row_id, ips, uas, call=None):
    """A new ban-data child page holding the full union as JSON code blocks. The caller
    points the row at it and only then archives the old one, so a failure in between
    leaves the row on its previous, complete page."""
    call = call or notion
    payload = json.dumps({"ips": ips, "uas": uas}, sort_keys=True)
    items = [{"text": {"content": c}} for c in chunks(payload)]
    blocks = [{"object": "block", "type": "code",
               "code": {"language": "json", "rich_text": items[i:i + MAX_ITEMS]}}
              for i in range(0, len(items), MAX_ITEMS)]
    page = call("POST", "/pages", {
        "parent": {"page_id": row_id},
        "properties": {"title": {"title": [{"text": {"content": "Ban data (IPs and user agents)"}}]}},
        "children": blocks[:100]})
    for i in range(100, len(blocks), 100):
        call("PATCH", f"/blocks/{page['id']}/children", {"children": blocks[i:i + 100]})
    return page["id"]


def row_ban_data(row, call=None):
    """(child page id, ips, uas) of a row: its columns plus any overflow page."""
    props = row["properties"]
    child, o_ips, o_uas = read_overflow(props, call)
    ips = parse_ips(plain(props.get("IP addresses")))
    uas = parse_uas(plain(props.get("User agent")))
    for ip, v in o_ips.items():
        ips[ip] = v
    for ua, n in o_uas.items():
        uas[ua] = n
    return child, ips, uas


# ---- group rows -----------------------------------------------------------------------

def parse_ts(ts):
    """Aware UTC datetime. A date-only value (Notion allows `YYYY-MM-DD`) is midnight UTC."""
    t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def ended(row):
    d = (row["properties"].get("Ended (UTC)") or {}).get("date") or {}
    return (d.get("start") or "").replace(".000+00:00", "Z").replace("+00:00", "Z")


def open_row(group_key, first, call=None):
    """The newest non-archived row with this Group key, unless its Ended is more than
    GROUP_OPEN_DAYS before `first`: then the group is closed and a new row starts."""
    q = (call or notion)("POST", f"/databases/{INCIDENT_DB}/query", {
        "filter": {"property": "Group key", "rich_text": {"equals": group_key}},
        "sorts": [{"property": "Ended (UTC)", "direction": "descending"}], "page_size": 5})
    for row in q.get("results", []):
        if row.get("archived"):
            continue
        end = ended(row)
        if end and (parse_ts(first) - parse_ts(end)).total_seconds() > GROUP_OPEN_DAYS * 86400:
            return None
        return row
    return None


def open_rows(call=None, now=None):
    """Every open group row (Ended within GROUP_OPEN_DAYS), newest first."""
    call = call or notion
    now = now or dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(days=GROUP_OPEN_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out, cursor = [], None
    while True:
        body = {"filter": {"and": [{"property": "Group key", "rich_text": {"is_not_empty": True}},
                                   {"property": "Ended (UTC)", "date": {"on_or_after": since}}]},
                "sorts": [{"property": "Ended (UTC)", "direction": "descending"}], "page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        q = call("POST", f"/databases/{INCIDENT_DB}/query", body)
        out += [r for r in q.get("results", []) if not r.get("archived")]
        if not q.get("has_more"):
            return out
        cursor = q["next_cursor"]
