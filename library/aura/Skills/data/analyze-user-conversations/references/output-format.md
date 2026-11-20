# Output format

What `analyze-user-conversations` returns and where the optional side-effect outputs land. Recaps Steps 5–9; referenced from `SKILL.md`.

The skill's primary return value is a single Markdown document organised exactly as listed in Step 5 (Context → Friction → Positives → Patterns → Follow-up → Sources). Length depends on `time_window`:

- `last_conversation` → 1 friction section, ~30 lines total.
- `7d` → typically 1–3 conversations, ~50–80 lines.
- `30d` → typically 3–8 conversations, ~80–200 lines.

Always lead with a one-line headline above the **Context** section so the caller can grab a TL;DR for Slack:

> *Headline:* `<UID-prefix>` cancelled citing "not conversational" — analysis surfaces 4 friction moments across 3 conversations, mostly tied to scripted multiple-choice replies on emotional bids.

Step 6 returns the report inline. Step 7 (Mode A only) optionally posts a 4-section draft (Headline / What worked / What didn't / Hypothesis to validate) back to the source Slack thread on user approval. Step 8 records the analysis in `AI Knowledge → Learnings → Conversations` **by default for a conversation-pivot** (a single specified/rated conversation); a user-pivot run does not bulk-record (`--no-record` / `record: false` to skip; `record: ask`/`true` to opt a user-pivot in) — see the [`Learnings/Conversations` schema](../../../../shared/skills/record-knowledge/references/schemas/conversations.md) for the row shape. Step 9 (Mode A and B, conversation-pivot only) optionally saves the conversation as a runnable eval case in a Braintrust dataset (default `Feedback Conversation`) on user approval — see [`data:save-to-braintrust-dataset` SKILL.md](../../save-to-braintrust-dataset/SKILL.md) for the row contract.
