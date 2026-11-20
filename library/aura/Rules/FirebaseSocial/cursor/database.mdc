---
description: Firebase database conventions for Aura Cloud Functions
globs:
alwaysApply: true
---

# Firebase Database Conventions

## Realtime Database Triggers

- Use `onValueCreated`, `onValueUpdated`, `onValueDeleted`, `onValueWritten` from `firebase-functions/v2/database`.
- Always specify the `ref` path with params using `{paramName}` syntax.
- Spread `options` and include `region` from config when registering triggers.

```javascript
// ✅ GOOD - Realtime Database trigger
const {onValueCreated} = require('firebase-functions/v2/database');

const onReelCreated = onValueCreated(
  {ref: '/reels/{reelId}', ...options, region},
  async (event) => {
    const {reelId} = event.params;
    const reel = event.data.val();
    // ...
  },
);
```

## Firestore Triggers

- Use `onDocumentWritten`, `onDocumentCreated`, `onDocumentUpdated`, `onDocumentDeleted` from `firebase-functions/v2/firestore`.
- Use `event.data.after.data()` for the new state and `event.data.before.data()` for the old state.

## Firebase Admin

- Initialize Firebase Admin via the `services/firebase.js` service in `onInit`.
- Never import `firebase-admin` directly in trigger files; use the `services/firebase.js` abstraction.
- Access Realtime Database via `firebase.database()` and Firestore via `firebase.firestore()`.

## Data Safety

- Always guard against `null` values from `event.data.val()` (deleted nodes return `null`).
- Guard against `undefined` properties before writing back to Firebase.
- Use `new Date().toISOString()` for timestamps to ensure consistent UTC format.
- Never write `undefined` to Firebase paths — it will silently be ignored or cause errors.
