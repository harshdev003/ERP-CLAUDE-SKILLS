---
name: coach-refund-context
description: Analyze Aura coach subscription credit history for support refund queries. Given a `coachSubscriptionCreditHistory` JSON (optionally with a Stripe subscription screenshot), this skill computes remaining credit balance, breaks it down per billing cycle, matches credits to Stripe payments, calculates a suggested refund amount in dollars, and produces both a per-cycle analysis table AND a ready-to-send drafted message for the support team. Always trigger this skill whenever Ashish shares credit history JSON, mentions "credits left for refund", "how much to refund", "support team asked about credits", or pastes `coachSubscriptionCreditHistory` data — even without explicitly saying "use the skill". Also trigger when a Stripe subscription screenshot accompanies credit data. Flags transfer credits, comped credits, multi-cycle dormancy, and time-sensitive renewals.
---

# Coach Subscription Refund Context

When Ashish gets a support-team query about how many credits a user has left for a refund, this skill turns the raw `coachSubscriptionCreditHistory` JSON into a clean, structured summary that's ready to forward to support — plus an analysis table for his own reference.

## Inputs

**Always required:**
- A `coachSubscriptionCreditHistory` JSON blob (array of credit/debit events).

**Often included, use when present:**
- A Stripe subscription screenshot showing: customer email, plan name (Coach_119 / Coach_139 / Coach_209 / Coach_219 etc.), monthly price, subscription ID, status (Active / Cancels on X), next invoice date and amount, current period dates.
- A Stripe payments list screenshot showing succeeded/refunded/failed charges per cycle.

If a Stripe screenshot is missing, proceed with the JSON alone and note in the output that payment amounts couldn't be matched — but **do not** ask Ashish to provide one unless something in the JSON is genuinely ambiguous (e.g. an unexplained transfer chain).

## Core analysis

### 1. Compute totals
- Sum all `type: "credit"` counts → total credited
- Sum all `type: "debit"` counts → total debited
- Remaining = credited − debited

### 2. Group by billing cycle
A "cycle" starts at each `credit` event with reason `start` or `renewal`. Debits that occur *after* a credit event and *before* the next credit event belong to that cycle. For each cycle, compute: date, reason, credits issued, credits used, credits remaining.

### 3. Identify credit duration & plan
The `duration` field on credit events tells you session length (30 = 30-min credits typical of Coach_119/139/219; 60 = 60-min credits typical of Coach_209). Combined with the per-cycle issuance count (1 / 2 / 4) and the Stripe screenshot price, you can name the plan.

### 4. Match to payments (if Stripe screenshot present)
Map each `start` / `renewal` credit event to a paid Stripe charge by date proximity. Note any refunded charges separately — those credits aren't "paid value" anymore.

## Patterns to flag

Run through these checks and surface any that apply. These are the patterns Ashish has consistently needed to call out to support:

- **Transfer credits**: `reason: "transfer"` events indicate credits carried over from a previous subscription. These are NOT paid value on the current sub. If remaining credits include transferred ones, separate them: "X credits from current paid cycles + Y credits transferred from prior subs."
- **Manual "refund"-reason credits**: `reason: "refund"` on a `type: "credit"` event means support previously comped credits to this user. Note these as "comped" — they're not paid value either.
- **Multi-cycle dormancy**: If the user has 2+ consecutive renewal cycles with 0 usage, call this out explicitly with the dollar total of unused cycles. This is the strongest refund signal.
- **Time-sensitive upcoming renewal**: If the Stripe screenshot shows an upcoming invoice and the sub is NOT scheduled to cancel, flag that support should cancel before the next charge.
- **More sessions taken than paid for**: If total debits exceed total non-comped credits, the user got extra value — weak refund case. Flag it.
- **Chained sub transfers on the same day**: Multiple sub IDs in the JSON with `transfer` events all dated the same day means the user did plan changes. Untangle which sub is current (the one with renewal events continuing) and which are historical.

## Refund amount calculation

Compute a suggested refund amount based on **unused paid credits on the current subscription**. This is what support will most often want.

### The calculation

1. Identify the cycle price from the Stripe screenshot (e.g. $219/month for Coach_219).
2. Identify credits-per-cycle from the JSON (count on a `start` or `renewal` event — typically 1, 2, or 4).
3. Per-credit value = cycle price ÷ credits-per-cycle.
4. For each cycle, count unused paid credits (credits issued minus credits used in that cycle, floor at 0).
5. **Refund amount = sum of unused paid credits × per-credit value**, rounded to the nearest dollar.

### Important exclusions

Do NOT count these toward the refund amount — they aren't "paid value":
- **Transfer credits** (`reason: "transfer"`, type credit) — carried from prior subs.
- **Comped credits** (`reason: "refund"`, type credit) — manually granted by support.
- **Already-refunded charges** — if the Stripe screenshot shows a charge was refunded, exclude that cycle's credits from paid value.

### Two refund views to present

Give support two numbers so they can pick based on policy:

- **Strict (unused paid credits only)**: just the dollar value of unused credits from paid, non-refunded renewals on the current sub. This is the conservative number.
- **Full unused cycles**: sum of cycle prices for any renewal where 0 credits were used. This is what support typically refunds when the user clearly stopped using the service — they get the full price back for any cycle they didn't touch at all, regardless of per-credit math.

If the two numbers are the same (e.g. user never used anything from those cycles), just present one. If they differ (e.g. user used 1 of 4 credits in a cycle), show both and explain.

### Examples

- $219/month, 4 credits/cycle = $54.75/credit. 8 unused paid credits → strict refund $438. If those 8 are spread across 2 fully unused cycles → full-cycle refund also $438. Same number.
- $139/month, 4 credits/cycle = $34.75/credit. 25 unused credits across 7 cycles where 3 credits were used (spread across 3 different cycles). Strict: 25 × $34.75 = $869. Full unused cycles: 4 cycles × $139 = $556 (only the cycles with zero usage count toward full-cycle refund). Show both and let support pick.
- $209/month, 2 credits/cycle, 8 remaining where 6 are transferred + 2 are from the May renewal (cycle fully unused). Strict: 2 × $104.50 = $209. Full unused cycles: 1 × $209 = $209. Same number. Note the 6 transferred credits separately as "not paid value on this sub."

## Refund strength assessment

After the analysis, give a one-line read on how strong the refund case is:

- **Strong** — multi-cycle dormancy with no recent usage; user is owed something fair.
- **Moderate** — partial cycle unused, or last cycle fully unused.
- **Weak / none** — user consumed all or more than they paid for; no unused paid credits.

This isn't a recommendation to refund or not — it's a read on the data that helps support prioritize.

## Output format

Every output — both the inline analysis and the drafted message — must lead with these two numbers prominently:

- **Unused credits**: total remaining credit count (e.g. "18 unused credits")
- **Refund amount**: recommended dollar amount (e.g. "$985.50")

These are the two questions support always asks. They must appear in the first 2-3 lines of any response, before the cycle breakdown.

Produce **two things** in this order:

### 1. Inline analysis (for Ashish)

Lead-in (first line): user email + headline numbers. Example:
> For **<redacted-email>**: **18 unused credits**, suggested refund **$985.50**.

Then a markdown table with columns: Cycle | Date | Paid | Issued | Used | Remaining. Add a totals row.

Then 1-3 short paragraphs covering:
- Refund math shown explicitly (per-credit value, strict view, full-cycle view)
- Key observations from the pattern checks (transfer credits, dormancy, etc.)
- Subscription status and any time-sensitive renewal
- Refund strength read

### 2. Drafted message via `message_compose_v1`

Use `message_compose_v1` with `kind: "other"`. Produce TWO variants. Both must include unused credits and refund amount in the opening lines.

- **Full context**: starts "Hi team,". Opens with a 2-3 line summary block stating subscription, unused credits, and recommended refund. Then full cycle-by-cycle breakdown, subscription details, totals, flagged patterns, and an explicit "Suggested refund:" block showing both strict and full-cycle views with the math. Ends "Let me know how you'd like to proceed."
- **Concise**: 5-7 bullets. First two bullets MUST be `• Unused credits: X` and `• Recommended refund: $Y`. Then context bullets.

`summary_title`: `"Refund context for <email>"`.

### Required structure for the "Suggested refund" block

In the full-context message, present the refund as:

```
Suggested refund (two views):
• Strict (X unused credits × $Y/credit): $Z
• Full unused cycles only (N cycles × $price): $W

Recommended: $Z (strict view). The user consumed only $V of value from $total paid.
```

Always show both numbers even if they're equal — support needs to see the math.

## After the message

After calling `message_compose_v1`, add one short follow-up line if (and only if) there's something time-sensitive or unusual support should know — e.g., "Worth canceling before the May 30 renewal to avoid another $219 charge" or "The 6 transferred credits aren't paid value on this sub, so refund scope depends on policy." Don't pad with generic advice.

## Style notes

- Money figures: always include currency (`$209`), use the price shown in Stripe.
- Dates: use the format from the Stripe screenshot (e.g. "30 May 2026" or "1 Jun") — don't reformat.
- Plan naming: refer to it the way Stripe does ("Coach_219", "$219/month").
- Subscription IDs: include both the Stripe sub ID (sub_xxx) and internal subscription UUID from the JSON in the full-context message.
- Be specific, not vague: "0 credits used since 13 Jan" beats "low recent usage."
- Don't speculate about *why* the user wants a refund or whether one should be granted. The skill provides facts; the support team decides.

## Worked examples

### Example A — strong refund case (multi-cycle dormancy)

User on $219/month, 4 credits per cycle, paid 5 cycles, used only 2 credits (both early in cycle 1). Last 4 renewals had 0 usage. Subscription still active with upcoming renewal.

→ Table shows the dormancy cleanly. Flag: "4 consecutive renewals with 0 usage ($876 total)." Refund strength: Strong. Follow-up: "Subscription still active — worth canceling before next renewal."

### Example B — transfer credit complication

User on $209/month with `transfer` credit event of +6 on day 1, plus normal start (+2) and one renewal (+2). 2 credits used.

→ Table separates the 6 transferred credits from the 4 paid-cycle credits. Flag: "Of 8 remaining, 2 belong to the May renewal ($209), 6 were transferred from prior subs (no paid value on this sub)." Refund strength: Moderate, but scope depends on policy.

### Example C — comped credits / weak case

User had `start: +1`, `use: -1`, then two `refund`-reason credits (each followed by a use), then renewal +1 and a use. Net: 0 remaining, but user took 3 sessions in cycle 1 against 1 paid credit (2 were comped).

→ Note: "3 sessions taken against 1 paid credit; 2 comped via manual refund credits." Refund strength: Weak — user got more value than paid for.
