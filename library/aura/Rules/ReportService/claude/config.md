---
description: Config binding and documentation conventions for Aura services
globs: 
alwaysApply: true
---

# Config convention

> **Shared by every backend service.** This file is identical in all 13 services, in `.claude/rules/` and `.cursor/rules/`. Change every copy in the same batch, and compare git blob hashes to check. Rules that apply to one service only go in that service's `.claude/rules/service-specific.md` (and `.cursor/rules/service-specific.mdc`).

- Define config bindings at the top of each module via destructuring and use explicit constant aliases for readability.

```js
const {
  service: {version: SERVICE_VERSION},
  // other config keys as needed
} = config;
```

- When a new environment variable is added (or an existing one changes), update `README.md` in the same change. Config constants in `src/config.js` are not listed in the README; the config file documents them.

## Constants vs Environment Variables

### Use Config Constants For:
- Values that don't change between environments
- Business logic constants (token expiration, valid project lists)
- Timeouts and delays that are consistent
- Feature configuration that's the same everywhere

```javascript
// ✅ GOOD - Constants in config
timeouts: {
  requestMs: 5000, // 5 seconds
},
```

### Use Environment Variables For:
- Secrets (API keys, JWT secrets, passwords)
- Database connection strings
- Service URLs that change per environment
- Environment-specific feature flags

```javascript
// ✅ GOOD - Environment variables for secrets
service: {
  apiKey: SERVICE_API_KEY,  // Environment variable (secret)
  secret: SERVICE_SECRET,   // Environment variable (secret)
}

// ❌ BAD - Unnecessary environment variables for constants
timeouts: {
  requestMs: parseInt(SERVICE_REQUEST_TIMEOUT_MS, 10) || 5000,
}
```

## Locales

- Locale codes, URL forms and conversion rules for every layer are canonical in AI Knowledge → Procedures/Engineering/Locales: store and send `pt_BR`, and convert only at the edge (URL `pt-br`, `Intl` `pt-BR`, moment `pt-br`).
