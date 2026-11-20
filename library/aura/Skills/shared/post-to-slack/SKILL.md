---
name: post-to-slack
description: >
  Canonical "how to post to Slack" layer for AuraHealth — the one place the posting mechanics
  live (message, file upload, thread reply, permalink, channel resolution, message search,
  attribution footer). Use it two ways: (1) directly, when the user wants something sent to
  Slack — "post this to Slack", "send this to #ai-product", "drop this in the eng-budget
  channel", "share this summary on Slack", "upload this chart/file to Slack", "reply in the
  Slack thread", "get the Slack permalink for this message", "what's the channel ID for
  #growth", "search Slack for messages from @jerome"; and (2) as a library, when another skill
  needs to post — "how do I post to Slack from a skill", "add Slack posting to this skill".
  Defers to AI Knowledge Tools/Slack for the channel directory and footer spec. Do NOT use to
  author the daily standup (shared:daily-status) or to post pending product ideas
  (product:share-ideas).

metadata:
  version: "3.3.0"
knowledge:
  reads:
    - Guide/Index            # baseline routing aid (universal)
    - Tools/Slack            # canonical channel IDs + the attribution footer spec
---

# post-to-slack

The canonical Slack-posting layer for AINative. It owns the **mechanics** of talking
to Slack — posting a message, uploading files, replying in a thread, fetching a
permalink, resolving a channel id, searching messages, and building the attribution
footer. It does **not** own the channel directory or the footer spec: those live in
AI Knowledge → `Tools/Slack`, which this skill reads.

## Two roles

1. **Direct** — the user asks for something to be sent to Slack. Resolve the channel,
   compose the message, confirm, then post with [`scripts/slack_post.py`](scripts/slack_post.py).
2. **Library** — another skill needs to post. The reusable code is
   [`scripts/slack_post.py`](scripts/slack_post.py). Because skills are self-contained
   (Architecture Principle 1), a consuming skill **copies** that file into its own
   `scripts/` dir rather than importing it. This file is the **canonical source** — see
   "Using it from another skill" below.

## Auth — MCP-first, token fallback

Per best-practices principle 11 (MCP-first):

1. **Slack MCP** — preferred when a Slack connector is available in the session. No env
   var needed.
2. **`scripts/slack_post.py` + token** — the documented fallback for when MCP can't do
   the job or isn't reachable (Cowork, cron, headless). Tokens:
   - `SLACK_BOT_TOKEN` (`xoxb-…`) — post / upload / permalink / resolve-channel. The
     bot must be a member of the target channel.
   - `SLACK_USER_TOKEN` (`xoxp-…`) — used for **`search`** (`search.messages` rejects
     bot tokens with `not_allowed_token_type`; runs as the user, so it sees that user's
     DMs + private channels) **and for `post --as-user`** (posting with genuine user
     attribution — see Posting rules). Prefer the user's Slack MCP connector for as-user
     posting when it's connected; this token is the fallback.
   - `SLACK_WEBHOOK_URL` exists in the env inventory but **no skill uses it today** —
     it's a single-channel one-way webhook with no threading/permalink/upload, so it
     can't serve the general posting surface. Prefer the bot-token script.

Tokens are read from the environment (or `--token`) and never printed.

## The canonical script

Invoke from a skill with `python3 ${CLAUDE_PLUGIN_ROOT}/skills/post-to-slack/scripts/slack_post.py <subcommand>`.

| Subcommand | Does | Key flags |
|---|---|---|
| `post` | `chat.postMessage` | `--channel` `--text`/`--text-file`/`-` `--thread-ts` `--thread-overflow` `--unfurl` `--username`/`--icon-url` `--as-user` `--permalink` `--footer` `--footer-skill-md` `--footer-version` `--footer-runner` |
| `upload` | file(s) via external-upload flow | `--channel` `--file` (repeatable) `--comment` `--thread-ts` `--footer` `--footer-skill-md` `--footer-version` `--footer-runner` |
| `permalink` | `chat.getPermalink` | `--channel` `--ts` |
| `resolve-channel` | `#name` → channel ID (`conversations.list`) | `--name` |
| `search` | `search.messages` (user token) | `--query` `--count` |
| `footer` | print the `Tools/Slack` footer line | `--skill` `--skill-md` `--version` `--runner` `--extra` `--date` |

`post`, `upload`, and `footer` print a one-line JSON result (or the footer string) to
stdout so callers can parse `ts` / `permalink`. Full flag reference + worked examples:
[`references/recipes.md`](references/recipes.md).

## Posting rules (non-negotiable)

These are baked into the script defaults; keep them when composing by hand or via MCP:

- **Channel IDs, not names.** Anchor on the `C…` ID from `Tools/Slack` → "Channel IDs
  (for skills)". Names drift; IDs are stable. Use `resolve-channel` only for a channel
  not yet in that directory (and add the row to `Tools/Slack` in the same PR).
- **Unfurl off by default.** `post` sends `unfurl_links:false` + `unfurl_media:false`
  so Linear/GitHub links don't expand into preview cards. Pass `--unfurl` to opt in.
- **One footer per post — let the script build it, don't hand-type it.** Every message ends
  with the `Tools/Slack` footer
  (`_Generated by \`<plugin>:<skill>:<version>\` · <YYYY-MM-DD>[ · <runner>][ · <env-or-source>]_`).
  Pass `--footer <plugin>:<skill>` and the script **auto-resolves two of those fields**, so
  neither depends on the model reproducing it:
  - **`:<version>`** — from that skill's `metadata.version` frontmatter (e.g.
    `data:investigate-user-rating:1.0.3`). `--footer-version <v>` overrides; `''` forces none.
    Without the flag, resolution covers all three layouts the helper runs under: in-repo,
    the install cache, and Claude local-agent-mode sessions — where plugin roots are opaque
    ids (`plugin_01SQmXX…`) that encode no plugin name, so the sibling `manifest.json`
    supplies the id-to-name mapping. Before that was wired, **every** footer posted from
    such a session silently lost its version
    ([ENG-1110](https://linear.app/aura-health/issue/ENG-1110)). Prefer the flag anyway
    when you have the path — it needs no layout knowledge at all.
  - **`<runner>`** — the `who@surface` operator tag (e.g. `jcurlier@claude-desktop`): git
    identity + `CLAUDE_CODE_ENTRYPOINT`. It answers the question the version can't — *whose
    run produced this?* — which is the first thing you want to know when a post looks wrong.
    **Let it auto-resolve, including on unattended runs.** A cron sweep is armed by a person,
    and that person is the useful fact; the skill id in the same footer already distinguishes a
    cadence post from a hand-run one, so an anonymous `cron@…` would trade away the only part
    that isn't otherwise recoverable. `--footer-runner <tag>` overrides and `''` omits, for the
    rare runner with no meaningful git identity behind it (a CI container, a shared service
    account); `AINATIVE_FOOTER_RUNNER` does the same process-wide.

  **Don't compose the footer literal in the message body** — a model-typed footer silently drops
  the version (observed on a rating reply); always route it through `--footer` (or, when composing
  a body outside a post call, generate the line with `slack_post.py footer --skill <id>` and paste
  its output verbatim). `--footer-extra` is the env tag / source link slot only — the operator
  belongs in `<runner>`, so don't put a person there.
  Thread replies get one too; batched posts get one each (no summary footer). Exception: a skill
  posting *as the user* via an integration that auto-signs (e.g. `shared:daily-status`) omits the
  skill footer to avoid a double tail.
- **mrkdwn, not Markdown.** `_italic_`, `*bold*`, backticks for code, `<url|label>` for
  links.
- **Thread vs top-level.** `--thread-ts <parent_ts>` posts a reply; omit it for a new
  top-level message.
- **Posting identity — three choices.** (1) Default: as the **bot**. (2) `--username` +
  `--icon-url`: as the bot with a custom name + avatar (needs `chat:write.customize`) —
  it only *looks* like a person and still shows an "APP" badge. (3) `--as-user`: as the
  **genuine user** via `SLACK_USER_TOKEN` (real name + avatar, **no** badge) — use this
  when the post should be attributed to a person (e.g. `shared:daily-status`). `--as-user`
  is mutually exclusive with `--username`/`--icon-url` (the identity is the user's own).
  MCP-first: prefer the user's Slack connector/MCP for as-user posting when it's
  connected; the `SLACK_USER_TOKEN` script path is the fallback.

### Card guards: a post must be the caller's own body

A worker that posts a body it did not write sends one entry's analysis into another entry's thread. On 2026-10-27, two cadence workers shared a scratch file, and a 69k-character guardrail transcript went out as 19 replies under a cancellation card. Every unattended card post passes these guards. Each one refuses with exit 2 and sends nothing:

- `--expect <token>` (repeatable): the body must contain an id only this entry's body carries, such as its redacted UID or the alert's ts.
- `--expect-header <text>` (repeatable): the first non-empty line must contain it, typically the card's header and the uid.
- `--max-chars <n>`: a card is about 6,000 characters at most.
- `--max-chunks <n>`: refuse a body Slack would split into more replies (3 for a card).

Keep the body out of shared files: pipe it on stdin (`--text -`), or write it to a fresh path from `slack_post.py body-path --tag <skill>-<entry>`, which uses mkstemp. Never use a fixed name like `/tmp/reply.txt`.

## Plan before posting (side-effect gate)

Posting is an outward-facing side effect. Before the first `post`/`upload`:

1. Resolve the **channel ID** (from `Tools/Slack`, or `resolve-channel`).
2. Show the user the **rendered message** (including the footer) and the target channel.
3. Post only on confirmation. For a batch (N messages), show one representative render
   and the count before sending.

Never post user-scoped output without surfacing it first. If the message contains
end-user identity data, follow `Compliance/Privacy` (UID prefix + email domain only).

## Using it from another skill (the copy pattern)

Skills are self-contained, so a consuming skill **copies** `slack_post.py` into its own
`scripts/` dir — it does not read this folder at runtime. This file is the **canonical
source**; AINative `CLAUDE.md` → "Canonical source for `slack_post.py`" lists the copies
and the drift-check one-liner. When you change the canonical, propagate to every copy in
the same PR. (Same pattern as `notion_rest.py` / `resolve_window.py` / `gcp.py`.)

A consuming skill still keeps its **own** channel ID hardcoded (Principle 1) and declares
`Tools/Slack` in its `knowledge.reads`.

## Edge cases

- **`not_in_channel` / `channel_not_found`** → the bot isn't a member. Invite it
  (`/invite @aura_ai` in the channel) or pick a channel it's in.
- **`not_allowed_token_type` on `search`** → a bot token was used; `search` needs
  `SLACK_USER_TOKEN`.
- **No `requests` installed** → the script falls back to `curl` automatically; no action
  needed. (`urllib` is never used — it can't find the macOS CA bundle.)
- **A body over 4,000 characters** → the script splits it, on a blank line or a bullet,
  and posts the chunks in order. Do **not** pre-split it yourself and do not shorten the
  message to fit. Slack splits an over-long body on its own, mid-token, which tears a
  `<url|label>` link in half and returns the **last** ts; `post` returns the **first**,
  which is the one a `--permalink` or a follow-up `--thread-ts` must anchor to, and
  lists the rest under `chunk_ts`. A footer stays on the final chunk. The cut prefers
  the start of a section (a whole-line `*bold*` header), then of a bucket (a whole-line
  `_italic_` header), then a blank line. A chunk never ends on a header line.
- **Keeping the overflow in one thread** → by default, chunks 2..N are consecutive
  **top-level** messages. Pass `--thread-overflow` (`thread_overflow=True`) to post
  them as replies under chunk 1, so a long post is one message plus its thread. With
  `--thread-ts` every chunk is already in that thread and the flag changes nothing.
  `shared:daily-status` uses it for the standup main message.
- **Re-running** → `post`/`upload` are not idempotent (Slack has no client dedup key);
  the plan-and-confirm gate is the guard. Skills that loop should track an
  already-posted marker (e.g. `product:share-ideas` records the thread URL on the row).

## References

- [`references/recipes.md`](references/recipes.md) — full per-subcommand flag reference,
  the external-upload flow explained, and worked examples (post, threaded reply, file
  upload, posting-as-user).
- AI Knowledge → `Tools/Slack` — channel directory, footer spec, auth, attribution rules.
