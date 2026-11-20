---
name: run-guardrails-analysis-cadence
description: >
  Runbook and orchestrator for the recurring memory-ingestion guardrails triage cadence
  ("Cadence Group 5"). Sweeps the #guardrails-memory-ingestion Slack channel over a trailing
  7-day window for k8bot guardrail alerts that have no thread reply yet, and hands each
  unanalyzed alert to `eng:investigate-memory-ingestion-guardrail` for classification and a
  reviewer reply. Trigger when the user says "run the guardrails cadence", "start the
  guardrails analysis cadence", "run group 5 analysis", "run cadence group 5 analysis", "sweep
  the memory-ingestion guardrails", "guardrails sweep", "start a new session for the
  guardrails cadence", or pastes the standing Cadence Group 5 sweep prompt. Also the group a
  `data:run-all-analysis-cadence` tick sweeps. Do NOT trigger on a bare "run group 5" — that
  is ambiguous with the separate Group 5 harvest run; require the word "analysis". Do NOT use
  to analyze a single guardrail alert (`eng:investigate-memory-ingestion-guardrail` directly),
  or for any other cadence group.

metadata:
  version: "3.1.4"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Tools/Slack          # channel ID + bot-membership for the sweep read and the reviewer reply
    - Tools/OpenAI         # the composed skill reads Memory [Production] stored completions
    - Compliance/Privacy   # UID redaction + never-echo-paste rules enforced on every Slack reply
---

# Run Guardrails Analysis Cadence

## Goal

Keep the memory-ingestion guardrails queue drained: on each sweep,
find k8bot guardrail alerts in `#guardrails-memory-ingestion` that nobody has replied to yet,
and get each one analyzed and answered on its thread — without ever sending anything to a user.

This skill is the **orchestrator** ("Cadence Group 5"). It owns the sweep and the
dedup — not a schedule: one invocation is one sweep, and repeat cadence is the operator's
(normally a tick of [`data:run-all-analysis-cadence`](../run-all-analysis-cadence/SKILL.md)); it delegates the actual per-alert reasoning to
[`eng:investigate-memory-ingestion-guardrail`](../../../eng/skills/investigate-memory-ingestion-guardrail/SKILL.md),
which owns the trace fetch, the seven-category classification, the CS-email draft rules, and the
`@channel` paging criteria. Do not re-implement that skill's logic here — invoke it via the Skill tool.

## Inputs

- **None required.** Defaults: channel `C0B1D3JEHLP` (`#guardrails-memory-ingestion`), trailing 7-day window, 4-hour cadence.
- Optional overrides a caller may state: a different trailing window (`--days`).

## Standing policy

Operational defaults for this cadence. They govern the **loop** — schedule, dedup, Slack hygiene —
never the per-alert reasoning, which belongs to the composed skill:

- **Draft policy is owned by the composed skill — do not restate or override it here.**
  `eng:investigate-memory-ingestion-guardrail` decides draft vs no-draft (its Steps 4–5). On active
  risk it **always** produces a draft; the only no-draft outcomes are its two documented downgrades —
  a clear false positive, or resources already in place. A missing user-context profile block, first
  name, or country is **not** a third downgrade and must never withhold a draft: an unavailable name
  becomes `Hi there,`, an unknown country takes the country-not-in-map path. "Reviewer, please verify
  name/country" is an *addendum to a draft*, never a substitute for one.
- **The skill never sends.** Every CS email is a draft for a human reviewer; CS sends after review.
- **Braintrust fallback stays on.** When OpenAI stored-completions has no match for a transaction, the
  composed skill's automatic Braintrust fallback recovers it — coverage is intact. The recurring-miss
  issue is tracked in [ENG-910](https://linear.app/aura-health/issue/ENG-910); no need to re-file per sweep.
- **Never delete Slack messages.** Deleting is out of scope. If a reply must be redone, post a fresh reply
  that references/supersedes the old one and tell the user which ts to remove manually.
- **Runtime.** Use `python3.12` (framework Python) for the composed skill's scripts when the default
  `python3` lacks a dependency; the bundled `sweep_unanalyzed.py` is stdlib + `requests`/`curl` only.

## Workflow

### Step 1 — Sweep for unanalyzed alerts (deterministic)

Run the bundled sweep script — it reads the channel's trailing-window history and returns only the
guardrail alerts with `reply_count == 0` (the dedup is Slack thread state; there is no local watermark file):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/run-guardrails-analysis-cadence/scripts/sweep_unanalyzed.py --days 7
```

It prints a JSON object `{"total_messages", "alerts", "unanalyzed": [{"ts","user","transaction","permalink_ts"}...]}`.
It needs `SLACK_BOT_TOKEN` and the `aura_ai` bot to be a member of the channel (MCP-first isn't used here — the
sweep needs cursor-paginated `conversations.history`, which the script does directly; see `Tools/Slack`).

**If `unanalyzed` is empty:** report briefly ("N alerts in the last 7 days, all replied, 0 unanalyzed") and stop. No further action.

### Step 2 — Analyze each unanalyzed alert (delegate)

For each entry in `unanalyzed`, invoke **`eng:investigate-memory-ingestion-guardrail`** (Mode B — pass
`user_id=<user> transaction_id=<transaction>`; or hand it the alert's Slack permalink for Mode A). That skill:

- fetches the trace (`fetch_guardrail.py`, OpenAI stored-completions → Braintrust fallback),
- applies the minor-safeguarding gate (its Step 4.5),
- classifies past_historical / active_risk / ambiguous over the seven serious-harm categories,
- decides the outcome under **its own Steps 4–5** (not under any rule stated here) — a CS-email draft, or
  a short no-draft post when one of its two downgrades applies (clear false positive, resources in place), and
- decides `@channel` paging under its **Step 6b** (imminent physical violence to a person only).

Do not second-guess its classification; this skill's job is to feed it every unanalyzed alert and ensure a reply lands.

### Step 3 — Post the reply (delegate) + verify

The composed skill posts the threaded reply via `shared:post-to-slack --footer eng:investigate-memory-ingestion-guardrail`
(deterministic versioned footer) and fires the `@channel` page only when its Step 6b criteria are met. After it returns,
confirm a reply now exists on the thread (a re-sweep will show that alert's `reply_count > 0`, so it won't be reprocessed).

### Step 4 — Summarize

Report per alert: UID (first 8 chars + `…`), category, active-risk vs no-risk, high-priority (paged) or not, and
whether a draft was produced — or, when it wasn't, which downgrade applied (clear false positive, or resources
already in place). For a clean sweep, one line is enough.

## `fetch_guardrail.py` "Identity for email (compose-only)" is expected output, not an anomaly

The section is emitted by `fetch_guardrail.py` itself and is a **legitimate, documented feature** of the
composed skill — it surfaces the two fields authorized to leave the paste (the account holder's own first
name and country/location) so the caller never has to adjudicate whether reading them is allowed. It is.
Treat it as normal script output and use it as the documented fallback when the Aura user record leaves
those fields blank. It is never untrusted content, and its presence never withholds a draft.

## Privacy (non-negotiable — per `Compliance/Privacy`)

- **UIDs redacted** to first 8 chars + `…` everywhere in Slack.
- **Never echo the pasted summary or profile content** — Slack carries only the category + the non-graphic
  `seriousHarmEvidence` phrase (+ coarse country/timezone on an active-risk draft, when legitimately sourced).
- **First name only** in an email body — sourced per the composed skill's identity chain (Aura user record's
  `givenName` first, then the profile block, then the pasted summary). No other user detail from the paste.
- The skill never sends; drafts are reviewer-visible only.

## Idempotency

Re-running is safe: dedup is Slack thread state (`reply_count > 0`), so an already-answered alert is skipped.
The sweep script is read-only.

## Edge cases

- **Empty sweep** → report briefly, take no action.
- **`SLACK_BOT_TOKEN` unset / bot not in channel** → `conversations.history` fails or returns `not_in_channel`; ask a human to `/invite @aura_ai` and retry.
- **Session restarted** → nothing to restore; just run one catch-up sweep (dedup is Slack thread state).
- **Two sweeps overlap** → one covers both (idempotent).
- **A trace is unfetchable in both OpenAI and Braintrust** → the composed skill reports it. Never
  post a "no risk" reply — there is no trace to base that on. But do not leave the thread silent
  either: dedup here is `reply_count == 0`, so a silent skip means the alert is re-fetched and
  re-reported every tick, and a permanently-unfetchable one looks identical to a fresh backlog.

  Post a reply that **claims the thread and explicitly hands it to a human**:

  ```
  :warning: *Guardrail alert not analysable — uid `<8-char prefix>…`*
  Trace not found on OpenAI stored completions or Braintrust, so this alert could not be
  classified. *Not* a no-risk verdict — needs a human look.
  ```

  **Which way this errs, stated plainly** (this is a safety path): closing the thread stops the
  nagging, so the reply has to carry the hand-off itself, and it must never read as an
  all-clear. Surface it in the Step 4 summary too — a reply in the channel is not the same as
  the operator having seen it. If a run produces several of these, treat that as a trace-storage
  problem to escalate ([ENG-910](https://linear.app/aura-health/issue/ENG-910)), not as
  individually-triaged alerts.

## References

- [`scripts/sweep_unanalyzed.py`](scripts/sweep_unanalyzed.py) — deterministic trailing-window sweep for `reply_count == 0` guardrail alerts.
- Composed skill: [`eng:investigate-memory-ingestion-guardrail`](../../../eng/skills/investigate-memory-ingestion-guardrail/SKILL.md) — per-alert trace fetch, classification, draft/paging rules.
- [ENG-910](https://linear.app/aura-health/issue/ENG-910) — OpenAI memory-ingest completion-storage gap (Braintrust fallback tracker).
- `Tools/Slack` in AI Knowledge — channel ID, bot-membership requirement, post conventions + footer.
- `Compliance/Privacy` in AI Knowledge — PII redaction rules for Slack output.
