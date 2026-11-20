# Categorization heuristics

Keyword maps for classifying issues by **product**, **layer**, and **fix type**. Apply to the issue's title + description (case-insensitive). When multiple categories match, pick the one with the strongest signal; if genuinely cross-cutting, note both (e.g. "frontend + backend").

## Product

| Product | Strong signals (keywords/phrases) |
|---------|-----------------------------------|
| **Aura mobile** | `react native`, `RN`, `TestFlight`, `iOS`, `Android`, `mobile app`, `app version`, `3.19.`, `Aura app`, `Sentry mobile`, `deeplink`, `push notification`, screen names like `onboarding`, `paywall screen` |
| **Auri** | `Auri`, mobile-specific Auri features (treat as mobile app unless clearly separate) |
| **userWeb** | `userWeb`, `user-web`, `subscribe route`, `Stripe Checkout`, `web paywall`, `webview paywall`, `ab:webViewPaywall`, `user.aurahealth`, marketing/landing pages |
| **coachWeb** | `coachWeb`, `coach-web`, `coach dashboard`, `coach portal`, `live session`, coach-side donations/payouts |
| **backend / Support Service** | `Aura Support Service`, `support service`, `backend`, `API`, `endpoint`, `internal service`, `service API`, `webhook handler`, anything referencing server-side routes |
| **Aurabot** | `Aurabot`, `Slack bot`, `Block Kit`, `/support-` commands, support automation flows |
| **internal tooling** | `Front AI`, `internal dashboard`, `admin tool`, `Linear MCP`, `daily report` skill work |
| **infra / shared** | `Sentry config`, `CI/CD`, `Mixpanel instrumentation` (when not tied to a specific app), header rollout `X-App-Version` etc. |

If multiple products are touched (e.g. an X-Platform header rollout across mobile + web), label as `cross-product` and list which.

## Layer

| Layer | Signals |
|-------|---------|
| **frontend** | UI bugs, screen/component names, styling, navigation, React/RN code, paywall UI, form rendering, i18n display |
| **backend** | API routes, endpoint behavior, request/response shape, auth, business logic, third-party API calls (Stripe, Helium) on server |
| **database** | `query`, `migration`, `schema`, `null values`, `users table`, `subscriptions table`, data integrity issues, `convert: false/true` style DB-flag bugs |
| **infra / devops** | builds, deploys, env vars, secrets, headers, source maps, Sentry config, CI |
| **analytics / instrumentation** | `Mixpanel`, `event`, `tracking`, identity merge, funnel metrics |
| **cross-cutting** | spans 2+ layers — note which |

## Fix type

| Fix type | Signals |
|----------|---------|
| **code fix** | Clear bug with a known location; "fix X in Y file"; small logic change |
| **DB debugging** | "investigate why users have…", null fields, data inconsistency, needs SQL queries to diagnose |
| **config / env change** | Env vars, feature flags, experiment config, third-party dashboard settings (Stripe, Helium, Mixpanel) |
| **third-party integration** | Stripe Checkout, Helium SDK, Zoom Marketplace, OAuth flows — work involves vendor APIs/dashboards |
| **investigation / repro needed** | Bug reported but cause unknown; needs reproduction on specific build/version first |
| **design / spec needed** | Blocked on PM/design input; scope unclear |
| **experiment / A/B** | `ab:`, `variant`, `bucketing`, A/B test setup or analysis |

## Suggested-action heuristics

The next-action sentence should reference **specific artifacts from the issue**:
- If the description mentions an error message → "Check logs for `<exact error>`"
- If it references a Mixpanel event → "Query Mixpanel for `<event name>` over last 7 days"
- If it references a build version → "Repro on build `<version>`"
- If it references a DB field → "Query `<table>` for rows where `<field>` is null/unexpected"
- If it references another ticket → "Check status of `<ticket-id>` first; it's a blocker/sub-task"
- If it's a config change → "Update `<setting>` in `<dashboard/file>`"
- If it's a webview/web experiment → "Verify bucketing via Mixpanel and check variant labels aren't inverted"

Avoid generic actions like "investigate the bug" or "fix it". If you genuinely can't suggest something specific, write: "Re-read description; current title is too vague — possibly needs PM clarification."
