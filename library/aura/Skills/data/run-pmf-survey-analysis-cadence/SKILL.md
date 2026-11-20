---
name: run-pmf-survey-analysis-cadence
description: >
  Runs the recurring "Group 3" Sean-Ellis PMF survey cadence — the unattended sweep of
  #ai-feedback that posts a threaded card under every PMF respondent whose thread has no card
  yet. This is the standing-task LOOP MANAGER: on each tick it DELEGATES the per-entry fetch,
  dedup, enrich, card and post work to `data:investigate-pmf-survey` in cadence mode. Trigger
  when the user says: "run the PMF cadence", "run the PMF cadence sweep", "start the PMF
  cadence", "PMF cadence sweep", "sweep the PMF feedback on a cadence", "restart the PMF
  sweep", "run Group 3 analysis" (NOT bare "run Group 3", which is the harvest cadence's Group
  3), "kick off the PMF survey cadence", or "sweep" inside an already-running PMF cadence
  session. Also fire on a fresh session resuming the cadence after a restart. Do NOT use for a
  one-off interactive PMF review or the aggregate digest (`data:investigate-pmf-survey` in
  interactive mode), a single UID (`data:profile-user`), or cancellation feedback.

metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Guide/Index               # baseline routing aid (universal)
    - Compliance/Privacy        # PII rule — UID prefix + email domain only on every Slack surface
    - Products/Aurie       # brand voice for card framing
    - Team/Internal Accounts    # employee / internal-account allowlist passed through as --internal-accounts-file / --internal-names
    - Tools/Slack               # #ai-feedback channel ID + posting access pattern
    - Tools/Mixpanel            # per-entry engagement snapshot (via the delegate's bundled mixpanel_api.py)
    - Tools/Aura API            # segmentation join for email domain + tenure + sub state (via the delegate)
---

# run-pmf-survey-analysis-cadence

The **loop manager** for the recurring "Group 3" Sean-Ellis PMF survey cadence. It keeps
`#ai-feedback` (channel `C0AENS8D48N`) current with a threaded card under every PMF
respondent **whose thread has no card yet**, running unattended — one sweep per invocation,
normally driven by a `data:run-all-analysis-cadence` tick.

It owns one thing and **nothing else**: **a single named entrypoint**, so a fresh session can
resume the cadence by invoking this skill instead of re-pasting a long procedure by hand.

**Dedup is Slack thread state:** an entry is unprocessed if — and only if — its thread carries
no reply from the analysis bot `U0ATMPESR37`. Nothing is persisted, so there is no cursor to
bootstrap and no cold-start question to ask, and a missed tick self-heals on the next one.
Keep it that way — a monotonic cursor only ever advances, so an entry skipped for any reason
(a crash mid-tick, an enrichment failure, a card that didn't post) is never revisited, and a
cursor under `/tmp` does not survive a reboot. Same mechanism as Groups 4 and 5.

Everything about *how a single tick works* — fetching + parsing PMF messages, the dedup check,
the internal allowlist filter, the Mixpanel/Aura enrichment, the full-vs-behavior-only card
shape, the independent header flags, the Slack posting mechanics, and the PII rule — is
**not reimplemented here**. It is delegated to
[`data:investigate-pmf-survey`](../investigate-pmf-survey/SKILL.md) `mode=cadence`, which is
the canonical owner of that mechanism (see its "Cadence mode" section). This skill is the
harness around that mechanism, per best-practice principle 1 (atomic skills, composed
workflows).

## Charter

The cadence is a standing task the user (Jerome) has explicitly authorized to run
**unattended** — it may fetch, enrich, and post threaded PMF cards without a per-entry
confirmation, within this exact scope (the Group 3 PMF cadence on `#ai-feedback`). It does
**not** authorize posting anywhere else or acting on any other feedback stream. Groups 1/2/4
of the broader feedback run-cadence are out of scope for this skill.

## Inputs

All optional — the defaults are the cadence's standing configuration:

| Input | Default | Meaning |
|---|---|---|
| `channel_id` | `C0AENS8D48N` | `#ai-feedback` — where PMF k8bot messages land and cards are threaded. |
| `window` | `7d` | Trailing fetch window. A fixed width is safe because dedup is per-thread: re-fetching an already-carded entry costs one `conversations.replies` call. |
| `internal_allowlist` | `Team/Internal Accounts` | The cached page, passed to the delegate as `--internal-accounts-file` (never a hand-built `--internal-ids` — [ENG-1436](https://linear.app/aura-health/issue/ENG-1436)) plus first names as `--internal-names`. |

## Workflow

### 1. Preflight — none

There is nothing to bootstrap. No cursor file to read, no cold-start question to ask, no
seeding decision. Go straight to Step 2.

### 2. Run one sweep — delegate to `investigate-pmf-survey` (`mode=cadence`)

Invoke [`data:investigate-pmf-survey`](../investigate-pmf-survey/SKILL.md) with `mode=cadence`,
passing:

- `channel_id` (default `C0AENS8D48N`),
- `window` (default `7d`),
- the `Team/Internal Accounts` allowlist as `--internal-accounts-file` / `--internal-names`.

The delegate then performs the full per-tick procedure it documents — fetching the trailing
window, resolving the bot id via `auth.test` and keeping only entries whose thread has no
`U0ATMPESR37` reply, processing those oldest-first, and posting the appropriate reply per
entry. Its "Cadence mode" section is the spec for which reply each entry gets and what work
precedes it. **Do not restate that branching here** (see `/CLAUDE.md` → *Orchestrators:
restrict what you show, don't silently withhold the deliverable*): a copy of the delegate's
decision rules in this file is a second source of truth, and it goes stale without erroring —
so read the delegate rather than this paragraph for what a given entry gets.

Do **not** re-derive the window, re-implement the card, re-run the dedup, or re-post here —
the delegate owns all of it. This skill's job for the tick is done once the delegate returns.

### 3. Report

Summarize the tick: how many entries were in the window, how many had no card yet, and how many
cards posted (with the UID-prefix + a one-line read each). If everything was already carded,
report exactly: `caught up, N in window → 0 uncarded`.

## Footer / attribution

The per-tick card is **composed and posted by the delegate**, so its `Tools/Slack` footer
reads `data:investigate-pmf-survey:<that skill's version>` — that is correct and intended: the
footer traces a card to the skill that built its content. `run-pmf-survey-analysis-cadence` is the loop
manager on top and does not stamp its own footer on individual cards. (If a cadence-level
report is ever posted by *this* skill directly, it would use
`--footer data:run-pmf-survey-analysis-cadence` — built via the helper, never hand-typed — but the
routine per-entry cards stay attributed to the delegate.)

## PII (hard)

Every externally-posted surface obeys `Compliance/Privacy`: **UID first 8 characters + email
domain only**; never a full name, full email, or other direct identifier. The delegate enforces
this on the cards it posts; this skill must not widen it in any summary it prints or posts.

## Starting a fresh session

To resume the cadence in a new session, invoke this skill by name (e.g. "run the PMF cadence
sweep") — it just runs one sweep via the delegate and reports. There is no state to restore and
no seeding question, because the channel itself is the state. No long hand-pasted procedure is
needed — the standing configuration lives in this skill. **This skill does not schedule
itself**; repeat cadence is the operator's, normally a tick of
[`data:run-all-analysis-cadence`](../run-all-analysis-cadence/SKILL.md).

## Edge cases

- **Delegate reports 0 uncarded** → report `caught up, N in window → 0 uncarded`.
- **Re-run in the same tick** → idempotent: the entries carded a moment ago now have a bot reply,
  so the second invocation finds them processed and posts nothing.
- **`auth.test` fails** → the delegate posts nothing and reports it. A blind dedup check reads
  every thread as uncarded, so a failed bot-identity resolution must never fall through to
  posting.
- **A card fails to post** → that entry keeps no reply, so it is simply picked up next tick.
  Nothing needs unwinding.
- **A different feedback stream** (cancellations, ratings) → out of scope; route to the
  relevant sibling skill.

## References

- [`data:investigate-pmf-survey`](../investigate-pmf-survey/SKILL.md) — the delegate; its
  "Cadence mode (`mode=cadence`)" section is the canonical spec for the per-tick mechanism this
  skill loops.
- [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md) — the posting helper
  the delegate uses (versioned footer).
- `/CLAUDE.md` → "Skill versioning", "Slack post conventions", "Secrets and environment".
