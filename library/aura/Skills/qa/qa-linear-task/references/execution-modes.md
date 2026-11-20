# Execution modes and routing

Read by `qa-linear-task` in Steps 4 and 5.

## The three modes

All three follow the same rules: read the checklist from the card, tick as you go, file
QA Feedback cards on failure, and report open, moved and failed at the end. They differ only
in where the work runs.

### This session

Run Step 5 inline. Use it when:

- any open case is `device` or `human`: the user acts, and you watch logs, Mixpanel and
  Bugsnag live and tick;
- the pass is short (roughly ≤10 automatable cases);
- the user wants to watch.

### Subagent

Spawn **one** `general-purpose` subagent in the background with the prompt below. One agent
per pass, not one per case: the cases share a build, a simulator and an account, and
parallel agents would fight over them. Give it only the automatable cases.

**One runner per card at a time.** Linear has no conditional update, so two runners ticking
the same card can overwrite each other's ticks. `qa_card.py` re-reads the card just before it
writes and retries on a change, but that narrows the window rather than closing it. So while
the subagent runs, the main session does not execute cases on that card. `device` / `human`
cases either wait until the agent returns, or go to a follow-up card (Step 6) first, where
the main session can run them in parallel.

```
You are executing QA card <QA-ID> for <TASK-ID> (PR <url>). The card's checklist is the
source of truth: read it with
  python3 <S>/qa_card.py checklist --issue <QA-ID>
Run ONLY these open cases: <ids>. For each, run it, judge it against the expected result
written on the card, then:
  pass → python3 <S>/qa_card.py tick --issue <QA-ID> --case <id>   (immediately, one at a time)
  fail → create a sub-card of <QA-ID> labelled "QA Feedback" (label id <id>), title
         "QA: <symptom>", body = repro steps, expected vs actual, evidence. Do not tick.
  cannot run → leave unticked and say why.
Setup: <setup section from the card>. Test accounts only; never write an end user's email,
name or full UID to Linear.
Do not change any other part of the card, do not move card states, do not touch the PR.
Return: passed ids, failed ids with card links, not-run ids with reasons, surprises.
```

`<S>` is the absolute path of this skill's `scripts/` directory. Resolve
`${CLAUDE_PLUGIN_ROOT}` before handing it over. When the agent returns, re-read `checklist`
from the card rather than trusting the summary, then continue at Step 6.

### New session

The card is the whole handoff, so the new session needs one line and nothing else from this
conversation:

```
Run the QA card <QA-ID> with qa:qa-linear-task (task <TASK-ID>, PR <url>). Execute the open
cases in this session.
```

In the Claude desktop app, offer it as a background-task chip (`spawn_task`, title
`QA <QA-ID>`, with the line above as the prompt, plus the repo path if the change lives
elsewhere). Anywhere else, print the line for the user to paste into a fresh session. Either
way, stop here. Don't also start executing in this session, or two runners will tick the
same card.

## Routing

Send each case to the tool that owns its surface. Don't rebuild these.

| Case is about… | Use |
|---|---|
| Usage limits, voice/message caps, accrual after hang-up (iOS) | `qa:qa-aurie-usage-limits` |
| The Aurie voice agent itself (LiveKit worker, agent behaviour) | `qa:talk-to-aurie` |
| Aurie text replies / AI API behaviour | `qa:chat-with-aurie` |
| Aura (non-Aurie) chat prompter | `qa:chat-with-aura` |
| iOS app UI on the simulator | the iOS Simulator tool (attach → build → launch → inspect / tap / screenshot); speech by `say` + `afplay` through the Mac speakers |
| Web app UI | the built-in browser pane |
| Analytics events and properties | Mixpanel MCP (DEBUG → the pre-prod project), filtered on the test user |
| Backend calls and logs for the test user | `eng:search-gcp-by-user`, `eng:investigate-gcp-logs` |
| Errors reported during the pass | `eng:search-bugsnag-by-user` |
| Prompt or conversation quality | `eng:qa-prompt`, `eng:qa-conversation` |

When a case needs a tool this session doesn't have (no simulator, no device, no
credentials), it is a *cannot run here* case (Step 5.5), not a failure.
