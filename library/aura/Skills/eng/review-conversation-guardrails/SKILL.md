---
name: review-conversation-guardrails
description: >
  Walk a human reviewer through today's conversation guardrail email decisions, one case at a
  time, in this session: build the queue of active-risk alerts in #guardrails-conversation that
  still need a send / no-send call, show each case's key turns word for word (never in Slack),
  suggest send or no send against the written review rules, take the reviewer's call, and post
  the decision on the alert thread for Customer Support. When the reviewer is
  unsure it tags Talia instead. Built to train new reviewers and mirrors Talia's daily guardrail
  review routine. Trigger on "review guardrails", "guardrail daily review", "review the
  guardrail drafts", "send or no send", "do the guardrail email decisions", "walk me through
  the guardrail queue", or "guardrail review training". Do NOT use to triage an alert with no
  triage reply yet (`eng:investigate-conversation-guardrail`), to sweep the channel unattended
  (`data:run-conversation-guardrails-analysis-cadence`), or for #guardrails-memory-ingestion.
metadata:
  version: "1.2.0"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Compliance/Privacy   # nothing a user said ever goes into Slack
    - Tools/Slack          # #guardrails-conversation + the @cs group mention
---

# review-conversation-guardrails

**Purpose: make sure people in crisis receive information about how to get support from a human.** Every active-risk alert in `#guardrails-conversation` arrives with a triage reply and a draft CS email from [`eng:investigate-conversation-guardrail`](../investigate-conversation-guardrail/SKILL.md). Nothing is sent until a human decides. This skill walks that human through the decisions, one at a time, in this session.

It is the email half of Talia's daily guardrail review, written down so a new reviewer can run it. The reviewer reads conversations **here, never in Slack**, and answers one item at a time.

**The rules are [`references/review_rules.md`](references/review_rules.md)** — read it in full before Step 1. It is also published for reading as *Guardrail Review Training — send or don't send* under Guardrails & Safety in Notion; when the two differ, the repo file is the one this skill applies.

## What the reviewer's reply authorises

The reviewer starts this skill to post their decisions, so a reply of `send`, `no send` or `talia` on an item **is** the go-ahead to post that item's fixed text below. Nothing else is pre-approved:

- A **corrected draft** is posted only after the reviewer confirms the new text, and only when it passes [`references/email_template.md`](references/email_template.md) (its tone, privacy and checklist rules).
- Never contact a user, never edit a prompt, never post anything in Slack other than the texts in Step 4.
- **Slack carries no conversation content.** Never quote, paraphrase or summarise what anyone said in anything posted. Key turns are shown only here.

## Fixed facts

- Channel: `#guardrails-conversation` `C0C7HEZ7CEA` — private; the reviewer must be a member. Each alert is a k8bot parent message (`*Conversation guardrail triggered*`); the Aura AI bot posts the triage reply in its thread.
- Customer Support group mention: `<!subteam^S0AJ2TR6DB3>`. Copy it exactly — a typed `@cs` notifies nobody.
- Talia's Slack ID: `U0C6BHV1E58` (the escalation path).

## Step 0 — Prerequisites

1. **Slack as the reviewer.** Reads go through [`shared:read-slack`](../../../shared/skills/read-slack/SKILL.md) and posts through [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md) with `--as-user`, so each decision appears under the reviewer's own name — the record of who decided. Both prefer the reviewer's Slack connector; the token fallback is `SLACK_USER_TOKEN` (posting) and a token whose user or bot is in the channel (reading).
2. What `eng:investigate-conversation-guardrail` Mode C needs to show the key turns: `AURA_API_TOKEN` (required), `OPENAI_CONVERSATION_API_KEY` (fallback transcripts) and `gcloud` (handoff categories). A missing one is set up with `shared:setup-ainative`, then a Claude Code restart.
3. Read [`references/review_rules.md`](references/review_rules.md), [`references/outreach_levels.md`](references/outreach_levels.md), [`references/resource_map.md`](references/resource_map.md) and [`references/email_template.md`](references/email_template.md).

If Slack is unreachable, stop and say so — the queue cannot be built and nothing can be posted.

## Step 1 — Build the queue

Read `C0C7HEZ7CEA` for the last **7 days** (or the window the reviewer gives) by invoking `shared:read-slack` in `history` mode with `--oldest` set to the window start — it paginates the whole window, so no waiting alert is cut off by a page size — then read the thread of every k8bot alert in `replies` mode. An alert is **waiting for a decision** when all of these hold:

- its triage reply starts with `:rotating_light: active risk` and contains a `*Draft email` section;
- no human has replied on the thread tagging `<!subteam^S0AJ2TR6DB3>`, saying no email is needed, or tagging Talia — someone already decided or escalated;
- there is no ✅ reaction on it (the mark that CS sent the email).

Also count, without queueing:

- alerts with **no triage reply yet** — they get one on the next cadence tick;
- `:warning: … not analysable` replies with no human reply — **offer to tag Talia** on each (Step 4's escalation text), since there was no transcript to judge.

☑️ *resources already in place* and ✅ *no active risk* replies need nothing (`review_rules.md` → *Which triage replies need you*).

**Order:** alerts with a 🚨 HIGH PRIORITY tag first (level 1), then oldest first. When two waiting alerts belong to the same user (same UID prefix), put them next to each other and say so — the reviewer decides the earlier one first and never sends two.

Open with one line: `<n> email decisions waiting (<p> high priority). Reply "send", "no send" or "talia" to each.` If the queue is empty, say `Nothing to review today` with the not-analysable and no-triage-yet counts, and stop.

## Step 2 — Prepare one item

Show **only the current item**, then wait. For each:

1. **Get the key turns.** Invoke `eng:investigate-conversation-guardrail` with the Skill tool in **Mode C**: the alert's Slack URL plus "show the key turns". Mode C is read-only and posts nothing; it reads the triage reply's `Key turns:` ranges and prints those turns. If the conversation has grown since triage (more turns than the reply's total), read the new turns too.
2. **Work out the suggestion** — the rules' answer for each of `review_rules.md`'s five steps. Use:
   - step 1 — the key turns; for `subject: other` / third-party cases also *When someone else is at risk*;
   - step 2 — the `📧 Last CS email` line (open its linked thread for the date and topic) and any other waiting draft for the same UID;
   - step 3 — crisis-reply check (a) on the triage reply, confirmed against the key turns, and whether each topic's own line for the country (`resource_map.md`) was given; the triage reply's `Level:` line (`outreach_levels.md`); whether the risk came back after a line was given;
   - step 4 — the draft's resource list against the topic triage and against `resource_map.md` for the country on the UID line. Also confirm an `email:` is on the UID line — without it CS cannot send.

## Step 3 — Walk the reviewer through it

**Item format:**

```
Email <i> of <n> · <category> · <high priority / daily review> · <country> · <alert link>
<one plain line: who is at risk and the situation>

<key turns, word for word, each marked User / Aurie (coach) / Aurie (guardrail reply) — usually 4–10 turns>

Line given in the chat: yes (#<n>, <line>) / no
Last CS email: <date and topic, or none in 14 days>
Draft: <looks right / the problem: wrong country, missing line, line that doesn't fit, no email address>

Suggested: <send / no send / talia> — <the step that decides it, one line: "step 3, no line was given in the chat">

Your call: send · no send · talia (or describe a change to the draft)
```

Suggest `talia` when the rules genuinely don't settle it.

**Always offer all three calls — `send`, `no send` and `talia` — on every item, whatever the suggestion.** When the reply is offered as clickable choices (e.g. a question with options), those three are always the options, with the suggested one first; the reviewer can still type a draft change, a question or `skip`. Never drop the call the suggestion argues against: a choice the reviewer cannot pick is a decision made for them. Then wait for the reviewer's reply:

- **`send`, `no send`, or `talia`** →
  - **matches the suggestion** → post (Step 4) and move on.
  - **differs from it** → say which step points the other way and why, in plain words, and ask once: `Post your call, or change it?` The reviewer's call stands — post what they confirm. A reviewer who is still unsure takes `talia`.
- **A change to the draft** ("add RAINN", "drop Poison Control", "make it softer") → write the full corrected email, then **check it against [`references/email_template.md`](references/email_template.md)** — its fixed wording, *Tone rules*, *Privacy and safety rules*, *What the email never says* and *Final validation checklist* — before showing it. Rewording is allowed; breaking a rule is not. Show the draft with one line per check the change touched (`checklist 5 — resource type label still accurate`). If the change the reviewer asked for cannot be written without breaking one, say which and why, and offer `talia` — never post a draft that breaks one. Post it only after they confirm the text. Then ask for the send call.
- **A question** → answer it from `review_rules.md`, then ask again. Never act on a question.
- **`skip`** → leave it for the next run, untouched.

Keep every message short and plain — the reviewer is learning.

## Step 4 — Post the decision

**Re-read the thread immediately before posting** (`shared:read-slack`, `replies` mode). If someone else has decided or escalated since Step 1, do not post: tell the reviewer and move on.

Post one reply on the alert's thread by invoking `shared:post-to-slack` with `channel=C0C7HEZ7CEA`, `thread_ts=<the alert's ts>`, **`--as-user`**, and **`--footer eng:review-conversation-guardrails --footer-skill-md <this skill's base directory>/SKILL.md`** (the footer is stamped by the script — never type it). Skip its draft-and-confirm gate: the reviewer's reply in Step 3 is the confirmation. The body is exactly one of:

| Reply | Text |
|---|---|
| send | `<!subteam^S0AJ2TR6DB3> Reviewer approved the draft email above. Please send it as written.` |
| no send | `<!subteam^S0AJ2TR6DB3> No email needed here.` |
| talia | `<@U0C6BHV1E58> Reviewer unsure — please triage.` |
| corrected draft | `Reviewer update — corrected draft (<what changed, categorical>):` + the full email, as its own reply **before** the send text |

The escalation carries no `@cs` tag, so nothing goes to a user until Talia decides. Confirm each post landed, then show the next item.

If a post fails, say so, keep the item at the front of the queue, and do not move on as if it posted.

## Step 5 — Close

One line: `Done: <s> sent, <c> corrected and sent, <x> no email, <t> sent to Talia, <k> skipped. <a> not analysable, <u> waiting for triage.`

Then list any item where the reviewer's call differed from the suggestion, by alert link and step only — that list is what Talia reviews with a trainee.

## Edge cases

- **Duplicate pointer** — a triage reply that only points to an earlier alert for the same conversation is not an item.
- **The reviewer stops partway** — unanswered items stay undecided on Slack, so the next run shows them again.
- **Mode C fails** (missing token, no transcript) — do not decide from the summary line. Say what failed and offer `talia` for that item.
- **A HIGH PRIORITY tag arrives mid-review** — it goes to the front of the queue after the current item.

## When to use this vs siblings

- **This skill** — a human making the send / no-send calls on drafts that already exist.
- [`eng:investigate-conversation-guardrail`](../investigate-conversation-guardrail/SKILL.md) — triages one alert and writes the draft; its Mode C is how this skill shows the key turns.
- [`data:run-conversation-guardrails-analysis-cadence`](../../../data/skills/run-conversation-guardrails-analysis-cadence/SKILL.md) — the unattended sweep that gets every alert a triage reply.
- [`eng:investigate-memory-ingestion-guardrail`](../investigate-memory-ingestion-guardrail/SKILL.md) — the memory-ingestion channel, not covered here.

## References

- [`references/review_rules.md`](references/review_rules.md) — the five steps, the someone-else-at-risk rules, the topic triages, the quick card.
- [`references/outreach_levels.md`](references/outreach_levels.md) — the three outreach levels the triage assigns, and which cases always get an email (a tested copy of `eng:investigate-conversation-guardrail`'s; see `docs/canonical-sources.md`).
- [`references/resource_map.md`](references/resource_map.md) — country × category lines for the draft check (a tested copy; see `docs/canonical-sources.md`).
- [`references/email_template.md`](references/email_template.md) — the email template every corrected draft must pass (a tested copy of `eng:investigate-conversation-guardrail`'s; see `docs/canonical-sources.md`).
