# OpenAI stored-completions recipes for investigate-openai-trace

The exact requests each mode runs, plus the gotchas worth knowing if you ever want to hand-write a one-off query.

> **Migrated off Braintrust 2026-06-05** (PROD-2464, umbrella PROD-2456). This skill used to query the Braintrust BTQL endpoint; it now reads OpenAI's stored chat-completions. Auth is dual-key (PROD-3572): the `OPENAI_ADMIN_API_KEY` (Admin key, Owner role) is used **only** for the `/v1/organization/projects` discovery call; stored-completions reads use per-project service-account keys `OPENAI_<PROJECT>_API_KEY` — the Admin key can't hold the `model.request` scope OpenAI enforces on completions reads. Neither is `BRAINTRUST_API_KEY`.

## Endpoints

All reads go to `https://api.openai.com/v1`. Auth is split by endpoint (PROD-3572): the `/organization/projects` discovery call authenticates with `Authorization: Bearer $OPENAI_ADMIN_API_KEY`, while the per-project completions reads (`/chat/completions`, `/{id}`, `/{id}/messages`) authenticate with the project-scoped `Authorization: Bearer $OPENAI_<PROJECT>_API_KEY` — the Admin key can't hold the `model.request` scope those reads require. The env var name is derived from the project's display name: `Memory [Production]` → `OPENAI_MEMORY_API_KEY`, `Conversation [Production]` → `OPENAI_CONVERSATION_API_KEY`, `Journal [Production]` → `OPENAI_JOURNAL_API_KEY`. Every per-project read also needs the project header:

```
OpenAI-Project: <proj_id>
```

| Purpose | Request |
|---|---|
| List the org's projects | `GET /v1/organization/projects?limit=100&include_archived=false` |
| List completions in a project | `GET /v1/chat/completions` (+ project header, + `metadata[...]` filters) |
| Retrieve one completion | `GET /v1/chat/completions/{id}` (+ project header) |
| Get a completion's input messages | `GET /v1/chat/completions/{id}/messages` (+ project header) |

## The metadata model

Every completion carries a flat `metadata` dict written by the Intelligence Service:

| Field | Meaning | Braintrust-era name |
|---|---|---|
| `user` | Firebase UID | `metadata.userId` |
| `name` | the prompter (e.g. `coach-prompter`, `memory-ingest-prompter`) | `metadata.prompterName` |
| `transaction` | thread/session id (**filter only — never group by**) | `metadata.transaction` |
| `conversationId` | conversation id | `metadata.conversationId` |
| `dialogueId` | per-turn / per-invocation id | `metadata.dialogueId` |
| `model`, `service`, `version`, `platform`, `premium`, `aiPremium`, `aiPremiumState` | request context | same |

The structured guardrail envelope, when a prompter emits one, is the assistant message content: `choices[0].message.content` — a JSON string like `{"result":"guardrail","seriousHarmFlagSet":true,"seriousHarmEvidence":"…"}`. The system prompt (incl. any `## User-Provided Summary` block) is the system-role message returned by `/messages`.

## Server-side metadata filters (verified 2026-06-05)

Pass `metadata[<key>]=<value>` query params on the list endpoint. They filter **server-side** and combine as **AND**:

```
GET /v1/chat/completions?metadata[transaction]=<uuid>
GET /v1/chat/completions?metadata[name]=memory-ingest-prompter&metadata[transaction]=<uuid>
GET /v1/chat/completions?metadata[user]=<firebase-uid>
GET /v1/chat/completions?metadata[conversationId]=<uuid>
GET /v1/chat/completions?metadata[dialogueId]=<uuid>
```

Always prefer a server-side metadata filter over a client-side walk-and-filter when one exists.

## Pagination — hard cap at 100

`limit` max is **100**. Passing `limit > 100` silently returns an **empty page** (a real debugging trap). Paginate with the cursor:

```
?limit=100                 # first page
?limit=100&after=<last_id> # subsequent pages
```

Stop when `has_more` is false. The next cursor is the response `last_id` (fall back to the last item's `id`).

## No server-side time filter

There is no `created_after` / `created_before`. `created` is **unix seconds**. For time-bounded queries (Mode 1 triage, user pivot) walk newest-first and stop once you page past your `since` bound — the list endpoint returns newest-first, so once a page's oldest item is older than `since`, you're done.

## Project resolution — a completion can only be read against its owning project

`GET /chat/completions/{id}` returns **404** with the wrong project header (or none), **200** with the correct one. A bare `chatcmpl-…` id does NOT encode its project. So to resolve an id (Mode 3, URL follow) you **fan out** across the production projects, calling `GET /chat/completions/{id}` on each until one returns 200.

## Mode 1 — Triage

Walk one project newest-first, optionally server-side-filtered by prompter, client-side time-bounded:

```
GET /v1/chat/completions?limit=100[&metadata[name]=<prompter>]
OpenAI-Project: <proj_id>
```

The skill then applies `--status` (parse the envelope, match `result` or `seriousHarmFlagSet`) and `--root-only` (keep only completions that carry a guardrail envelope) client-side, since neither is expressible as a metadata filter.

## Mode 2 — Focus (transaction fan-out)

For **every** production project:

```
GET /v1/chat/completions?metadata[transaction]=<uuid>[&metadata[name]=<prompter>]
OpenAI-Project: <proj_id>
```

Collect matches, group by project, drop projects with 0 rows. Because `metadata[transaction]` is a server-side filter, a project with 0 rows genuinely has no completion for that transaction.

## Mode 3 — Trace (one completion)

Either retrieve directly (with `--project`):

```
GET /v1/chat/completions/<chatcmpl-id>
OpenAI-Project: <proj_id>
```

…or fan out across projects until one returns 200. Then fetch the input messages:

```
GET /v1/chat/completions/<chatcmpl-id>/messages
OpenAI-Project: <proj_id>
```

The renderer shows the header (project, created, model, transaction, user, conversationId, dialogueId, token usage, Logs URL), the input messages, the assistant output, the full metadata, and — for memory-ingest completions — the `## User-Provided Summary` block extracted from the system message.

## User pivot

For each target project (one if `--project` given, else all), newest-first, time-bounded:

```
GET /v1/chat/completions?metadata[user]=<firebase-uid>&limit=100
OpenAI-Project: <proj_id>
```

Firebase UIDs are mixed-case by design (e.g. `xv360L1eS7fHgnSvpGhlREnq8Vz2`) — no auto-folding. The server-side filter is exact-match.

## Gotchas

### 1. `limit > 100` returns an empty page

Not an error, not a 400 — an empty `data` array. Hard-cap at 100. (Cost a real debugging cycle during the migration.)

### 2. The project header is mandatory for completion reads

`GET /chat/completions` / `/{id}` / `/{id}/messages` all need `OpenAI-Project: <proj_id>`. Without it, completion retrieval 404s and listing returns the wrong (or no) project's data.

### 3. NEVER group / join / aggregate by `metadata.transaction`

It's a thread/session id and can span multiple unrelated conversations and users. Filtering by it (Mode 2) is correct — that selects all completions of one session. Grouping by it produces wrong aggregates. Group / join on `conversationId` / `dialogueId`.

### 4. memory-ingest completions don't carry the live `conversationId`

`metadata.conversationId` IS present on memory-ingest completions, but it's the **memory-ingestion run's own id** — not the user's live coach conversation in the Conversation project. To find a plausibly-related live conversation, use `metadata.user` + the transaction `created` timestamp to bracket a search (e.g. `data:analyze-user-conversations --user-id <uid>`).

### 5. Multiple `name` (prompter) variants in the Memory project

| `metadata.name` | When it runs | Typical guardrail signal |
|---|---|---|
| `memory-ingest-prompter` | Once-per-signup, "Bring your memory" pasted summary | High-signal — one fire = one alert |
| `memory-prompter` | Per-turn during live conversations | Lower-signal but very frequent — hundreds per user/week |
| `memory-sorting-prompter` | Post-processing of stored memories | Operational, not user-facing |

Different ops concerns. Use `--prompter memory-ingest-prompter` to filter triage to just the once-per-signup ingestion runs.

### 6. Only successful completions are stored

OpenAI stores the completions it returned — there's no equivalent of a Braintrust "errored span". An error inside the agent loop that prevented a completion has no stored-completions record; reach for `eng:investigate-gcp-logs` for the service-layer error.

### 7. No latency / timing data

Completions carry `created` (unix seconds) and token `usage` only. The Braintrust-era per-span duration has no analog — don't try to surface latency from this skill.

## One-off requests via curl

List a transaction across one project:

```bash
curl -sS \
  -H "Authorization: Bearer $OPENAI_MEMORY_API_KEY" \
  -H "OpenAI-Project: proj_f0PpUSyYnJNifUZj44FrjUhr" \
  "https://api.openai.com/v1/chat/completions?metadata[transaction]=<UUID>&limit=100"
```

Retrieve one completion + its messages (you must know the project, or fan out):

```bash
curl -sS \
  -H "Authorization: Bearer $OPENAI_CONVERSATION_API_KEY" \
  -H "OpenAI-Project: proj_esFyd4eq2VIAjKUn69X1ZFfk" \
  "https://api.openai.com/v1/chat/completions/<chatcmpl-id>"

curl -sS \
  -H "Authorization: Bearer $OPENAI_CONVERSATION_API_KEY" \
  -H "OpenAI-Project: proj_esFyd4eq2VIAjKUn69X1ZFfk" \
  "https://api.openai.com/v1/chat/completions/<chatcmpl-id>/messages"
```

The `Authorization` key must match the `OpenAI-Project` header — use `$OPENAI_<PROJECT>_API_KEY` for whichever project you target (e.g. `$OPENAI_MEMORY_API_KEY` with `proj_f0PpUSyYnJNifUZj44FrjUhr`, `$OPENAI_JOURNAL_API_KEY` with `proj_qXZ9CZnjp4mL5PVsW43JyOpj`).

The skill avoids the per-project guessing by fanning the retrieve across all `[Production]` projects automatically.
