---
name: investigate-conversation-guardrail
description: >
  Triage one Aurie conversation guardrail alert from #guardrails-conversation: read the WHOLE
  conversation (every turn, guardrail replies and handoff categories marked), classify it as
  active risk, non-crisis guardrail or false positive, and on active risk run the crisis-reply
  check — did the person get a way to reach a real human (988 or the right line for their
  country), did Aurie stay with them — then draft a location-aware CS email for human review.
  Posts one threaded reply; never sends to the user. Accepts a k8bot alert Slack URL, or a
  user_id plus conversation_id. Trigger on "investigate conversation guardrail", "conversation
  guardrail triggered", "triage this conversation guardrail alert", "did this crisis
  conversation get 988", or a pasted #guardrails-conversation Slack URL. Do NOT use for memory-
  ingestion guardrail alerts (`eng:investigate-memory-ingestion-guardrail`), rating analysis
  (`data:investigate-user-rating`), or changing the handler prompt (`eng:update-intelligence-
  prompt`).

metadata:
  version: "1.13.1"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Compliance/Privacy   # UID prefix only, never echo conversation content into Slack
    - Tools/Slack          # channel directory + bot-membership requirement + post format/footer
    - Tools/Aura API       # GET /users/:userId, POST /ai/conversations/export/:userId
    - Tools/OpenAI         # Conversation [Production] Responses reads (fallback transcript source)
    - Tools/GCP            # gcloud logging read — handoff category per turn
---

# investigate-conversation-guardrail

Triage for one **conversation** guardrail alert. When a live Aurie turn matches a guardrail condition, Intelligence Service's `handoffToGuardrails` tool hands the turn to the Guardrails Handler prompter, whose reply is what the person sees. Intelligence Service posts **one** k8bot alert per conversation, on its **first** handoff ([AuraHealth/IntelligenceService#569](https://github.com/AuraHealth/IntelligenceService/pull/569), [PROD-7238](https://linear.app/aura-health/issue/PROD-7238)). Later handoffs are never posted, so this skill always reads the **whole** conversation.

One alert in, one threaded reply out:

- **Active risk** — crisis-reply check (PASS/FAIL per check, turn references only) and an outreach level from [`references/outreach_levels.md`](references/outreach_levels.md): **1 high priority** or **2 daily review** get a CS outreach draft for human review and tag Diana, the guardrail reviewer; **3 no email** gets a ☑️ reply and tags nobody. Harm to others (category 2) adds a harm-to-others check line and uses its own email (`email_template.md` → Category 2 body).
- **Duplicate alert** for a conversation already triaged — a one-line pointer to the first thread.
- **Non-crisis guardrail** or **false positive** — a short reply, no draft.
- **Not analysable** (no transcript) — a reply that hands it to a human. Never a no-risk verdict.

The skill never sends the email and never tags Customer Support. A human reviewer approves every draft, and only then is CS tagged to send it.

## Why the crisis-reply check is the point

In one week (889 handoffs, 506 conversations, 147 with a `crisis` handoff), 17 people who said they wanted to die or disappear, or that they were in danger, got a guardrail reply with no crisis line; 11 never got 988 or any emergency number all week; one person disclosing suicidal thoughts, self-harm urges, domestic violence and trafficking got "Okay. We can leave that there." The Guardrails Handler's own rules explain part of it: it only escalates on the *current* message, its crisis templates hard-code US numbers (988/911), and its passive-death-wish path only asks a safety question. So the classification here is made on the whole conversation, and the check judges what the person actually heard. Rules: [`references/crisis_reply_check.md`](references/crisis_reply_check.md).

## Inputs

- **Mode A — Slack URL** to the k8bot alert in `#guardrails-conversation` (`https://<workspace>.slack.com/archives/<channel>/p<ts_no_dot>`). Primary path.
- **Mode B — direct ids:** `user_id=<firebase-uid> conversation_id=<uuid>` and optionally `transaction=<uuid>` (marks the alert turn). For paging, testing, or an unreachable message.
- **Mode C — reviewer reads the key turns.** An alert URL plus "show the key turns" (or "show me the conversation", "what did they say"). Run by a **human reviewer in their own Claude Code session**, never by the cadence. Resolve the ids as in Mode A, read the triage reply on the thread (`shared:read-slack`, thread mode) for its `Key turns:` ranges, then run Step 3 with `--turns <ranges>` (`--turns key` when the thread has no triage reply yet) and show the reviewer the output as-is. **Read-only and local: post nothing, tag nobody, write no file outside the session's temp directory.** This is the only place the words are shown — the Slack reply carries turn numbers only. Then stop.

The channel is `#guardrails-conversation` (`C0C7HEZ7CEA`), a **private** channel restricted to the reviewers who action these alerts. The `aura_ai` bot must be a member to read it and reply, and because it is private the bot token needs `groups:history` / `groups:read`; if the bot is not a member, reads return `not_in_channel` and a human must `/invite @aura_ai`. `Tools/Slack` in AI Knowledge is the canonical channel directory.

## Workflow

### Step 0 — Load context

1. Make sure `retrieve-knowledge` has pre-fetched the declared reads (`Compliance/Privacy`, `Tools/Slack`, `Tools/Aura API`, `Tools/OpenAI`, `Tools/GCP`, `Guide/Index`). The privacy rules govern Step 6.
2. Read the six bundled references — they are the source of truth:
   - [`references/classification_rubric.md`](references/classification_rubric.md) — active risk / non-crisis guardrail / false positive, the seven categories, minors, at-risk subject
   - [`references/crisis_reply_check.md`](references/crisis_reply_check.md) — checks (a) and (b), what counts, what does not, output lines
   - [`references/outreach_levels.md`](references/outreach_levels.md) — the three outreach levels: who is tagged and whether an email is drafted
   - [`references/email_template.md`](references/email_template.md) — category-matched email assembly, tone/privacy rules, validation checklist
   - [`references/resource_map.md`](references/resource_map.md) — country × category lines, emergency numbers, coverage gaps, global backstops
   - [`references/trigger_reasons.md`](references/trigger_reasons.md) — the trigger-reason list, its attributes and the exact line format

### Step 1 — Resolve the input

**Mode A.** Parse the URL: `channel_id` is the segment after `/archives/`; `message_ts` is the `p<digits>` slug with a dot before the last 6 digits (`p1779144885524169` → `1779144885.524169`). Fetch the parent message by **invoking [`shared:read-slack`](../../../shared/skills/read-slack/SKILL.md)** in `message` mode. The k8bot body is:

```
*Conversation guardrail triggered*
*User:* <firebase-uid>
*Transaction:* <transaction-id>
*Conversation:* <conversation-id>
*Prompt:* <prompt-file>   ← optional, see below
_First guardrail trigger in this conversation. Later triggers are not posted, so review the whole conversation._
```

Extract `User`, `Transaction` and `Conversation` with a plain regex that takes each **whole** value up to whitespace / end of line (`\*User:\*\s*(\S+)` and the same for the other two) — never a character class like `[A-Za-z0-9]+`, which silently truncates an id containing `:`, `-`, `_` or `.` and sends every later lookup to the wrong account. The body carries **no category** — that comes from Step 3. Keep the `*Prompt:*` value verbatim (`\*Prompt:\*\s*(\S+)`): it is the experiment-arm prompt file that fired (e.g. `coach.coachPrompterGuardrails.a.txt`), so an A/B test on the coach prompt can be counted per arm. Intelligence Service prints the line **only when an arm swapped the prompt body** — no line means the prompter's base file ran (control, a user outside any experiment, or an alert from before the line existed): record `base`. If `User` or `Conversation` is missing, say "couldn't parse the k8bot body — use Mode B" and stop. Keep `channel_id` + `message_ts` for Step 6.

**Mode B.** Use the ids as given.

### Step 2 — Confirm prerequisites

```bash
test -n "$AURA_API_TOKEN" && echo "AURA OK" || echo "AURA MISSING"
test -n "$OPENAI_CONVERSATION_API_KEY" && echo "OPENAI OK" || echo "OPENAI MISSING (fallback source unavailable)"
command -v gcloud >/dev/null && echo "GCLOUD OK" || echo "GCLOUD MISSING (no handoff categories)"
test -n "$GCP_KEY" && echo "GCP_KEY OK" || echo "GCP_KEY MISSING (handoff categories fall back to your gcloud login)"
test -n "$SLACK_BOT_TOKEN" && echo "SLACK OK" || echo "SLACK MISSING (Mode A cannot re-check the thread — do not post)"
```

`AURA_API_TOKEN` is effectively required: it reads the stored transcript (`POST /ai/conversations/export/:userId`, a privileged route) and `GCP_KEY` reads the llmCall ledger from BigQuery (`llm.llm_call`). `OPENAI_CONVERSATION_API_KEY` (a `Conversation [Production]` service-account key — Admin keys cannot read stored responses, PROD-3572) powers the fallback for conversations the export excludes. `gcloud` supplies the handoff category per turn. The script runs it as the `GCP_KEY` service account through the bundled `scripts/gcloud_sa.py`, which is not subject to Google's reauth policy, and uses your own gcloud login only when `GCP_KEY` is unset. Without either, the categories read "unknown", the warning names the credential that failed, and everything else still works. A missing token is walked through with the `update-config` skill, then a Claude Code restart. Cowork strips host env ([PROD-1305](https://linear.app/aura-health/issue/PROD-1305)) — paste-export per session.

### Step 3 — Fetch the whole conversation

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-conversation-guardrail/scripts/fetch_conversation.py \
  --conversation-id <conversation-id> --user-id <firebase-uid> --transaction <transaction-id>
```

Add `--json <path>` to keep the structured report. Deterministic and read-only. Name the shell variable `USER_ID`, never `UID` (readonly in bash, expands to the OS user id).

**How the transcript is built — and why** (chosen over `fetch_logs.py` and over `GET /ai/conversations/:id`):

| Source | Gives | Role |
|---|---|---|
| `POST /ai/conversations/export/:userId` | The verbatim user + assistant messages from the `dialogue` table — exactly what the person saw, opener and guardrail replies included, in order | **Primary transcript** |
| llmCall ledger, BigQuery `llm.llm_call` by `conversationId` | The prompter behind each assistant message (`guardrails-handler-prompter` on a handoff turn) and the turn's `transaction` | **Attribution** — joined on the assistant message's dialogue id |
| `gcloud logging read` (`component="handoffToGuardrails"`) | `category` + `transaction` per handoff | **Category per turn** — joined on `transaction` |
| `GET /v1/responses/{id}` + `/input_items` per ledger row | The reply and the user message for each turn | **Fallback** when the export excludes the conversation (incognito, deleted) and for ledger turns missing from it |

`fetch_logs.py` was rejected because it rebuilds a conversation from the *latest* call's input — about the last 10 turns. On the test conversation it returned 10 of 26 turns and no handler turn at all. `GET /ai/conversations/:conversationId` was rejected because it 403s for any caller who is not the conversation's owner. The handler runs in the same OpenAI project as coach and quick-processing (`Conversation [Production]`) — a handoff never switches project — so the fallback reaches its replies with the same key.

The report gives: the transcript source and warnings; the **Aura user record** (`givenName`, `email`, `countryCode` → resource-map key, `timezone`); a **Guardrail-handled turns** table (turn, time, prompter, handoff category, which one is the alert turn); a **Resource / drop-topic signals** table (hints for Step 5); the **whole transcript** numbered `#1…#N`; and any ledger turns absent from the stored transcript, numbered on after it (`#N+1…`, marked `absent from stored transcript`) and included in both tables — a removed guardrail reply was still seen by the person, so it is judged like any other turn. **When the report opens with a `TRANSCRIPT INCOMPLETE` banner** (`transcript_complete: false`), turns may be missing: the ledger was unreadable (so no window and no check for missing turns), some turns would not fetch, or GCP logged a handoff whose reply is in no source (an orphan handoff). Classify on what you have, and carry the banner's reasons into the Step 6 incomplete-transcript line — the verdict is provisional.

**Exit 3 = NOT ANALYSABLE** — no source produced a transcript. Go straight to the not-analysable reply in Step 6. Never post a no-risk verdict without a transcript.

### Step 4 — Classify the conversation

Apply [`references/classification_rubric.md`](references/classification_rubric.md) to the **whole** transcript, not only the alert turn:

1. Read every turn, including those after the alert and the absent-ledger-turn section.
2. Decide **A (active risk)**, **B (non-crisis guardrail)** or **C (false positive)** on the most serious thing the person expressed anywhere. Passive death wishes ("I want to disappear") are category 1. Ambiguity fails toward A. A resolved past, a clear denial of an ambiguous statement, speculation about someone else, or relationship conflict without danger is not A on its own: apply the rubric's *Not active risk on their own* section, exceptions included.
3. On A, pick the category (1–7) and note the turn(s) where the risk was expressed — turn numbers only.
4. Note the minor flag if the person appears to be under 18.

**Step 4.6 — At-risk subject.** On A, decide who the resources are for: the person (`{audience_suffix}` empty), a third party they care about (`, for you or for anyone you care about` + the country's caregiver line), or unclear (treat as third party). A minor in an adult's care → caregiver framing + the safeguarding flag in Step 6.

**Step 4.5 — Harm-to-others check (category 2 only).** Answer each from the whole conversation, with turn numbers, `yes` / `no` / `unknown`:

| Item | `yes` when |
|---|---|
| target | the person is identifiable — named, or a role that points to one person ("my ex", "my boss") |
| plan | how, where or when they would do it |
| means | access to a weapon or method, or a weapon named in the statement itself ("shooting him") — a named weapon is `yes (named, access unknown)` |
| timeline | a time or occasion ("tonight", "when I see him Friday") |
| escalation | the statement recurs or sharpens in later turns |
| denied | the person plainly said they will not act on it, and nothing later contradicts that |
| close relationship | the target is a current or former partner, spouse, or a family or household member — adds the row-2a line to the email (`email_template.md` → *Category 2 body*) |

Write it as one categorical line — never the words, never the target's name or relationship:
```
Harm-to-others check: target <yes|no> #<n> · plan <…> · means <…> · timeline <…> · escalation <…> · denied <yes #<n>[, #<m>]|no> · close relationship <yes|no|unknown>
```
The denial does not change Step 4: an explicit statement stays A (rubric, *Not active risk* §2). It goes on the reply so the reviewer sees the shape of the risk without opening the transcript; it never withholds the draft or lowers the level.

**Step 4.7 — Trigger reason (every outcome).** Apply [`references/trigger_reasons.md`](references/trigger_reasons.md) to the user message that triggered the alert (with the one before it): one reason from its list, the subject (`self` / `other` / `unclear`), what the person asked for (`professional-help` / `clinical-advice` / `none`) and whether they are already in care. It records why the guardrail fired, for `data:analyze-conversation-guardrails`; it never changes Step 4's outcome, the check, the level or the draft. The line goes on every Step 6 format.

### Step 5 — Active risk: crisis-reply check, then the draft

Skip this step on B or C.

**5a. Crisis-reply check.** Apply [`references/crisis_reply_check.md`](references/crisis_reply_check.md). Use the signals table to find candidate turns, then read them in context. Produce exactly two lines — check (a) way to reach a human, check (b) stayed with the person — each PASS or FAIL with turn numbers and a categorical reason. Judge (a) against the person's country from the Aura record: 988/911 for someone in the UK is a FAIL (`wrong-country line`). **Any FAIL is a product-safety finding** and is flagged in Step 6. A PASS alone never removes the draft: level 3 needs the topic's own lines (5a′).

**5a′. Outreach level.** Assign level 1, 2 or 3 per [`references/outreach_levels.md`](references/outreach_levels.md): level 1 first, then level 3, and anything else is level 2; when unsure, the level that does more. For level 3, name the lines the chat gave (by resource name and turn) and check them against `resource_map.md` for the person's country and every topic they raised. For *Covered by a recent email or a waiting draft*, run Step 5.5 first: `last_email_line` names the newest draft marked sent and its age, and `prior_line` lists earlier active-risk drafts; open the thread you rely on to confirm the topic, that nothing is new, and (for a waiting draft) that no reviewer has replied on it yet. **Level 3 → skip 5b–5e** and post the ☑️ reply in Step 6. **Levels 1 and 2 → draft (5b–5e).**

**5b. Country.** `countryCode` from the Aura record (already mapped to the resource-map key, `GB → UK`), with `timezone` to confirm an ambiguous or blank code. Fall back to where the person said they live, only if it is plainly their own current location. None → the country-not-in-map path. Never write the country into the email body.

**5c. Resources.** Per `resource_map.md` and `email_template.md`: 2–3 lines for a mapped country, hotline first; an ongoing-care directory only on non-acute cases; coverage-gap cells use the related same-category lines, never a mismatched one; an unmapped or unknown country gets a single global line (Befrienders Worldwide for emotional-need categories, medical-care / practical-help guidance for 5/7). Never a mapped country's number for an unmapped country.

**5d. Assemble** the template per `email_template.md` → Step 2 — **category 2 uses its own body** (`email_template.md` → *Category 2 body*), not the shared one: `{subject}` discreet, `{first_name}` from `givenName` (else the name the person was addressed by, if plainly theirs; else `there`), `{audience_suffix}` from Step 4.6, `{risk_phrase}`, `{resource_intro}` (count and type accurate), `{resource_list}`, `{closing_line}` for the category.

**5e. Validate** against the template's 13-item checklist plus its Tone and Privacy sections. Above all: no self-harm language outside category 1, nothing from the conversation in the body beyond the first name, and no mention of a conversation, a chat, Aurie being told anything, or a review.

A missing name or country is never a reason to withhold a draft — `Hi there,` and the country-not-in-map path exist for that.

### Step 5.5 — Prior guardrails for this user (every outcome)

The same person can trip this channel and `#guardrails-memory-ingestion`, or one of them repeatedly, and each alert gets its own draft. Look across **both** channels before posting:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-conversation-guardrail/scripts/prior_guardrails.py \
  --user <firebase-uid> --before-ts <message_ts> --conversation <conversation-id>
```

Mode B: omit `--before-ts`. It lists the user's alerts in the last 30 days strictly older than this one, with each one's outcome (from the triage reply on its thread), whether it was tagged high priority, and whether a reviewer put the ✅ on it (the mark that the CS email went out). It prints three ready lines, used verbatim in Step 6:

- `last_email_line` — the newest active-risk draft a reviewer marked ✅ (CS's sent mark) in either channel in the last **90 days**: `📧 *Last CS email:* draft posted <YYYY-MM-DD HH:MM UTC> (<age>) in #guardrails-<channel> — marked sent ✅ (<open thread>)`, plus the email count and any newer draft not yet marked sent. Every earlier draft this line or `email_line` names carries a Slack link to its alert thread, so the reviewer can read the email that already went out. When the user has earlier alerts but none marked sent it reads `none marked sent in 90d`; with no history at all it is `null` — omit the line. It goes **directly under the UID line** on every format so the reviewer sees it first. The date is when the draft was posted: Slack does not timestamp reactions, so the actual send time is not known.
- `prior_line` — always present, on **every** format, including `none`. When a channel could not be read it says `lookup incomplete`, and that is never reported as "no prior guardrails".
- `email_line` — present only when an active-risk draft for this user was posted in either channel in the last 24 hours. **One CS email per user per day**: this flags the duplicate for the reviewer. It does not withhold the draft — the draft is still produced and the reviewer decides whether to fold it into the earlier email, hold it, or send (a high-priority case is always their call).

Never add anything from an earlier thread beyond what the script prints — the same Slack content rule applies.

**Pass `--conversation <conversation-id>`.** Intelligence Service is meant to post one alert per conversation but can post one per handoff (two alerts two seconds apart on one conversation, 2026-10-19). Only the **oldest** alert of a conversation is triaged. The script reads the channel past `--before-ts` for this, so the rule holds in whatever order a sweep reaches the alerts (newest-first would otherwise draft and page both). An earlier alert for the same conversation is left out of `prior_line`, `email_line` and `last_email_line` and returned as `duplicate_of`. When `duplicate_of` is set, whether or not that thread has a triage reply yet, skip Steps 4–5 and 6b and post only the pointer. Run Step 3 and Step 4.7 first, so the pointer carries this alert's `Trigger reason:` line like every other reply (`data:analyze-conversation-guardrails` reads it back per alert):
```
↩️ duplicate alert — same conversation as <duplicate_of thread link>; triage is there. No second draft, no tag.
UID `<first8>…`
Trigger reason: <reason> · subject: <…> · asked for: <…>[ · already in care]
```

### Step 6 — Post one reply on the alert thread

**Pre-post re-check (Mode A, mandatory).** Immediately before posting, re-read the alert's thread:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-conversation-guardrail/scripts/thread_state.py \
  --channel <channel_id> --ts <message_ts>
```

- `"has_reply": true` → **do not post** (and do not tag): another run of the analysis bot answered the alert while this one was reading the conversation. A human's reply does not count — a reviewer asking why nobody followed up must not hide the alert. Report "already answered — skipped" to the caller.
- Exit 1 (the thread could not be read) → **do not post blind**. Report the failure; the alert keeps no reply from this run, so the next tick retries it.
- `"has_reply": false` → post now.

The cadence's sweep snapshot is minutes old by this point, so it is not a lock; this re-check is what keeps two overlapping ticks from both posting a draft, a tag and the account email on one crisis alert.

Mode A: post by **invoking [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** with `channel=<channel_id>`, `thread_ts=<message_ts>` and **`--footer eng:investigate-conversation-guardrail --footer-skill-md <this skill's base directory>/SKILL.md`** (the versioned footer is stamped by the script — never type it into the body). Always pass `--footer-skill-md`: the base directory is given when the skill is invoked, and without it the helper has to guess where this SKILL.md lives. That guess resolves against the *posting* helper's own plugin root, so in a session whose `eng` copy predates this skill it finds nothing and the footer silently loses its `:<version>`. After posting, confirm the footer reads `eng:investigate-conversation-guardrail:<version>`; if it does not, report it to the caller rather than posting again. Use its posting mechanics but **skip its draft-and-confirm gate**: this is an unattended triage flow that always posts, and the human review happens on the draft in the thread. Mode B: print the same text to the caller and say there was no Slack anchor.

**Card guards and scratch files (mandatory in cadence mode).** Pass `--expect "<uid8>…"` (this alert's redacted UID), `--max-chars 8000` and `--max-chunks 3` on every post: the helper refuses, exit 2 and nothing sent, a body that is not this alert's reply. A refusal leaves the alert without a bot reply, so the next tick retries it. Report it to the caller as **not posted: card guard**, never as handled, because a guardrail reply is a safety path. When the caller gives an **output folder** (its worker folder, e.g. `g6_b1/`), every file this skill writes goes there under a typed name (`g6_tx_<uid8>.txt` for the `fetch_conversation.py` readout, `g6_reply_<uid8>.txt` for the reply), never the scratchpad root or a generic name, and the transcript file is deleted once the reply is posted or skipped. Without one, pipe the reply on stdin (`--text -`). See `data:run-all-analysis-cadence` → *Scratch files and posting*.

**Slack content rule (non-negotiable, per `Compliance/Privacy`).** The reply carries only: the redacted UID (first 8 chars + `…`), turn numbers, handoff categories, the risk category and a **categorical** risk label from this list — `suicidal thoughts`, `wish to die or disappear`, `self-harm`, `harm to others`, `domestic violence`, `abuse`, `sexual violence`, `trafficking`, `stalking`, `child/elder abuse`, `medical emergency`, `overdose`, `psychosis with safety concern`, `basic-safety risk`, `in danger (unspecified)` — the crisis-reply check lines, the `Harm-to-others check:` line, the `Key turns:` line (turn numbers only), the `Trigger reason:` line and the `Prompt:` line. Never quote, paraphrase or summarise what the person said, never name a third party, never a city. **One exception:** on the active-risk **draft** formats only, the account holder's email from the Aura record goes on the UID line so the reviewer can send without a lookup (omit it when none resolved; never on a short post).

**Formats.** Exactly one of. They are Slack mrkdwn, not Markdown: bold is `*single asterisks*`; `**double**` shows up literally in the channel.

- **Active risk** (mapped country):
  ```
  🚨 active risk — review draft below before CS sends.
  UID `<first8>…` · category: <category> · country: <country> · email: <email — omit if none>
  <last_email_line from Step 5.5 — omit if null>
  Risk: <categorical label(s)> at #<n>[, #<m>] · handoffs: #<n> <category>[, …] · <total> turns
  Level: <1 — high priority | 2 — daily review> — <categorical reason, e.g. "minor", "active suicidal thoughts, ongoing", "no DV line in the chat">
  <prior_line from Step 5.5>
  <email_line from Step 5.5 — omit if none>
  Crisis-reply check (a) way to reach a human: <PASS|FAIL> — <turns / reason>
  Crisis-reply check (b) stayed with the person: <PASS|FAIL> — <turns / reason>
  <Harm-to-others check line from Step 4.5 — category 2 only>
  Trigger reason: <reason> · subject: <self|other|unclear> · asked for: <professional-help|clinical-advice|none>[ · already in care]
  Prompt: <the alert's *Prompt:* value, or "base">
  Template deviations: <none — or what changed and why>
  Key turns: #<a>–#<b>[, #<c>] — to read them, paste this thread's link into Claude Code and ask to "show the key turns"

  ---
  *Draft email (not yet sent — review and approve):*

  Subject: <subject>

  Hi <first_name>,
  …
  Warmly,
  The Aura team
  ```
  Coverage-gap variant: keep `country:` and add after the check lines `Reviewer: <country> has no <category> line in the map — the draft uses its related/global lines; add the specialized line before CS sends.`
  The Step 5.5 lines go in the same place on every format below — `last_email_line` under the UID line when not null, `prior_line` always, `email_line` (active-risk drafts only) when present.
  The `Key turns:` line goes last before any draft on every format except not-analysable. It names the turns a reviewer needs to check the call: from two turns before the earliest risk or guardrail-handled turn through four after the latest, plus any later turn that changed the verdict (a recurrence, a withdrawn denial). Ranges and turn numbers only. It is how the reviewer reads the words without them ever entering Slack — see Mode C.
- **Active risk** (country not in map or unknown): same, with the first line `🚨 active risk — country not in resource map; draft below uses global resources, reviewer please localize.`, `suspected country: <code or "unknown">` on the UID line, and the line `Reviewer: the map has no country-specific lines for <country> — the draft uses the global backstop(s); add or swap in a country-correct specialized line before CS sends.`
- **Active risk, level 3 — no email** (no draft, no tag):
  ```
  ☑️ active risk — resources already in place, no immediate danger, no outreach drafted.
  UID `<first8>…` · category: <category> · country: <country> · handoffs: #<n> <category>[, …]
  <last_email_line from Step 5.5 — omit if null>
  Level: 3 — no email
  Reason: <categorical — the lines given and where, e.g. "DV hotline #12, RAINN #14; no danger sign, nothing recurred"; "in active care for this; category 5"; "covered: emailed <age> ago (<thread link>), same topic, nothing new"; "covered: draft waiting for review (<thread link>), same topic, nothing new"; or "not current: contact ended, no current distress">
  <prior_line from Step 5.5>
  Crisis-reply check (a) …: <PASS|FAIL> — …
  Crisis-reply check (b) …: <PASS|FAIL> — …
  Trigger reason: <reason> · subject: <…> · asked for: <…>[ · already in care]
  Prompt: <the alert's *Prompt:* value, or "base">
  ```
- **No active risk** (B or C):
  ```
  ✅ no active risk — <non-crisis guardrail | false positive>, no action needed.
  UID `<first8>…` · handoffs: #<n> <category>[, …] · <total> turns read
  <last_email_line from Step 5.5 — omit if null>
  Reason: <categorical — e.g. "medication-change question, no danger expressed anywhere in the conversation">
  <prior_line from Step 5.5>
  Crisis-reply check: n/a (no active risk)
  Trigger reason: <reason> · subject: <…> · asked for: <…>[ · already in care]
  Prompt: <the alert's *Prompt:* value, or "base">
  ```
- **Not analysable** (Step 3 exit 3):
  ```
  :warning: *Conversation guardrail alert not analysable — UID `<first8>…`*
  No transcript from the stored export, the llmCall ledger or OpenAI, so this conversation could not be classified. *Not* a no-risk verdict — needs a human look.
  <last_email_line from Step 5.5 — omit if null>
  <prior_line from Step 5.5>
  Trigger reason: unknown · subject: unclear · asked for: none
  Prompt: <the alert's *Prompt:* value, or "base">
  ```

**Product-safety line.** When either check FAILs, add directly under the check lines:
```
🛑 Product-safety finding: the crisis-reply check failed — <(a) | (b) | (a) and (b)>. Please review the guardrail replies at the turns above.
```

**Incomplete-transcript line (mandatory when the report says INCOMPLETE).** When Step 3's report opens with the `TRANSCRIPT INCOMPLETE` banner (`transcript_complete: false` in `--json`), add directly under the UID line, on **every** format including the no-risk one:
```
⚠️ Transcript incomplete (<reasons from the banner>) — the classification and the crisis-reply check verdict are provisional; a human should confirm against the full conversation.
```
Never present an incomplete transcript as complete: a no-risk verdict or a crisis-reply PASS on a partial read is provisional, not clean.

**Minor flag** (active risk, the person appears under 18 — always level 1) — after the check lines:
```
⚠️ Reviewer: the account holder appears to be a minor — confirm a safe channel and consider any reporting obligation before sending.
```
**Caregiver-of-a-minor flag** (Step 4.6):
```
⚠️ Safeguarding: the at-risk person is a minor in the account holder's care. Route to whoever owns safeguarding alongside sending. Do not name or contact the minor.
```

### Step 6b — Tag Diana (levels 1 and 2)

After the thread reply posts — and only if **this run** posted it (Step 6's pre-post re-check did not skip) — post a second reply on the **same thread** for a level 1 or level 2 case, same `shared:post-to-slack` mechanics and footer (including `--footer-skill-md`), no gate. Level 3, duplicates, no-risk and not-analysable replies get no tag.

Level 1:
```
<@U01KSNNQQJX> 🚨 HIGH PRIORITY — <categorical label> · UID `<first8>…`
Reviewer needed now — details above in this thread.
```

Level 2:
```
<@U01KSNNQQJX> for review — <categorical label> · UID `<first8>…`
Daily review — details above in this thread.
```

`U01KSNNQQJX` is Diana, the guardrail reviewer. When she is unsure she sends the case to Talia from `eng:review-conversation-guardrails`, which tags Talia. **Never tag Customer Support or `<!channel>` here:** CS is tagged only by the reviewer, on an approved email. Copy the mention exactly — this posts through `slack_post.py` (`chat.postMessage`), and the API notifies only on Slack's mention syntax; a typed `@Diana` is plain text and notifies no one. Keep it to two lines; do not restate the draft. Only the level 1 tag says `HIGH PRIORITY` — `prior_guardrails.py` and the review queue read that phrase as "paged".

## Output to the caller

1. The decision line — active risk (category), non-crisis guardrail, false positive, or not analysable.
2. The Step 5.5 last-CS-email line (when not null), the prior-guardrails line, and the once-a-day line when it fired.
3. The two crisis-reply check lines (active risk), and on category 2 the harm-to-others check.
4. The level, and the filled draft on levels 1 and 2.
5. The `Trigger reason:`, `Prompt:` and `Key turns:` lines.
6. Mode A: confirmation the reply posted, and which tag posted (high priority, for review, or none).

The transcript is read, never reproduced: nothing from it leaves except turn numbers, categories and categorical labels. Mode C is the one place the words are shown — locally, to the reviewer who asked.

## Edge cases

- **The alert transaction matches no turn.** Rare (a handoff whose reply was not stored). The script warns; locate the alert turn by the first guardrail-handled turn and say so in the reply.
- **Conversation still running.** The alert fires mid-conversation; a sweep may read it before it ends. Classify on what exists and add a note `⚠️ Conversation may still be in progress — later turns were not read.` under the UID line when the last turn is within the hour.
- **Two people's data in one conversation** cannot happen — the ledger's `userId` is authoritative; the script warns on a mismatch and proceeds on the conversation's own UID.
- **Voice conversations** store their turns like any other; nothing changes.
- **Re-running** is read-only and idempotent up to the Slack post; when invoked by the cadence, an already-replied alert is never re-processed.

## When to use this vs siblings

- **This skill** — a `*Conversation guardrail triggered*` alert, or a conversation id you want judged for guardrail handling.
- [`eng:investigate-memory-ingestion-guardrail`](../investigate-memory-ingestion-guardrail/SKILL.md) — `Memory ingestion guardrail triggered` alerts (passive uploads). Its rubric trusts an upstream flag; this one cannot.
- [`data:run-conversation-guardrails-analysis-cadence`](../../../data/skills/run-conversation-guardrails-analysis-cadence/SKILL.md) — sweeps the channel and calls this skill per unanswered alert.
- [`eng:update-intelligence-prompt`](../update-intelligence-prompt/SKILL.md) — when the conclusion is that the Guardrails Handler prompt itself must change.

## References

- [`scripts/fetch_conversation.py`](scripts/fetch_conversation.py) — the whole-conversation reader (export + ledger + GCP, Responses fallback).
- [`scripts/prior_guardrails.py`](scripts/prior_guardrails.py) — the Step 5.5 lookup across both guardrail channels (canonical copy; byte-identical in `investigate-memory-ingestion-guardrail`, see `docs/canonical-sources.md`). [ENG-1540](https://linear.app/aura-health/issue/ENG-1540).
- [`scripts/thread_state.py`](scripts/thread_state.py) — the Step 6 pre-post re-check (`conversations.replies`; `groups:history` on the private channel).
- [`scripts/responses_reader.py`](scripts/responses_reader.py) — byte-identical copy of the ledger → Responses reader (canonical: `data:analyze-user-conversations`; see `docs/canonical-sources.md`).
- [`references/classification_rubric.md`](references/classification_rubric.md), [`references/crisis_reply_check.md`](references/crisis_reply_check.md), [`references/outreach_levels.md`](references/outreach_levels.md) (canonical; byte-identical in `review-conversation-guardrails`), [`references/email_template.md`](references/email_template.md), [`references/resource_map.md`](references/resource_map.md), [`references/trigger_reasons.md`](references/trigger_reasons.md).
- [PROD-7238](https://linear.app/aura-health/issue/PROD-7238) — this skill's ticket; [AuraHealth/IntelligenceService#569](https://github.com/AuraHealth/IntelligenceService/pull/569) — the alert.
