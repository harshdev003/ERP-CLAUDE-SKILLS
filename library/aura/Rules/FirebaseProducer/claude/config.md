---
description: Config binding and documentation conventions for Aura Firebase Cloud Functions
globs:
alwaysApply: true
---

# Config convention

- Define config bindings at the top of each module via destructuring and use explicit constant aliases for readability.

```js
const {
  runtime: {options, region},
  pubsub: {
    topics: {content: contentTopic},
  },
} = config;
```

- When a new environment variable is added (or an existing one changes), update `README.md` in the same change. Config constants in `functions/src/config.js` are not listed in the README; the config file documents them.

## Config Shape

`config.js` exports a single object with these standard top-level keys:

| Key | Purpose |
|-----|---------|
| `logger.level` | Minimum log severity (400 = WARNING by default) |
| `pubsub.origin` | Origin identifier stamped on outgoing Pub/Sub messages |
| `pubsub.topics` | Topic name map (coaching, content, payment, user, etc.) |
| `runtime.options` | Firebase function options (memory, timeoutSeconds) |
| `runtime.region` | Deployment region (`us-central1`) |
| `service.name` | Derived from `package.json` scoped name |
| `service.version` | Derived from `package.json` version |

## Constants vs Environment Variables

### Use Config Constants For:
- Runtime options (memory, timeout)
- Pub/Sub topic names
- Validation lists (emotion types, locale codes, allowed values)
- Region and origin identifiers

### Use Environment Variables For:
- API keys and secrets
- External service credentials
- Environment-specific feature flags

```javascript
// ✅ GOOD - Constants in config
runtime: {
  options: {memory: '512MiB', timeoutSeconds: 180},
  region: 'us-central1',
},

// ✅ GOOD - Secrets via environment variables
service: {
  apiKey: process.env.SERVICE_API_KEY,
}
```
