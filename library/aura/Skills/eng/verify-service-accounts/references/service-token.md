# What `SERVICE_TOKEN` actually is

Read this before changing any check in `scripts/verify.py`. It decides what
"still valid" means, and the intuitive answer is wrong in both directions.

## The shape

Every `SERVICE_TOKEN` in AuraInfra is a **Firebase ID token** — an RS256 JWT
issued by `securetoken.google.com`, `sign_in_provider: custom`, carrying a
1-hour `exp` and the service's own address in `email`.

So the credential does structurally expire. What matters is that **user-service
stops checking**.

## Why an expired token keeps working

`POST /users/verifyToken` resolves a token in this order
(`UserService/src/models/user.js` → `verifyToken`):

1. A 24-hour result cache, keyed on the token string.
2. **`postgres.getOneById(TOKEN_TABLE, token)`** — a token table keyed by the
   raw token string. A hit returns `{id: tokenDb.userId, verified: true}` and
   returns immediately.
3. Only on a miss: `firebase.decodeToken(token)`, which is
   `getAdminAuth().verifyIdToken(token)` and *does* enforce `exp`.

The last step of a successful Firebase verification **upserts the token into
that same table**. So the first time a freshly minted token is verified it is
persisted, and from then on every check short-circuits at step 2 — where
nothing re-reads `exp`, and nothing re-reads the account's role either.

A stored `SERVICE_TOKEN` is therefore a **long-lived, database-backed bearer
credential that merely looks like a 1-hour JWT**.

## What follows for the checks

**`exp` is not a health signal.** 26 of the 28 stored tokens carry an `exp`
from 2021–2023 and every one of them authenticates today. The skill reports
expiry because the card asks for it and because it explains the shape, but it
never counts a past `exp` as a fault — a check that did would report a
fleet-wide outage that is not happening. `tests/test_service_account_verdict.py`
→ `TestExpiryIsInformational` pins this.

**`tokenUserId` is usually absent.** It is set only on the Firebase branch. The
persisted branch returns `id` alone, which is the shape 26 of 28 real
identities come back as:

```json
{ "id": "QhSqZC6ehKVNKcYRFIM9SctRESW2", "verified": true }
```

Asserting `tokenUserId == id` unconditionally — the reading the card's "healthy
response" sample invites, and which `Tools/Aura API/Users/Verify Token` states
as always true — fails every long-lived service account while nothing is wrong.
Compare the pair only when the server sent both.

**`verifyToken` alone cannot see the PROD-6468 fault.** The token table maps
token → `userId` and nothing else. It does not read `role`, and on that branch
it does not even read `deletedAt`. A principal whose role has gone `null` keeps
returning `verified: true` forever. That is why the role probe is a separate
call and why a run is not green without it.

## Why the role probe is `GET /users/me`

`authorize({roles})` (`UserService/src/http/middlewares/authorize.js`) calls
`userModel.retrieve(context.userId)` and tests `roles.includes(user.role)`.

`GET /users/me` is `authenticate` + the `retrieve` handler, which calls that
same `userModel.retrieve` and returns the row. So the role the skill reads is
the identical value, from the identical call, that every authorization decision
in the product is made on — rather than a lookalike read of some other store.
It takes no parameters beyond the bearer, is a pure read, and returns `role`,
`deletedAt` and `email` in one call.

## Roles are not uniform across identities

Thirteen of the fourteen identities are `@services.*` accounts carrying role
`service`. `emotion-system` is a `@systems.*` account carrying `system` — a
real role, accepted by routes gated on
`authorize({roles: ['admin', 'agent', 'service', 'system']})`. Both of its
accounts are live and correctly configured:

| Env | UID | Email | Role |
|---|---|---|---|
| prod | `Vq4GsXRqUPT8Oo0fylrXogMYcDS2` | `<redacted-email>` | `system` |
| dev | `aRe7NVIq1aX2t6tTMh4pX3S9sq93` | `<redacted-email>` | `system` |

Read the sub-domain from the **first** label of the address domain. A dev
address carries an extra label (`systems.dev.aurahealth.io`), so a rule keyed
on the second-to-last label answers `dev` in one environment and `aurahealth`
in the other.

So the expectation is resolved per identity from the account's sub-domain rather
than fixed globally. A single `service` expectation reports emotion-system's
live, correctly-configured account as broken, which is the same shape of false
positive as the `tokenUserId` trap above: a check that looks obviously right
against the majority and is wrong about the rest.

## A dead credential is not the same as a broken service

`emotion-system`'s stored token is genuinely dead in both environments —
expired 2025-01-07, never persisted, `verified: false` and 401. The accounts
behind them are fine (above); only the credentials are stale. Nothing is
failing because of it. Its one consumer path is
`POST {content}/contents/recommend/{userId}`, and that route is registered
without `authenticate`:

```js
router.post('/recommend',          authenticate, recommend);
router.post('/recommend/:userId',                recommend);   // no auth
```

The handler takes the user id from `request.params`, never from
`context.userId`, so the Bearer header is ignored entirely. Verified against
production: the dead token, no `Authorization` header, and a garbage bearer all
return byte-identical 200s.

So a FAIL from this skill means *the credential does not work*. Whether that is
an outage depends on whether the consumer authenticates — read the consuming
service's router before escalating.

## Why the tokens are read, never minted

Minting a fresh token per account would be simpler and would sidestep all of
the above. It is the wrong trade twice over:

- **It would test the wrong credential.** A freshly minted token proves the
  account can sign in. The thing that breaks — and the thing every service
  actually presents — is the token sitting in the secret.
- **It would silently defeat `eng:cleanup-orphaned-auth`.** `signInWithCustomToken`
  is a sign-in, so it bumps `lastLoginAt` / `lastRefreshAt`. That skill's
  `--min-inactive-days` gate skips accounts active inside the window, so a
  recurring mint-based sweep would keep every service account permanently
  "active" and quietly disable the gate. Its SKILL.md carries the same warning
  for the same reason.

Both probes here are reads: `verifyToken` sends the stored token in a body,
`GET /users/me` sends it as a bearer. Neither signs in, so neither touches the
activity record.

One side effect is worth naming: on the Firebase branch `verifyToken` upserts
the token row and sets the cache. For an already-persisted or already-expired
token that branch is never reached, so in practice a sweep writes nothing. It
is also exactly what a real caller's first authenticated request does.

## The three sources, and which can actually be compared

The same `SERVICE_TOKEN` exists in three places:

| Source | What it is | Comparable? |
|---|---|---|
| `k8s/apps/**/.env.{env}` | plaintext, the source of truth | yes — this is what the skill probes |
| `k8s/apps/**/*-secret.json` | the committed `SealedSecret` | **no** — see below |
| the live cluster `Secret` | plaintext, base64 | yes, by fingerprint (`--check-cluster`) |

The committed JSON is **sealed**: RSA+AES ciphertext under a random per-seal
session key, so the same plaintext seals to a different ~2 KB string every
time. Its value can never be matched against the `.env` one, by fingerprint or
otherwise. Only its **presence** is checkable, which is still worth doing —
that is how `k8s/apps/ai/emotion-system/dev/` was found to carry a
`SERVICE_TOKEN` in its `.env.dev` with no committed secret at all.

# Sources

- `AuraHealth/ContentService` — `src/http/router/contents/index.js`,
  `src/http/handlers/contents/recommend.js` (read at `6714f57d`, 2026-09-23).
- `AuraHealth/EmotionSystem` — `src/services/content_service.py`.
- `AuraHealth/UserService` — `src/models/user.js` (`verifyToken`),
  `src/services/firebase.js` (`decodeToken`),
  `src/http/middlewares/authorize.js`, `src/http/router/users/index.js`
  (read at `e792e77`, 2026-09-23).
- `AuraHealth/AuraInfra` — `k8s/apps/**/.env.{dev,prod}` and the ingress hosts
  in `k8s/apps/services/ingress/{dev,prod}/aura-services-ingress.yaml`
  (read at `1ab239c`, 2026-09-23).
- Live sweep of all 28 identities against both environments, 2026-09-23.
- [PROD-6468](https://linear.app/aura-health/issue/PROD-6468) — the incident.
- [PROD-6470](https://linear.app/aura-health/issue/PROD-6470) — this skill.
