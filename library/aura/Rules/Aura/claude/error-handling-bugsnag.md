---
description: Bugsnag import + report convention for error tracking in Aura
globs: src/**/*.{js,jsx}
alwaysApply: false
---

# Bugsnag Error Reporting

All caught errors funnel through `bugsnag.report(...)` exposed by `src/logic/Trackings.js`. The function wraps `Bugsnag.notify` and adds the supporting data as event metadata.

## Correct Import

```js
import { bugsnag } from '<relative-path>/logic/Trackings'
```

The path depends on where you are — examples:

- From `src/screens/Foo.js` → `import { bugsnag } from '../logic/Trackings'`
- From `src/stores/FooStore.js` → `import { bugsnag } from '../logic/Trackings'`
- From `src/screens/familyPlan/FamilyPlanOnboarding.js` → `import { bugsnag } from '../../logic/Trackings'`

If `Trackings` is **already imported** in the file as the default export (used for analytics), add a separate named import for `bugsnag` — do not use `Trackings.bugsnag.report`.

```js
// ✅ GOOD — separate named import even when Trackings is also used
import Trackings, { bugsnag } from '../logic/Trackings'
```

## Reporting an Error

```js
bugsnag.report('Title that identifies the failure', {
  // any context that helps debugging — keep it JSON-serializable
  userId: profile?.id,
  attemptedAction: 'purchase.subscribe',
  productId,
  errorMessage: error?.message,
})
```

- The first argument is a short, **stable** title. When it's a string, the wrapper sets it as the Bugsnag **`groupingHash`**, so it is literally the grouping key — errors with the same title collapse into one Bugsnag error group, regardless of stack frame or release. This mirrors the native app's `event.groupingHash = message`.
- **Keep the title constant — never interpolate per-instance values into it.** `bugsnag.report(\`Upload failed for ${userId}\`, ...)` creates a brand-new group for every user and fragments the dashboard. Put the variable part in the metadata object instead: `bugsnag.report('Upload failed', { userId, ... })`.
- The second argument is plain-object metadata. The wrapper guards against unserializable input by JSON-validating it; it still helps to keep your context flat and small.
- Passing a string as the first argument auto-wraps it in `new Error(string)`, so you can call `bugsnag.report('Foo failed', { ... })` even when you don't have an `Error` object.
- Passing a real `Error` object (rather than a string) leaves `groupingHash` unset, so Bugsnag groups it by its default algorithm (error class + stack). That's the right behavior for genuine exceptions — e.g. `handleFBError`.
- **Optional third argument = metadata section.** `bugsnag.report('Title', { ... }, 'network')` routes the metadata into a named Bugsnag section/tab (mirrors the native app's `event.addMetadata(dict, section:)`). Omit it and metadata lands in the default `eventStack` section, as before.

## User identity is set globally — don't put it in metadata

The current user is attached to **every** Bugsnag event via the native **User** tab, set once at login and cleared at logout:

- **Login** — `ProfileStore.getProfile` calls `setBugsnagUser(profile)` (exported from `logic/Trackings`) whenever the profile loads.
- **Logout** — `app.js handleLogout` calls `clearBugsnagUser()` alongside the other identity resets (Mixpanel, OneSignal, …), so a later session isn't mis-attributed to the previous user.

This mirrors AuraConsumerApp's `BugsnagReportService.setUser`. Two consequences for call sites:

- **You don't need `userId` in `bugsnag.report` metadata** — it's already on the event in the User tab. Prefer dropping it; keep it only when a report genuinely concerns a *different* id than the logged-in user.
- **Only the Firebase id is attached — never email or name** (PII, per `security.md`). Don't pass email/name to `setBugsnagUser`.

## Anti-Patterns

```js
// ❌ BAD — never use Trackings.bugsnag.report
Trackings.bugsnag.report('...', {})

// ❌ BAD — silently swallowing an error
try {
  await doThing()
} catch (e) {
  // nothing
}

// ❌ BAD — console.log only (lint error in this repo)
catch (e) {
  console.log(e)
}

// ✅ GOOD
catch (e) {
  bugsnag.report('doThing failed', { error: e?.message, context })
}
```

## When NOT to Report

- Expected validation failures (form validation, "no internet" warnings the user already sees).
- User cancellations (e.g. user hits Cancel on a payment sheet — that's a normal exit).
- Promises that intentionally race and one losing.

For those cases, log via `Trackings('event_name', data)` if you want analytics, but skip Bugsnag.

## Shared Helper

`src/logic/Trackings.js` also exports `handleFBError(error, cb)` which is a thin wrapper over `bugsnag.notify(error, cb)` for Firebase callback contexts.
