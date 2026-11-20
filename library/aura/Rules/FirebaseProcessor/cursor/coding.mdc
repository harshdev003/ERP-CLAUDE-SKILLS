---
description: Code style guidelines and patterns for Aura Firebase Cloud Functions
globs:
alwaysApply: true
---

# Code Style Guidelines

## Formatting
- Use single quotes for strings
- Use trailing commas in multi-line objects/arrays
- Use consistent indentation (2 spaces)
- Group related imports together
- Prefer async/await

## JavaScript (all)
- `DEV-JS-501`: Use guard clauses to reduce nesting and make exit conditions explicit.
- `DEV-JS-601`: For membership/lookup-heavy logic, prefer `Set`, `Map`, or objects over repeated array `includes` checks.

## Trigger Functions
- `DEV-CF-101`: Create a new `Context` instance at the top of every trigger handler.
- `DEV-CF-102`: Destructure config values (topics, options, region) at module scope, not inside the handler.
- `DEV-CF-103`: Use `logger.info()` to log when a trigger fires, with `context.meta()` for structured metadata.
- `DEV-CF-104`: Use `logger.debug()` for step-by-step operational traces within a trigger.
- `DEV-CF-105`: Guard against missing or invalid event data with early returns before any service calls.
- `DEV-CF-106`: Use `event.params` for path parameters and `event.data.val()` for Realtime Database values.
- `DEV-CF-107`: Pass `{context}` to all service calls for consistent tracing.

## Services
- `DEV-CF-201`: Use `initialize` for service setup called from `onInit`.
- `DEV-CF-202`: Services should be stateless modules with exported functions.
- `DEV-CF-203`: Use `debug` log level for service operational traces.

## Data
- `DEV-BE-401`: When writing to Firebase, guard against `undefined` values.
- `DEV-CF-301`: Always provide an `at` timestamp in Pub/Sub event payloads, falling back to `new Date().toISOString()`.
- `DEV-CF-302`: Pub/Sub event names use PascalCase (e.g., `ReelCreated`, `UserDeleted`).
