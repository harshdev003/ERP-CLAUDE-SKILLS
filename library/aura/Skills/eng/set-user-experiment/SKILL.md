---
name: set-user-experiment
description: >
  Force-assign an existing Aura user to a specific experiment bucket against production,
  bumping their joined-version first if it is below the experiment's minVersion so the
  assignment actually takes effect. Accepts an experiment name, a target bucket value, and
  either a userId or an email — resolves the user, checks the experiment config, and writes
  the bucket assignment. Every write step is shown with the assembled curl and only executes
  after the user types yes. Trigger on "put {user} into {experiment} bucket {value}", "set
  {experiment}={val} for {email}", "force-assign {user} to {experiment}", "QA {experiment}
  variant b on Daniel", "stick this user into the {experiment} A/B", or any request to
  override an existing user's experiment assignment. Do NOT use for bulk assignment, assigning
  a brand-new user with no document yet, reading the current bucket only (use
  `eng:call-aura-api` with the read-only assignments endpoint), or sweeping ended experiments
  (`eng:sweep-ended-experiments`).

metadata:
  version: "3.2.0"
knowledge:
  reads:
    - Guide/Index
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
---

# Set User Experiment

Force-assign an existing Aura user to a specific experiment bucket on
**production**, bumping `aiJoinedVersion` first if the user is below the
experiment's `minVersion` floor. Read-only steps run without prompting; every
write step asks for explicit `yes` confirmation before executing.

## Goal

Given:

- `experimentName` (e.g. `aurieEnvironment`)
- `value` (the target bucket, e.g. `a`, `b`)
- `userId` **or** `email`

…assign the user to that bucket, making sure their `aiJoinedVersion` is at
least the experiment's `minVersion` so the assignment isn't filtered out
client-side or by downstream gating logic.

## Inputs

Free-form from the user. Examples:

- `set exp aurieEnvironment value a for <redacted-email>`
- `aurieOnboarding=b for userId google:111555896239269076429`
- `put <redacted-email> into triton bucket a`

If any of the three (`experimentName`, `value`, user identifier) is missing,
ask the user before doing anything. Never invent a user identifier.

## Required env vars

This skill writes to production and depends on a single shell secret in
`~/.claude/settings.json` → `env`:

| Shell env var      | Purpose                                                                 |
|--------------------|-------------------------------------------------------------------------|
| `AURA_API_TOKEN`   | Firebase ID token for a user with `role: 'admin' | 'service' | 'system'` on the prod `users` collection. Required for the PATCH and POST writes below. |

If unset, stop and instruct the user to add it (see [`/CLAUDE.md`](../../../../CLAUDE.md)
→ "Secrets and environment").

## Workflow

### 1. Resolve the user

If the caller gave a `userId` (anything matching `^(google:|apple:|email:)?[A-Za-z0-9_-]{20,}$`
or a known provider prefix), skip the lookup and use it directly — but still
call `GET /users/:userId` to confirm the record exists.

If the caller gave an email, look it up with the email in the request body, never in a URL
(`AURA_API_TOKEN`; the route answers `403` to a role it doesn't allow, service today and agent once
UserService adds it):

```bash
curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/eng:set-user-experiment (session ${AINATIVE_SESSION_NAME:-adhoc})" -sS -X POST "https://services.aurahealth.io/users/email/lookup" \
  -H "Authorization: Bearer $AURA_API_TOKEN" -H "Content-Type: application/json" \
  -d "$(jq -n --arg email "<email>" '{email: $email}')"
```

Capture from the response:

- `id` — used as `:userId` in every subsequent call
- `aiJoinedVersion` — used in step 3 to decide whether a bump is needed
- `email`, `givenName` — for the confirmation summary only (never logged, never
  pasted into external surfaces)

If status is `404` or the body is all `null`, stop. Tell the caller: "no user
found for `<email>`. Check the email on the user document — the Firebase Auth
email and the DB `email` field can drift apart. Try `GET /users/:userId` with
the Firebase UID if you have it." Do not proceed.

### 2. Fetch the experiment config

```bash
curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/eng:set-user-experiment (session ${AINATIVE_SESSION_NAME:-adhoc})" -sS -X GET "https://services.aurahealth.io/experiments/<experimentName>" \
  -H "Authorization: Bearer $AURA_API_TOKEN"
```

Capture from the response:

- `minVersion` — semver string, e.g. `1.17.0`. Used in step 3.
- `maxVersion` — optional semver, can be `null`. Used in step 3 as an upper
  bound on the bump.
- `active` — sanity check. If `null`, warn the user that the experiment is
  inactive but proceed if they reconfirm.
- `deletedAt`, `endedAt` — if either is non-null, stop and tell the user the
  experiment is no longer live. Don't proceed.
- `targetApp` — if set (`ai`, `mobile`, etc.), include in the summary so the
  caller can sanity-check the experiment matches the user's surface.

If the GET returns `404`, stop and tell the user the experiment doesn't exist.

### 3. Decide whether to bump `aiJoinedVersion`

Compare the user's current `aiJoinedVersion` to the experiment's `minVersion`
using semver:

- If `user.aiJoinedVersion >= experiment.minVersion` → **skip step 4** (no
  bump needed). Tell the user in one line: "no bump needed; user is on
  `<v>`, minVersion is `<v>`".
- If `user.aiJoinedVersion < experiment.minVersion` → **propose a bump to
  exactly `minVersion`** (don't go higher, don't go lower). If
  `experiment.maxVersion` is set and `minVersion > maxVersion` (shouldn't
  happen but check), stop and ask the user to resolve the inconsistency.
- If `user.aiJoinedVersion` is `null` or unset → treat as below `minVersion`
  and propose a bump to `minVersion`.

Use a deterministic semver compare (e.g. split by `.`, parse ints, compare
tuple-wise) — don't string-compare.

### 4. (Conditional) PATCH the user's `aiJoinedVersion`

Show the user the assembled curl:

```bash
curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/eng:set-user-experiment (session ${AINATIVE_SESSION_NAME:-adhoc})" -sS -X PATCH "https://services.aurahealth.io/users/<userId>" \
  -H "Authorization: Bearer $AURA_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data-raw '{"aiJoinedVersion":"<minVersion>"}'
```

Plus a one-row resolution table:

```
| Field           | From                          | Value           |
|-----------------|-------------------------------|-----------------|
| userId          | step 1                        | <userId>        |
| current version | user.aiJoinedVersion          | <old>           |
| target version  | experiment.minVersion         | <new>           |
| token           | shell env AURA_API_TOKEN      | eyJh…xyz        |
```

Mask the token as first-4 + `…` + last-3 (same rule as `eng:call-aura-api`).

Prompt: "This is a `PATCH` to **production** — it will write `aiJoinedVersion`
on the user document. Reply `yes` to execute." On `yes`, run the curl and
print the response. On anything else, stop.

The backend's pubsub listener
(`UserService/src/listeners/commands/aiJoinedVersionUpdated.js` →
`FirebaseProducer/functions/src/triggers/user/onAIJoinedVersionUpdated.js`)
automatically splits the semver and populates
`aiJoinedVersionMajor/Minor/Patch` — so sending just the string is sufficient.
Do not send the parsed numeric components; that's redundant and risks them
drifting from the canonical string.

### 5. POST the experiment bucket assignment

Show the user the assembled curl:

```bash
curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/eng:set-user-experiment (session ${AINATIVE_SESSION_NAME:-adhoc})" -sS -X POST "https://services.aurahealth.io/experiments/<experimentName>/set/<userId>/<value>" \
  -H "Authorization: Bearer $AURA_API_TOKEN" \
  -H "Content-Type: application/json"
```

Plus a resolution table:

```
| Field           | From                          | Value           |
|-----------------|-------------------------------|-----------------|
| :name           | input                         | <experimentName>|
| :userId         | step 1                        | <userId>        |
| :value          | input                         | <value>         |
| token           | shell env AURA_API_TOKEN      | eyJh…xyz        |
```

Prompt: "This is a `POST` to **production** — it will write the experiment
assignment for the user. Reply `yes` to execute." On `yes`, run the curl and
print the response (typically `{"<experimentName>": "<value>"}`). On anything
else, stop.

### 6. Summarize

Print a single-line summary:

```
<userId>: aiJoinedVersion <old> → <new>; <experimentName> = <value>
```

Plus the two raw `<METHOD> <url> → <status>` lines from steps 4–5 so the
caller can see exactly what changed.

## Edge cases

- **User document doesn't exist.** Stop at step 1. Do not auto-create — that
  belongs to a different flow (use `eng:call-aura-api` with the user creation
  route if you really mean it).
- **Experiment doesn't exist / is deleted / ended.** Stop at step 2 with a
  clear message; never write the assignment.
- **User is at exactly `minVersion`.** Skip step 4 — equality counts as
  "qualifies".
- **User's `aiJoinedVersion` is `null`.** Treat as below `minVersion` and
  propose the bump in step 4.
- **User is above `maxVersion`.** Warn but do not auto-downgrade. Ask the
  caller whether they really want to proceed. Downgrading `aiJoinedVersion`
  silently is unsafe — it can re-trigger onboarding flows or break analytics.
- **403 Forbidden on PATCH.** The authenticated user (decoded from
  `AURA_API_TOKEN`) doesn't have `role: 'admin' | 'service' | 'system'` on
  their DB record. Decode the JWT, find the user's UID, look it up via
  `GET /users/:userId`, and report their actual role so the caller can fix
  it. Don't retry with a different token automatically.
- **`AURA_API_TOKEN` is expired** (Firebase ID tokens last ~1h). The response
  body will be plain `Unauthorized`. Tell the caller to refresh the token.
- **Caller asks to use a non-production env.** Refuse for v1. This skill is
  prod-only.
- **Caller passes both `userId` and `email`.** Use `userId`; ignore the email
  but log it for context.
- **Re-running on the same inputs.** Safe — the GETs are read-only, and both
  writes are idempotent (PATCHing the same version is a no-op; POSTing the
  same bucket overwrites with the same value).

## Security

- Never inline the bearer token in the rendered curl text — always reference
  `$AURA_API_TOKEN`.
- Always mask the token in the resolution table (first 4 + `…` + last 3).
- Treat the user document fetched in step 1 as PII. Print only what's needed
  for the confirmation summary (UID, `aiJoinedVersion`). Never echo
  `givenName`, `familyName`, full `email`, phone, IP, calendar tokens, or
  onboarding transcripts. If the caller later asks you to share the run
  externally, follow the PII redaction rule (UID prefix + email domain only).

## References

- Sibling skill `eng:call-aura-api` — the generic Postman + curl wrapper this
  skill specializes from. Re-read its workflow if you need to debug the
  shell-env / Postman variable resolution model.
- `UserService/src/http/router/users/index.js` — the `PATCH /users/:userId`
  route declaration. The `authorize()` middleware enforces the role check
  that surfaces as 401 in the edge case above.
- `FirebaseProducer/functions/src/triggers/user/onAIJoinedVersionUpdated.js` —
  the pubsub trigger that auto-splits `aiJoinedVersion` into the
  `Major/Minor/Patch` siblings on every write.
