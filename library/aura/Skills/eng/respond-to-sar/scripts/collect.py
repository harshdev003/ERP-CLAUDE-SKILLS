#!/usr/bin/env python3
"""
Collect one user's raw data for a Subject Access Request pack.

Every subcommand is read-only against its source and writes into <out>/raw/.
Re-running a subcommand overwrites its own raw files, so a re-run is safe.

Subcommands:
  stripe    customer, subscriptions, invoices and PaymentIntents (with charges)
  mixpanel  raw event export by user id, then again by every device id seen
  gcp       Cloud Logging: a tight text search around sign-up, plus a per-day
            sweep of the user-facing services over the whole window
  whois     network owner + reverse DNS for each IP address
  llm-calls the person's IntelligenceService llmCall rows (one per AI call: time, prompter,
            model, tokens, platform, conversation id; no message text) from BigQuery

Secrets come from the environment and are never printed or put on a command line:
  STRIPE_API_KEY, MIXPANEL_SERVICE_ACCOUNT_USERNAME / _SECRET, MIXPANEL_PROJECT_ID, GCP_KEY
  (Cloud Logging and the BigQuery llmCall ledger).
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse

# Containers whose log lines describe what the user's own browser or app did.
USER_FACING = ("user-web", "payment-service", "api-gateway", "proxy-web", "user-service")


def _curl_json(url: str, user: str, params: list[tuple[str, str]] | None = None, raw: bool = False,
               form: list[tuple[str, str]] | None = None):
    """GET (or form POST) via curl — the macOS CA bundle trips urllib. Credentials go in on stdin, never argv."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    cfg = f'user = "{user}"\nurl = "{url}"\nsilent\nshow-error\n'
    if form:
        cfg += f'data = "{urllib.parse.urlencode(form)}"\n'
    out = subprocess.run(["curl", "-K", "-"], input=cfg, capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"curl exit {out.returncode}")
    return out.stdout if raw else json.loads(out.stdout)


def _raw_dir(out: str) -> str:
    path = os.path.join(out, "raw")
    os.makedirs(path, exist_ok=True)
    return path


def _dump(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, default=str)


# --------------------------------------------------------------------------- stripe

def cmd_stripe(a) -> dict:
    key = os.environ.get("STRIPE_API_KEY")
    if not key:
        raise SystemExit("STRIPE_API_KEY is not set")
    user = f"{key}:"
    base = "https://api.stripe.com/v1"
    raw = _raw_dir(a.out)
    errors: dict[str, str] = {}

    def get(path, params=None):
        d = _curl_json(f"{base}/{path}", user, params)
        if "error" in d:
            errors[path] = d["error"].get("code") or d["error"].get("message", "")
            return None
        return d

    customer_id = a.customer
    if not customer_id:
        found = get("customers", [("email", a.email), ("limit", "10")]) or {"data": []}
        if not found["data"]:
            raise SystemExit(f"No Stripe customer for that email")
        if len(found["data"]) > 1:
            _dump(f"{raw}/stripe_customer_candidates.json", found["data"])
        customer_id = found["data"][0]["id"]

    customer = get(f"customers/{customer_id}")
    # Cards attached through the Payment Methods API don't appear under customer.sources.
    methods = get("payment_methods", [("customer", customer_id), ("type", "card"), ("limit", "100")]) or {"data": []}
    subs = get("subscriptions", [("customer", customer_id), ("status", "all"), ("limit", "100")]) or {"data": []}
    invoices = get("invoices", [("customer", customer_id), ("limit", "100")]) or {"data": []}
    intents = []
    for inv in invoices["data"]:
        if inv.get("payment_intent"):
            pi = get(f"payment_intents/{inv['payment_intent']}")
            if pi:
                intents.append(pi)
    # Probed so the pack's README can say what this key could not see.
    for probe in ("disputes", "charges", "events", "checkout/sessions"):
        get(probe, [("limit", "1")])

    _dump(f"{raw}/stripe_customer.json", customer)
    _dump(f"{raw}/stripe_payment_methods.json", methods["data"])
    _dump(f"{raw}/stripe_subscriptions.json", subs["data"])
    _dump(f"{raw}/stripe_invoices.json", invoices["data"])
    _dump(f"{raw}/stripe_payment_intents.json", intents)
    _dump(f"{raw}/stripe_unreadable.json", errors)
    return {"customer": customer_id, "subscriptions": len(subs["data"]), "invoices": len(invoices["data"]),
            "payment_intents": len(intents), "payment_methods": len(methods["data"]), "unreadable": sorted(errors)}


# --------------------------------------------------------------------------- mixpanel

def _project_today(tz: str) -> dt.date:
    """Mixpanel rejects a to_date past the project's calendar day, which runs behind UTC."""
    from zoneinfo import ZoneInfo
    return dt.datetime.now(ZoneInfo(tz)).date()


def _dedupe_key(e: dict) -> tuple:
    # Not $insert_id alone: clients reuse one id across several distinct events.
    p = e.get("properties") or {}
    return (e.get("event"), p.get("distinct_id"), p.get("time"), p.get("$insert_id"))


def _mp_export(where: str, date_from: str, date_to: str) -> list[dict]:
    u, s, pid = (os.environ.get(k) for k in
                 ("MIXPANEL_SERVICE_ACCOUNT_USERNAME", "MIXPANEL_SERVICE_ACCOUNT_SECRET", "MIXPANEL_PROJECT_ID"))
    if not (u and s and pid):
        raise SystemExit("MIXPANEL_SERVICE_ACCOUNT_USERNAME / _SECRET / MIXPANEL_PROJECT_ID must be set")
    host = os.environ.get("MIXPANEL_EXPORT_HOST", "data.mixpanel.com")
    body = _curl_json(f"https://{host}/api/2.0/export", f"{u}:{s}",
                      [("project_id", pid), ("from_date", date_from), ("to_date", date_to), ("where", where)], raw=True)
    events = []
    for line in body.splitlines():
        if line.strip():
            e = json.loads(line)
            if "error" in e:
                raise RuntimeError(f"Mixpanel export: {e['error']}")
            events.append(e)
    return events


def cmd_mixpanel(a) -> dict:
    uid = a.uid
    today = _project_today(a.project_tz)
    date_to = min(dt.date.fromisoformat(a.date_to), today).isoformat()
    date_from = min(dt.date.fromisoformat(a.date_from), today).isoformat()
    ids = ("$user_id", "distinct_id", "UserID", "userId")
    by_user = " or ".join(f'properties["{k}"] == "{uid}"' for k in ids)
    events = _mp_export(by_user, date_from, date_to)
    devices = sorted({e["properties"].get("$device_id") for e in events} - {None})
    dropped = 0
    if devices and events:
        # The device pass exists to catch this person's anonymous pre-sign-up events. A shared or
        # reused device also carries other people's events, so keep only anonymous events from the
        # 24 hours before this person was first identified, and drop anything tied to another id.
        first_known = min(e["properties"]["time"] for e in events)
        by_device = " or ".join(f'properties["$device_id"] == "{d}"' for d in devices)
        for e in _mp_export(by_device, date_from, date_to):
            p = e["properties"]
            other_ids = {str(p.get(k)) for k in ids if p.get(k)} - {uid}
            anonymous = not any(p.get(k) == uid for k in ids) and all(i.startswith("$device:") for i in other_ids)
            if any(p.get(k) == uid for k in ids) or (anonymous and first_known - 86400 <= p["time"] <= first_known):
                events.append(e)
            else:
                dropped += 1
    unique = {_dedupe_key(e): e for e in events}
    ordered = sorted(unique.values(), key=lambda e: e["properties"]["time"])
    raw = _raw_dir(a.out)
    with open(f"{raw}/mixpanel_events.jsonl", "w", encoding="utf-8") as f:
        for e in ordered:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    # The user profile (people properties) is personal data too — often more of it than the events.
    u, s, pid = (os.environ[k] for k in
                 ("MIXPANEL_SERVICE_ACCOUNT_USERNAME", "MIXPANEL_SERVICE_ACCOUNT_SECRET", "MIXPANEL_PROJECT_ID"))
    host = os.environ.get("MIXPANEL_QUERY_HOST", "mixpanel.com")
    profile = _curl_json(f"https://{host}/api/query/engage", f"{u}:{s}", [("project_id", pid)],
                         form=[("distinct_id", uid)])
    _dump(f"{raw}/mixpanel_profile.json", (profile.get("results") or [None])[0])
    return {"events": len(ordered), "device_ids": len(devices), "device_events_dropped": dropped,
            "window": [date_from, date_to], "profile": bool(profile.get("results"))}


# --------------------------------------------------------------------------- gcp

GCP_CAP = 5000


def _gcp_query(filter_str: str, max_entries: int = GCP_CAP) -> list[dict]:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import gcp  # noqa: E402  (canonical helper copy, see docs/canonical-sources.md)
    auth = gcp.get_client()
    return list(gcp.list_entries(auth, filter_str, max_entries=max_entries, order_by="timestamp asc"))


def _gcp_window(lo: dt.datetime, hi: dt.datetime, clause: str, truncated: list[str]) -> list[dict]:
    """Fetch [lo, hi). A window that hits the cap is split in half until it fits, so a busy day
    never silently keeps its oldest lines and loses the newest. A window that is still capped at
    one minute is kept and recorded in `truncated` — the pack must say the log is incomplete."""
    flt = f'timestamp>="{lo.isoformat()}" AND timestamp<"{hi.isoformat()}" AND {clause}'
    got = _gcp_query(flt)
    if len(got) < GCP_CAP:
        return got
    if hi - lo <= dt.timedelta(minutes=1):
        truncated.append(f"{lo.isoformat()}/{hi.isoformat()}")
        return got
    mid = lo + (hi - lo) / 2
    return _gcp_window(lo, mid, clause, truncated) + _gcp_window(mid, hi, clause, truncated)


def _slim(e: dict) -> dict:
    return {"ts": e.get("timestamp"), "severity": e.get("severity"),
            "resource": (e.get("resource") or {}).get("type"),
            "container": ((e.get("resource") or {}).get("labels") or {}).get("container_name"),
            "http": e.get("httpRequest"), "payload": e.get("jsonPayload") or {"message": e.get("textPayload", "")}}


ENDING_BATCH = 40


def _parse_ts(t: str) -> dt.datetime:
    # Cloud Logging returns nanoseconds; fromisoformat takes at most microseconds on older Pythons.
    t = re.sub(r"(\.\d{6})\d+", r"\1", str(t)).replace("Z", "+00:00")
    return dt.datetime.fromisoformat(t)


def _endings(rows: list[dict], truncated: list[str], pace: float = 1.1) -> tuple[list[dict], int]:
    """Each transaction's "Ending transaction" line, for the transactions whose own lines carry no
    originalUrl. Services log the route there (as a pattern) instead of on "Starting transaction", and
    that line doesn't name the person, so the uid search never returns it. Batched OR filters, paced
    under the 60 reads/min Cloud Logging quota. Returns the lines and how many transactions needed one."""
    has_url, when = set(), {}
    for r in rows:
        pl = r.get("payload") or {}
        tx = pl.get("transaction")
        if not tx or not r.get("ts"):
            continue
        if pl.get("originalUrl"):
            has_url.add(tx)
        when.setdefault(tx, []).append(r["ts"])
    todo = sorted((tx for tx in when if tx not in has_url), key=lambda tx: min(when[tx]))
    out = []
    for i in range(0, len(todo), ENDING_BATCH):
        batch = todo[i:i + ENDING_BATCH]
        ts = [_parse_ts(t) for tx in batch for t in when[tx]]
        lo, hi = min(ts) - dt.timedelta(minutes=1), max(ts) + dt.timedelta(minutes=10)
        txs = " OR ".join(f'"{tx}"' for tx in batch)
        out += [_slim(e) for e in _gcp_window(lo, hi, f'jsonPayload.message="Ending transaction" AND jsonPayload.transaction=({txs})', truncated)]
        if i + ENDING_BATCH < len(todo):
            time.sleep(pace)
    return out, len(todo)


def cmd_gcp(a) -> dict:
    uid, raw = a.uid, _raw_dir(a.out)
    result = {}
    truncated: list[str] = []
    if a.signup:
        t = dt.datetime.fromisoformat(a.signup.replace("Z", "+00:00"))
        lo, hi = (t - dt.timedelta(minutes=a.signup_minutes)), (t + dt.timedelta(minutes=a.signup_minutes))
        rows = [_slim(e) for e in _gcp_window(lo, hi, f'"{uid}"', truncated)]
        with open(f"{raw}/gcp_signup.jsonl", "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r, default=str) + "\n" for r in rows)
        result["signup_entries"] = len(rows)
    if a.date_from and a.date_to:
        # A single OR filter across weeks of logs stalls for over an hour; one query per day,
        # narrowed to user-facing resources, finishes in minutes.
        start = dt.date.fromisoformat(a.date_from)
        days = [start + dt.timedelta(days=i) for i in range((dt.date.fromisoformat(a.date_to) - start).days + 1)]
        containers = " OR ".join(f'"{c}"' for c in USER_FACING)
        res = f'((resource.type="k8s_container" AND resource.labels.container_name=({containers})) OR resource.type="http_load_balancer")'

        def one(day):
            lo = dt.datetime.combine(day, dt.time(), dt.timezone.utc)
            return day, [_slim(e) for e in _gcp_window(lo, lo + dt.timedelta(days=1), f'"{uid}" AND {res}', truncated)]

        per_day = {}
        rows = []
        with cf.ThreadPoolExecutor(max_workers=8) as pool:
            for day, got in pool.map(one, days):
                per_day[str(day)] = len(got)
                rows += got
        rows.sort(key=lambda r: r["ts"] or "")
        with open(f"{raw}/gcp_window.jsonl", "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r, default=str) + "\n" for r in rows)
        result["per_day"] = per_day
    rows_all = []
    for name in ("gcp_signup.jsonl", "gcp_window.jsonl"):
        if os.path.exists(f"{raw}/{name}"):
            with open(f"{raw}/{name}", encoding="utf-8") as f:
                rows_all += [json.loads(l) for l in f if l.strip()]
    endings, wanted = _endings(rows_all, truncated)
    with open(f"{raw}/gcp_endings.jsonl", "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, default=str) + "\n" for r in endings)
    found = {(e.get("payload") or {}).get("transaction") for e in endings}
    result["endings_wanted"] = wanted
    result["endings_missing"] = wanted - len(found)
    # Written on every run (empty when complete) so a stale flag from an earlier run never lingers.
    _dump(f"{raw}/gcp_truncated.json", truncated)
    result["truncated_windows"] = truncated
    return result


# --------------------------------------------------------------------------- whois

WHOIS_KEYS = ("inetnum", "netrange", "netname", "descr", "orgname", "org-name", "country", "origin", "route")


def cmd_whois(a) -> dict:
    out = {}
    for ip in a.ip:
        info: dict = {}
        for server in ("whois.ripe.net", None):
            cmd = ["whois", "-h", server, ip] if server else ["whois", ip]
            try:
                text = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
            except (OSError, subprocess.TimeoutExpired):
                continue
            for line in text.splitlines():
                m = re.match(r"^([A-Za-z-]+):\s+(.+)$", line)
                if m and m.group(1).lower() in WHOIS_KEYS:
                    info.setdefault(m.group(1).lower(), m.group(2).strip())
            if "netname" in info and "not managed by the RIPE" not in text:
                break
        try:
            info["reverse_dns"] = socket.gethostbyaddr(ip)[0]
        except OSError:
            info["reverse_dns"] = None
        out[ip] = info
    _dump(f"{_raw_dir(a.out)}/ip_whois.json", out)
    return out


# --------------------------------------------------------------------------- llm-calls

def cmd_llm_calls(a) -> dict:
    """Every llmCall row for the uid, oldest first. The table keeps 396 days."""
    uid = (a.uid or "").strip()
    # The ledger drops an empty filter, so a blank uid would read every person's calls.
    if not uid:
        raise SystemExit("llm-calls: --uid is empty; refusing to read the ledger without a user filter")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import llm_ledger  # noqa: E402  (bundled copy — canonical at data:analyze-user-conversations)
    try:
        rows = llm_ledger.rows({"userId": uid, "order": "asc"})
    except llm_ledger.LedgerError as e:
        raise SystemExit(f"llm-calls: {e}")
    _dump(f"{_raw_dir(a.out)}/llm_calls.json", rows)
    return {"rows": len(rows), "first": rows[0]["createdAt"] if rows else None,
            "last": rows[-1]["createdAt"] if rows else None,
            "prompters": sorted({r.get("prompterName") for r in rows if r.get("prompterName")})}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("stripe")
    s.add_argument("--out", required=True)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--customer")
    g.add_argument("--email")

    m = sub.add_parser("mixpanel")
    m.add_argument("--out", required=True)
    m.add_argument("--uid", required=True)
    m.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD, a day before sign-up")
    m.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD, today (clamped to the project's day)")
    m.add_argument("--project-tz", default="America/Los_Angeles", help="Mixpanel project timezone (Tools/Mixpanel)")

    c = sub.add_parser("gcp")
    c.add_argument("--out", required=True)
    c.add_argument("--uid", required=True)
    c.add_argument("--signup", help="account creation time, ISO UTC (e.g. 2026-09-16T07:51:30Z)")
    c.add_argument("--signup-minutes", type=int, default=10)
    c.add_argument("--from", dest="date_from", help="YYYY-MM-DD")
    c.add_argument("--to", dest="date_to", help="YYYY-MM-DD")

    w = sub.add_parser("whois")
    w.add_argument("--out", required=True)
    w.add_argument("--ip", action="append", required=True)

    lc = sub.add_parser("llm-calls")
    lc.add_argument("--out", required=True)
    lc.add_argument("--uid", required=True)

    a = p.parse_args()
    fn = {"stripe": cmd_stripe, "mixpanel": cmd_mixpanel, "gcp": cmd_gcp, "whois": cmd_whois,
          "llm-calls": cmd_llm_calls}[a.cmd]
    print(json.dumps(fn(a), indent=1, default=str))


if __name__ == "__main__":
    main()
