#!/usr/bin/env python3
"""
Build the user-facing SAR attachments from whatever collect.py (and the human) put in <out>/raw/.

Each section is emitted only when its source file exists, so a pack for a mobile-only user with
no Stripe customer simply has no billing CSVs. Deterministic: the same raw/ always yields the
same export/, so re-running after adding a source is safe.

Reads (all optional except the user row):
  raw/postgres_user.json        the `user` row the human ran (see SKILL.md step 2)
  raw/stripe_*.json             collect.py stripe
  raw/mixpanel_events.jsonl     collect.py mixpanel
  raw/mixpanel_profile.json     collect.py mixpanel
  raw/gcp_signup.jsonl          collect.py gcp --signup
  raw/gcp_window.jsonl          collect.py gcp --from/--to
  raw/gcp_endings.jsonl         collect.py gcp (each transaction's "Ending transaction" line)
  raw/ip_whois.json             collect.py whois
  raw/llm_calls.json            collect.py llm-calls
  raw/route_review.json         by hand: {transaction: "staff" | "own"} for each unresolved route
Writes:
  export/01_account_profile.csv            always
  export/02_timeline.csv                   always (account, subscription and billing milestones)
  export/03_billing_history.csv            if Stripe
  export/04_payment_method_and_tokens.csv  if Stripe
  export/05_activity_events.csv            if Mixpanel
  export/06_server_request_log.csv         if GCP
  export/07_ai_call_log.csv                if llm-calls
  <out>/SAR_<date>.zip                     reply.md (if present) + every file in export/
Any other file already in export/ (for example a conversations PDF from
eng:export-user-conversations) is kept and zipped with the rest.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import zipfile
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc

# Mixpanel property prefixes that are internal plumbing, not information about the person.
MP_SKIP = re.compile(r"^(\$mp_|mp_|\$insert_id$|\$lib_version$|Experiment|distinct_id$|time$|\$had_persisted|\$distinct_id_before)")

# Internal Aura log messages → plain language for the person reading the export.
LOG_LABELS = [
    (r"^Setting user", "Account record saved"),
    (r"^Starting transaction", "Request received from your browser or app"),
    (r"^Created the customer", "Payment customer created at Stripe"),
    (r"^Showing payment request", "Payment sheet shown"),
    (r"^Payment request processing started", "Payment confirmation started"),
    (r"^Payment request authorization started", "Payment authorisation started"),
    (r"^Payment request subscription started", "Subscription creation started"),
    (r"^Payment request subscription completed", "Subscription confirmed"),
    (r"^Sent '([A-Za-z]+)' pixel event", r"'\1' event sent to Meta (Conversions API)"),
]
# Lines about us, not about the person: internal computation and staff support lookups.
LOG_DROP = re.compile(r"^(Checking ai plan|Fetched .* from cache|Getting the prediction|Returning recommendations|"
                      r"Selected \d+ tracks|Got \d+ recommendations|.*tracks made it through|Starting calculation|"
                      r"Ending transaction|Done getting the prediction|No results from this model|User has no .*preferences|"
                      r"No properties to create|Experiment not assigned)")
# Mixpanel libraries that report from our backend. Anything else is the person's own browser or app.
SERVER_LIBS = {"node", "python", "ruby", "java", "go", "php", "dotnet"}


def recorded_by(pr: dict) -> str:
    lib = pr.get("mp_lib")
    if lib in SERVER_LIBS:
        return "Aura server"
    return "Web browser" if lib == "web" else "Aura app"


# A staff support lookup: a raw id in older logs, a route pattern (`/checkUser/:userId`) since services
# log the pattern instead of the path.
STAFF_ROUTE = re.compile(r"/(getCustomerId|list|checkUser)/(?::\w+|[A-Za-z0-9]+)")


def staff_transactions(*line_sets) -> set:
    """Transactions whose request is a staff route. Since services log the route only on the
    transaction's "Ending transaction" line, every line of such a transaction is dropped by this join."""
    out = set()
    for lines in line_sets:
        for g in lines:
            pl = g.get("payload") or {}
            if pl.get("transaction") and STAFF_ROUTE.search(str(pl.get("originalUrl", ""))):
                out.add(pl["transaction"])
    return out


def routed_transactions(*line_sets) -> set:
    """Transactions whose route is known from any of their lines. A transaction outside this set
    can't be told apart from the person's own traffic: its ending line wasn't found."""
    return {(g.get("payload") or {}).get("transaction") for lines in line_sets for g in lines
            if (g.get("payload") or {}).get("transaction") and (g.get("payload") or {}).get("originalUrl")}
INTERNAL_IP = re.compile(r"^(10\.|35\.191\.|130\.211\.|127\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.)")

PG_LABELS = {
    "id": "User ID", "email": "Email", "givenName": "First name", "familyName": "Last name", "gender": "Gender",
    "ageGroup": "Age group code", "createdAt": "Account created", "ip": "Last recorded IP address",
    "userAgent": "Last recorded device / browser", "city": "City (derived from IP)", "region": "Region (derived from IP)",
    "countryCode": "Country", "initialReferrer": "How you first arrived (referrer URL)", "deviceOS": "Device OS",
    "locale": "Language", "timezone": "Time zone", "stripeId": "Stripe customer ID",
}


class Pack:
    def __init__(self, out: str, tz: str, mp_tz: str):
        self.out, self.raw, self.export = out, f"{out}/raw", f"{out}/export"
        os.makedirs(self.export, exist_ok=True)
        self.tz, self.mp_tz = ZoneInfo(tz), ZoneInfo(mp_tz)
        self.written: list[str] = []

    def has(self, name: str) -> bool:
        return os.path.exists(f"{self.raw}/{name}")

    def json(self, name: str):
        with open(f"{self.raw}/{name}", encoding="utf-8") as f:
            return json.load(f)

    def jsonl(self, name: str) -> list[dict]:
        if not self.has(name):
            return []
        with open(f"{self.raw}/{name}", encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]

    def when(self, t) -> tuple[str, str]:
        if isinstance(t, (int, float)):
            t = dt.datetime.fromtimestamp(t, UTC)
        elif isinstance(t, str):
            t = dt.datetime.fromisoformat(t.replace("Z", "+00:00").replace(" +0000", "+00:00"))
            t = t if t.tzinfo else t.replace(tzinfo=UTC)
        return t.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"), t.astimezone(self.tz).strftime("%Y-%m-%d %H:%M:%S %Z")

    def mp_time(self, secs: int) -> dt.datetime:
        # The raw export reports wall-clock time in the project's timezone, not UTC.
        naive = dt.datetime.fromtimestamp(secs, UTC).replace(tzinfo=None)
        return naive.replace(tzinfo=self.mp_tz).astimezone(UTC)

    def write(self, name: str, header: list[str], rows: list[list]) -> None:
        with open(f"{self.export}/{name}", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
        self.written.append(name)


def build(p: Pack, whois: dict) -> dict:
    pg = p.json("postgres_user.json")
    stripe = p.has("stripe_customer.json")
    methods = p.json("stripe_payment_methods.json") if p.has("stripe_payment_methods.json") else []
    cus = p.json("stripe_customer.json") if stripe else None
    subs = p.json("stripe_subscriptions.json") if stripe else []
    invoices = p.json("stripe_invoices.json") if stripe else []
    intents = p.json("stripe_payment_intents.json") if stripe else []
    events = p.jsonl("mixpanel_events.jsonl")
    mp_profile = p.json("mixpanel_profile.json") if p.has("mixpanel_profile.json") else None
    logs = sorted(p.jsonl("gcp_signup.jsonl") + p.jsonl("gcp_window.jsonl"), key=lambda g: g.get("ts") or "")
    endings = p.jsonl("gcp_endings.jsonl")
    # raw/route_review.json: {transaction: "staff" | "own"}, the reviewer's verdict on each unresolved
    # request. It is read on every build, so a removed staff row stays removed when the pack is rebuilt.
    review = p.json("route_review.json") if p.has("route_review.json") else {}
    bad = {v for v in review.values()} - {"staff", "own"}
    if bad:
        raise SystemExit(f"raw/route_review.json: values must be \"staff\" or \"own\", got {sorted(bad)}")
    staff_tx = staff_transactions(logs, endings) | {tx for tx, v in review.items() if v == "staff"}
    routed_tx = routed_transactions(logs, endings) | set(review)
    unresolved: dict = {}

    def net(ip):
        w = whois.get(ip) or {}
        owner = w.get("descr") or w.get("orgname") or w.get("org-name") or w.get("netname")
        return f"{ip} ({owner})" if owner else ip

    # 01 — account and profile
    rows = []
    for k, v in pg.items():
        if v in (None, "", "Not found"):
            continue
        if k == "createdAt":
            v = p.when(v)[1]
        if k == "ip":
            v = net(v)
        rows.append([PG_LABELS.get(k, k), v, "Aura account database"])
    if cus:
        md = cus.get("metadata") or {}
        rows += [["Email on payment record", cus.get("email"), "Stripe"],
                 ["Country on payment record", md.get("countryCode"), "Stripe"],
                 ["Language on payment record", ",".join(cus.get("preferred_locales") or []) or md.get("locale"), "Stripe"]]
    if mp_profile:
        for k, v in sorted((mp_profile.get("$properties") or {}).items()):
            if v in (None, "", []) or MP_SKIP.match(k):
                continue
            rows.append([f"Analytics profile: {k.lstrip('$')}", json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v, "Mixpanel"])
    p.write("01_account_profile.csv", ["Field", "Value", "Source"], [r for r in rows if r[1] not in (None, "")])

    # 05 — analytics events
    if events:
        rows = []
        for e in events:
            pr = e["properties"]
            props = {k: v for k, v in pr.items() if not MP_SKIP.match(k) and v not in (None, "", [])}
            rows.append([*p.when(p.mp_time(pr["time"])), e["event"], recorded_by(pr),
                         pr.get("$current_url", ""), json.dumps(props, ensure_ascii=False, sort_keys=True)])
        p.write("05_activity_events.csv", ["Time (UTC)", "Time (local)", "Event", "Recorded by", "Page URL", "Event properties"], rows)

    # 06 — server request log
    token_ips = set()
    if logs:
        rows, seen = [], set()
        for g in logs:
            pl, h = g.get("payload") or {}, g.get("http") or {}
            if STAFF_ROUTE.search(str(pl.get("originalUrl", ""))) or pl.get("transaction") in staff_tx:
                continue
            msg = pl.get("message") or ""
            if LOG_DROP.match(msg):
                continue
            body_user = (pl.get("body") or {}).get("user") if isinstance(pl.get("body"), dict) else None
            tok = pl.get("token") if isinstance(pl.get("token"), dict) else {}
            ip = tok.get("client_ip") or h.get("remoteIp") or pl.get("ip") or ((body_user or {}).get("ip") if isinstance(body_user, dict) else None) or ""
            if tok.get("client_ip"):
                token_ips.add(tok["client_ip"])
            tx = pl.get("transaction")
            if tx and tx not in routed_tx and tx not in unresolved:
                unresolved[tx] = [p.when(g["ts"])[0] if g.get("ts") else "", g.get("container"), tx, ip, msg[:80]]
            if INTERNAL_IP.match(str(ip)):
                ip = ""
            what = msg
            for pat, label in LOG_LABELS:
                if re.match(pat, msg):
                    what = re.sub(pat + ".*", label, msg)
                    break
            if not what and h:
                what = f"{h.get('requestMethod', 'GET')} {re.sub(r'[?].*', '', h.get('requestUrl', ''))}"
            if not what or not (ip or h or g.get("container") in ("user-web", "payment-service")):
                continue
            key = ((g.get("ts") or "")[:19], what)
            if key in seen:
                continue
            seen.add(key)
            system = g.get("container") or ("Web load balancer" if g.get("resource") == "http_load_balancer" else g.get("resource"))
            rows.append([*p.when(g["ts"]), system, what[:200], ip, h.get("userAgent") or ""])
        p.write("06_server_request_log.csv", ["Time (UTC)", "Time (local)", "Aura system", "Action / request", "Client IP", "User agent"], rows)

    # 03/04 — billing
    charges = [c for pi in intents for c in (pi.get("charges") or {}).get("data", [])]
    if stripe:
        rows = []
        for i in sorted(invoices, key=lambda i: i["created"]):
            lines = "; ".join(l.get("description") or "" for l in (i.get("lines") or {}).get("data", []))
            rows.append([p.when(i["created"])[1], f"Invoice {i.get('number')} ({(i.get('billing_reason') or '').replace('_', ' ')})",
                         lines, f"{i['amount_paid'] / 100:.2f} {i['currency'].upper()}", i["status"], i["id"]])
        for c in charges:
            # `refunded` is true only for a full refund; amount_refunded is the fact (article-15.md).
            refunded = c.get("amount_refunded") or 0
            status = c["status"]
            if refunded:
                status += "; fully refunded" if refunded >= c["amount"] else f"; partially refunded ({refunded / 100:.2f} {c['currency'].upper()})"
            status += "; disputed" if c.get("disputed") else ""
            rows.append([p.when(c["created"])[1], "Card payment", f"{c.get('description') or ''} (statement: {c.get('calculated_statement_descriptor')})",
                         f"{c['amount'] / 100:.2f} {c['currency'].upper()}", status, c["id"]])
        for s in subs:
            if s.get("canceled_at"):
                reason = (s.get("cancellation_details") or {}).get("reason") or ""
                rows.append([p.when(s["canceled_at"])[1], "Subscription status changed to cancelled",
                             f"Stripe reason code: {reason or 'none'}; source of the change to be verified", "", s["status"], s["id"]])
        p.write("03_billing_history.csv", ["Date (local)", "Item", "Description", "Amount", "Status", "Reference"], rows)

        rows = []
        # Legacy card sources, Payment Methods API cards, and — when neither is readable — the card
        # details Stripe returned on each charge. One shape: brand, funding, last4, country, expiry.
        cards = [dict(s, _id=s.get("id")) for s in (cus.get("sources") or {}).get("data", []) if s.get("object") == "card"]
        cards += [dict(m.get("card") or {}, _id=m.get("id"), wallet=(m.get("card") or {}).get("wallet")) for m in methods]
        if not cards:
            cards = [dict(c["payment_method_details"]["card"],
                          _id=c.get("payment_method") or (c.get("source") or {}).get("id"))
                     for c in charges if (c.get("payment_method_details") or {}).get("card")]
        seen_cards = set()
        for src in cards:
            fp = (src.get("fingerprint") or src.get("last4"), src.get("exp_month"), src.get("exp_year"))
            if fp in seen_cards:
                continue
            seen_cards.add(fp)
            wallet = (src.get("wallet") or {}).get("type") or src.get("tokenization_method")
            rows += [["Payment method", f"{src.get('brand')} {src.get('funding')} card" + (f" via {wallet.replace('_', ' ').title()}" if wallet else "")],
                     ["Card last 4 digits", src.get("last4")], ["Card issuing country", src.get("country")],
                     ["Expiry", f"{src.get('exp_month'):02d}/{src.get('exp_year')}" if src.get("exp_month") else ""],
                     ["Device account number, last 4 (wallet)", src.get("dynamic_last4") or (src.get("wallet") or {}).get("dynamic_last4") or ""],
                     ["Stored payment token (held by Stripe)", src.get("_id")]]
        rows += [["Full card number", "Not held by Aura. Card data is held by our payment processor, Stripe."],
                 ["Stripe customer ID", cus["id"]]]
        for s in subs:
            price = s["items"]["data"][0]["price"]
            md = s.get("metadata") or {}
            amount = f"{price['unit_amount'] / 100:.2f} {price['currency'].upper()}" if price.get("unit_amount") is not None else ""
            trial = f", {md['trialDays']}-day free trial" if md.get("trialDays") else ""
            rows.append(["Billing agreement", f"Subscription {s['id']}: {amount} every {price['recurring']['interval_count']} "
                         f"{price['recurring']['interval']}{trial}, auto-renewing, status {s['status']}"])
        p.write("04_payment_method_and_tokens.csv", ["Field", "Value"], [r for r in rows if r[1] not in (None, "")])

    # 02 — timeline of milestones (the rest of the detail lives in 05/06)
    steps = []
    if pg.get("createdAt"):
        steps.append((p.when(pg["createdAt"]), "Account created", net(pg["ip"]) if pg.get("ip") else "", "Aura account database"))
    client = [e for e in events if recorded_by(e["properties"]) != "Aura server"]
    if client:
        steps.append((p.when(p.mp_time(client[0]["properties"]["time"])), f"First recorded activity: {client[0]['event']}", "", "Mixpanel"))
        steps.append((p.when(p.mp_time(client[-1]["properties"]["time"])), f"Last recorded activity: {client[-1]['event']}", "", "Mixpanel"))
    for e in events:
        if re.search(r"Subscription (View|Select|Processing|Processed)|Subscription purchase", e["event"]):
            steps.append((p.when(p.mp_time(e["properties"]["time"])), e["event"], "", "Mixpanel"))
    for g in logs:
        tok = (g.get("payload") or {}).get("token")
        if isinstance(tok, dict) and tok.get("client_ip") and "processing started" in ((g["payload"].get("message") or "").lower()):
            steps.append((p.when(g["ts"]), "Payment confirmed in the payment sheet; payment token created", net(tok["client_ip"]) + " (recorded by Stripe)", "Aura server logs; Stripe"))
    if cus:
        steps.append((p.when(cus["created"]), "Payment customer created and card saved", "", "Stripe"))
    for s in subs:
        steps.append((p.when(s["created"]), "Subscription created", "", "Stripe"))
        if s.get("trial_start"):
            steps.append((p.when(s["trial_start"]), "Free trial started", "", "Stripe"))
        if s.get("trial_end"):
            steps.append((p.when(s["trial_end"]), "Free trial ended", "", "Stripe"))
        if s.get("canceled_at"):
            steps.append((p.when(s["canceled_at"]), "Subscription status changed to cancelled (source to be verified)", "", "Stripe"))
    for c in charges:
        steps.append((p.when(c["created"]), f"Charged {c['amount'] / 100:.2f} {c['currency'].upper()}", "", "Stripe"))
    steps.sort(key=lambda x: x[0][0])
    p.write("02_timeline.csv", ["Time (UTC)", "Time (local)", "Step", "IP address", "Source"], [[*w, s, ip, src] for w, s, ip, src in steps])

    # 07 — AI call log (one row per IntelligenceService LLM call; no message text)
    if p.has("llm_calls.json"):
        rows = [[*p.when(c["createdAt"]), c.get("prompterName") or "", c.get("model") or "",
                 c.get("platform") or "", c.get("version") or "", c.get("conversationId") or "",
                 c.get("inputTokens"), c.get("outputTokens")] for c in p.json("llm_calls.json")]
        p.write("07_ai_call_log.csv", ["Time (UTC)", "Time (local)", "AI feature (prompter)", "Model",
                                       "Platform", "App version", "Conversation id", "Input tokens",
                                       "Output tokens"], rows)

    # Internal review list, outside export/ so it never reaches the zip: requests whose route is unknown
    # may be staff lookups (SKILL.md step 7).
    with open(f"{p.out}/review_unresolved_routes.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Time (UTC), as in 06", "System", "Transaction", "Client IP (before blanking)", "First message"])
        w.writerows(sorted(unresolved.values(), key=lambda r: r[0] or ""))

    # zip — reply + everything in export/, never raw/ or README.md
    zname = f"SAR_{dt.date.today().isoformat()}.zip"
    with zipfile.ZipFile(f"{p.out}/{zname}", "w", zipfile.ZIP_DEFLATED) as z:
        if os.path.exists(f"{p.out}/reply.md"):
            z.write(f"{p.out}/reply.md", "reply.md")
        for f in sorted(os.listdir(p.export)):
            z.write(f"{p.export}/{f}", f)
    return {"written": p.written, "zip": f"{p.out}/{zname}", "token_ips": sorted(token_ips),
            "client_events": len(client), "last_client_event": client[-1]["event"] if client else None,
            "unresolved_routes": len(unresolved)}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="pack directory (session scratchpad, never a repo)")
    ap.add_argument("--tz", required=True, help="the requester's IANA timezone, e.g. Europe/Dublin")
    ap.add_argument("--mixpanel-tz", default="America/Los_Angeles", help="Mixpanel project timezone (Tools/Mixpanel)")
    a = ap.parse_args()
    p = Pack(a.out, a.tz, a.mixpanel_tz)
    if not p.has("postgres_user.json"):
        raise SystemExit("raw/postgres_user.json is missing — save the user row first (SKILL.md step 2)")
    whois = p.json("ip_whois.json") if p.has("ip_whois.json") else {}
    print(json.dumps(build(p, whois), indent=1))


if __name__ == "__main__":
    main()
