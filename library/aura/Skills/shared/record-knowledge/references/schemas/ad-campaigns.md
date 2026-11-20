# Schema — `Learnings/Ad Campaigns`

**Status:** placeholder. Authoritative schema is being finalised under Linear [PROD-1215](https://linear.app/aura-health/issue/PROD-1215). Update this file when it lands.

Rows are write-once. Each row captures one campaign's creative, spend, and outcome — distilled after the campaign ended or a variant test closed.

## Fields

All [base-schema](../base-schema.md) fields apply. In addition:

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | string | yes | Short name, e.g. `Grief vertical — March 2026 — Meta video A/B` |
| `summary` | string | yes | 1–2 sentence framing |
| `learning` | string | yes | The takeaway |
| `vertical` | enum | yes | From [base-schema § vertical](../base-schema.md#vertical) |
| `creative_ref` | string | yes | Link or ID for the creative asset (Figma, Drive, ad manager) |
| `channel` | `meta` \| `tiktok` \| `google` \| `youtube` \| `reddit` | yes | Where it ran |
| `spend_usd` | number | yes | Total spend across the flight |
| `ctr` | number | yes | Observed CTR (0.0–1.0) |
| `cpa_usd` | number | yes | Observed cost per acquisition |
| `winning_variant` | string \| null | no | Variant label if a test ran; null/omit if single-creative |
| `flight_start` | ISO date | no | When the campaign started |
| `flight_end` | ISO date | no | When the campaign ended |
| `audience` | string | no | Audience / placement / targeting note |

## Example row

```yaml
title: "Grief vertical — March 2026 — Meta video A/B"
summary: "Tested empathetic voiceover vs testimonial voiceover on Meta Reels, grief vertical."
learning: "Testimonial variant had 1.8× CTR but 1.3× CPA — lower cost-efficiency despite higher engagement. Ship the empathetic variant for cost, save testimonial for retargeting."
vertical: grief
creative_ref: "https://figma.com/…/grief-march-2026"
channel: meta
spend_usd: 12400.0
ctr: 0.032
cpa_usd: 18.40
winning_variant: empathetic_voiceover
flight_start: 2026-03-04
flight_end: 2026-03-25
audience: "Lookalike 1% — grief events, 25-55, US"
sources:
  - label: "Meta Ads Manager — campaign 823991"
    url: "https://business.facebook.com/…"
    primary: true
  - label: "Figma — creative set"
    url: "https://figma.com/…"
```

## Validation rules

1. All required fields present.
2. `channel` is one of the allowed values (hard error otherwise).
3. `ctr` is between 0 and 1 (warn if outside — might be a unit mistake).
4. `spend_usd` and `cpa_usd` are non-negative numbers.
5. `flight_end` (if given) is on or after `flight_start`.
6. `vertical` matches canonical list (warn, not error).
7. `sources` has at least one entry.

## Open questions (resolve in PROD-1215)

- Do we capture `impressions` / `clicks` / `conversions` explicitly, or only derived ratios?
- Multi-channel campaigns — one row per channel, or aggregated?
- How do we represent multi-variant tests with > 2 variants? Separate DB? Extra field?
- Retention cohort tied to this campaign — link via `cohort_id`?

Treat this schema as provisional until PROD-1215 finalises it.
