# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

A collection of independently deployable AI tools for the Braintrust orchestration platform. Each tool is a typed async function that integrates with Aura's content service and user memory system, and is registered on Braintrust with a JSON schema and slug.

## Commands

```bash
npm run lint          # ESLint check
npm run lint:fix      # ESLint with auto-fix

# Before deploying, always switch to the correct Node version and reinstall deps:
nvm use
npm i

# Deploy a specific tool
npx braintrust push tools/<toolName>/deploy.ts

# Or use the npm shortcuts (after running nvm use && npm i):
npm run deploy:recommendTracks
npm run deploy:searchTracks
npm run deploy:createInsightCard
npm run deploy:handoffToGuardrails
npm run deploy:findSimilarInMemory
npm run deploy:storeInMemory
npm run deploy:recommendTracksMock
```

Prefer the `npm run deploy:*` shortcut over a raw `npx braintrust push` — some tools deploy to multiple projects and the shortcut encodes that (see [Multi-project deployment](#multi-project-deployment)).

There is no test runner — tools are validated through Braintrust's deployment and observability platform. Deploys write to the live Braintrust project, so confirm before deploying and expect the change to be visible immediately.

## Environment

Copy `.env.sample` to `.env`. Required at minimum:
- `BRAINTRUST_API_KEY` — for deployment and logging
- `CONTENT_SERVICE_BASE_URL` + `SERVICE_TOKEN` — injected at runtime by Braintrust

## Tool Structure

Every tool follows the same layout:

```
tools/<toolName>/
├── config.ts      # projectNames[] (target Braintrust projects) + deploy-time defaults
├── handler.ts     # Zod input/output schemas + core async logic
└── deploy.ts      # Registers the tool on Braintrust via SDK (name, slug, ifExists)
```

The deploy script calls `braintrust.projects.create()` → `project.tools.create()` with `ifExists: 'replace'`, so re-deploying is idempotent. Tools are matched by **slug** within a project, so `ifExists: 'replace'` overwrites the existing tool with that slug — including renaming its display `name`.

Tools that are schema-only (no handler yet) have a `schema.json` instead: `endOnboarding`, `showCrisisModal`, `search`. Once a handler is created for a tool, delete its `schema.json`.

`projects/quick-processing/tools.json` is a checked-in snapshot of the Quick Processing project's tool schemas. It is not read by the deploy pipeline, but keep it in sync when you change a tool's parameters (e.g. adding a tag enum value) so it doesn't drift.

## Architecture Patterns

**Zod schemas are the source of truth.** Input and output types are inferred from Zod schemas defined in `handler.ts`, not written separately. Always define schemas first, then derive TypeScript types from them.

**Logging via Braintrust SDK.** Call `braintrust.initLogger({ projectName })` at the top of each handler to get a logger; pass it through to sub-functions rather than using `console.log` directly.

**Environment variables at runtime.** `BRAINTRUST_USER_ID`, `SERVICE_TOKEN`, and `CONTENT_SERVICE_BASE_URL` are injected by the Braintrust runtime — do not hard-code or default them in handlers.

**Content Service fallback pattern.** `recommendTracks` demonstrates the expected resilience pattern: attempt a primary request, then progressively relax constraints across up to 6 fallback strategies before returning an empty result.

## Multi-project deployment

A tool's `config.ts` exports `projectNames: string[]` — the Braintrust projects it deploys to (e.g. `Quick Processing`, `Conversation`, `Deep Processing`, `Memory`). `deploy.ts` loops over them and creates the tool in each. Two slug strategies coexist, and confusing them breaks deploys:

- **Distinct slug per project** (the memory tools): `slug: \`store-in-memory-${projectName.toLowerCase().replace(/\s+/g, '-')}\``. Every slug in the push is unique, so a single `braintrust push` deploys all projects at once.
- **Shared slug across projects** (`recommendTracks`, `recommendTracksMock`): one slug used everywhere (`recommend-tracks`, `recommend-tracks-mock`) because Conversation's Coach prompts reference the tool by a stable slug. `braintrust push` **rejects duplicate slugs within a single push**, so these `deploy.ts` files filter `projectNames` by a `BT_PROJECT` env var and the npm script runs one push per project:

  ```
  BT_PROJECT='Quick Processing' npx braintrust push … && BT_PROJECT='Conversation' npx braintrust push …
  ```

  Running such a `deploy.ts` with no `BT_PROJECT` set attempts all projects in one push and fails on the duplicate slug — always go through `npm run deploy:*`.

Don't convert a shared-slug tool to per-project slugs without also updating every prompt that references its slug, or those prompts lose the tool. When adding a project to a shared-slug tool, add it to `projectNames` **and** extend that tool's `deploy:*` npm script with another `BT_PROJECT=…` push.

## Memory System

The memory tools (`storeInMemory`, `findSimilarInMemory`) use a structured schema:
- **category**: 27 enum values (ACHIEVEMENT, BELIEF, HABIT, PREFERENCE, etc.)
- **depth**: `surface` | `intermediate` | `deep` (based on Social Penetration Theory)
- **tags**: typed JSONB object covering emotional states, relationships, activities, time context, life areas, and personal qualities
- **expiryAt**: optional ISO datetime for time-bounded memories

When adding new memory-related tools, follow these same field conventions for consistency with the memory API.
