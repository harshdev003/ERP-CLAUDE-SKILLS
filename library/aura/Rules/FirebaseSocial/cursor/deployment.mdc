---
description: Deployment conventions and rules for Aura Firebase Cloud Functions
globs:
alwaysApply: true
---

# Deployment

## Deploy Scripts

Each function project uses granular deploy scripts in `functions/package.json`:

```bash
# Deploy all functions
npm run deploy

# Deploy to dev environment
npm run deploy:dev

# Deploy to prod environment
npm run deploy:prod

# Deploy a specific domain
npm run deploy:<domain>
```

## Adding a New Trigger Function

When you add a new trigger (e.g. `onWidgetCreated`), **you must update `functions/package.json`**:

1. **Find the matching `deploy:<domain>` script** for the trigger's domain (e.g. `deploy:content`).
2. **Append the exported function name** to the `--only functions:` list.

```json
// Before
"deploy:content": "npx firebase deploy --only functions:onContentCreated --non-interactive",

// After (new function added)
"deploy:content": "npx firebase deploy --only functions:onContentCreated,functions:onWidgetCreated --non-interactive",
```

3. **If it is a new domain**, create a new `deploy:<domain>` script and add it to the main `deploy` script:

```json
"deploy": "... && npm run deploy:widget",
"deploy:widget": "npx firebase deploy --only functions:onWidgetCreated --non-interactive",
```

## Deployment Flags

- Always use `--non-interactive` to prevent prompts in CI/CD.
- Use `npx firebase use <env>` before deploying to switch Firebase projects.
- Never deploy directly to `prod` without first deploying and verifying on `dev`.

## Pre-Deployment Checklist

- [ ] `nvm use` — correct Node.js version active
- [ ] `npm run lint` — no ESLint errors
- [ ] `functions/package.json` updated with all new function names
- [ ] New trigger exported from `functions/src/triggers/index.js` (and domain index if applicable)
- [ ] Deployed to `dev` and verified behavior
- [ ] Ready to deploy to `prod`
