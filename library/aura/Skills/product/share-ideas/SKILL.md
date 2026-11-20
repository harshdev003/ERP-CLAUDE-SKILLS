---
name: share-ideas
description: >
  Post pending rows from the `Learnings/Loop/{Loop}` Notion database to the `#ai-loop-aurie-features` Slack
  channel for async team input. Each idea becomes its own top-level message so replies thread
  per-idea. The thread URL is written back to the Notion row's `Slack thread` property —
  re-running is a no-op for rows that already have one. Use when the user says "share the
  ideas to Slack", "post pending ideas", "ping the team on these ideas", "send the Aurie ideas
  to #ai-loop-aurie-features", or "preview the Slack message for these ideas". Default target is the Aurie
  Features database.

metadata:
  version: "3.13.1"
knowledge:
  reads:
    - Guide/Index            # baseline routing aid (universal)
    - Learnings/Loop/Aurie Features Ideas
    - Tools/Slack            # canonical Channel IDs (for skills) — resolves #ai-loop-aurie-features → C0C1JTK3YCC for the post target
    - Tools/Notion                   # Notion REST / MCP access pattern (NOTION_TOKEN)
  writes:
    - Learnings/Loop/Aurie Features Ideas
---

# share-ideas

Posts pending Aurie ideas to Slack and records the thread URL back on the Notion row. Once a row has a `Slack thread` URL, it's never re-posted — idempotency lives on the row, not in a local state file.

## Where it posts, and what it says

**One channel per loop, resolved from the database being shared.** `--data-source-id` selects the destination through `CHANNEL_BY_DATA_SOURCE`, so a caller that targets the right database cannot post into the wrong channel; `--channel` overrides for a loop not yet mapped, and an unmapped source warns rather than silently defaulting. Mapped today: Aurie Features → `#ai-loop-aurie-features` `C0C1JTK3YCC`, Aura Recommendations → `#ai-loop-aura-recommendations` `C0C0ZSMU7C7`. Still to wire: `#ai-loop-aura-features` `C0C0ZSMNZM5`, `#ai-loop-aurie-conversations` `C0C29JPEUQG`.

**`#ai-ideas` (`C0ATUL8T936`) is now a read-only archive** of the April 2026 threads. Those rows already carry a `Slack thread` URL, and the pending-and-unposted filter excludes them, so nothing is ever re-posted into the new channel. Nothing special is needed to protect the archive — the existing filter does it.

**The decision protocol lives in the channel intro, not in each post.** `Reply to decide — Steve's counts: I approve for implementation / no because … / later. Like the idea but not the design? Say so. :+1: is not a decision. The loop marks the thread: :rocket: approved · :x: closed · :white_check_mark: done.` Repeating it under every idea buried the idea, so the post is the idea and nothing else (the `DECISION_PROTOCOL` constant is the canonical wording for the topic, and it is written to Slack's 250-character topic cap — `TOPIC_MAX`, asserted in the tests).

**The pictures ride as threaded replies — the sheet, then the flows.** From the row's `Screenshot`, `_files_to_post` keeps the combined `Current vs Proposed.png` (the light sky, every single-screen arm on it) and every `Flow …` file, and posts each as its own reply captioned by its name; the per-arm images, the night and anytime sheets and the bare current capture stay on the row for the Linear card. A reviewer answers the sheet ("ship c") or a flow. (Jerome, 2026-09-30.)

**Posting flips `Status` → `shared`**, in the same write as the permalink. That pair is what makes a re-run a no-op — the row then fails both the pending filter and the empty-thread filter.

## When to invoke

| User says | Run |
|---|---|
| "share the ideas to Slack" / "post pending ideas to #ai-loop-aurie-features" | `post-pending --confirm` (after `preview`) |
| "preview the Slack message for the pending ideas" | `preview` |
| "share the first 5 pending ideas" | `post-pending --limit 5 --confirm` |

The skill only posts rows with `Status = pending` AND `Slack thread = empty`. Anything else is skipped — the database is the source of truth.

## Prerequisites

- `NOTION_TOKEN` set (see AINative root [CLAUDE.md → "Notion REST integration"](../../../../CLAUDE.md#notion-rest-integration)). Required to query pending rows + write back the Slack URL.
- **Slack is MCP-first** (best-practices principle 11): prefer the Slack MCP connector when one is connected. This skill's post-and-record loop runs `slack_post.py` directly with `SLACK_BOT_TOKEN` (the `aurabot` bot token, `xoxb-...`) — API-first by necessity, because the loop captures each post's permalink to write back to the Notion row. The bot must be a member of the target channel — invite it once with `/invite @AuraBot` in `#ai-loop-aurie-features` if it isn't already.
- Destination channel is resolved from `--data-source-id` via `CHANNEL_BY_DATA_SOURCE`, or overridden with `--channel`; it defaults to `#ai-loop-aurie-features` (`C0C1JTK3YCC`). Canonical IDs are in `Tools/Slack` (AI Knowledge). The bot must be a member of whichever channel a loop posts to.
- [`scripts/share_ideas.py`](scripts/share_ideas.py) and its [`scripts/notion_rest.py`](scripts/notion_rest.py) and [`scripts/slack_post.py`](scripts/slack_post.py) copies alongside this SKILL.md. Slack posting goes through `slack_post` (the canonical `shared:post-to-slack` helper — see AINative [CLAUDE.md → "Canonical source for `slack_post.py`"](../../../../CLAUDE.md)); this skill keeps its own channel ID and message format.

## Workflow

### Step 0 — Confirm tokens

```bash
test -n "$SLACK_BOT_TOKEN" && echo "Slack OK" || echo "MISSING SLACK_BOT_TOKEN"
test -n "$NOTION_TOKEN"    && echo "Notion OK" || echo "MISSING NOTION_TOKEN"
```

If either is missing, stop and prompt the user to add it via `update-config`.

### Step 1 — Preview (always)

```bash
python3 plugins/product/skills/share-ideas/scripts/share_ideas.py preview
```

Prints the rendered Slack body for every pending unposted row. Show the user the count and (if there are fewer than ~5) the bodies themselves. For larger batches, summarise: count + a sample of the first message + a one-line description of the others.

Never skip preview. The channel is team-visible — a misformatted post burns trust faster than a one-minute pause to confirm.

### Step 2 — Post with `--confirm`

```bash
python3 plugins/product/skills/share-ideas/scripts/share_ideas.py post-pending --confirm
```

Without `--confirm`, `post-pending` is the same as `preview`. The flag is the gate.

Add `--limit N` for staged rollouts (e.g. post 5, confirm the look, post the rest).

Posts are paced at ~1.2s per message to stay under Slack's per-channel rate limit. A batch of 20 ideas takes ~25 seconds.

### Step 3 — Report

The script prints one line per post and a totals line at the end. Surface the totals verbatim, plus the channel link so the user can scan the result:

> Posted 14 of 14 pending Aurie ideas to [#ai-loop-aurie-features](https://auramobile.slack.com/archives/C0C1JTK3YCC). Zero failures.

## Message format

Every idea is a **thread**: a short top post, then the reasoning and the hypothesis as its first two replies, then the pictures — one reply each. The top post is the idea and nothing else:

```
*IDEA-42 · <title>*
`<screen>` · <category> · <lever> · Impact: <impact> · Effort: <effort>

*The idea*
<description>
```

The parent stops there. It does not announce the pictures — they are the next thing in
the thread, and a sentence saying so is one more line between the reader and the idea.

Then, in order (`render_replies` + `_attach_screenshots`):

```
↳ *Why it lifts the lever*  <rationale>
↳ *Hypothesis*              <hypothesis>
↳ [sheet]  Current vs Proposed — the real screen with the change on it; b is the current screen (control).
↳ [flow]   Flow c · tap → Share Aurie sheet → iOS share sheet      ← only when the row has flows
↳ …
```

The header carries the **Idea ID** — the immutable, human-friendly address the team refers to the idea by (a pre-id row falls back to its fingerprint). There is no trailing footer line and no decision protocol in the body (see above). Variant letters match the experiment arms they would become: the current screen is **b** (control), treatments are a, c, d ….

`share_ideas.py` passes `footer_skill="product:share-ideas"` to `post_message`, so the versioned `Tools/Slack` footer (`_Generated by `product:share-ideas:<version>` · <YYYY-MM-DD>_`, version from `metadata.version`) is appended automatically — it's not part of `render_message`'s body.

The team decides by **replying** in the thread; `product:harvest-idea-decisions` reads those replies (approve / reject / defer, a question → `needs_answer`, "like the idea, not the design" → `needs_redesign`, answered by `post-redesign`).

## Idempotency

The script's per-row filter is:

```
Status = pending  AND  Slack thread is empty
```

That filter is the *only* idempotency mechanism — there is no local state file, no per-run cache, no "already-posted" set carried in memory. Re-running on a fully-posted backlog returns `No pending unposted ideas.` and exits clean.

If a post lands in Slack but Notion's write-back fails (network blip), the row stays "unposted" from this skill's perspective and a re-run will double-post. This is rare but possible. If you see a duplicate, delete the second Slack message manually and re-`update_page_props` the row with the surviving permalink — there's no automated reconciliation today.

## Slack channel membership

If the bot isn't in `#ai-loop-aurie-features`, `chat.postMessage` returns `not_in_channel` and every post fails. One-time fix from any channel member's account:

```
/invite @AuraBot
```

The bot only needs `chat:write` + `channels:read` scopes for this skill.

## Future

Not built yet, tracked separately:

- **Pull thread replies back into Notion.** Periodically scan each posted row's thread, summarise reactions and substantive replies into a Notion comment or a new `Slack signal` property. Today the team reads `#ai-loop-aurie-features` directly. A future `share-ideas replies` subcommand will close the loop.
- **Multi-channel routing.** If we ever post Aura ideas vs Aurie Web ideas to different channels, parametrise the destination channel per app (add the second ID to `Tools/Slack` in AI Knowledge and select it based on the row's `App` field).

## Related skills

- [`product:record-ideas`](../record-ideas/SKILL.md) — adds new pending rows. Run this skill *after* the backlog is curated by `manage-ideas`.
- [`product:manage-ideas`](../manage-ideas/SKILL.md) — review / approve / reject. Run between `record-ideas` and `share-ideas` so the channel doesn't get duplicates.

## References

- Notion database: [Aurie Features Ideas](https://www.notion.so/7e721e75374645a7acfa085e3aeca6df) (data source `8d14f3eb-0c55-4f25-9322-ab2b2a69f092`)
- Slack channel: `#ai-loop-aurie-features` (`C0C1JTK3YCC`) · archive `#ai-ideas` (`C0ATUL8T936`)
- Linear: [PROD-1700](https://linear.app/aura-health/issue/PROD-1700) (parent [PROD-1686](https://linear.app/aura-health/issue/PROD-1686)).

## What reaches the thread

One combined **Current vs Proposed** sheet per idea and one reply per **flow**
(`Flow <arm> · <title>.png`); nothing else on the row is posted. A batch posts in **Idea ID
order**. When a thread asks for the screens after a tap, attach the flows and post them alone:

```bash
python3 share_ideas.py post-redesign --idea IDEA-13 --flows-only --confirm
```
