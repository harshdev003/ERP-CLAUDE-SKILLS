---
description: Security and privacy requirements for Aura
alwaysApply: true
---

# Security & Privacy

## Sensitive Data

- **Never log PII**: no user emails, names, IDs, health data, payment data, tokens, or passwords in `console.log`, `bugsnag.report` metadata, or `Trackings(...)` payloads.
- **Never hardcode secrets**: API keys, tokens, and credentials live in `.env` (and the platform-specific config files like `Info.plist` / `gradle.properties`). They are loaded via `react-native-dotenv`. The `.env` file must remain gitignored.
- **Never check in real production credentials** — even in code comments or test fixtures.

## Secure Storage

- The codebase currently has **no secure-storage wrapper** and no `react-native-keychain` (or equivalent) dependency. Persistent client-side data lives in `@react-native-community/async-storage` (legacy package, version 1.11.0) — which is **plaintext on disk**.
- This is a known gap, not a target. **Do not** silently add `react-native-keychain` or another secure-storage library in a feature PR — it's a cross-cutting decision that affects auth, logout, existing stored data, and migration. Raise it for discussion first.
- Even with the AsyncStorage limitation, **don't make it worse**: don't write fresh JWTs, refresh tokens, or PII you weren't already storing. If a new feature genuinely needs persistent secrets, flag it before implementing.
- Hashing: `react-native-sha256` is available for non-secret-storage uses (e.g. content fingerprints, idempotency keys).

### The one keychain surface: `SharedKeychainService` (not a secure-storage wrapper)

`src/services/SharedKeychainService.js` + `ios/Aura/SharedKeychain.m` reach the iOS
keychain, but they are **not** the general secure-storage wrapper the bullets above
say we don't have. They exist for exactly one job: the cross-app referral handoff to
Aurie through the `com.aurahealth.shared` access group (PROD-4479 / PROD-4481). The
paragraph above still stands — nothing else in the app has a secure-storage path, and
adding `react-native-keychain` remains a discussion, not a feature-PR decision.

Rules if you touch it:

- **Nothing secret goes in the shared group.** It is readable by every app on the team
  prefix. Referral context only — never tokens, JWTs, credentials, or health data.
- **Every item must be declared in `src/data/sharedKeychainKeys.js`.** The service
  rejects any key outside that registry, so the registry is the reviewable allowlist
  for what leaves this app. It is a cross-repo contract with Aurie's
  `SharedKeychainKey` — changing a value there breaks the channel silently.
- **`com.AuraMobile.sharedItems` must stay first** in `keychain-access-groups` in both
  `ios/Aura/Aura.entitlements` and `ios/Aura/AuraDebug.entitlements`. The first entry
  is the default group for writes that don't name one; prepending anything repoints
  existing writes.
- Don't reach for this module for local persistence — that's still AsyncStorage.

## Network

- All HTTP calls go through `axios` via `src/api.js` (the central network module, default-exports `Api`) and the higher-level wrappers in `src/requests/`. Don't use raw `fetch` from random screens.
- HTTPS only. Never disable TLS validation, even temporarily for debugging.
- Token refresh and session lifecycle live in the Auth-related stores. Don't roll auth logic in screens or per-feature stores.

## Authentication

- Trust `__DEV__` for dev-only behavior; never gate security logic on `__DEV__` (a release build with `__DEV__ === false` would skip the gate).
- On logout, clear user-scoped state in every relevant Reflux store. Don't leave user data behind in `seamless-immutable` collections.
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
