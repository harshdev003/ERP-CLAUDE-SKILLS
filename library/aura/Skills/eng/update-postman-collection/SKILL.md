---
name: update-postman-collection
description: >
  Add or update ONE request, with its example responses, in the Aura Services Postman collection
  through the Postman REST API (POSTMAN_API_KEY): method, path, auth, body example, response
  examples and a description with its status codes. Uses only the per-request endpoints, never a
  full-collection PUT. Dry run first with a diff, then apply, then read back. Trigger on "update
  Postman", "add this route to Postman", "document the new endpoint in Postman", "Postman entry
  for {route}", "the route changed, update the collection", "add the response example to
  Postman", or a session needing a Postman doc entry for a new or changed route. Do NOT use to
  call an Aura endpoint (eng:call-aura-api), to document it in AI Knowledge (Tools/Aura API via
  shared:ingest-knowledge), or to clean up or restructure the whole collection.
metadata:
  version: "1.0.1"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Tools/Aura API       # route conventions and auth model the entry documents
    - Compliance/Privacy   # placeholders only: no token, uid or email in an example
---

# update-postman-collection

You keep the **Aura Services** Postman collection current, one route at a time. The script writes
one request and its example responses, shows a diff before writing, and reads every field back
afterwards. It never touches anything else in the collection.

Slow down on two things:

- **An example holds placeholders, never real data.** Use `{{user.token}}`, `{{user.id}}`,
  `tester@example.com`, and a made-up UUID. The script refuses a spec that contains a JWT, a
  literal bearer token, an API key, a real email or a Firebase-uid-shaped string. Never weaken that
  check to get a spec through; replace the value instead.
- **Document what the code does, read from the code.** Read the route, its handler and its error
  map in the service's default branch, and take the status codes from them, never from memory. A
  route's tests are good sources for the response shape.

| | |
|---|---|
| API | `https://api.getpostman.com`, header `X-Api-Key: $POSTMAN_API_KEY` |
| Collection | the one named `Aura Services` (or `--collection-id` / `POSTMAN_COLLECTION_ID`) |
| Writes | `POST`/`PUT /collections/{id}/requests`, `POST`/`PUT /collections/{id}/responses`, and `POST /collections/{id}/folders` with `--create-folders` |
| Never | a full-collection `PUT /collections/{uid}`, or any delete |

Per-request edits are the only write path, so a change to one route can't disturb the other 800.

## Workflow

### 1. Find the current entry (read-only)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/update-postman-collection/scripts/postman.py \
  find --path /communities/subscriptions/:communityId --method GET
```

The match is by method and route shape: the host variable, the query and the `:param` names are
ignored, so `/:userId/:communityId` and `/:communityId` are different routes, while `/:id` and
`/:communityId` are the same. Note the folder the collection uses for that resource (`Users/Authenticate`,
`Communities/Subscriptions/Retrieve`); folders follow resources, not services.

### 2. Write the spec

Write a JSON spec to the scratchpad. The fields and a worked example are in
[`references/spec.md`](references/spec.md). The description must carry the route's status codes;
each entry says when the code is returned. Under the auth rule (2026-11-06) these usually include:
`404` for a row the caller doesn't own, `403` for someone else's list, and `400` for a bad filter or id.

### 3. Dry run

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/update-postman-collection/scripts/postman.py run /path/spec.json
```

It prints `CREATE` (with the target folder) or `UPDATE` (with the request id), a unified diff
of every managed field, and one diff per example response. An existing request is updated in its
current folder and never moved; a `note:` line says when that differs from the spec's folder.
Show the user the plan and the diff.

### 4. Apply and read back

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/update-postman-collection/scripts/postman.py run /path/spec.json --apply
```

Apply recomputes the plan against the live collection, writes only what changed, then reads the
request and each response back and compares every managed field. Report the request id and
`applied and read back`. A request's scripts, tests and any example the spec doesn't name are left
as they are.

Exit codes: `0` done (or nothing to change), `1` a Postman call failed or the read-back didn't
match, `2` `POSTMAN_API_KEY` is unset, `3` the spec is invalid or refused (including a secret or
PII), `4` more than one request matches: pin one with `request_id`.

## Without a key

`POSTMAN_API_KEY` is a personal key with write access to the workspace (`docs/token-inventory.md`).
Without it the script reads and writes nothing: it exits `2` and prints the entry as text
(method, URL, auth, headers, body, description, examples), so someone can add it by hand. Say that
it wasn't written; never report it as done.

## Setup

- `POSTMAN_API_KEY` in `~/.claude/settings.json` `env`, from <https://go.postman.co/settings/me/api-keys>.
- Python 3 standard library only.
