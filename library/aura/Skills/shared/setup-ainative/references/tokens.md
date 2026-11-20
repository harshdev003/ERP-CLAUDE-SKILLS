# Phase 6 — API keys and tokens

Per-token walkthrough for contributor onboarding: where each value comes from, who can issue it, and what degrades without it.

**The canonical inventory is `/CLAUDE.md` → Token inventory.** This page is the *setup procedure* for those variables, not a second list of them — when a token is added, add it there first.

AINative skills authenticate to external services using API keys read from
`~/.claude/settings.json` under the `env` block. **Tokens are soft requirements** —
skills attempt to run without them, fall back to an MCP server when one exists, and
only prompt the user mid-run if they hit a wall they can't route around. Don't
pressure the user to set every key up front. Walk through the inventory, explain
which skills each token unlocks, and let them choose what to install now versus
later.

Explain to the user:

> AINative skills call out to Slack, Linear, Notion, Braintrust, Bugsnag, GCP, Gemini, OpenAI,
> Anthropic, and Aura's own APIs. The team standard is **API token first, MCP fallback when there isn't one** —
> so a missing token usually
> just means "slower" or "capped result set" rather than "broken" — install only the
> ones for the skills you actually plan to run. The keys live in
> `~/.claude/settings.json` under an `env` block; Claude Code loads them at startup,
> so a restart is needed after any change. I'll check which ones you already have,
> tell you what each one unlocks, and add the ones you want.

### 6a: Inventory existing keys

Read the current settings file and report which of the expected keys are already set
(don't echo the values themselves — just present/missing):

```bash
SETTINGS="$HOME/.claude/settings.json"
EXPECTED='ANTHROPIC_API_KEY AURA_API_TOKEN AURA_USER_TOKEN BRAINTRUST_API_KEY BUGSNAG_TOKEN CARTESIA_ADMIN_API_KEY CIRCLE_TOKEN DEEPGRAM_OWNER_API_KEY ELEVENLABS_ADMIN_API_KEY ELEVENLABS_API_KEY GCP_KEY GEMINI_API_KEY INWORLD_API_KEY IPGEOLOCATION_API_KEY LINEAR_API_KEY MIXPANEL_SERVICE_ACCOUNT_USERNAME MIXPANEL_SERVICE_ACCOUNT_SECRET MIXPANEL_PROJECT_ID NOTION_TOKEN OPENAI_ADMIN_API_KEY OPENAI_CLAUDE_API_KEY OPENAI_EVALUATIONS_API_KEY OPENAI_CONVERSATION_API_KEY OPENAI_INSIGHTS_API_KEY OPENAI_JOURNAL_API_KEY OPENAI_MEMORY_API_KEY POSTMAN_API_KEY SLACK_BOT_TOKEN SLACK_USER_TOKEN'

if [ ! -f "$SETTINGS" ]; then
  echo "No settings.json yet at $SETTINGS — will create it via update-config."
else
  for key in $EXPECTED; do
    present=$(python3 -c "import json,sys; d=json.load(open('$SETTINGS')); print('yes' if d.get('env',{}).get('$key') else 'no')" 2>/dev/null)
    echo "$key: ${present:-no}"
  done
fi
```

Show the user the present/missing list, then offer to set up the missing ones one at
a time. For each missing key, **delegate to the `update-config` skill** to do the
actual write — never edit `~/.claude/settings.json` by hand from inside this skill.
Don't ask Claude to read the value; ask the user to paste it directly into
`update-config`.

### 6b: Per-token guidance

For each missing key, give the user a short, accurate instruction on where to get the
value. Skip keys that are already set. Columns:

- **Self-serve?** — "Yes" if the user can create the value themselves; "Team" if it
  must be issued by Jerome / Rohan / eng (ask in `#ai-eng`).
- **MCP fallback?** — whether skills can degrade to MCP when the token is absent.

| Env var | Used by | Self-serve? | MCP fallback? | Where to get it |
|---|---|---|---|---|
| `ANTHROPIC_API_KEY` | `eng:qa-prompt` (Anthropic / Claude-model Braintrust prompts — see [ENGPROM-54](https://linear.app/aura-health/issue/ENGPROM-54)) | Yes | No | **Optional** — only needed when the Braintrust prompt under QA declares a Claude model. Personal API key from <https://console.anthropic.com/settings/keys> under the Aura Anthropic org → "Create Key". The `anthropic` Python/JS SDKs auto-read this env var name. No MCP fallback exists, so a missing key is a hard fail when the prompt is Claude-flavoured |
| `AURA_API_TOKEN` | `data:sync-experiments`, `data:analyze-user-behavior`, and the Aura API generally (the `llmCall` ledger moved to BigQuery under `GCP_KEY`, ENG-1657) | Team | No | Ask in `#ai-eng` for the current dev API token |
| `AURA_USER_TOKEN` | `qa:chat-with-aurie` (user JWT for `/ai/dialogue/ask`) | Yes (with caveat) | No | Sign in to the Aura app on web/mobile and copy the JWT from a network request, or grab it from the team-shared dev account doc. Pre-check expiry — JWTs rotate |
| `BRAINTRUST_API_KEY` | `data:fetch-conversation-topics`, `data:analyze-user-conversations`, `eng:update-intelligence-prompt` | Yes | No | <https://www.braintrust.dev/app/aurahealth/settings/api-keys> → "New API key" |
| `BUGSNAG_TOKEN` | `eng:investigate-bugsnag-errors` ([PROD-1659](https://linear.app/aura-health/issue/PROD-1659)) | Yes | No | <https://app.bugsnag.com/settings/my-account/auth-tokens> → Personal auth tokens → "Generate a new auth token". Used as `Authorization: token <BUGSNAG_TOKEN>` with `X-Version: 2` against the [Data Access API](https://developer.smartbear.com/bugsnag/docs/data-access#authentication). No Bugsnag MCP exists today, so a missing token is a hard fail (not a degrade) |
| `CARTESIA_ADMIN_API_KEY` | `eng:analyze-cartesia-costs`, `eng:store-aurie-user-daily-cost` + daily/weekly cost reports (Cartesia TTS usage — [PROD-3178](https://linear.app/aura-health/issue/PROD-3178)) | Yes | No | **Optional** — only the voice-cost skills need it. Cartesia **admin** key (`sk_car_admin_…`, NOT a regular `sk_car_` key — regular keys 401 on `/usage`) from <https://play.cartesia.ai/keys/admin>. Sent as `Authorization: Bearer <key>` + header `Cartesia-Version: 2026-03-01` against `/usage/credits` + `/usage/agents`. No MCP fallback |
| `CIRCLE_TOKEN` | `eng:investigate-circleci` ([PROD-2978](https://linear.app/aura-health/issue/PROD-2978)), `eng:rerun-circleci` ([PROD-2979](https://linear.app/aura-health/issue/PROD-2979)) | Yes | No | **Personal** API token from <https://app.circleci.com/settings/user/tokens> → "Personal API Tokens" → "Create New Token". Must be a **personal** token — project tokens are rejected by the v2 workflow-rerun endpoint with a 403 (v1.1 read-only paths accept either). Token resolution order: `CIRCLE_TOKEN` → `CIRCLECI_TOKEN` → `CIRCLE_CI_TOKEN` → `CIRCLECI_API_TOKEN`. Sent as `Circle-Token: <token>` header. No CircleCI MCP exists, so a missing token is a hard fail |
| `DEEPGRAM_OWNER_API_KEY` | `eng:analyze-deepgram-costs`, `eng:store-aurie-user-daily-cost` + daily/weekly cost reports (Deepgram STT usage — [PROD-3179](https://linear.app/aura-health/issue/PROD-3179)) | Team | No | **Optional** — only the voice-cost skills need it. Deepgram management key with **usage:read** on the **production** STT project — `7e42b984-…` (**“Aura Health”**), the project the LiveKit voice agent authenticates against (ask `#ai-eng` to mint a key or invite you). Sent as `Authorization: Token <key>` against `/v1/projects/{id}/usage`, `/balances`, `/billing/breakdown`. Falls back to `DEEPGRAM_API_KEY`; Deepgram is LiveKit-path-only so $0 on a no-LiveKit-traffic day is expected, and skills still degrade gracefully (reachable project + a `deepgram_pending` note) if the key is missing or loses access |
| `ELEVENLABS_ADMIN_API_KEY` | `eng:analyze-elevenlabs-costs` ([ENG-511](https://linear.app/aura-health/issue/ENG-511)) | Yes (with caveat) | No | **Optional** — only the cost-reporting skill needs it. Workspace-admin scope key created at <https://elevenlabs.io/app/developers/api-keys> (developer key on your own account) or <https://elevenlabs.io/app/developers/service-accounts> (service account, for CI). When creating the key choose **Restrict Access** and grant **Workspace Analytics → Access** (required, gates `/v1/usage/character-stats`). Optionally grant **Workspace → Read** so `--breakdown api_keys` resolves key IDs to names. Leave everything else at *No Access*. The cost skill falls back to `ELEVENLABS_API_KEY` if absent but workspace totals will under-count |
| `ELEVENLABS_API_KEY` | `eng:investigate-elevenlabs-conversations`; fallback for `eng:analyze-elevenlabs-costs` | Yes | No | Personal/per-agent key at <https://elevenlabs.io/app/developers/api-keys> (Aura Health workspace). Used as `xi-api-key: $ELEVENLABS_API_KEY` against `https://api.elevenlabs.io/v1/...` |
| `GCP_KEY` | `eng:investigate-gcp-logs`, BigQuery reads (cost stores, and the `llmCall` ledger `aura-main.llm.llm_call` that every ledger-reading skill uses since ENG-1657) | Team | No | GCP service-account JSON (`claude@firebase-auratech16`): `roles/logging.viewer` for logs, BigQuery read on `aura-main`. Ask `#ai-eng` |
| `GEMINI_API_KEY` | Gemini-powered marketing/creative skills | Yes | No | <https://aistudio.google.com/apikey> → create key under your `aurahealth.io` Google account |
| `INWORLD_API_KEY` | `eng:store-aurie-user-daily-cost` + daily cost report (Inworld TTS usage) | Team | No | **Optional** — only the voice-cost skills need it. The LiveKit voice agent's Inworld key (in its deployed env). Sent as `Authorization: Basic <key>` to `GET https://api.inworld.ai/usage/v1/records`. No MCP fallback; without it Inworld $ is derived from the agent's logs |
| `IPGEOLOCATION_API_KEY` | `eng:lookup-ip-location`, `eng:monitor-firebase` (city of a console read) | Team | No | Ask in `#ai-eng`. Optional: locations come back blank (`no_key`) without it |
| `LINEAR_API_KEY` | Every Linear-touching skill (`add-linear-issues`, `daily-status`, etc.) | Yes | Yes (Linear MCP covers most reads) | <https://linear.app/aura-health/settings/account/security> → Personal API keys → "New API key" |
| `MIXPANEL_SERVICE_ACCOUNT_USERNAME` / `_SECRET` / `_PROJECT_ID` | `data:profile-user`, `data:analyze-user-behavior` ([ENG-607](https://linear.app/aura-health/issue/ENG-607)) | Yes (project admin creates a project-scoped service account) | Yes (Mixpanel MCP) | Project-scoped service account at <https://mixpanel.com/project/963821/settings/service-accounts> → "Add Service Account". Role: **Analyst** (read-only). Set `_PROJECT_ID=963821` (the Aurie project). Sent as HTTP Basic auth against `https://mixpanel.com/api/2.0/` and `https://data.mixpanel.com/api/2.0/` |
| `NOTION_TOKEN` | All AI Knowledge skills via REST (`retrieve-knowledge`, `record-knowledge`, `harvest-knowledge`, `data:sync-experiments`, etc.) | Team (integration secret) + user (teamspace connection) | Yes (`notion-search` MCP, capped at 25 results, no DB enumeration) | Integration secret from the shared `Aura AI Knowledge` integration. **Critical second step**: connect the integration to the AI Knowledge teamspace via teamspace `···` → Connections → Connect to → "Aura AI Knowledge". Without this step every REST call 404s |
| `OPENAI_ADMIN_API_KEY` | `eng:analyze-openai-costs` ([PROD-1725](https://linear.app/aura-health/issue/PROD-1725)) and sibling org-level cost skills | Team (Owner-only) | No | OpenAI **Admin** API key (`sk-admin-...`, distinct from the project-scoped keys below). Create at <https://platform.openai.com/settings/organization/admin-keys> → "Create new admin key". Requires Owner role on the OpenAI org. Works against `/v1/organization/*` (costs, usage, projects) — still required & valid there — but **not** stored-completions reads (`GET /v1/chat/completions`), which need a project-scoped `OPENAI_<PROJECT>_API_KEY` ([PROD-3572](https://linear.app/aura-health/issue/PROD-3572); see below). No MCP fallback exists, so a missing key is a hard fail (not a degrade) |
| `OPENAI_EVALUATIONS_API_KEY` | `eng:qa-prompt` + `eng:qa-conversation` — OpenAI prompt/eval inference + the self-critique scorer ([ENG-1171](https://linear.app/aura-health/issue/ENG-1171)) | Yes (project member) or Team | No | OpenAI **project-scoped service-account** key for the `Evaluations [Production]` project — **the project all AINative QA/eval inference bills to**. Deliberately not a product project: `Evaluations` is the sole member of `INTERNAL_PROJECT_BASE_NAMES` in `eng:analyze-openai-cost-per-user`, so eval spend is excluded from per-user attribution **and** from the attribution coverage ratio. Billing eval traffic to a product project instead divides it across real users — that is the $837 / $0.318 incident ENG-1171 fixed. Create at <https://platform.openai.com/api-keys> → Create new secret key → Owned by: Service account → Project: `Evaluations [Production]`. No MCP fallback — missing key is a hard fail for both QA skills. |
| `OPENAI_CLAUDE_API_KEY` | **No consumer skill** | Yes (project member) or Team | No | OpenAI project-scoped service-account key for the standalone `Claude` project. **Not required to run any skill** — skip it during onboarding. AINative QA/eval inference authenticates with `OPENAI_EVALUATIONS_API_KEY` instead, because `Evaluations [Production]` is the only project excluded from per-user cost attribution and from the attribution coverage ratio. This key exists for ad-hoc use against the `Claude` project, which carries ~$0 of traffic. |
| `OPENAI_<PROJECT>_API_KEY` (family) | Stored-completions readers: `OPENAI_MEMORY_API_KEY` → `eng:investigate-memory-ingestion-guardrail`; `OPENAI_CONVERSATION_API_KEY` → `data:analyze-user-conversations` (+ its copies; **not** `data:report-aurie-conversations`, which reads the `llmCall` ledger since ENG-1113); `OPENAI_JOURNAL_API_KEY` → `data:analyze-user-journal`; `OPENAI_INSIGHTS_API_KEY` → `eng:build-aurie-user-insights`; the multi-project cost skills (`analyze-openai-costs`, `report-openai-user-cost`, `investigate-openai-trace`, …) resolve every production project's key dynamically | Yes (project member) or Team | No | **Optional — install only the projects your skills touch.** OpenAI **project-scoped service-account** keys, one per project, for reading stored completions. Under [PROD-3572](https://linear.app/aura-health/issue/PROD-3572) — Admin keys can't hold the `model.request` scope these reads require. **Naming:** `OPENAI_<PROJECT>_API_KEY` from the project display name (`Memory [Production]` → `OPENAI_MEMORY_API_KEY`, `Conversation [Production]` → `OPENAI_CONVERSATION_API_KEY`, etc.). Create each at <https://platform.openai.com/api-keys> → "Create new secret key" → Owned by: Service account → Project: `<name>`. Multi-project skills degrade gracefully per project (missing key → that project skipped with a warning); single-project skills hard-fail without their key |
| `POSTMAN_API_KEY` | Postman collection management — agent/operator edits to the **Aura Services** collection (no dedicated skill yet) | Yes (personal) | Yes | **Optional.** Personal Postman API key from <https://go.postman.co/settings/me/api-keys> → "Generate API Key", with workspace write access. Sent as `X-Api-Key: <key>` against `https://api.getpostman.com` |
| `SLACK_BOT_TOKEN` | Slack-posting skills (`report-conversation-patterns`, `analyze-experiment` post-to-`#ai-product`, etc.) | Team | Yes (Slack MCP) | Bot User OAuth Token from the `aura-ai` Slack app at <https://api.slack.com/apps> → OAuth & Permissions (`xoxb-…`). Ask `#ai-eng` if you don't see the app |
| `SLACK_USER_TOKEN` | `shared:daily-status` Slack activity search (`search.messages`) to surface non-Linear work | Yes | Yes (degrades to bot-token channel sweep, then Linear-only) | **Optional and personal.** User OAuth Token (`xoxp-…`) from the `aura-ai` Slack app → OAuth & Permissions: add the `search:read` **User** Token Scope, then **reinstall as yourself** (search runs as the authorizing user, so it returns *your* visible channels + DMs). User-token-only — `search.messages` rejects a bot token (`not_allowed_token_type`), so this can't be the same value as `SLACK_BOT_TOKEN`. Skip it if you don't run daily-status |

### 6c: Let the user pick which keys to add now

Show the user the list of missing keys with a one-line "what it unlocks" summary
next to each, and ask which ones they want to set up now. Keys are additive — they
can always come back later via `update-config`, and skills will fall back to MCP or
prompt mid-run when an absent token actually blocks something. Do not push them to
add every missing key.

For each key the user opts in to:

1. Confirm what it's for and (if Team-issued) who to ask if they don't have it yet.
2. Show the "Where to get it" guidance from the table above.
3. Wait for them to confirm they have the value ready.
4. Invoke the `update-config` skill to add the key under the `env` block of
   `~/.claude/settings.json` — pass the env-var name and let the user paste the value
   into that skill's prompt. Do not pass the secret through this skill's context.
5. After all selected keys are added, remind the user that **environment changes only
   take effect after restarting Claude Code**.

In the Phase 7 summary, list intentionally-unset keys as "skipped" rather than
"missing" so the user isn't nagged about choices they made deliberately.

### 6d: Cowork caveat

Per CLAUDE.md, `mcp__workspace__bash` strips host env, so settings.json values don't
reach Cowork sandboxes. If the user is setting this up to run skills inside Cowork
specifically, mention the per-session `export` workaround (paste-and-export at the
start of a Cowork bash session). For local Claude Code use, the settings.json `env`
block is sufficient.

### 6e: Configure Cursor Bugbot (team recommendation)

Cursor's Bugbot reviews PRs in AuraHealth's GitHub repos that have it enabled
(currently AINative, PaymentService, and a few others — see CLAUDE.md → PR workflow
for the full list). Auto vs manual-trigger mode is a **per-user setting** in the
Cursor dashboard, not a per-repo setting, so each contributor configures their own.

**Team recommendation: manual-trigger mode.** Auto-review on every push burns
Bugbot credits and clutters PRs with low-signal comments on docs-only or trivial
changes. In manual mode, Bugbot only runs when someone explicitly comments
`bugbot run` or `@cursor review` on a PR.

Walk the user through it:

> One more thing — open <https://www.cursor.com/dashboard/bugbot> and switch your
> Bugbot config to manual-trigger mode. Cursor's auto-review fires on every push
> across all our enabled repos, which is noisy on docs-only PRs and burns Bugbot
> credits. After flipping this, you'll only see Bugbot when you (or another
> reviewer) post `bugbot run` on the PR. The trigger phrases (`bugbot run`,
> `@cursor review`) work the same regardless of which mode you're in.

This is optional but strongly encouraged for AuraHealth engineering. If the user
prefers auto-review, that's fine — note that they may want to pipe Bugbot
comments to their own filter (e.g. auto-resolve docs-only suggestions).

No verification step here — the Cursor dashboard doesn't expose the setting via
API. Trust the user to flip it (or note they declined) and move on.

---
