---
name: analyze-deepgram-costs
description: >
  Project-level Deepgram STT spend report — answers "how much are we spending on Deepgram?",
  "what's our STT cost?", "how many audio hours did nova-3 process?", "what's our Deepgram
  balance?". Deepgram is our speech-to-text provider (nova-3, streaming) on the LiveKit voice
  path. Actuals only — every number reflects what already happened, no projection. Prefers
  ACTUAL dollars from the billing breakdown and prepaid balance where the API exposes them;
  otherwise reports actual audio hours and request counts and DERIVES USD from a documented
  per-hour rate, clearly labelled as derived. Requires `DEEPGRAM_OWNER_API_KEY` (falls back to
  `DEEPGRAM_API_KEY`). Output is a markdown report; optionally route a summary to Slack
  #eng-budget. Trigger on "analyze deepgram costs", "deepgram spend", "deepgram stt cost",
  "how much are we spending on deepgram", "stt cost", "deepgram usage", "deepgram balance",
  "speech-to-text cost", "nova-3 cost", "weekly deepgram cost", or "deepgram cost breakdown".

metadata:
  version: "3.0.2"
knowledge:
  reads:
    - Guide/Index    # baseline routing aid (universal)
    - Tools/Deepgram # provider context — STT provider, env var, access pattern
    - Tools/Slack    # channel directory + Post format footer spec for #eng-budget post
---

# Analyze Deepgram Costs

Reports Deepgram STT usage and spend over a window from the Deepgram management
API, then renders a markdown report of **actuals only** — no end-of-period
projection.

Deepgram is our speech-to-text provider. We run **nova-3** in real-time
(streaming) mode on the **LiveKit voice path** (the Aurie voice agent). There is
no Deepgram MCP — this skill is API-first.

By default the report shows:

- **Prepaid balance** — actual USD from `/v1/projects/{id}/balances`.
- **Usage** — actual audio hours + request counts for the window (from
  `/v1/projects/{id}/usage`), plus a per-model / per-feature breakdown (from
  `/usage/breakdown`).
- **Spend this window** — **actual** USD from `/v1/projects/{id}/billing/breakdown`
  when that endpoint exposes per-period dollars; otherwise **derived** USD
  (`hours × --rate-per-hour`), clearly labelled as an estimate.

### Why actuals only

Same philosophy as the sibling `eng:analyze-elevenlabs-costs`: every number on
the page reflects what has already happened. No straight-line burn projection
(guesses about future usage), no converting a projected figure into dollars.
When you need a forward signal, read the hrs/day run-rate the report prints, or
run the skill repeatedly through the period.

### Actual vs derived USD

The skill leads with the **actuals** (hours, requests, prepaid balance) and
treats derived USD as **secondary**:

1. If `billing/breakdown` returns per-period dollars, that's the **actual**
   spend — the report labels it "actual, from billing breakdown".
2. Otherwise USD is **derived** = `total_hours × --rate-per-hour`, and a caveat
   line says so. The prepaid balance is the only actual $ figure in that case.

The per-hour rate defaults to **\$0.348/hr** (Nova-3 **Multilingual** streaming
PAYG — our current model — \$0.0058/min, from <https://deepgram.com/pricing> on 2026-07-02). It's an
assumption used only to derive USD when actual $ is unavailable — it never
overrides a billed figure. See [references/methodology.md](references/methodology.md)
for the rate source, alternatives (multilingual / pre-recorded tiers), and the
project-scoping caveat.

## Step 0 — Check prerequisites

```bash
if [ -n "$DEEPGRAM_OWNER_API_KEY" ]; then
  echo "OK: owner key set"
elif [ -n "$DEEPGRAM_API_KEY" ]; then
  echo "OK: falling back to DEEPGRAM_API_KEY"
else
  echo "MISSING"
fi
```

If both are missing the skill **cannot run**. Walk the user through setup:

1. Create a **Deepgram management API key** with `usage:read` (and `billing:read`
   if you want the actual-$ billing breakdown) on the **project you want to
   report on**, at <https://console.deepgram.com/> → project → **Settings → API
   Keys**.
2. Store it in **Claude global env only** — `~/.claude/settings.json` under the
   `env` block as `DEEPGRAM_OWNER_API_KEY`. Hand off to the `update-config` skill
   to add it without clobbering existing entries.
3. The user must **restart Claude Code** for it to load — confirm before retrying.

**Project-scoping caveat (read this).** A management key only sees projects it
is a **member of**. `GET /v1/projects` lists those; the skill resolves the
project from that list (or from `--project-id`). If the key is a member of
exactly one project, it's used automatically; if it sees several, pass
`--project-id`. Hitting a project the key isn't a member of returns 404
`NOT_FOUND` / 403 `INSUFFICIENT_PERMISSIONS` — the skill prints a clear message
(a usage:read key on that project is needed) and exits cleanly rather than
crashing.

The Aura **PRODUCTION** STT project is **`7e42b984-d5d4-4cdb-aba3-b5bd8408933f`
('Aura Health')** — the project the LiveKit voice agent authenticates against
(its `LivekitVoiceAgent/.env` `DEEPGRAM_API_KEY` is a member of it). The
`DEEPGRAM_OWNER_API_KEY` in settings is an Owner key in that same project and is
a member of only it, so the skill auto-selects the project — **no `--project-id`
needed**. Usage is small during early voice rollout (~2–3 audio hrs/week); the
prepaid balance (~\$188) lives in this project too.

Other prereqs: `python3` with `requests`. Install with `pip3 install requests`
(or `pip3 install -r scripts/requirements.txt`) if the script errors on import.

## Step 1 — Resolve the window

Default is **last 7 days**. Windows are UTC-calendar-anchored:

- `--window last-7d` *(default)* — rolling 7 days ending today
- `--window last-30d` — rolling 30 days
- `--window yesterday` — the prior UTC day
- `--window today` — the current UTC day so far
- `--since YYYY-MM-DD --until YYYY-MM-DD` — explicit range (`--until` inclusive)

If the user names a month directly ("June", "2026-06"), translate it to a
`--since` / `--until` pair covering that full calendar month.

## Step 2 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-deepgram-costs/scripts/report.py \
  --window last-7d \
  --json /tmp/deepgram_costs.json
```

Single-window and range forms:

```bash
# Last 30 days
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-deepgram-costs/scripts/report.py --window last-30d

# Yesterday
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-deepgram-costs/scripts/report.py --window yesterday

# Explicit range
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-deepgram-costs/scripts/report.py \
  --since 2026-06-01 --until 2026-06-30

# A specific project (only needed if the key ever sees several projects)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-deepgram-costs/scripts/report.py \
  --project-id 7e42b984-d5d4-4cdb-aba3-b5bd8408933f --window last-30d

# Override the derived-USD rate (e.g. model monolingual \$0.288/hr, or Growth \$0.300/hr)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-deepgram-costs/scripts/report.py \
  --window last-30d --rate-per-hour 0.288
```

### Flags

- `--window {yesterday,today,last-7d,last-30d}` — default `last-7d`.
- `--since` / `--until` — explicit UTC date range (`--until` inclusive).
- `--project-id <uuid>` — override auto-resolution from `/v1/projects`.
- `--rate-per-hour <float>` — USD/audio-hour for **deriving** spend when actual $
  is unavailable. Default `0.348` (Nova-3 Multilingual streaming PAYG, current). See
  methodology.md for alternatives.
- `--json <path>` — write structured JSON alongside the markdown (includes
  `usd_is_derived`, `derived_usd`, `actual_usd`, `prepaid_balance_usd`,
  per-day/per-model rows, and the raw API responses).

The script prints stderr progress lines (`# auth`, `# query <path>`,
`# fetched ...`) and the markdown report on stdout.

## Step 3 — Show the report inline

Show the script's stdout directly. Lead with a one-line headline, e.g.:

> _"Deepgram processed **312.4 audio hours** across **48,102 requests** last 7
> days (~44.6 hrs/day) — nova-3 is 100% of volume. Prepaid balance is **\$188.67**.
> Spend ~**\$108.72** (derived at \$0.348/hr Nova-3 Multilingual — Deepgram's billing breakdown didn't
> expose per-period dollars for this window)."_

Rules for the headline:

- **Lead with actuals** — hours, requests, prepaid balance. Only quote a USD
  spend figure with its label: "actual" if from the billing breakdown, "derived"
  otherwise.
- If the reachable project has **zero usage**, say so plainly and surface the
  production-project key gap (the report already prints this note): the personal
  project the key can see has no traffic; production STT is in a separate project
  requiring a usage:read key.
- Never present a derived USD figure as if it were the bill.

## Step 4 — Ask where to route it

After the report, ask the user what to do next:

1. **Post a summary to Slack** *(optional)* — compact message with the usage
   headline + balance + spend (labelled actual/derived), then **invoke
   [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** to
   send it to `#eng-budget` (`C05PC18APLH`, canonical ID in `Tools/Slack`); that
   skill owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback) and
   the unfurl-off default; pass `--footer eng:analyze-deepgram-costs --footer-extra prod`
   so it stamps the versioned footer (don't hand-type one). Don't call Slack
   directly here. If it reports neither
   Slack MCP nor a token is available, tell the user and skip.
2. **Save a Markdown summary** to a path the user provides.
3. **Nothing, just show it** — stop here.

Do NOT route automatically; always wait for the user's pick.

## Slack message format (option 1)

```markdown
**Deepgram STT Spend — <window>** (actuals only)

- **Project:** <project name>
- **Usage:** <hours> audio hrs · <requests> requests (~<hrs/day>/day)
- **Prepaid balance:** $<balance> (actual)
- **Spend:** $<spend> (<actual, from billing breakdown | derived at $<rate>/hr>)

**By model / feature**

| Model / feature | Hours | Requests |
|---|---:|---:|
| nova-3 | <n> | <n> |
| ...top 5... |

_Caveat: actuals only — no projection. USD is <actual | derived at $<rate>/hr>._

Deepgram console: https://console.deepgram.com/
```

Notes:

- Send via **[`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)**
  with channel `#eng-budget` (`C05PC18APLH`) and `--footer eng:analyze-deepgram-costs
  --footer-extra prod` — the helper stamps the versioned footer; don't put one in the body.
- Cap the model/feature table at 5 rows.
- If the window has no usage, post the zero-usage note + production-project gap
  instead of a table.

## Notes & edge cases

- **Production project.** `DEEPGRAM_OWNER_API_KEY` is an Owner key in
  `7e42b984-d5d4-4cdb-aba3-b5bd8408933f` ('Aura Health'), the project the LiveKit
  voice agent uses for STT. It's a member of only that project, so the skill
  auto-selects it (no `--project-id`). Usage is small during early rollout (~2–3
  audio hrs/week) but real; the prepaid balance shows actual $.
- **Unreachable project.** Passing `--project-id` for a project the key isn't a
  member of returns 404 `NOT_FOUND` / 403 `INSUFFICIENT_PERMISSIONS`; the skill
  prints a clear message and exits non-zero — it does not crash.
- **Actual vs derived USD.** Actual $ comes from `billing/breakdown` (per period)
  and `balances` (prepaid). When the billing breakdown is empty for a project /
  window, USD is derived from hours × rate and labelled as such. The `--json`
  payload carries `usd_is_derived` so downstream tooling can tell them apart.
- **Rate is our current PAYG rate.** The \$0.348/hr default is Nova-3 **Multilingual**
  streaming on-demand (deepgram.com/pricing, 2026-07-02) — our current model. Monolingual
  (\$0.288/hr, ~17% less) and Growth (\$0.300/hr multilingual) are cost-reduction levers, not
  applied by default. Override with `--rate-per-hour`. See
  [references/methodology.md](references/methodology.md).
- **No projection.** Every number reflects what has already happened.
- **API-first.** No Deepgram MCP exists — the skill leads with the management
  API key.

See [references/methodology.md](references/methodology.md) for endpoints, auth,
the project-scoping caveat, and the rate default with its source + date.
