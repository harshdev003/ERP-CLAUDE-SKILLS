# OpenAI Usage & Costs API — quick reference

Loaded on demand when the skill author needs to extend the report (new dimension, new endpoint, etc.). Not pre-fetched by the workflow.

## Auth

All `/v1/organization/*` endpoints require an **Admin API key**, not a regular API key.

- Admin key prefix: `sk-admin-...`
- Created at: <https://platform.openai.com/settings/organization/admin-keys>
- Required role: Owner on the OpenAI org
- Header: `Authorization: Bearer sk-admin-...`

A regular `sk-proj-...` / `sk-...` key returns `401 invalid_api_key` on these endpoints.

## Endpoints used by this skill

### `GET /v1/organization/costs`

Returns dollar costs grouped into time buckets.

| Param | Type | Notes |
|---|---|---|
| `start_time` | int (Unix seconds) | inclusive |
| `end_time` | int (Unix seconds) | exclusive |
| `bucket_width` | string | `"1d"` is the only supported value today |
| `group_by[]` | string[] | `project_id`, `line_item` |
| `project_ids[]` | string[] | filter to specific projects |
| `limit` | int | max 180 buckets per page |
| `page` | string | pagination cursor from `next_page` |

**Response shape:**

```json
{
  "object": "page",
  "data": [
    {
      "object": "bucket",
      "start_time": 1714521600,
      "end_time": 1714608000,
      "results": [
        {
          "object": "organization.costs.result",
          "amount": { "value": 12.34, "currency": "usd" },
          "project_id": "proj_abc",
          "line_item": null
        }
      ]
    }
  ],
  "next_page": "page_..."
}
```

Costs are reported in USD. Daily buckets lag the live dashboard by up to ~24h — the most recent bucket may not be closed yet.

### `GET /v1/organization/projects`

Lists all projects in the org. Used to map `project_id` → human-readable name.

| Param | Type | Notes |
|---|---|---|
| `limit` | int | max 100 |
| `after` | string | pagination cursor (project ID) |
| `include_archived` | bool | include archived projects |

### `GET /v1/organization/usage/completions`

Token usage for `chat.completions` and `responses` endpoints. Use this for per-API-key breakdowns since `/costs` does not support `group_by[]=api_key_id`.

| Param | Type | Notes |
|---|---|---|
| `start_time` / `end_time` / `bucket_width` / `limit` / `page` | — | same as `/costs` |
| `group_by[]` | string[] | `project_id`, `user_id`, `api_key_id`, `model`, `batch` |
| `project_ids[]` | string[] | filter to specific projects |

**Per-row fields** (selected): `input_tokens`, `output_tokens`, `input_cached_tokens`, `num_model_requests`, `project_id`, `user_id`, `api_key_id`, `api_key_name`, `model`.

**Important:** this endpoint does NOT return dollar amounts. To convert tokens to dollars you'd need to multiply by per-model pricing — but pricing isn't on a public API endpoint, so the conversion is brittle (manual price table that drifts when OpenAI updates rates). v1 of this skill reports tokens only and tells the user.

## Back-calculating empirical per-token rates (`--by-model`)

Even though no endpoint returns `$/token` directly, you can derive it by dividing:

- `/organization/costs?group_by=line_item` — returns `$` keyed by line items shaped `"<model>, input"`, `"<model>, cached input"`, `"<model>, output"`.
- `/organization/usage/completions?group_by=model` — returns `input_tokens`, `input_cached_tokens`, `output_tokens` per model.

For each model:

```
non_cached_input_tokens = input_tokens − input_cached_tokens
input_rate_per_1M       = (spend["<m>, input"]        / non_cached_input_tokens) * 1_000_000
cached_input_rate_per_1M = (spend["<m>, cached input"] / input_cached_tokens)    * 1_000_000
output_rate_per_1M      = (spend["<m>, output"]       / output_tokens)           * 1_000_000
```

These rates reflect Aura's **actual** billing including any negotiated discount (currently ~12–13% under OpenAI's public list prices on the gpt-5.x family). The numbers are exact within the window's daily-bucket resolution; rounding-noise is sub-cent on million-token volume.

### Pitfalls

- **Non-token services masquerade as token line items.** Image generation models (`gpt-image-1`, `gpt-image-2-*`) emit `line_item = "<model> image"` and `"<model> text"` rows in `/costs` but bill per-image and don't appear in `/usage/completions`. Filter them out by requiring `token_total > 0`.
- **Tiny windows give noisy rates.** Daily buckets close on a ~24h lag, so a freshly-launched model can have $0 cost while showing tokens (or vice versa) until the bucket closes. The script drops rows with `spend_total < $1` to suppress that noise.
- **Cached input pricing is separately metered.** Don't fold cached input dollars into the regular input divisor — they have their own line item and their own (typically ~10×-cheaper) price.

## Sibling endpoints (not used yet, but available)

- `/v1/organization/usage/embeddings`
- `/v1/organization/usage/moderations`
- `/v1/organization/usage/images`
- `/v1/organization/usage/audio_speeches`
- `/v1/organization/usage/audio_transcriptions`
- `/v1/organization/usage/vector_stores`
- `/v1/organization/usage/code_interpreter_sessions`

All share the `start_time` / `end_time` / `bucket_width` / `group_by` shape. Add them to the script if a future report needs to break costs down by service type.

## Rate limits

The org-level endpoints share a single 60 req/min quota. The script paginates serially and retries on `429` with exponential backoff (max 5 attempts).

## Docs

- Costs: <https://platform.openai.com/docs/api-reference/usage/costs>
- Usage: <https://platform.openai.com/docs/api-reference/usage>
- Admin keys: <https://platform.openai.com/docs/guides/production-best-practices/api-keys#admin-keys>
