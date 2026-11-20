---
description: Architecture conventions for Aura Firebase Cloud Functions
globs:
alwaysApply: true
---

# Architecture

## Architecture conventions

- Follow the established layering (top folders under `functions/src/`):
  - `services/`: external integrations (firebase, pubsub, mixpanel, etc.)
  - `triggers/`: event handlers organized by domain
  - `config.js`: application configuration (runtime options, pubsub topics, logger level)
  - `context.js`: Context class for request tracking and metadata
  - `logger.js`: Google Cloud Logging wrapper
  - `index.js`: entry point with `onInit` initialization

## Trigger Structure

Each trigger is a standalone module in `src/triggers/{domain}/on{Entity}{Action}.js`:
- Declare the firebase-functions v2 event listener
- Create a new `Context` instance per invocation
- Destructure event parameters and data at the top
- Call the appropriate service (pubsub, firebase, etc.)
- Never share `Context` instances between triggers

## Entry Point Pattern

`src/index.js` uses `onInit` to initialize services once on cold start, then spreads all triggers into `module.exports` for Firebase discovery:

```javascript
// ✅ GOOD - Entry point pattern
const {onInit} = require('firebase-functions/v2/core');
const Context = require('./context');
const triggers = require('./triggers');
const firebase = require('./services/firebase');

onInit(async () => {
  const context = new Context('functionName');
  await firebase.initialize({context});
});

module.exports = {
  ...triggers,
};
```

## Triggers Index Pattern

`src/triggers/index.js` aggregates all domain trigger modules:

```javascript
// ✅ GOOD - Triggers aggregator
module.exports = {
  ...require('./reel'),
  ...require('./user'),
  // one spread per domain folder
};
```

## Service Pattern

Services in `src/services/` are stateless modules. Each service exports discrete functions and an optional `initialize` function for `onInit` setup.
