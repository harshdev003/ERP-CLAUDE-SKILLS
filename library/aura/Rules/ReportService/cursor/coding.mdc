---
description: Code style guidelines, formatting, and legacy coding guidelines
globs: 
alwaysApply: true
---

# Code Style Guidelines

> **Shared by every backend service.** This file is identical in all 13 services, in `.claude/rules/` and `.cursor/rules/`. Change every copy in the same batch, and compare git blob hashes to check. Rules that apply to one service only go in that service's `.claude/rules/service-specific.md` (and `.cursor/rules/service-specific.mdc`).

## Formatting
- Use single quotes for strings
- Use trailing commas in multi-line objects/arrays
- Use consistent indentation (2 spaces)
- Group related imports together
- Prefer async/await.

## JavaScript (all)
- `DEV-JS-501`: Use guard clauses to reduce nesting and make exit conditions explicit.
- `DEV-JS-601`: For membership/lookup-heavy logic, prefer `Set`, `Map`, or objects over repeated array `includes` checks.
- `DEV-JS-602`: Keep destructured parameter-object keys in alphabetical order, the same as object literals (`fn({apple, banana, cherry})`). ESLint `sort-keys` only flags object *literals*, not destructuring patterns, so this is enforced by convention — lint stays green either way. ([apps#241 comment](https://github.com/AuraHealth/IntelligenceService/pull/241))
- `DEV-JS-603`: When building a nested config object with multiple optional siblings (e.g. `request.text.verbosity` + `request.text.format`), build the object up in a single guarded block. Don't write multiple sequential `request.text = { ... }` assignments that rely on `{ ...request.text, x }` spread-merging — ordering is fragile and a third branch added later silently breaks if someone forgets the spread. ([#265](https://github.com/AuraHealth/IntelligenceService/pull/265))

  ```js
  // ✅ build up once
  if (verbosity || jsonOutput) {
    const text = {};
    if (verbosity) text.verbosity = verbosity;
    if (jsonOutput) text.format = format;
    request.text = text;
  }

  // ❌ spread-merge across two assignments
  if (verbosity) request.text = { verbosity };
  if (jsonOutput) request.text = { ...(request.text || {}), format };
  ```

- `DEV-JS-604`: When a flag's `false` value must override a default (not just `true`), gate the override with `typeof x === 'boolean'`. `if (x)` drops the `false` case (the very thing you want to capture), and `!== undefined` reads less clearly. `typeof === 'boolean'` says "true or false explicitly set, undefined falls through." Worked example: the `useResponseConversation` precedence in `Prompter.buildPrompt()`. ([#218](https://github.com/AuraHealth/IntelligenceService/pull/218))

  ```js
  // ✅ explicit boolean check — false overrides, undefined falls through
  if (typeof this.useResponseConversation === 'boolean') {
    result.useResponseConversation = this.useResponseConversation;
  } else if (typeof promptConfig.useResponseConversation === 'boolean') {
    result.useResponseConversation = promptConfig.useResponseConversation;
  }

  // ❌ truthy check — silently drops explicit `false`
  if (this.useResponseConversation) {
    result.useResponseConversation = this.useResponseConversation;
  }
  ```

## HTTP handler / router
- `DEV-BE-101`: Use CRUD method names (`create`, `retrieve`, `update`, `delete`) or clear REST verbs (`assign`, `recommend`, `search`, `list`) for handlers.
  A new route takes the same form as its siblings, path and handler alike: next to `POST /rate`, an action is `POST /view` with handler `view`, not `/viewed`. ([IntelligenceService#700 review](https://github.com/AuraHealth/IntelligenceService/pull/700))
- `DEV-BE-102`: Use `debug` log level by default for operational traces.
- `DEV-BE-103`: Use `info` for completion logs, typically once per endpoint (for example: `logger.info('Done getting recommendations', meta);`).
- `DEV-BE-104`: Return dates in JSON using ISO 8601. Models return `Date` objects (or `null`) and `res.json` serializes them to ISO 8601, so don't convert dates in the model or the handler, and don't add a helper for it. ([IntelligenceService#700 review](https://github.com/AuraHealth/IntelligenceService/pull/700))
- `DEV-BE-105`: Keep parameter parsing in dedicated files/modules when logic is non-trivial (for example `parseParameters.js`).
- `DEV-BE-150`: Use HTTP methods according to RESTful semantics.
- `DEV-BE-151`: Use HTTP status codes according to RESTful semantics.
- `DEV-BE-152`: Return structured error JSON on failure, for example:

```json
{
  "error": {
    "key": "COACH_NOT_FOUND",
    "message": "The coach was not found",
    "status": 404,
    "transaction": "42df7cfd-d2af-4174-be35-87871663f70b",
    "transactionSlice": "42df7cfd",
    "coachId": "invalid"
  }
}
```

## Model
- `DEV-BE-201`: Use named parameters for optional arguments.
- `DEV-BE-202`: Use `info` log level for important model-level events.
- `DEV-BE-203`: Use `try/catch` in model methods that perform I/O.
- `DEV-BE-204`: Prefer `result`/`results` naming for returned values.
- `DEV-BE-205`: Keep model method names aligned with CRUD patterns (`retrieve`, `list`, `create`, etc.).
- `DEV-BE-206`: Validate input.
- `DEV-BE-207`: Create specific errors instead of generic failures.
- `DEV-BE-208`: The last positional parameter is always `{context}` — never bundle `user` or other data into it. If a function needs more than `context`, take them as named positional args earlier in the signature. ([apps#240 comment](https://github.com/AuraHealth/IntelligenceService/pull/240#discussion_r3324623363))
- `DEV-BE-209`: Default the return holder to `undefined` (`let result;` / `let results;`) and only assign on the success path. Don't initialize to `[]` or `{}` — empty defaults hide whether the function actually did work. ([apps#240 comment](https://github.com/AuraHealth/IntelligenceService/pull/240#discussion_r3324889751))
- `DEV-BE-210`: Functions whose return value no caller reads should be `Promise<void>` (or `void`). Drop the trailing `return`, the holder variable, and any accumulators kept only for the return.
- `DEV-BE-211`: Once a precondition is enforced with an early-return + `addError`, drop any later guard ternaries that re-check the same condition. Move the check higher rather than guarding every subsequent use.
- `DEV-BE-212`: Once a function takes **3 or more logical arguments**, bundle them into a single leading object literal (`fn({a, b, c}, {context})`) instead of positional args, keeping `{context}` as the last positional parameter. At 2 args, positional is fine (`fn(userId, candidates, {context})`). Refines DEV-BE-201/DEV-BE-208. ([apps#241 comment](https://github.com/AuraHealth/IntelligenceService/pull/241#discussion_r3326372029))

## Service
- `DEV-BE-301`: Use `initialize` and `finalize` for service lifecycle hooks used by app/jobs.
- `DEV-BE-302`: Initialize dependencies early and fail startup if critical services are unavailable.
- `DEV-BE-303`: Use `debug` log level by default unless higher visibility is required.

## Data
- `DEV-BE-401`: When updating Firebase, guard against `undefined` values.
- `DEV-BE-451`: Use named query parameters where available for performance/caching and query clarity.
