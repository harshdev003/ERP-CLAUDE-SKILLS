---
description: Performance guidelines for Aura Firebase Cloud Functions
globs:
alwaysApply: true
---

# Performance Guidelines

## Cold Start Optimization

- Use `onInit` to initialize services once per instance, not inside trigger handlers.
- Keep `require()` calls at the module top level — avoid dynamic requires inside handlers.
- Minimize dependencies to reduce cold start time.

## Runtime Configuration

- Set `memory` and `timeoutSeconds` in `config.runtime.options` and spread into every trigger registration.
- Default settings: `512MiB` memory, `180s` timeout — adjust per trigger needs.

```javascript
// ✅ GOOD - Runtime options applied consistently from config
const onReelCreated = onValueCreated(
  {ref: '/reels/{reelId}', ...options, region},
  async (event) => { ... },
);
```

## Trigger Efficiency

- Return early (guard clauses) when event data is missing or irrelevant — avoid unnecessary service calls.
- Keep trigger handlers focused on a single responsibility.
- Avoid blocking operations inside triggers; prefer async patterns.

## Pub/Sub Performance

- Batch data into a single Pub/Sub message per trigger invocation rather than multiple publishes.
- Only include fields in the Pub/Sub payload that consumers actually need.

## Memory Management

- Do not accumulate state in module-level variables across invocations.
- Release large objects from scope as soon as they are no longer needed.
- Avoid memory leaks from unclosed streams or dangling event listeners.
