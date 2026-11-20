---
description: Naming conventions and handler naming patterns for Aura services
globs: 
alwaysApply: true
---

# Naming Conventions

> **Shared by every backend service.** This file is identical in all 13 services, in `.claude/rules/` and `.cursor/rules/`. Change every copy in the same batch, and compare git blob hashes to check. Rules that apply to one service only go in that service's `.claude/rules/service-specific.md` (and `.cursor/rules/service-specific.mdc`).

## General Naming
- **Files**: `camelCase.js` (e.g., `userService.js`)
- **Functions**: `camelCase` (e.g., `getUserById`)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `MAX_RETRY_ATTEMPTS`)
- **Classes**: `PascalCase` (e.g., `UserService`)
- **Variables**: `camelCase` (e.g., `userData`)

## Style
- Use descriptive, self-documenting names
- Prefer clarity over brevity
- Use camelCase for variables and functions
- Use PascalCase for classes and constructors

## Handler Naming Patterns
- **Handler Functions**: Use simple action names (e.g., `create`, `list`, `update`, `remove`, `retrieve`)
- **Handler Files**: Match the action name (e.g., `create.js`, `list.js`, `update.js`, `remove.js`, `retrieve.js`)
- **Handler Modules**: Use simple action names in JSDoc `@module` tags
- **Reserved Words**: For JavaScript reserved words like `delete`, use descriptive names:
  - Function name: `deleteJournal` (avoids reserved `delete` keyword)
  - Module name: `deleteJournal`
  - Export: `deleteJournal`
  - Handler index: Import as `deleteJournal` and export as `delete: deleteJournal`

## Consistency
- For CRUD-style handlers, prefer simple action names: `create`, `list`, `retrieve`, `update`, `remove`.
- For reserved words (for example `delete`), use descriptive names such as `deleteJournal` and map to `delete` at API boundaries if needed.

## Identifiers
- **Use `uuidV4()` for any synthetic identifier** the code needs to mint (correlation handles, reference handles passed to an LLM, etc.). Don't hand-roll string IDs like `cand_${index}` — UUIDs are uncoordinated, collision-proof across processes, and align with the database's id column shape. Import as `const {v4: uuidV4} = require('uuid');`. ([apps#240 comment](https://github.com/AuraHealth/IntelligenceService/pull/240#discussion_r3324897260))

## Helper function names
- **Name helpers for what they produce, not where they're called from.** A function that returns deduplicated similar memories is `gatherSimilars`, not `gatherSimilarPool` (the calling site is the only place the word "pool" matters). A function that validates a prompt's `{delete, store}` plan is `validatePromptPlan`, not `validatePlan` (the prompt context is what makes the name precise). Names that bind to the caller's frame rot the first time the helper is reused.
