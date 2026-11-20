---
name: run-cancellation-feedback-analysis-cadence
description: >
  Runs the recurring, unattended Cadence Group 4 cancellation-feedback sweep across the churn
  channels — #ai-cancellation-feedback and #aurie-feedback — profiling every genuinely-new
  canceller and AUTO-POSTING a threaded reply. The unattended auto-post wrapper around
  `data:investigate-cancellation-feedback`: it owns the trailing-window fetch, the dedup
  pre-check, the sequential profiling via `data:profile-user`, and the auto-post, with no
  human in the loop. It is the analysis leg of Group 4, distinct from the Group 4 harvest run
  — so "run Group 4" alone is ambiguous. Trigger on "run the cancellation-feedback cadence",
  "run the Group 4 analysis", "run the cancellation-feedback analysis cadence", "cancellation
  cadence sweep", "auto-post the cancellation feedback", or "keep sweeping cancellations every
  30 min". Do NOT use for the interactive draft-and-confirm review.

metadata:
  version: "3.6.0"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Tools/Slack          # auth.test, conversations.replies, threaded auto-post + versioned footer
    - Compliance/Privacy   # posts user-scoped output to Slack — UID-prefix + email-domain only
    - Tools/Mixpanel                 # Mixpanel service-account / MCP access
    - Tools/OpenAI                   # OpenAI project keys + stored-completions access
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
---

# run-cancellation-feedback-analysis-cadence

The **unattended** cancellation-feedback sweep (a.k.a. the "cancellation-feedback
cadence"). It is the auto-post wrapper
that wraps [`data:investigate-cancellation-feedback`](../investigate-cancellation-feedback/SKILL.md)
and its per-user engine [`data:profile-user`](../profile-user/SKILL.md), adding the three things
an unattended cadence needs that the interactive skill deliberately does not have:

1. **All-entries scope** — profiles every real, non-test canceller, **text AND no-text**
   (the interactive default is `meaningful_text_only`). A no-text cancellation still carries
   signal in behaviour + conversations + cost; the dropdown reason drives the framing.
2. **Auto-post, no draft-and-confirm** — each new entry's reply is posted directly. This is
   safe only because it runs unattended on a schedule; a human is not present to approve each
   post. It relies on the durable, user-established autonomy for this specific workflow.
3. **Whole-window scope per invocation** — each run re-checks the full trailing window and
   picks up anything the dedup shows as unanswered, so a missed run self-heals on the next one.

**Analysis vs harvest (why the name).** Cadence Group 4 has two legs. This skill is the
**analysis** leg — it profiles each new canceller and posts a per-user threaded reply. The
**harvest** leg ([`data:harvest-cancellation-feedback`](../harvest-cancellation-feedback/SKILL.md))
aggregates the same cancellation feed into a store/report. "Run Group 4" alone doesn't say which;
`run-cancellation-feedback-analysis-cadence` names the analysis leg unambiguously.

**Relationship to the sibling.** `data:investigate-cancellation-feedback` is the interactive,
human-in-the-loop, one-shot tool: it shows the roster, walks entries one at a time, and posts
only on explicit opt-in. `run-cancellation-feedback-cadence` is the *machine* version of the
same job: no roster confirmation, no per-entry gate, all entries, auto-post, on a timer. Both
share the same deterministic fetch/parse (`fetch_cancellations.py`), the same dedup mechanism
(prior bot reply on the thread), and the same versioned Slack footer
(`data:investigate-cancellation-feedback`). This skill does not re-implement any analysis —
that all lives in `data:profile-user` and its sub-skills.

## When to use

- "run the cancellation-feedback cadence" / "start Group 4" / "run the cadence now"
- "auto-post cancellations" / "keep the cancellation sweep running"
- A tick of `data:run-all-analysis-cadence`, which sweeps this group with the other four.

**Do NOT use for:**

- A one-off, reviewed, draft-and-confirm sweep → `data:investigate-cancellation-feedback`.
- A single UID / conversation / rating → `data:profile-user`.
- PMF survey responses → `data:investigate-pmf-survey`.

## Prerequisites

- **`SLACK_BOT_TOKEN`** — the fetch (`fetch_cancellations.py`), the `auth.test` bot-identity
  resolution, the per-thread `conversations.replies` dedup check, and the threaded post all use
  it. The bot (`aura_ai`, `U0ATMPESR37`) must be a member of every channel swept —
  #ai-cancellation-feedback, #aurie-feedback, and #ai-feedback for pre-split history.
- **`data:profile-user` chain env** — Mixpanel (`MIXPANEL_SERVICE_ACCOUNT_*` + `MIXPANEL_PROJECT_ID=963821`),
  OpenAI (`OPENAI_CONVERSATION_API_KEY`), Aura API (`AURA_API_TOKEN`), cost (`OPENAI_ADMIN_API_KEY`).
  This skill doesn't check those directly — `profile-user` runs its own prerequisite checks and
  degrades per its own docs.
- **python3.12** for the scripts. Use the Python `requests` library for all Slack HTTPS calls
  (bundles certifi; direct `urllib`/`curl` can fail with `SSL: CERTIFICATE_VERIFY_FAILED`).
- **Live-repo `slack_post.py`** for posting — never a pinned `…/rpm/plugin_<hash>/…` snapshot
  path (a stale snapshot drops the footer version). Use
  `/Users/jeromecurlier/Development/Aura/AI/AINative/plugins/shared/skills/post-to-slack/scripts/slack_post.py`.

## Inputs

- **`window`** (optional, default `7d`) — trailing window for the fetch (`7d` / `14d` / `30d`).
- **`channels`** (optional, default `all`) — `all` sweeps #ai-cancellation-feedback +
  #aurie-feedback + pre-split #ai-feedback; `cancellations` scopes to the k8bot feed on both
  sides of the split; a single channel ID scopes to one.
- **No schedule input.** This skill sweeps once per invocation; it neither owns nor sets a
  cadence. Avoid running it concurrently with another group's sweep — the `profile-user`
  OpenAI fan-outs collide.

## Workflow

### Step 1 — Fetch every channel (deterministic)

Run the sibling's parser over the trailing window and capture its JSON:

```bash
python3 <investigate-cancellation-feedback>/scripts/fetch_cancellations.py --channel all --window 7d
```

Output wraps each channel under `channels[]` (byte-for-byte identical to a single-channel run),
with the built-in test/noise filter already applied (test feedback, QA blasts, the iOS
cancel-modal double-fire, dev Stripe price IDs). See the sibling's
[Channel formats](../investigate-cancellation-feedback/SKILL.md#channel-formats) for the parse
detail.

### Step 2 — Prior-analysis pre-check (idempotency)

Resolve the bot identity once via Slack `auth.test` (returns `U0ATMPESR37` / `aura_ai`). Then for
**every real entry** — text and no-text — call `conversations.replies` on **that entry's own
`channel_id` + `thread_ts`** and skip any thread that already has a reply whose `user` equals the
bot id (skip the thread parent itself). Only entries with **no** prior bot reply are "new". This is
what makes the cadence idempotent: re-running, or overlapping sweeps, never double-posts. Pace
~0.35s between checks to stay under Slack's Tier-3 `conversations.replies` limit.

> **Always read the channel from the entry's `channel_id` — never default or hardcode it.**
> `fetch_cancellations.py` emits `channel_id` on every entry (`C0BR3UTNL83` =
> `#ai-cancellation-feedback`, `C0AEK511BT9` = `#aurie-feedback`, `C0AENS8D48N` = `#ai-feedback`
> for pre-split cancellations). Because Step 1 flattens the `channels[]` wrapper, an entry's
> channel is only knowable from its `channel_id` field. Defaulting the channel runs the dedup check
> against the **wrong** channel — an already-answered `#aurie-feedback` entry then looks "new" and
> gets a duplicate reply misrouted to the wrong channel.

If `auth.test` fails, skip the run's posting and report the miss — never post without a resolved
bot identity (dedup would be blind).

### Step 3 — Profile each new entry (SEQUENTIALLY)

**One at a time — never a parallel fan-out.** Parallel profiling hit OpenAI / ElevenLabs rate
limits and lost in-flight agents in the originating sweeps. For each new entry, invoke
[`data:profile-user`](../profile-user/SKILL.md) (Mode B):

- `user_id=<uid>`
- `context = { kind: "cancellation", stated: "<feedback or ''>", category: <see below> }`
  - k8bot cancellation posts (#ai-cancellation-feedback, or pre-split #ai-feedback): `category = <parsed dropdown reason>` (e.g. `"Too expensive"`).
  - #aurie-feedback: `category = "free-text aurie-feedback channel post"`.
- `steering = "<feedback text>"` (empty string on no-text entries — the profile then leans on the
  categorical reason + behaviour + conversations).
- `sources = [behavior, conversations, cost]`
- `side_effects = { slack_reply: off, notion_record: off, braintrust_dataset: off }` — **this skill
  owns the post**, so profile-user stays silent.
- `time_window = 30d`
- `skip_prior_check = true` — Step 2 already ran the dedup; suppress profile-user's own pre-check.

**Time-box cost: ~20 minutes.** The per-user cost walk is slow (minutes) — let it run. Give it a
**~20 minute safety-net cap**: if it hasn't finished by then, record cost as **partial/pending** and
move on rather than blocking the post indefinitely on a genuinely hung call.

**A pending cost figure is never rendered as `\$0.00`.** The two are opposite claims — "we didn't
finish measuring" vs "this user cost us nothing" — and the distinction is load-bearing on a *"Too
expensive"* cancellation, where cost-to-serve is the whole question. Say `pending`, and say why.

The cap is a backstop for a stuck network call, **not** the expected outcome, and it is sized from
measurement so it stays one. Measured 2026-08-16 on a real canceller at `--window last-30d`, all
legs in parallel:

| Leg | Measured | Note |
|---|---|---|
| `report-listens-user-cost` | **0.6 s** | single POST |
| `report-openai-user-cost` | **8.8 min** | serial over the 18 `[Production]` projects |
| `report-elevenlabs-user-cost` | **0.9 s** – **22.9 s** | user-scoped; 22.9 s at 58 calls |
| `report-livekit-user-cost` | **3.5 s** | two BigQuery reads of the per-user cost store (measured 2026-09-25) |

20 min is ~2× the 8.8-minute OpenAI leg, which is the slowest of the four.
`eng:report-user-costs` collects by running its sub-skills' `report.py` scripts directly (its Step
1a), so a cost run here is bounded scripts rather than a nested agent fan-out. A step that reports
itself as in progress and returns no figure is the nesting symptom that path avoids: confirm the
composed skill is taking its Step 1a script route before raising the cap.

**Re-derive the cap from measurement; never nudge it.** The OpenAI walk is serial over the
`[Production]` project count, which grows on its own, so any fixed cap drifts toward firing on
ordinary runs. When it starts timing out, time the leg again and re-derive at ~2× — **a cap that
fires on ordinary runs is indistinguishable from a hang**, which is the failure this sizing exists
to prevent.

**All four cost legs run — both voice paths included.** Voice is the leg most likely to explain a
*"Too expensive"* cancellation, so leaving it out would blind the sweep on exactly its motivating
case: a measured heavy user came to **\$64.84 over 30 days**, which is the whole answer to why they
found it expensive. The ElevenLabs walk filters its list call server-side by `user_id`, so it costs
one detail fetch per *that user's* conversations — 0.9 s for a dormant user, 22.9 s at 58 calls — and
comfortably fits the cap. The LiveKit leg covers the other voice path. A canceller with LiveKit
minutes and zero ElevenLabs calls would otherwise show no voice cost at all. It reads the daily
cost store, so a cancellation on the day of the voice session reports LiveKit as **pending** for
that day. Carry that pending line into the reply, exactly as the rule above requires.

**A narrowed cost run names what it left out.** `cost_sources` is the mechanism (it forwards to
`eng:report-user-costs --sources`) and exists for a real constraint, such as a missing provider key.
Whenever it drops a leg, the reply says which — a partial total rendered as a whole one understates
cost-to-serve, which inverts the conclusion on a churn-for-price signal. Same rule as the pending
case above: name what is missing, and never write it as `\$0.00`.

Hold each returned profile for the Step 4 post.

### Step 4 — Auto-post the threaded reply

Compose a compact reply in **Account → Subscription → Conversations → Signal → Follow-up** shape and post it
as a threaded reply on the entry's source thread via the **live-repo** helper. **`--channel` MUST be
the entry's own `channel_id`** (from `fetch_cancellations.py`) — never default or hardcode it, or the
reply misroutes to the wrong channel:

```bash
python3 /Users/jeromecurlier/Development/Aura/AI/AINative/plugins/shared/skills/post-to-slack/scripts/slack_post.py \
  post --channel <entry channel_id> --thread-ts <entry thread_ts> --text-file <body> \
  --expect "<uid8>…" --max-chars 6000 --max-chunks 3 \
  --footer data:investigate-cancellation-feedback --permalink
```

**Card guards (mandatory).** `--expect "<uid8>…"` names this entry's redacted UID. `slack_post.py` refuses with exit 2 and sends nothing when the body lacks it, runs past 6,000 characters, or would split into more than 3 replies. That's how a body from another worker, such as a guardrail transcript, gets stopped. Report a refused post for that entry; never retry it without the guards. Write the body to your worker folder under a typed name, or pipe it on stdin (`--text -`); never a shared or generic file such as `c1.txt` (see `data:run-all-analysis-cadence` → *Scratch files and posting*).

`--footer data:investigate-cancellation-feedback` makes `slack_post.py` resolve and stamp the
versioned `Tools/Slack` footer from that skill's `metadata.version` (the cadence is a runner for
that skill's analysis, so its footer is the canonical attribution). Never hand-type a footer, and
never post via a raw `chat.postMessage`. Pace ~0.5s between posts; confirm each permalink.

**PII (hard rule, per `Compliance/Privacy`):** UID prefix (first 8 chars) only, email **domain**
only — never `givenName` / `familyName` / full email. Redact any names from quoted transcript
snippets. **`deletedAt`** on a subscription row is a backend artifact (Stripe/PaymentService
cleanup), **NOT** cancellation intent — never read it as the user asking to delete data.

Never edit a prior reply — new analyses always post as new replies.

**Close every reply with a `*Follow-up*` section** after `Signal` — up to four bullets (🧠 prompt candidate · 🛠 product/eng · 📊 data check · 💬 CS), only the ones that apply, and none at all when nothing is actionable. A prompt line is a *candidate* rule for the prompt engineer, drafted only after reading the current prompt text and checking what the model actually received; link an existing Linear card, never create one. Format, rules and worked examples: [`references/follow-up.md`](references/follow-up.md). The reply posts unattended, so the candidate discipline in that file matters most here.

### Step 5 — Digest

- **0 new entries:** report a one-line clean-sweep summary (`real` counts per channel), post
  nothing, and stop.
- **New entries posted:** give a short digest (one line per entry + permalink), and carry forward
  any cross-cutting pattern (see below).

**This skill does not schedule itself.** One invocation is one sweep. Repeat cadence is the
operator's — normally a tick of
[`data:run-all-analysis-cadence`](../run-all-analysis-cadence/SKILL.md), which sweeps this group
alongside the other four. **Auto-posting without a human in the loop is consequential**, so it
relies on the durable, user-established autonomy for this specific workflow; it is not a licence
to schedule the sweep unprompted.

## Pattern tracking (carry across runs)

This cadence surfaces recurring cross-cutting signals worth naming in each digest rather than
re-discovering per entry. The dominant one as of v1: a **no-trial paywall pattern**
(`aiFourStepNoTrial` / `aurie_webview` — users charged in full within seconds-to-minutes of
signup, no trial buffer, cancelling within days across every dropdown reason). Keep a running
count in digests and note new instances against it. Before filing a Linear ticket for a recurring
finding, check whether one already exists (e.g. the webview purchase-event mismatch) — don't
duplicate.

## Edge cases

- **Overlapping sweeps / manual re-run mid-run.** Idempotent by construction — Step 2's live
  prior-reply check means an entry already answered (or answered by a concurrent run) is skipped.
- **`profile-user` returns a partial picture** (no Mixpanel profile, zero OpenAI completions, cost
  pending). It degrades and flags the gap; carry that note into the reply rather than dropping the
  entry.
- **An entry can't be profiled at all** → split on whether retrying could help, because the
  Step 2 prior-reply check is the *only* dedup: an entry with no reply is retried next tick.
  - **Transient** (a 5xx, a rate limit, a network error, a missing token) → post nothing and
    report it. The absent reply is what schedules the retry.
  - **Terminal** (a malformed entry, a UID that resolves to nothing on any source) → post a
    one-line error reply so the thread is closed out instead of retried forever:

    ```
    :warning: *Cancellation review unavailable — uid `<8-char prefix>` · <reason>*
    Could not build a profile for this account (no Mixpanel profile, no Aura record).
    Closed out rather than retried.
    ```

  Never leave a terminal failure silent: with thread-state dedup a silent skip is
  indistinguishable from an entry that was never reached, and it re-runs the whole
  `profile-user` chain on it every tick.
- **Sub-agent nesting blow-up.** When delegating a profile to a sub-agent, instruct it to do the
  work directly (no further nested sub-agents) — nested fan-out has caused slow, non-converging
  runs. Force-finalize with whatever landed rather than waiting indefinitely.
- **Spend / rate limits.** On an org spend-limit or rate-limit error, stop dispatching new
  profiles and surface it — don't stack failing calls. Resume when cleared.
- **Slack `auth.test` fails.** Skip posting for the run and report it; dedup is unreliable without
  the bot identity.

## Key references

- **Interactive sibling:** [`data:investigate-cancellation-feedback`](../investigate-cancellation-feedback/SKILL.md)
  — owns the parser (`fetch_cancellations.py`), the channel formats, the test/noise filter, and
  the canonical versioned footer this cadence stamps.
- **Per-user engine:** [`data:profile-user`](../profile-user/SKILL.md) — called once per canceller
  (Mode B, `context.kind=cancellation`, `side_effects: off`).
- **Poster:** [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md) — the
  `slack_post.py` helper + versioned-footer resolution.
- **Channels:** #ai-cancellation-feedback `C0BR3UTNL83`, #aurie-feedback `C0AEK511BT9`, #ai-feedback `C0AENS8D48N` (pre-split cancellations only). Bot: `aura_ai` `U0ATMPESR37` — it must be a member of every channel swept, including the new one.
