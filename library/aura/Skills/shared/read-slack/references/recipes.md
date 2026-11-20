# read-slack — recipes & API reference

Full reference for [`scripts/slack_read.py`](../scripts/slack_read.py). The SKILL.md has
the rules and the quick table; this file has the per-subcommand detail, the importable
library API, and worked examples.

## Global

- `--token TOKEN` — override the env token. Otherwise `SLACK_BOT_TOKEN` is read from the
  environment.

## CLI

### `history` — windowed conversations.history

| Flag | Meaning |
|---|---|
| `--channel` (required) | Channel ID (`C…`). |
| `--oldest` | Oldest epoch seconds (inclusive lower bound). Omit to leave open. |
| `--latest` | Latest epoch seconds (upper bound). Omit to leave open. |
| `--limit` | **Page size** (default 200) — NOT a total cap. `history` paginates the whole window. |
| `--newest` | Cap to the most recent **N** messages (stops paginating; result truncated to N). The faithful "newest N" read — Slack's `conversations.history limit=N`. |

```bash
# Everything in #ai-feedback over a UNIX-epoch window
python3 scripts/slack_read.py history --channel C0AENS8D48N --oldest 1780300000 --latest 1780900000

# Just the most recent 100 messages (newest-N, no full-window sweep)
python3 scripts/slack_read.py history --channel C0AENS8D48N --newest 100
```
Prints the raw Slack message dicts as a JSON array. Bound with `--oldest` (time window) or
`--newest` (count) to avoid sweeping a channel's entire history.

### `message` — one message by ts

```bash
python3 scripts/slack_read.py message --channel C0AENS8D48N --ts 1780465597.965429
```
Returns a 0- or 1-element JSON array (the message at that ts, `inclusive`).

### `replies` — a thread's messages

```bash
python3 scripts/slack_read.py replies --channel C0B3GN41MDX --ts 1780465597.965429 --limit 100
```
Returns the thread as a JSON array — **parent at index 0**, replies after. Not paginated
(single call, `--limit` default 100).

## Library API

```python
from slack_read import history, message_by_ts, replies, SlackReadError

# Windowed history (epoch-second bounds; both optional). Returns raw message dicts.
msgs = history("C0AENS8D48N", oldest=1780300000.0, latest=1780900000.0)

# Newest-N: the most recent 100 messages, no full-window sweep (faithful limit=N read).
recent = history("C0AENS8D48N", newest=100)

# Single message by ts → 0- or 1-element list.
one = message_by_ts("C0AENS8D48N", "1780465597.965429")

# Thread → parent at [0], replies after.
thread = replies("C0B3GN41MDX", "1780465597.965429", limit=100)
```

- `token=` overrides the env token on any function.
- All three raise `SlackReadError` on a Slack-level error (`ok:false`) or a persistent
  429. They do **not** `sys.exit` — so a loop caller can catch and continue.

### Strict vs best-effort

```python
# Strict — let the error abort (and surface a clean message at the top level):
try:
    msgs = history(channel, oldest=oldest, latest=latest)
except SlackReadError as e:
    raise SystemExit(str(e))

# Best-effort — degrade instead of aborting (the harvest-cost-changes pattern):
try:
    msgs = history(channel, oldest=oldest)
except SlackReadError as e:
    degraded.append(f"slack: {e}")
    msgs = []
```

## Notes

- **Pagination:** by default `history` loops on `response_metadata.next_cursor` until
  exhausted, so it returns the *entire* window — page size (`limit`) only affects
  round-trips, not the total. Pass `newest=N` (`--newest N`) to cap it: pagination stops
  once N messages are collected and the result is truncated to the newest N, the faithful
  equivalent of `conversations.history limit=N`.
- **Timestamp formatting:** epoch bounds are sent as `f"{value:.6f}"`; `ts` values
  (message / thread) are passed through as-is (Slack ts strings already carry 6 decimals).
- **Transport:** prefers `requests` (certifi); falls back to `curl` with HTTP-status
  capture for the 429 check. `urllib` is never used.
- **What's NOT here:** posting (`shared:post-to-slack`), workspace-wide `search.messages`
  by query (`shared:post-to-slack`, user token), and channel-name → ID resolution
  (`shared:post-to-slack`'s `resolve-channel`).
