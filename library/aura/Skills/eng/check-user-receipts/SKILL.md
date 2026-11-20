---
name: check-user-receipts
description: >
  Look up a single Aura user's subscription receipts directly from PaymentService and print a
  readable summary — productId, provider, transactionId, autoRenew and autoRenewProductId,
  purchaseAt, expirationAt, paid state, receiptId. Takes a user ID and calls the checkUser
  receipts endpoint. Built for the CX pattern where a user reports they paid but the app is
  locked. Trigger on "check user receipts", "check receipts for {user-id}", "look up
  subscription for {user-id}", "what subscriptions does {user-id} have", "is this user
  paying", "user paid but doesn't have aiPremium", "subscription lockout", "checkUser for
  {user-id}", or a pasted user ID with a question about subscription state. Do NOT use for
  aggregate subscription analytics (`eng:analyze-payment-pending`), lookup by email or
  transaction ID (this skill is UID-only), or arbitrary Aura API calls (`eng:call-aura-api`).

metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Guide/Index           # baseline routing aid (universal)
    - Tools/Aura API        # canonical reference for Aura backend endpoints and conventions
    - Compliance/Privacy    # PII handling — user identity output is UID-only by default
---

# Check User Receipts

A thin wrapper around PaymentService's `POST /receipts/checkUser/:userId`.
Hand it a user ID — or a file of user IDs in `--batch` mode — and get back a
clean summary of every receipt the backend knows about for each user. Built
for fast CX-investigation triage: did the purchase reach PaymentService, what
SKU is it on, is it paid + auto-renewing?

## Modes

| Mode | Invocation | Use when |
|---|---|---|
| Single user | `check_receipts.py <user-id>` | Investigating one CX-reported account |
| Batch | `check_receipts.py --batch <uids-file> --csv <out> [--filter-missing-product SKU]` | Cohort sweep — feed a list of UIDs and get a CSV summary plus an optional list of lockout candidates (users without a receipt for the named SKU) |

## Goal

Given a user ID, print a one-screen summary of the user's subscription
receipts as PaymentService sees them, so the caller can decide whether the
issue is upstream (purchase never made it) or downstream (entitlement didn't
propagate to the user profile).

## Inputs

A single **Aura user ID** (the long alphanumeric string Aura uses as the
user's primary key, e.g. `KKBjkNBBBHShNfAVEoD41dwmdEk1`).

If the caller did not provide a user ID, ask for one before doing anything.
Do not guess from context.

## Required environment

- `AURA_API_TOKEN` — the team-issued Aura backend token (see `/CLAUDE.md` →
  "Secrets and environment"). Required. No MCP fallback exists for this
  endpoint, so a missing token is a hard fail — the skill prints a short
  install hint and stops.

## Workflow

### 1. Resolve the UID

Accept it from the caller's message, a clipboard paste, or a prior
conversation step. If absent, ask: *"Which user ID should I check?"* — and
wait. Don't try to look it up by email or any other identifier (out of
scope for v1).

### 2. Run the script

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/check-user-receipts/scripts/check_receipts.py <user-id>
```

The script:

- Reads `AURA_API_TOKEN` from env; bails with a friendly message if absent.
- Sends `POST https://services.aurahealth.io/receipts/checkUser/<uid>` with
  `Authorization: Bearer <token>` and an empty JSON body. The endpoint
  accepts `admin`, `service`, and `system` roles.
- Parses the response (the server keys each receipt by `<uid>-<suffix>`).
- Prints a markdown summary, one receipt per row, with timezone-aware ISO
  timestamps preserved verbatim.

Optional flags:

- `--json` — emit the raw response body instead of the rendered table. Use
  this when piping into another tool or for debugging unfamiliar payload
  shapes.
- `--batch <uids-file>` — read one user ID per line and fetch receipts for
  every UID in parallel. Mutually exclusive with the positional `uid`.
- `--csv <out-file>` — in batch mode, write a CSV summary with one row per
  user: `userId, status, hasReceipt, productIds, paid, autoRenew, purchaseAt,
  expirationAt, error`. Recommended for any batch run over 50 users.
- `--filter-missing-product <SKU>` — in batch mode, print to stdout the
  users whose `productIds` don't contain the named SKU substring. Useful for
  sweeping a Mixpanel-extracted cohort against the SKUs that should have a
  receipt (e.g. `ai_yearly_6999`).
- `--concurrency <N>` — parallel requests in batch mode (default 10).
  Increase for faster sweeps; keep modest to avoid hammering PaymentService.

Example batch run (1,329 UIDs → ~2 min wall-clock at concurrency 15):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/check-user-receipts/scripts/check_receipts.py \
  --batch outputs/affected-uids.txt \
  --csv outputs/affected-results.csv \
  --filter-missing-product ai_yearly_6999 \
  --concurrency 15 \
  > outputs/lockout-candidates.txt \
  2> outputs/batch-progress.log
```

### 3. Interpret the output

The script flags two anomalies inline (informational, not blocking):

- **No receipts found** — the user record exists upstream (otherwise the call
  would 404 or 401), but PaymentService has no purchases for them. Either
  they never paid, the purchase failed validation, or they purchased a SKU
  PaymentService doesn't know about (the PROD-1981 pattern).
- **Receipt is `paid: true` but expired** — `expirationAt` is in the past
  while `paid` is still true. Usually fine (paid history is retained), but
  worth noting when investigating a lockout.

Cross-checking `aiPremium` on the user's profile is **out of scope for v1**
— that would require a second authenticated call. Caller can run
`eng:call-aura-api` for `get user by id` if needed; this skill stays atomic.

### 4. Privacy

The UID is the only PII this skill emits. No email, name, or transaction
amounts beyond what's already in the receipt payload. If pasting the output
into Slack or Linear, the UID truncation rule from `Compliance/Privacy`
(first 8 chars + `…`) still applies — but the script does **not** truncate
automatically, since the UID is the search key the caller hands the skill
in the first place. The caller decides whether to share verbatim or
truncate before posting externally.

## Output

A markdown table like:

```
## Receipts for `KKBjkNB…`

| Receipt | Product | Provider | Paid | Auto-renew | Purchase | Expires | Transaction |
|---|---|---|---|---|---|---|---|
| ccf741bf… | ai_monthly_1999 | apple | ✓ | ✓ → ai_monthly_1999 | 2026-05-10T02:52:42Z | 2026-06-17T23:48:51Z | 710002771119345 |

_1 receipt._
```

When `--json` is passed, the raw JSON body from PaymentService is emitted
unmodified.

## Edge cases

- **Token missing** — print a one-line hint pointing at `/CLAUDE.md` →
  "Secrets and environment" and stop. Don't attempt the call.
- **401 / 403 from the endpoint** — surface the status code and response
  body verbatim; don't guess at the cause. The most common reason is a
  wrong-role token (the `system` role was added in PaymentService
  [`e791483`](https://github.com/AuraHealth/PaymentService/commit/e791483)
  released 2.185.2 on 2026-05-07).
- **404** — almost certainly a mistyped UID. Print the UID back to the
  caller and suggest double-checking; do not retry with a guess.
- **Empty response** (`{}` or `{<uid>-content: null}`) — print "No receipts
  found for `<uid>`." as the body; still a successful run.
- **Multiple receipts** — render one table row per receipt, sorted by
  `purchaseAt` descending (most recent first).
- **Re-run on the same UID** — pure read, no side effects. Idempotent by
  construction.

## References

- Source ticket: [PROD-1981](https://linear.app/aura-health/issue/PROD-1981/support-user-subscribed-to-monthly-plan-but-locked-out-of-aurie)
- Implementation card: [PROD-1996](https://linear.app/aura-health/issue/PROD-1996/build-check-user-receipts-skill-document-paymentservice-receipt-check)
- Tools/Aura API page in AI Knowledge — canonical reference for host, auth,
  and endpoint conventions.
