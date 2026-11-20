#!/usr/bin/env python3
"""Compute the pause schedule + Linear card plan for pause-coaching-subscription.

Deterministic-first (best-practices principle 9): all date math — the resume
date, the per-month renewal dates, and the card specs (title / assignee / due
date) — lives here, not in an LLM step. The skill calls this, shows the plan,
waits for approval, then performs the Stripe pause + Linear card creation.

Stdlib only — no third-party deps so it runs anywhere Python 3 does.

Usage:
  python3 plan_pause.py \
    --first-renewal 2026-06-25 \
    --months 3 \
    --subscription-id sub_1Psq... \
    --user-id <uid> --coach-id <cid> \
    [--resumes-at 2026-09-25T00:00:00.000Z] \
    [--price "139 USD/month"] \
    [--credit-assignee Ashish] [--payout-assignee Jerome]

Prints a JSON plan to stdout.
"""

import argparse
import json
import sys
from datetime import date, datetime, timedelta


def _last_day_of_month(year: int, month: int) -> int:
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return (nxt - timedelta(days=1)).day


def add_months(d: date, n: int) -> date:
    """Add n calendar months to d, clamping the day to the target month's last day."""
    month_index = d.month - 1 + n
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(d.day, _last_day_of_month(year, month)))


def iso_midnight_utc(d: date) -> str:
    """Render a date as the Stripe-style instant the endpoint expects."""
    return d.strftime("%Y-%m-%dT00:00:00.000Z")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="Plan a coaching-subscription pause + Linear cards.")
    ap.add_argument("--first-renewal", required=True,
                    help="YYYY-MM-DD of the first renewal that falls inside the pause window")
    ap.add_argument("--months", type=int, required=True, help="number of months to pause")
    ap.add_argument("--subscription-id", required=True)
    ap.add_argument("--user-id", required=True)
    ap.add_argument("--coach-id", required=True)
    ap.add_argument("--resumes-at", default=None,
                    help="explicit resume instant (ISO). If omitted, computed as first-renewal + months")
    ap.add_argument("--price", default="", help="human-readable price, e.g. '139 USD/month'")
    ap.add_argument("--credit-assignee", default="Ashish",
                    help="assignee for the 'Remove the credit on renewal' cards")
    ap.add_argument("--payout-assignee", default="Jerome",
                    help="assignee for the 'Remove the payout' cards")
    args = ap.parse_args()

    if args.months < 1:
        print(json.dumps({"error": "months must be >= 1"}))
        sys.exit(1)

    try:
        first = datetime.strptime(args.first_renewal, "%Y-%m-%d").date()
    except ValueError:
        print(json.dumps({"error": f"bad --first-renewal {args.first_renewal!r}; use YYYY-MM-DD"}))
        sys.exit(1)

    # Optional explicit resume instant — validate up front, same as --first-renewal,
    # so a malformed value returns a JSON error instead of an uncaught ValueError.
    resume_override = None
    if args.resumes_at:
        try:
            resume_override = datetime.strptime(args.resumes_at[:10], "%Y-%m-%d").date()
        except ValueError:
            print(json.dumps(
                {"error": f"bad --resumes-at {args.resumes_at!r}; expected ISO starting YYYY-MM-DD"}))
            sys.exit(1)

    # Resume on the next renewal after the paused window, unless the caller pinned it.
    computed_resume = add_months(first, args.months)
    resume_date = resume_override or computed_resume
    resumes_at = args.resumes_at or iso_midnight_utc(computed_resume)

    if resume_date <= first:
        print(json.dumps({
            "error": f"resume date {resume_date.isoformat()} must be after the "
                     f"first renewal {first.isoformat()}"}))
        sys.exit(1)

    # Paused renewals = every monthly renewal anchored at `first` that falls
    # strictly before the resume date. Deriving these from resume_date (not from
    # --months directly) keeps the credit/payout cards covering exactly the same
    # window Stripe is paused for, even when --resumes-at is overridden to a date
    # other than first + months.
    paused_renewals = []
    i = 0
    while add_months(first, i) < resume_date:
        paused_renewals.append(add_months(first, i))
        i += 1
    months_paused = len(paused_renewals)

    cards = []
    for d in paused_renewals:
        month_name = d.strftime("%b")          # Nov, Dec, ...
        day_ord = f"{d.day}th"                  # plain ordinal suffix; good enough for card titles
        # 1) credit removal — assigned to the credit-assignee, due on the renewal date
        cards.append({
            "kind": "credit",
            "title": f"Remove the credit on renewal {month_name} {day_ord}",
            "assignee": args.credit_assignee,
            "dueDate": d.isoformat(),
        })
        # 2) payout removal — assigned to the payout-assignee, due on the renewal date
        cards.append({
            "kind": "payout",
            "title": f"Remove the payout for {month_name} for this coach",
            "assignee": args.payout_assignee,
            "dueDate": d.isoformat(),
        })

    plan = {
        "subscriptionId": args.subscription_id,
        "userId": args.user_id,
        "coachId": args.coach_id,
        "price": args.price,
        "months": months_paused,
        "monthsRequested": args.months,
        "firstRenewal": first.isoformat(),
        "pausedRenewals": [d.isoformat() for d in paused_renewals],
        "resumesAt": resumes_at,
        "verifyOn": resume_date.isoformat(),
        "parentTitle": f"Pause coach subscription for {months_paused} month(s)",
        "stripeCall": {
            "method": "POST",
            "path": "/subscriptions/stripe/pause",
            "body": {"subscriptionId": args.subscription_id, "resumesAt": resumes_at},
        },
        "cards": cards,
    }
    print(json.dumps(plan, indent=2))


if __name__ == "__main__":
    main()
