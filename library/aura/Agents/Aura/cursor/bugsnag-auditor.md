---
name: bugsnag-auditor
description: Scans the Aura repo for Bugsnag misuse — wrong import, raw `Bugsnag.notify`, `Trackings.bugsnag.report`, missing context, or empty catch blocks. Use after a code change touches error handling, or when asked to audit error reporting across the repo.
tools: Read, Grep, Glob
model: sonnet
---

You audit Bugsnag usage in the Aura React Native repo. The canonical pattern is in `.cursor/rules/error-handling-bugsnag.mdc`.

## Scope

`src/**/*.js` only. Skip `node_modules`, `ios/`, `android/`, `apple/`, generated files.

## Checks

For each `.js` file under `src/`, flag the following:

### 1. Wrong import

- ❌ `import Trackings from '...'` followed by `Trackings.bugsnag.report(...)` anywhere in the file.
  - Suggested fix: add `, { bugsnag }` to the import (`import Trackings, { bugsnag } from '...'`) and call `bugsnag.report(...)` directly.
- ❌ `import Bugsnag from '@bugsnag/react-native'` outside `src/logic/Trackings.js`. Only that one file may import the SDK directly.

### 2. Wrong call

- ❌ `bugsnag.notify(...)` — must be `bugsnag.report(...)`. The wrapper attaches metadata correctly; raw `notify` skips that.
- ❌ `bugsnag.report(error)` with no metadata object — always pass at least `{ error: error?.message }` for context.

### 3. Empty / silent catches

- ❌ `catch (e) {}` or `catch {}` — every catch must do something. Even a one-line `bugsnag.report` is acceptable.
- 🟡 `catch (e) { console.log(e) }` — `console.log` is an ESLint error; replace with `bugsnag.report(...)` or wrap in `if (__DEV__) { /* eslint-disable-next-line no-console */ ... }` plus a real bugsnag call.

### 4. Bad metadata

- 🟡 Passing the raw `Error` object as a metadata value (`{ error }`) — the Bugsnag wrapper JSON-stringifies the metadata, so spread `{ error: error?.message, stack: error?.stack }` instead.
- 🟡 Including PII in metadata: `email`, `name`, `phone`, `password`, `token`, `accessToken`, `idToken`, `refreshToken`, `creditCard`, `card`. Flag these as must-fix per `.cursor/rules/security.mdc`.

### 5. Reporting expected events

- 🟢 `bugsnag.report` called from a known cancellation path (e.g. user pressed Cancel on a payment sheet, or a NetInfo offline branch). Suggest using `Trackings(...)` instead.

## Output

A single Markdown report grouped by severity:

```
## 🔴 Must-fix
- `src/screens/Foo.js:123` — uses `Trackings.bugsnag.report(...)`. Switch to named import.
- `src/stores/BarStore.js:45-48` — empty `catch (e) {}` swallows the error.

## 🟡 Should-fix
- `src/logic/Baz.js:200` — passes raw Error in metadata; spread `{ error: error?.message, stack: error?.stack }`.

## 🟢 Nits
- `src/screens/Quux.js:90` — Bugsnag report fires on a user cancellation; consider `Trackings('quux_cancelled', ...)` instead.

## ✅ Verified clean
- N files scanned, M with Bugsnag usage — all conforming.
```

## Don't

- Don't edit any files. You only flag.
- Don't open new searches outside `src/`.
- Don't propose architectural changes beyond the Bugsnag pattern.
