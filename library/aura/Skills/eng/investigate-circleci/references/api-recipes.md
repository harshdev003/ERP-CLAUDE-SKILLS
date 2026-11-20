# CircleCI API Recipes

## Auth

All requests use `Circle-Token: <token>` header. No Bearer prefix.

```bash
curl -H "Circle-Token: $CIRCLE_TOKEN" https://circleci.com/api/v2/me
```

## v2 — Pipeline list for a branch

```
GET /api/v2/project/{slug}/pipeline?branch={branch}
```

Response: `{ "items": [...], "next_page_token": "..." }`

Each pipeline has: `id`, `number`, `status`, `vcs.branch`, `vcs.pull_requests`, `created_at`.

## v2 — Workflows in a pipeline

```
GET /api/v2/pipeline/{pipeline_id}/workflow
```

Response: `{ "items": [...] }` — each item has `id`, `name`, `status`, `created_at`.

Status values: `success`, `failed`, `failing`, `on_hold`, `canceled`, `running`, `not_run`.

## v2 — Jobs in a workflow

```
GET /api/v2/workflow/{workflow_id}/job
```

Response: `{ "items": [...] }` — each item has `id`, `name`, `status`, `job_number`, `duration` (seconds), `type`.

`job_number` maps to the v1.1 build number for log fetching.

## v1.1 — Build details + log

```
GET /api/v1.1/project/{slug}/{build_num}
```

Response includes: `build_url`, `steps[]`, each step has `name`, `actions[]`. Each action has `output_url`.

Fetch the output URL to get the log lines:

```
GET <output_url>   # returns [{message: "...", type: "out|err", ...}, ...]
```

## v2 — Rerun workflow

```
POST /api/v2/workflow/{workflow_id}/rerun
Body: {"from_failed": true}
```

**Requires a personal token.** Project tokens receive 403.

Response: `{ "workflow_id": "..." }` — the ID of the new workflow.

## Project slug format

`gh/{org}/{repo}` — e.g. `gh/AuraHealth/AuraConsumerApp`.

## Pagination (v2)

Pass `page-token=<next_page_token>` to get the next page. Stop when `next_page_token` is `null`.

## Rate limits

CircleCI v2 enforces rate limits. On 429, respect `Retry-After` header (seconds). The `_get()` helper in `circleci_api.py` handles this automatically with up to 3 retries.
