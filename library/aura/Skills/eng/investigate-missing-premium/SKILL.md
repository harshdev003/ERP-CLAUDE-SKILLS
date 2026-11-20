---
name: investigate-missing-premium
description: >
  Investigate why a single Aura user may be missing premium access they're entitled to —
  across both Aurie (an `ai` subscription driving the premiumAI flag) and Aura content (a
  `content` subscription driving the premium flag). Cross-checks the canonical subscription
  array against the user record's premium flags and returns a per-type verdict: OK,
  LOCKED-OUT, STALE-PREMIUM, or CORRECTLY-NOT-PREMIUM. When the verdict is LOCKED-OUT or
  STALE-PREMIUM, suggests a ready-to-run `eng:investigate-gcp-logs` filter so the next step is
  one paste away. Built for the recurring CX-triage pattern where a user reports they paid but
  the app is locked. Trigger on "investigate missing premium for {uid}", "why isn't {uid}
  premium", "user {uid} paid but no premium", "subscription lockout investigation", "is {uid}
  locked out", "premium entitlement check for {uid}", or a pasted user ID with a question
  about premium, paid or locked-out state. Do NOT use for receipt-only inspection
  (`eng:check-user-receipts`).

metadata:
  version: "3.1.1"
knowledge:
  reads:
    - Guide/Index                # baseline routing aid (universal)
    - Tools/Aura API             # backend host + auth conventions
    - Tools/Aura API/Subscriptions  # canonical subscription state
    - Tools/Aura API/Receipts    # sibling receipt-side endpoint
    - Tools/Aura API/Products    # product catalog — missing row = lockout root cause
    - Compliance/Privacy         # PII handling — UID-only by default
---

# Investigate Missing Premium

Hand a single user ID. The skill resolves "is this user in the state we
expect?" by composing the canonical subscription array with the user
record's premium flags, and tells you which way any mismatch points.

## Goal

Given one Aura user ID, return a one-screen verdict per subscription type
(`ai`, `content`) plus an overall verdict word and — when there's a
mismatch — a copy-pasteable GCP-logs filter to investigate root cause.

## Inputs

A single **Aura user ID** (long alphanumeric string, e.g.
`KKBjkNBBBHShNfAVEoD41dwmdEk1`). No app hint required — the skill checks
both Aurie and Aura sides.

If the caller did not provide a user ID, ask for one before doing anything.
Don't try to look it up by email or any other identifier.

## Required environment

- `AURA_API_TOKEN` — service-level Aura backend token (see
  `/CLAUDE.md` → "Secrets and environment"). Required for the
  `/subscriptions/list/:userId` call (admin / service / system roles
  accepted). Missing token is a hard fail.

The `/users/:userId` call is currently unauthenticated per
`Tools/Aura API/Users`, so the user-record fetch needs no token.

## Workflow

### 1. Resolve the UID

Accept from the caller's message or a clipboard paste. If absent, ask:
*"Which user ID should I investigate?"* — and wait.

### 2. Run the script

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-missing-premium/scripts/investigate.py <user-id>
```

The script:

- Sends `POST https://services.aurahealth.io/subscriptions/list/<uid>` with
  `Authorization: Bearer ${AURA_API_TOKEN}` and an empty JSON body.
- Sends `GET https://services.aurahealth.io/users/<uid>` (no auth).
- For each unique provider in the subscriptions, sends
  `GET https://services.aurahealth.io/products/<provider>?limit=0` to fetch the
  registered product catalog. Used to detect the PROD-1981 root cause:
  a subscription that references a `productId` not present in the
  `product` table will silently fail entitlement sync.
- For each subscription `type` with a user-flag mirror (`ai` → `premiumAI`,
  `content` → `premium`), classifies the state:

  | Sub state | Flag value | Verdict |
  |---|---|---|
  | active (paid + non-expired, or lifetime) | true | **OK** |
  | active | false | **LOCKED-OUT** (PROD-1981 pattern) |
  | none or expired | false | **CORRECTLY-NOT-PREMIUM** |
  | none or expired | true | **STALE-PREMIUM** (missed cancellation/refund webhook) |

- Renders a per-type markdown table that includes an **In catalog** column
  per subscription, plus an overall verdict word. When a subscription
  references a product missing from the catalog, the report emits a
  ready-to-run SQL `INSERT` for the missing row(s) — that's the
  PROD-1981-style direct fix. When the verdict is LOCKED-OUT or
  STALE-PREMIUM, the report also prints a copy-paste GCP-logs filter
  scoped to the user.

Optional flags:

- `--json` — emit the raw subscriptions array + user record as JSON.
  Useful for piping into another tool or for debugging an unfamiliar
  response shape.
- `--quiet` — suppress the markdown report; print only the overall
  verdict word (`OK` / `LOCKED-OUT` / `STALE-PREMIUM` /
  `CORRECTLY-NOT-PREMIUM`). Designed for shell scripting / batch loops.

### 3. Interpret the verdict

- **OK** — the user is correctly premium for at least one type, no
  intervention needed.
- **LOCKED-OUT** — active subscription but premium flag is false. The
  PROD-1981 root-cause pattern: receipt validated but entitlement never
  propagated to the user record. If the report's "In catalog" column
  shows `✗ MISSING` for the affected subscription, apply the emitted
  SQL `INSERT` and the next entitlement-sync tick will resolve the
  lockout. If the product *is* in the catalog but the user is still
  locked out, run the suggested GCP-logs filter to find the failing
  call (validation 500, missing role, etc.) and hand the failing
  receipt ID to backend for re-sync.
- **STALE-PREMIUM** — no active subscription but premium flag is still
  true. Usually means a refund / expiration / cancellation webhook
  didn't fire (or fired without effect). Run the suggested filter and
  look for the missed-webhook event around the subscription's
  `expirationAt`.
- **CORRECTLY-NOT-PREMIUM** — no active sub, no flag set. Expected
  state for a free user.

When the user has both `ai` and `content` types, the overall verdict
worsens to whichever per-type verdict is most severe (LOCKED-OUT >
STALE-PREMIUM > OK > CORRECTLY-NOT-PREMIUM).

### 4. Privacy

The UID is the only PII this skill emits. The output retains the full
UID (caller needs it for follow-up calls); apply the standard
`Compliance/Privacy` UID-prefix truncation rule if the report is being
pasted into Slack / Linear / Notion. The script does not auto-truncate.

## Output

A markdown verdict block. Example for the PROD-1981 user, after the
product registration fix:

```
## Premium investigation: `KKBjkNBBBHShNfAVEoD41dwmdEk1`

| Type | Sub state | Provider | Product | In catalog | Paid | Expires | User flag | Flag value | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| ai | active | apple | ai_monthly_1999 | ✓ | ✓ | 2026-06-17T23:48:51.000Z | `premiumAI` | ✓ | **OK** |
| content | none | — | — | — | — | — | `premium` | ✗ | **CORRECTLY-NOT-PREMIUM** |

**Overall verdict:** OK
```

For a LOCKED-OUT case where the product is missing from the catalog (the
PROD-1981 pattern), the report appends a `### Root cause` section with a
ready-to-run `INSERT INTO "product" ...` statement so the operator can fix
it without hand-crafting the SQL.

## Edge cases

- **Token missing** — print install hint, exit 2. No retries.
- **`/subscriptions/list` 403** — token doesn't carry an accepted role
  (`admin`, `service`, `system`). Surface the response body verbatim.
- **`/users/:userId` 404** — the user record doesn't exist. PaymentService
  may still know about the user via subscriptions, but the cross-check
  can't run; surface the 404 with the UID echoed so the caller can
  double-check.
- **Subscription array is empty** — both per-type rows show "none"; if
  the user record's flags are also false, the verdict is
  CORRECTLY-NOT-PREMIUM (the expected state for a free user).
- **Non-mirrored subscription types** (`coaching`, `business`,
  `community`, `coach`) — shown for visibility in the table but no
  verdict assigned (these types don't mirror to a user-record flag).
- **Multiple subscriptions of the same type** — picks the most recent
  non-deleted row by `createdAt`. The historical rows are ignored.
- **Lifetime subs** — `lifetime: true` is treated as active regardless of
  `expirationAt`.
- **Re-run on the same UID** — pure read, no side effects. Idempotent.

## References

- Related skill: [`eng:check-user-receipts`](../check-user-receipts/SKILL.md) —
  receipt-side view of the same investigation (when you want the
  PaymentService receipt detail rather than the subscription summary).
- Related skill: [`eng:investigate-gcp-logs`](../investigate-gcp-logs/SKILL.md) —
  the natural follow-up when this skill returns LOCKED-OUT or
  STALE-PREMIUM. The script prints a ready-to-paste filter.
- Source ticket: [PROD-1981](https://linear.app/aura-health/issue/PROD-1981/support-user-subscribed-to-monthly-plan-but-locked-out-of-aurie) —
  the CX-reported subscription lockout that surfaced the pattern this
  skill collapses into one invocation.
- Implementation: [PROD-2001](https://linear.app/aura-health/issue/PROD-2001/build-investigate-missing-premium-skill).
