# Algorithm reference — `eng:report-elevenlabs-user-cost`

Short technical note on how the per-user ElevenLabs cost is computed.
Loaded by the SKILL.md on demand.

## The pattern

```
1. List /v1/convai/conversations in the window (cursor-paginated).
2. For each conversation, GET /v1/convai/conversations/{id}.
3. Read userId from conversation_initiation_client_data.dynamic_variables
   — the Aurie mobile / desktop client (which calls ElevenLabs directly,
   NOT via IntelligenceService) adds this dynamic variable when the user
   is known. Pre-auth / anonymous calls have no userId set; those drop
   out of per-user attribution entirely (see "Unattributable calls" below).
4. If userId matches the target UID, accumulate duration_secs split by
   is_burst (from metadata.charging).
5. Cost = duration_secs / 60 × ELABS rate per bucket.
```

Per-call $ comes directly from `call_duration_secs / 60 × rate` —
$0.09/min in-plan, $0.19/min burst (Business plan rates from
`Tools/ElevenLabs` in AI Knowledge). No credits→minutes derivation;
calculation is `minutes × rate`, full stop. Same source rates as the
rank-N sibling [`eng:analyze-elevenlabs-cost-per-user`](../../analyze-elevenlabs-cost-per-user/SKILL.md).

## Why two API calls per conversation

ElevenLabs' list endpoint (`/v1/convai/conversations`) doesn't return
`dynamic_variables` on its summary objects — only on detail. So a
conversation's `userId` can only be read from a detail fetch, and that
part has no shortcut.

**What does have a shortcut is deciding *which* conversations to detail-fetch.**
The list endpoint accepts a `user_id` query parameter and filters
server-side, so the walk is `O(that user's calls)` instead of
`O(workspace voice volume)`. Measured 2026-08-18 against a real user on
`--window today`: **318 → 18** detail fetches (17.7× less work, 26.9s),
with cost, call count and duration **identical** to the unfiltered walk.
Without it, a dormant user costs exactly as much to query as a heavy one,
and the walk grows on its own with Aurie traffic.

**The client-side `userId` check stays, and is not redundant.** ElevenLabs
ignores unknown query parameters *silently* — a deliberately bogus param
returns `200` with a full unfiltered page. So if `user_id` is ever renamed
or dropped, the filter stops filtering without erroring, and every
workspace conversation would be attributed to the requested user. The
client-side check is what makes that failure mode a *slowdown* rather than
a massive over-count: the cost stays correct, the walk just degrades to
`O(workspace)`. `report.py` reports that degradation rather than absorbing
it — `server_side_user_filter_effective: false` plus a caveat and a stderr
warning when a majority of listed conversations get rejected.

The walk only covers days inside the 10-day retention window. Older
days come from the cost store — see *The 10-day retention cutoff, and
the store half* below.

## Unattributable calls

ElevenLabs Conversational AI is called **directly by the Aurie mobile
and desktop clients** — NOT via IntelligenceService. The clients add
the `userId` dynamic variable when the user is signed in. Two
categories of calls won't have one:

- **Pre-auth / anonymous calls.** A new user trying voice before
  account creation; an unauthenticated browse session on web. These
  conversations still exist in the workspace's spend (and the
  workspace-level [`eng:analyze-elevenlabs-costs`](../../analyze-elevenlabs-costs/SKILL.md)
  skill captures them) but they can't be attributed to any specific
  UID, so this skill silently skips them. Same behaviour as the
  rank-N sibling.
- **Implementation gaps** (testing harnesses, agent-internal calls,
  any future client that forgets to pass the dynamic variable). Also
  silently skipped from per-user attribution.

When the total of unattributable calls is large enough to skew
workspace-level math, the rank-N sibling
[`eng:analyze-elevenlabs-cost-per-user`](../../analyze-elevenlabs-cost-per-user/SKILL.md)
reports the gap as an `untagged` bucket. This skill doesn't surface
it (a single-user lookup can't usefully say "you might also have
some untagged anonymous calls"); use the workspace-level skill if
that question matters.

## The 10-day retention cutoff, and the store half

The Aura workspace runs ElevenLabs Conversational AI with a
**10-day Conversations Retention Period** (set on the ElevenLabs Privacy
panel). After 10 days, ElevenLabs deletes the transcript, the audio and
the dynamic variables (including `userId`). The live walk therefore
cannot attribute an older call.

The script splits the window instead of losing those days:

```
split     = first UTC midnight after (now − 10 days)
store     = whole UTC days of the window before `split`
            → SUM(voice_11labs_agent) from aura-main.aurie_cost.aurie_per_user_daily_cost
live      = [max(window start, split), window end)
            → the walk below, unchanged
```

- **No day is read twice.** The store's last day is `split − 1 day` and
  the walk starts at `split`.
- **A store day with no rows at all is pending**, never $0. A stored day
  with no row for the user is a real $0.
- **The store half runs only when it has days.** `last-7d` never
  imports BigQuery. A store that can't be reached reports `unavailable`
  (or `error`), exits `2`, and leaves the live half intact.
- **The column is the ConvAI slice only.** `voice_11labs_livekit` belongs
  to `eng:report-livekit-user-cost`. `tests/test_livekit_user_cost.py`
  pins that split, and `tests/test_elevenlabs_store_split.py` pins this
  one.

## Cost rate constants

Pulled from `Tools/ElevenLabs` in AI Knowledge (Business plan):

| Bucket | Rate | Trigger |
|---|---:|---|
| In-plan | $0.09/min | `metadata.charging.is_burst = false` |
| Burst | $0.19/min | `metadata.charging.is_burst = true` |

In-plan rate applies even on workspace usage that has crossed into
overage — we can't attribute the marginal overage minute to a specific
call. The workspace-level skill
[`eng:analyze-elevenlabs-costs`](../../analyze-elevenlabs-costs/SKILL.md)
reports the live `current_overage.amount` on the subscription endpoint
when we're over.

## Why this skill exists vs the rank-N sibling

The rank-N sibling ranks the top-N users and includes a distribution
stats block. For a single-user lookup that's overkill — the skill walks
the same conversations the rank-N does, but renders only one row of
output instead of ranking everything.

Wall-clock is similar to the rank-N sibling (same N detail fetches
required). The orchestrator parent `eng:report-user-costs` calls this
skill for its ElevenLabs slice; the structured `--json` output is
designed for that composition.

## What this skill does NOT do

- **Trend lines.** Day-over-day cost shift for one user. If needed,
  call the skill twice with different windows and diff.
- **Cohort analysis.** Use the rank-N sibling.
- **OpenAI inference or Aura content-listen cost.** Sibling skills
  handle those — `eng:report-openai-user-cost` and
  `eng:report-listens-user-cost`; LiveKit-path voice (including
  ElevenLabs TTS on LiveKit sessions) by `eng:report-livekit-user-cost`.
  The parent `eng:report-user-costs` composes all four.
- **Audio / transcript download.** Out of scope for cost reporting.

## Privacy posture

UIDs are not PII on their own, but they're user-identifiable in the
Aura DB. The skill follows the same posture as the rank-N sibling:

- UID is truncated to first 8 chars + `…` in rendered markdown.
- `givenName` from any ElevenLabs response is **never** read or
  rendered.
- `--json` output retains the full UID (caller-controlled file path;
  caller is responsible for not pasting it into public surfaces).
- See `Compliance/Privacy` in AI Knowledge for the canonical rule.
