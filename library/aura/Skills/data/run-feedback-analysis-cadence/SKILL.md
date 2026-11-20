---
name: run-feedback-analysis-cadence
description: >
  The unattended runner for the negative Aurie rating deep-analysis sweep (the "Group 2
  analysis" run — qualify it as analysis to distinguish it from the Group 2 harvest run). The
  automated counterpart to the interactive `data:investigate-user-rating`: it runs one silent
  sweep per invocation, processes every negative rating whose Slack thread has no analysis-bot
  reply yet, fetches each transcript plus Aura profile and subscription context, synthesises a
  why-negative and product-signal analysis, auto-posts it as a threaded reply — the reply
  itself marking the entry done — and records the conversation to Learnings/Conversations. Trigger on "run the feedback analysis cadence", "run the
  rating sweep", "negative rating sweep", "sweep the conversation ratings on a cadence",
  "start the rating feedback cadence", or "make sure the rating sweep is running". Do NOT use
  for an interactive review or positive ratings (`data:investigate-user-rating`).

metadata:
  version: "3.12.1"
knowledge:
  reads:
    - Guide/Index            # baseline routing aid (universal)
    - Tools/Slack            # footer spec (Post format) + channel directory
    - Compliance/Privacy     # PII rules for any user-scoped external output
    - Team/Internal Accounts # employee allowlist passed to fetch_ratings.py --internal-accounts-file (catches employees on personal accounts the Aura-API ladder misses)
    - Tools/Braintrust               # Braintrust fallback path (BRAINTRUST_API_KEY)
    - Tools/OpenAI                   # OpenAI project keys + stored-completions access
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
    - Tools/Notion                   # NOTION_TOKEN, REST access for the Step 6 pre-check
    - Learnings/Conversations        # Step 6 dedup: an existing row for the conversation_id is updated, not duplicated
  writes:
    - Learnings/Conversations        # Step 6: one row per recorded negative conversation rating
---

# run-feedback-analysis-cadence

The **unattended cadence runner** for the negative-rating deep-analysis sweep.
Where [`data:investigate-user-rating`](../investigate-user-rating/SKILL.md) is the
interactive, one-at-a-time reviewer that produces an aggregate digest, this skill
is its **automated sibling**: each invocation asks Slack which threads have no analysis
reply yet, and auto-posts one threaded analysis reply per unanswered negative —
no human in the loop, no digest. **This skill does not schedule itself**; scheduling
is the operator's, via `data:run-all-analysis-cadence` or a direct invocation.

It is deliberately narrow: **negative polarity only**, one channel, and **no state on disk at
all**. It produces two things per entry: a threaded reply, and — for a conversation-rated entry
— a `Learnings/Conversations` row, the corpus the prompts are improved from. Everything that makes it safe to
run unattended — the dedup contract, the safety gate, the race-check — lives here.

## When to use

- "run the feedback analysis cadence" / "run the rating sweep" / "negative rating sweep"
- "sweep the conversation ratings on a cadence" / "start the rating feedback cadence"
- "make sure the rating sweep is running"
- a tick of [`data:run-all-analysis-cadence`](../run-all-analysis-cadence/SKILL.md), which sweeps
  this group along with the other four

**Do NOT use for:**

- A one-at-a-time interactive review that emits an aggregate digest, or a
  positive/marketing sweep → [`data:investigate-user-rating`](../investigate-user-rating/SKILL.md).
- Cancellation feedback → `data:investigate-cancellation-feedback`.
- PMF survey responses → `data:investigate-pmf-survey`.

## Prerequisites

Runs from the **host harness** (not Cowork — `mcp__workspace__bash` strips the host
env, so the tokens below are unreachable there). API-first by necessity — no MCP
covers these paths:

- **`SLACK_BOT_TOKEN`** — `conversations.history` (via `fetch_ratings.py`) + posting the
  threaded reply (via `slack_post.py`). The `aura-ai` bot must be a member of the channel.
- **`OPENAI_CONVERSATION_API_KEY`** — reads OpenAI stored completions for the transcript
  (`fetch_logs.py`). **`BRAINTRUST_API_KEY`** is the automatic fallback when OpenAI returns
  0 completions (BTQL against the `Conversation` project).
- **`AURA_API_TOKEN`** — per-user profile + subscriptions + the tenure/engagement call behind each reply's `Tenure & engagement` line (no MCP). Base URL is
  **`https://services.aurahealth.io`** (not `api.aurahealth.io`).
- **`NOTION_TOKEN`** — the Step 6 pre-check and write to `Learnings/Conversations`. When it is
  missing, Step 6 is skipped and counted, and the replies still post.
- **`python3.12`** — the scripts and inline Aura calls use `requests` (bundles certifi;
  don't reach for `curl` — direct HTTPS `curl`/`urllib` fails in some sandboxes with
  `CERTIFICATE_VERIFY_FAILED`).

Every token is a **soft requirement** — if one is missing, degrade and note the gap
rather than hard-failing the whole run.

## Dedup — the reply is the watermark

There is **no state file**. An entry is unprocessed if — and only if — its Slack thread
carries no reply from the analysis bot `U0ATMPESR37`. Every tick re-checks the full window
and processes everything that check says is unprocessed, so a missed tick, a crash, or a
widened upstream filter all self-heal on the next run.

**Read [`references/dedup.md`](references/dedup.md) before the first post.** It is the
shared contract with
[`data:run-rating-feedback-analysis-cadence`](../run-rating-feedback-analysis-cadence/SKILL.md)
(both sweeps read this one channel): the reply check itself, the rule that every terminal
outcome posts *something*, the transient-vs-terminal split behind the error reply, and the
pre-post race check.

`~/.claude/rating_sweep_state.json` is **not** part of this sweep: don't read it, don't write
it. A copy on disk is inert.

## Workflow

Resolve the plugin root at runtime; **never** `cd` into a hardcoded
`…/rpm/plugin_<hash>/…` path (those pin a stale snapshot). Invoke scripts as
`python3.12 ${CLAUDE_PLUGIN_ROOT}/skills/run-feedback-analysis-cadence/scripts/<file>.py`.

### Step 1 — Resolve inputs

- **channel** = `#ai-data-intelligence` = `C07MB0TMF5J` (canonical; from `Tools/Slack` →
  Channel Directory). This is the only channel with `*[TYPE] RATED*` shapes.
- **window** = `7d`, **polarity** = `negative`.

> **`#ai-feedback` (`C0AENS8D48N`) and `#ai-cancellation-feedback` (`C0BR3UTNL83`) are out of
> scope.** They carry `New PMF Survey Response` and `*New Cancellation Feedback*` messages
> respectively (#ai-feedback also holds cancellations predating the 2026-08 split), **not**
> `*RATED*` rating shapes — the parser returns 0 there every run. That is **expected, not a
> miss**. Don't sweep them with this skill (those are
> `data:investigate-cancellation-feedback` / `data:investigate-pmf-survey`).

### Step 2 — Fetch + parse (deterministic)

```bash
python3.12 ${CLAUDE_PLUGIN_ROOT}/skills/run-feedback-analysis-cadence/scripts/fetch_ratings.py \
  --channel C07MB0TMF5J --window 7d --polarity negative --shape all \
  --internal-accounts-file ${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/team__internal-accounts.md
```

Emits JSON with `counts` (parsed / after_polarity_filter / real / test_dropped /
infra_skipped / by_shape), `enrichment`, `prompter_mix`, and `entries` (the real,
non-test negatives). Internal accounts and infra-only entries (e.g. bare "Voice issue")
are already filtered out.

**Two signals drop an entry, both statements about the account itself:** the
`Team/Internal Accounts` allowlist, read off the cached page by `--internal-accounts-file`
(every Firebase UID and `google:` id — never hand-build `--internal-ids`,
[ENG-1436](https://linear.app/aura-health/issue/ENG-1436); a hard flag that works with no
Aura token — the only one that catches employees on personal accounts),
and the Aura API ladder (`role`, `isAIReviewer`, `@aurahealth.io` email, `test=true`, Aurie
lifetime-free creator sub). Rating volume is never one of them; prolific UIDs are annotated
`high_volume` and reported, never dropped — see
[ENG-1048](https://linear.app/aura-health/issue/ENG-1048).

The API join needs `AURA_API_TOKEN`; without it `enrichment.ran` is false with a reason and
only the allowlist applies — the run still completes, it just drops less. It walks one
`GET /users/:uid` + one `POST /subscriptions/list/:uid` per distinct **surviving** UID
(negatives only — ~40 UIDs / ~14s on a 7-day window), cheap enough for every tick.

**Log the drop count every run, including zero** (`filtered: N (reasons)`), and name
`enrichment.reason` whenever the join didn't run. A silent drop reads exactly like an empty
window, so a filter defect can run for months unnoticed.

### Step 3 — Dedup against Slack thread state

Resolve the bot id once via `auth.test` — it must come back `U0ATMPESR37`. **If `auth.test`
fails, post nothing for the tick and report it:** without a resolved bot id the check is
blind, and a blind check reads every thread as unprocessed.

Then for **every** parsed entry, call `conversations.replies` on `C07MB0TMF5J` + that entry's
`ts` and keep the entry only if no returned message (excluding the thread parent) has
`user == U0ATMPESR37`. Pace ~0.35s between checks — `conversations.replies` is Tier-3.

Sort the surviving set **oldest-first** and process all of it. There is no cap and no
seed-instead-of-post path: an unprocessed entry is real feedback sitting unanswered, so the
correct response is to answer it. Report the count — `in-window N → unanswered M` — so an
unusually large tick is visible. Full rationale in
[`references/dedup.md`](references/dedup.md).

When nothing is unanswered, the run is done with nothing to post.

### Step 4 — Per-entry analysis (sequential)

For each unanswered entry, oldest-first (do not fan out — avoids rate-limit collisions and
keeps posting order deterministic):

**4a. Gather context.**

- **Transcript** — `fetch_logs.py --conversation-id <conversation_id>` (OpenAI stored
  completions, automatic Braintrust `Conversation`-project BTQL fallback on 0 matches). For
  a **journal-shape** entry there is no fetchable transcript — analyse from the entry's
  `journal_text` directly.

> **Reads both OpenAI surfaces.** `fetch_logs.py` merges Chat-Completions conversations (walked via `GET /v1/chat/completions`) with Responses-API conversations, which are **not enumerable** on OpenAI — the `llmCall` ledger (BigQuery `aura-main.llm.llm_call`) indexes those and each object is then fetched by id. This needs **`GCP_KEY`** in addition to the OpenAI project key. If the ledger read fails the script warns on stderr and returns Chat-Completions-only results — treat that as incomplete, not as the answer. Results carry `api_breakdown` and `responses_source` so you can tell which surfaces were actually read. Full write-up: [`data:analyze-user-conversations`](../analyze-user-conversations/SKILL.md) Step 2.
- **Profile-shape entries have no transcript either, and no `conversation_id`** — the rated
  object is the user's generated profile, not a conversation. **Don't call `fetch_logs.py`
  for them**; with `conversation_id: None` it can only return nothing. Instead fetch the
  rated artifact: `eng:build-aurie-user-insights` with **`mode=read`**,
  `rating_id=<entry rating_id>` (the footer `id:`) **and `user_id=<entry uid>`**, which
  returns the renderers + compiled insight as shown at rating time. `mode=read` is
  mandatory — the skill's default `build` mode POSTs a recompile and would overwrite the
  artifact being analysed. **Pass the uid as well as the rating id**: the read skill's
  live-profile fallthrough is keyed by user, and a 404 on the snapshot returns no user to
  fall back to, so a rating-id-only call dead-ends. Both values sit in the same footer.
  Carry the sub-skill's source label (rated snapshot vs live profile) into the reply; when
  it can only return the live profile, say so rather than implying the user saw it.
- **Aura profile** — `GET https://services.aurahealth.io/users/<AUID>`.
- **Subscriptions** — `POST https://services.aurahealth.io/subscriptions/list/<AUID>` with
  body `{"includeDeleted": true}`. **`deletedAt` on a subscription is a backend artifact,
  NOT cancellation intent** — never read it as churn.
- **Tenure & engagement volume** — `GET https://services.aurahealth.io/ai/usage/engagement/<AUID>`.
  **Only when `<AUID>` is non-empty** — a blank id builds a bare trailing slash that
  resolves to the self-serve route and returned the token owner's record before
  Intelligence Service 1.21.5. Render `unavailable — no user_id supplied` instead;
  precondition in [`references/tenure-rendering.md`](references/tenure-rendering.md).
  One call, every shape. **Read [`references/tenure-rendering.md`](references/tenure-rendering.md)
  before rendering anything from it** — that file carries the field catalog, the two
  bulk-stamp guards, the six render forms (neither tenure clock can be printed raw), and
  the degradation rule. A `0` count means zero rather than missing; a `null` `aiCreatedAt`
  has its own render form distinct from a guard trip; and you stop at the lower bound
  rather than chasing the real join date.

- Synthesise a compact **why-negative + product-signal** analysis (what missed, which
  prompter/version, the eval-worthy behaviour). Lead with the UID prefix, shape, polarity.

- **The Signal line is an observation plus a falsifiable hypothesis, never a prompt
  change.** This sweep posts unattended, with nobody reviewing the claim before it
  lands, so the discipline matters more here than on the interactive path. A
  prompter-version or config change is the prompt engineer's call off the accumulated
  corpus; a Braintrust eval case is in scope. State the evidentiary weight — a rating
  with no typed feedback is thin on its own — and never write a **designed** behaviour
  up as a defect (the sentinel ending a conversation is the worked example). Full rule:
  [`references/signal-scope.md`](references/signal-scope.md).
  **Include a `*Tenure & engagement:*` line in the posted reply** — the two tenure clauses
  (verbatim from the tenure-rendering rules, never a bare date for a stamped clock) ·
  lifetime volume (completed conversations · messages · distinct active days). Keep it to
  tenure + volume so the line matches the other rating-reply surfaces; this skill does
  fetch subscriptions above, but that belongs in the analysis body, not here. It renders
  on every entry and is never omitted: it is what tells a
  reader how much weight the thumbs-down carries, and a negative from a two-session account
  is a different finding from one from a several-hundred-conversation account. Zeroes render
  as zeroes with *"no Aurie activity on record"*. Feed the read into the why-negative
  synthesis rather than leaving the numbers stranded on their own line.
  On a **profile** entry there is no prompter/version to name — the product signal is which
  card missed and how, so lead with the renderer (or `whole profile`) and point at the
  specific content that doesn't fit this user.
- **`*Follow-up*` closes the card** (negative and neutral entries; a positive gets one only when there is a concrete marketing or product action). Up to four bullets — 🧠 prompt candidate · 🛠 product/eng · 📊 data check · 💬 CS — only the ones that apply, none when nothing is actionable. The Signal rule above still holds: the prompt line is a **candidate** rule paired with the data check that would justify it, drafted only after reading the current prompt text and checking what the model actually received, never a decided change. Link an existing Linear card, never create one. Format, rules and worked examples: [`references/follow-up.md`](references/follow-up.md).

**Profile entries with no feedback text get the same full analysis and post normally.** `fetch_ratings.py` normalises the k8bot `No feedback` placeholder to an empty string, so `feedback == ""` is the *common* case on this shape — a profile rating is one tap and most carry no words. The absent text is not a gap in the evidence: the rated snapshot records what was compiled, from what material, and when, which explains a 👎 on its own. Read four things off it:

- **`rendererKey`** — the specific card, or `null` when the whole profile was rated.
- **Compile provenance.** Each insight carries its own `Evidence:` line naming its source. When every line cites the intake form rather than conversations, the profile was written from a signup questionnaire.
- **Confidence the compiler already declared.** Sections returned as `No high-confidence insight available`, plus any insight self-labelling as an early-stage or limited-material inference. Count them — a profile that ships several is reporting how little it had to work with.
- **What actually displayed.** Renderers with `display: false`, an empty body, or an empty array never reached the user, so what they saw is narrower than the artifact implies.

Pair that with `insightsRenderersCompiledAt` against the rating's `createdAt`, and with the account context from the two Aura calls above plus the user's conversation count — `dayCount`, `listenedCount`, and whether they have had any conversation at all.

The signal is the gap between what the profile claimed and what it could have known. A longitudinal or progress-framed card — one describing a pattern as already loosening, or naming what the user is moving away from and toward — asserts movement that someone with no conversational history has no way to recognise in themselves; name that framing and the missing signal rather than speculating about the user's reaction. When the compile itself is the defect (it ran before there was material to compile), separate the two levers: gating compile-eligibility on a minimum of real signal, versus making renderer selection signal-aware so progress framings are suppressed on an intake-only profile. An entry **with** feedback text follows the same procedure, reading the stated reason against the same provenance.

**4b. SAFETY GATE (hard — analyse but DO NOT post the analysis; flag instead).**

If the transcript involves any of the following, do **not** post the analysis publicly.
Instead surface it to the operator in-session — UID-prefix + one-line reason, nothing more —
and post the **minimal placeholder** below on the thread:

```
:lock: *Rating withheld from automated review — uid `<8-char prefix>` · <shape>*
Held by the content-safety gate, so no analysis is posted here. *Not* a no-concern
verdict, and nothing has been routed anywhere — this thread needs a human look.
```

The placeholder carries **no** detail about the content, and is not optional. Because the
reply is the dedup signal (Step 3), a held entry with no reply is re-fetched, re-analysed and
re-flagged every single tick — so the placeholder is what stops a loop over sensitive
content. Draft it as its own file and post it via the same Step 5 call.

**The wording is load-bearing, and it is specified once** — in
[`references/dedup.md`](references/dedup.md) → Rule 2, alongside why it asks for a human
rather than asserting one already has it, and which way it errs. Read it before drafting
the reply. The held entry goes in the run readout too (see *Output*).

The gate fires on:

- **CSA disclosure** (child sexual abuse).
- **Active SI** (suicidal ideation / self-harm intent — not ordinary grief or "I want
  this pain to stop" relief language; use judgement).
- **A distressed minor** (age cues, references to a parent/guardian/CPS, school-age
  context) — the whole session is held regardless of rating.
- **An active `guardrails-handler-prompter` firing on acute-crisis content** (e.g. severe
  substance-use distress). A fired guardrail is the system's own signal that the session
  is sensitive.

**Every trigger above reads on what the *user* disclosed — Aurie's own words are never a
disclosure.** The assistant's crisis boilerplate ("I'm not a therapist or crisis service…",
"Are you thinking about harming yourself right now?") is a *response*, and it fires on false
positives, so its presence says nothing about the user. Where the user contradicts it — "I
never said anything about self harm, you inferred that" — the user is the authority on their
own state: that is a resolved false positive, not a doubtful case for the rule below.

**This is the one exception to the doubt rule, and it needs stating because the failure is
self-concealing.** A mis-fired crisis response manufactures the exact language the gate looks
for, so the gate hides the misfire that created it, and the louder the misfire the more
certainly the analysis is suppressed. Read the inversion: a safety response firing on a user
who denied it is among the highest-value signals the sweep can surface, so it is analysed and
posted, not held. Case that established this:
[ENG-1219](https://linear.app/aura-health/issue/ENG-1219).

When in genuine doubt, hold and flag — the cost of skipping one product-signal reply is
low; the cost of an auto-posted public analysis on a vulnerable person is not.

**4c. Unanalysable entry — transient retries, terminal gets an error reply.**

Per [`references/dedup.md`](references/dedup.md) Rule 3, split on whether retrying could help:

- **Transient** — a missing token, a 5xx, a rate limit, a network error, or a `0`-match
  transcript on an entry **younger than 1 hour** (OpenAI stored-completions indexing lags the
  k8bot message). **Post nothing.** The absent reply is what schedules the retry next tick.
- **Terminal** — a `0`-match on **both** OpenAI and Braintrust for an entry **older than
  1 hour**, or any failure that is a property of the entry rather than of this moment. Post
  the error reply so the thread is closed out instead of retried forever:

```
:warning: *Rating review unavailable — uid `<8-char prefix>` · <shape>*
No transcript found for this conversation on either OpenAI stored completions or Braintrust.
Nothing to analyse, so this entry is closed out rather than retried.
```

The entry's own `ts` carries its age, so this needs no persisted first-miss marker.

### Step 5 — Post the threaded reply

For every entry that reached a terminal outcome in Step 4 — a normal analysis, a safety
placeholder, or an error reply — **race-check then post**:

1. **Re-run the Step 3 reply check** for that `ts` immediately before posting. If a bot reply
   now exists, a sibling session got there first — **skip** (no post, no duplicate). See
   [Coordination](#coordination).
2. Post with the canonical Slack helper:

```bash
python3.12 ${CLAUDE_PLUGIN_ROOT}/skills/run-feedback-analysis-cadence/scripts/slack_post.py post \
  --channel C07MB0TMF5J \
  --thread-ts <entry ts> \
  --text-file <path to the analysis body> \
  --expect "<uid8>…" --max-chars 6000 --max-chunks 3 \
  --footer data:run-feedback-analysis-cadence \
  --footer-version <this skill's metadata.version>
```

**Card guards (mandatory).** `--expect "<uid8>…"` is this rating's redacted UID. A body that lacks it, runs past 6,000 characters or would split into more than 3 replies is refused with exit 2, and nothing is sent; report it for that entry. Write the body to your worker folder under a typed name, or pipe it on stdin (`--text -`); never a shared or generic file such as `c1.txt` (see `data:run-all-analysis-cadence` → *Scratch files and posting*).

**Let the runner tag auto-resolve — do not pass `--footer-runner`.** The sweep is
unattended, but it runs inside a host session that a person armed, and the useful fact on
a cron post is *which* person's loop produced it — so the footer should read
`jcurlier@claude-desktop`, not an anonymous `cron@…`. The `data:` skill id in the same
footer already says the post came from the cadence skill rather than a hand-run
investigation, so the operator identity is the part that would otherwise be lost.

**Pass `--footer-version` EXPLICITLY.** The footer auto-resolver locates the skill's
SKILL.md relative to the plugins tree; under the rpm plugin-snapshot layout that lookup
fails and the version is **silently dropped**, producing a version-less footer (the
[PROD-3636](https://linear.app/aura-health/issue/PROD-3636)-class bug). Reading
`metadata.version` from this file and passing it as `--footer-version` is the reliable
fix. **Never** hand-type the footer into the message body.

3. The posted reply **is** the dedup record: the next tick's Step 3 check sees it and skips
   the entry. No file to update. Then record the entry (Step 6).

**Privacy (hard — per `Compliance/Privacy`).** Every posted reply and every flag:
**UID first 8 chars only**; **email domain only** (never the full address); **never**
`givenName` / `familyName`. Aurie sometimes addresses the user by first name in the
transcript — **scrub that name** before quoting any turn.

**Sentinel markers are stripped before quoting.** A prompt can close a reply with a literal
marker glued to the end of the message (`…a good place to stop.**ENDCONVERSATION**`, today only
on `quickProcessing`). The IntelligenceService strips it from the stream and from the persisted
`dialogue.message` but **cannot** strip OpenAI's stored copy, so the transcript fetched in 4a
still carries it. It is a completion signal, never text the user saw — remove it from any quoted
turn, and don't post it as a formatting bug or a prompt leak. Registry:
`conversation.sentinels` in the IS `src/config.js`; more markers will follow the same path.

**Renderer content is never quoted verbatim.** A rated profile artifact is generated
*about* the user, so its cards routinely name people, places, employers, health details
and circumstances — more densely than a transcript does. On a profile-shape reply,
**paraphrase** the card content: describe what it claims and why it does or doesn't fit,
without reproducing its sentences. The reply lands in a public channel, and this is the
one shape where the source text is a profile of the person being discussed.

### Step 6 — Record the conversation to `Learnings/Conversations`

After an entry's normal review has posted, write its analysis as a row in
`Learnings/Conversations` via `shared:record-knowledge`
(`caller=data:run-feedback-analysis-cadence`). Every entry this sweep reviews is negative, so
the eligibility test is the shape (`qp` / `coaching` / `conversation` / `chat` — never journal
or profile) and the outcome (the normal review — never the safety placeholder or an error
reply). Dedup on `conversation_id`: an existing row is updated in place.

Recording is this skill's own decision: the Conversations loop improves the prompts from this
table, and a sweep that analysed every rating but recorded none would leave the loop blind to
exactly the ratings it most needs. The row is
written from the analysis this tick already produced, so recording costs one Notion write per
entry, not a second analysis.

**A recording failure never touches the reply.** Log it and move on: a failed create is
recovered by `data:backfill-learnings-conversations`; a failed update is named in the readout
for a manual re-record. Row fields, body template, the
create-vs-update pre-check and the invocation shape:
[`references/record-conversation.md`](references/record-conversation.md).

## Coordination

A sibling session (Group 1, `data:run-rating-feedback-analysis-cadence`) may sweep
`C07MB0TMF5J` at the same time — both sweeps read the same channel and the same reply signal.
The Step 5 pre-post race-check is what prevents a double-post when both pick the same
unanswered entry. The two sweeps can safely overlap: the dedup signal lives in Slack, so
there is no shared file and no read-modify-write window in which one sweep's write drops the
other's.

Run **this** sweep before Group 1 when both run in one pass: whichever replies first claims
the thread, and this sweep's negative-only review is the deeper one.
[`data:run-all-analysis-cadence`](../run-all-analysis-cadence/SKILL.md) fixes that order.

## Output

The primary output is the **side effect**: threaded Slack replies. In-session, print a
one-line-per-run readout — `in-window N → unanswered M`, the internal-filtered count, and
per-entry the UID-prefix + shape + a one-line headline (for held entries the safety reason,
for error replies what failed), then the recording counts
(`recorded C created · U updated · create_failed · update_failed · S not eligible`). Idempotent: a re-run with everything answered posts nothing.

## Bundled scripts (self-contained per Architecture principle 1)

- `scripts/fetch_ratings.py` — the k8bot `*RATED*` parser + window/polarity/shape filter.
  Byte-identical copy of `data:investigate-user-rating`'s copy (that skill owns it). Imports
  `slack_read` for the channel history read.
- `scripts/slack_read.py` — the canonical Slack **read** helper (`shared:read-slack`),
  byte-identical. Backs `fetch_ratings.py`'s `conversations.history` window read, and its
  `replies()` function is the Step 3 dedup check (it rides the shared 429-retry transport,
  which matters when the check runs once per in-window entry).
- `scripts/fetch_logs.py` — OpenAI stored-completions reader with Braintrust BTQL fallback.
  Copy derived from `data:investigate-user-rating`'s copy.
- `scripts/slack_post.py` — the canonical Slack posting helper
  (`shared:post-to-slack`), byte-identical. Provides the `post` CLI + `--footer` /
  `--footer-version` support.

Keep all three in sync with their sources per the drift-check one-liners in
[`/CLAUDE.md`](../../../../CLAUDE.md).

## Key references

- **Interactive sibling:** [`data:investigate-user-rating`](../investigate-user-rating/SKILL.md)
- **Dedup contract:** [`references/dedup.md`](references/dedup.md) — shared with Group 1; the reply check, error-as-reply, and the race check.
- **Channel:** `#ai-data-intelligence` = `C07MB0TMF5J` (canonical in AI Knowledge → `Tools/Slack`).
- **Slack footer + PII spec:** `Tools/Slack` (Post format) and `Compliance/Privacy` (both pre-fetched via `knowledge.reads`).
- **Linear:** [ENG-1055](https://linear.app/aura-health/issue/ENG-1055) — thread-state dedup · [ENG-915](https://linear.app/aura-health/issue/ENG-915) — Update Knowledge and skills, Cycle 36.
