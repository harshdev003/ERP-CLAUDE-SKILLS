# The `Follow-up` section of a review card

Shared by every per-entry feedback review that posts a card: cancellation reviews
(`investigate-cancellation-feedback`, `run-cancellation-feedback-analysis-cadence`) and
rating reviews (`investigate-user-rating`, `run-rating-feedback-analysis-cadence`,
`run-feedback-analysis-cadence`). Each of those skills carries an identical copy of this
file; edit all of them together.

`Signal` says what happened and what would have to be true across the corpus for it to
matter. `Follow-up` says **what someone could do about it next**, and who. It is the last
section of the card, after `Signal`.

## Format

```
*Follow-up*
• 🧠 Prompt: <prompter> · <the failing behaviour, cited to a turn> · candidate rule: "<draft text>" · eval: <this conversation + the turn where the expected behaviour should appear>
• 🛠 Product/eng: <the change> · owner: <area, e.g. practices rollout, voice, memory, paywall>
• 📊 Data check: <the query that tests the Signal hypothesis across the corpus>
• 💬 CS: <refund handling or win-back, when the entry calls for it>
```

- **Only the lines that apply, four at most.** A line with nothing concrete behind it is
  dropped, not padded.
- **Omit the whole section when nothing is actionable** — a price-only cancel with no
  usage, a positive rating with no marketing use, a thin rating with no typed feedback and
  nothing in the transcript. A missing section is a finding too: there is nothing to do.
- **`📊 Data check` carries the hypothesis's measurement.** `Signal` states the
  hypothesis; the query that tests it lives here, so it reads as a task.
- **Link the Linear card when one already covers it** (e.g. `→ PROD-6969`). **Never create
  one.** The section proposes; a human decides what gets ticketed.

## The `🧠 Prompt` line — a candidate, not a change

A single entry cannot justify editing a prompt that runs on all traffic: one session
carries no base rate (see `signal-scope.md` where the skill has it). So the rule this line
drafts is a **candidate** for the prompt engineer, paired with the `📊 Data check` that
would justify shipping it. Write it only when all of these hold:

1. **The failure is in the transcript**, cited to a turn — not only in the user's
   complaint. The user's words point at where to look; the transcript is the evidence.
2. **It is not designed behaviour.** Check the prompter's contract first (the sentinel
   ending a Quick Process session is the standing example of a design that reads as a
   failure).
3. **You read the current prompt text** for that prompter before drafting. If an
   equivalent rule already exists, write `rule exists but isn't followed: "<quote>"`
   instead of proposing a duplicate — that is a different fix (placement, emphasis,
   model, context) and the prompt engineer needs to know which one it is.
4. **The cause is the prompt, not the input.** Before writing a memory or context
   failure up as a prompt problem, check what the model actually received for that turn.
   `coach-prompter` gets only the last 20 messages of the current conversation, and
   summaries cover past conversations only, so a detail shared 60 turns earlier in the same
   conversation is simply absent. Count the `findSimilarInMemory` calls too. If the model
   never saw the information, the fix is in the context pipeline (`🛠 Product/eng`), not the
   prompt.

Draft the rule as one or two short imperative sentences that could be pasted into the
prompt as written. Name the prompter (`coach-prompter`, the voice prompter,
`quick-processing-sentinel-prompter`, …). The eval half is always in scope: the
conversation plus the turn by which the expected behaviour should appear is a Braintrust
case whether or not the rule ships. A candidate a human wants to take further goes to
`product:draft-prompt-candidate`.

## Worked examples

From #aurie-feedback, Sep–Oct 2026. Sensitive content is paraphrased, as on the card.

**Left over the "practices" format (power user, 66-day streak).**
- 🧠 Prompt: coach-prompter · keeps offering practices after the user says they don't want them · candidate rule: "If the user declines a practice or says they'd rather just talk, don't suggest another practice in this conversation; ask one open question about what's on their mind." Persist the preference to memory.
- 🛠 Product/eng: keep the daily two questions as an entry point, or a conversation-first option for existing users · owner: practices rollout.
- 📊 Data check: sessions per day before and after the change, for users whose sessions mostly started from the daily question; cancellations citing "practices" since the rollout date.
- 💬 CS: win-back outreach — auto-renew is off but the user still chats daily.

**Asked Aurie to slow down; got "shorter responses" (voice).**
- 🧠 Prompt: voice prompter · treats a speech-rate request as a length request · candidate rule: "If the user asks you to slow down in voice, don't shorten your replies; use short sentences with natural pauses, and don't promise a pacing change you can't make."
- 🛠 Product/eng: whether ElevenLabs speech rate can be exposed as a user setting · owner: voice.
- 💬 CS: a refund request before any cancellation, day 1 of an annual plan — a save opportunity.

**"It validates only" (self-identified clinician).**
- 🧠 Prompt: coach-prompter · eleven turns of reflection, no technique · candidate rule: "When the user asks for strategies or coping skills, or says validation isn't helping, give one concrete, named technique in your next reply, then check whether it fits." · eval: this conversation, technique expected by turn 3.
- 🛠 Product/eng: a preview or trial before the full charge — charged 57 seconds after signup · owner: paywall.

**Grounding offered again after it was rejected; the user's own message labelled clinically.**
- 🧠 Prompt: coach-prompter · candidate rule: "If the user rejects a grounding exercise, don't offer another one; ask what would help right now. Never describe the user's own messages in clinical terms."
- 🛠 Product/eng: the "permanent memory" ad claim against what memory delivers · owner: marketing.
- 📊 Data check: assistant turns conceding their own output was harmful, and the fraction with any downstream signal attached.

**Had to repeat herself; Aurie critiqued a text it had written.** The cause was input, not
prompt: the draft fell outside the 20-message window of the same conversation, and
`findSimilarInMemory` ran 0 times in 642 responses.
- 🛠 Product/eng: a rolling summary of the current conversation's earlier turns; tighten the memory-retrieval trigger · → PROD-6969.
- 🧠 Prompt: coach-prompter · candidate rule: "Before critiquing a message, check whether you helped write it; if you did, say so first."
