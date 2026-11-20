---
description: Logging standards and best practices for Aura Firebase Cloud Functions
globs:
alwaysApply: true
---

# Logging Standards

## Logger Usage Requirements

- **ALWAYS use logger instead of console statements** — No `console.log()` calls in production code.
- **Use appropriate log levels** — debug, info, warn, error, crit.
- **Always pass `context.meta()`** as the metadata argument for structured, traceable logs.
- **Never log sensitive information** — No tokens, passwords, or personal data.

## Log Level Guidelines

Severity numbers map to Google Cloud Logging levels:

| Method | Severity | When to use |
|--------|----------|-------------|
| `logger.debug()` | 100 | Step-by-step operational traces, variable values |
| `logger.info()` | 200 | Trigger fired, important business events, successful operations |
| `logger.warn()` | 400 | Missing optional data, fallback scenarios, unexpected-but-handled state |
| `logger.error()` | 500 | Failed operations, caught exceptions |
| `logger.crit()` | 600 | Critical failures requiring immediate attention |

> Default `config.logger.level` is `400` (WARNING), meaning `debug` and `info` are suppressed in production unless the level is lowered.

## Structured Logging with Context

Always use `context.meta()` to attach the `functionName` and `transaction` UUID to every log entry:

```javascript
const meta = context.meta({reelId});
logger.info(`Reel '${reelId}'`, meta);
// Produces: {message, functionName, transaction, reelId}
```

## Required Logging Points

- **Trigger fired**: `logger.info()` with the entity ID when a trigger handler begins.
- **Service calls**: `logger.debug()` before and after external calls.
- **Error scenarios**: `logger.error()` in every `catch` block with `error.message`.
- **Initialization**: `logger.info()` or `logger.debug()` during `onInit` service setup.

## Example

```javascript
const context = new Context('onReelCreated');
const {reelId} = event.params;
const meta = context.meta({reelId});

logger.info(`Reel '${reelId}'`, meta);

try {
  await pubsub.publish(contentTopic, 'ReelCreated', data, {context});
} catch (error) {
  logger.error('Failed to publish ReelCreated', context.meta({reelId, error: error.message}));
  throw error;
}
```
