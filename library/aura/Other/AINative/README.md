# AINative

AuraHealth's AI-native tooling repository. Contains team plugins for Codex, Claude Code, and Cursor, distributable via their native marketplace systems.

## Plugins

| Plugin | Team | Contents |
|--------|------|----------|
| `shared` | Everyone | Org-wide skills and commands for all AuraHealth teams |
| `eng` | Engineering | Skills and commands for backend, mobile, and web development |
| `product` | Product | Skills and commands for spec writing, ticket management, and roadmap workflows |
| `data` | Data | Skills and commands for analytics, ML pipelines, and evaluation |
| `marketing` | Marketing | Skills and commands for ad creative, growth, and campaign workflows |
| `qa` | QA | Skills and commands for end-to-end testing (Aurie conversation drivers, regression sweeps, simulated user QA) |

## Installing

### Codex

Add the Aura marketplace once:

```
codex plugin marketplace add AuraHealth/AINative
```

Install `shared` for the org-wide skills, then install the plugin for your team:

```
codex plugin add shared@aura-tools
codex plugin add eng@aura-tools
```

Replace `eng` with the appropriate team plugin:

| Team | Install command |
|---|---|
| Engineering | `codex plugin add eng@aura-tools` |
| Product | `codex plugin add product@aura-tools` |
| Data | `codex plugin add data@aura-tools` |
| Marketing | `codex plugin add marketing@aura-tools` |
| QA | `codex plugin add qa@aura-tools` |

To install every Aura plugin:

```
codex plugin add shared@aura-tools
codex plugin add eng@aura-tools
codex plugin add product@aura-tools
codex plugin add data@aura-tools
codex plugin add marketing@aura-tools
codex plugin add qa@aura-tools
```

To refresh installed Aura plugins from GitHub after this repo changes:

```
codex plugin marketplace upgrade aura-tools
codex plugin add shared@aura-tools
codex plugin add eng@aura-tools
```

Re-run the `codex plugin add ...` command for each plugin you want refreshed.
After installing or refreshing plugins, start a new Codex thread so the updated
skills are loaded. Existing threads keep the skill list they started with.

### Claude Code

Add the Aura marketplace once:

```
/plugin marketplace add AuraHealth/AINative
```

Install `shared` for the org-wide skills, then install the plugin for your team:

```
/plugin install shared@aura-tools
/plugin install eng@aura-tools
```

#### Auto-install via repo settings

Add this to `.claude/settings.json` in any AuraHealth repo to automatically register the marketplace and enable plugins on `claude trust`:

```json
{
  "extraKnownMarketplaces": {
    "aura-tools": {
      "source": { "source": "github", "repo": "AuraHealth/AINative" }
    }
  },
  "enabledPlugins": {
    "shared@aura-tools": true,
    "eng@aura-tools": true
  }
}
```

Replace `eng` with the appropriate team plugin for each repo.

### Cursor

Team marketplaces are managed by admins via **Dashboard → Settings → Plugins → Team Marketplaces → Import**.

1. Paste `https://github.com/AuraHealth/AINative`
2. Set `shared` as **Required** — this auto-installs it for everyone
3. Set team plugins (`eng`, `product`, `data`, `marketing`, `qa`) as **Optional** so developers can install the one relevant to them

## Configuration (API keys & settings)

Most skills call external services and need credentials. These live in the **`env` block of `~/.claude/settings.json`** — the team standard (not shell rc, project `.env`, or Keychain). Claude Code loads `env` at **startup**, so after adding or changing a key you must **restart Claude Code**.

**Conventions**

- **MCP-first / soft requirements.** Most read-path skills try an MCP connector (Linear, Notion, Slack) first and only fall back to the API token when MCP can't do the job (bulk queries, pagination, admin-only endpoints). So a missing token is usually a *degraded mode*, not a hard fail — **install only the keys for the skills you actually run.** Exceptions (API-first by necessity, e.g. the OpenAI/ElevenLabs cost skills) are noted in `CLAUDE.md`.
- **Never commit secret values.** The table below lists variable *names* and where to obtain them — never paste actual keys into the repo. If a value leaks, rotate it immediately.
- **Onboarding:** run [`shared:setup-ainative`](plugins/shared/skills/setup-ainative/SKILL.md) to walk through the keys; use the [`update-config`](https://code.claude.com/docs/en/skills) skill to add one without clobbering existing entries.

| Variable | Used by (examples) | Self-serve? | Where to get |
|---|---|---|---|
| `OPENAI_ADMIN_API_KEY` | `analyze-openai-costs` + per-user/-state, `store-aurie-user-daily-cost` | **Team** (Owner role) | OpenAI → Settings → Organization → Admin keys (`sk-admin-…`) |
| `OPENAI_API_KEY` | `qa-prompt`, `setup-ad-creative-project` | Yes | <https://platform.openai.com/api-keys> (`sk-…`) |
| `ANTHROPIC_API_KEY` | `qa-prompt` (Claude-model prompts) | Yes | <https://console.anthropic.com/settings/keys> |
| `GEMINI_API_KEY` | Gemini creative skills, `qa-prompt` (Google models) | Yes | <https://aistudio.google.com/apikey> |
| `ELEVENLABS_ADMIN_API_KEY` | `analyze-elevenlabs-*`, `report-elevenlabs-*`, `store-aurie-user-daily-cost` | Yes | <https://elevenlabs.io/app/developers/api-keys> — scopes **Workspace Analytics: Access** + **User: Read** |
| `CARTESIA_ADMIN_API_KEY` | `analyze-cartesia-costs`, `store-aurie-user-daily-cost` + daily/weekly cost reports (Cartesia TTS usage) | Yes | <https://play.cartesia.ai/keys/admin> — **admin** key (`sk_car_admin_…`); a regular `sk_car_` key returns 401 on `/usage`. Sent as `Authorization: Bearer` + header `Cartesia-Version: 2026-03-01` |
| `DEEPGRAM_OWNER_API_KEY` | `analyze-deepgram-costs`, `store-aurie-user-daily-cost` + daily/weekly cost reports (Deepgram STT usage) | **Team** | Deepgram console → the **production** STT project → API Keys, with **usage:read** (Owner/Member) scope. Prod STT lives in project `7e42b984-…` (**“Aura Health”**) — the project the LiveKit voice agent authenticates against, which also holds the prepaid balance. Sent as `Authorization: Token`; falls back to `DEEPGRAM_API_KEY` |
| `AURA_API_TOKEN` | Aura backend skills (cost, latency, usage, user/subscription lookups) | **Team** | Ask `#ai-eng` (dev service token) |
| `AURA_USER_TOKEN` | `qa:chat-with-aurie` (user JWT for `/ai/dialogue/ask`) | Yes (rotates) | Sign in to Aura, copy the JWT from a network request |
| `LINEAR_API_KEY` | Every Linear-touching skill | Yes | <https://linear.app/aura-health/settings/account/security> |
| `NOTION_TOKEN` | All AI Knowledge skills (`retrieve-/record-/harvest-knowledge`, `sync-experiments`, …) | **Team** secret + user teamspace connect | `Aura AI Knowledge` integration (see CLAUDE.md → Notion REST integration) |
| `SLACK_BOT_TOKEN` | Skills that post to Slack | **Team** | `aura-ai` Slack app → Bot User OAuth Token |
| `SLACK_USER_TOKEN` | `daily-status` Slack activity search (`search.messages`) — *optional, personal* | Yes | `aura-ai` Slack app → **User** OAuth Token (add `search:read` user scope, reinstall as yourself). User-token-only; degrades to the bot token / Linear if absent |
| `MIXPANEL_SERVICE_ACCOUNT_USERNAME` / `_SECRET` / `_PROJECT_ID` | `profile-user`, `analyze-user-behavior`, `report-aurie-usage`, `store-aurie-user-daily-cost` (voice users) | Yes (project admin) | <https://mixpanel.com/project/963821/settings/service-accounts> (Analyst role) |
| `BRAINTRUST_API_KEY` | `update-intelligence-prompt`, `analyze-user-conversations`, … *(legacy — being migrated off, PROD-2456)* | Yes | <https://www.braintrust.dev/app/aurahealth/settings/api-keys> |
| `BUGSNAG_TOKEN` | `investigate-bugsnag-errors` | Yes | <https://app.bugsnag.com/settings/my-account/auth-tokens> |
| `CIRCLE_TOKEN` | `investigate-circleci`, `rerun-circleci` | Yes | CircleCI → User Settings → Personal API Tokens (<https://app.circleci.com/settings/user/tokens>). Must be a **personal** token — project tokens are rejected by the v2 workflow rerun API. Fallback names: `CIRCLECI_TOKEN`, `CIRCLE_CI_TOKEN`, `CIRCLECI_API_TOKEN` |
| `GCP_KEY` | `investigate-gcp-logs`, `analyze-gcp-logs` | **Team** | GCP service-account JSON (`roles/logging.viewer`) — ask `#ai-eng` |
| `POSTMAN_API_KEY` | Postman collection management — agent/operator edits to the **Aura Services** collection (no dedicated skill yet) | Yes (personal) | Postman → Settings → API keys (<https://go.postman.co/settings/me/api-keys>); needs workspace write access. Sent as `X-Api-Key` against `https://api.getpostman.com`. Edit one request at a time (`PUT /collections/{id}/requests/{requestId}`); see [`docs/token-inventory.md`](docs/token-inventory.md) |
| `STRIPE_API_KEY` | `analyze-payment-pending` | **Team** | Stripe dashboard → restricted key |
| `AURIE_COST_CSV` | `store-aurie-user-daily-cost` (store path — **not a secret**) | n/a (local path) | Absolute path to your canonical per-user cost CSV, e.g. `…/Aura/Data/AINative/per_user_daily_cost.csv` |
| `AURIE_UNIT_COST_CSV` | `store-aurie-user-daily-cost` (unit-cost store path — **not a secret**) | n/a (local path) | Absolute path to your daily unit-cost CSV, e.g. `…/Aura/Data/AINative/daily_unit_costs.csv` |
| `AURIE_KPI_CSV` | `store-aurie-user-daily-cost` (engineering-KPI store path — **not a secret**) | n/a (local path) | Absolute path to the 3-KPI CSV, e.g. `…/Aura/Data/AINative/daily_eng_kpis.csv` |

The **authoritative, always-current inventory** — with the MCP-fallback column, per-skill rationale, and the "API-first by necessity" exemption list — lives in [`CLAUDE.md` → "Secrets and environment" → "Token inventory"](./CLAUDE.md). When a new skill adds a token dependency, update both that table and `shared:setup-ainative`.

## Contributing

Each team plugin lives in `plugins/<team>/`. To add a skill:

1. Create `plugins/<team>/skills/<skill-name>/SKILL.md`
2. Add frontmatter with a `description` field and your instructions
3. Open a PR — changes go live as soon as the PR merges

To add a slash command:

1. Create `plugins/<team>/commands/<command-name>.md`
2. Add frontmatter with a `description` field and the step-by-step instructions
3. Open a PR

Org-wide automations that should be available to everyone go in `plugins/shared/`.

See [Claude Code plugin docs](https://code.claude.com/docs/en/plugins) and [Cursor plugin docs](https://cursor.com/docs/plugins.md) for full authoring guides.

## Structure

```
AINative/
├── .claude-plugin/
│   └── marketplace.json          # Claude Code marketplace catalog
├── .cursor-plugin/
│   └── marketplace.json          # Cursor marketplace catalog
└── plugins/
    ├── shared/                   # Org-wide — auto-installed for everyone
    │   ├── .claude-plugin/plugin.json
    │   ├── .cursor-plugin/plugin.json
    │   ├── skills/
    │   └── commands/
    ├── eng/
    ├── product/
    ├── data/
    ├── marketing/
    └── qa/
```
