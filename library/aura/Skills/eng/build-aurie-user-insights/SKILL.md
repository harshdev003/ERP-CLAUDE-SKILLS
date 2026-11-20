---
name: build-aurie-user-insights
description: >
  Trigger an Aura Intelligence Service insights compilation for one or more users, then
  optionally fetch and display the compiled insight. Trigger build mode on "build insights for
  user {userId}", "build aurie insights for {userId}", "compile insights for {userId}",
  "trigger insights compilation for {id}", "run insights for user(s) {ids}", or one or more
  user IDs with a request to build their insights. A READ-ONLY mode fetches the insight and
  card renderers without compiling — no POST, no state change — taking a rendererRating id
  (from a `*PROFILE RATED*` Slack footer) or a userId. Trigger read mode on "fetch the
  insights for {userId} without rebuilding", "get the renderers for {userId}", or "read the
  rendererRating snapshot". Never use build mode to inspect a rated profile — it overwrites
  the artifact you want to look at. Do NOT use for analysing a *PROFILE RATED* rating,
  "profile user {UID}", or any question about WHY a user rated their profile — that is
  `data:profile-user`.

metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Guide/Index       # baseline routing aid (universal)
    - Tools/Aura API    # backend host + auth conventions
    - Tools/Aura API/Insights  # canonical endpoint reference for /ai/insights/* — routes, roles, request/response shapes
    - Tools/OpenAI      # reading stored completions access pattern
    - Products/Aurie/Technology/Profile Insights & Card Renderers  # feature background — renderer keys, the rendererRating snapshot, and why read mode must not recompile
    - Compliance/Privacy # PII handling — UID-only by default
---

# Build User Insights

Trigger insights compilation for one or more Aura users via the Intelligence
Service compile endpoint, then optionally fetch and display the result from
OpenAI stored completions.

## Goal

Two modes over the same artifact — the user's compiled insight plus its card
renderers.

| Mode | What it does | Use when |
|---|---|---|
| **`build`** (default) | Triggers a fresh `insights-compiler` run, waits, then displays the result | An operator wants up-to-date insights on demand |
| **`read`** | Returns an existing artifact — **no POST, no state change** | Inspecting a profile without rebuilding it; serving `data:profile-user`'s `insights` source |

**Why `read` exists.** `build` mutates: it overwrites `user.insightsRenderers`
with a fresh compile. That is exactly wrong for rating forensics — you need what
the user was looking at when they rated, and rebuilding destroys the ability to
compare. Never use `build` to answer "why did this user rate their profile down".

**`read` fetches; it doesn't analyse.** It returns the artifact and stops. The
question "why did this user rate their profile down" belongs to
[`data:profile-user`](../../../data/skills/profile-user/SKILL.md), which calls this
mode as its `insights` source and cross-references the behavioural and
conversational picture. Don't answer a rating question from this skill alone.

## Inputs

- **`mode`** — `build` (default) or `read`.
- **`userId`** (one or more) — Aura Firebase user ID(s), long alphanumeric strings.
  Required for `build`; one of `userId` / `ratingId` for `read`.
- **`ratingId`** (`read` only) — a `rendererRating` row id, the uuid in the
  `*PROFILE RATED*` Slack footer `[id: <uuid>, user: <UID>]`. **Preferred over
  `userId` in `read` mode** — it returns the exact snapshot that was rated.

**Pass BOTH `ratingId` and `userId` in `read` mode whenever you have them.** The
`*PROFILE RATED*` footer always carries `user:` next to `id:`, so a caller that parsed
the footer has both. The rating id alone can't fall back: R2 is keyed by user, and a
404 from R1 returns no user to fall back *to* (see R1 below).

Ask for what's missing, per mode:

- **`build`** — > Which user ID(s) should I build insights for? Provide one or more Firebase user IDs.
- **`read`** — > Which profile should I read? A rating id from the `*PROFILE RATED*` footer gives the exact snapshot the user rated; a Firebase user ID gives their current profile. Pass both if you have them.

## Required environment

- `AURA_API_TOKEN` — service-level Aura backend token. Used as the Bearer token
  for all IS calls. It does not expire — use it directly without inspecting the
  JWT. Only ask for a fresh token if a call returns an actual 401.
- `OPENAI_INSIGHTS_API_KEY` — a **project-scoped service-account key** for the
  `Insights [Production]` project, in `~/.claude/settings.json`. **Not**
  `OPENAI_ADMIN_API_KEY`: OpenAI enforces a `model.request` scope on
  `GET /v1/chat/completions` that Admin keys cannot hold, by OpenAI design, so
  stored-completions reads now require a per-project key ([PROD-3572](https://linear.app/aura-health/issue/PROD-3572)).
  Create at <https://platform.openai.com/api-keys> → Create new secret key →
  Owned by: Service account → Project: `Insights [Production]`. Pair it with an
  `OpenAI-Project: <proj_id>` header. Missing key = skip retrieval and tell the user.

**The Insights project — get the ID right.** Insights stored completions live in
**Insights [Production]** = `proj_W8baHE7bC4QZsBGIoYMC6ylQ` (plural). There is an
OLD, dead project named **Insight [Production]** = `proj_83ODMPARCD3M4SP1ZskA0CaW`
(singular) with **0 completions** — do NOT use it. The names differ by one letter;
always pin the project ID, never resolve by display name. (Full
project inventory: knowledge `Tools/OpenAI`.)

## Workflow

**Route on `mode` first.** `read` → the [Read-only mode](#read-only-mode) section
below, and stop; Steps 1–6 are the `build` path and must not run (Step 2 is the
mutating POST).

### Step 1 — Resolve user IDs

Accept user IDs from the caller's message. Multiple IDs may be space- or
comma-separated. If none are provided, ask for them and wait.

### Step 2 — Trigger compilation for each user

For each `userId`, call and capture both the HTTP status and the
`X-Response-ID` response header. That header carries the transaction UUID
that will be stamped on the background OpenAI completion:

```bash
HEADERS_FILE=$(mktemp)
HTTP_STATUS=$(curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/eng:build-aurie-user-insights (session ${AINATIVE_SESSION_NAME:-adhoc})" -s -o /dev/null -D "$HEADERS_FILE" -w "%{http_code}" \
  -X POST "https://services.aurahealth.io/ai/insights/compile/<userId>" \
  -H "Authorization: Bearer $AURA_API_TOKEN" \
  -H "Content-Type: application/json")
TRANSACTION=$(grep -i "^x-response-id:" "$HEADERS_FILE" | grep -iv "slice" \
  | awk '{print \$2}' | tr -d '\r\n')
rm -f "$HEADERS_FILE"
```

The endpoint returns **202 Accepted** immediately — compilation runs in the
background. Store the `TRANSACTION` value for each userId (keyed by userId);
you will need it in Step 5 to retrieve the exact completion.

A non-202 status is an error; report it per user and continue with any
remaining IDs. If `TRANSACTION` is empty despite a 202, fall back to
`metadata[user]` + `metadata[name]` filtering in Step 5 (see fallback note
there).

When triggering multiple users, fire them sequentially (not in parallel) so
logs remain readable.

Tell the user after each trigger:
> Compilation triggered for `<userId>` (transaction: `<TRANSACTION>`) — running in background (~10 seconds).

### Step 3 — Wait for background processing

After all triggers are fired, wait **10 seconds** before checking results:

```bash
sleep 10
```

Tell the user:
> Waiting 10 seconds for the compilation to finish…

### Step 4 — Ask whether to retrieve results

After the wait, ask:
> Compilation should be complete. Would you like to see the compiled insights?
> (yes to retrieve, no to skip)

If the user says **no**, summarise which users were compiled and stop.

### Step 5 — Retrieve compiled insights from OpenAI

For each `userId` the user wants to view, query the **Insights [Production]**
project's stored completions with the project-scoped key + project header. Use
`-G` + `--data-urlencode` so the `metadata[...]` brackets are encoded correctly.

**Primary path — filter by transaction ID (precise):**

```bash
curl -sS -G \
  -H "Authorization: Bearer $OPENAI_INSIGHTS_API_KEY" \
  -H "OpenAI-Project: proj_W8baHE7bC4QZsBGIoYMC6ylQ" \
  --data-urlencode "metadata[transaction]=<TRANSACTION>" \
  --data-urlencode "limit=5" \
  https://api.openai.com/v1/chat/completions
```

The transaction ID uniquely identifies this specific compilation run, so the
result is exact regardless of how many prior insights exist for the user.

**Fallback path — filter by userId + prompter name (if transaction was not captured):**

```bash
curl -sS -G \
  -H "Authorization: Bearer $OPENAI_INSIGHTS_API_KEY" \
  -H "OpenAI-Project: proj_W8baHE7bC4QZsBGIoYMC6ylQ" \
  --data-urlencode "metadata[user]=<userId>" \
  --data-urlencode "metadata[name]=insights-compiler-prompter" \
  --data-urlencode "limit=5" \
  https://api.openai.com/v1/chat/completions
```

This returns the most recent completions for the user+prompter combination;
take `data[0]`.

**Parse the result:**

- If `.data` is empty → wait 5 more seconds and retry once. If still empty
  after the retry → "No insight completion found for `<userId>` — the job
  may have failed. Check IS logs for transaction `<TRANSACTION>`."
- If `.data` has entries → take `data[0]`:
  - The compiled insight is in `choices[0].message.content`
  - Display: the full text content, prefixed with the completion `id`
    and `created` timestamp (ISO 8601) for traceability.

**Display format:**

```
--- Insights for <userId> ---
Completion: <chatcmpl-id>  |  Built: <ISO timestamp>  |  Transaction: <TRANSACTION>

<content of choices[0].message.content>
```

Render the insight content **verbatim and complete**: reproduce every section the
compiler emitted, in the exact order it returned them, including any section whose
body is `No high-confidence insight available`. Do not drop, reorder, merge, or
summarise sections, and do not relegate the `No high-confidence insight available`
sections to a footnote or parenthetical — they are part of the output and carry
signal (the compiler considered that dimension and found nothing high-confidence).
Light Markdown reformatting for readability is fine as long as the section set and
their order match the source exactly.

If the user asked for token/cost details (e.g. "show tokens", "include usage", "how many tokens"), append a usage line after the header:

```
Model: <model>  |  Tokens: <prompt_tokens> in / <completion_tokens> out / <total_tokens> total  |  Cached: <cached_tokens>  |  Tier: <service_tier>
```

Pull token fields from `usage`: `prompt_tokens`, `completion_tokens`, `total_tokens`.
Cached tokens come from `usage.prompt_tokens_details.cached_tokens` — show `0` if absent.
`service_tier` is the top-level field on the completion object (e.g. `default`, `flex`).

If the content is JSON (starts with `{`), pretty-print it; otherwise display
as plain text.

### Step 6 — Summary

After retrieving (or skipping) all results, output a one-line summary:

> Done. Compiled insights for N user(s): <userId1>, <userId2>, …

## Read-only mode

`mode=read`. Fetches an existing artifact and **never POSTs**. Two sources, in
preference order — try the snapshot first, fall back to live.

### R1 — The rated snapshot (exact, preferred)

When a `ratingId` is available, read the `rendererRating` row. It carries the
`renderers` and `insights` **as they were shown at rating time** — Intelligence
Service snapshots them on every rating precisely so feedback stays linked to what
the user saw after the next recompile overwrites `user.insightsRenderers`.

```bash
curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/eng:build-aurie-user-insights (session ${AINATIVE_SESSION_NAME:-adhoc})" -sS \
  -H "Authorization: Bearer $AURA_API_TOKEN" \
  "https://services.aurahealth.io/ai/insights/renderers/ratings/<ratingId>"
```

Returns `id`, `userId`, `rating` (`1` / `0` / `-1`), `feedback`, `rendererKey`
(`null` = the whole profile was rated), `renderers`, `insights`,
`insightsCompiledAt`, `insightsRenderersCompiledAt`.

> Requires an `admin`- or `agent`-role token; `AURA_API_TOKEN` is `agent`. A 404 means the
> rating id doesn't exist. (Added in
> [ENGBACK-1254](https://linear.app/aura-health/issue/ENGBACK-1254), Intelligence Service
> 0.291.0.)

> **`GET …/renderers/ratings/:ratingId` above is the only rating lookup — one rating by
> id, with no list variant.** The Intelligence Service has no list route and no list model
> function for the `rendererRating` table: it is reached by exactly one `getOneById`, plus
> the insert that writes a rating.
>
> The `ratingId` comes from the *PROFILE RATED* Slack footer, the same place as the
> `userId` (see Inputs). That footer is the only source — a rating id cannot be
> discovered or enumerated. Never probe for a collection: paths like
> `/ai/insights/renderers/ratings/list` or `/ai/insights/ratings` are bound by the
> `GET /:ratingId` and `GET /:insightId` wildcard routes, so `list` and `ratings` are
> read as ids and rejected as malformed uuids (`422`) rather than served as collections.
>
> With no `ratingId` in hand, go straight to R2 with the `userId`.

**A 404 does not hand you a `userId`.** The response carries no user, so R1 alone can't
feed R2. If the caller passed a `userId` alongside the rating id (it's in the same
Slack footer — see Inputs), fall through to R2 with it. If **only** a rating id was
supplied, stop and report the 404 with the id; don't guess a user and don't silently
return nothing.

### R2 — The live profile (current, fallback)

No `ratingId`, or R1 404'd **and** a `userId` is available:

```bash
curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/eng:build-aurie-user-insights (session ${AINATIVE_SESSION_NAME:-adhoc})" -sS \
  -H "Authorization: Bearer $AURA_API_TOKEN" \
  "https://services.aurahealth.io/ai/insights/renderers/<userId>"
```

Returns every renderer, keyed by prompter name — which is what R3 needs, since it
renders all of them. **Don't add `?renderers=`** unless the caller explicitly asked for
one card: the filter is partial-tolerant (an unavailable key is omitted rather than
erroring), so a stray filter silently truncates the profile and the output looks
complete. Requires an `admin`- or `agent`-role token; `AURA_API_TOKEN` is `agent`.

### R3 — Report which source answered (required)

**Every `read` output states its source.** This is not decoration: R1 and R2 can
differ, and a reader who assumes R2 is the rated artifact will analyse content the
user never saw. Compiles run per user, and ratings are typically reviewed hours
later, so drift is the normal case rather than an edge case.

```
--- Profile for <userId> ---
Source: rated snapshot (rating <ratingId>, rated <createdAt> · profile compiled <insightsRenderersCompiledAt>)
Rated: <👍 | 😐 | 👎> <rendererKey, or "whole profile" when null> · feedback: <"<text>", or "none">

## Compiled insight
<the `insights` field, verbatim>

## Renderers
<one section per card, verbatim>
```

Every placeholder comes from the R1 row — **don't hardcode the example's values**. Map
`rating` to the emoji: `1` → 👍, `0` → 😐, `-1` → 👎 (the shape carries all three; a
neutral profile rating is real). `rendererKey: null` means the whole profile was rated.
`feedback` is already normalised, so render an absent one as `none` rather than echoing a
placeholder string.

For R2, the Source line reads instead, and there is no `Rated:` line — a live read isn't
tied to a rating:

```
Source: LIVE current profile (compiled <insightsRenderersCompiledAt>) — NOT necessarily what was rated
```

**Render both the compiled insight and the renderers.** R1 returns `insights` alongside
`renderers`, and the insight is the narrative the cards are generated from — a rating that
reads as a complaint about one card is often a complaint about the underlying insight. R2
returns renderers only, so the insight section is omitted there; say so rather than leaving
the reader to wonder.

Render content **verbatim and complete**, same rule as Step 5: every card the compiler
emitted, in order, including empty ones. When `rendererKey` is set, lead with that card and
mark it as the rated one — but still show the rest, since a card is rated in the context of
the profile around it.

## Error handling

| Situation | Action |
|-----------|--------|
| Trigger returns non-202 | Report HTTP status + response body for that userId; continue with others |
| `AURA_API_TOKEN` missing / 401 | Hard fail with instructions to set the token |
| `OPENAI_INSIGHTS_API_KEY` missing | Skip Step 5; tell the user to add the Insights [Production] project-scoped key to `~/.claude/settings.json` (see Required environment) |
| Query returns 0 rows | First confirm the project header is `proj_W8baHE7bC4QZsBGIoYMC6ylQ` (plural Insights), NOT `proj_83…` (singular, dead). Then wait 5s and retry once; if still empty, report "not found — job may have failed; check IS logs for transaction `<TRANSACTION>`." |
| OpenAI API error | Show the error body; don't retry automatically |
| `read`: snapshot endpoint 404s | The rating id doesn't exist. With a `userId` in hand, fall through to R2 and label the output `LIVE current profile` — never present a live read as the rated artifact. With only a rating id, report the 404 and stop |
| `read`: 403 on either endpoint | Both renderer reads accept `admin` or `agent`. A 403 means the token's user holds neither — report that; don't fall back to `build` to get the data, that would mutate the thing under investigation |
| `read`: user has no renderers | `{}` or empty — report "no compiled profile for this user". Offer `build` as a **separate, explicit** next step; never run it automatically from `read` |
