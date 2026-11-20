---
name: report-aurie-conversations
description: >
  Weekly (or any-period) Aurie conversation analytics report. Reports surface coverage — how
  much of the ledger's own call total this run actually read, per OpenAI API, so a partial
  read is never presented as the answer — plus active conversation users by type, number of
  conversations by distinct conversationId, voice conversations and voice %, duration
  distribution and turns, voice vs text-only on the same basis, and full cost-to-serve across
  conversational LLM, voice, and conversation-triggered side-effect projects, with per-day,
  per-conversation, per-turn and per-voice-conversation figures. A conversation is one
  conversationId, duration first-to-last turn. Presets `last-7d` (default), `last-30d`,
  `month-to-date`, `previous-month`, or `--since`/`--until`. Trigger on "aurie conversation
  report", "weekly conversation report", "how many conversations this week", "active
  conversation users by type", "voice vs text conversations", "how long are conversations", or
  "cost per conversation".

metadata:
  version: "3.3.1"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Tools/Aura API       # llmCall ledger (BigQuery llm.llm_call) — the index for BOTH OpenAI APIs (ENG-1113)
    - Tools/OpenAI         # conversation project, prompter→app routing, metadata.user/name/conversationId
    - Tools/ElevenLabs         # ConvAI conversations, dynamic_variables.conversationId, retention
    - Compliance/Privacy   # UID truncation; never render message text / names
    - Products/Aurie  # Technology page — clients, transports, prompter inventory
  writes:
    - Signals/Aurie Conversations   # opt-in Step 3: record this run's report as a dated snapshot
---

# Report Aurie Conversations

A repeatable conversation-analytics report for Aurie, on a single consistent
unit: the **`conversationId`** (a persistent conversation thread). Companion to
the cost skills (`eng:analyze-openai-cost-per-user`,
`eng:analyze-elevenlabs-cost-per-user`) — this one answers *engagement* shape
(how many conversations, who, how long, voice vs text) rather than dollars.

## What it reports (per window)

1. **Active conversation users by type** — distinct `metadata.user` per
   conversational prompter (coach / quick-processing / chat / companion, plus
   insight-card / quick-processing-done / guardrails-handler as secondary). The
   three secondary types:
   `guardrails-handler-prompter` is a live in-conversation reply substituting for
   a coach turn (a safety/crisis deflection via `handoffToGuardrails`).
   `insight-card-prompter` is a card generated when the client ends a conversation
   (`createInsightCard=true` on `POST /conversations/:id/end`), shown on the app's
   done screen; it stamps its **own** `conversationId`, distinct from the coach
   conversation it summarizes, so it's excluded from the conversation count in
   item 2. `quick-processing-done-prompter` is the quick-processing equivalent
   (`createQuickProcessingDone=true` on the same endpoint), and differs on that
   last point: it carries the conversation's **own** `conversationId`, so its calls
   join to the conversation they close. `journal-prompter` is listed too — a single-shot prompter in its own
   OpenAI project, not a conversation turn. The table is built from the ledger
   rather than a fixed list, so a prompter this section doesn't name still gets
   a row, marked as discovered.
2. **Conversations** — distinct coach `conversationId`.
3. **Voice conversations** — conversationId threads that had an ElevenLabs voice
   session, matched via the Aura `conversationId` dynamic variable + voice users
   + voice %.
4. **Conversation duration** — `max(created) − min(created)` across a thread's
   coach turns, as mean / p25 / p50 / p75 / p95 / max (minutes) + turns/conversation.
5. **Voice vs text-only** — the same duration + turn distributions split by
   whether the thread had voice.
6. **Cost (window)** — full conversational cost-to-serve, all from the OpenAI
   Costs API (authoritative) + ElevenLabs:
   - **Conversation LLM** — OpenAI Conversation [Production] project.
   - **Voice** — ElevenLabs minutes × \$0.09 in-plan / \$0.19 burst.
   - **Direct conversation cost** = Conversation LLM + voice.
   - **Side-effects (triggered)** — the conversation-triggered pipelines that
     bill to their own OpenAI projects: Memory, Summary, Profile, Journal,
     Notification, Feed, Insights. These are real conversation cost (an Aurie
     conversation fires memory writes, an end-of-conversation summary + profile,
     an insights compilation, and feeds the daily journal/notification/feed
     crons), so they're **included** in the full number.
   - **FULL cost-to-serve** = direct + side-effects, with **per-day**,
     **per-conversation**, **per-turn** computed on the full number, plus
     **per-voice-conversation** (voice $ only).
   Caveat: Memory / Summary / Profile / Insights scale ~per conversation, but
   Journal / Notification / Feed are **activity-gated crons** (per active
   user/day, not per conversation) — they're folded into the per-conversation
   number as a lump, so treat that figure as approximate. For the full org cost
   breakdown by project/prompter use `eng:analyze-openai-costs`.

## Step 0 — Prerequisites

```bash
test -n "$GCP_KEY" && echo "ledger (BigQuery): OK" || echo "ledger (BigQuery): MISSING GCP_KEY (required)"
test -n "$OPENAI_ADMIN_API_KEY" && echo "openai admin: OK" || echo "openai admin: MISSING (required)"
test -n "$ELEVENLABS_ADMIN_API_KEY" && echo "11labs admin: OK" \
  || (test -n "$ELEVENLABS_API_KEY" && echo "11labs fallback: OK" || echo "11labs: MISSING (voice sections skipped)")
```

- `GCP_KEY` is **required** — hard fail if missing. Every activity number comes from the `llmCall` ledger (BigQuery `llm.llm_call`), which is the only index for Responses-API traffic. See "Where the activity numbers come from" below.
- `OPENAI_ADMIN_API_KEY` (`sk-admin-…`) is **required** — hard fail if missing. Used only for the `/v1/organization/*` cost + project-list calls, which still accept Admin keys.
- `ELEVENLABS_ADMIN_API_KEY` (or `ELEVENLABS_API_KEY`) is **optional** — without it
  the voice sections are skipped with a note (graceful degradation). Pass
  `--no-voice` to skip the voice walk deliberately (much faster).
- `python3` with `requests` (`pip3 install requests`).

No OpenAI project-scoped key is needed: this skill reads no stored completions or
responses. It counts calls and threads, never transcripts.

### Where the activity numbers come from

Activity comes from the Aura **`llmCall` ledger** (BigQuery `llm.llm_call`), which records one
row per LLM call across **both** OpenAI APIs and carries every field this report
aggregates: `userId`, `conversationId`, `prompterName`, `project`, `createdAt`.

**It is the only index that can see all the traffic.** Aurie runs on the **Responses API**,
and `GET /v1/responses` rejects API keys — stored responses are readable by id but not
*enumerable*. OpenAI's stored-completions list (`GET /v1/chat/completions`) therefore
returns only the chat-completions remnant, with no error to indicate the rest is missing.
On 2026-08-04→08-11 that remnant was 45,356 of 108,899 conversation-project calls (42%),
showed `insight-card` as 0 against a true 1,482, and omitted journal entirely.

Being bounded server-side by `from`/`to`, the ledger walk is also fast: **~1–3 min** for a
7-day window.

**Two guards, both fail-closed** — a partial read must never present itself as the answer:

- **§0 Surface coverage** cross-checks the walk against the ledger's own `/stats`
  aggregate every run and prints per-API shares. A shortfall prints the deficit in calls
  and marks every count a floor. This is reported as a *share*, never as a "both surfaces
  were read" flag — a boolean cannot distinguish a trivial gap from a total one.
- **Ledger history.** The ledger began recording **2026-07-23**. A window reaching further
  back is *refused*, not silently under-reported. Pass `--allow-partial` to accept floor
  counts deliberately.

  The refusal is load-bearing, not caution: an uncovered day does not degrade, it returns
  **zero rows** — indistinguishable from a quiet week. That is the same failure class as
  the surface gap above, so a run that reported it as a result would reintroduce exactly
  the problem this design exists to prevent. The first covered day is *probed* rather than
  hardcoded, because the ledger's start is a deploy artifact and a literal date goes stale
  silently if the table is ever backfilled or rotated.

An unreachable ledger **hard-fails**. Returning chat-completions-only counts would
understate reality by roughly half while looking like a normal run.

### Reading the voice %

Voice % is `voice threads ÷ **observed** conversations` — a share of what the report could
see, not of all Aurie usage. The denominator therefore moves with §0 coverage.

**The denominator is coach threads only** — `chat`, `journal` and `insight-card` never
enter it (journal is not a conversation; insight-card stamps its own `conversationId` for
a card *about* a finished thread). That trips up the natural inference: a coverage change
on a text-only surface moves the per-type table without moving voice % at all, while a
change in coach's own coverage moves the denominator with voice *and* text threads mixed
together.

So **don't reason about the direction — re-run and compare.** Worked example: closing the
Responses gap was expected to lower the share (the missing surfaces looked like text
ones); measured, it went **43% → 45%**, up. Any figure carried over from a run with
different §0 coverage — such as the 43% on the
[2026-08-10 snapshot](https://app.notion.com/p/Aurie-Conversation-Report-2026-08-10-snapshot-3b875e23af2081ed8030f5dadc6d6dc8)
— is not comparable and should be restated rather than quietly superseded.

Conversation ids are folded to upper case everywhere they are keyed, on both the thread
side and the voice side. Aurie writes the UUID upper-case on voice paths and lower-case
elsewhere, so an unfolded key splits one thread in two (32 of 587 on 2026-08-09, ~5.5%)
and leaves lower-case threads unable to match a voice session at all. Folding in only
some places is worse than none — §1 and §2 then disagree with no sign of which is right.

## Step 1 — Run

```bash
# Default: last 7 days, with voice
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-aurie-conversations/scripts/report.py

# Other periods
python3 .../report.py --window last-30d
python3 .../report.py --window month-to-date
python3 .../report.py --window previous-month
python3 .../report.py --since 2026-05-19 --until 2026-05-26   # UTC, half-open

# Faster (skip ElevenLabs voice walk)
python3 .../report.py --window last-7d --no-voice

# Structured JSON alongside the rendered report
python3 .../report.py --json /tmp/aurie_conversations.json
```

**Runtime.** The OpenAI completion-list walk is ~10–15 min on a 7-day window
(one paginated list walk of the Conversation project — no per-completion detail
fetch). The ElevenLabs voice walk adds ~8–10 min (one detail fetch per voice
conversation, for the Aura conversationId). `--no-voice` skips the second walk.

**Past windows are slower.** The `/chat/completions` list endpoint is
newest-first with no server-side time filter, so a window that ends in the past
(`previous-month`, or `--since/--until` ending before today) pages through all
the *newer* completions before reaching the window — `previous-month` walks the
whole month-plus since. The default `last-7d` (and any window ending ~now) has
nothing newer to skip, so it's the fast path. Same limitation as the
`eng:analyze-openai-cost-*` skills (shared list-walk pattern).

## Step 2 — Show the report

Print the script's stdout. Lead with the headline: conversations, % voice,
median duration. Example framing:

> _Last 7 days: **1,373 conversations** from 418 coach users; **39% had voice**
> (260 voice users). Median conversation ran **8.9 min / 10 turns**; voice
> conversations are turn-denser (median 17 turns vs 8 for text)._

## Step 3 — Offer to record the snapshot in AI Knowledge → Signals → Aurie Conversations

After the report renders, finish the run by offering to **persist this snapshot** under `Signals/Aurie Conversations`. Future skills (next week's report doing week-over-week, `analyze-experiment` baselining voice %, anyone asking "how is Aurie trending?") read these snapshots via `retrieve-knowledge` — that institutional memory only exists if we record it.

Like the analogous step in `data:analyze-user-conversations`, this is **opt-in and requires explicit per-turn approval**. Writing to AI Knowledge is a side-effect visible to others — the caller has to say yes. Default to **offer** (not silently skip) so the loop closes for the typical case.

**Step 3a — Pre-check for today's snapshot.** List child pages of `Signals/Aurie Conversations`. If a snapshot dated today already exists for this `window_label`, frame the offer as "already recorded — link below" rather than asking again. Snapshots are immutable; re-runs of the same window on the same calendar day don't get a second page.

**Step 3b — Ask.** Phrase it tightly:

> *"Want me to record this report as a snapshot under `Signals/Aurie Conversations`? It'll be findable next week for WoW comparisons and by anyone asking how Aurie trended this period."*

If the caller declines, stop — the inline report from Step 2 already covers the ask.

**Step 3c — Draft + write.** On approval, compose the payload per the [`Signals/Aurie Conversations` schema](../../../shared/skills/record-knowledge/references/schemas/aurie-conversations.md):

```yaml
path: Signals/Aurie Conversations
action: create
fields:
  producer: "Aurie Conversation Report"
  snapshot_date: <today, UTC ISO date>
  window_label: <e.g. "Last 7 days">
  since_iso: <window start, ISO datetime from the JSON sidecar>
  until_iso: <window end, ISO datetime from the JSON sidecar>
  headline: <one-line summary built from totals: conversations, voice %, per-conversation $>
  conversations: <int>
  active_coach_users: <int>
  voice_pct: <number 0–100>
  voice_users: <int>
  voice_minutes: <int>
  total_cost_usd: <number>
  llm_cost_usd: <number>
  voice_cost_usd: <number>
  side_effects_cost_usd: <number>
  per_conversation_usd: <number>
  body_md: |
    <the full rendered report markdown from Step 2 — verbatim, lightly stripped of progress noise>
sources:
  - label: "Producer skill — data:report-aurie-conversations"
    url: "https://github.com/AuraHealth/AINative/tree/main/plugins/data/skills/report-aurie-conversations"
    primary: true
  - label: "OpenAI Conversation project usage"
    url: "https://platform.openai.com/usage?project=proj_esFyd4eq2VIAjKUn69X1ZFfk"
  - label: "ElevenLabs workspace usage"
    url: "https://elevenlabs.io/app/usage"
```

Invoke `shared:record-knowledge` with the payload. Surface the resulting Notion URL inline so the caller can verify the snapshot landed.

If `--json` was passed in Step 1, you already have the structured fields ready — the JSON sidecar exists precisely for this handoff.

## Methodology

- **OpenAI side.** Walks the **Conversation [Production]** project
  (`proj_esFyd4eq2VIAjKUn69X1ZFfk`; override with `AURA_CONVERSATION_PROJECT_ID`)
  — every conversational prompter logs there. Groups stored completions by
  `metadata.name` (prompter), `metadata.user`, `metadata.conversationId`. For
  coach completions it tracks per-conversationId `min`/`max` `created` (→
  duration) and a turn count.
- **ElevenLabs side.** Lists `/v1/convai/conversations` for the window and
  fetches each conversation's detail to read
  `conversation_initiation_client_data.dynamic_variables.conversationId` — the
  **Aura** conversationId. Distinct Aura conversationIds = voice threads.
- **Voice conversations = voice threads ∩ OpenAI coach thread set.** The OpenAI
  and ElevenLabs windows are aligned to the **same unix range** — a window
  offset otherwise biases the voice % (an earlier ~16h offset produced 35% vs
  the aligned 39%).
- **Cost.** OpenAI $ comes from the **Costs API** (`group_by project_id`) —
  authoritative billed dollars, not token back-calc. The Conversation project is
  the conversational LLM line; the seven side-effect projects (matched by name,
  `<name> [Production]`) are summed as the side-effect line. Voice $ is summed
  during the ElevenLabs walk (in-plan vs burst minutes × rate). The cost fetch
  adds two quick API calls (costs + project-name lookup) — negligible vs the walks.

## Sample output (last 7 days, 2026-05-19 → 26)

```
## 1. Active conversation users by type
type                         users  conversations    calls
coach                          418           1373    30746
quick-processing                89            170      975
chat                             6              8       38
companion                        1              3       16
insight-card                    64            167      167  (end-of-conversation card, own conversationId)
guardrails-handler              57             66      115  (secondary — in-conversation reply)

## 2. Conversations in period: 1,373
## 3. Voice conversations: 531 = 39% (61% text-only); 260 voice users

## 4. Conversation duration — ALL (n=1,373)
   duration min: mean 60.9  p25 2.3  p50 8.9  p75 25.3  p95 162.3  max 6294
   turns:        mean 22.4  p25 4  p50 10  p95 76  max 793

## 5. Voice vs text-only
VOICE (n=531):  duration p50 8.1  p95 119.9 ; turns p50 17  p95 140
TEXT  (n=842):  duration p50 9.3  p95 221.1 ; turns p50 8   p95 38

## 6. Cost (window)
   Conversation LLM (OpenAI):   \$417.22
   Voice (ElevenLabs):          \$572.09   [6,357 min]
   Direct conversation cost:    \$989.31
   Side-effects (triggered):    \$257.10   [Memory \$65 · Summary \$46 · Profile \$45 · Notification \$39 · Feed \$37 · Journal \$25]
   FULL cost-to-serve:          \$1,246.41
   per day:                     \$178.06
   per conversation:            \$0.964
   per turn:                    \$0.0464
   per voice conversation:      \$1.126  (voice $ only)
```

Voice is the largest single share (~46% of full cost-to-serve) despite being
only ~39% of conversations — voice minutes (\$0.09/min) dominate. Side-effects add
~21% on top of the direct conversation cost. Per-conversation/turn/day figures use
the **full** cost-to-serve; the crons (journal/notification/feed) are activity-gated
so the per-conversation number is approximate. For the full org breakdown by
project/prompter, use `eng:analyze-openai-costs`.

## Notes & edge cases

- **`conversationId` IS the conversation** — a persistent thread that can span
  days (max observed 6,294 min ≈ 105 hrs). Duration is the thread's active span
  (first→last turn), **not** a session. The ElevenLabs per-*call* duration
  (median ~6 sec — many instant connect/disconnect calls) is the wrong unit; a
  voice conversation is the conversationId thread (~2.9 voice calls per thread).
- **Voice is turn-denser than text** — similar median duration (~8–9 min) but
  ~2× the turns (spoken back-and-forth). Text threads have a longer tail (people
  return over more days).
- **Privacy.** The report is aggregate; it reads `metadata.user` only to *count*
  distinct users and never renders raw UIDs, message text, titles, summaries, or
  names (`Compliance/Privacy`). If a future variant surfaces per-user rows,
  truncate UIDs to the first 8 chars + `…`.
- **Voice is only available for ~the last week.** ElevenLabs deletes conversation
  detail — including the `dynamic_variables` we read the Aura `conversationId`
  from — after the workspace **retention period (10-day config; treat ~7 days as
  the reliable window)**. So voice attribution only works for recent windows: for
  `last-7d` it's complete; for `last-30d` / `previous-month` / older
  `--since/--until`, the older portion's detail 404s and **voice % is
  undercounted**. The script warns on stderr and in the report when the window
  starts before retention, and the conversation/user/duration sections (OpenAI,
  ~30-day completion retention) remain correct regardless. Override the threshold
  with `ELEVENLABS_RETENTION_DAYS`. For long-window reports, prefer `--no-voice`
  (skip the misleading partial voice numbers) and run voice separately on a
  `last-7d` window.
- **Window alignment.** Presets are UTC-anchored; the same unix range is used for
  both APIs so the voice % isn't biased by an offset.
- **Project drift.** If the Conversation project is renamed/rotated, set
  `AURA_CONVERSATION_PROJECT_ID`. Prompter names come from
  IntelligenceService `src/config.js` (see `Products/Aurie` → Technology).
- **Cowork.** Host env is stripped in Cowork sandboxes (PROD-1305) — paste-export
  the keys per session, or run on the host.
