# Spec format

One JSON object per request.

| Field | Required | Meaning |
|---|---|---|
| `folder` | yes | Folder path in the collection, `/`-separated, e.g. `Communities/Subscriptions/Retrieve`. Must exist unless `--create-folders` is passed |
| `method` | yes | `GET`, `POST`, `PUT`, `PATCH` or `DELETE` |
| `path` | yes | The route as the router declares it, from `/`, with `:params`: `/communities/subscriptions/:communityId`. No host, no query |
| `name` | no | The request's name; defaults to `path`, the collection's usual style |
| `path_variables` | no | `{param: example}`, e.g. `{"communityId": "{{community.id}}"}`. A param left out gets an empty value |
| `query` | no | `{key: value}`, or a list of `{key, value, description}` |
| `auth` | no | `user` (default, `{{user.token}}`), `coach`, `admin`, `none` (an unauthenticated route), `inherit` |
| `headers` | no | `{name: value}`. `Content-Type: application/json` is added when there is a body |
| `body` | no | An example request body: a JSON value, or a string sent as-is. Leave it out for a route without a body; then the request's body is left unchanged |
| `description` | one of the two | Markdown: what the route does, who may call it, the response shape |
| `status_codes` | one of the two | `{"200": "when", "404": "when"}`, rendered as a `## Status codes` table under the description |
| `responses` | no | A list of `{name, code, body}` example responses. One with the same `name` is updated; others are created; none is deleted |
| `request_id` | no | Pin the request to update when the route's shape changed or several requests match |

Placeholders only: `{{user.token}}`, `{{user.id}}`, `tester@example.com`, a made-up UUID. The
script refuses a JWT, a literal bearer token, an API key, an email outside `example.com` /
`example.org`, and a 28-character mixed-case Firebase-uid-shaped string.

## Example

```json
{
  "folder": "Communities/Subscriptions/Retrieve",
  "method": "GET",
  "path": "/communities/subscriptions/:communityId",
  "path_variables": {"communityId": "{{community.id}}"},
  "auth": "user",
  "description": "Returns the caller's own subscription to a community. The user comes from the token, never from the request; the payment fields (`revenue`, `transactionId`) are left out.",
  "status_codes": {
    "200": "The caller's subscription row",
    "400": "`communityId` is not a UUID",
    "401": "No or invalid token",
    "404": "The caller has no subscription to this community"
  },
  "responses": [
    {"name": "200 OK", "code": 200, "body": {"id": "c0ffee00-0000-4000-8000-000000000001", "communityId": "2b7c9e1a-3d4f-4a5b-8c6d-7e8f9a0b1c2d", "userId": "{{user.id}}", "productId": "community-monthly"}},
    {"name": "404 Not Found", "code": 404, "body": {"error": {"key": "COMMUNITY_SUBSCRIPTION_NOT_FOUND", "message": "The community subscription was not found", "status": 404}}}
  ]
}
```
