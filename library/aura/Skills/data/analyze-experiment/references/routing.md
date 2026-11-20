# Step 8 — routing the finished report

Where an experiment readout can land, and the rules for each destination: creating a new `Learnings/Experiments` row vs updating an existing one in place, the optional `#ai-product` post, and the confirm-before-write discipline.

All four options are side-effecting, so none of them fires without an explicit yes.

**8a. Resolve any disagreements with Slack first.** If Step 6.5 surfaced one or
more `⚠ Disagreement` blocks, ask the user how to handle each before routing
the readout. Offer three choices (pickable in any combination — e.g. "1 + 3"
files our analysis AND posts a question in Slack): **(1) Keep our analysis**
(file our verdict; `## Learning` documents the divergence; Slack untouched),
**(2) Keep the Slack analysis** (Slack's verdict drives `Status` and the `##
Learning` headline; our read is preserved as a "Minority view" footnote), or
**(3) Reply in Slack with questions / critique** (draft a threaded reply under
the `#wins` post or `#support-bot` end-note, show the user for approval, then
post via `shared:post-to-slack` with `thread_ts` = the original post's ts).

> See [`references/output-format.md`](output-format.md) → "Step 8 disagreement-resolution prompt" for the verbatim prompt text to show the user.

If the user picks **2**, the row's `Status` follows Slack's verdict and `##
Learning` opens with their framing; the page body still ends with a `##
Minority view` section containing our verdict, primary metric, lift math,
and the guardrail concern that drove our different read.

If the user picks **3**, the Slack reply is **separate** from the `Learnings/
Experiments` write — the user can pick **1+3** to file our view AND publicly
challenge, or **2+3** to defer in writing while still asking the question.

After resolving any disagreements, ask what they'd like to do with the
report. Offer these options (multiple can be picked):

1. **Nothing — keep it in chat.** Default if the user doesn't respond.
2. **Post to `#ai-product`** (`C04R4L67676`) as a parent `[experiment][analysis]` message with threaded per-experiment + summary replies.
3. **Reply in `#support-bot` or `#data-experiment`** threading each per-experiment section under the original `Experiment ended:` end-note (`thread_ts` = the Step 1 `message_ts`).
4. **File learnings to `Learnings/Experiments`** *(create mode default)* — invoke `shared:record-knowledge` to create a new row per experiment. Title-dedup first: if a same-`Title` row exists, switch to 4b.
4b. **Update existing `Learnings/Experiments` row** *(enrich mode default)* — invoke `shared:record-knowledge` with `action: update` and the Step 1c `page_id`.
5. **Create Linear tickets for follow-up experiments** — invoke `shared:add-linear-issues`, one issue per idea.

> See [`references/output-format.md`](output-format.md) → "Step 8 routing options — full mechanics" for the per-option execution detail (channel IDs, `record-knowledge` schema fields + Recipe C, footer, Title dedup). Payload shapes are under "Step 8 option 4b — `record-knowledge` update payload".

Phrase the ask as a single multi-select question, adapting the options text to the mode (create mode offers option 4 = new rows; enrich mode offers option 4b = update existing row(s), which is the default).

> See [`references/output-format.md`](output-format.md) → "Step 8 mode-specific routing asks" for the verbatim create-mode and enrich-mode question text.

Execute the chosen actions in sequence. For any Slack post the user hasn't
explicitly pre-approved, **show the drafted message and wait for approval**, then
post it by **invoking [`shared:post-to-slack`](../../../../shared/skills/post-to-slack/SKILL.md)**
(the slack skill owns the connection — don't call Slack directly). Never auto-post.
For Slack formatting rules (no markdown tables, use `*bold*` / `_italic_` / backticks,
always set `thread_ts`), see [`references/output-format.md`](output-format.md)
→ "Slack posting details (Step 8, options 2 & 3)".
