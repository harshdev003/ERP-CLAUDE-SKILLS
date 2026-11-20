# Running the loop unattended — dispatch, in-flight, restarts

The detail behind `features-loop`'s `start` and `run`: how a tick decides whether dispatched
work is live, stalled, or done, and what changes when no one is there to confirm. The
procedure lives in [`SKILL.md`](../SKILL.md); this is what to look up when a tick has to
decide something about work it did not do itself.

## A session runs one loop, and its agents live in it

**One loop per app, one session per loop.** The harness runs a single self-paced loop in a
session, so running Aurie and Aura at the same time takes two sessions, each started with
its own `--app`. A tick only ever touches its own app's database and channel, so the two
never interfere. `start --app aura` before [Aura readiness](#aura-readiness) lands reports
the gap and starts nothing.

**What survives a restart and what does not.** The loop lives in the session, so closing it
stops the loop; start it again in the new one. Nothing is lost by that — every leg dedups on
the Notion row and the Slack thread, not on session state, so the first tick after a restart
picks up exactly where the last one left off. Recurring loops also expire after seven days,
so a loop meant to run longer needs restarting within the week.


## Builds: in flight, stalled, done

**One build agent at a time; PRs queue for review.** A build is in flight when a build agent
this session dispatched has not finished, **or** when an `implementing` row has not yet
reached a ready-for-review PR — no PR on the row, the PR still a draft
(`gh pr view <url> --json isDraft`), or BugBot not clean on its head. Either one makes `build`
a no-op this tick (or a resume, below), and it says so. An `implementing` row whose PR is
**ready for review** does not count: it is waiting on a person, and holding the queue behind
it would make review latency the loop's throughput. Keying on the agent and the unfinished
row, rather than on any PR existing, still stops a second idea starting in the hours before
the first one opens its PR. The build leg's
`guard-collision` also refuses a second experiment on a surface that already has one — let it,
and report the skip rather than overriding.

**Check the work, not the worker — and never by elapsed time.** Ticks land every 20–30
minutes and a build takes hours, so "no PR after a tick or two" would call a live agent
stalled and start a second one on the same idea. What decides it is whether a worker is
still alive:

- **A live agent** — one this session dispatched and the harness has not reported finished —
  is working. Leave it alone, however quiet it is.
- **A finished agent** — the harness reports it done. Now check its work. **Done** is one
  definition, used everywhere: the row's PR is **ready for review** — not a draft
  (`isDraft` false) — with a clean BugBot review on its current head. Anything short of that
  (no PR, still a draft, open findings, `finish --post` or `ready` not run) is **stalled**,
  because an agent can stop between steps without saying so (AuraConsumerApp
  `.claude/rules/sim-testing-conventions.md` → "One long command per agent"). Finish it from
  the tick, or dispatch a fresh agent with only the remaining steps. A clean draft is
  stalled too: `gh pr ready <the row's PR URL>` then `build-idea ready <IDEA-N>` finishes it —
  always name the PR, because the tick does not run in the build's worktree and a bare
  `gh pr ready` acts on whatever branch the current directory is on.
- **An `implementing` row with no live agent** is judged by the same definition, never by
  the missing agent alone. Short of a ready-for-review PR, it is stalled — that is what every
  unfinished build looks like after a restart, because **dispatched agents live and die with
  the session, exactly as the loop does** — so the first tick of a new session resumes it.
  With a ready-for-review PR it is **done and waiting on a person**: leave it, never
  re-dispatch it, and do not let it hold the queue.

Silence is never progress, and elapsed time is never a stall.

## Unattended legs pass `--confirm`

**Under `run` there is no one to confirm**, and both writing legs gate behind a plan
first — `record-ideas apply --confirm`, `share-ideas post-pending --confirm`. So an
unattended `ideate` passes `--confirm` to both and skips the preview: dispatching `ideate`
from a tick **is** the standing approval, the same as for `digest`. Without it the agent
previews, waits for a yes nobody is there to give, and the idea never reaches the channel.
Run by hand, `ideate` keeps the preview.

## Aura readiness

Cloning this loop for Aura needs, before `--app aura` runs:

1. An **Aura Features** Ideas database from the same schema (Domain, Status, Fingerprint,
   Slack thread, Experiment relation, Screenshot/Video, Decided by/at, …).
2. The channel is already made — `#ai-loop-aura-features` `C0C0ZSMNZM5`.
3. A **sim-test target** for the Aura app, so `build` can sim-test both arms (scoped to Aura's
   screens and Domain values).
4. Wire the new data source into the `--app aura` row of the `--app` table in [`SKILL.md`](../SKILL.md#--app-and-the-target-database), and confirm
   every leg's `--data-source-id` reaches it.

Aurie experiments omit nothing special; Aura experiments **omit `targetApp ai`** in the
AuraBot grammar (see the runbook). No code here changes per app — only the data source,
channel, and sim-test target.

## Driving `run` from a cron

A harness cron (`CronCreate`, or the `schedule` skill for a cloud routine that outlives the
session) can drive `run` instead of `start` when a fixed cadence is wanted; the tick is the
same either way.
