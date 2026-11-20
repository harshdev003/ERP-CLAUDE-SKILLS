---
name: read-slack
description: >
  Canonical "how to read Slack" layer for AuraHealth — the one place the history-read
  mechanics live (windowed `conversations.history` with cursor pagination, single-message
  fetch by ts, `conversations.replies` thread fetch, over a shared 429-retry transport).
  Companion to `shared:post-to-slack`. Use it two ways: (1) directly, when the user wants to
  read Slack — "read the last messages from #channel", "pull the history of #ai-feedback",
  "fetch this Slack thread", "get the replies under that message", "sweep
  #ai-data-intelligence for the last week", "what was posted in #growth recently"; and (2) as
  a library, when another skill needs channel history — "consolidate the Slack read code",
  "use the shared Slack read helper". Do NOT use to POST to Slack, or to SEARCH the workspace
  by query across channels (both `shared:post-to-slack`), or to author the daily standup
  (`shared:daily-status`).

metadata:
  version: "3.0.1"
knowledge:
  reads:
    - Guide/Index            # baseline routing aid (universal)
    - Tools/Slack            # canonical channel IDs (resolve #name → C… for the read target)
---

# read-slack

The canonical Slack-**read** layer for AINative. It owns the mechanics of pulling
messages out of Slack — windowed channel history (cursor-paginated), a single
message by ts, and a thread's replies — over one 429-retry transport. It does **not**
own the channel directory (that's AI Knowledge → `Tools/Slack`) and it does **not**
post (that's `shared:post-to-slack`).

## Two roles

1. **Direct** — the user wants to read something from Slack. Resolve the channel ID,
   call [`scripts/slack_read.py`](scripts/slack_read.py), summarise what came back.
2. **Library** — another skill needs channel history. The reusable code is
   [`scripts/slack_read.py`](scripts/slack_read.py). Because skills are self-contained
   (Architecture Principle 1), a consuming skill **copies** that file into its own
   `scripts/` dir and imports it — see "Using it from another skill" below.

## Auth — MCP-first, token fallback

Per best-practices principle 11 (MCP-first):

1. **Slack MCP** — preferred when a Slack connector is available in the session. No env
   var needed.
2. **`scripts/slack_read.py` + token** — the documented fallback for when MCP can't do
   the job or isn't reachable (Cowork, cron, headless). `SLACK_BOT_TOKEN` (`xoxb-…`);
   the bot must be a member of the channel. Read from env or `--token`; never printed.

> Workspace-wide message **search** (`search.messages`, by query, needs a *user*
> token) lives in `shared:post-to-slack`, not here. This skill reads a *specific
> channel's* history and threads.

## The canonical script

Invoke from a skill with `python3 ${CLAUDE_PLUGIN_ROOT}/skills/read-slack/scripts/slack_read.py <subcommand>`.

| Subcommand | Does | Key flags |
|---|---|---|
| `history` | windowed `conversations.history`, cursor-paginated | `--channel` `--oldest` `--latest` `--limit` `--newest` |
| `message` | one message by ts (`inclusive`) | `--channel` `--ts` |
| `replies` | a thread's messages (`conversations.replies`) | `--channel` `--ts` `--limit` |

Each prints the raw Slack message dicts as JSON. Full flag reference + the library API:
[`references/recipes.md`](references/recipes.md).

## Behaviour that matters

- **`history` returns the whole window by default.** It paginates `conversations.history`
  until the cursor is exhausted — `--limit` is the **page size** (default 200), not a total
  cap. Bound the result with `--oldest` / `--latest` (epoch seconds); omit a bound to leave
  it open.
- **`--newest N` = the most recent N messages.** Caps the result to N and stops paginating —
  the faithful equivalent of a `conversations.history limit=N` read. Use it (instead of, or
  with, `--oldest`) when you want "the latest N" by count rather than a time window — e.g.
  scanning recent channel activity where a fixed time window would over-read a busy channel
  or miss older items in a quiet one.
- **Errors raise `SlackReadError`.** The library functions raise on a Slack error
  (`ok:false`) or a persistent 429. Best-effort callers catch it and degrade (e.g.
  `eng:harvest-cost-changes` appends to a `degraded` list); strict callers let it
  propagate. The CLI converts it to a clean non-zero exit.
- **429 retry is built in.** Up to 4 attempts, backing off by `Retry-After` (capped 5s).
- **Channel IDs, not names.** Anchor on the `C…` ID from `Tools/Slack`. This skill reads;
  it doesn't resolve names — use `shared:post-to-slack`'s `resolve-channel` for that.
- **Read-only.** Reading is safe and idempotent; no draft-and-confirm gate needed (unlike
  posting).

## Using it from another skill (the copy pattern)

Skills are self-contained, so a consuming skill **copies** `slack_read.py` into its own
`scripts/` dir and imports it — it does not read this folder at runtime:

```python
from slack_read import history, message_by_ts, replies, SlackReadError
```

This file is the **canonical source**; AINative `CLAUDE.md` → "Canonical source for
`slack_read.py`" lists the copies and the drift-check one-liner. When you change the
canonical, propagate to every copy in the same PR. (Same pattern as `slack_post.py` /
`notion_rest.py`.) The consuming skill keeps its own channel ID hardcoded and its own
parsing/filtering logic — the helper owns only the fetch mechanics.

## Edge cases

- **`not_in_channel` / `channel_not_found`** (`SlackReadError`) → the bot isn't a member;
  invite it or pick a channel it's in.
- **Best-effort reads** → wrap the call in `try/except SlackReadError` and degrade, rather
  than aborting (the `harvest-cost-changes` pattern).
- **No `requests` installed** → falls back to `curl` automatically (with status capture);
  `urllib` is never used.
- **Huge window** → `history` pulls every page; bound it with `--oldest` to avoid sweeping
  a channel's entire history.

## References

- [`references/recipes.md`](references/recipes.md) — full flag reference, the library API,
  and worked examples (window read, single message, thread replies, best-effort pattern).
- AI Knowledge → `Tools/Slack` — channel directory + auth.
- `shared:post-to-slack` — the posting + workspace-search companion.
