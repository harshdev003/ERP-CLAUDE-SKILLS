---
name: analyze-user-behavior
description: >
  Builds a behavioural and identity profile for a single Aura user from their Mixpanel engage
  profile, event stream and (best-effort) Aura API record, synthesised into a structured
  Markdown readout. Sibling to `analyze-user-conversations` — same Firebase UID input,
  different data source. Default window 90 days, narrowable to 30d or the most recent N
  events. An optional steering string focuses the synthesis on evidence for a stated reason.
  Returns the report inline; no persistence. Trigger on "analyze user behavior for {UID}",
  "analyze how {UID} uses the app", "build a profile for {UID}", "profile this user", "tell me
  about user {UID}", "what does mixpanel know about {UID}", "create a user profile", or a
  pasted Firebase UID with a request for a behavioural / usage / Mixpanel readout. Do NOT use
  for cohort analysis (Mixpanel UI), conversation analysis (`analyze-user-conversations`), or
  experiment readouts (`analyze-experiment`).

metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Compliance/Privacy                # PII redaction rules — UID prefix + email domain only; never full email/name in Slack/Notion
    - Guide/Index                       # baseline routing aid (universal)
    - Products/Aurie               # brand voice for the report framing
    - Team/Internal Accounts            # employee / internal-account allowlist — drives the Identity "Internal / employee account" flag
    - Tools/Mixpanel                    # access pattern + project ID
    - Tools/Mixpanel/Events             # event taxonomy
    - Tools/Mixpanel/User Properties    # people property definitions
    - Tools/Mixpanel/Super Properties   # super property definitions
    - Tools/Aura API                    # auth + base URL
    - Tools/Aura API/Subscriptions      # canonical subscription state across Aurie + Aura
---

# analyze-user-behavior

End-to-end behavioural + identity profile for one Aura user. Connects Mixpanel (people profile + event stream) and the Aura API (server-side user record), then synthesises a profile report. Sibling to `analyze-user-conversations` — both take a Firebase UID and produce a user-level readout, just from different data sources.

The skill **returns the report as its result**. The caller decides what to do with it (paste in Slack, drop in Notion, feed to another skill). No persistence.

## When to use

Trigger when the user asks any of:

- "analyze user behavior for `<UID>`"
- "analyze how `<UID>` uses the app"
- "build a profile for `<UID>`"
- "profile this user"
- "tell me about user `<UID>`"
- "what does mixpanel know about `<UID>`"
- "create a user profile"
- pastes a Firebase UID and asks for a behavioural / usage / Mixpanel readout

Common framing question this skill answers: *"Who is this user, what plan are they on, how engaged have they been, and where are they in the journey?"*

**Do NOT trigger for:**

- Aurie conversation friction analysis → use `analyze-user-conversations`.
- Experiment-level analysis (per-variant) → use `analyze-experiment`.
- Cohort or aggregate behavioural analysis → use the Mixpanel UI directly.
- Generic "what events did we fire today" — that's a tracking-plan question, not a profile question.

## Inputs

- **`user_id`** (required) — Firebase UID. 28 alphanumeric chars typical (e.g. `0VwbDpKVEva6FvKOSjYWDzb07yi2`). The skill **does not assume** this equals Mixpanel `distinct_id` — see Step 2 for the resolution recipe (Aurie/AurieWeb users in particular often have an anonymous `$device:...` distinct_id with the Firebase UID present only as the `UserID` event property).
- **`cancellation_feedback`** (optional) — free text. When present, the synthesis prompt is steered toward behavioural evidence relevant to the stated reason — specifically: ranking the **Top features** section to surface features tied to the complaint, weighting the **Notable signals** section toward signals that corroborate or contradict the reason, and tightening the **Subscription history** section's narrative around the events leading up to the cancellation. When absent, the skill produces the unsteered profile.
- **`time_window`** (optional, default `90d`) — one of:
  - `90d` *(default)* — wider lens than friction analysis, captures full subscription cycle.
  - `30d`
  - `last_n_events=<int>` — most recent N events, regardless of date.
- **`include_cost`** (optional, default `false`) — when `true`, the skill **also** invokes `eng:report-user-costs` for the same UID + window and renders a one-line cost summary as a sub-bullet of the **Engagement** section. Adds **~9 min** wall-clock, effectively all of it the OpenAI leg walking the `[Production]` projects serially; the ElevenLabs, LiveKit and listens legs are seconds (see `eng:report-user-costs` → Runtime). Off by default to keep the lightweight behavioural-only mode fast. Parent orchestrators (e.g. `data:profile-user`) typically pass `include_cost=true`, or supply a pre-computed cost report directly so this skill skips the additional invocation.

If `user_id` is missing, ask. Don't guess.

## Workflow

### Step 0 — Check prerequisites

The skill needs **one** Mixpanel access path (MCP **or** API creds — try MCP first, fall through to the API per the [MCP-first convention](../../../../CLAUDE.md#tokens-are-soft-requirements)) plus `AURA_API_TOKEN` for the Aura cross-reference (the latter for *both* `/users/<id>` and `POST /subscriptions/list/:userId`).

```bash
# Mixpanel — at least one path must be present.
if [ -n "$MIXPANEL_SERVICE_ACCOUNT_USERNAME" ] && [ -n "$MIXPANEL_SERVICE_ACCOUNT_SECRET" ] && [ -n "$MIXPANEL_PROJECT_ID" ]; then
  echo "OK: Mixpanel API creds set (will prefer API over MCP — see Step 2)"
else
  echo "Mixpanel API creds absent; will rely on Mixpanel MCP"
fi

test -n "$AURA_API_TOKEN" && echo "OK: AURA_API_TOKEN set" || echo "MISSING AURA_API_TOKEN (will skip /users/<id> AND /subscriptions/list/<id>)"
```

**Mixpanel access — pick a path:**

- **API path (preferred when creds are set).** If all three of `MIXPANEL_SERVICE_ACCOUNT_USERNAME`, `MIXPANEL_SERVICE_ACCOUNT_SECRET`, and `MIXPANEL_PROJECT_ID` are present, use the API fallback documented in Step 2 — `scripts/mixpanel_api.py` wraps Engage + Export with HTTP Basic auth, pagination, and the property-fallback resolution. This is the path that survives Cowork sandboxes (MCP isn't reachable from `mcp__workspace__bash` per PROD-1305) and a no-MCP-loaded host session.
- **MCP path (fallback).** If the API creds are absent but the Mixpanel MCP is loaded, use the MCP recipes in Step 2 as before.
- **Neither.** Hand off to `update-config` with a pointer to either (a) the Mixpanel API setup in [`/CLAUDE.md`](../../../../CLAUDE.md) → "Token inventory" — service account at `https://mixpanel.com/project/963821/settings/service-accounts`, **Analyst** role read-only — or (b) the Mixpanel MCP setup. The skill cannot proceed without one of them — Mixpanel is the primary behavioural data source.

If `AURA_API_TOKEN` is missing or 401s on `/users/<id>`, the skill still runs but loses identity/onboarding context. If it 403s on `/subscriptions/list/:userId` specifically, that's the role-allowlist bug — the system role was added in PaymentService [`e791483`](https://github.com/AuraHealth/PaymentService/commit/e791483) (released 2.185.2 on 2026-05-07); a 403 today means the env's token doesn't carry that role. Record the gap in **Sources** and continue with whatever data did come back.

**Cowork caveat.** `mcp__workspace__bash` strips host env (PROD-1305), so `AURA_API_TOKEN` and the three `MIXPANEL_*` vars are unreachable inside Cowork sandboxes. Use the per-session paste-export workaround documented in [`/CLAUDE.md`](../../../../CLAUDE.md) → "Known Cowork constraints", or run from the host harness. The Mixpanel MCP isn't reachable from workspace bash either, so the API path is the only one that survives Cowork once those vars are exported.

### Step 1 — Pull AI Knowledge priors

Invoke `shared:retrieve-knowledge` to pre-load the paths declared in `knowledge.reads`. The relevant ones for this skill:

- **`Tools/Mixpanel`** — project ID, MCP access pattern.
- **`Tools/Mixpanel/Events`** — event taxonomy. Critical for translating raw event names (e.g. `AI Conversation Session Started`) into the team's vocabulary in the report.
- **`Tools/Mixpanel/User Properties`** — people property definitions. Used to interpret what each property on the engage profile actually means.
- **`Tools/Mixpanel/Super Properties`** — super property definitions. Used the same way for properties attached to events.
- **`Products/Aurie`** — brand-voice priors for how to phrase the profile (especially the **Notable signals** section — frame observations the way Aurie's team thinks about users, not generically).
- **`Tools/Aura API`** — base URL + auth conventions for the `/users/<id>` cross-reference in Step 4.
- **`Tools/Aura API/Subscriptions`** — request/response shape for `POST /subscriptions/list/:userId`, the canonical source of truth for subscription state across **all six** Aura Health subscription types: Aurie's `ai`, plus Aura's `content` / `coaching` / `business` / `community` / `coach`. See [List Subscriptions By User Id](https://www.notion.so/35975e23af20818088b4e59dd028bbdd) for the full doc; the parent [Subscriptions](https://www.notion.so/35975e23af20817ab2ded91f7ca8cbbc) page has the type taxonomy table.

Keep the fetched content in working memory through Steps 2–7. Cite paths and URLs in the **Sources** block of the final report.

If a path fails to resolve (taxonomy drift), note the miss and proceed. Don't block.

### Step 2 — Resolve Mixpanel `distinct_id` from the Firebase UID, then pull the engage profile

The Firebase UID is **not** always Mixpanel's `distinct_id`. Aurie and AurieWeb users in particular are often never `mixpanel.identify()`'d — Mixpanel sees them under an auto-generated anonymous device id (`$device:19df4cb3e1b3dd-…`) and the Firebase UID lives only as event properties (`UserID`, `$user_id`, `userId`). Filtering the people profile or events by `distinct_id == <Firebase UID>` returns 0 rows for those users; that's a recipe bug, not a missing user. Resolve first, then query.

Two recipes — **API** (preferred when `MIXPANEL_SERVICE_ACCOUNT_*` is set; the path that survives Cowork sandboxes) and **MCP** (fallback). Same property-fallback chain on both — only the transport differs. Pick the path identified in Step 0.

#### 2.1 — Resolve

Property fallback chain (in order — first non-empty wins, on both transports):

1. `UserID` *(highest event count in Aura's tracking plan; the canonical client-set property)*
2. `$user_id` *(Mixpanel's standard identified-user property)*
3. `userId` *(legacy / sparse)*

**Recipe A — API (preferred).** Import the bundled helper and call `find_events_by_uid` — it walks the chain via Export `where` clauses and returns the events that matched plus which property hit. Engage is queried directly by the resolved distinct_id (the "identified user" case is just `distinct_id == Firebase UID`, which the helper tries first via Engage):

```python
import os, sys, datetime as dt
sys.path.insert(0, f"{os.environ['CLAUDE_PLUGIN_ROOT']}/skills/analyze-user-behavior/scripts")
from mixpanel_api import mixpanel, MCPFallback

# Optimistic path: did the client `identify()` the user? Engage keyed by UID
# returns 0 or 1 row — non-empty means distinct_id == Firebase UID.
profile = mixpanel.get_engage_profile(uid)

# Always run the property-fallback resolve so the event stream in Step 3 is
# correct for AurieWeb / anonymous-distinct_id users.
to_date = mixpanel.project_today()                       # NOT dt.date.today()
from_date = to_date - dt.timedelta(days=window_days)
events, hit_prop = mixpanel.find_events_by_uid(uid, from_date=from_date, to_date=to_date)

if profile is None and events:
    # Anonymous distinct_id case — re-query Engage with the resolved id.
    resolved = mixpanel.top_distinct_id(events)
    profile = mixpanel.get_engage_profile(resolved) if resolved else None

distinct_id_counts = mixpanel.distinct_id_counts(events)  # for Sources
```

**Anchor the window on `mixpanel.project_today()`, not `dt.date.today()` or a UTC clock.** Mixpanel validates `from_date` / `to_date` against the **project's** calendar — whose timezone, and the `/api/app/me` read that resolves it, are canonical on `Tools/Mixpanel` → Project timezone (declared in `knowledge.reads` above, so it is pre-fetched) — and rejects anything past it outright: `400 to_date cannot be later than today; to_date=2026-08-05; today=2026-08-04`. `dt.date.today()` follows the *host's* timezone, which matches the project only on a Pacific machine and runs a full day ahead for the whole US evening on a UTC-clocked host (CI, a container, a Cowork sandbox). `export_events` / `find_events_by_uid` clamp both ends to the project's current date as a backstop, but it only corrects a date that is too *late* — a date that is too early passes through silently and just returns less data. So the clock still has to be right at the call site.

The helper raises `MCPFallback` if the three env vars aren't set; the caller falls through to Recipe B.

**Recipe B — MCP (fallback).** Run an Insights query to find which `$distinct_id`(s) carry events tagged with this UID, walking the chain in order:

```text
Run-Query insights, breakdown=$distinct_id, filter <property>=<UID>, dateRange 30d (or wider)
```

If all three return 0 rows: the user has no Mixpanel footprint over the window. Continue with `distinct_id = <Firebase UID>` as a final fallback (it'll likely return nothing too) and surface the gap explicitly in **Notable signals** rather than blocking the report. The Aura API + subscriptions data alone may still produce a useful profile for an Aurie-only / AurieWeb-only user.

If the query returns multiple `$distinct_id` values, take the one with the highest event count for downstream calls and note the others in **Sources** — multiple distinct ids on one UID can indicate cross-device usage or an `identify()` miss worth flagging. The helper's `distinct_id_counts()` returns the full ranked list for this.

#### 2.2 — Pull the engage profile (using the resolved distinct_id)

**Recipe A — API.** The Recipe A block in 2.1 above already populated `profile`. Capture the full property bag and map known properties via `Tools/Mixpanel/User Properties`. Replay availability isn't exposed via the API today — skip the replays cross-reference on this path and note it in **Sources**.

**Recipe B — MCP.**

1. Call `Run-Query` for the engage record keyed by the resolved `$distinct_id` from 2.1. The exact query shape comes from the `Tools/Mixpanel` priors fetched in Step 1 — don't hard-code it here.
2. Capture the full property bag returned. Map known properties via `Tools/Mixpanel/User Properties` to their team-canonical names.
3. Also call `Get-User-Replays-Data` with the resolved `distinct_id` — surfaces session replay availability and counts, useful as an "is this user currently active" heuristic. Skip silently if the call returns nothing.

**If 2.1 found events but the engage profile is missing:** that's normal for users who fired events but never had a `$people.set` call run against them (common for very new web signups, anonymous browsing). Continue with the event stream alone for the **Engagement** / **Top features** sections; flag the missing engage in **Notable signals** so the team can decide whether the `identify()` flow needs work for this surface.

**If 2.1 returned 0 events across all three property names:** stop here. Surface the gap to the user — do not synthesise from no Mixpanel data when the Aura API + subscriptions data is also thin. Suggest verifying the UID and the user's tracked surface.

### Step 3 — Pull the user's event stream

Scoped to the resolved `$distinct_id` from Step 2.1 (or equivalently, to the `UserID` event property) over the chosen `time_window`. **Do not use the raw Firebase UID as the `distinct_id` filter** — that's the recipe bug Step 2 exists to fix.

**Fetch:**

- **Recipe A — API.** The `events` list returned by `find_events_by_uid` in Step 2.1 is already the user's event stream over the window — no second call needed. Each event is a `{"event": "...", "properties": {"$distinct_id": "...", "time": ..., ...}}` dict from the Export JSONL stream. Sort by `properties.time` descending to get the most-recent-first view the steps below expect.
- **Recipe B — MCP.** Use `Run-Query` to pull events filtered by the resolved `$distinct_id`, ordered by time descending, capped per the window flag. Equivalently, filter by `UserID == <Firebase UID>` (the property the events themselves carry) — same row set, no resolution required for the filter (the resolution still matters for engage profile / replays where you need a `distinct_id`-keyed lookup).

**Then, on either transport:**

1. Group by `event` name. For each group, capture: count, first/last seen timestamps, and any properties relevant for the **Top features** / **Journey** / **Subscription history** sections (e.g. `vertical`, `subscription_state`, `feature_id`, `product_id`, `subscription_id`, `provider`).
2. Tag known journey events using `Tools/Mixpanel/Events`. Aura's canonical journey events vary by app — examples include sign-up, login, trial start, conversion, cancellation, paywall views, content plays. Pull the actual taxonomy from the priors; don't enumerate here (it drifts).
3. **Capture every subscription lifecycle event** as a chronological list — trial start, subscription start, conversion, paywall view, subscription cancelled, subscription renewed, subscription resumed, subscription updated, refund, etc. (exact event names live in `Tools/Mixpanel/Events`). These events feed the **Subscription history** narrative alongside the authoritative subscription rows from Step 5 (see Step 6's authority rule).

If the event stream is empty over the window but the engage profile exists, that's a real signal: the user signed up at some point but hasn't been active recently. Call this out explicitly in **Notable signals** rather than treating it as missing data.

### Step 4 — Best-effort: cross-reference the Aura API user record

```bash
curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/data:analyze-user-behavior (session ${AINATIVE_SESSION_NAME:-adhoc})" -fsS -H "Authorization: Bearer $AURA_API_TOKEN" \
  https://services.aurahealth.io/users/<UID> \
  | jq '{ id, email, givenName, vertical, locale, deviceOS, sourcePlatform, attributionData, createdAt, loggedInAt, onboarding, role, isAIReviewer, test }'
```

Capture identity / origin: `givenName` / `email` (domain only in the report), `sourcePlatform` (e.g. `AurieWeb` vs `AuraIOS`), `deviceOS`, `locale`, `createdAt`, `loggedInAt`, and the `onboarding.conversation` quiz answers + `onboarding.highlight` insight if present (these are surprisingly informative for cancellation triage — a mismatch between the user's quiz answers and the produced highlight is a real signal).

Also capture the **internal-account signals** — `email` domain, `role`, `isAIReviewer`, `test` — plus check the UID against the pre-loaded `Team/Internal Accounts` allowlist. An account is an **employee / internal / test account** when *any* of: the UID (or an auth-provider id / email) is in `Team/Internal Accounts`; the email ends in `@aurahealth.io`; `isAIReviewer = true`; `test = true`; or `role ∈ {admin, agent, service, system, creator, audio-engineer}`. These fields are **not PII** (they're account metadata, not identity) — safe to report the flag + which rule fired. This is what lets `data:profile-user` (and the feedback/rating/PMF/cancellation orchestrators) flag an entry as employee-originated rather than a genuine user signal.

Also capture **`attributionData`** — the SDK-supplied payload that records *how the user arrived*. **Treat it as an opaque JSON blob**: the schema has drifted across SDK generations and acquisition surfaces, and the field may carry any subset of dozens of keys (newer AppsFlyer-style `af_*` keys, legacy keys like `installSource` / `installAt` / `campaignName` / `adsetName` / `appsflyerAdName` / `fbAdName` / `keywords` / `agency` / `affiliateId` / `promocode` / `referralCode` / `referrerId` / `siteSourceName`, or organic-only payloads with just `sourcePlatform`). **Do not assume a specific shape.** Persist the raw object verbatim in working memory and let Step 6 do the interpretation.

Examples of shapes seen in production (illustrative — the actual payload can be different):

- **Paid AppsFlyer** — `{ "af_ad_type": "ChatGPT Relationships -F- 04-15-26", "af_channel": "Facebook_Mobile_Feed", "af_keywords": "120246849049970184", "campaign": "Aurie_WO_Scale_04-17-26 - Purchase", "media_source": "fb", "sourcePlatform": "AurieWeb" }`
- **Paid legacy** — may include any of `campaign`, `campaignName`, `campaignId`, `adsetName`, `adsetId`, `appsflyerAdName`, `appsflyerAdSet`, `fbAdName`, `fbAdSet`, `installSource`, `installType`, `agency`, `affiliateId`, `siteSourceName`, `medium`, `term`, `content`, `keywords`, `clickId`, `clickAt`, etc.
- **Referral / promo** — `referralCode`, `referrerId`, `referralType`, `promocode`, `affiliateId`
- **Organic / direct** — only `sourcePlatform` (e.g. `{ "sourcePlatform": "AIiOS" }`)
- **Empty / missing** — `null`, `{}`, or absent entirely; treat as "attribution unavailable"

Treat the payload as non-PII campaign metadata — safe to surface verbatim in the report, Slack reply, and Notion row. The interpretation rules in Step 6 must be schema-agnostic: pick the most semantically informative keys when known names match, otherwise dump the non-empty keys as-is rather than dropping the payload.

**Important:** the `premium`, `productId`, `provider`, `purchaseAt`, `premiumPaidAt`, `premiumCancelledAt`, `autoRenew`, `period`, `transactionId`, `lifetime`, `paid`, `expirationAt` fields on this row mirror **only the `type:"content"` subscription row** (the consumer-facing Aura content-app sub) — they are `null` for users whose paid products are any of the other five types (`ai`, `coaching`, `business`, `community`, `coach`). **Do not read them as the canonical "is this user paying?" answer.** Use Step 5 for that. See [Get User By Id](https://www.notion.so/35875e23af208101b456f25cbc15e5f8) for the full warning.

If the call fails (401/403, network error, Cowork sandbox), record "Aura profile lookup unavailable" in the **Sources** block and continue.

### Step 5 — Pull the authoritative subscription history (`POST /subscriptions/list/:userId`)

This is the canonical source of truth for whether the user is paying for **any** Aura Health product. Returns one row per subscription type per user — types are `ai` (Aurie) plus Aura's `content` / `coaching` / `business` / `community` / `coach`. Each row carries `cancelReason` / `cancelFeedback` / `subscriptionCampaign` / `provider` / `purchaseAt` / `expirationAt` / `autoRenew` / `trial` / `lifetime`. Full doc: [List Subscriptions By User Id](https://www.notion.so/35975e23af20818088b4e59dd028bbdd); type taxonomy: [Subscriptions](https://www.notion.so/35975e23af20817ab2ded91f7ca8cbbc).

```bash
curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/data:analyze-user-behavior (session ${AINATIVE_SESSION_NAME:-adhoc})" -fsS -X POST \
  -H "Authorization: Bearer $AURA_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"includeDeleted": true}' \
  https://services.aurahealth.io/subscriptions/list/<UID>
```

Use `includeDeleted: true` so the report covers refunded / revoked rows too — a normal cancellation does *not* soft-delete a row (it just sets `autoRenew=false` and populates `cancelReason`/`cancelFeedback`), so the default already returns cancelled-but-not-deleted rows.

What to capture from each row, for the **Subscription history** and **Subscription state** sections:

- `id` (`<userId>-<type>` e.g. `…-ai` for Aurie, `…-content` for the Aura content app, `…-coaching` / `…-business` / `…-community` / `…-coach` for the other Aura products)
- `type` (`ai` = Aurie; `content` / `coaching` / `business` / `community` / `coach` = Aura)
- `provider` (`stripe`, `apple`, `google`, `aura` for internal grants, etc.)
- `productId` (Stripe `price_*`, Apple/Google product id, etc.)
- `purchaseAt`, `expirationAt`, `trial`, `autoRenew`, `lifetime`
- `cancelReason` + `cancelFeedback` (these are **the same values** k8bot posts to `#cancellation-feedback` — no need to re-resolve from Slack if the row is in hand)
- `subscriptionCampaign` (acquisition tag, e.g. `aurie_web` for the Instagram → AurieWeb funnel)
- `revenue`, `period`, `duration`
- `deletedAt` (`null` for normal cancellations; non-null only for refunds/revokes — flag those explicitly)

Behaviour notes:

- **HTTP 200 + `[]`** means the user has no subscription rows on file — distinct from a 404 on `/users/<id>` which means "no user record at all." Don't conflate.
- **Auth:** `AURA_API_TOKEN` (system role) works as of PaymentService [`e791483`](https://github.com/AuraHealth/PaymentService/commit/e791483) (2026-05-07). A missing or invalid token gets 401; a wrong-role token gets 403; a UserService outage gets 503.
- If the call fails (network, Cowork sandbox, transient 5xx), record "Aurie subscriptions endpoint unavailable" in **Sources** and proceed. The Mixpanel subscription events from Step 3.4 + the Aura API premium fields from Step 4 (Aura-only) are weaker fallbacks. When `cancellation_feedback` was passed and the subscriptions endpoint is unavailable, surface the gap in the **Headline** since the **Subscription history** section becomes lossy.

### Step 6 — Build the analysis plan, then synthesise

**Plan-before-synthesis cue.** Before drafting the report, lay out a one-paragraph plan describing:

- The Mixpanel `distinct_id` resolution outcome (resolved → which property; or "no Mixpanel footprint" if Step 2.1 returned 0 across all three property names).
- The window analysed (e.g. "90 days, 142 events across 8 distinct event types").
- The subscription state at a glance pulled from Step 5 (e.g. "1 row: Aurie Stripe trial, autoRenew=false, cancelReason='Didn't help me the way I hoped', expires 2026-05-11" — vs "0 rows: never paid"). Cross-reference with Step 3.4 events: are the Mixpanel subscription events consistent with the rows from Step 5?
- The Aura API agreement-or-disagreement points from Step 4: vertical, locale, sourcePlatform; the onboarding-quiz-vs-highlight match where applicable.
- Whether `cancellation_feedback` is shaping the synthesis (and what specifically you'll be looking for — e.g. for "too expensive": paywall views in the 7d before cancellation + product price hits; for "didn't help me": engagement breadth + content completion + which features were tried; for "not conversational": `AI Conversation Session Started` count vs other events).
- Which sources came back empty (no replays? no events? no Aura API? no subscriptions endpoint?).

This is the moment to slow down — without it, profiles tend to skim the property bag and produce a flat list of attributes rather than a synthesised picture.

Synthesis sections (always in this order):

1. **Headline** — a one-line TL;DR of the user. Lead with UID prefix only (never paste the full UID without need), plan, vertical, primary signal. Example: *`0VwbDpK…` — Sleep vertical, monthly trial day 4 of 7, high content engagement, no Aurie sessions yet.*
2. **Identity** — UID prefix (first 8 chars), email domain (not full email), signup date, vertical, plan, platform, locale, last seen. Pull from Mixpanel engage; cross-reference with the Aura API and call out any disagreement. **Never paste `givenName`, `familyName`, or the full email from `/users/<id>`** in this section or any downstream surface (Slack, Notion, forwarded reports) — see `Compliance/Privacy` for the canonical rule.

   **Internal / employee account flag** — when the Step 4 internal-account check fired, add a one-line bold flag right under the identity fields: `**⚠️ Internal / employee account** — <who if from Team/Internal Accounts, else "—"> (matched: <rule that fired, e.g. allowlist:google:… | email_domain=aurahealth.io | isAIReviewer | test | role=admin>).` When nothing fired, omit the line entirely (don't print "not internal"). This flag is the signal `data:profile-user` reuses for its employee banner — surface it here so a standalone behavioural readout carries it too.

   **Acquisition sub-line + raw payload** — render `attributionData` (Step 4) below the identity fields. The schema is not fixed (see Step 4 for the field zoo), so the rule is **schema-agnostic interpretation, never schema-dependent assumptions**:

   1. **Headline line** — synthesise a one-line summary by picking the most semantically informative non-empty values across four categories, in this priority order. For each category, walk the candidate keys and take the first non-empty value:
      - **Source / channel**: `media_source` → `installSource` → `source` → `siteSourceName` → `agency` → `affiliateId` → `medium` → `sourcePlatform`
      - **Campaign**: `campaign` → `campaignName` → `campaignId` → `fbCampaignId`
      - **Creative / ad**: `af_ad_type` → `appsflyerAdName` → `fbAdName` → `appsflyerAdSet` → `adsetName` → `adId`
      - **Free-text signal** (only when none of the above resolved a useful value): first non-empty of `keywords` → `af_keywords` → `content` → `term` → `referralCode` → `promocode` → `referrerId`
   2. Format the headline as `<source> · <campaign> · <creative> · <free-text>` — drop any category that didn't resolve. Suppress a value that's purely a numeric id (e.g. `af_keywords` set to a 15-digit Facebook ad id) when a sibling human-readable value already carries the meaning; keep numeric ids when they're the only signal.
   3. If **none** of the priority keys above resolved AND the payload is non-empty (some unknown SDK shape or new keys we don't yet know about), fall back to: *"unrecognised shape — keys: `<comma-separated non-empty keys, max 6>`"* and append the raw payload (next step). Don't drop the data — surface the keys so a reader can update this skill's priority chain.
   4. If the payload is `null`, `{}`, or absent, render: *`attribution unavailable`*. Distinguish in the report between "Aura API call failed" (Step 4 logged "Aura profile lookup unavailable") and "API returned an empty/null attributionData".
   5. **Always append the raw non-empty payload** in a compact fenced block right below the headline — one line per key, sorted by key name, null/empty values dropped. This is the verifiability artifact: the headline is interpretation; the block is the data. Keep it short (typically 3–8 lines).

   Examples:
   ```
   - **Acquisition:** fb · Aurie_WO_Scale_04-17-26 - Purchase · ChatGPT Relationships -F- 04-15-26 · Facebook_Mobile_Feed
     ```
     af_ad_type: "ChatGPT Relationships -F- 04-15-26"
     af_channel: "Facebook_Mobile_Feed"
     af_keywords: "120246849049970184"
     campaign:    "Aurie_WO_Scale_04-17-26 - Purchase"
     media_source: "fb"
     sourcePlatform: "AurieWeb"
     ```
   ```
   ```
   - **Acquisition:** organic · sourcePlatform=AIiOS
     ```
     sourcePlatform: "AIiOS"
     ```
   ```
   ```
   - **Acquisition:** unrecognised shape — keys: `installSource, adsetName, promocode, referrerId`
     ```
     adsetName:   "..."
     installSource: "..."
     promocode:   "..."
     referrerId:  "..."
     ```
   ```

   Keep the raw block whenever the headline is non-trivial — it's how a reader audits whether the interpretation is faithful, and how the team spots new attribution shapes that this skill should learn to recognise.
3. **Engagement** — sessions over the window (count + cadence), days active, last active timestamp, replay availability if pulled. Quantitative.

   **Cost (optional sub-bullet).** If the caller passes `include_cost=true` (default `false`) OR if a parent orchestrator (`data:profile-user`) is composing this skill with a pre-fetched cost report, render a one-line cost summary here: *"Cost over window: $X.XX ($Y.YY/day) — OpenAI $X.XX · ElevenLabs $X.XX · LiveKit $X.XX · listens $X.XX."* (a LiveKit leg reported as pending or unavailable is written that way, never as \$0.00) The figures **must** come from `eng:report-user-costs` (the canonical multi-source per-user cost skill) — **never** from Braintrust's `metrics.estimated_cost` field, which is unreliable; see AI Knowledge [`Tools/Braintrust` → "Cost accounting"](https://www.notion.so/34c75e23af2081968f7bf7ee43e1442a). Skip the line entirely when `include_cost=false` and no cost report was supplied; this skill's lightweight default is behavioural only.
4. **Journey** — chronological list of journey events with timestamps. Examples: signup → first content play → paywall view → trial start → cancellation. Format:
   ```
   - **2026-04-12 14:22** — Signed up (vertical: Sleep, platform: iOS)
   - **2026-04-12 14:31** — First content played: "Sleep Story · Drift"
   - **2026-04-13 09:05** — Paywall viewed (variant: paywall_v3_control)
   - **2026-04-14 21:18** — Trial started (monthly_premium)
   ```
   Include only journey-tagged events here; non-journey events go to **Top features**.
5. **Top features** — top 5–10 non-journey events by count, with one-line descriptions pulled from `Tools/Mixpanel/Events`. Helps the reader see what the user actually *uses*. When `cancellation_feedback` is present, re-rank to surface features tied to the complaint first (e.g. for "not conversational": lead with `AI Conversation Session Started` count even if it's not top-5 by raw count).
6. **Subscription history** — chronological timeline. **Two sources, with a clear authority rule:**
   - **Primary (rows from Step 5):** one entry per row from `POST /subscriptions/list/:userId`. Each row gives you `type` (Aurie or Aura), `provider`, `productId`, `purchaseAt`, `expirationAt`, `autoRenew`, `trial`, `lifetime`, plus `cancelReason`/`cancelFeedback` and `subscriptionCampaign` when set. Format:
     ```
     - **2026-05-04 21:24** — Aurie trial started · stripe · price_1Sab5fIS90bgNudE3zMnUbEj · 7-day · campaign=aurie_web · revenue=\$69.99
     - **2026-05-06 13:01** — Aurie cancelled · autoRenew→false · reason="Didn't help me the way I hoped" · feedback="It's not helping the way I hoped." · expires 2026-05-11
     ```
   - **Cross-reference (events from Step 3.4):** weave in Mixpanel subscription events (`AI Subscription Modal Seen`, `AI Subscription Purchased`, `AI Cancel Subscription Tapped`, `AI Unsubscribing Reason Given`, etc.) for *user-side* timing — when did they see the paywall? how many cancel taps? — that the row alone doesn't show. Quote count + first/last seen for each event family. Don't invent timeline entries from events that don't have row counterparts; use them to *enrich* the row entries, not replace them.

   This section is the canonical record of what the user paid for and what they did around those decisions; section 7 (**Subscription state**) summarises the current snapshot derived from it.
7. **Subscription state** — current plan, billing state, trial dates if applicable, cancellation date if applicable. **Authority rule:** the rows returned by `POST /subscriptions/list/:userId` (Step 5) are source of truth. The Aura API user record's `premium*` fields (Step 4) describe Aura content-app only and are `null` for Aurie-only subscribers — never read them as the canonical "is this user paying?" answer. Mixpanel events (Step 3.4) and engage profile (Step 2.2) are cross-references that may lag by minutes during a transition. If sources disagree, lead with the subscriptions endpoint, cite the others, and note the disagreement in **Notable signals**. If Step 5 was unavailable (rare — endpoint down or auth misconfigured), say so explicitly here rather than silently falling back to the Aura premium fields.
8. **Notable signals** — 2–4 bullets calling out anything worth a follow-up. Examples: "ten paywall views in 48h, never converted", "active on iOS but no Aurie sessions despite vertical=Anxiety", "trial started but never opened the app on day 1". Each bullet should be falsifiable — checkable against another user's data. When `cancellation_feedback` is present, at least one bullet must directly cite evidence (or the absence of evidence) that explains the stated reason; if the data contradicts the reason, say so explicitly. **Cross-reference `attributionData` (Step 4) with the cancellation reason when *any* attribution field carries a specific creative / campaign / referral signal** — could be `af_ad_type`, `appsflyerAdName`, `fbAdName`, `adsetName`, `campaignName`, `keywords`, `promocode`, or any newer key. The canonical example is a user acquired via a "ChatGPT Relationships" creative who cancels citing "not conversational" — that's expectations-mismatch, not product-quality. Quote the actual key + value from the raw payload so the bullet is auditable; do not paraphrase or assume which key carries the meaning across schemas. **Unit-economics framing (cost ↔ save-decision bridge):** when the Cost sub-bullet on **Engagement** (item 3) rendered AND `cancellation_feedback` is set, one of the bullets must bridge the cost number to a save-decision implication — quantitatively comparing the user's total cost over the window to standard sub pricing. Examples: *"$Z over <window> — a monthly Aurie sub at standard pricing covers ~X months of this usage; high-conviction save target"* (low cost relative to engagement), or *"$Z over <window> approaches monthly sub revenue; converting them is break-even at best — weigh acquisition cost"* (when cost is genuinely high for the cohort). This is the bullet that connects unit economics to the recommendation the team has to make; skip it when Cost wasn't computed (lightweight behavioural-only mode).
9. **Sources** — every URL and path used. Required entries:
   - Mixpanel project link with `distinct_id` filter applied (link to the user's Mixpanel record). Note the resolution path: e.g. *"distinct_id resolved via `UserID` event property"* / *"no Mixpanel footprint over window"*.
   - Aurie subscriptions endpoint: `POST https://services.aurahealth.io/subscriptions/list/<UID>` with `includeDeleted=true` (or "unavailable" if the call failed)
   - AI Knowledge paths consulted (e.g. `Tools/Mixpanel/Events`, `Products/Aurie`, `Tools/Aura API/Subscriptions`)
   - Aura API user profile URL (or "unavailable")

If the user has Aurie conversations available (look at `AI Conversation Session Started` count from Step 3), cross-link to `data:analyze-user-conversations` in **Notable signals** — *"X Aurie sessions in window; consider running `analyze-user-conversations` for transcript-level detail."* Don't run it here; that's the caller's call.

### Step 7 — Return inline

Return the report as the skill's result. Don't post it anywhere, don't write it to disk, don't update Notion. The caller asked for the profile; what they do with it is their call.

If the user explicitly asks during the same turn ("…and post it to that Slack thread"), do that — but it's their explicit instruction, not the skill's default behaviour.

## Output format

A single Markdown document organised exactly as listed in Step 6 (Headline → Identity → Engagement → Journey → Top features → Subscription history → Subscription state → Notable signals → Sources). Length depends on `time_window` and how active the user is:

- Light user, `30d` → ~40 lines.
- Typical user, `90d` → ~80–120 lines.
- Power user, `90d` → ~150–200 lines.

The **Headline** section is a single line so the caller can grab a TL;DR for Slack without re-reading the whole report.

## Edge cases

**No engage profile for the UID, but events found.** Continue with the event stream alone for **Engagement** / **Top features**; flag missing engage in **Notable signals**. Common for very fresh signups or anonymous-only browsing.

**No engage profile AND no events for the UID across all three property fallbacks.** If `/users/<id>` returned 200 (user exists) and `/subscriptions/list/<id>` returned non-empty rows, you can still produce a useful profile from identity + subscription history alone — common for AurieWeb-only users whose Mixpanel client never fired during the trial. State explicitly in **Headline** that the picture is "Aura API + subscriptions only — no Mixpanel footprint." If all three sources came back empty, *then* stop and ask the user to confirm the UID.

**Engage profile exists but no events in window.** Real signal — surface it explicitly in **Notable signals**: *"User has not fired a tracked event in the last `<window>`."* Then narrow the question: was the user always inactive, or did they fall off recently? The **Journey** section's most-recent dated event answers that.

**Sources disagree on current subscription status.** Step 5's `/subscriptions/list/:userId` rows are authoritative. Mixpanel events (Step 3.4) and the Aura API user record's `premium*` fields (Step 4) are cross-references — Mixpanel can lag by minutes during a transition; the `users.premium*` fields are Aura-content-only and irrelevant for Aurie subs. Lead **Subscription state** with what Step 5 says, cite the others, note disagreements in **Notable signals**.

**Engage profile says active but Step 5 shows a recent cancellation.** Trust Step 5. The engage property bag may not have re-materialised yet — specifically common right after a `Subscription Cancelled` event lands.

**Mixpanel `distinct_id` filter returned 0 events for the Firebase UID.** That's the recipe bug Step 2.1 fixes. Always resolve via the property-filter fallback chain (`UserID` → `$user_id` → `userId`) before concluding the user has no Mixpanel footprint. Pre-Step-2.1 versions of this skill mis-classified AurieWeb users as "no Mixpanel data" because their `distinct_id` is anonymous (`$device:...`) while the Firebase UID lives only as event properties.

**UID has hundreds of events but only one event type.** Profile reads as flat. That's still a real profile — call it out: *"User has fired only `<event_name>` (N times). Engagement is narrow."*

**Cowork sandbox.** `AURA_API_TOKEN` is blocked from `mcp__workspace__bash` (PROD-1305). Use the per-session paste-export workaround in [`/CLAUDE.md`](../../../../CLAUDE.md) → "Known Cowork constraints", or run from the host harness where env reaches subprocesses normally.

**Re-running on the same UID + window.** Idempotent. The report has no persistence; re-running just regenerates the analysis with potentially newer events. No state to corrupt.

## Out of scope (v1)

- **Multi-user batch profiles.** One user per invocation. For aggregate views, use the Mixpanel UI or build a separate skill — the per-user report is too long to fan out responsibly.
- **Auto-trigger on Slack messages or k8bot.** v1 is on-demand only.
- **Persistence (Notion / Slack post / file).** The caller decides — the report is ephemeral by design.
- **Aurie transcript inclusion.** That's `analyze-user-conversations`'s job. This skill links out to it via **Notable signals** when transcripts are available.
- **Predictive scoring (LTV, churn risk).** Profile only — no model inference. The report is descriptive, not prescriptive.
- **Computing cost from Braintrust `metrics.estimated_cost`.** Hard prohibited. If a cost number is needed, invoke `eng:report-user-costs` (or its sub-skills `eng:report-openai-user-cost` / `eng:report-elevenlabs-user-cost` / `eng:report-livekit-user-cost` / `eng:report-listens-user-cost` for a single source). Braintrust's `estimated_cost` field has pricing-table gaps, wrong rate tiers, and a single-project framing problem — see AI Knowledge [`Tools/Braintrust` → "Cost accounting"](https://www.notion.so/34c75e23af2081968f7bf7ee43e1442a) for the rule and the 17× under-count case that motivated it.

## Key IDs and references

- **Mixpanel project:** the Aura production project — exact ID lives in `Tools/Mixpanel`.
- **Aura API base URL:** `https://services.aurahealth.io`
- **Sibling skill:** [`data:analyze-user-conversations`](../analyze-user-conversations/SKILL.md) — pair this skill with that one for end-to-end user investigations.
- **Linear ticket:** [PROD-1624](https://linear.app/aura-health/issue/PROD-1624/add-dataanalyze-user-behavior-skill).
- **Parent epic:** [PROD-1522](https://linear.app/aura-health/issue/PROD-1522/add-update-skills).
