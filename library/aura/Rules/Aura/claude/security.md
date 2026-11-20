---
description: Security and privacy requirements for Aura
alwaysApply: true
---

# Security & Privacy

## Sensitive Data

- **Never log PII**: no user emails, names, IDs, health data, payment data, tokens, or passwords in `console.log`, `bugsnag.report` metadata, or `Trackings(...)` payloads.
- **Never hardcode secrets**: API keys, tokens, and credentials live in the cask (`src/cask/meals.js`, plus the platform-specific config files like `Info.plist` / `gradle.properties`). `src/cask/` is gitignored in its entirety — see the cask rules below. There is **no repo-root `.env`** and no `react-native-dotenv`; both were removed in PROD-4975 because the only thing the file carried was two non-secret layout constants, now inlined in `src/styles/dimensions.js`. Don't reintroduce a `.env` to hold a secret — put it in the cask so the sample-mirroring rule below applies to it.
- **Never check in real production credentials** — even in code comments or test fixtures.

### Adding a key to the cask — mirror it into the sample (MANDATORY)

Whenever you add a key to `src/cask/meals.js` (i.e. to `src/cask/{dev,prod}/meals.{dev,prod}.js`), **add that same key to `src/cask/meals.sample.js` in the same commit**:

- **Same key name**, inserted in **alphabetical position** — the sample is sorted, and appending to the bottom is what let keys drift and duplicates hide.
- **Value redacted as `****`.** Keep the shape where the shape is the information — `price_****`, `pk_test_****`, `https://****.example.com/` — but never anything more.
- **Never the real value.** `meals.sample.js` is committed; a real value in it is a committed secret. This is the one hard line here.

Why it is mandatory: `src/cask/` is gitignored, so the sample is the **only** record in the repo of what a cask must contain. A key that exists solely in one person's local file is precisely how three developers' copies drifted apart and how builds started failing on `undefined` config. The same applies to `GoogleService-Info.sample.plist` if you add a plist key.

### Analytics are production on every env (PROD-6414)

`sync-cask switch dev` does **not** isolate analytics. Measured 2026-09-22:
`src/cask/dev/meals.js` and `src/cask/prod/meals.js` carry the **same** `mixpanelToken`
— the production project — and a second key `mixpanelTokenDev` sits in both and is read
nowhere in the repo (`app.js` initialises with `code.mixpanelToken` unconditionally).

So every dev, QA and sim-test event lands in the production Mixpanel project, whatever the
cask or build configuration says. Test accounts use `test+{unix_timestamp}@aurahealth.io`
and are filterable, which is what has made this survivable — but filterable is not
isolated: it requires every dashboard and every ad-hoc query to remember the filter.

`ExperimentHelper.save` writes to `users/<id>/experiments/<name>` in Firebase RTDB on the
same basis, so a test account's assignment record is production data too.

**Decided 2026-09-22: accepted, not fixed.** Everything reports to production Mixpanel and
that is the intended state for now. This section exists so nobody mistakes it for a bug, and
so nobody "fixes" it by accident.

Two things follow from the decision:

- **Filter by the test-email convention, always.** `test+{unix_timestamp}@aurahealth.io` is
  the only thing separating synthetic traffic from real users in any dashboard or ad-hoc
  query. It is not automatic — a query that forgets it silently includes sim runs.
- **Do not re-introduce a `__DEV__` switch in `app.js`.** That was tried and reverted: it
  tracked the build configuration rather than the cask, so a Release build on the dev cask
  reported to production anyway. Selecting on the cask is the right shape, and it is already
  what the code does — the cask simply carries the same token for both envs.

If the decision is revisited, the change is a cask edit, not a code one: put the dev
project's token in `dev/meals.js`'s `mixpanelToken` and delete `mixpanelTokenDev`. The dead
key is also absent from `meals.sample.js`, which violates the mirroring rule above;
deleting it resolves both.

## Secure Storage

- The codebase currently has **no secure-storage wrapper** and no `react-native-keychain` (or equivalent) dependency. Persistent client-side data lives in `@react-native-community/async-storage` (legacy package, version 1.11.0) — which is **plaintext on disk**.
- This is a known gap, not a target. **Do not** silently add `react-native-keychain` or another secure-storage library in a feature PR — it's a cross-cutting decision that affects auth, logout, existing stored data, and migration. Raise it for discussion first.
- Even with the AsyncStorage limitation, **don't make it worse**: don't write fresh JWTs, refresh tokens, or PII you weren't already storing. If a new feature genuinely needs persistent secrets, flag it before implementing.
- Hashing: `react-native-sha256` is available for non-secret-storage uses (e.g. content fingerprints, idempotency keys).

## Network

- All HTTP calls go through `axios` via `src/api.js` (the central network module, default-exports `Api`) and the higher-level wrappers in `src/requests/`. Don't use raw `fetch` from random screens.
- HTTPS only. Never disable TLS validation, even temporarily for debugging.
- Token refresh and session lifecycle live in the Auth-related stores. Don't roll auth logic in screens or per-feature stores.

## Authentication

- Trust `__DEV__` for dev-only behavior; never gate security logic on `__DEV__` (a release build with `__DEV__ === false` would skip the gate).
- On logout, clear user-scoped state in every relevant Reflux store. Don't leave user data behind in `seamless-immutable` collections. The same applies on an account switch: `ProfileStore` clears the global profile when the profile id changes, so code reading `getProfile().id` never sends the previous account's id.
- Session expiry (HTTP 401) routes the user back to login — don't conflate it with "permission denied".

## Privacy / Consent

- Permissions requests (`react-native-permissions`) are **just-in-time**, not at app launch. Show the explainer modal that the design team has already specced before triggering the system prompt.
- Tracking SDKs (AppsFlyer, IDFA, Branch attribution) follow ATT consent. Don't fire attribution events before the consent step.
- Healthkit, contacts, calendar — same just-in-time rule. Always provide a usage description in `Info.plist`.

## User Input

- Sanitize user-generated text before sending to the API; the server should also sanitize but client-side validation prevents trivially bad payloads.
- Never `eval` or `new Function` user input.
- Deep links: validate every parameter before using it to navigate or fetch data — never trust the URL.

## User-Facing Errors

- Show friendly, translated copy (`t('errors.generic')`). Never show raw API error messages, stack traces, or internal IDs in the UI.
- Detailed errors go to Bugsnag (with PII stripped); the user gets the friendly version.

## Don't

- Don't commit `.env` or any file containing real keys.
- Don't log JWTs, even partially.
- Don't pass credentials through route params.
- Don't store anything sensitive in `AsyncStorage`.
