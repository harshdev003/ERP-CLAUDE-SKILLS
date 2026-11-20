---
name: run-rating-feedback-analysis-cadence
description: >
  Recurring, all-polarity Aurie rating-feedback analysis sweep for #ai-data-intelligence —
  "Group 1 analysis" in the Feedback Analysis Run Cadence spec. A wrapper around
  `data:investigate-user-rating`: fetches new ratings (all polarities and shapes), dedups
  against already-answered threads, analyses each (product signal for negative and neutral,
  marketing lens for positive), auto-posts a threaded reply per rating, and records negatives
  and neutrals to Learnings/Conversations. Trigger on "run group 1 analysis", "run the rating
  analysis cadence", "start the rating feedback analysis", "group 1 analysis tick", "resume
  the rating analysis cadence", or "sweep" when a rating-analysis cadence session is active.
  Requires the word "analysis": a bare "run group 1" is ambiguous with the separate harvest
  Group 1 and must NOT trigger this. Do NOT use for a one-off review of a single rating or
  window (`data:investigate-user-rating`), cancellation feedback, the harvest Group 1, or the
  negative-only Group 2 analysis.

metadata:
  version: "3.5.0"
knowledge:
  reads:
    - Guide/Index
    - Tools/Slack
    - Compliance/Privacy
    - Team/Internal Accounts
    - Tools/Braintrust               # Braintrust fallback path (BRAINTRUST_API_KEY)
    - Tools/OpenAI                   # OpenAI project keys + stored-completions access
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
    - Tools/Notion                   # NOTION_TOKEN, REST access for the Step 7 pre-check
    - Learnings/Conversations        # Step 7 dedup: an existing row for the conversation_id is updated, not duplicated
  writes:
    - Learnings/Conversations        # Step 7: one row per negative or neutral conversation rating
---

# Run Rating Feedback Analysis Cadence (Feedback Analysis — Group 1 Analysis)

## Goal

Keep the #ai-data-intelligence rating feed continuously triaged: on a fixed
4-hour schedule (and on manual "sweep" triggers), find every new Aurie
rating that hasn't been answered yet, analyse it, and post a threaded review —
product signal for negative/neutral, marketing lens for positive — without
needing per-entry human confirmation.

This is the **Group 1 Analysis** role from the AI Knowledge
`Feedback Analysis — Run Cadence` spec — the *analysis* half of that spec's
Group 1. Note the name collision: the broader Run Cadence runbook has its own,
unrelated "Group 1" (conversation-patterns harvest → report). They are different
cadences run by different sessions; this skill is only the rating-feedback
analysis one, so always qualify it as "Group 1 **analysis**."

## Relationship to `data:investigate-user-rating`

A thin scheduling + orchestration wrapper that does **not** reimplement rating
analysis: that skill is the engine, this one is the schedule around it, driving the
engine's deterministic scripts on a pinned schedule. It runs unattended every 4
hours over ticks of roughly 20–40 entries, so it deliberately takes a **lighter path** than
the engine's per-entry `data:profile-user` chain: transcript straight from
`fetch_logs.py`, a compact self-contained Session / Why-polarity / Signal write-up,
and — for a negative or neutral conversation rating — a `Learnings/Conversations` row
distilled from that same write-up (Step 7), never a second analysis.

**Read [`references/lighter-path.md`](references/lighter-path.md)** for the full
rationale, the per-shape detail, the accepted tradeoffs, and the **script-access
rule** — Steps 1 and 3 call scripts out of the `investigate-user-rating` folder and
that path must be resolved at runtime, never from a hardcoded
`rpm/plugin_<hash>/…` snapshot.

## Prerequisites

- **`SLACK_BOT_TOKEN`** — the sweep reads channel threads (dedup) and posts
  replies. Both go through the shared Slack skills — `shared:read-slack` for the
  thread-replies read (Step 2) and `shared:post-to-slack` for the threaded reply
  (Step 6) — per the repo convention that agent-driven Slack steps compose the
  shared skills rather than call Slack directly. The analysis bot
  **`U0ATMPESR37`** must be a member of #ai-data-intelligence and able to read
  its threads (that bot's replies are the dedup signal).
- **`OPENAI_CONVERSATION_API_KEY`** — service-account key scoped to the OpenAI
  `Conversation [Production]` project, used by `fetch_logs.py` to read stored
  completions. **`BRAINTRUST_API_KEY`** is the automatic fallback when OpenAI
  returns 0 matches.
- **`AURA_API_TOKEN`** — the Step 3b `GET /ai/usage/engagement/:userId` call that
  supplies every reply's `Tenure & engagement` line. Privileged endpoint (roles
  `admin` / `agent` / `service` / `system`), no MCP. **Soft requirement:** without it
  the line renders `unavailable — no AURA_API_TOKEN` and the rest of the analysis is
  unaffected — but the line is the only thing telling a reader how much weight a
  rating carries, so a session missing this token degrades every reply it posts.
- **`NOTION_TOKEN`** — the Step 7 row write. Soft: without it the rows are skipped and
  counted, and every reply still posts.
- **TLS gotcha.** All HTTPS calls use the Python `requests` library (bundles
  certifi). Do **not** reach for `urllib`/`curl` — they have failed with
  `SSL: CERTIFICATE_VERIFY_FAILED` in this environment.

## Channel

`#ai-data-intelligence` = **`C07MB0TMF5J`**. Canonical mapping lives in AI
Knowledge → `Tools/Slack`; keep this in sync if the channel ever changes.

## Workflow

### Step 1 — Fetch new ratings (deterministic)

Run `investigate-user-rating`'s fetch script (path resolved per the script-access rule in
`references/lighter-path.md`):

```bash
python3.12 <iur-scripts>/fetch_ratings.py \
  --channel C07MB0TMF5J --window 1d --polarity all --shape all \
  --internal-accounts-file ${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/team__internal-accounts.md
```

Capture the JSON. It parses all six `*RATED*` k8bot shapes, drops internal accounts, and
emits one entry per rating with `ts`, `shape`, `polarity`, `uid`, `conversation_id`,
`prompter_name`/`prompter_version`, `feedback`, and `journal_text`, plus a
`filter_generation` and an `enrichment` block. **`filter_generation` is informational here** —
this sweep does not compare it against anything. The Step 2 dedup signal distinguishes answered
from unanswered regardless of how the filter has moved.

`--internal-accounts-file` is the `Team/Internal Accounts` page as `retrieve-knowledge`
caches it; the script reads every Firebase UID and `google:` id off it — never hand-build
`--internal-ids` ([ENG-1436](https://linear.app/aura-health/issue/ENG-1436)). A hard
internal flag needing no Aura token, and the only one that catches employees on personal accounts. It drops those entries up front rather than at the
Step 4 employee check. An entry is dropped only on a real internal signal, **never on
rating volume** — full contract in
[`data:investigate-user-rating`](../investigate-user-rating/SKILL.md) Step 1.

### Step 2 — Dedup on Slack thread state

**One layer, and it is the only one.** An entry is unprocessed if — and only if — its thread
carries no reply from the analysis bot `U0ATMPESR37`.

Resolve the bot id once via `auth.test`. **If that fails, post nothing for the tick and report
it** — without a resolved bot id the check is blind, and a blind check reads every thread as
unprocessed. Then fetch each entry's thread by composing
**[`shared:read-slack`](../../../shared/skills/read-slack/SKILL.md)** — its thread-replies
read (the `conversations.replies` path), which rides the shared 429-retry transport and is
robust under throttling — and check whether any returned message has `user == U0ATMPESR37`.
Compose the shared skill rather than hand-rolling a Slack call; in particular do **not**
hand-roll a raw `conversations.history` call with a `limit`+`oldest` filter — under throttling
it has intermittently returned 0 messages, which would re-post over existing replies.

Sort the unanswered set **oldest-first** and process all of it.

> **The reply is the only dedup signal. Keep no local watermark, no processed-entry cap, and
> no seed-instead-of-post guard.** A local list cannot tell *already answered* from *never
> looked at*, so any guard built on one is guessing; the reply answers it outright.
>
> A large unanswered set means real feedback is sitting unanswered, so the correct response is
> to answer it — never to mark it handled. Report the count (`in-window N → unanswered M`) so
> an unusually large tick is visible, and let Slack's rate limits pace the posting.
>
> `~/.claude/rating_sweep_state.json` is **not** part of this sweep: don't read it, don't write
> it. A copy on disk is inert.
> [`references/dedup.md`](references/dedup.md) carries the full contract.

### Step 3 — Fetch the transcript per unanswered entry

Branch on `shape` (the six `*RATED*` shapes `fetch_ratings.py` emits are
`conversation`, `chat`, `qp`, `coaching`, `journal`, `profile`):

- **conversation / chat / qp / coaching** — all carry a `conversation_id`;
  fetch the transcript:

  ```bash
  python3.12 <iur-scripts>/fetch_logs.py --conversation-id <FULL_UUID> --json <path>
  ```

  **CRITICAL — always pass the FULL conversation UUID from the entry, never an
  8-character prefix.** The underlying `metadata[conversationId]` filter is an
  **exact string match**, so a truncated ID returns HTTP 200 with an *empty*
  completions list — indistinguishable from "genuinely not stored," so a prefix
  makes a fetchable conversation look permanently missing. Take the
  `conversation_id` verbatim from the Step 1 entry; never truncate it.
  `fetch_logs.py` falls back to Braintrust automatically when OpenAI returns 0.

  Transcript JSON shape: top-level `conversations` (list) → each has `turns`
  (list) → each turn is `{input: {role, content}, output: {role, content},
  metadata: {…}}`. `fetch_logs.py` returns only the **tail** of long
  conversations, so `turn_count` may be < `total_turn_count` — fine for a
  fresh-rating review (recent context is what matters), but don't assume you're
  seeing turn 1.

- **journal** — no transcript fetch. Use `journal_text` from the Step 1 entry
  directly.
- **profile** — a rating of the user's generated profile/insights, not a
  conversation, so there is no transcript and no `conversation_id`. Fetch the
  **rated artifact** instead: `eng:build-aurie-user-insights` with **`mode=read`**,
  `rating_id=<entry rating_id>` (the footer `id:`) **and** `user_id=<entry uid>`.
  It returns the renderers + compiled insight as shown at rating time.

  `mode=read` is mandatory — the skill's default `build` mode POSTs a recompile
  and would overwrite the artifact being analysed. Pass **both** values (they sit
  in the same footer): the read skill's live-profile fallthrough is keyed by user,
  and a 404 on the snapshot returns no user to fall back to, so a rating-id-only
  call dead-ends. Carry its source label (rated snapshot vs live profile) into the
  reply — when it can only return the live profile, say so rather than implying
  that is what the user saw.

  **The `feedback` text is not the evidence here — the artifact is.** A profile
  rating is one tap, so an empty `feedback` is the common case rather than an
  exception. Read four things off the snapshot: `rendererKey` (the specific card,
  or `null` for the whole profile); each insight's own `Evidence:` line, which
  names its source, so an all-intake citation set means the profile was written
  from a signup questionnaire; sections returned as
  `No high-confidence insight available` plus any insight self-labelling as a
  limited-material inference; and which renderers actually displayed (`display:
  false`, an empty body, or an empty array never reached the user). Pair that with
  `insightsRenderersCompiledAt` against the rating's `createdAt`.

  Two GETs on a small minority of entries, so this stays within the lighter path
  this cadence is built around (see "Relationship to `data:investigate-user-rating`"
  above) — it does not reintroduce the per-entry `profile-user` cost that path
  exists to avoid.

### Step 3b — Fetch tenure & engagement volume (every shape)

One authenticated call per fresh entry, regardless of shape — a conversation, journal,
and profile rating all benefit from knowing how much the rater has actually used Aurie:

```python
# requests only — this skill's Prerequisites forbid curl/urllib (CERTIFICATE_VERIFY_FAILED here)
import os, requests

uid = (uid or "").strip()
if not uid:
    raise ValueError("no user_id — render `unavailable` rather than calling")

r = requests.get(
    f"https://services.aurahealth.io/ai/usage/engagement/{uid}",
    headers={"Authorization": f"Bearer {os.environ['AURA_API_TOKEN']}"},
    timeout=60,
)
```

**Never build the path from an empty `uid`.** A blank id interpolates to a bare
`…/ai/usage/engagement/`, which resolves to the self-serve route and — before
Intelligence Service 1.21.5 — returned the *token owner's* record with a 200. A
truncated k8bot footer produces exactly that; render `unavailable — no user_id
supplied` instead of calling. See the precondition in
[`references/tenure-rendering.md`](references/tenure-rendering.md).

**Read [`references/tenure-rendering.md`](references/tenure-rendering.md) before rendering
anything from the response.** It carries the field catalog, the two bulk-stamp guards, the
six render forms (both clocks are stamped independently, so neither date can be printed
raw), and the degradation rule. Three things it settles that are easy to get wrong on your
own: a `0` count means zero rather than missing, a `null` `aiCreatedAt` has its **own**
render form distinct from a guard trip, and you **stop at the lower bound** rather than
chasing the real join date through Mixpanel or BigQuery.

### Step 4 — Analyse each fresh entry

- **Negative / neutral → product signal.** Name a concrete signal: a UX gap, a
  prompter-behaviour pattern, a scope-limiting example, a repeatable pattern
  worth flagging. Not vague "could be better" notes.
- **Positive → marketing lens.** Tag **Marketing-usable** (a clean, name-free,
  broadly relatable quote or reframe with no sensitive backdrop) or **Not
  marketing-usable** (name the reason — relationship conflict, medical /
  mental-health content, family / minor involvement, etc.). Even when Not
  marketing-usable, capture the internal proof point (the technique or reframe
  that worked) — still useful signal.
- **Tag-only journal entries** (only generic praise tags, no free-text
  reflection) → log as positive volume, explicitly "no quotable content." Don't
  fabricate analysis from tags alone.
- **Profile entries** are analysed from the rated artifact fetched in Step 3, not
  from the feedback box, so a wordless one still gets a real read. There is no
  prompter/version to name on this shape. **Read the artifact against the
  polarity — the same evidence supports opposite conclusions, so never assume a
  defect:**
  - **Negative / neutral** → the signal is the gap between what the profile
    claimed and what it could have known. Name the card (or `whole profile`) and
    the missing signal: a longitudinal or progress-framed card asserts movement a
    user with no conversational history has no way to recognise in themselves.
    Where the compile itself is at fault (it ran before there was material to
    compile), separate the two levers — compile-eligibility gating versus
    signal-aware renderer selection.
  - **Positive** → the signal is what the compiler got *right* on the material it
    had. Name the card that landed and the evidence behind it — a specific,
    checkable insight drawn from thin signal is a stronger proof point than one
    drawn from months of history. A 👍 is never written up as a defect, and thin
    compile provenance on a positive rating is a *credit* to the compiler, not a
    finding against it. The Marketing-usable bar below still applies.

  **A profile rating is never Marketing-usable on the strength of renderer
  content** — that content is generated *about* the user, so it is not their
  testimonial. Only the user's own feedback words could ever qualify, and then
  only under the normal content-safety gate.
- **Feedback that describes a product/infra issue, not content quality**
  (e.g. "you abruptly stopped talking", "I wish I could talk longer without an
  upgrade", "it's going off a high-level summary… depth is missing") → treat as
  its own signal category (session-truncation bug, paywall friction,
  profile-depth gap) in the Signal line — don't force it into a
  friction-with-the-coaching narrative.
- **Employee / internal-originated ratings — a backstop, because Step 1 already
  drops most of them.** An entry whose account the fetch step identified as
  internal (allowlist hit, or the Aura-API ladder) never reaches this loop: it is
  filtered out and counted in the readout. **So this cadence posts no threaded
  reply on an internal account's rating** — a deliberate difference from
  `data:investigate-user-rating`, whose digest counts internal ratings as a flag
  rather than dropping them. The digest is a private aggregate a human reads; this
  cadence auto-posts a public reply per rating, and a public analysis of a
  teammate's own thumbs-down is noise rather than signal.

  The check here remains as a **backstop for what the filter couldn't see** —
  the allowlist wasn't passed, or `enrichment.ran` was false so the API ladder
  never ran. In that case treat it as a **flag, not a filter**: still post, label
  it `internal/employee account` in the Signal line, and **never** tag it
  Marketing-usable (a teammate's thumbs-up is not a user testimonial). When
  neither signal was available, say the internal check was skipped rather than
  asserting the rating is external.

**Content-safety gate — hard rule.** NEVER mark **Marketing-usable** for:
minors / possible-minors, active mental-health crisis or medication
transitions, trauma / abuse, or intimate coercion. If an entry discloses
**active suicidal ideation** or **CSA (child sexual abuse)** content: analyse it
internally but do **NOT** post the analysis — instead flag it directly to the user
(Jerome) in the session's own message. Never silently drop it. **A withheld entry
still gets a reply on its thread** — the minimal placeholder below, and nothing
more:

```
:lock: *Rating withheld from automated review — uid `<8-char prefix>` · <shape>*
Held by the content-safety gate, so no analysis is posted here. *Not* a no-concern
verdict, and nothing has been routed anywhere — this thread needs a human look.
```

The placeholder carries **no** detail about the content, and is not optional.
Because the reply is the dedup signal (Step 2), a held entry with no reply is
re-fetched, re-analysed and re-flagged every single tick — so the placeholder is
what stops a loop over sensitive content. **The wording is load-bearing, and it is
specified once** — in [`references/dedup.md`](references/dedup.md) → Rule 2, alongside why it
asks for a human rather than asserting one already has it, and which way it errs. Read it
before drafting the reply; the held entry goes in the tick readout too (see *Output*).
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

When in doubt, **withhold and flag** rather than post the analysis. Distinguish active SI from
past/processing despair language ("this is the end" about a relationship,
"losing battle" about chores) — post the latter as a review with an explicit
"no active suicidal ideation" safety note; withhold only genuine active
ideation or a plan.

**Recurring users.** A handful of UIDs generate many rated sessions per day.
Briefly referencing a user's ongoing arc across ticks ("same betrayal-recovery
user as earlier this window") is fine for continuity — but every entry still
gets its **own** independent safety check; a benign history never exempts a new
entry from the SI/CSA gate.

### Step 5 — PII + duplicate collapsing

Per `Compliance/Privacy` (declared in `knowledge.reads`):

- **UID:** first 8 characters only — never the full Firebase UID.
- **Email:** domain only if ever present — never the full address.
- **Third-party names** in transcripts (partners, family, friends, exes):
  omit from the written reply even when the transcript uses them.
- **Renderer content is paraphrased, never quoted.** A rated profile artifact is
  generated *about* the user, so its cards routinely name people, places,
  employers, health details, and circumstances — more densely than a transcript
  does. On a profile-shape reply, describe what a card claims and why it does or
  doesn't fit, without reproducing its sentences. The reply lands in a public
  channel, and this is the one shape whose source text is a profile of the person
  being discussed.
- **Duplicate surfaces.** The same `conversation_id` is often rated on more than
  one surface (a `conversation` and a `chat` entry sharing one conversation_id,
  sometimes with different UUID capitalisation). Write **one** analysis and post
  the **identical** reply body to **each** surface's own thread `ts` (each
  surface got its own k8bot message and needs its own reply) — but count the
  session **once** in any summary language ("2 surfaces, counted once").

### Step 6 — Post the threaded reply

**Reply template (exact):**

```
:mag: *Rating review — <polarity> · uid `<8-char prefix>` · prompter v<version>*
*Session (<conversation|chat|qp|coaching|journal|profile>):*  <2–4 sentence factual account>
*Tenure & engagement:*  <Aura clause> · <Aurie clause> · <lifetime volume>
*Why <polarity> makes sense:*  <the interpretive read>
*Signal:*  <neg/neutral: the product signal · positive: Marketing-usable / Not marketing-usable + reason/quote>
*Follow-up*
• <🧠 Prompt · 🛠 Product/eng · 📊 Data check · 💬 CS — only the lines that apply; omit the section when nothing is actionable>
```

- **Header variants:** journals use `· Quick Process journal` in place of the
  `prompter v…` clause; profile ratings use `· profile rating`.
- **`*Tenure & engagement:*` renders on every entry, every shape** — from the Step 3b
  call. Both tenure clauses come straight from that step's classify-then-render rules;
  never write a bare date for a stamped clock. Lifetime volume is completed
  conversations · messages · distinct active days (+ current/best streak when notable,
  and voice minutes when non-zero). **No subscription state** — Step 3b calls only the
  engagement endpoint, so there is nothing to source it from; don't add a
  `subscriptions/list` call for a stat line. Zeroes render as zeroes with
  the plain clause *"no Aurie activity on record"*; a failed call renders
  `unavailable — <reason>`. **Never omit the line** — it is what tells a reader how much
  weight the rating carries, and its absence is indistinguishable from a dormant account.
- **Use it in the read, don't just report it.** A thumbs-down from an account with
  hundreds of conversations across months is a different finding from one from a
  two-session account; a progress-framed profile card landing well on a
  44-conversation account is evidence the *material* was there, not that the card shape
  is safe generally. Feed that into the `Why <polarity> makes sense` and `Signal` lines
  rather than leaving the numbers stranded on their own line.
- **Sensitive entries:** end the Signal line with a one-line safety note (e.g.
  "No active suicidal ideation") confirming the check was done, plus the
  prompter version tag.

- **The Signal line is an observation plus a falsifiable hypothesis, never a prompt
  change.** This sweep posts unattended, with nobody reviewing the claim before it
  lands, so the discipline matters more here than on the interactive path. A
  prompter-version or config change is the prompt engineer's call off the accumulated
  corpus; a Braintrust eval case is in scope. State the evidentiary weight — a rating
  with no typed feedback is thin on its own — and never write a **designed** behaviour
  up as a defect (the sentinel ending a conversation is the worked example). Full rule:
  [`references/signal-scope.md`](references/signal-scope.md).
- **`*Follow-up*` closes the card** (negative and neutral entries; a positive gets one only when there is a concrete marketing or product action). Up to four bullets — 🧠 prompt candidate · 🛠 product/eng · 📊 data check · 💬 CS — only the ones that apply, none when nothing is actionable. The Signal rule above still holds: the prompt line is a **candidate** rule paired with the data check that would justify it, drafted only after reading the current prompt text and checking what the model actually received, never a decided change. Link an existing Linear card, never create one. Format, rules and worked examples: [`references/follow-up.md`](references/follow-up.md).

**Draft each reply body via the Write tool to a temp file first.** Never build
multi-paragraph Slack text with inline `python -c` and escaped quotes — that has
broken with `SyntaxError` mid-post, leaving an entry recorded as answered when no
reply had landed.

**Race-check immediately before each post.** Re-run the Step 2 reply check for that
`ts`; if a bot reply now exists, **skip** it. Group 2 sweeps this same channel and a
sibling Group 1 tick can overlap, so between Step 2 and here another session may have
claimed the thread. This check is the **only** double-post guard
([`references/dedup.md`](references/dedup.md) Rule 5) — the dedup pass at the top of
the tick is a snapshot, not a lock.

**Post by composing [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)**
— one invocation per thread `ts`, with `channel=C07MB0TMF5J`,
`thread_ts=<ts>`, the drafted reply body, and `footer=data:investigate-user-rating`.
Compose the shared skill rather than hand-rolling a Slack call; matches how
`data:investigate-user-rating` posts.

The `footer=data:investigate-user-rating` value attributes the post to the
analysis skill and auto-stamps *its* current version — this cadence skill has no
separate Slack footer identity. Pace **~0.6s** between consecutive posts so a
multi-entry tick doesn't burst the channel.

### Step 7 — Record negative and neutral conversations; no dedup state

After a **negative or neutral** entry's normal review has posted on a conversation-rated shape
(`qp` / `coaching` / `conversation` / `chat`), record it to `Learnings/Conversations` via
`shared:record-knowledge` (`caller=data:run-rating-feedback-analysis-cadence`), deduped on
`conversation_id` — this skill's own decision, because the Conversations loop improves the
prompts from that table. A write failure never touches the reply; failed creates and
updates are counted apart because only a create is recovered by the backfill. Eligibility, fields, body and invocation:
[`references/record-conversation.md`](references/record-conversation.md).

There is no dedup state to update. **The posted reply IS the record** — the next tick's Step 2
check sees it and skips the entry. No file is written, so there is nothing to prune, cap, or
keep in sync between sessions.

What this makes mandatory instead: **every entry that reached a terminal outcome must end with
a reply on its thread.** An entry with no reply is by definition still unprocessed and will be
retried next tick. Three terminal outcomes, three replies — the normal review, the
content-safety placeholder (Step 4), and the error reply for a terminally unanalysable entry
(Step 3 / [`references/edge-cases.md`](references/edge-cases.md)).

## Output

Per tick, a short readout to the user: counts (`in-window N → internal-filtered → unanswered
M`), then one line per posted reply (UID prefix · polarity · one-clause signal), plus any
content-safety flags raised, any error replies posted, and the Step 7 recording counts. On a zero-unanswered tick, just
report the counts and stop.
State the internal-filtered count **even at zero** (plus `test_reasons` when non-zero, and
`enrichment.reason` when the Aura-API join didn't run) — a filtered entry that goes
unmentioned is indistinguishable from one that never arrived.

## Edge cases

The per-tick rules — the zero-unanswered stop, the transient-vs-terminal split behind the
error reply, the recurring open-questions product signal, and the bare-"sweep" path — live
in [`references/edge-cases.md`](references/edge-cases.md). Read them before the first post.

## References

- Analysis engine: [`data:investigate-user-rating`](../investigate-user-rating/SKILL.md)
  (shape parsing, `fetch_ratings.py`, `fetch_logs.py`, the full per-entry
  `profile-user` path this cadence deliberately skips).
- Dedup contract: [`references/dedup.md`](references/dedup.md) — shared with Group 2; the
  reply check, the every-outcome-posts rule, error-as-reply, and the race check
  ([ENG-1055](https://linear.app/aura-health/issue/ENG-1055)).
- Poster: [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)
  (threaded reply + `footer` versioned-attribution contract — Step 6).
- Reader: [`shared:read-slack`](../../../shared/skills/read-slack/SKILL.md)
  (thread-replies read over the 429-retry transport — the Step 2 dedup check).
- Spec: AI Knowledge → `Feedback Analysis — Run Cadence` (Group 1 vs Group 2).
- Channel + footer conventions: AI Knowledge → `Tools/Slack`.
