---
name: chart-openai-project-cost
description: >
  Given an OpenAI project (by name or project_id), produce daily, weekly, and monthly cost
  charts for that project alone — a self-contained HTML report with a daily bar chart, a
  weekly (Monday-anchored) rollup, a monthly (calendar-month) rollup, and key tiles (total,
  avg/day, peak day, peak week, peak month). Trigger when the user says: 'chart openai project
  cost', 'daily cost chart for {project}', 'weekly cost chart for {project}', 'monthly cost
  chart for {project}', 'openai project spend chart', 'cost over time for an openai project',
  'cost by month', 'monthly cost', 'chart the Insights project cost', 'how much is the Memory
  project spending, charted', or asks for a visual/chart breakdown of one OpenAI project's
  spend over time. Distinct from `eng:analyze-openai-costs`, which reports spend across ALL
  projects for one window as a markdown table — this skill charts ONE project's cost over
  time. Project-level aggregate $ only (no per-user, per-key, or per-model breakdown, no
  UID/PII).

metadata:
  version: "3.0.2"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/OpenAI                   # OpenAI project keys + stored-completions access
---

# Chart OpenAI Project Cost

Given a single OpenAI project, pulls its daily spend from the [OpenAI Costs API](https://platform.openai.com/docs/api-reference/usage/costs), aggregates it into daily + weekly (Monday-anchored, ISO) + monthly (calendar-month) series, and renders a self-contained HTML report with three hand-built inline SVG bar charts.

Sibling to `eng:analyze-openai-costs` (which reports spend across the whole org, all projects, for one window as a markdown table) — this skill instead charts **one project's cost over time**, day by day, week by week, and month by month.

## Step 0 — Check prerequisites

```bash
test -n "$OPENAI_ADMIN_API_KEY" && echo "OK: admin key set" || echo "MISSING"
```

**API-first by necessity** — this skill leads with `OPENAI_ADMIN_API_KEY` and skips any MCP attempt. There is no MCP for OpenAI's Admin-scoped `/v1/organization/*` endpoints (`/organization/costs`, `/organization/projects`), so a missing key is a hard fail, not a degrade.

If missing, walk the user through setup:

1. The key must be an **Admin API key** (`sk-admin-...`), not a regular `OPENAI_API_KEY`.
2. Create one at <https://platform.openai.com/settings/organization/admin-keys> — requires Owner role on the OpenAI org.
3. Store it in `~/.claude/settings.json` under the `env` block (hand off to `update-config` if needed).
4. Restart Claude Code for it to load.

Other prereqs: `python3` with `requests`. If the default `python3` errors on import, use the framework interpreter (`/Library/Frameworks/Python.framework/Versions/3.12/bin/python3` on macOS) or `pip3 install requests`.

## Step 1 — Resolve the project + window

Ask which project (if not named) and which window (default: all history).

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chart-openai-project-cost/scripts/costs.py \
  --project "Insights [Production]" --all \
  --json /tmp/openai_project_cost.json
```

Other window forms:

```bash
# Rolling last N days
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chart-openai-project-cost/scripts/costs.py \
  --project "Insights [Production]" --last 30 --json /tmp/openai_project_cost.json

# Custom range (UTC, half-open — --until is exclusive)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chart-openai-project-cost/scripts/costs.py \
  --project "Insights [Production]" --since 2026-06-01 --until 2026-07-01 \
  --json /tmp/openai_project_cost.json
```

**Project matching** (against `/v1/organization/projects`, active + archived): exact name match (case-insensitive) → unique case-insensitive substring match → exact `project_id` → error. A project name given loosely (e.g. "Insights" when both `Insights [Development]` and `Insights [Production]` exist) errors with the list of matches so the user can disambiguate — prefer the `[Production]` variant unless the user asks for dev. Zero matches prints every known project name + id.

The script prints the JSON payload to stdout and (with `--json`) writes it to a file: `meta` (project, project_id, resolved window), `daily` (gap-filled from the first day with spend to the last day in the window — a project with zero spend across the whole window returns empty arrays), `weekly` (Monday-anchored, each entry flags `partial: true` when fewer than 7 days of the week fall inside the window — always true for the most recent week unless it happens to end exactly on a Sunday), `monthly` (calendar-month, each entry flags `partial: true` when fewer than the full calendar month's days are present in the window — true for the first month when leading zero days were trimmed and for the current month in progress), and `total`.

## Step 2 — Render the HTML report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chart-openai-project-cost/scripts/render.py \
  --data /tmp/openai_project_cost.json \
  --out /tmp/openai_project_cost.html \
  --title "Insights — OpenAI cost"
```

`render.py` is stdlib-only (no sibling-skill imports, no CDN/external `<script src>` — Artifacts enforce a strict CSP) and writes one self-contained HTML file: a tile row (total / avg-per-day / peak day / peak week / peak month) plus three inline SVG bar charts (daily, weekly, monthly). Partial weeks and partial months render with a hatched/lighter fill and dashed outline so they're visually distinguishable from full periods. Theme-aware — matches the `eng:build-aurie-cost-report` house style (light/dark via `@media (prefers-color-scheme)` + `:root[data-theme]` overrides). The report **footer stamps the skill version** — `eng:chart-openai-project-cost:<version>`, read from this skill's `metadata.version` (see `/CLAUDE.md` → "Skill versioning") — so a published artifact is traceable to the exact revision that produced it.

## Step 3 — Publish

Publish the rendered HTML via the **Artifact** tool (do not just print the file path — the user wants to see the charts). Pick a favicon and title that reflect the project being charted.

## Notes & edge cases

- **Numbers may lag the OpenAI dashboard by up to 24h** — the Costs API aggregates daily buckets and the most recent bucket isn't always closed yet. The last day/week in the report is typically partial for this reason, independent of the `partial` flag on weeks (which only measures day-count-within-window, not bucket-closedness).
- **Currency.** Amounts are USD per the Costs API; the script does not attempt multi-currency formatting.
- **Aggregate only.** No per-user, per-API-key, or per-model breakdown — for those, use `eng:analyze-openai-costs` (org-wide, one window) or `eng:analyze-openai-cost-per-user` / `eng:analyze-openai-tokens`.
- **Cowork sandboxes** strip host env (PROD-1305), so `OPENAI_ADMIN_API_KEY` won't reach `mcp__workspace__bash` automatically. In Cowork, the user must paste-export the value at the start of the session (see CLAUDE.md → "Known Cowork constraints"). There is no MCP fallback for this skill.
- **Rate limits.** The Costs API caps at 60 req/min for the org; `costs.py` paginates and respects 429 with backoff.
