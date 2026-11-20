---
name: analyze-cartesia-costs
description: >
  Cartesia (our TTS provider) spend and usage report — answers "how much are we spending on
  Cartesia?", "what's our Cartesia TTS cost this week?", "how many Cartesia credits have we
  burned?". Actuals only: credits come straight from the Cartesia admin usage API and are
  ground truth. TTS USD is DERIVED from a documented plan rate and clearly labelled as such —
  it is NOT a Cartesia invoice. Agent-calling USD is ACTUAL; it currently reads 0 because we
  use LiveKit for orchestration and Cartesia only for TTS, but it is included so the report
  stays correct if that changes. Output is a markdown report with a per-day table (credits,
  derived TTS $, agent minutes and calls, actual agent $). Default window last-7d; also
  yesterday, today, last-30d, or custom. Requires `CARTESIA_ADMIN_API_KEY` — a regular
  inference key returns 401 on /usage. Trigger on "analyze cartesia costs", "cartesia spend",
  "cartesia tts cost", "how much are we spending on cartesia", "voice tts cost cartesia",
  "cartesia credits", or "cartesia usage".

metadata:
  version: "3.0.2"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/Cartesia   # env-var name + usage API access pattern
    - Tools/Slack   # channel directory + Post format footer spec for #eng-budget post
---

# Analyze Cartesia Costs

Pulls actual usage from Cartesia's admin usage API and renders a markdown report
of **actuals only** — no end-of-period projection.

- **`GET /usage/credits`** — per-day TTS credit consumption (≈1 credit per
  synthesised character). This is the ground truth; the credit column is never
  estimated.
- **`GET /usage/agents`** — per-day Cartesia agent-calling usage, which returns
  `cents` directly, so `cents/100` is **actual** USD. We use LiveKit for voice
  orchestration and Cartesia only for TTS, so this is currently 0 — but included
  so the report stays correct if that changes.

### Why TTS $ is derived, not actual

The usage API has **no per-request USD figure for TTS** — only credits. So the
skill *derives* TTS dollars from the plan's effective per-credit rate:

```
TTS USD (derived) = credits / 1,000,000 × rate_per_million   (default 38.00)
```

The default 38.00 is the Cartesia **Scale** plan rate (\$38/1M, list). We are on
Scale (\$299/mo, 8M credits/mo) as of the LiveKit voice go-live; \$38/M is the
marginal/list rate and the real negotiated contract overage rate is still TBD.
This is a plan-rate
approximation for budgeting and trend-watching — **not** a Cartesia invoice
(the actual bill is the flat plan price plus any overage). Every report labels
the TTS dollar column **derived** so it isn't misread as a bill — same spirit as
`eng:analyze-elevenlabs-costs` hiding the retail-equivalent figure. Agent $, by
contrast, is **actual** because the API hands us cents directly. Full model,
plan-tier table, and rate override in [references/methodology.md](references/methodology.md).

## Step 0 — Check prerequisites

```bash
if [ -n "$CARTESIA_ADMIN_API_KEY" ]; then
  echo "OK: admin key set"
else
  echo "MISSING"
fi
```

If missing, the skill **cannot run**. Walk the user through setup:

1. **Use the admin key, not an inference key.** `/usage` endpoints require the
   admin key (`sk_car_admin_...`). A regular `sk_car_` inference key returns
   **401** on `/usage`. Create/obtain the admin key from the Cartesia dashboard
   (<https://play.cartesia.ai/>, organization/API-keys settings — admin scope).
2. Store it in **Claude global env only** — `~/.claude/settings.json` under the
   `env` block as `CARTESIA_ADMIN_API_KEY`. Hand off to the `update-config`
   skill to add it without clobbering existing entries. **Never** print the key,
   put it in a command line, or write it to a file.
3. The user must **restart Claude Code** for it to load — confirm before retrying.

Other prereqs: `python3` with `requests`. Install with
`pip3 install -r ${CLAUDE_PLUGIN_ROOT}/skills/analyze-cartesia-costs/scripts/requirements.txt`
(or `pip3 install requests`) if the script errors on import.

## Step 1 — Resolve the time window

**Default**: `last-7d`. Windows are **UTC**. Honour an explicit request:

- **Last 7 days** *(default)* — rolling 7d ending now
- **Yesterday** — the previous full UTC calendar day
- **Today** — UTC midnight → now
- **Last 30 days** — rolling 30d ending now
- **Custom range** — `--since` / `--until`, each `YYYY-MM-DD` (UTC midnight) or a
  full RFC3339 string, half-open

If the user names a month ("June", "2026-06"), translate it to a `--since` /
`--until` pair covering that full calendar month.

## Step 2 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-cartesia-costs/scripts/report.py \
  --window last-7d \
  --json /tmp/cartesia_costs.json
```

### Other forms

```bash
# Yesterday
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-cartesia-costs/scripts/report.py --window yesterday

# Rolling 30 days
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-cartesia-costs/scripts/report.py --window last-30d

# Custom range
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-cartesia-costs/scripts/report.py \
  --since 2026-06-01 --until 2026-07-01

# Override the plan rate used to derive TTS $ (e.g. model a move to Scale: \$38/1M overage)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-cartesia-costs/scripts/report.py \
  --window last-30d --rate-per-million 38.00
```

### Flags

- `--window {yesterday,today,last-7d,last-30d}` — default `last-7d`.
- `--since` / `--until` — custom UTC range (`YYYY-MM-DD` or RFC3339). Use together.
- `--rate-per-million <usd>` — USD per 1,000,000 credits used to **derive** TTS $
  (default 38.00, the Scale-plan rate). See the plan-tier table in
  `references/methodology.md`.
- `--json <path>` — write the full payload (actual credits **and** derived TTS $,
  actual agent $, per-day rows, and the raw API `data` arrays).

The script prints a markdown report on stdout, progress lines (`# auth`,
`# query`, `# fetched`) on stderr, and — with `--json` — writes structured data.

## Step 3 — Show the report inline

Show the script's stdout directly. Lead with the one-line headline pairing the
actual credits, the derived TTS $, and the actual agent $, e.g.:

> _"Cartesia burned **3.18M TTS credits** in the last 30 days → **\$120.84** derived at the \$38/M Scale rate. Agent calling: 1.7 min / 3 calls → **\$0.12** actual (we run voice on LiveKit, so Cartesia agent spend is near-zero)."_

Always keep the TTS $ labelled as **derived** and the agent $ as **actual** —
never present the derived TTS figure as a bill.

## Step 4 — Ask where to route it

After the report, ask the user what to do next:

1. **Post a summary to Slack** *(optional)* — compact message with the headline +
   per-day highlights, then **invoke [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)**
   to send it to `#eng-budget` (`C05PC18APLH`, canonical ID in `Tools/Slack`) with
   `--footer eng:analyze-cartesia-costs --footer-extra prod` (it stamps the versioned
   footer; don't hand-type one). That skill owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback)
   and the unfurl-off default — don't call Slack directly here. If it reports
   neither Slack MCP nor a token is available, tell the user and skip — don't
   treat Slack as a prerequisite.
2. **Save a Markdown summary** to a path the user provides.
3. **Nothing, just show it** — stop here.

Do NOT route automatically; always wait for the user's pick.

## Notes & edge cases

- **Admin key required.** Only `sk_car_admin_...` in `CARTESIA_ADMIN_API_KEY`
  works on `/usage`; a regular `sk_car_` inference key returns 401. The script
  detects the 401 and prints a targeted hint.
- **Credits are truth; TTS $ is derived.** The credit column is exactly what
  Cartesia recorded. The TTS dollar column is `credits × plan rate` — a
  budgeting approximation, not an invoice. Override the rate with
  `--rate-per-million` when the plan changes.
- **Agent $ is actual but usually 0.** We orchestrate voice on LiveKit and use
  Cartesia only for TTS, so `/usage/agents` returns 0 cents most days. It's kept
  in the report so any future agent-calling spend shows up immediately.
- **UTC windows.** All windows anchor to UTC, matching the API's day buckets.
  Mention this if the user compares to a Pacific-anchored figure from another
  tool.
- **No projection.** Every number reflects what has already happened — no
  end-of-period extrapolation.

See [references/methodology.md](references/methodology.md) for the credit model,
plan-tier table, and the actual-vs-derived rationale.
