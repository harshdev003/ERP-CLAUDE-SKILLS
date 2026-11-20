---
name: call-aura-api
description: >
  Call an Aura backend API directly from Claude Code by first resolving the endpoint via
  `Tools/Aura API` in AI Knowledge, which provides method, path, auth, headers, body and
  sample curl — so the skill never needs Postman in the happy path. Documented endpoints
  proceed through resolve-secrets, show-curl, confirm, then execute against production. When
  an endpoint is NOT documented the skill surfaces the gap and asks explicit permission before
  falling back to Postman, then prompts to ingest the new endpoint so the next call is
  self-served. Secrets come from the shell env and are masked in the surfaced curl. Trigger on
  "call the Aura API for X", "hit the {endpoint} endpoint", "run /users/email", "get user by
  email", "dialogue ask", "end conversation", "call the Aurie dialogue API", "exec this
  request against prod", "curl the Aura prod API for {description}", or a free-form API
  description with a request to run it. Prod-only. Do NOT use for editing Postman requests or
  third-party APIs.

metadata:
  version: "3.0.2"
knowledge:
  reads:
    - Guide/Index           # baseline routing aid (universal)
    - Tools/Aura API        # canonical reference + build source for Aura backend endpoints
    - Compliance/Privacy    # PII handling rule when responses contain user data
---

# Call Aura API

An AI Knowledge–first wrapper around the Aura API, with Postman as a
permission-gated fallback: resolve the endpoint in `Tools/Aura API`, build the
curl directly from the documented sub-page, resolve variables against the
**production** environment, mask secrets, execute after confirmation. Postman
is touched only when the endpoint isn't documented yet — and after the call
succeeds, the new endpoint is ingested into `Tools/Aura API` so the next call
self-serves.

## Goal

Given a description like "get user by email", produce and (on confirmation)
execute the equivalent curl against `https://services.aurahealth.io`. The
documented sub-page in `Tools/Aura API` is the build source — it carries the
method, path, auth, headers, body shape, and a sample curl. Postman is only
the fallback for endpoints that aren't documented yet, and is gated by
explicit user permission.

## Policy: AI Knowledge is the source of truth

This skill only runs against Aura endpoints documented in `Tools/Aura API` (or
its sub-pages, e.g. `Tools/Aura API/Users/Get User By Id`,
`Tools/Aura API/Conversations`, `Tools/Aura API/Subscriptions`). Documented
endpoints have a written contract — purpose, auth model, headers, sample
curl, PII flags, expected shape — that the skill builds from directly. No
Postman call is required in the happy path.

Undocumented endpoints can still be called, but only with **explicit user
permission per request** after the skill surfaces the gap. On permission, the
skill falls back to Postman to extract the request shape, executes (still
with the same `yes` confirmation), and then prompts the user to ingest the
endpoint into `Tools/Aura API` via `shared:ingest-knowledge`. The next call
hits the documented path with no permission gate and no Postman.

The invariant: every Aura endpoint a skill ever calls ends up documented in
`Tools/Aura API`. Postman is the long-tail source, never the steady state.

## Inputs

A free-form API description or title from the user. Examples:

- `get user by email`
- `dialogue ask`
- `end conversation`
- `/conversations/:id/start`
- `payments stripe getCustomer`

If the user did not provide a description, ask for one before doing anything.

## Required tools

Primary (always needed — declared via `knowledge.reads`):

- `mcp__notion-fetch`, `mcp__notion-search` — used by `shared:retrieve-knowledge` to load `Tools/Aura API` pages
- `shared:retrieve-knowledge` — fetch a documented endpoint sub-page by path
- `shared:search-knowledge` — discover candidate paths when the user's description doesn't map to a single page

Optional (used only on user-authorized Postman fallback):

- `mcp__postman__searchPostmanElements`
- `mcp__postman__getCollectionRequest`
- `mcp__postman__getEnvironment`
- `shared:ingest-knowledge` — document the endpoint in `Tools/Aura API` after a successful Postman-fallback execute

Postman MCP availability is **not** a precondition. If the user only ever calls
documented endpoints, this skill never touches Postman. If Postman MCP is
unavailable when an undocumented endpoint is requested, the skill explains
the gap and stops (no silent bypass).

## Workflow

### 1. Verify the endpoint is documented in AI Knowledge

`Tools/Aura API` and its sub-pages are pre-fetched via `retrieve-knowledge`
(declared in this skill's `knowledge.reads`), so the documented endpoint
families are already in context. Match the user's description against them —
the match can be loose (a path family, an action verb, a feature name); the
goal is to identify the documented sub-page that fits.

If `Tools/Aura API` itself doesn't list a sub-page matching the description,
use `shared:search-knowledge` scoped under `Tools/Aura API` to discover
candidates before declaring "not documented".

Three outcomes:

- **Exact match** (one documented sub-page clearly fits): record the
  sub-page path and proceed to step 2.
- **Multiple candidates** (description fits more than one): present the
  candidate list in chat, ask the user to pick one, then proceed to step 2.
- **No documented match**: surface the gap with this exact shape:

  > "I couldn't find this endpoint in `Tools/Aura API`. The documented
  > families are: <list>. Three options:
  >
  > 1. Did you mean one of the documented endpoints? (clarify and re-run)
  > 2. Add the endpoint to `Tools/Aura API` first (via `ingest-knowledge` or a
  >    manual Notion edit) and re-run.
  > 3. Proceed against the undocumented endpoint anyway — reply `proceed
  >    undocumented` to authorize this one call. I'll fall back to Postman
  >    for the request shape, ask for the same execution confirmation in
  >    step 5, and then prompt you to ingest the endpoint into AI Knowledge
  >    so the next call self-serves."
  >
  > I won't search Postman or assemble any curl until you reply."

  On `proceed undocumented`: jump to **Step F (Postman fallback)** below, then
  resume at step 3 with the shape Postman returned. Record in the resolution
  table (step 4) a `Source: undocumented (user-authorized)` row so the gap is
  visible in the final summary. On any other reply: stop.

### 2. Build the request shape from the AI Knowledge sub-page

The documented sub-page carries everything needed to build the curl:

- **Method + path** (e.g. `GET /users/:userId`)
- **Auth** (which bearer token is required — `AURA_API_TOKEN`, `AURA_USER_TOKEN`, or none)
- **Headers** (the full list, including `Authorization`)
- **Path params** (with required/optional flags; never invent defaults)
- **Query params** (same)
- **Body** (None for GET; the JSON shape for POST/PUT/PATCH)
- **Sample curl** (a working bash example — the canonical reference for variable substitution)

Read these directly off the page text. The sample curl block tells you exactly
how variables should be referenced (`${AURA_API_TOKEN}`, `${USER_ID}`, etc.) —
preserve that form when building the rendered curl in step 4.

If a required field is missing from the sub-page (rare — usually means the
page is stale), stop and tell the user the page needs updating. Suggest
`shared:harvest-knowledge` or a manual Notion edit to fix it. Do not fall back
to Postman silently — that would route around the documented contract.

Continue to step 3.

### 3. Resolve secrets via the shell env map

The sample curl references bearer tokens as shell variables. Resolve each by
reading the agent's process env (`printenv <NAME>`):

| Token reference in docs/curl | Shell env var      |
|------------------------------|--------------------|
| `${AURA_API_TOKEN}`          | `AURA_API_TOKEN`   |
| `${AURA_USER_TOKEN}`         | `AURA_USER_TOKEN`  |
| `${OPEN_AI_API_KEY}` / `${OPENAI_API_KEY}` | `OPEN_AI_API_KEY` |

If a token reference is needed but the shell env var is unset, record it as
`MISSING` in the resolution table (step 4) and stop before execute.

Path/query params come from the user. If the sub-page lists example values,
treat them as defaults and ask the user to confirm or override before
building the curl. Never invent realistic-looking PII (emails, user IDs).

### 4. Build the curl and the resolution table

Assemble the curl command:

- Method via `-X <METHOD>` (or no flag for GET).
- URL with `:pathVar` placeholders substituted from user-supplied values.
- Headers from the sub-page header list, including `Authorization: Bearer
  $AURA_API_TOKEN` (or `$AURA_USER_TOKEN`) when the documented auth is bearer.
- Query string from documented query params, only those the user explicitly
  set.
- Body via `--data` / `--data-raw` if present, with secrets kept as `$VAR`
  references rather than inlined.

Show two artefacts in chat:

**(a) The curl command** — secrets stay as `$VAR` references so the rendered
text never contains a literal token.

**(b) Variable resolution table** — one row per variable the request
references:

```
| Variable        | Source                          | Value                         |
|-----------------|---------------------------------|-------------------------------|
| base URL        | Tools/Aura API                  | https://services.aurahealth.io |
| AURA_USER_TOKEN | shell env                       | eyJh…lDw (masked)             |
| AURA_API_TOKEN  | shell env                       | **MISSING**                   |
| :userId         | path variable (user-supplied)   | V1sSwAGN5uMmUMJp8NltKDdcKFg1  |
```

Masking rule for tokens: show first 4 and last 3 chars, replace the middle
with `…`. Never print the whole secret.

If any required variable is **MISSING**, stop before execute and tell the
user which env var to add to `~/.claude/settings.json` under the `env` block
(see [`/CLAUDE.md`](../../../../CLAUDE.md) → "Secrets and environment").

### 5. Confirm and execute

Only after the table renders cleanly with **no missing values**, ask the
user explicitly:

> "Ready to run this request against **production** (`https://services.aurahealth.io`)? Reply `yes` to execute."

On `yes`: run the curl via Bash. Pretty-print the JSON response in chat
(`| python3 -m json.tool` or equivalent). On any other reply, do not execute.

For non-GET methods (`POST`, `PUT`, `PATCH`, `DELETE`), use the same prompt
but make the side-effect explicit: "this is a `POST` to production — it will
write data".

### 6. Handle the response

- Print method, URL, status code, and the pretty-printed body.
- If the body contains user PII (full `email`, `givenName`, `familyName`,
  phone, address), warn the user **once** before printing that the response
  contains PII and is governed by `Compliance/Privacy`'s redaction rule (UID
  prefix + email domain only) if they intend to paste it into Slack, Notion,
  Linear, GitHub, or any other external surface.

### 7. Self-heal (only when this call used the Postman fallback)

If step 1 went through `proceed undocumented` and Step F ran, then after a
successful execute prompt the user:

> "This endpoint isn't yet documented in `Tools/Aura API`. Want me to ingest
> it now so the next call self-serves? Reply `yes` to ingest, anything else
> to skip."

On `yes`: invoke `shared:ingest-knowledge` with:

- **Source**: the Postman request URL captured in Step F + the captured response (with PII redacted per `Compliance/Privacy` before passing along).
- **Target path**: `Tools/Aura API/<Family>/<Endpoint>` — pick the family from the existing top-level entries (`Users`, `Subscriptions`, `Conversations`, `Receipts`, `Products`, `Memory`, `Payouts`, `Content`); create a new family page only if no existing one fits, and prompt the user before creating.
- **Shape**: match the existing documented endpoints (Method + path, Purpose, Auth, Headers, Path/query params, Body, Sample curl, Response, Notes, `# Sources`).

On anything else: skip and continue. The endpoint stays undocumented; the user
will hit the same permission gate next time.

## Step F: Postman fallback (only on `proceed undocumented`)

Triggered only by step 1's `proceed undocumented` path. The user has
explicitly authorized one call against an endpoint that isn't in
`Tools/Aura API`.

**F1. Confirm Postman MCP availability.** If `mcp__postman__searchPostmanElements`
isn't loaded, stop and tell the user to install/authenticate the Postman MCP.
Do not silently bypass.

**F2. Search Postman.** Call `searchPostmanElements` with `q` = the user's
description, `entityType` = `"requests"`, `ownership` = `"organization"`,
`limit` = `10`. Zero results → stop, suggest saving the request in Postman
first. Multiple results → show ranked list (method, URL, collection, Postman
link) and ask the user to pick one. Exactly one result → use it.

**F3. Fetch the request shape.** Call `getCollectionRequest` with the chosen
request's `collectionId` and `requestId` and `populate: true`. Capture
`method`, `url` (template), `headerData[]`, `queryParams[]`,
`pathVariableData[]`, `rawModeData` / `dataMode` / `data` (body), `auth`.

**F4. Load the Postman production environment.** Call `getEnvironment` with
`environmentId` = `accadff0-abf3-4b55-a9b7-58cd185030d8` (Postman `production`
env, Aura Team Workspace). Build a `var → value` map. Any value of `"*****"`
is masked — record it as needing shell-env resolution in step 3.

**F5. Resume the main workflow at step 3** with the shape Postman returned.
Masked Postman secrets map to shell env vars via the same table as step 3
(`admin.token` → `AURA_API_TOKEN`, `user.token` → `AURA_USER_TOKEN`,
`openai.key` → `OPEN_AI_API_KEY`). Unmappable masked secrets stop the
workflow with the same `MISSING` row treatment.

After step 6 (response handled), run step 7 (self-heal) to document the
endpoint.

## Output

A pretty-printed JSON (or text) response body printed in chat, plus a brief
one-line summary: `<METHOD> <url> → <status>`.

## Edge cases

- **Endpoint not in `Tools/Aura API`.** Default behaviour is the step-1 gap
  prompt — surface the documented families, ask whether the user meant one of
  them, wants to document the new endpoint first, or wants to authorize this
  one undocumented call (`proceed undocumented`). Never assume.
- **`Tools/Aura API` page exists but the sub-page is stale.** A field is
  missing or the sample curl references a removed variable. Stop, surface
  the staleness, and suggest `shared:harvest-knowledge` or a manual Notion
  edit. Do not silently fall back to Postman — that routes around the gate.
- **Postman MCP unavailable when undocumented endpoint requested.** Stop and
  tell the user. The right next step is either to install the Postman MCP or
  to add the endpoint to `Tools/Aura API` first.
- **User declines to ingest after a Postman-fallback execute.** The call
  still ran; only the documentation step is skipped. The next call against
  the same endpoint will hit the same permission gate.
- **Path variable has no stored example.** Ask the user for the value
  explicitly — do not guess.
- **Re-run on the same input.** Safe: resolve + show are read-only. The
  execute step always re-asks for confirmation, so two runs = two
  confirmations = no accidental double-write.
- **User asks for a non-production env.** Refuse for v1 — point at Postman
  directly. Extending this skill to dev/local envs is a future enhancement.

## Security

- Never inline a secret into the rendered curl text. Always reference shell
  env vars (`$AURA_API_TOKEN` etc.) in the command shown to the user.
- Always mask tokens in the resolution table (first 4 + `…` + last 3).
- Never write the curl, the response, or the resolution table to a file in
  the working tree. Output is chat-only.
- Treat any response body as potentially containing PII and follow the
  `Compliance/Privacy` redaction rule before suggesting external sharing.
- When passing a captured response to `shared:ingest-knowledge` for step 7,
  redact PII per `Compliance/Privacy` first — the ingested AI Knowledge page
  will be readable by every agent that reads `Tools/Aura API`.

## References

Primary build source: `Tools/Aura API` in the AI Knowledge Notion teamspace —
every documented endpoint sub-page carries method, path, auth, headers, body
shape, and a sample curl. Fallback source (permission-gated): the
*Aura Services* Postman collection. Postman MCP tool reference: the Postman
MCP server's own `getPostmanContextOverview` resource.
