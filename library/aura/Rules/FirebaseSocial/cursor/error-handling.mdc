---
description: Error handling patterns for Aura Firebase Cloud Functions
globs:
alwaysApply: true
---

# Error Handling Patterns

## Trigger Error Handling

Wrap all I/O and service calls in `try/catch`. Log errors with context metadata and let Firebase handle retries:

```javascript
// ✅ GOOD - Trigger with error handling
const onReelCreated = onValueCreated(
  {ref: '/reels/{reelId}', ...options, region},
  async (event) => {
    const context = new Context('onReelCreated');
    const {reelId} = event.params;

    try {
      const reel = event.data.val();
      if (!reel) return;

      const meta = context.meta({reelId});
      logger.info(`Reel '${reelId}'`, meta);

      await pubsub.publish(contentTopic, 'ReelCreated', data, {context});
    } catch (error) {
      const meta = context.meta({reelId, error: error.message});
      logger.error('Failed to process onReelCreated', meta);
      throw error; // Re-throw so Firebase can retry if configured
    }
  },
);
```

## Service Error Handling

```javascript
// ✅ GOOD - Service with error handling
async function publish(topic, eventName, data, {context}) {
  try {
    await client.topic(topic).publishMessage({data: Buffer.from(JSON.stringify({eventName, data}))});
    logger.debug(`Published ${eventName} to ${topic}`, context.meta({topic, eventName}));
  } catch (error) {
    logger.error(`Failed to publish ${eventName}`, context.meta({topic, eventName, error: error.message}));
    throw error;
  }
}
```

## Guard Clauses

Use early returns for missing or invalid data before performing any side effects:

```javascript
// ✅ GOOD - Guard against null data
const reel = event.data.val();
if (!reel) return;

// ✅ GOOD - Guard against missing required field
const {reelId} = event.params;
if (!reelId) return;
```

## Re-throwing Errors

- Always re-throw errors in trigger handlers so Firebase can apply retry policies.
- Never silently swallow errors in triggers.
- Log the error with `logger.error()` before re-throwing.
