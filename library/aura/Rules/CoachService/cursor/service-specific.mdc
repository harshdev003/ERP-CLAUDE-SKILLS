---
description: Rules that apply to CoachService only
globs:
alwaysApply: true
---

# CoachService rules

Rules for this service only. The rules shared by every backend service are in the other files in this directory and are identical in all 13 services; a general rule goes there, not here.

- **Call `jest.resetModules()` before requiring the module under test when a spec mocks config, the logger or a model.** The jest setup loads the whole app, so handlers are already cached, and a spec's `jest.mock(...)` doesn't reach them otherwise. `test/unit/services/logLeaks.spec.js` shows the pattern (ENGBACK-1922).
- **The SendBird webhooks log ids and counts only:** `channelUrl`, `memberCount`, `type`, `receiverId`, `senderId`. Never spread the payload into `meta`, since it carries chat text, nicknames and photo URLs, and coaching chats can hold health information. Errors log `errorCode` and `errorName`, never the error object, the raw body or the signature (ENGBACK-1922, #262).
- **A SendBird webhook answers 2xx when the entity is gone** (a deleted user, for example) and 5xx only for a real failure, because SendBird retries every 5xx (ENGBACK-1921).
