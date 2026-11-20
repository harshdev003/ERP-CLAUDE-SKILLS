---
name: fetch-conversation-topics
description: >
  Generate a Conversation Topics report (Task / Sentiment / Issues) for the Aura Health
  Conversation project over a chosen time window, then optionally route the result to Slack,
  AI Knowledge, or another skill. Data comes from Braintrust's server-side BERTopic
  classifications. Trigger when the user says: 'conversation topics', 'conversation topics
  report', 'what are users talking to Aurie about', 'fetch conversation topics', 'braintrust
  topics', 'fetch braintrust topics', 'sentiment breakdown', 'sentiment breakdown from
  braintrust', 'issues flagged in logs', or asks for the numbers shown in the Braintrust UI
  Topics view.

metadata:
  version: "3.0.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Signals/Conversation Topics   # latest snapshot, for idempotency check before writing
    - Tools/Braintrust              # auth + access pattern for the Braintrust API
    - Tools/Slack                   # canonical Channel IDs (for skills) — resolves #ai-prompt-engineering for the optional Step 4 post
  writes:
    - Signals/Conversation Topics   # structured snapshot row via shared:record-knowledge
---

# Conversation Topics Report

Pulls the server-side Topics classifications Braintrust already computes for the `Conversation` project in org `Aura Health 1` and aggregates them by `Task`, `Sentiment`, and `Issues` with percentages + trace counts — matching the Braintrust UI Topics view exactly.

Source data lives on `project_logs.classifications.<Facet>[0].label` (BERTopic clustering, stored per row). No local clustering or LLM is needed — just a `GROUP BY`.

## Step 0 — Check prerequisites

Before anything else, confirm the Braintrust API key is available. Run:

```bash
test -n "$BRAINTRUST_API_KEY" && echo "OK: key set" || echo "MISSING"
```

If missing, help the user set it up. Do NOT proceed until a key is available.

1. Tell the user: the key is called `BRAINTRUST_API_KEY` and the skill reads it from env.
2. Point them at https://www.braintrust.dev/app/settings/api-keys to create one, scoped to `Aura Health 1`.
3. Store the key in the **Claude global env only** — `~/.claude/settings.json` under the `env` block. Hand off to the `update-config` skill to add `BRAINTRUST_API_KEY` there without clobbering existing entries. Do not suggest shell rc, Keychain, or project `.env` alternatives — Claude env is the team standard for this skill.
4. After the key is added, the user must **restart Claude Code** for it to load — confirm this before retrying.

Other prereqs (usually already present): `python3` with `requests`. Install with `pip3 install requests` if the script errors on import.

## Step 1 — Ask for the time window

Ask the user which window they want, offering these presets. Pick one; only one.

- **Last 24 hours** (`--days 1`)
- **Last 7 days** (`--days 7`, default)
- **Last 30 days** (`--days 30`)
- **Current month** — from the 1st of this calendar month to now
- **Last month** — full previous calendar month
- **Custom `--days N`** — any integer
- **Custom date range** — explicit ISO `since` / `until`

For `current month` / `last month` / custom date range, compute ISO timestamps locally and pass `--since` / `--until` to the script. For everything else, use `--days`.

## Step 2 — Run the report

From the repo root:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/fetch-conversation-topics/scripts/report.py --days 7 --json /tmp/bt_report.json
```

For an explicit range:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/fetch-conversation-topics/scripts/report.py \
  --since 2026-04-01T00:00:00Z --until 2026-05-01T00:00:00Z \
  --json /tmp/bt_report.json
```

The script prints a formatted table and (with `--json`) writes structured data.

## Step 3 — Show the report inline

Show the table output directly in the response so the user can see it immediately. Lead with a one-line headline: top Task topic + its share (e.g. "41% of conversations this week are about relationship and emotional safety concerns").

## Step 4 — Ask where to route it

After the report, ALWAYS ask the user what to do next. Use AskUserQuestion with these options:

1. **Post to Slack `#ai-prompt-engineering`** *(optional)* — format a compact message (headline + top 5 topics per facet + a link back to the Braintrust UI; include the time window), then **invoke [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** to send it to `#ai-prompt-engineering` (`C07EX6CL43A`) with `--footer data:fetch-conversation-topics --footer-extra prod` so it stamps the versioned footer (don't hand-type one in the body). That skill owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback), the unfurl-off default, and channel discipline — don't call Slack directly here. If it reports neither Slack MCP nor a token is available, tell the user and skip — don't treat Slack as a prerequisite.
2. **File snapshot to `Signals/Conversation Topics`** — invoke [`shared:record-knowledge`](../../../shared/skills/record-knowledge/SKILL.md) with `path=Signals/Conversation Topics` and a `fields` object built from the report's `--json` output. `record-knowledge` validates the payload against the [Conversation Topics schema](../../../shared/skills/record-knowledge/references/schemas/conversation-topics.md), refuses the write if a snapshot for the same date already exists, and returns the new page URL. Field mapping is in Step 4a below. (No `ingest-knowledge` hand-off — this is a structured row, not a freeform page.)
3. **Save a Markdown summary** to a path the user provides.
4. **Nothing, just show it** — stop here.

Do NOT route automatically; always wait for the user's pick.

## Step 4a — record-knowledge field mapping (option 2 only)

Build the `fields` object from the report's `--json` output (written to `/tmp/bt_report.json` in Step 2) plus the run metadata. The script's JSON has shape `{ since, until, facets: { Task: [{topic, traces}, ...], Sentiment: [...], Issues: [...] } }`.

- `producer` → `"Braintrust Topics"`
- `snapshot_date` → the run's `--until` date if the user specified an explicit range, otherwise today in the user's local timezone (`YYYY-MM-DD`)
- `window` →
  - `queried_since` → `report.since` (ISO datetime)
  - `effective_window` → string like `"last 7 days (since 2026-04-21T00:00:00Z)"` matching the `window_label` the script prints — Braintrust classifications populate only the last ~7 days of logs, so note that explicitly if the query window is wider
  - `pulled_on` → today (`YYYY-MM-DD`)
- `headline` → one paragraph referencing the top Task topic + percentage and top Sentiment topic + percentage. Add the top Issue topic + percentage **only if the Issues facet has topics**; when `issue_topics` is empty (the common case — most conversations are `no_match`), write "no issues flagged this window" instead. Never invent an Issue stat to fill the slot. Compute percentages as `100 * traces / sum(traces)` per facet.
- `task_topics`, `sentiment_topics`, `issue_topics` → lists of `{ pct, traces, topic }` rows. The script returns `{ topic, traces }` per facet; compute `pct` per row against the facet's total.
- `sources` → `[ { label: "Braintrust Conversation project — Topics view", url: "https://www.braintrust.dev/app/Aura%20Health%201/p/Conversation/logs?v=Review+-+All&qs=topics", primary: true } ]`

**Idempotency.** Before invoking `record-knowledge`, check the existing `Signals/Conversation Topics` snapshots (via `shared:retrieve-knowledge` or by listing child pages of the parent) for one with the same title. If one exists, stop and tell the user — `record-knowledge` will refuse the write anyway (snapshots are immutable), but surfacing the duplicate up front is a better UX than catching the refusal.

**On success.** Report back the new snapshot page URL to the user. On failure (Notion down, validation error), surface the error — do not fall back to the old `ingest-knowledge` path.

## Slack message format (option 1)

```
*Conversation Topics — <window>*
<headline sentence>

*Task* (N topics, X traces)
  • 41.3% (1,842) Process relationship and emotional safety concerns
  • 24.3% (1,083) Seek emotional support for chronic struggles
  • ...top 5...

*Sentiment* (...)
  • ...top 5...

*Issues* (...)
  • ...all (usually small)...

<https://www.braintrust.dev/app/Aura%20Health%201/p/Conversation/logs?v=Review+-+All&qs=topics|Open in Braintrust>
```

Send it by **invoking [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** with this message and channel `#ai-prompt-engineering` (`C07EX6CL43A`) plus `--footer data:fetch-conversation-topics --footer-extra prod` — the helper stamps the versioned footer. Don't put a footer in the message body.

## Notes

- Numbers may drift by a few traces from the UI if new logs land between screenshot and query — that's expected.
- The `Issues` facet is usually small (dozens, not thousands) because most conversations have `Issues = no_match`. Highlight these even when counts are low — they're the actionable quality signals.
- If the user asks for facets beyond Task/Sentiment/Issues, they'd need to be configured in the Braintrust project first (custom facets with preprocessor + extraction prompt).
