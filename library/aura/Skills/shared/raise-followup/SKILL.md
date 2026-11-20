---
name: raise-followup
description: >
  The one way a session or an unattended skill asks for something: a PR that is ready for
  review, a go for a release or prod change, a decision, a blocked step, or an FYI. Posts one
  self-contained, role-neutral message (what, why, the exact ask, links, risk, the paste-ready
  go line and where it is typed) to the follow-up channel, and by kind files or updates a deduped
  Linear card. One post per dedupe key: a change is a reply in that post's thread, never a new post or an edit. Where
  it goes and which role acts live in one config file, never in the caller. Trigger on "raise a
  follow-up", "ask for a go", "tell the approver this PR is ready", "post a follow-up", "escalate
  this blocked step", "flag this for a decision", or any unattended skill that would otherwise
  end a run with a question nobody reads. Do NOT use for alerts (they go to the alert channels),
  for a general Slack post (shared:post-to-slack), or to read and work the follow-up queue
  (shared:check-followups).
metadata:
  version: "1.0.1"
knowledge:
  reads:
    - Guide/Index      # baseline routing aid (universal)
    - Tools/Slack      # channel directory and footer spec
    - Tools/Linear     # team keys
---

# raise-followup

Every session and every unattended skill raises its follow-ups through this skill, so the
destination, the format and the card handling can change in one place without touching any
caller.

## Unattended runs: every stop raises one

A skill that runs unattended (a routine, cadence, loop or scheduled task) calls this skill on
every stop or failure, including hard stops, a failed or blocked step, a crash, an expired wait,
and an early end. A card comment or a local report alone is not enough. Use `--kind blocked` (or
`decision` when a person must choose) and a stable key per stop, such as
`<skill>:<app>:stop:<stage>`, so a stop that repeats each run stays one post with replies. Real
alerts (cost thresholds, outages, security events) are not follow-ups and keep their alert
channels.

**Why.** The weekly Aurie screens routine ran on 2026-11-16, started its harvests, and let a
30-minute wait expire. The session ended with nothing posted: no report, no syncs, no
follow-up, and nobody knew until the next week.

## What the caller gives

| Field | Required | Meaning |
|---|---|---|
| `--kind` | yes | `pr-ready`, `go-needed`, `decision`, `blocked` or `fyi` |
| `--title` | yes | One line, readable on its own |
| `--what` / `--why` / `--ask` | what always; why and ask unless fyi | Plain English for a reader with no context |
| `--for` | no | The role that must act: `approver`, `reviewer` or `owner`. Defaults by kind |
| `--go-line` / `--go-session` / `--go-type` | go-needed | The paste-ready go text, the session where it is typed, and its type (`release`, `prod-change`, `security`, `merge`) |
| `--risk` / `--rollback` | no | One line each |
| `--pr` / `--card` | pr-ready needs `--pr` | The PR link; the Linear card the follow-up is about (it gets a comment) |
| `--key` | yes | The dedupe key: one post per key, e.g. `ainative-pr-1587` or `sync-screens:orphan:paymentPending_screen` |
| `--session` | yes | The session or skill raising it (default `AINATIVE_SESSION_NAME`) |

## Run it

Always look at the post first:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/raise-followup/scripts/followup.py raise --dry-run \
  --kind go-needed --title "Release IntelligenceService 2.3.1" \
  --what "Ships the prompt-cache fix to prod." --why "Cache misses cost about \$40 a day." \
  --ask "Give the release go." --go-line "go: release IntelligenceService 2.3.1" \
  --go-session Backend --go-type release --rollback "Redeploy 2.3.0." \
  --pr https://github.com/AuraHealth/IntelligenceService/pull/1 --key is-release-2.3.1 --session Backend
```

Then run the same command without `--dry-run`. It prints one JSON object:
`action` (`posted`, `replied`, `unchanged`, `reposted` or `refused`), `permalink`, `ts`, `channel`,
`card` and `card_action` (`filed`, `commented`, `none` or `failed`). Exit 1 means the post broke a
rule and nothing was written (the `problems` list says which). Exit 2 means a Slack or Linear call
failed; re-running with the same key is safe.

## The rules the script enforces

- **Role-neutral.** No person's name in any field: the post names a role ("needs a go from the
  approver (release)"). Who fills each role lives only in [`references/routing.json`](references/routing.json),
  and a name listed there is refused anywhere in the post.
- **Self-contained.** "As discussed", "see above" and the like are refused: say the thing.
- **No personal data or secrets.** Emails and tokens are refused anywhere. IP addresses and full
  ids are refused outside links: use a uid prefix.
- **One post per key, never edited.** The script finds the newest post with the same key in the
  channel (window from the config). The same text as the latest version (the post or its last
  reply) does nothing. Changed text is posted as a reply in that post's thread: messages in this
  channel are never edited or deleted (`tests/test_slack_never_edits.py`). A post already marked
  handled (✅) gets a new top-level post, so the change is not buried in a closed thread.
- **Cards by kind.** `go-needed`, `decision` and `blocked` comment on `--card` when given, else on
  the open card already carrying the key, else file one in the configured team. `pr-ready`
  comments on `--card` only. `fyi` gets no card. A card is touched only when the post is new or
  changed, never on every run.
- **A post is never a go.** The go line is text for the approver to type in the named session.
  Nothing reading the channel acts on it.
- **No runner in the footer.** The footer names the skill and date only, so posts stay
  role-neutral; `from:` names the session.

## Changing where things go

Edit [`references/routing.json`](references/routing.json) in a PR: roles and who fills them,
which role may give which type of go, and per kind the header, default role and card handling.
`followup.py check-config` validates it. No caller changes.
