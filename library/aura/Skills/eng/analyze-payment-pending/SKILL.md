---
name: analyze-payment-pending
description: >
  Fetches all Stripe subscriptions in a payment-pending state (past_due, incomplete, unpaid),
  classifies each as Aurie or Aura (via metadata.campaign with product-name fallback), computes
  how long each user has been in pending (from the open invoice's created date), and reports a
  headline (total + by-app + by-status), a time-in-pending stats block per app, a decline-code
  summary, a per-user detail table sorted by days-in-pending, and action items including a
  flag for users approaching Stripe's ~21-day Smart-Retry auto-cancel cliff. Trigger when
  the user says: "analyze payment pending", "who is on payment pending", "payment pending
  breakdown", "how many users are payment pending", "how long have they been in pending",
  "aura vs aurie payment pending", "stripe decline reasons", "payment pending by app",
  "payment pending report".
metadata:
  version: "3.0.0"
knowledge:
  reads:
    - Guide/Index
    - Tools/Stripe
---

# Analyze Payment Pending

Queries Stripe for all subscriptions in a payment-pending state, classifies them per app
(Aurie / Aura) from metadata + product, derives time-in-pending from the open invoice, and
reports a grouped summary plus per-user detail.

## Step 0 — Check prerequisites

```bash
test -n "$STRIPE_API_KEY" && echo "OK" || echo "MISSING: STRIPE_API_KEY not set"
python3 -c "import requests" 2>/dev/null && echo "OK" || echo "MISSING: pip3 install requests"
```

If `STRIPE_API_KEY` is missing, ask the user to add it to their Claude global env via the
`update-config` skill (restricted key with read on Subscriptions / Invoices / PaymentIntents /
Customers / Products is sufficient — see `Tools/Stripe` in AI Knowledge), then restart
Claude Code.

## Step 1 — Resolve parameters (ask the user at start)

Before running, ask the user three questions via `AskUserQuestion`. Reasonable defaults are
shown — if the user says "just run it" or similar, take all defaults.

1. **App scope** — `aurie` / `aura` / `all`. Default: `all`. Classification uses
   `metadata.campaign` first (values `aurie_web` / `aura_web`), then falls back to the product
   name on the first line item. Subs that match neither bucket land in `unknown` — surfacing
   them as a coverage gap is a feature, not a bug.
2. **Statuses** — which pending statuses to include. Default: `past_due incomplete unpaid`.
   Narrow to `past_due` if the user only cares about the grace-window cohort.
3. **Mode** — `full` (fetch decline reasons per invoice; slower) vs `fast` (skip the
   per-invoice payment-intent expansion; ~10x faster; no decline codes / attempt counts).
   Default: `full`.

Carry the chosen values forward as `--app`, `--statuses`, and (if fast) `--skip-decline`.
For very large result sets, optionally pass `--top N` to cap the detail table.

Backward-compat: the older `--campaign aurie_web` flag still works and maps to `--app aurie`.

## Step 2 — Fetch subscriptions

Run the script:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-payment-pending/scripts/report.py \
  --app aurie \
  --statuses past_due
```

Or full sweep across both apps with decline codes (slowest, most informative):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-payment-pending/scripts/report.py
```

Fast mode (no decline codes — useful for a quick count + days-in-pending view):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-payment-pending/scripts/report.py --skip-decline
```

What the script does:

1. Lists all Stripe subscriptions in the requested statuses (no metadata filter on the API call
   — bucketing happens client-side so subs without proper `campaign` tagging surface as
   `unknown`).
2. Classifies each as `aurie` / `aura` / `unknown` via `metadata.campaign`, falling back to the
   product name on the first line item (cached per product ID).
3. For each, fetches its latest open invoice. In `full` mode, also expands `payment_intent` to
   read `last_payment_error.decline_code` and `attempt_count`.
4. Computes `days_in_pending = now - invoice.created` (days).
5. Emits the report described in Step 3.

## Step 3 — Render the report

The script prints these sections; surface them as-is in chat (markdown-friendly):

1. **Headline** — total count in payment_pending, plus breakdowns by app (with median / p90 /
   max days-in-pending per app) and by status.
2. **Decline-code summary** (skipped in fast mode) — count per decline code, with a one-line
   meaning and whether retries are likely to help.
3. **Per-user detail** — sorted by days_in_pending desc. In fast mode includes email / app /
   status / days / since-date / notes; in full mode adds decline code / attempts / next retry.
4. **Action items** — structural-vs-recoverable counts (full mode), a ≥18-day cliff warning
   (within ~3 days of Stripe's ~21-day Smart-Retry auto-cancel), and email-typo flags for
   users whose dunning emails are bouncing.

Reference: the Smart Retry policy + 21-day cancellation window come from Stripe's billing
config — full breakdown lives in `Tools/Stripe` in AI Knowledge.

## Step 4 — Offer follow-ups

After rendering, suggest:

- **Drill into the unknowns** — if `app=unknown` count is non-trivial, those are subs missing
  `metadata.campaign` AND not matching the product-name heuristic. Worth fixing at checkout.
- **Email the recoverables** — users with `insufficient_funds` / `do_not_honor` and ≥3 days
  in pending often need a nudge to update their card before the 21-day cliff.
- **Audit the cliff cohort** — users ≥18 days in pending are about to auto-cancel; if any
  are high-LTV, manual outreach may save them.
- **Record empirical confirmation** — if any user has aged through the full window (~21 days),
  their timestamps can populate the "Stripe empirical confirmation" section of `Tools/Stripe`
  (analogous to the Apple confirmation row in `Grace Periods`).
