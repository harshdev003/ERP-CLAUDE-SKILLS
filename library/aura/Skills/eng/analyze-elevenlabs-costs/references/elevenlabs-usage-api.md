# ElevenLabs Usage API — quick reference

Notes for future maintenance of `eng:analyze-elevenlabs-costs`. The script `scripts/report.py` handles all of this — read this if you need to extend it.

Upstream docs: <https://elevenlabs.io/docs/api-reference/usage/character-stats>

## Endpoints used

| Endpoint | Purpose | Required scope (UI label) |
|---|---|---|
| `GET /v1/usage/character-stats` | Per-window usage in either `credits` (characters) or `cost` (USD). | **Workspace Analytics → Access**. Without it the endpoint returns 401. |
| `GET /v1/workspace/api-keys` | Map `api_key_id` → `name` for the `breakdown_type=api_keys` view. | **Workspace → Read**. 401/403 is non-fatal — the script falls back to raw IDs. |

Auth header is **`xi-api-key: <key>`** — not `Authorization: Bearer`.

Keys come from either of two places in the ElevenLabs UI:

- **Developer API keys** (<https://elevenlabs.io/app/developers/api-keys>) — tied to a user account. Right choice for local/personal Claude usage.
- **Service accounts** (<https://elevenlabs.io/app/developers/service-accounts>) — workspace-owned, not tied to a user. Right choice for CI or shared automation. Each service account has its own bag of API keys with their own scopes.

## `/v1/usage/character-stats` parameters

| Param | Type | Notes |
|---|---|---|
| `start_unix` | int (ms) | **Milliseconds**, not seconds. Required. |
| `end_unix` | int (ms) | Half-open. Required. |
| `breakdown_type` | string | `none`, `voice`, `user`, `api_keys`, `product_type`, `model`, `resource`, `request_queue`, `voice_add_edit`, `source`, `request_id`. The script exposes a curated subset via `--breakdown`. |
| `aggregation_interval` | string | `hour`, `day`, `week`, `month`, `cumulative`. Script always passes `day` so the caller can sum client-side and partial-day windows behave. |
| `metric` | string | Live API enum: `credits`, `tts_characters`, `minutes_used`, `request_count`, `ttfb_avg`, `ttfb_p95`, `fiat_units_spent`, `concurrency`, `concurrency_average`. The script calls the endpoint twice per window — `credits` (per-bucket credit consumption, rendered in markdown) and `fiat_units_spent` (per-bucket consumption value **returned in cents** — the script divides by 100 → real USD in `cost_usd` / `total_cost_usd`; surfaced in JSON only, hidden from markdown to avoid reading as the invoice). The `raw` JSON field preserves the unmodified response, so its cost numbers are still in cents — divide by 100 if you read `raw` directly. (Fixed in PROD-2477; the value reconciles within ~1% of the dashboard "Cost before overage surcharge" tile.) `minutes_used` is also a valid metric — useful for ad-hoc queries — but not called by this skill since it reports credits-denominated actuals only. (Heads up: ElevenLabs' public docs still reference `cost` for the dollar metric. That value 422s — `fiat_units_spent` is what the live API accepts.) |
| `include_workspace_metrics` | bool | `true` widens the response to all workspace members when the key has admin scope; silently ignored on personal keys. |

## Response shape

```json
{
  "time": [1700000000000, 1700086400000, ...],
  "usage": {
    "<bucket-name>": [v_t0, v_t1, ...],
    "<bucket-name>": [v_t0, v_t1, ...]
  }
}
```

- `time[i]` is a Unix-ms timestamp; values across all buckets share the same `time` array.
- For `breakdown_type=none`, `usage` has a single key (typically `"All"`).
- Series may contain `null` for buckets with no data — the script's `sum_by_bucket` coerces them to 0.
- Bucket names for `breakdown_type=api_keys` are raw key IDs. Name resolution is best-effort via the second endpoint above.

## Cost vs credits — the important caveat

`metric=cost` is **only populated on plans where ElevenLabs has a per-character-cost agreement with the workspace** (Enterprise, Growth, Scale). On simpler plans the response either omits `cost` or returns zeros, and the user has to multiply characters by their negotiated rate themselves.

The script detects this case (`total_cost == 0 && total_credits > 0`) and surfaces a callout in the rendered markdown rather than silently misreporting.

## Rate limits

The Usage API is generously rate-limited (it's a workspace-admin endpoint, not a user-facing one). `_request` retries with exponential backoff on 429 up to 5 times, honouring `Retry-After`.

## Things the script does NOT do (yet)

- **Per-Firebase-UID drill-down.** ElevenLabs has no `metadata.user` equivalent today — the request side doesn't tag with our user IDs. Adding `user_id` propagation to ElevenLabs request metadata is a separate ticket before a `eng:analyze-elevenlabs-cost-per-user` sibling skill can exist.
- **Daily run-rate charting.** The `--json` output retains the per-day arrays under `raw.cost` / `raw.credits`, so a follow-up skill that wants to render a chart has the data without needing to refetch.
- **Currency translation.** All amounts are assumed USD. If ElevenLabs ever returns another currency it would surface in their response shape (not currently exposed) — the script does not translate.
- **Cross-window deltas in the JSON.** The combined view computes the run-rate ratio at render time but does not persist it. Re-derive from `daily_cost_usd` if a downstream skill needs it.
