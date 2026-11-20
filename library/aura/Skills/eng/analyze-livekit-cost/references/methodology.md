# LiveKit cost methodology

Why this report says **DERIVED** everywhere, and how the one number it produces is built.

## There is no LiveKit billing API — read this first

Unlike ElevenLabs (`/v1/usage/character-stats`, `current_overage.amount`) or OpenAI (the Costs API), **LiveKit exposes no usage or billing endpoint**. The `lk` CLI has no usage/spend command. Actual spend lives **only in the LiveKit Cloud dashboard** (Project → Billing). Nothing this skill can call returns "yesterday's LiveKit $".

So cost here is **derived, not billed**:

```
derived spend = total session minutes × rate ($/session-minute)
```

Every surface of the report labels this "⚠ derived, not billed" on purpose. It is an estimate to size the line item and catch runaway usage — **not** an invoice. Always reconcile against the dashboard before quoting a number as actual spend.

## How minutes are computed

Minutes are the **same log-span proxy** [`eng:analyze-livekit-usage`](../../analyze-livekit-usage/SKILL.md) uses, and it comes from the shared `livekit_core.build_sessions`:

- One **session** == one LiveKit `room`.
- Session minutes = `(last log ts − first log ts)` for that room. There is no explicit session-duration field in the forwarded logs, so log-span is the honest proxy for connection minutes.
- A room that logs nothing after connect reads as ~0 min; a reused room reads as the whole reuse span (treat multi-hour "sessions" as suspect and drill in).

Total minutes = sum of per-room minutes across the window. This is intentionally identical to the usage skill so the two never disagree — if they do, something is wrong in one of the copies of `livekit_core.py`.

## The rate — LiveKit Scale plan ([livekit.com/pricing](https://livekit.com/pricing))

The `--rate` flag is **USD per session-minute**, defaulting to **`$0.01/min`** — the LiveKit **Scale** plan agent-session-minute **overage** rate, our plan as of the voice go-live. LiveKit Cloud exposes no usage/billing API, so cost stays derived.

**Scale plan ($500/mo flat), the components that matter here:**

| Line item | Included / month | Overage |
|---|---:|---|
| Agent session minutes | 50,000 | **$0.01/min** ← the `--rate` default |
| Voice isolation minutes | 10,000 | $0.0012/min |
| Observability entries (logs + spans + transcripts, one shared pool) | 5,000,000 | $0.00003/entry |
| Observability audio minutes | 50,000 | $0.005/min |

**Key caveat — the allotments.** Inside the 50,000 included agent minutes the marginal LiveKit cost is **~$0** (already paid in the $500 flat fee); past it, $0.01/min is real marginal cost. Since the 2026-09-24 LiveKit-only switch we are past it every month (September 61,838 min; October passed 50k by mid-month). Krisp and observability-audio minutes track agent minutes ~1:1 and are over their allotments too, adding ~$0.0062/min that `minutes × $0.01` does not include. The live plan table is `scripts/livekit_plan.py`.

Change the rate in two places if the plan changes:

1. **Per run** — pass `--rate <your-rate>`. The value stays with the invocation.
2. **Default** — change `default=0.01` on the `--rate` argument in [`scripts/report.py`](../scripts/report.py) (`parse_args`), and update this doc in the same change.

Voice-isolation and observability are separate Scale line items not folded into the `--rate` derivation; model them separately if they become material (usage TBD).

## What this excludes — do NOT double-count

This is **LiveKit's own cost** — realtime WebRTC transport plus Cloud Agents (the hosted worker running the Aurie agent). It is a **separate line item** from the other providers a voice turn touches:

| Cost | Owned by |
|---|---|
| **LiveKit transport + agent hosting** | **this skill** (derived) |
| TTS (Cartesia / ElevenLabs) | [`eng:analyze-elevenlabs-costs`](../../analyze-elevenlabs-costs/SKILL.md), [`eng:report-elevenlabs-costs`](../../report-elevenlabs-costs/SKILL.md) |
| LLM (OpenAI) | [`eng:analyze-openai-costs`](../../analyze-openai-costs/SKILL.md), [`eng:analyze-openai-cost-per-user`](../../analyze-openai-cost-per-user/SKILL.md) |
| STT | folded into the voice provider's cost above |

The derived spend here does **not** overlap with any of those — they meter different vendors. When someone asks "what does an Aurie voice minute cost end-to-end?", combine the three (LiveKit + ElevenLabs + OpenAI), counting each provider exactly once. This skill answers only the LiveKit slice.

## Per-user cost breakdown

The report allocates the same minutes-based derivation per Firebase UID (`jsonPayload.userId` on the session's log lines), ranks by derived cost, and shows the top N. Because it is `minutes × rate` per user, the per-user costs sum exactly to the headline derived spend for the window (plus a `(no-user)` bucket for sessions with no attributed UID — worker-level or console/QA rooms).

UIDs are truncated to an 8-char prefix in the markdown table (`livekit_core.short_uid`). The full UID appears only in the `--json` payload (opt-in). No transcripts or free-text content are surfaced — follows `Compliance/Privacy`.

## Subscription-tier annotation (optional)

When `AURA_API_TOKEN` is set, the top-N users are annotated with a coarse subscription tier via `GET https://services.aurahealth.io/subscriptions/list/<userId>` (Aura PaymentService). This is **best-effort and degrades gracefully**: no token → the tier column is omitted; a failed/timed-out call → that user shows `?`. It never hard-fails the report.

The tier label is intentionally coarse (Paid / Trial / Lifetime / Expired-Refunded / Never premium) — a cost-report annotation, not a billing readout. For the full refunded/revoked/trial-expired disambiguation state machine, see [`report-elevenlabs-costs/references/methodology.md`](../../report-elevenlabs-costs/references/methodology.md). It matters for cost because a single non-paying UID dominating minutes is a very different signal from a paying one.

## Reconciling against the LiveKit Cloud dashboard

The derived figure will not match the dashboard invoice exactly — and that's expected:

- **The rate is a blended assumption**, not LiveKit's actual per-tier pricing math.
- **Log-span minutes** approximate connection minutes; LiveKit's own billing meters participant-minutes / bandwidth / agent-compute, which log-span cannot see.
- **Console/QA rooms** (rooms prefixed `console-`) are test traffic from the LiveKit Cloud playground; they inflate derived minutes but may be billed differently (or excluded).

To reconcile: pick a closed billing period, run this skill with `--since/--until` matching it, and compare derived spend to the dashboard total. If they're materially apart, re-derive `--rate` from the actual invoice (see "The rate — LiveKit Scale plan" above) rather than trusting the default overage rate. The dashboard is always the source of truth for actual spend; this skill is for sizing, trend, and per-user attribution.

## When NOT to use this skill

- **What did we actually pay LiveKit?** → the LiveKit Cloud dashboard invoice. This skill can only estimate.
- **How busy is the voice agent / how many sessions?** → [`eng:analyze-livekit-usage`](../../analyze-livekit-usage/SKILL.md) (sessions, concurrency, minutes — no $).
- **Voice turn latency?** → [`eng:analyze-livekit-latency`](../../analyze-livekit-latency/SKILL.md).
- **Full end-to-end voice cost per user?** → combine this (LiveKit) with the ElevenLabs and OpenAI per-user skills.
