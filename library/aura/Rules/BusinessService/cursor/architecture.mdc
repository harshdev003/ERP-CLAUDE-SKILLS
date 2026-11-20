---
description: Architecture conventions for Aura services
globs: 
alwaysApply: true
---

# Architecture

> **Shared by every backend service.** This file is identical in all 13 services, in `.claude/rules/` and `.cursor/rules/`. Change every copy in the same batch, and compare git blob hashes to check. Rules that apply to one service only go in that service's `.claude/rules/service-specific.md` (and `.cursor/rules/service-specific.mdc`).

## Architecture conventions

- Follow the established layering (top folders under `src/`):
  - `http`: handlers/controllers for HTTP I/O
  - `services`: business logic
  - `models`: persistence
  - `jobs`: background processing
  - `utils`: shared utilities
  - other service-specific modules as needed (e.g. connectors, listeners)

### When to extract a utility into `src/utils/`

Pull a helper out of a model and into `src/utils/` when it's **mechanically reusable** (no business logic, no model-specific assumptions, no I/O) and **likely to be useful in more than one place**. Single-line shallow clones, object-key strippers, ID-shape helpers, format normalisers — these all belong in `utils/`, not as private functions inside the model that first needed them. Follow the existing util shape: single function, JSDoc, `module.exports = name;` (see `normalizeToObject.js`, `cleanUndefined.js`, `stripId.js`).

Trigger to extract: the same shape (or near-identical shape) shows up in ≥1 other place — or the function is so obviously generic that hiding it inside a model would force the next caller to either re-implement it or import from an unexpected path.

## Model vs Handler Separation (Thin Controller Pattern)

### Handler Responsibilities
Handlers are thin controllers that ONLY handle HTTP concerns:
- Extract parameters from request (userId, query params, body)
- Call model methods
- Check `context.error` once (single point of error handling)
- Return appropriate HTTP responses
- Log HTTP-level events

### Model Responsibilities
Models contain ALL business logic:
- Retrieve and validate data from database (including user retrieval)
- Validate all input parameters
- Perform all business logic operations
- Add errors to context using `context.addError('ERROR_KEY', {data})`
- Return results or null on error

### Pattern Example
```javascript
// ✅ GOOD - Handler delegates everything to model
async function handler(request, response) {
  const {context} = request;
  const userId = context.userId;

  const result = await model.retrieve(userId, {context});
  
  if (context.error) {
    return response.status(context.error.status).json({error: context.error});
  }
  
  return response.status(200).json({result});
}

// ✅ GOOD - Model handles all logic including user retrieval
async function retrieve(userId, {context}) {
  // Model retrieves user
  const user = await postgres.getOneById(USER_TABLE, userId, {context});
  if (!user) {
    context.addError('USER_NOT_FOUND', {userId});
    return null;
  }
  
  // Model validates parameters
  if (!isValid(userId)) {
    context.addError('INVALID_REQUEST', {userId});
    return null;
  }
  
  // Model performs business logic
  return performOperation(user);
}
```

## Router Health Check Pattern

Every router should include ready and status routes:

```javascript
const router = express.Router();

// Routes for the service status
router.get('/ready', ready);
router.get('/status', status);

// Business routes
router.get('/', authenticate, handler.retrieve);
```

### Fire-once side effects need a re-run path

An end-of-lifecycle side effect that writes **user-visible** data needs a way to
be re-run for one entity, because the thing that triggers it will eventually
fail and nothing will come back for it.

`conversation.setSummary` was reachable only from `processEndSideEffects`, which
runs once when a conversation ends. The `endConversation` cron re-ends only a
conversation that is either not yet ended *or* has a dialogue created after
`endedAt` — so an already-ended conversation whose summary call failed kept a
blank title and preview in the user's chat history forever. One flex-capacity
incident produced 61 of them.

When adding such a side effect, add the single-entity re-run path alongside it:

- Expose it as its own privileged route (`POST /:id/<thing>`), guarded with
  `authorize({roles: ['admin', 'agent', 'service', 'system']})` — by-id and
  therefore cross-user, same as `/:conversationId/metrics`.
- Run **only** that side effect. Don't reach the recovery by re-invoking the
  whole lifecycle hook: calling `conversation.end` again with a fresh timestamp
  does re-summarize, but it also rewrites `endedAt`, rebuilds the user profile
  from stale dialogue, and risks duplicate memories.
- Enforce the preconditions the lifecycle hook enforces — incognito, deleted
  user, no dialogues — before spending an LLM call.

([#457](https://github.com/AuraHealth/IntelligenceService/pull/457))
