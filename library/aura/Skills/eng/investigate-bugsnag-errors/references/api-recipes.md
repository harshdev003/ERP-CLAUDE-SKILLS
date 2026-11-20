# Bugsnag Data Access API — recipes

Reference for the endpoints this skill calls. Authoritative source: <https://developer.smartbear.com/bugsnag/docs/data-access>.

## Auth + headers (every request)

```
Authorization: token <BUGSNAG_TOKEN>
X-Version:     2
Accept:        application/json
```

`X-Version: 2` is **mandatory** — easy to forget; the helper client in `scripts/bugsnag.py` sets it on the session by default. Forgetting it surfaces as a generic 400 with a misleading message about the path. Don't.

## Base URL

`https://api.bugsnag.com`

(Distinct from the UI host `https://app.bugsnag.com/`.)

## Endpoints used today

| Endpoint | Used in | Returns |
|---|---|---|
| `GET /user/organizations` | first run, finding the `aura-health` org ID | array of orgs the token has access to |
| `GET /organizations/<org-id>/projects` | once per session (cached) | array of projects (12 today) |
| `GET /projects/<project-id>/errors` | Mode 1 (triage) | array of error signatures, paginated |
| `GET /errors/<error-id>` | Mode 2 (focus) | one error's metadata |
| `GET /errors/<error-id>/events` | Mode 2 (focus) | recent events for that error |
| `GET /projects/<project-id>/events/<event-id>` | Mode 3 (trace) | full event payload |

## Pagination

Bugsnag uses **`Link: <url>; rel="next"`** headers — same shape as GitHub's REST API. The client follows the `next` URL until exhausted or `max_pages` is hit. There's no cursor in the URL params; once you have the `next` URL, GET it with no extra params.

Default `per_page` is 30. The errors endpoint accepts up to 100 per page; the events endpoint up to 30.

## Filtering (Mode 1 triage)

Time-window filter syntax for `/projects/<id>/errors`:

```
?filters[event.since]=24h
```

Bugsnag's `event.since` filter accepts **duration shorthand** as a flat key=value query param: `1h`, `24h`, `7d`, `30d`, etc. **Don't** use the array-shape `filters[event.since][][type]=eq&filters[event.since][][value]=<iso>` — that returns 400 on this filter. The `[][type]` / `[][value]` array shape is for typed-comparison filters like `user.id`; `event.since` is its own simpler thing. Which shape a field takes is not a judgement call — a field that declares `match_types` in `event_fields` is typed, one that doesn't is flat, and `build_filters` picks from that.

> **Bugsnag ignores an unknown `filters[...]` key instead of rejecting it** — the request returns 200 and the *unfiltered* result set, which looks like a real answer. `app.version` and `event.app_version` are not fields, despite reading like the obvious ones. Never hand-build a filter param: `bugsnag.py`'s `build_filters()` validates every key against `GET /projects/<id>/event_fields` and raises. The behaviour itself, the valid version/release fields, and the repeated-pair last-wins trap are documented on **`Tools/Bugsnag`** in AI Knowledge — the canonical home for these facts.

Common sort keys for triage:
- `sort=last_seen` (default in this skill)
- `sort=events` (most-eventful first)
- `sort=users` (most users affected first)

> Searching errors by `user.id` lives in the sibling skill `eng:search-bugsnag-by-user` (it uses the typed-comparison filter `filters[user.id][][type]=eq` + `filters[user.id][][value]=<uid>`), not in this skill.

## Rate limits

Per-token rate-limited. Hit a 429 → respect `Retry-After`. The client retries up to 5 times before failing the request. Cross-project fan-out (Mode 1 with `--all-projects`) skips per-project failures silently rather than failing the whole run, so a transient rate-limit on one project doesn't kill the digest.

## Error responses

- `401 Unauthorized` → `BUGSNAG_TOKEN` is missing, expired, or revoked. Hard fail with the canonical "regenerate + add to settings + restart" message.
- `404 Not Found` → the error ID / event ID / project ID doesn't exist (or is in a different org). Surface the URL that 404'd so the user can spot a typo.
- `429 Too Many Requests` → rate-limited. Client backs off using `Retry-After`.

## What's NOT here yet

This skill is **read-only**. The Data Access API also supports writes (`PATCH /errors/<id>` to resolve / ignore, `POST /comments`, etc.) but those are out of scope for this skill. If a future card adds a write path, document the additional endpoints here and discuss the auth scope it needs.
