---
description: Error handling patterns for services, database, and API calls
globs: 
alwaysApply: true
---

# Error Handling Patterns

> **Shared by every backend service.** This file is identical in all 13 services, in `.claude/rules/` and `.cursor/rules/`. Change every copy in the same batch, and compare git blob hashes to check. Rules that apply to one service only go in that service's `.claude/rules/service-specific.md` (and `.cursor/rules/service-specific.mdc`).

## Service Errors
```javascript
try {
  const result = await service.performOperation();
  logger.info('Operation completed successfully', {result});
  return result;
} catch (error) {
  logger.error('Operation failed', {error: error.message, stack: error.stack});
  throw new Error('Service operation failed');
}
```

## Database Errors
```javascript
try {
  const result = await postgres.query('SELECT * FROM users WHERE id = $1', [userId]);
  return result.rows[0];
} catch (error) {
  logger.error('Database query failed', {error: error.message, query: 'SELECT * FROM users'});
  throw new Error('Database operation failed');
}
```

## API Errors
```javascript
try {
  const response = await axios.get(apiUrl);
  return response.data;
} catch (error) {
  if (error.response) {
    logger.error('API request failed', {status: error.response.status, data: error.response.data});
  } else {
    logger.error('API request failed', {error: error.message});
  }
  throw new Error('External API request failed');
}
```


## Context Error Pattern

### Required parameters fail fast
A parameter that the rest of the function can't work without is **required**, not silently optional. Fail fast: check it at the top of the function, `addError` with a specific key, return early. Don't carry the missing-value condition through downstream guards (`if (lockKey)`, `if (conversationId)` sprinkled later in the function). Moving the check to the top makes the rest of the body cleaner and surfaces the contract at the API boundary.

```javascript
// ✅ GOOD - precondition enforced once; rest of the function trusts it
async function setMemory({conversationId, dialogues, user}, {context}) {
  if (!conversationId) {
    context.addError('MEMORY_MISSING_CONVERSATION_ID');
    return;
  }
  const lockKey = `memoryPrompter/lock/${conversationId}`;  // unconditional
  // ...
}

// ❌ BAD - silently optional, then guarded everywhere
async function setMemory({conversationId, dialogues, user}, {context}) {
  const lockKey = conversationId ? `memoryPrompter/lock/${conversationId}` : null;
  if (lockKey) { /* ... */ }
  // ... every downstream use needs the same guard
}
```

### Single Point of Error Handling
Models add errors to context; handlers check once (no redundant validation):

```javascript
// ✅ GOOD - Model adds error to context
async function modelRetrieve(userId, {context}) {
  if (!userId) {
    context.addError('MISSING_USER_ID');
    return null;
  }
  
  const user = await postgres.getOneById(USER_TABLE, userId, {context});
  if (!user) {
    context.addError('USER_NOT_FOUND', {userId});
    return null;
  }
  
  return user;
}

// ✅ GOOD - Handler checks context.error once
async function handler(request, response) {
  const result = await model.retrieve(userId, {context});
  
  if (context.error) {
    return response.status(context.error.status).json({error: context.error});
  }
  
  return response.status(200).json({result});
}

// ❌ BAD - Redundant null check (model already added error)
async function handler(request, response) {
  const result = await model.retrieve(userId, {context});
  
  if (context.error) {
    return response.status(context.error.status).json({error: context.error});
  }
  
  if (!result) {  // ❌ Redundant - model already added error if result is null
    return response.status(500).json({error: context.error});
  }
  
  return response.status(200).json({result});
}
```

## Success is a value, failure is an error

- **Never return `true`/`false` to say whether a method succeeded** (Jerome, 2026-10-26: "we don't return true/false for method success; if we want to return a failure → exception"). A model or service method returns its result, or nothing, on success. On failure it throws, or, on the request path above, adds the error to `context` for the handler. A boolean success flag is neither: callers forget to check it, and the reason for the failure is lost.
- A method whose answer really is a boolean (`isPremium`, `exists`) is fine. The rule is about signalling success or failure.

```javascript
// ❌ BAD - the caller has to remember to check, and learns nothing about why
async function deleteAuthUser(uid) {
  try { await auth.deleteUser(uid); return true; } catch (error) { return false; }
}

// ✅ GOOD - success returns nothing; a failure throws with its cause
async function deleteAuthUser(uid) {
  await auth.deleteUser(uid);
}
```

## Error Key Management
- **Error Keys**: All error keys used in the codebase must be defined in `src/errors.js`
- **Alphabetical Order**: Error keys must be sorted alphabetically within their category
- **Naming Convention**: Use semantic naming based on what the error represents, not the model name
  - Error keys describe **what they are**, not which model uses them
  - Example: Use `TOKEN_MISSING_PROJECT` for token errors even if model is called `auth`
- **Status Codes**: Use appropriate HTTP status codes (400, 401, 403, 404, 422, 500)
- **Message Clarity**: Error messages should be clear and user-friendly
- **Validation**: Always ensure all error keys referenced in code exist in `errors.js`
- **Loud misconfig, never silent fallback.** When a feature is opted in
  (`jsonOutput: true`, `tools: [...]`, etc.) but its required dependency
  (schema file, tool definition, env var) is missing or invalid,
  `logger.warn` **and** disable the feature for that call. Don't degrade
  silently to a weaker mode (e.g. generic JSON mode when a strict schema was
  expected) — that papers over the misconfiguration and makes it invisible
  in traces. Example: `Prompter.buildPrompt()` only sets
  `result.jsonOutput = true` when `readSchema()` succeeds; otherwise it
  warns and emits no `response_format` at all. ([#265](https://github.com/AuraHealth/IntelligenceService/pull/265))

## Provider and upstream errors

- **Match on the error code, not the message.** Decide how to handle a provider error from its stable code (Stripe: `error.code` / `error.raw.code`, e.g. `invalid_canceled_subscription_fields`). An exact message comparison may only be kept as a fallback for responses that carry no code.
- **Why:** providers reword their messages. PaymentService's `cancel` in `src/services/stripe.js` compared Stripe's message text to fixed strings, so when Stripe changed the wording, cancelling an already-canceled subscription returned an error instead of the subscription.

- **Match a vendor's transient errors on status, not on error code.** Vendors
  attach more than one code to the same condition, and a gate written against
  the one code the docs name will miss the ones production actually returns.
  `createWithServiceTierFallback` fell back from `service_tier: 'flex'` to
  `'auto'` only on code `resource_unavailable`; OpenAI also sheds flex traffic
  as `rate_limit_exceeded` ("We're currently processing too many requests").
  Over a week of prod 429s, **all** were the code the gate ignored and none
  were the documented one, so the fallback had never fired. The fix is
  `status === 429` — on the pre-emptible queue, any 429 means "not now",
  whatever code is attached. Keep the originating code in the warn metadata so
  which shape triggered it stays visible in traces.
  ([#454](https://github.com/AuraHealth/IntelligenceService/pull/454))

- **SDK-level retries are not a fallback.** The OpenAI client retries 429s
  (`maxRetries: 2`), but that is three attempts inside ~1.5s of jittered
  backoff. Capacity shedding lasts minutes to hours — the incident above ran
  three hours. Don't read "the SDK already retries" as covering a transient
  upstream failure; a tier/route change is what recovers, and the retry only
  smooths over a blip.
  ([#454](https://github.com/AuraHealth/IntelligenceService/pull/454))
