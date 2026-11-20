# Usage-limit semantics & magic strings

Everything the skill needs to mirror the backend faithfully and to query
Mixpanel correctly. Verified against `IntelligenceService` and
`AuraConsumerApp` as of 2026-08.

## Backend `getLimits` (IntelligenceService — `src/models/usage.js`)

The voice usage gate is served by `GET /ai/usage/limits` and computed by
`getLimits`. The skill's `compute_limits.py` reproduces these rules exactly:

- **Metric**: `SUM("duration")` (milliseconds) over rows where
  `mode = 'voice'` and `deletedAt IS NULL`.
- **Daily window**: `createdAt >= DATE_TRUNC('day', NOW() AT TIME ZONE <tz>) AT TIME ZONE <tz>`
  → midnight in the **user's timezone**.
- **Weekly window**: **Sunday 00:00** in the user's tz — this is the rule the
  script applies. Postgres `DATE_TRUNC('week')` is ISO/Monday-based, so
  `getVoiceDurations` shifts `+1 day` before truncating and `-1 day` after to
  land the boundary on Sunday:

  ```sql
  (DATE_TRUNC('week', (NOW() AT TIME ZONE <tz>) + INTERVAL '1 day') - INTERVAL '1 day') AT TIME ZONE <tz>
  ```

  A Sunday-start week aligns the paid-package window with the free-tier limiter,
  which also resets Sunday (product decision
  [PROD-3041](https://linear.app/aura-health/issue/PROD-3041), IntelligenceService
  [#315](https://github.com/AuraHealth/IntelligenceService/pull/315)). The same
  shift governs the user-message counts in `dialogue.countByPeriod`. Watch the
  off-by-one: on a **Monday** the window opens the *previous* day, so reading it
  as a plain `DATE_TRUNC('week')` boundary over-counts by a day.
- **Comparison**: strict `>` — `dailyDuration > DAILY_LIMIT_VOICE_MS` then
  `weeklyDuration > WEEKLY_LIMIT_VOICE_MS`. The gate blocks the *next* call once
  the total has crossed the line.
- **Limits** (`src/config.js`, ~lines 436–437): `DAILY_LIMIT_VOICE` default
  **5_400_000 ms (1.5h)**, `WEEKLY_LIMIT_VOICE` default **48_600_000 ms
  (13.5h)**. The skill reads these live via
  `compute_limits.py --config-js <path-to/src/config.js>` so the caps track the
  latest config instead of a hardcode (precedence: explicit `--daily-ms` /
  `--weekly-ms` flag > `--config-js` > built-in fallback). Env vars can override
  per environment and **no endpoint exposes the running numbers** — if a
  deployment overrides the defaults, pass the real value via the explicit flags.
- **Timezone source (backend)**: `user.timezone`, falling back to
  `America/New_York`. This skill instead reads the timezone from Mixpanel — the
  user-profile `$timezone` property (see Mixpanel section) since the analyst
  rarely has the user row handy; pass the same tz to the script.
- **Recording model**: a usage row is written by the ElevenLabs **post-call
  webhook at call END**, with the full call duration. So an in-progress call
  contributes 0 until it ends, and the gate (checked before a call starts) lags
  by up to one call. The per-call ~59.9-min cap (`maxConversationDurationSeconds
  = 3594`) is an ElevenLabs limit, not a usage limit.

## When the backend gate applies (AuraConsumerApp)

Scope note: this skill analyzes the **backend** voice limit only. The two
points below matter solely because they decide *whether the backend gate was
ever consulted* for a given user — they are not a separate client-side limit.

- The backend gate is consumed by
  `UsageLimitsService.checkUsageLimitReached()` → `GET /ai/usage/limits`, called
  at LiveSession / Quick Processing entry **and** before each connection
  (`startConnection`). It is **experiment-gated**: it returns "not limited"
  unless `usageLimitsNewUsers` or `usageLimitsOldUsers` resolves to variant
  `"a"`, and it **fails open** on any backend error.
- **The gate shipped in app `v1.20.0`** — commit `ac53e5a3` (2026-05-27),
  first release tag `v1.20.0-1`. It is **not** in `1.19.0` (tag dated
  2026-05-21). Any usage on `≤ 1.19.x` was unrestricted because the feature did
  not exist in that build. This is the single most common explanation for
  "huge usage over the cap" and why Step 5 finds the v1.20.0 cutover date.

## Aura API endpoints

Base host: `https://services.aurahealth.io`. Auth: `Authorization: Bearer
$AURA_API_TOKEN` (admin/service/system role).

- `POST /ai/usage/list/:userId` — **system-authorized**, row-level usage for the
  given user. Body: `{mode, since, until, sort, limit, page, includeDeleted,
  type}` (`since`/`until` ISO with `:` and `Z`). The non-`:userId` variants
  (`GET /ai/usage`, `POST /ai/usage/list`) are **self-scoped** to the token
  holder and cannot read another user. Response is a **top-level JSON array** of
  rows.
- `GET /experiments/assignments/:userId` — **read-only**. Returns the user's
  full experiment map as flat `{experimentName: value}` (no request body). A key
  is present **only for experiments the user is assigned to**; a missing key
  means **not** in that treatment — e.g. `{…,"usageLimitsOldUsers":"a"}` =
  old-users treatment, unassigned to new-users. Filter for
  `usageLimitsNewUsers` / `usageLimitsOldUsers` client-side. Default
  `?includeBackup=true` unions live + archived (ended/deleted) assignments —
  keep it so an ended usage-limit experiment still shows up. **Auth:** caller
  role must be `agent` or `system` (`403` for a wrong role, `401` for a missing or
  invalid token, `503` when UserService is down) —
  stricter than the other Experiments endpoints. Quirk: an internal caller
  reading its **own** UID always gets `{}` (internal users are never bucketed);
  point it at the target user. Shipped in ExperimentService v2.36.1
  (PROD-2481). Documented in `Tools/Aura API → Get User Experiment Assignments`.
- ⚠️ `POST /experiments/assign/:userId` — **do not use this to read.** It
  *mutates state*: calling it on an unbucketed user retroactively assigns them
  (it's the client-side bucketing path), so using it for analysis perturbs the
  very history this skill reconstructs. Use the read-only `GET …/assignments/`
  above instead.

> If an endpoint isn't documented in `Tools/Aura API`, resolve it via
> `eng:call-aura-api` and ingest it back so the next run is self-served.

## Mixpanel (project_id `963821` — Project Aura, prod)

Access via the Mixpanel MCP tools (`Run-Query`, `Get-Events`,
`List-Properties`). Key magic strings:

- **User identifier**: event property **`UserID`** = Firebase UID. (Do **not**
  filter on `$distinct_id` — it is not the UID and returns nothing.)
- **Usage-limit block event**: **`AI Usage Limit Modal Seen`** — fired
  (`aiUsageLimitModalSeen`) when the backend gate blocks entry, from both
  LiveSession and Quick Processing. Only ever fires on the gated build
  (v1.20.0+), so it doubles as a cutover signal. Sibling CTA event:
  `AI Usage Limit CTA Tapped`.
- **App version**: `AI Current Version` (Aura-specific) or `$app_version_string`
  (iOS default). `AI Joined Version` is the install-time version.
- **Login events**: `AI Login`, `AI Login (Mobile)`, `Login` — use to trace the
  version-over-time and find the v1.20.0 cutover.
- **Timezone**: the **user-profile** property **`$timezone`** (`resource: user`,
  Mixpanel geo-derived); Aura events do not carry a timezone property.
  Resolution order: user `$timezone` → event `timeZone` → event `Timezone` →
  `America/New_York` default (note it). Confirm names with
  `List-Properties resource_type=User query="zone"` if unsure.
- **Session event** (useful for tz lookup / activity): `AI Conversation Session
  Started`.

Mixpanel charts bucket time in the **project** timezone, which may differ from
the user's tz; the skill computes its authoritative per-day buckets from raw
`createdAt` in the user's tz, so small day-boundary differences vs a Mixpanel
chart near midnight are expected.

## Related issue

`PROD-2318` originally hypothesised an in-session reconnect bypass; investigation
(this skill's workflow, run manually) showed it was a non-bug — the heavy usage
predated the v1.20.0 gate reaching the device. Keep that framing when reporting.
