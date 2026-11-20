#!/usr/bin/env python3
"""
Fetch Stripe subscriptions in payment-pending state, classify each as Aurie / Aura
(via metadata.campaign with product-name fallback), compute how long each has been
in pending (from the open invoice's created date), and report:

  1. Headline counts — total + by app + by status
  2. Time-in-pending stats per app (median, p90, max)
  3. Decline-code summary (skipped if --skip-decline)
  4. Per-user detail (sorted by days_in_pending desc, optionally capped)
  5. Action items

Usage:
  python3 report.py                                       # all apps, all statuses, full detail
  python3 report.py --app aurie                           # Aurie only
  python3 report.py --app aura --statuses past_due        # Aura, past_due only
  python3 report.py --skip-decline                        # ~10x faster, no decline codes
  python3 report.py --top 20                              # cap detail table to top 20

Backward-compatible flags:
  --campaign aurie_web  (kept for older callers — equivalent to --app aurie)
"""

import argparse
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip3 install requests")
    sys.exit(1)

STRIPE_API = "https://api.stripe.com/v1"

DECLINE_DESCRIPTIONS = {
    "insufficient_funds":      ("Card exists, not enough money",              "Maybe — if user tops up before next retry"),
    "transaction_not_allowed": ("Card doesn't support subscriptions/recurring","No — user must update payment method"),
    "do_not_honor":            ("Generic bank decline",                        "Maybe"),
    "pickup_card":             ("Card flagged / reported lost or stolen",      "No"),
    "invalid_account":         ("Account closed or invalid",                   "No"),
    "expired_card":            ("Card expired",                                "No — user must update payment method"),
    "incorrect_cvc":           ("Incorrect CVC",                               "No — user must update payment method"),
    "card_velocity_exceeded":  ("Too many recent charges on this card",        "Maybe — Stripe will retry later"),
    "lost_card":               ("Card reported lost",                          "No"),
    "stolen_card":             ("Card reported stolen",                        "No"),
}

# metadata.campaign → app bucket
CAMPAIGN_APP = {
    "aurie_web": "aurie",
    "aura_web":  "aura",
}


def stripe_get(path, params=None, api_key=None):
    r = requests.get(
        f"{STRIPE_API}{path}",
        params=params or {},
        auth=(api_key, ""),
        headers={"Stripe-Version": "2023-10-16"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def list_subscriptions(statuses, api_key):
    """List all subs in the given statuses (no metadata filter — bucket client-side
    so subs without proper campaign tagging show up in the 'unknown' bucket)."""
    subs = []
    for status in statuses:
        after = None
        while True:
            params = {"status": status, "limit": 100, "expand[]": "data.items.data.price"}
            if after:
                params["starting_after"] = after
            data = stripe_get("/subscriptions", params, api_key)
            batch = data.get("data", [])
            subs.extend(batch)
            if not data.get("has_more"):
                break
            after = batch[-1]["id"] if batch else None
    return subs


def classify_app(sub, product_name_cache, api_key):
    """Return 'aurie' / 'aura' / 'unknown' for a subscription.

    Order of preference:
      1. metadata.campaign  (aurie_web / aura_web)
      2. product name on the first line item (cached)
    """
    campaign = (sub.get("metadata") or {}).get("campaign", "")
    if campaign in CAMPAIGN_APP:
        return CAMPAIGN_APP[campaign]

    items = (sub.get("items") or {}).get("data", [])
    if not items:
        return "unknown"
    price = items[0].get("price") or {}
    product_ref = price.get("product")
    if not product_ref:
        return "unknown"
    # `product` may be an expanded object or just an ID string
    if isinstance(product_ref, dict):
        name = (product_ref.get("name") or "").lower()
    else:
        if product_ref not in product_name_cache:
            try:
                p = stripe_get(f"/products/{product_ref}", None, api_key)
                product_name_cache[product_ref] = (p.get("name") or "").lower()
            except Exception:
                product_name_cache[product_ref] = ""
        name = product_name_cache[product_ref]

    if "aurie" in name or name.startswith("ai ") or " ai " in name:
        return "aurie"
    if "aura" in name:
        return "aura"
    return "unknown"


def get_open_invoice(customer_id, subscription_id, api_key, expand_pi=True):
    params = {
        "customer": customer_id,
        "subscription": subscription_id,
        "status": "open",
        "limit": 1,
    }
    if expand_pi:
        params["expand[]"] = "data.payment_intent"
    data = stripe_get("/invoices", params, api_key)
    invoices = data.get("data", [])
    return invoices[0] if invoices else None


def fmt_date(ts):
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %-d")


def fmt_days(d):
    if d is None:
        return "—"
    return f"{d:.0f}d"


def looks_like_typo(email):
    if not email:
        return False
    suspicious = ["gnail.com", "gmial.com", "gmal.com", "yaho.com", "homail.com", "outlok.com"]
    return any(email.endswith(s) for s in suspicious)


def main():
    # Force UTF-8 stdout — report contains arrows, em-dashes, and other non-ASCII
    # that would raise UnicodeEncodeError under Windows' default cp1252.
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser()
    parser.add_argument("--app", choices=["aurie", "aura", "all"], default="all",
                        help="Which app to include (default: all). Classification uses metadata.campaign first, product name fallback.")
    parser.add_argument("--campaign", default=None,
                        help="DEPRECATED — use --app. metadata.campaign filter, e.g. aurie_web.")
    parser.add_argument("--statuses", nargs="+", default=["past_due", "incomplete", "unpaid"],
                        help="Statuses to include (default: past_due incomplete unpaid)")
    parser.add_argument("--skip-decline", action="store_true",
                        help="Skip the per-invoice payment-intent expansion (~10x faster, no decline codes / attempt counts).")
    parser.add_argument("--top", type=int, default=0,
                        help="Cap the per-user detail table to the top N longest-in-pending (default: 0 = no cap).")
    args = parser.parse_args()

    api_key = os.environ.get("STRIPE_API_KEY")
    if not api_key:
        print("ERROR: STRIPE_API_KEY environment variable not set.")
        sys.exit(1)

    # Backward compat: --campaign maps to --app
    if args.campaign and args.app == "all":
        args.app = CAMPAIGN_APP.get(args.campaign, "all")

    print(f"Fetching Stripe subscriptions: statuses={args.statuses} app={args.app} decline_lookups={'no' if args.skip_decline else 'yes'}\n", flush=True)

    t0 = time.time()
    subs = list_subscriptions(args.statuses, api_key)
    t_list = time.time() - t0
    if not subs:
        print("No subscriptions found matching criteria.")
        return

    print(f"Listed {len(subs)} subscription(s) in {t_list:.1f}s. Classifying + fetching invoice details...\n", flush=True)

    product_name_cache = {}
    rows = []
    now_ts = time.time()
    for i, sub in enumerate(subs, 1):
        app = classify_app(sub, product_name_cache, api_key)
        if args.app != "all" and app != args.app:
            continue

        cus_id = sub["customer"]
        sub_id = sub["id"]
        status = sub.get("status")
        invoice = get_open_invoice(cus_id, sub_id, api_key, expand_pi=not args.skip_decline)
        if not invoice:
            continue

        email = invoice.get("customer_email") or "unknown"
        attempt_count = invoice.get("attempt_count", 0)
        next_attempt = invoice.get("next_payment_attempt")
        invoice_created = invoice.get("created")
        days_in_pending = ((now_ts - invoice_created) / 86400.0) if invoice_created else None

        decline_code = "—"
        if not args.skip_decline:
            pi = invoice.get("payment_intent") or {}
            last_error = pi.get("last_payment_error") or {}
            decline_code = last_error.get("decline_code") or last_error.get("code") or "unknown"

        rows.append({
            "email": email,
            "sub_id": sub_id,
            "app": app,
            "status": status,
            "decline_code": decline_code,
            "attempt_count": attempt_count,
            "next_attempt": next_attempt,
            "invoice_created": invoice_created,
            "days_in_pending": days_in_pending,
            "typo": looks_like_typo(email),
        })

        if i % 50 == 0:
            print(f"  ...processed {i}/{len(subs)} subs ({len(rows)} matching {args.app})", flush=True)

    if not rows:
        print("No open invoices found for matching subscriptions.")
        return

    # --- Headline ---
    print("\n## Headline\n")
    print(f"**Total in payment_pending: {len(rows)}**")
    by_app = defaultdict(list)
    for r in rows:
        by_app[r["app"]].append(r)
    by_status = defaultdict(int)
    for r in rows:
        by_status[r["status"]] += 1

    print("\n### By app")
    for app in ("aurie", "aura", "unknown"):
        n = len(by_app.get(app, []))
        if n == 0:
            continue
        days = [r["days_in_pending"] for r in by_app[app] if r["days_in_pending"] is not None]
        if days:
            med = statistics.median(days)
            p90 = sorted(days)[max(0, int(len(days) * 0.9) - 1)]
            mx  = max(days)
            print(f"- **{app}**: {n} users · median {med:.0f}d · p90 {p90:.0f}d · max {mx:.0f}d in pending")
        else:
            print(f"- **{app}**: {n} users (no invoice timestamps)")

    print("\n### By status")
    for s, n in sorted(by_status.items(), key=lambda x: -x[1]):
        print(f"- `{s}`: {n}")

    # --- Decline code summary (if collected) ---
    if not args.skip_decline:
        counts = defaultdict(int)
        for r in rows:
            counts[r["decline_code"]] += 1

        print("\n## Decline-code summary\n")
        print(f"{'Decline Code':<30} {'Count':>5}  {'What it means':<45}  {'Will Stripe retry help?'}")
        print("-" * 120)
        for code, count in sorted(counts.items(), key=lambda x: -x[1]):
            desc, retry = DECLINE_DESCRIPTIONS.get(code, ("Unknown decline code", "Unknown"))
            print(f"`{code}`{' ' * max(0, 28 - len(code))} {count:>5}  {desc:<45}  {retry}")

    # --- Per-user detail ---
    sorted_rows = sorted(rows, key=lambda x: -(x["days_in_pending"] or 0))
    if args.top and args.top > 0:
        shown = sorted_rows[:args.top]
        print(f"\n## Per-user detail (top {args.top} by days in pending)\n")
    else:
        shown = sorted_rows
        print("\n## Per-user detail (sorted by days in pending desc)\n")

    if args.skip_decline:
        print(f"{'Email':<42} {'App':<8} {'Status':<11} {'Days':>5}  {'Since':<8}  Notes")
        print("-" * 100)
        for r in shown:
            notes = "⚠️ email typo" if r["typo"] else ""
            print(f"{r['email'][:42]:<42} {r['app']:<8} {r['status']:<11} {fmt_days(r['days_in_pending']):>5}  {fmt_date(r['invoice_created']):<8}  {notes}")
    else:
        print(f"{'Email':<38} {'App':<7} {'Days':>5}  {'Decline':<26} {'Attmpt':>6}  {'Next':<8}  Notes")
        print("-" * 120)
        for r in shown:
            notes = "⚠️ email typo" if r["typo"] else ""
            print(f"{r['email'][:38]:<38} {r['app']:<7} {fmt_days(r['days_in_pending']):>5}  `{r['decline_code']}`{' ' * max(0, 24 - len(r['decline_code']))} {r['attempt_count']:>6}  {fmt_date(r['next_attempt']):<8}  {notes}")

    # --- Action items ---
    print("\n## Action items\n")
    if not args.skip_decline:
        no_retry = {c for c, (_, retry) in DECLINE_DESCRIPTIONS.items() if retry.startswith("No")}
        structural = [r for r in rows if r["decline_code"] in no_retry]
        recoverable = [r for r in rows if r["decline_code"] not in no_retry and r["decline_code"] != "unknown" and r["decline_code"] != "—"]
        unknowns    = [r for r in rows if r["decline_code"] in ("unknown", "—")]
        print(f"- {len(structural)} user(s) have structural card issues and will never recover via retry — need to update payment method")
        print(f"- {len(recoverable)} user(s) may recover on next Stripe retry")
        print(f"- {len(unknowns)} user(s) have unknown / missing decline codes — check Stripe Dashboard")

    # Highlight users near the 21-day cliff (Stripe will auto-cancel after Smart Retries exhaust)
    near_cliff = [r for r in rows if r["days_in_pending"] and r["days_in_pending"] >= 18]
    if near_cliff:
        print(f"- ⏳ {len(near_cliff)} user(s) are ≥18 days into pending — within ~3 days of Stripe's auto-cancel (Smart Retry window is ~21d):")
        for r in sorted(near_cliff, key=lambda x: -x["days_in_pending"])[:10]:
            print(f"    • {r['email']} — {fmt_days(r['days_in_pending'])} ({r['app']}, since {fmt_date(r['invoice_created'])})")

    typos = [r for r in rows if r["typo"]]
    if typos:
        print(f"- ⚠️  {len(typos)} user(s) have email typos — Stripe dunning emails are bouncing:")
        for r in typos:
            print(f"    • {r['email']} ({r['sub_id']})")


if __name__ == "__main__":
    main()
