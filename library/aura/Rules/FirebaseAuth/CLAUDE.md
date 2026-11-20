# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every Firebase rules and Functions repo. Change that page first, then every copy.

## Project Purpose

Firebase Cloud Functions that react to Firebase Authentication events:

- `authSignIn`, an Identity Platform `beforeUserSignedIn` blocking function: logs Apple email changes (`APPLE_EMAIL_DETECTOR`) and never refuses a sign-in. It no longer stamps a `userId` custom claim and doesn't change claims.
- `authSignUp`, a `beforeUserCreated` blocking function: refuses script sign-ups and logs every sign-up's client family. Named `authMergeLock` until 2026-11-20, when the account-merge lock it enforced was removed.

## Commands

All scripts run from `functions/` directory. Always run `nvm use` first (Node version via `.nvmrc`).

```bash
nvm use
cd functions

npm run lint          # Run ESLint
npm run lint:cache    # Run ESLint with cache (used in CI)
npm test              # Run unit tests (node:test, no network)

npm run deploy:dev    # Switch to dev project and deploy all functions
npm run deploy:prod   # Switch to prod project and deploy all functions
npm run deploy:user   # Deploy authSignIn only
npm run deploy:signUp # Deploy authSignUp (sign-up check) only

npm run logs          # View Firebase function logs
```

Firebase project aliases: `dev` → `aura-dev`, `prod` → `firebase-auratech16`

## Architecture

Same layout as the other Aura Firebase projects, under `functions/src/`:

- **`triggers/{domain}/`** — Firebase event handlers, one file per function
- **`services/`** — `firebase.js` (Admin app: `getAuth`, `getDatabase`), `signupClient.js` (sign-up client family) and `appleEmail.js` (Apple email detector)
- **`config.js`**, **`context.js`**, **`logger.js`** — Shared support (runtime config, per-invocation tracing, GCP Logging wrapper)

**`index.js`** uses `onInit` to initialize services once on cold start, then spreads all triggers into `module.exports` for Firebase discovery.

## Blocking functions (Identity Platform)

`authSignIn` uses `beforeUserSignedIn` from `firebase-functions/v2/identity` (2nd gen, Node 24, like the other projects). It requires the project to be upgraded to **Firebase Authentication with Identity Platform**, and the function must be registered in the console (Authentication → Settings → Blocking functions) after deployment.

- It fires on **new registrations and every sign-in**, so one function covers new and existing accounts.
- It does **not** fire on token refreshes or custom-token sign-ins.
- **7-second limit:** Auth rejects the sign-in if the function is slower, and the SDK refuses `timeoutSeconds` above 7 — use `runtime.optionsBlocking`.
- **Warm instance on prod:** `minInstances` comes from `AUTH_MIN_INSTANCES` in `functions/.env.<alias>` (resolved at deploy time): `1` in `.env.prod` so sign-ins never wait on a cold start, `0` in `.env.dev`.
- **Fail open:** on any error, log and return nothing so the sign-in proceeds.

## Repo exception to A2 (ENGBACK-2799, 2026-11-15)

- authSignUp refuses sign-ups whose user agent is a script's default label, as a stopgap speed bump. It never allows or exempts anything because of a user agent. The exception ends when App Check or reCAPTCHA replaces it.

## Unresolved-claim alert

The log-based metric `UserClaimUnresolved` and its alert policy (`3550279123770183235`) in `firebase-auratech16` counted `User claim unresolved` lines from `authUserClaim`. Nothing logs that line any more, so the metric stays at zero until the metric and the alert are deleted.

## Logging

Use `logger` (never `console`). Five levels: `debug` (100), `info` (200), `warn` (400), `error` (500), `crit` (600). Default production level is `400`.

## Code Style

- Single quotes, 2-space indent, trailing commas in multi-line structures, `sort-keys`
- Guard clauses over nested conditionals
- `UPPER_SNAKE_CASE` for constants, `PascalCase` for classes, `camelCase` for everything else

## CI/CD

CircleCI pipeline: lint + tests → deploy to dev (on `v*` tags) → promote to prod. **There is no manual approval between dev and prod** — a `v*` tag deploys to both.
