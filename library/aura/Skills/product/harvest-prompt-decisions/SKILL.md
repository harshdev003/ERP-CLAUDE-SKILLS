---
name: harvest-prompt-decisions
description: >
  Read Gate 1 and Gate 2 verdicts out of `#ai-loop-aurie-conversations` and write them onto the
  Notion rows, with the quote that produced each one. Sweeps every candidate waiting on a human,
  classifies the last human reply, and hands back anything it cannot read with confidence as
  `needs_judgment` rather than guessing. Use when the user says "harvest the decisions", "sweep
  the loop channel", "did anyone approve CONV-9", "read the gate verdicts", or "what did the
  team decide". Do NOT use to post a candidate for review (use `share-prompt-candidate`) or to
  set a status by hand (`manage-prompt-candidates`).

metadata:
  version: "0.2.3"
knowledge:
  reads:
    - Guide/Index                          # baseline routing aid (universal)
    - Learnings/Loop/Aurie Conversations Ideas   # the gates, the verdicts, the lifecycle
    - Tools/Notion                         # NOTION_TOKEN + the REST access pattern
    - Tools/Slack                          # SLACK_BOT_TOKEN + channel ids
  writes:
    - Learnings/Loop/Aurie Conversations Ideas
---

# harvest-prompt-decisions

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/harvest-prompt-decisions/scripts/harvest_prompt_decisions.py"

python3 "$S" sweep                                   # dry run — shows what it read
python3 "$S" sweep --confirm                         # records the verdicts
python3 "$S" classify "not approved for release" --gate 2   # offline, for calibration
```

A quiet channel is a no-op, and that is success rather than a failure.

## What it reads, and where it stops

Only rows waiting on a human: `shared` is at **Gate 1**, `evaluated` is at **Gate 2**. The row's status is what disambiguates a generic "approved" — the same word means *draft it* at one gate and *ship it* at the other.

The **last** human reply wins. A thread can change its mind, and the most recent word is the one the team stands behind. Bot replies are skipped, including the loop's own post.

## The verdicts

| Verdict | Gate 1 → | Gate 2 → |
|---|---|---|
| `approved` | `approved` | `in_review` |
| `rejected` | `rejected` | `held` |
| `deferred` | `deferred` | `held` |
| `wrong_change` | back to `proposed` | — |
| `changes_requested` | — | back to `approved` |
| `needs_answer` · `needs_judgment` · `no_decision` | **holds where it is** | holds |

Two of those rows carry the loop's shape rather than a parser detail:

**`wrong_change` is not a rejection.** "Right defect, wrong change" endorses the problem and declines the fix. Recorded as a rejection it would retire a defect the reviewer just agreed exists, so it sends the change back to `proposed` with the defect intact.

**Gate 2's approval is `in_review`, not `released`.** Approval means *approved to ship*, and only `ship-prompt-candidate` knows whether a release actually happened. Writing `released` here would also skip that skill's change-class guard — the one that stops an arm-routed candidate being booked as a full rollout.

**Gate 2's no-verdict is `held`, not `rejected`.** A prompt cannot sit dormant in a build the way a flagged feature can — the commit exists, production is untouched, and the candidate stays cheap to revive because the diagnosis is already agreed.

## The traps the parser is built around

Each was paid for in the Features loop's harvester, which is this parser's reference implementation.

**Every approval pattern matches inside its own negation.** "Not approved for release" *contains* "approved for release". An approval only counts when nothing negates the verb immediately before it, and a negated one is recorded as an explicit **refusal** — not as an absence of one, which would leave the row waiting on a reply it already has.

**A deferral contains an approval.** "Let's do it later" is checked before the approval patterns, or its first three words win.

**An approval that names the wrong gate is handed back.** A release approval on a row nobody has drafted is somebody in the wrong thread, or a row that is behind. Both are worth stopping on rather than resolving silently, so it comes back as `needs_judgment` naming the mismatch.

**A bare "approved" cannot pass Gate 2.** The thread still holds the Gate 1 approval that got the candidate here, and this skill reads the *last human reply* — so if nobody has posted the Gate 2 message yet, that Gate 1 approval is what it finds. Accepting it would ship a candidate on a decision made about something else. Gate 2 requires the explicit `approve for release`; Gate 1 does not, because nothing earlier in the thread can be confused with it.

> **Known gap.** Nothing posts the Gate 2 message yet — `share-prompt-candidate` posts Gate 1 only. Until that leg exists, a Gate 2 verdict has to be asked for by hand in the candidate's thread. The guard above is what makes that gap safe rather than silent: a sweep over a thread with no Gate 2 post returns `needs_judgment`, not a pass.

**A trailing question is not a decision.** The row holds at `needs_answer` — a thread waiting on an answer is not an undecided candidate, and treating it as one is how a thread goes quiet: nobody approves something they just asked about. Question marks inside links and mentions do not count.

`classify` runs the parser on one line with no Slack and no Notion, which is how to calibrate it before trusting a sweep.

## Self-contained, and what that costs

The verdict vocabulary and the negation handling are **ported from** `product:harvest-idea-decisions`, which stays the reference implementation. Skills here are self-contained by design, so this is a copy rather than an import. The cost is drift: two loops disagreeing about what "approved" means. When a bug is found in either parser, **fix both in the same change**, and when a case is added to `tests/test_prompt_decisions.py`, check whether it belongs in the Features suite too.

## Setup

`NOTION_TOKEN` and `SLACK_BOT_TOKEN`. `notion_rest.py`, `slack_read.py` and `slack_post.py` are per-skill copies of their canonicals.

## Related

- `share-prompt-candidate` — posts the thread this reads
- The gates and the decision protocol: `Learnings/Loop/Aurie Conversations Ideas`

## A verdict answers the question that preceded it

The loop asks more than once in one thread: Gate 1 opens it, Gate 2 replies into it, and a further Gate 2 ask follows further evidence. So the sweep reads only the human replies **after the loop's most recent ask**, identified by the skill name in its Tools/Slack footer rather than by `bot_id` alone — another integration replying in-thread is not the loop asking a question.

Reading the last human reply regardless of position reapplies the previous gate's answer to the new question, before anyone has read the new one. A thread with no recognisable ask falls back to everything after the parent, which is what a pre-footer thread looks like.

## A Gate 2 drop prints a register entry

`no because …` at Gate 2 holds the candidate — the Braintrust commit stays, production is untouched — and prints an entry ready for `references/register.yaml` in `product:prompt-knowledge`, carrying the candidate id, the prompter, and the quote that produced the decision.

A candidate dropped at Gate 2 was drafted, evaluated and read, which makes it the most expensive answer this loop produces; the reason is what stops the digest raising it again next month. The entry is printed rather than written: the register lands through a PR, and `reopens_if` is deliberately left blank, because only a human can say what would reopen the question and an entry without one is a prejudice with a version number.
