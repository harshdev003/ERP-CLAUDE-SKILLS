---
description: Logging and analytics standards for Aura
alwaysApply: true
---

# Logging & Analytics

## Two Channels

| Purpose | API | Where to call from |
|---------|-----|--------------------|
| Error reporting | `bugsnag.report('Title', { ...context })` | Anywhere a `try/catch` is — see `error-handling-bugsnag.md` |
| Analytics events | `Trackings(eventName, dataObject)` | Screens, stores, helpers — wherever a meaningful user/system event happens |
| Dev-time tracing | guarded `console.log` (only when truly needed) | Behind `if (__DEV__)` and `// eslint-disable-next-line no-console` |

`no-console` is an **ESLint error** in this repo. Do not commit `console.log` without the dev guard + disable comment.

## Analytics — `Trackings(...)`

```js
import Trackings from '<path>/logic/Trackings'

Trackings('family_plan_purchased', {
  productId,
  source: 'onboarding',
  trial: true,
})
```

### Conventions

- **Event name**: `snake_case` verb-phrase that names a discrete event (`screen_viewed`, `cta_pressed`, `purchase_completed`). Keep names stable — analytics dashboards depend on them.
- **Data object keys**: `camelCase`, JSON-serializable scalars/arrays only. Don't include large blobs.
- **Add common context implicitly via `Trackings` itself** — it auto-attaches `Time`, `Day`, `Locale`, `Day N` from the user profile. Don't duplicate those.
- **Do not include PII** (email, name, exact location, payment data). Use the user id only if the analytics platform expects it for identity reconciliation, never as a free field.
- **Don't fire `Trackings` from inside a tight render** — it queues to the JS event loop and adds work.

## Error Reporting — `bugsnag.report(...)`

See `error-handling-bugsnag.md` for the full pattern. Quick reminder:

```js
bugsnag.report('FooStore.loadFoos failed', {
  error: error?.message,
  stack: error?.stack,
  attempt,
})
```

## Dev Tracing

When you genuinely need a `console.log` while debugging:

```js
if (__DEV__) {
  // eslint-disable-next-line no-console
  console.log('[FooStore] state after merge', this.foos.length)
}
```

But before committing — **delete the log** unless the value is high enough to keep around as documentation (in which case it's better as a `// NOTE:` comment).

## Don't

- Don't introduce a new logging library — Bugsnag + `Trackings` cover everything.
- Don't reuse the `Trackings` event name across two different events. Add a suffix or a new name.
- Don't use `bugsnag.notify` directly — the wrapper `bugsnag.report` adds metadata in the format the dashboard expects.
- Don't log full HTTP responses — pick the few fields that help debugging.
