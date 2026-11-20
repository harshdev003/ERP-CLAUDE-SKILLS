# post-to-slack — recipes & flag reference

Full reference for [`scripts/slack_post.py`](../scripts/slack_post.py). The SKILL.md has
the rules and the quick table; this file has the per-subcommand detail and worked
examples. All examples assume the script is invoked from a skill via
`python3 ${CLAUDE_PLUGIN_ROOT}/skills/<skill>/scripts/slack_post.py …` (or directly with
a relative path during development).

## Global

- `--token TOKEN` — override the env token. Otherwise `SLACK_BOT_TOKEN` (most
  subcommands) or `SLACK_USER_TOKEN` (`search`) is read from the environment.

## `post` — chat.postMessage

| Flag | Meaning |
|---|---|
| `--channel` (required) | Channel ID (`C…`) — preferred — or a name. |
| `--text` | Inline message text. Use `-` to read from stdin. |
| `--text-file` | Read the message body from a file (keeps long bodies off argv). |
| `--thread-ts` | Reply under this parent message ts (omit for top-level). |
| `--unfurl` | Enable link/media unfurling (OFF by default). |
| `--username` | Post as the **bot** with this display name (needs `chat:write.customize`) — still shows an "APP" badge. |
| `--icon-url` | Avatar URL to pair with `--username` (bot-impersonation path). |
| `--as-user` | Post as the **genuine user** via `SLACK_USER_TOKEN` (real name + avatar, no "APP" badge). Mutually exclusive with `--username`/`--icon-url`. |
| `--permalink` | Also fetch + include the message permalink in the output. |
| `--footer SKILL` | Append the Tools/Slack footer for this skill id. |
| `--footer-extra` | Optional env/user/source-link slot for the footer. |
| `--footer-skill-md` | Path to the footer skill's `SKILL.md`, or the base directory holding it. |
| `--footer-version` | Explicit semver override; prefer `--footer-skill-md` when the file path is available. |
| `--footer-runner` | Explicit `who@surface` override; `''` omits it. |
| `--date` | Footer date (`YYYY-MM-DD`); defaults to today. |

Output: one-line JSON, e.g. `{"ok": true, "ts": "1717603200.123", "channel": "C04R4L67676", "permalink": "https://…"}`.

**Body files: never a fixed name.** Get a fresh path per message, write the body there, and post it:
```bash
BODY=$(python3 scripts/slack_post.py body-path --tag cost-report)   # mkstemp: unique per call
# … write the message body to "$BODY" …
```

**Top-level post with footer + permalink:**
```bash
python3 scripts/slack_post.py post \
  --channel C05PC18APLH \
  --text-file "$BODY" \
  --footer eng:analyze-openai-costs \
  --footer-skill-md "$CLAUDE_PLUGIN_ROOT/skills/analyze-openai-costs" \
  --footer-extra prod \
  --permalink
```

**Unattended card into an entry's thread, with the card guards** (refused, exit 2, unless the body is this entry's card):
```bash
python3 scripts/slack_post.py post \
  --channel C0BR3UTNL83 --thread-ts 1793123447.185399 --text-file "$BODY" \
  --expect "abcd1234…" --expect-header "*Cancellation*" --max-chars 6000 --max-chunks 3 \
  --footer data:run-cancellation-feedback-analysis-cadence
```

**Threaded reply** (use the parent's `ts` as `--thread-ts`):
```bash
python3 scripts/slack_post.py post \
  --channel C07MB0TMF5J --thread-ts 1717603200.000400 \
  --text "This user rated COACHING 2/5 — transcript shows the coach pivot broke continuity." \
  --footer data:profile-user
```

**Post as the GENUINE user** (real name + avatar, no "APP" badge) — via `SLACK_USER_TOKEN`:
```bash
python3 scripts/slack_post.py post \
  --channel CPTFXHNP2 --text-file "$BODY" --as-user
```
> `--as-user` uses `SLACK_USER_TOKEN`; a user token on `chat.postMessage` posts as the
> authorizing user. MCP-first: prefer the user's Slack connector/MCP for as-user posting
> when it's connected — this token path is the fallback. Used by `shared:daily-status`.

**Bot-impersonation** (looks like a person but keeps the "APP" badge) — `chat:write.customize`:
```bash
python3 scripts/slack_post.py post \
  --channel CPTFXHNP2 --text-file "$BODY" \
  --username "Jerome Curlier" --icon-url "https://…/avatar.png"
```
> This is the bot path with name/avatar overrides — still shows a small "APP" badge.
> For genuine attribution use `--as-user` above. (`--as-user` and `--username`/`--icon-url`
> are mutually exclusive.)

**Long body via stdin:**
```bash
printf '*Heading*\n\nbody line' | python3 scripts/slack_post.py post --channel C04R4L67676 --text -
```

## `upload` — files via the external-upload flow

`files.upload` is deprecated/removed for new apps. The script uses the 3-step flow:

1. `files.getUploadURLExternal` — request a per-file upload URL + file id (GET with
   `filename` + `length`).
2. POST the raw bytes to that URL.
3. `files.completeUploadExternal` — attach the file(s) to the channel with an optional
   `initial_comment` and `thread_ts`.

| Flag | Meaning |
|---|---|
| `--channel` (required) | Channel ID (`C…`). |
| `--file PATH` (required, repeatable) | A file to upload. Repeat for multiple. |
| `--comment` | `initial_comment` posted alongside the file(s). |
| `--thread-ts` | Attach inside this thread. |
| `--footer`, `--footer-extra`, `--date` | Append the footer to the comment. |

```bash
python3 scripts/slack_post.py upload \
  --channel C07MB0TMF5J \
  --file /tmp/ratings_jun.png --file /tmp/ratings_may.png \
  --comment "*Monthly ratings* — May vs Jun" \
  --footer data:chart-monthly-ratings
```
Output: `{"ok": true, "files": 2, "permalink": "https://…"}` (permalink of the first file).

The bot needs `files:write` + `chat:write` and membership in the channel.

## `permalink` — chat.getPermalink

```bash
python3 scripts/slack_post.py permalink --channel C0ATUL8T936 --ts 1717603200.123
```
Prints the permalink string. Useful to record a posted message's URL back onto a Notion
row / Linear card.

## `resolve-channel` — #name → channel ID

```bash
python3 scripts/slack_post.py resolve-channel --name ai-ideas      # -> C0ATUL8T936
python3 scripts/slack_post.py resolve-channel --name "#growth"     # leading # is stripped
```
Paginates `conversations.list` (public + private). The bot must be a member to see
private channels. **Prefer the hardcoded `C…` ID from `Tools/Slack`** — only fall back to
this for a channel not yet in that directory, and add the row to `Tools/Slack` in the same
PR (best-practices principle 10).

## `search` — search.messages (USER token only)

```bash
SLACK_USER_TOKEN=xoxp-… python3 scripts/slack_post.py search \
  --query "from:@jerome after:2026-06-01" --count 30
```
Prints the raw `matches` array as JSON. Bot tokens are rejected with
`not_allowed_token_type`. `search.messages` runs as the authorizing user, so it's the only
path that sees that user's DMs and private channels — the reason `shared:daily-status`
prefers it for the activity sweep.

## `footer` — build the Tools/Slack footer line

```bash
# version + runner are both auto-resolved — pass only the skill id
python3 scripts/slack_post.py footer --skill shared:post-to-slack
# _Generated by `shared:post-to-slack:3.0.1` · 2026-07-28 · jcurlier@claude-desktop_

python3 scripts/slack_post.py footer --skill data:analyze-experiment \
  --skill-md "$CLAUDE_PLUGIN_ROOT/skills/analyze-experiment" \
  --date 2026-06-05 --extra "<https://linear.app/aura-health/issue/PROD-2154|PROD-2154>"
# _Generated by `data:analyze-experiment:3.0.0` · 2026-06-05 · jcurlier@claude-desktop · <https://linear.app/…|PROD-2154>_

# a runner with no person behind it (CI container, shared service account) can name itself
AINATIVE_FOOTER_RUNNER=ci@github-actions \
  python3 scripts/slack_post.py footer --skill data:run-feedback-analysis-cadence
# _Generated by `data:run-feedback-analysis-cadence:3.2.2` · 2026-07-28 · ci@github-actions_

python3 scripts/slack_post.py footer --skill shared:post-to-slack --runner ''
# _Generated by `shared:post-to-slack:3.0.1` · 2026-07-28_
```
Use this when you compose the message body yourself (or post via MCP) and just need the
canonical footer string. `post`/`upload` append it for you via `--footer`.

The `who@surface` runner tag resolves from the git identity (email local-part, then
`user.name`, then the OS account) and `CLAUDE_CODE_ENTRYPOINT`. Unresolvable halves read
`unknown`. Two characters are folded to `-` so the tag always renders: `_` (the footer is a
`_…_` italic span, so `john_doe@cli` would close the italic early) and, in the surface half
only, `.` (a dotted right-hand side would linkify as a mailto — a dot left of the `@` is
kept, since a local-part like `jerome.curlier` is fine).

An explicit `--runner` / `--footer-runner` / `AINATIVE_FOOTER_RUNNER` gets the **same
per-half folds** rather than being passed through raw, so an override can't render worse than
the auto-resolved tag: `cron@feedback_cadence` and `cron@feedback.cadence` both post as
`cron@feedback-cadence`, and a mistakenly-pasted email (`<redacted-email>`) posts as
`jerome@aurahealth-io` instead of a live mailto link. The split is on the **last** `@`.
`''` omits the field.

**Prefer auto-resolution, including for cron and other unattended sweeps.** Those runs are
armed by a person, and that person is the fact worth keeping — the skill id in the same
footer already marks the post as coming from a cadence skill, so an anonymous `cron@…` tag
would discard the only part that isn't otherwise recoverable. Reach for an override when
there genuinely is no person behind the run (a CI container, a shared service account).

The git identity is read with `git config --get`, so it honours a repo-local override: the
same person shows as whatever that repo sets (e.g. a GitHub noreply handle) and as their
global identity elsewhere. Both still name the person, which is all the field claims.

## Transport notes

- HTTPS prefers `requests` (bundles `certifi`); falls back to `curl` when `requests`
  isn't importable. `urllib` is never used — stock macOS Python can't find the system CA
  bundle through it, which is the bug that made earlier skills reach for curl.
- Every Slack Web API response is checked for `ok:false` and exits with the Slack error
  string, so a caller never silently treats a failed post as success.
