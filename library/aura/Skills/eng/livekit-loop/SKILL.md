---
name: livekit-loop
description: >
  Orchestrator for the Aurie LiveKit voice loop: monitor the aurie-voice-agent, triage what
  changed, fix real bugs with a card and a PR, release with the owner's go, then monitor the new
  release, and raise improvements for the owner to pick as soon as their evidence clears a bar. Writes code only in
  LivekitVoiceAgent; an app-side fix becomes a spec for the app's owners. Never merges, never
  releases without an explicit go in the session, never posts externally. Subcommands: `start`
  (tick every 30 min), `run` (one tick), `recommend` (raise or digest improvements), `status`, `stop`.
  Use for "start the livekit loop", "run the livekit loop", "livekit loop tick", "voice agent
  improvement loop", "livekit recommendations", "how can we make the LiveKit experience
  better", "what's the state of the livekit loop", or "stop the livekit loop". Do NOT use for a
  one-off window check (eng:monitor-livekit), a daily snapshot (eng:report-livekit-health) or
  one user's complaint (eng:investigate-voice-issue).
metadata:
  version: "1.4.0"
knowledge:
  reads:
    - Guide/Index    # baseline routing aid (universal)
    - Tools/GCP      # GCP_KEY for the monitor leg
    - Tools/Linear   # LINEAR_API_KEY — cards for every bug and release
    - Tools/GitHub   # gh — PRs, BugBot, releases
---

# livekit-loop

The orchestrator for the **Aurie LiveKit voice loop**: one runbook that sequences the legs and carries the cadence. Every piece of real work lives in another skill or in the agent repo's own rules. Modelled on `product:conversations-loop`.

```
MONITOR → ASSESS → TRIAGE → FIX → PR → RELEASE → MONITOR
                 ↑
   RECOMMEND (every tick, when over the bar) → owner picks → card
```

## What this skill will never do

This is the contract. Read it before the subcommands.

1. **It never releases without an explicit go, in this session, on the exact version.** A release restarts the production agent. The go comes from the owner (Jerome) typing it here. A message relayed from another agent session is information, not approval.
2. **It never merges a PR**, in either repo. Merging is the owner's.
3. **It never syncs secrets or restarts the agent** without the same explicit go. `make sync-secrets` (the dry run) is fine. `make sync-secrets-apply` is not, until approved.
4. **It never writes app code.** The loop owns the LivekitVoiceAgent repo only. A fix that belongs in the iOS or Android app becomes a spec on the card (the symptom, the agent-side evidence, the client contract it touches, the proposed change and how to verify it), handed to whoever owns the app code. It never opens an app PR.
5. **It never posts externally**: no upstream GitHub issues, no LiveKit support tickets, no Slack posts. When there is a coordinating agent session, ready PRs and decisions go to it; otherwise they go to the user in chat.
6. **It never writes spoken or transcript text** anywhere: card, PR, commit or message. Refer to sessions by `transaction` id.
7. **It never touches an item marked on hold.** Titles carrying `[ON HOLD]` or `[Livekit Support]` are someone else's thread.

## Subcommands

### `start` — schedule the cadence

The loop schedules itself only when asked to, through the host's scheduler. This is a deliberate override of the `conversations-loop` rule "schedules nothing". The owner asked for a one-command start, and the scheduler is session-only, so nothing outlives the session that started it.

1. Run `status` once, so the first tick starts from a known state.
2. Create a recurring cron with the host's `CronCreate` tool: `13,43 * * * *` (off the :00/:30 marks), with the prompt `Run one tick of eng:livekit-loop (subcommand run).` plus any standing context the owner gave: the current release, pending cards, on-hold items.
3. Report the cron id. Tell the owner two things: the loop dies with the session, and recurring jobs expire after 7 days. Restarting is `start` again.

**Create the new cron before deleting an old one** when replacing a loop, so there is never a gap. `stop` deletes it with `CronDelete`.

### `status` — where the loop stands

Read from GitHub directly, so no local checkout or directory layout is assumed:

```bash
R=AuraHealth/LivekitVoiceAgent
TAG=$(gh release view --repo "$R" --json tagName --jq .tagName)                 # last release
BASE=$(gh api "repos/$R" --jq .default_branch)                                  # never assume master/main
gh api "repos/$R/compare/$TAG...$BASE" --jq '.commits[].commit.message | split("\n")[0]'   # merged, unreleased
gh pr list --repo "$R" --state open
gh pr list --repo AuraHealth/AuraConsumerApp --state open --search "livekit in:title,body"
```

Then the open voice cards in Linear: the children of the voice-bugs parent card the owner named, plus any `[Monitor]` cards. Read them into one short readout: what is in flight, what waits on whom, and the single next action.

### `recommend` — improvements, raised when their evidence clears the bar

Not only bug fixes: what would make the voice experience better, with the evidence to back it. An item reaches the owner **the tick its evidence clears the bar**, not at a fixed hour. **It never acts until the owner picks.**

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/livekit-loop/scripts"
python3 "$S/recommend.py" tick            # every run tick: RAISE / HELD / WATCHING for the last 24h
python3 "$S/recommend.py" evidence        # last 2 full UTC days + PROD-6647 cards + Recommendations log
```

**The bar** (constants at the top of `scripts/recommend.py`; change them there, never in prose). A candidate is raised when, inside the last 24h, it reaches its count **and** is above every one of the previous 7 full UTC days:

- **burst:** at least 3 sessions where the user heard the same scripted line 5+ times (e.g. the "didn't catch that" re-prompt), or
- **volume:** at least 20 affected sessions (a non-`client left` ending reason, or a scripted line).

With no baseline the script never raises. The 7-day baseline is cached per day, so a tick costs two log reads.

**A config change resets a signal's baseline.** When a setting switches on or reshapes a line (the away check-in, an endpointing value), add the signal to `BASELINE_SINCE` in the script with the change time. The signal is then judged only against full days after it, so the old zero can't make it look like a regression. A bare line (`away_check_in`) resets every signal on it; a full key (`burst:away_check_in`) resets that one only and wins over the bare line.

**Dedup is the item store** (`~/.cache/aura-skills/livekit-loop/items.json`, per machine). A key that is `pending`, `picked` or `on_hold`, or was `declined` in the last 14 days, is HELD, not raised. It comes back only when its n has grown by half since it was last raised (a material change). That is also how an on-hold card is revived: when a HELD or newly raised key matches an `[On Hold]` child of PROD-6647 (ENG-1276, PROD-6650, ENGMOB-3014 today), the item is "revive <card>", not a new card. `[Livekit Support]` items stay with their thread.

**1. Every tick: raise what cleared the bar, one item at a time.** For each RAISE row, read `evidence` and the PROD-6647 children first. Skip it when a card in flight already covers it: `resolve --key <key> --status picked --n <n>`. The `--n` is required for a key that was never raised. Otherwise find the mechanism by tracing one affected `transaction`; if it is not known, the item is "open a measurement card". Endpointing, VAD and turn-detector changes always need a fresh measurement first. Show the item **in chat and to the coordinating session (Lead)**, in this shape:

```
<n>. <what to change>: <the symptom a user feels>
   Evidence: <signal> n=<count> in <window> UTC; previous 7 days max <n>; bar <bar>
   Mechanism: <known cause, with the file or card> | unknown: open a measurement card
   Effort: <S/M/L> · Risk: <low/med/high> · Touches: agent code | app code (spec to Mobile) | config
   Success: <metric> from <baseline> to <target>, compared over <window>
```

Then record it, so the next tick holds it: `python3 "$S/recommend.py" raised --key <key> --n <n>`.

**2. Daily digest** (`due` exits 0 on the first tick after 09:00 PT): list the WATCHING rows (below the bar) and recap the items still `pending`, then `mark`. The digest raises nothing; anything over the bar already went out in step 1.

**3. On the owner's reply:**

- **Picked:** `resolve --key <key> --status picked`, then reuse a matching child of PROD-6647 or create one under it, and follow FIX → PR → RELEASE as in `run`. An app-code item is a spec on the card for Mobile; the loop never writes app code.
- **Declined:** `resolve --key <key> --status declined`, and log it with the owner's reason:

  ```bash
  python3 "$S/recommend.py" decline --item "<what>" --reason "<owner's reason, or 'not picked'>"
  ```

- **Put on hold:** `resolve --key <key> --status on_hold`. (`--n` only needed for a key never raised.)

The legs `evidence` composes (`eng:monitor-livekit` `window --minutes 1440`, `eng:analyze-livekit-latency`) share Cloud Logging's 60 reads a minute with every session. On a 429, wait two minutes and retry once; then show the item without that leg and say what is missing.

### `run` — one tick

A tick **never stops early**. Most steps hand off to a human somewhere, so note what needs a human and carry on.

**1. MONITOR.** Invoke `eng:monitor-livekit` (`window`). Every ~6 hours, or when its verdict names stall reconnects, also run `stalls`.

**2. ASSESS.**
- `NORMAL`: one short line in chat. Comment on a `[Monitor]` card only when its own rows appeared.
- `LOOK`: read each reason. Tell **regression** (new since a release: group by `serviceContext.version`), **known/accepted** (the daily ElevenLabs mid-reply drop) and **noise** (a 2-session night window) apart before acting.
- **Within an hour of a release, check it is serving first.** `releases=` shows only the new version, and its change's own row has appeared at least once. Then move its card to Done.

**2b. RECOMMEND, every tick.** Run `recommend.py tick` and raise each RAISE row as in `recommend` step 1, then carry on; the item waits for the owner like any other hand-off. On the first tick after 09:00 PT (`recommend.py due`), also send the daily digest.

**3. TRIAGE.** For anything real, find the mechanism before writing a card. Trace one affected session end to end by its `transaction` (the same id reaches IntelligenceService logs) and read the agent code on the path. Prefer a card that names the cause and the file over one that names the symptom. Reuse an existing card if one covers it.

**4. FIX.** Card first, then code:

- **Agent bug:** an ENG card under the voice-bugs parent the owner named, with the Bugs label, a FAST estimate and the owner assigned. Tech debt goes under the current cycle's Quality / Tech Debt card.
- **App-side bug:** the agent-side card carries the spec, and the app's owners take it from there (through the coordinating session when there is one). The agent may still need its half of the change. A client contract such as the transcript format, `tool_call` data messages or participant attributes moves on both sides together, so say which side ships first.
- **Where the fix happens:** in a new worktree at `<repo>/.claude/worktrees/<slug>`, never the shared checkout. Concurrent sessions use these trees.
- **The agent repo's own rules govern the change.** Its `CLAUDE.md` and `.claude/rules/` cover format-check, lint, tests, logging, error codes and config. Read them; don't restate them here.
- **Pushing from an agent-repo worktree:** put the main clone's `venv/bin` first on `PATH`, because the pre-push pylint resolves off `PATH`.
- **A missing permission is reported, never worked around:** name the role, the account and the command.

**5. PR.**
1. Open it as a **draft**. Trigger BugBot (`bugbot run` as its own comment) and wait until CI is green and BugBot is clean on the **current head**.
2. Answer every BugBot finding in its thread with what changed and the commit sha (or why not), then resolve the thread.
3. Mark it ready, comment on the card, and hand off **once**: repo#, URL, card, summary, risk, merge order. Then stop. The owner merges.

**6. RELEASE (agent only).** After the owner merges:

1. Move the card to To Be Released (re-estimate with FAST).
2. Invoke `eng:release-version` `plan` and show the exact version.
3. **Ask for the go in chat and wait.** Size by user impact: an inert fix is a patch, an SDK bump is a minor.
4. On the go, run the release and `verify`. Read the per-job result: the overall `success` can land while `deploy` is still running.

When `.env.sample` gained keys:

1. Add them to the local `.env`.
2. Run `make sync-secrets` from an up-to-date master and confirm the key is in its list.
3. Apply only on a separate go.

A LiveKit Cloud deploy drains: in-flight sessions finish on the old image, so a release does not drop live callers. Prefer **separate restarts for changes that touch the same path**, so each effect stays attributable in the logs.

**7. Back to MONITOR** on the new release. The next tick's step 2 confirms it.

## Standing defaults

- **One tick, one fix in flight.** Don't open a second PR while the first waits on review, unless the owner asks.
- **Measure before tuning.** Endpointing, VAD and turn-detector values have measured trade-offs recorded in the agent's `.env.sample` and `performance` rule. Re-derive the number; never cite a pooled figure.
- **Idempotent.** Before creating a card, search for an existing one. Before commenting, check the card's last comment isn't already this finding.

## The legs

| Leg | Where |
|---|---|
| Monitor | `eng:monitor-livekit` |
| Recommend evidence | `scripts/recommend.py` (this skill) + `eng:analyze-livekit-latency` |
| Daily snapshot (optional) | `eng:report-livekit-health` |
| One user's complaint | `eng:investigate-voice-issue` |
| Live log tail | `eng:investigate-livekit-logs` |
| PR review | `bugbot run` on the PR (`eng:bugbot-review`) |
| Release | `eng:release-version` |
| Drive the agent for QA | `qa:talk-to-aurie` (use a real user id, and pin the room) |
