---
name: monitor-openai-changelog
description: >
  Watches the OpenAI API changelog and posts to #ai-prompt-engineering only the new entries that
  could affect Aura: prompt caching changes (retention, cached-input pricing, cache hits) or any
  entry naming a model IntelligenceService's live config uses, read from GET /ai/openai/models. A chat-latest
  entry gets an alert header, because an unannounced chat-latest snapshot update on 2026-10-07
  halved coach's prompt-cache hit rate (PROD-7290). One deterministic script fetches, filters,
  posts as the aura_ai bot and records what it has seen; when nothing relevant is new it posts
  nothing. Trigger on "check the OpenAI changelog", "any OpenAI API news", "did chat-latest
  change", "monitor the OpenAI changelog", "run the changelog monitor", or the
  openai-changelog-monitor scheduled task. Do NOT use for Aura's own CHANGELOG.md
  (eng:update-changelog) or OpenAI cost reports (eng:report-openai-costs).
metadata:
  version: "1.1.0"
knowledge:
  reads:
    - Guide/Index     # baseline routing aid (universal)
    - Tools/OpenAI    # the changelog is the first thing to check when OpenAI behaviour shifts
    - Tools/Aura API  # GET /ai/openai/models with AURA_API_TOKEN for the model list
    - Tools/Slack     # bot-token posting to #ai-prompt-engineering
---

# monitor-openai-changelog

You check the [OpenAI API changelog](https://developers.openai.com/api/docs/changelog) for news
that could change Aura's cost or behaviour, and post it to **#ai-prompt-engineering**. The script
does all of it. Your job is to run it once and report the result.

Why it exists: OpenAI updated the `chat-latest` snapshot on 2026-10-07. Coach's prompt-cache hit
rate fell from ~89% to ~52%, and the changelog entry that explained it was found two weeks later.

## Run

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/monitor-openai-changelog/scripts/monitor_changelog.py
```

It prints one JSON line. Report it in one line:

| `status` | Meaning | Report |
|---|---|---|
| `seeded` | First run on this machine: every current entry recorded, nothing posted | "seeded N entries" |
| `unchanged` | Nothing new about caching or our models | "unchanged" |
| `posted` | Posted the new entries to Slack | "posted N entries (ts …)" |
| `error` | Fetch, parse or Slack failed; a ⚠️ warning was posted when Slack worked | the `error` text |

Don't post to Slack yourself, and don't edit the state file. On `error` the entries stay unseen,
so the next run retries.

## What counts as relevant

An entry is posted when either is true:

- **It mentions prompt caching:** prompt caching, cache retention, cached input, cache hits.
- **It names, exactly, a model IntelligenceService's live config uses.** The list comes from
  `GET /ai/openai/models` on `services.aurahealth.io` (ENGBACK-1656; also what the LiveKit LLM
  prewarm calls), authenticated with `AURA_API_TOKEN`, so it is what production runs, not a list
  kept here. `gpt-5.2` matches `gpt-5.2`, not `gpt-5.2-codex`. If the list can't be read the run is
  an `error`: there is no fallback list, because a stale one would hide news about a new model.

A `chat-latest` entry gets a 🚨 header pointing to PROD-7290. Others get 📰.

## Options

| Flag | Use |
|---|---|
| `--dry-run` | Print what would be posted, including the message; post and write nothing |
| `--state PATH` | Seen-entries file. Default `~/.claude/monitors/openai-changelog/seen.json`, outside the plugin because an update replaces the install directory |
| `--api-base URL` | Aura services base URL for the model list (default `$AURA_BASE_URL` or production) |
| `--models a,b` | Use these model ids instead of asking the API (tests) |
| `--html-file PATH` | Parse a saved page instead of fetching (tests) |

## Notes

- **The first run only seeds.** Without a state file, it records every entry and posts nothing, so
  a new machine doesn't post the changelog's whole history.
- **A layout change is an error, not silence.** Fewer than 10 parsed entries means the page
  changed shape; the run fails with a warning rather than reporting `unchanged` forever.
- **Tokens:** `AURA_API_TOKEN` (model list) and `SLACK_BOT_TOKEN` (post) from the environment.
  Posts go through the local copy of the canonical `slack_post.py`, which adds the Tools/Slack
  footer. The `aura_ai` bot must be in #ai-prompt-engineering (`C07EX6CL43A`), which is private.
- **Transport:** HTTP goes through `curl`, because stock macOS Python's `urllib` can't find the
  system CA bundle.
- **Scheduled task:** `openai-changelog-monitor`, daily at 9:01 AM local. It runs only while the
  Claude app is open.
- Tests: `tests/test_monitor_openai_changelog.py`, against a verbatim excerpt of the page.
