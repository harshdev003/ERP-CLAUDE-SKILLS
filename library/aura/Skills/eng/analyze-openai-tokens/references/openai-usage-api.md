# OpenAI Usage API — token-focused reference

Loaded on demand when the skill needs to extend to a new dimension or endpoint. Not pre-fetched by the workflow. For the cost-side endpoints (`/v1/organization/costs`, the `line_item` line shapes, empirical per-model rate calculation) see the equivalent reference under `eng:analyze-openai-costs/references/openai-usage-api.md`.

## Auth

All `/v1/organization/*` endpoints require an **Admin API key** (`sk-admin-...`), not a regular `OPENAI_API_KEY`.

- Created at: <https://platform.openai.com/settings/organization/admin-keys>
- Required role: Owner on the OpenAI org
- Header: `Authorization: Bearer sk-admin-...`

A regular `sk-proj-...` / `sk-...` key returns `401 invalid_api_key` on these endpoints.

## `GET /v1/organization/usage/completions`

Returns token counts for `chat.completions` and `responses` calls, aggregated into daily UTC buckets and optionally grouped by one dimension.

| Param | Type | Notes |
|---|---|---|
| `start_time` | int (Unix seconds) | inclusive |
| `end_time` | int (Unix seconds) | exclusive |
| `bucket_width` | string | `"1d"` is the only supported value today |
| `group_by[]` | string[] | `project_id`, `user_id`, `api_key_id`, `model`, `batch` — one dimension per call |
| `project_ids[]` | string[] | filter to specific projects |
| `models[]` | string[] | filter to specific models |
| `limit` | int | max 31 buckets per page for `bucket_width=1d` |
| `page` | string | pagination cursor from `next_page` |

**Response shape (group_by=model, abbreviated):**

```json
{
  "object": "page",
  "data": [
    {
      "object": "bucket",
      "start_time": 1715817600,
      "end_time": 1715904000,
      "results": [
        {
          "object": "organization.usage.completions.result",
          "input_tokens": 1234567,
          "input_cached_tokens": 234567,
          "input_audio_tokens": 0,
          "output_tokens": 345678,
          "output_audio_tokens": 0,
          "num_model_requests": 1234,
          "model": "gpt-5.4-2026-03-05"
        }
      ]
    }
  ],
  "next_page": null
}
```

### Token-field semantics

**Primary fields** (rendered by default — true top-level totals):

| Field | What it counts |
|---|---|
| `input_tokens` | Prompt tokens billed at the model's input rate. **Includes cached input tokens** — they're a subset, not a separate bucket. |
| `input_cached_tokens` | Subset of `input_tokens` that hit OpenAI's prompt cache (billed at ~10× cheaper). To get *non-cached* input: `input_tokens − input_cached_tokens` (or read `input_uncached_tokens` directly). |
| `input_audio_tokens` | Audio input tokens (Whisper-aware models, gpt-4o-audio-preview etc.). Zero for text-only calls. |
| `output_tokens` | Generation tokens including any internal reasoning tokens for reasoning models. As of late 2025 reasoning tokens are folded into this total at the org-usage level; the per-call API surfaces them under `usage.completion_tokens_details.reasoning_tokens` but the org endpoint typically does not split them out. |
| `output_audio_tokens` | Audio output tokens (TTS-aware models). Zero for text-only output. |

**Detail fields** (rendered under `--all-fields` — sub-decompositions of input/output, observed live on 2026-05-19):

| Field | What it counts |
|---|---|
| `input_text_tokens` | Text portion of `input_tokens`. For text-only calls equals `input_uncached_tokens`. |
| `input_image_tokens` | Image portion of `input_tokens` (image generation, vision models). Zero for text-only calls. |
| `input_uncached_tokens` | `input_tokens − input_cached_tokens`. Convenience field. |
| `input_cached_text_tokens` | Cached text input. Sub-slice of `input_cached_tokens`. |
| `input_cached_image_tokens` | Cached image input. Sub-slice of `input_cached_tokens`. |
| `input_cached_audio_tokens` | Cached audio input. Sub-slice of `input_cached_tokens`. |
| `output_text_tokens` | Text output. Sub-slice of `output_tokens`. |
| `output_image_tokens` | Image output (image-generation models). Sub-slice of `output_tokens`. |

**Don't sum detail fields with primary fields** — they double-count. The skill's `_row_total` helper sums `input_tokens + output_tokens` only, so the rendered Total column reconciles regardless of which set is on.

**Forward compatibility.** OpenAI has historically added new `*_tokens` fields to this endpoint without bumping the API version (e.g. when audio support landed). `scripts/report.py` picks up any field ending in `_tokens` dynamically — under `--all-fields`, new columns appear automatically. When that happens, update `PRETTY_TOKEN_LABEL` for a nicer header and decide whether the field belongs in `PRIMARY_TOKEN_FIELDS` (rendered by default — true top-level total) or `DETAIL_TOKEN_FIELDS` (sub-decomposition).

### Reasoning tokens — confirmed not available today

Live probe on 2026-05-19 verified across three endpoints:

| Endpoint | Has `reasoning_tokens`? | Notes |
|---|---|---|
| `/v1/organization/usage/completions` (this skill's source) | **No** — folded into `output_tokens`. Full row shape is 13 `*_tokens` fields; none break out reasoning. |
| `/v1/chat/completions` list & retrieve (`/v1/chat/completions/{id}`) | **No** — returns simplified usage (`total_tokens`, `prompt_tokens`, `completion_tokens`); the `completion_tokens_details` object surfaces in real-time SDK responses but not on the stored-completions API. |
| `/v1/responses` (Responses API) | **Yes** — `usage.output_tokens_details.reasoning_tokens` is exposed, but requires the `api.responses.read` Admin-key scope (current key 401s). |

This skill picks `usage/completions` because it's the only path that gives a clean per-day org-wide aggregate without per-call walks. Until reasoning lands there, the skill's policy is:

- The bundled `--all-fields` view shows all 13 fields the endpoint actually returns.
- The dynamic detection in `fetch_usage` picks up any new `*_tokens` field automatically — so if/when OpenAI adds `reasoning_tokens` to the org endpoint, the column appears with no code change. Update `PRETTY_TOKEN_LABEL` for a nicer header when that happens.
- If a user specifically asks for reasoning tokens, point them at this section and explain the options (wait for OpenAI, rotate the Admin key with the Responses scope and build a per-project walk, or pull from Braintrust traces which capture SDK-level usage).

## `GET /v1/organization/projects`

Lists all projects in the org. Used to map `project_id` → human-readable name in the by-project table.

| Param | Type | Notes |
|---|---|---|
| `limit` | int | max 100 |
| `after` | string | pagination cursor (project ID) |
| `include_archived` | bool | include archived projects (this skill passes `true`) |

Response: `{"data": [{"id": "proj_abc", "name": "aurie-prod", "status": "active"}, ...], "has_more": false}`.

## Sibling endpoints (not used here, but shaped the same)

If a future report needs to break out usage by service type, these are all available:

- `/v1/organization/usage/embeddings`
- `/v1/organization/usage/moderations`
- `/v1/organization/usage/images`
- `/v1/organization/usage/audio_speeches`
- `/v1/organization/usage/audio_transcriptions`
- `/v1/organization/usage/vector_stores`
- `/v1/organization/usage/code_interpreter_sessions`

Each has its own metering unit (per-image, per-second, etc.) and won't reconcile with completions. Don't sum across them without converting.

## Rate limits

The org-level endpoints share a single 60 req/min quota for the org. This skill makes at most three calls per run (one model-grouped usage call, one project-grouped usage call, one projects-list call) so a back-to-back retry is well inside the quota. The script paginates serially and retries on `429` with exponential backoff (max 5 attempts).

## Lag

The most recent daily bucket usually lags **1–4 hours** while OpenAI rolls up logs; in worse cases up to ~24h. The skill's default `--window yesterday` lands on the bucket that closed at today's UTC midnight — by morning standup that bucket is reliably propagated. `--window today` returns a partial-day bucket; the header in the rendered report flags this.

## Docs

- Usage API: <https://platform.openai.com/docs/api-reference/usage>
- Costs API (sibling, for $): <https://platform.openai.com/docs/api-reference/usage/costs>
- Admin keys: <https://platform.openai.com/docs/guides/production-best-practices/api-keys#admin-keys>
