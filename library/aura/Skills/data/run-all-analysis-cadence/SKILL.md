---
name: run-all-analysis-cadence
description: >
  One-invocation meta-orchestrator that runs a single sweep of the entire "Feedback Analysis —
  Run Cadence" by composing the six `run-*-analysis-cadence` group skills, one tick each. To
  protect coverage it runs the negative-deep pass before the all-polarity pass (they share one
  dedup list): Group 2 negative ratings, then Group 1 all-polarity, Group 3 PMF, Group 4
  cancellations, Group 5 memory-ingestion guardrails, then Group 6 conversation guardrails last.
  A thin composer — it never reimplements any group's logic. It is a manual "sweep everything
  once now": nothing here schedules anything. Trigger on "run all analysis cadences", "run every
  feedback cadence", "sweep all the feedback cadences", "run the whole feedback analysis
  cadence", "sweep all feedback groups once", or "run all the feedback analysis groups". Do NOT
  use to run a single group, or for the harvest cadence (`data:run-all-harvest-cadence`).

metadata:
  version: "3.3.0"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal). Every group skill owns its
                    # own knowledge manifest (Tools/Slack, Compliance/Privacy,
                    # Team/Internal Accounts, etc.); this meta-orchestrator
                    # delegates all Slack / OpenAI / Mixpanel / Aura API I/O to them.
---

# Run All Analysis Cadence

A single manual sweep of the whole **Feedback Analysis — Run Cadence** in one
invocation. This skill is a meta-orchestrator: it runs one tick of each of the
six analysis group cadences in order, then prints a single consolidated readout.
All real work — transcript fetches, dedup, per-entry analysis, threaded
auto-posts, PII redaction, the SI/CSA content-safety gate — lives in the composed
group skills. This meta-skill adds no new analysis or posting logic; its one script,
`outcomes.py`, only formats the follow-ups Step 8 raises.

It sits one level **above** the `run-*-analysis-cadence` family: those each run
one group; this runs a single tick of all of them. Neither level schedules anything.

## One sweep of each group, not a launcher

Each `run-*-analysis-cadence` group skill sweeps once per invocation and schedules
nothing — no group arms a cron, and neither does this skill. So a tick here is exactly
what it looks like: six sweeps, in order, then a summary.

Everything else about each group's tick is inherited unchanged — including its
**pre-authorized unattended auto-posting** of threaded Slack replies, its dedup (so
nothing double-posts if a sweep is repeated or overlaps), its PII redaction
(UID-prefix + email-domain only), and its content-safety withhold-and-flag rule for
active SI / CSA content.

Repeat cadence is the operator's. If you want this running on a schedule, drive it
from outside the skills — the host's own scheduler (in Claude Code, its cron /
scheduled-task tools) — with a body that invokes **this** skill by name, so one
schedule covers all six groups and every tick picks up the current skill
definitions from disk.

## What it runs

**Run order** (the `#` column) is deliberately **not** the runbook group number —
see the ordering rule below for why the negative-deep pass runs before the
all-polarity pass.

| Run # | Group | Skill | Channel / source |
|---|---|---|---|
| 1 | Negative feedback (Group 2) | `data:run-feedback-analysis-cadence` | `#ai-data-intelligence` — negative ratings, **deep** analysis |
| 2 | Rating feedback (Group 1 analysis) | `data:run-rating-feedback-analysis-cadence` | `#ai-data-intelligence` — all-polarity ratings |
| 3 | PMF survey (Group 3) | `data:run-pmf-survey-analysis-cadence` | `#ai-feedback` — Sean-Ellis PMF respondents |
| 4 | Cancellations (Group 4) | `data:run-cancellation-feedback-analysis-cadence` | `#ai-feedback` + `#aurie-feedback` — cancellers |
| 5 | Memory-ingestion guardrails (Group 5) | `data:run-guardrails-analysis-cadence` | `#guardrails-memory-ingestion` — memory-ingestion guardrail alerts |
| 6 | Conversation guardrails (Group 6) | `data:run-conversation-guardrails-analysis-cadence` | `#guardrails-conversation` — conversation guardrail alerts + crisis-reply check |

**Group numbers are per the Feedback Analysis runbook and overlap with the sibling
harvest cadence's numbers** — match on the domain (rating / negative / PMF /
cancellation / memory guardrails / conversation guardrails), not the index.

**Ordering rule — run the deep-negative pass (Group 2) BEFORE the all-polarity
pass (Group 1), because they share one channel and one dedup signal.** Group 1
(rating, all-polarity) and Group 2 (feedback, negative-only) both read
`#ai-data-intelligence`, and both treat **a reply from the analysis bot
`U0ATMPESR37` on the thread** as the sole marker that an entry is processed. So
whichever posts first claims the overlapping negatives: if the all-polarity pass
ran first it would answer negatives with its lighter review, and the deeper Group 2
pass would then see a reply and skip them — its why-negative analysis would never
post. Running **Group 2 first** gives every unanswered negative the deeper
analysis; the all-polarity pass then sees those replies and covers only
neutral/positive — full coverage, no double-post. **Never run them in the reverse
order.** This is also why the two must not be consolidated into one sweep: with one
shared reply-space, order is the only thing keeping them from suppressing each other.

**Group 6 runs last, after Group 5.** The two guardrail groups read different channels
and never share an alert or a dedup signal, so their relative order changes no coverage;
Group 6 is placed last by decision, so the existing five groups' tick is unchanged by its
addition and its summary — which carries the crisis-reply-check FAIL count — is the last
thing the operator reads before the consolidated readout.

**There is no first-run special case.** A first run against this channel simply
answers whatever is unanswered, deep pass first. Neither sweep has a cold-start or
seeding path, so the ordering rule alone gives correct coverage on every tick
including the first.

## Standing defaults

- **Composition only.** Always invoke each group skill via the Skill tool; never
  inline its scripts or duplicate its analysis/posting logic.
- **One tick each.** Invoke every group for a single sweep; no group schedules
  anything, so there is no self-scheduling step to suppress.
- **Auto-post inherited.** Each group auto-posts per its own pre-authorization —
  this is the same autonomy those cadences already have. This meta-skill does not
  add, suppress, or re-route any post; it also posts nothing itself.
- **Run autonomously — and never let one group block the others.** No per-group
  confirmation between steps; invoking this meta-skill is standing approval to sweep
  all six once. Run each group **non-interactively**. No group has a bootstrap prompt
  — no group keeps a cursor to seed. If a group does try to pause for a setup question,
  don't let it stall the sweep: take the safe default and continue; if it can only proceed
  by prompting, skip it, note the gap in the summary, and move on. Only a hard blocker a
  group can't route around stops that group — and even then, continue to the next.
- **Idempotent.** Every group dedups against already-answered threads, and no group keeps
  an on-disk watermark, so re-running the whole sweep is safe and won't re-post.
- **"0 posted" is not the same as "0 outstanding".** Because the dedup signal is the
  reply, an entry skipped for a *transient* reason (a token, a 5xx, a lagging transcript
  index) deliberately keeps no reply so the next tick retries it. Carry each group's own
  reported failures into the summary rather than reading a quiet group as a clean one.

## First — resolve the window

Each group defaults to its own trailing fetch window (Group 1 a 1-day rating window,
Group 2 7 days of negatives, the cancellation and PMF groups their own trailing widths).
No window is cursor-anchored — dedup is per-thread, so re-fetching an already-answered
entry just costs one `conversations.replies` call.
Let each use its default unless the user asks otherwise; if they name a window,
pass each group the flag that expresses it **in its own SKILL.md's terms** (the
groups do not share one window flag).

## Scratch files and posting — no worker shares a file

On 2026-10-27 one tick's main loop (Group 4) and a parallel Group 6 subagent both wrote `c1.txt`–`c3.txt` in the scratchpad **root**. A 69k-character guardrail transcript was then posted as 19 Aura AI replies under an unrelated cancellation thread. These rules hold whether the groups run in order or a step fans out to subagents:

1. **One folder per worker**, created before it starts: `<scratchpad>/<group>_<batch>/` (e.g. `g4_main/`, `g6_b1/`). A subagent's prompt states its folder as a mandatory line, and it writes nowhere else. Nothing is ever written to the scratchpad root.
2. **Typed file names** that carry the entry, e.g. `g4_card_<uid8>.txt` or `g6_tx_<uid8>.txt`. Never `c1.txt`, `a.txt` or a name another worker could pick.
3. **Post with the card guards** from `shared:post-to-slack` (*Card guards*), using **the values each group's own post step names**, not one set for all. Every card passes `--expect "<uid8>…"` and `--max-chunks 3`. The size cap is the skill's own: 6,000 for cancellation, rating and PMF cards, 8,000 for Group 5/6 guardrail replies, which carry a CS draft. `--expect-header` is used only where that skill names its first line; the guardrail replies put the uid on the second line, so they don't use it. Pipe the body on stdin (`--text -`) where the caller can; otherwise write it to a path from `slack_post.py body-path`.
   - **A refused post (exit 2) is never retried with the guards off.** For cards it's reported for that entry.
   - **For a Group 5/6 guardrail reply, a refusal errs toward silence on a safety path**, so it is a **Needs attention** line in the Step 7 readout and a `blocked` outcome for Step 8, both naming the alert link, so a human posts the reply. It must never pass as a handled alert.
4. **Transcripts stay in the worker's folder** and are deleted when it finishes.

## Steps 1–6 — Run each group's tick

For each row in the table, **in the Run # order shown** (Group 2 before Group 1 —
see the ordering rule):

1. Invoke the group skill via the Skill tool with an explicit **non-interactive**
   instruction: **"run one sweep tick only; do not pause for confirmation"**, plus any
   window the user asked for.
2. Let it run its own fetch → dedup → per-entry analysis → threaded auto-post loop.
3. Capture its counts (in-window → already answered → unanswered → posted), any
   content-safety flags it raised, and any entries it reported as failed or retried,
   for the final summary.
4. Append the group's outcome line for Step 8.
5. On a hard failure in one group, **note it and continue** to the next group —
   do not abort the whole sweep (see Degradation below).

If a group skill isn't in the registry yet (freshly reinstalled), `/reload-plugins`
(or restart) to surface it, then invoke it normally. Do **not** substitute a
bundled script as a fallback — that would skip the group's dedup/safety steps and
violate the composition rule.

## Step 7 — Consolidated summary

Print one readout with a line per group:

- Per group: counts (in-window → already answered → unanswered → posted), and one clause
  per posted reply (UID-prefix · polarity/category · signal) if few, else a count.
- **Needs attention — refused posts:** every post a card guard refused (exit 2), by entry or alert link. A refused Group 5/6 guardrail reply leads this line: its alert has no reply, so the next tick retries it, but a person should look now (see *Scratch files and posting*).
- **Retries and error replies:** entries a group left for the next tick (transient) and
  entries it closed out with an error reply (terminal). Both are invisible in a "posted"
  count and both matter.
- **Crisis-reply check (Group 6):** carry Group 6's FAIL line verbatim — `<n> FAIL of
  <m> active-risk conversations`, with its redacted UIDs and thread links — even when it is
  zero. A FAIL means someone at risk did not get a way to reach a human or was dropped by
  Aurie; it is a product-safety finding and must not be folded into a "posted" count.
- **Content-safety flags:** surface any entries a group withheld-and-flagged
  (active SI / CSA) directly to the user here — never let a flag get buried.
- **Failures/skips:** any group that couldn't run, with the reason and the
  single-group skill to re-run it.

## Step 8 — Raise a follow-up for every stop (unattended runs)

A scheduled run has nobody reading the Step 7 summary, so every group that did not finish
becomes a `shared:raise-followup` post. Keep the outcomes file in the session scratchpad and
append one JSON object per line as each group finishes (Steps 1–6), e.g.
`{"step": "cancellation", "status": "blocked", "reason": "bot is not in #aurie-feedback", "rerun": "data:run-cancellation-feedback-analysis-cadence"}`.
Steps are `feedback`, `rating`, `pmf`, `cancellation`, `guardrails`, `conversation-guardrails`.
Status is `ok` for a group that ran its tick (retries left for the next tick included),
`failed` or `blocked` for a hard blocker (missing token, bot not in the channel, skill not
installed), and `blocked` for a group with a refused Group 5/6 guardrail reply, its reason
naming the alert link. Then:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/run-all-analysis-cadence/scripts/outcomes.py \
  --cadence run-all-analysis-cadence --session "${AINATIVE_SESSION_NAME:-scheduled}" \
  --in "$SCRATCH/analysis-outcomes.jsonl" \
  --due feedback,rating,pmf,cancellation,guardrails,conversation-guardrails
```

Every group is due on every tick. A group with no entry counts as `stopped`, and a missing
outcomes file means nothing was recorded, so a tick that ends early still reports the groups it
never reached. The script prints one object per follow-up (`kind`, `title`, `what`, `why`,
`ask`, `key`, `session`): pass each to `shared:raise-followup` as is. The key is
`run-all-analysis-cadence:<group>:stop`, one post per group, with a changed reason going in as a
reply. Exit 0 means nothing to raise.

Run this step **before the turn ends, however it ends**: after Step 7, on a hard blocker, and
when a wait expires or the tick is cut short. An interactive run with someone reading the
summary may skip it. Content-safety flags and Group 6 crisis-reply FAILs are findings, not
stops: they stay in the Step 7 readout, and this step does not replace them.

## Notes & edge cases

- **Composition, not reimplementation.** If a group cadence's behaviour changes,
  this meta-orchestrator inherits it for free.
- **Content safety is the group's job, surfaced here.** Each group runs its own
  SI/CSA withhold-and-flag gate; this meta-skill's only added duty is to make sure
  those flags reach the user in the consolidated summary rather than being lost
  between groups.
- **Partial degradation.** If one group can't run (missing token, bot not in a
  channel, upstream outage), complete the others, record the gap in the summary and its
  outcome for Step 8, rather than aborting.
- **No background persistence.** This sweep schedules nothing, so nothing keeps
  running after it finishes. That is intentional: repeat cadence belongs to the
  operator, driven from the harness scheduler against **this** skill. Say so in the
  summary if the user seems to expect a standing loop.
- **Scope.** Analysis run cadence only. The **harvest** run cadence
  (Notion/BigQuery snapshots + stores) is the sibling
  `data:run-all-harvest-cadence` — do not run it from here.

## References

- Group 1: [`data:run-rating-feedback-analysis-cadence`](../run-rating-feedback-analysis-cadence/SKILL.md)
- Group 2: [`data:run-feedback-analysis-cadence`](../run-feedback-analysis-cadence/SKILL.md)
- Group 3: [`data:run-pmf-survey-analysis-cadence`](../run-pmf-survey-analysis-cadence/SKILL.md)
- Group 4: [`data:run-cancellation-feedback-analysis-cadence`](../run-cancellation-feedback-analysis-cadence/SKILL.md)
- Group 5: [`data:run-guardrails-analysis-cadence`](../run-guardrails-analysis-cadence/SKILL.md)
- Group 6: [`data:run-conversation-guardrails-analysis-cadence`](../run-conversation-guardrails-analysis-cadence/SKILL.md)
- Sibling meta-skill (harvest): `data:run-all-harvest-cadence`
- Spec: AI Knowledge → `Feedback Analysis — Run Cadence`.
