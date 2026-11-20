---
description: Documentation standards for Aura services
globs: 
alwaysApply: true
---

# Documentation

> **Shared by every backend service.** This file is identical in all 13 services, in `.claude/rules/` and `.cursor/rules/`. Change every copy in the same batch, and compare git blob hashes to check. Rules that apply to one service only go in that service's `.claude/rules/service-specific.md` (and `.cursor/rules/service-specific.mdc`).

## Code Documentation
- Use JSDoc for all public functions
- Include parameter types and return types
- Document complex business logic
- Keep documentation up-to-date with code changes

## Comments
- Default to **no comments**. Only add one when the WHY is non-obvious — a hidden constraint, a workaround for a specific upstream quirk (e.g. ElevenLabs invalid tool_call_ids), an invariant a reader can't infer from the code.
- Don't restate what the code does. Variable names and the if/else structure already say it. Multi-line blocks that narrate "this controls whether…" or "kept as-is so undefined falls through…" should be deleted. ([#218](https://github.com/AuraHealth/IntelligenceService/pull/218))
- **No Linear ticket references in source comments** (`// See ENGBACK-858.`, `// Per PROD-1234.`, etc.). Ticket linkage belongs in the PR description, the commit message, and `git blame` — comments age, tickets don't. The PR/blame trail is how a reader gets back to the ticket; the source should explain the code. ([#218](https://github.com/AuraHealth/IntelligenceService/pull/218))

## API Documentation Standards

### Where Postman docs go, and in what shape

The collection is **Aura Services** in the Team Workspace. Its convention is one
top-level folder per domain (`Conversations`, `Dialogue`, `Insights [AI]`, …), then
**one subfolder per action** (`Create`, `End`, `Rate`, `Summarize`), each holding a
single request named for the path (`/conversations/:id/end`). Requests use
`{{default.host}}` and bearer `{{user.token}}`; there is no collection-level auth.

A new endpoint is not documented until it is in there. Include, in the request
description: the required role if the route is role-guarded, what the endpoint does
*not* do when that is the reason it exists (e.g. "does not modify `endedAt`"), and
the error keys it can return with their statuses.

The same holds for a new parameter on an existing endpoint: update every Postman
request for that route (including a self-serve twin of an admin route) in the same
change, with what the parameter does, its accepted values (body and query forms) and
its default. Why: a flag added to the conversation export shipped with tests and a
release but no Postman entry, and the docs had to be chased afterwards.


### Do NOT Document in README
- **API endpoints should NOT be documented in README.md**
- API documentation is maintained in **Postman**
- README should focus on infrastructure and system information

### README Should Include:
- Service purpose and overview
- Environment variables (a config constant in `src/config.js` is documented there, not in the README)
- Jobs and background scripts
- System health endpoints (`/ready`, `/status`)
- Local development setup
- Database setup instructions

### README Should NOT Include:
- Detailed API endpoint documentation
- Request/response examples for business endpoints
- API parameter descriptions
- Authentication details for business endpoints
- Endpoint-specific usage instructions

### Example:
```markdown
## Endpoints

### System
Endpoint                   | Description
-------------------------- | --------------------------------------------------------------------
`GET /`                    | Returns `200` if the service is running
`GET /ready`               | Returns `200` if the service is ready for requests, `503` if not
`GET /status`              | Returns the status of the server

<!-- ❌ DO NOT add detailed API documentation here - use Postman instead -->
```
