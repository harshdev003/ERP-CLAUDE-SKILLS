---
name: support-loop
description: >
  The engineering leg of Aura app support. Reads the requests Customer Support posts in #support,
  investigates each one assigned to the loop's engineers, and for a real bug with a known cause opens
  a card and a PR for review only. Replies in the request's thread, as Aura AI, only when the finding
  is confirmed. Runs as a dry run by default: it prints the reply, card and PR it would make and
  writes nothing. Never merges, releases or changes user data. Subcommands: `run` (one tick,
  dry run), `run --live` (posts and opens PRs, only on the owner's go), `status`. Trigger on "run the
  support loop", "support loop tick", "dry run the support loop", "sweep #support", "check the support
  threads", or "what would the support loop do". Do NOT use for one request you are already working
  (use the leg skill directly), for erasure or access requests (eng:respond-to-erasure,
  eng:respond-to-sar), or for threads that tag Aura AI (shared:reply-to-aura-ai-mentions).
metadata:
  version: "0.1.2"
knowledge:
  reads:
    - Guide/Index            # baseline routing aid (universal)
    - Tools/Slack            # channel ids, post format and footer
    - Tools/Linear           # Support [Cycle N] parent and card conventions
    - Tools/GitHub           # gh, PR and BugBot conventions
    - Compliance/Privacy     # what a reply, card or PR may repeat about a user
---

# support-loop

Support posts a request in #support; an engineer reads it, digs, fixes and answers. This loop does the
digging and the fix up to a PR, so the engineer's part is the review. Modelled on `eng:livekit-loop`
(card → PR → hand off) and `product:conversations-loop` (answer a thread at most once, from facts only).

```
#support request → sort → investigate → bug? card + PR (stop) → confirmed? reply in thread
```

## What this skill will never do

1. **It never merges, releases or deploys.** A PR ends BugBot-clean and ready. The engineer merges.
2. **It never changes user data unattended.** Credits, refunds, cancellations, subscriptions, account
   state: the leg skill runs only after an explicit go typed in this session.
3. **It never posts outside a dry run's report unless the run is `--live`**, and `--live` needs the
   owner's go in this session. A dry run creates no card, no branch, no PR and no Slack message.
4. **It never replies on a guess.** A reply carries a confirmed cause or a confirmed "not an app
   problem", with the source of each claim. Anything else goes in the tick summary.
5. **It never repeats what isn't already in the thread.** No uid, email or name the thread doesn't
   carry, and none at all in a PR, card, commit or log. Never a user's message text in a PR.
6. **It never takes a privacy-law request or a thread that tagged Aura AI.** Those have owners.

## `run` — one tick

### 1. Sort the threads

```bash
S="python3 ${CLAUDE_PLUGIN_ROOT}/skills/support-loop/scripts/find_support_threads.py"
$S --lookback-days 14                 # live read, needs SLACK_BOT_TOKEN
$S --from-json threads.json           # threads read through the Slack MCP when no token is set
```

Prefer the live read: it pages `conversations.history` over the whole window and reads every
thread with replies. Without `SLACK_BOT_TOKEN`, read #support (`C7KS8TWAG`) with the Slack MCP,
and **follow its cursor until it reports no more messages**: one page is not the window, and a
request on page two would silently drop out of the sort. Read each root that has replies the same
way, in `detailed` format. Write them as `[{"channel", "ts", "messages": [...]}]`, keeping each
message's `user`, `bot_id`, `ts`, `text` and `files`, and pass the file in. Put the number of roots
you read in the tick summary, so a short read shows.

A person who posts through an app keeps their own id in `user`, but the message also carries the
app's `bot_id`. The live read looks each such author up with `users.info`, so a person's reply
counts as an answer and a bot's (Linear, Viktor) does not. With `--from-json`, add
`"author_is_person": true` to a message you know a person posted through an app; without it the
message reads as a bot's. `--check` fails closed when it cannot place an author.

Work only the `pending_new` and `pending_followup` buckets, oldest first. Report every other bucket as
a count. `skipped_engineer` means an assignee is already on it: leave it alone.

### 2. Read and sort one request

Read the whole thread, not just the root. A screenshot often carries the request: open it with the
session's Slack file reader when it has one. When a file won't open, say so in the summary and don't
reply on that thread, rather than answering around the picture. Then put it in one of four kinds:

| Kind | Looks like | Leg |
|---|---|---|
| how-to | "how do I…", a setting the user can't find | the screen's code on origin's default branch |
| account / billing | paid but locked, wrong price, credits | `eng:check-user-receipts`, `eng:investigate-missing-premium`, `eng:fetch-coaching-credits` |
| app or service bug | crash, blank page, button that does nothing, data missing | `eng:search-bugsnag-by-user`, `eng:search-gcp-by-user`, `data:profile-user`, the code |
| voice / live | sound, live chat, replay | `eng:investigate-voice-issue`, the live-session code |

Read code from `origin/<default>` in a worktree, never a local checkout that may be behind.

### 3. Reuse before creating

Search Linear for an open card on the same request: the thread's own card links first (the script
lists them in `cards`), then a title search under the current `Support [Cycle N]` parent. Check
`prs` and open PRs on that card. If a card or PR already covers it, the loop's job is the status, not a
second card.

### 4. Act — or, in a dry run, say what it would do

- **Confirmed bug, cause known, fix fits in one PR:** card under `Support [Cycle N]`, assigned to the
  request's engineer. Fix in a worktree off origin's default branch, following the repo's own
  `.claude/rules/` and the fix loop in `eng:sweep-bugsnag` Steps 4–5: draft PR, BugBot plus a
  sub-agent review, every finding answered, then ready. Stop there.
- **Data fix needed:** say which leg skill and what it would change, and wait for a go. Never in a dry run.
- **Cause not confirmed:** no card unless the thread asked for one, no reply. Summary only.

### 5. Reply — only when confirmed

2–8 lines of Slack mrkdwn, threaded on the request, as Aura AI through `shared:post-to-slack`
(`footer_skill: eng:support-loop`). Lead with the finding, then what was checked, then the card and
PR, then one line support can send the user. Before posting, re-check the thread:

```bash
$S --check C7KS8TWAG:<thread_ts>      # exit 0 = still pending, 3 = someone answered: don't post
```

In a dry run, print the reply instead of posting it.

### 6. Tick summary

```
Support loop · #support · last <n>d · <n> threads read · dry run|live
pending <n> (new <n>, follow-up <n>) · answered <n> · engineer on it <n> · mention <n>
privacy <n> · not ours <n> · cap <n>
- <title> — <kind> — <finding> — would: <reply | card | PR | nothing> <card/PR if any>
```

## `status`

Open cards under the current `Support [Cycle N]` parent assigned to the loop's engineers, with each
one's PR and whether its thread has had a reply from Aura AI.

## Before `--live`

- `SLACK_BOT_TOKEN` (aura_ai), Linear, GCP and Bugsnag access on the machine that runs it.
- A dry run the owner has read and agreed with.

## Two skills post in #support as Aura AI

`shared:reply-to-aura-ai-mentions` reads a thread only when its search for the Aura AI tag returns
it. This loop skips every thread where a person tagged Aura AI (`skipped_mention`) and works the
rest, so each thread has one owner.

**Keep #support out of that skill's excluded channels.** Excluding it would leave the tagged threads
with no owner: the loop skips them and the mention skill would stop reading them.

One case has two claimants: the request post itself lists Aura AI as an assignee beside a loop
engineer. The loop still works that request, and after its reply both skills see an untagged
follow-up as theirs. They post as the same bot and each skips once Aura AI has spoken last, so the
first to run answers and the other stays quiet.
