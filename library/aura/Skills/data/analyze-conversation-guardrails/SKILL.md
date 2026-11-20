---
name: analyze-conversation-guardrails
description: >
  Identifies why each Aurie conversation guardrail fired across a window of alerts in
  #guardrails-conversation and builds a per-alert trigger dataset: reason
  (from a shared list), who it was about, what the person asked for, and severity. Reuses the
  triage reply's Trigger reason line when present, otherwise reads the turn that fired the
  guardrail and classifies it. Rolls up by reason, by user (each counted once) and by handoff
  category. The dataset feeds follow-ups such as Aurie Care fit, false-positive review, or a
  guardrail eval dataset. Trigger on "analyze the conversation guardrails", "why are the
  guardrails firing", "break down the guardrail triggers", "guardrail trigger reasons this
  week", "build a guardrail dataset", "are the guardrails over-triggering", or "how many
  guardrail users would fit Aurie Care". Do NOT use to triage one alert
  (`eng:investigate-conversation-guardrail`), to drain the unanswered queue
  (`data:run-conversation-guardrails-analysis-cadence`), or for memory-ingestion alerts.
metadata:
  version: "1.9.1"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Compliance/Privacy   # counts and generic paraphrases only; no UIDs, quotes or names leave the machine
    - Tools/Slack          # channel directory, private-channel scopes, post format + footer
    - Tools/Aura API       # stored-transcript export + llmCall ledger (fetch_conversation.py)
    - Tools/OpenAI         # Responses fallback for the transcript
    - Tools/GCP            # handoff category per turn
---

# analyze-conversation-guardrails

**Why did each guardrail fire?** `eng:investigate-conversation-guardrail` judges one alert at a time for safety. This skill answers the question across every alert in a window and produces a **dataset**: one record per alert with the reason it fired, who it was about, what the person asked for and how severe it was.

The handoff category (`crisis`, `treatment`, `boundary`, …) is the prompt's routing label, and it often differs from what the person said: a `crisis` handoff on "kill me now", a `boundary` handoff on an assault disclosure. The reason is read from the person's own words, using the shared list in [`references/trigger_reasons.md`](references/trigger_reasons.md), a byte-identical copy of the one the triage skill writes its `Trigger reason:` line from.

The dataset is the deliverable. Specific questions are **follow-ups** computed from it, with no second pass over the conversations:

| Follow-up | Question |
|---|---|
| [`aurie-care.md`](references/follow-ups/aurie-care.md) | Who was at a natural moment to be offered a therapist (Aurie Care)? |
| [`false-positives.md`](references/follow-ups/false-positives.md) | Where does the guardrail fire without need? |
| [`guardrail-dataset.md`](references/follow-ups/guardrail-dataset.md) | Which alerts should become guardrail eval cases, and does a candidate prompt fix them? Rebuilds and replays the production call, de-identifies, writes dataset rows, then compares in Braintrust |

## Inputs

- **Window** — default trailing 7 days (`--days`), or `--oldest` / `--latest` epoch seconds.
- **Follow-up** (optional) — one of the files above, run after the dataset is built.
- **Post target** (optional) — a Slack channel or thread for a summary. Nothing is posted without one, and nothing is posted before the user confirms.

## Prerequisites

`SLACK_BOT_TOKEN` with the `aura_ai` bot in `#guardrails-conversation` (`C0C7HEZ7CEA`, private: `groups:history`). `AURA_API_TOKEN` for the transcript export, `GCP_KEY` for the llmCall ledger (BigQuery `llm.llm_call`), `OPENAI_CONVERSATION_API_KEY` for the fallback (and the `guardrail-dataset` follow-up's stored-call reads), `OPENAI_EVALUATIONS_API_KEY` for its replays, `gcloud` + `GCP_KEY` for handoff categories (run as the service account through the bundled `scripts/gcloud_sa.py`, your gcloud login only when `GCP_KEY` is unset; without either, categories read `unknown`; everything else works). Missing tokens: walk through `update-config`, then restart. Name the shell variable `USER_ID`, never `UID`.

## Workflow

Work in the session scratchpad (`$WORK`). Every file there holds conversation text or user ids, so it stays on this machine. `S=${CLAUDE_PLUGIN_ROOT}/skills/analyze-conversation-guardrails`.

### Step 1 — Sweep the alerts

```bash
python3 "$S/scripts/sweep_alerts.py" --days 7 --out "$WORK/alerts.json"
```

Lists every `*Conversation guardrail triggered*` alert in the window, **answered or not**, and reads each triage reply's severity (its verdict) and `Trigger reason:` line, plus the alert's own `*Prompt:*` line (the experiment-arm prompt file that fired, e.g. `coach.coachPrompterGuardrails.a.txt`; Intelligence Service prints it only when an arm swapped the body, so an alert without it ran the base prompt). Prints counts: alerts, users, conversations, alerts with a triage reply, alerts with a reason line. Exit 1 is a Slack failure (`not_in_channel` → `/invite @aura_ai`): stop and report it.

### Step 2 — Extract the triggering turns

```bash
python3 "$S/scripts/extract_turns.py" --alerts "$WORK/alerts.json" \
  --cache-dir "$WORK/conversations" --out-dir "$WORK"
```

For alerts without a reason line (all of them with `--all`), fetches each conversation with the bundled `fetch_conversation.py` (10 in parallel, cached per alert, so a re-run fetches only what is missing) and writes `turns.txt`: one line per alert with its `ts`, the handoff category, the previous user message and the user message that fired the guardrail. Expect about 15 minutes for 500 alerts on a cold cache. `turns.json` carries the handoff category for Step 4.

### Step 3 — Classify why each guardrail fired

Read [`references/trigger_reasons.md`](references/trigger_reasons.md), then `turns.txt` in chunks of about 140 lines. For each alert assign:

- `reason` — one code from the list, first match in its order;
- `subject` — `self`, `other` or `unclear`;
- `asked_for` — `professional-help`, `clinical-advice` or `none`;
- `in_care` — true when the person says they see a therapist, counsellor or psychiatrist;
- `severity` — `crisis` (current danger to self or others, including passive wishes to die or disappear; ambiguity fails toward crisis), `non-crisis` (a guardrail topic with no current danger), `false-positive` (nothing guardrail-worthy: a figure of speech, a fragment, a plot), or `unknown` (`TRIG: -`).

Write `$WORK/codes.json` as `{"<ts>": {"reason", "subject", "asked_for", "in_care", "severity"}}`, appending after each chunk so a long run survives an interruption. **Key by `ts`, never by line position**: a miscounted chunk would shift every later code onto the wrong alert. Every `ts` in `turns.txt` gets a record; `TRIG: -` is `reason: unknown`.

Severity here is judged on two turns; a triage reply's severity covers the whole conversation. Say which source a number came from when it matters.

### Step 4 — Build the dataset and roll up

```bash
python3 "$S/scripts/rollup.py" --alerts "$WORK/alerts.json" --turns "$WORK/turns.json" \
  --codes "$WORK/codes.json" --reasons "$S/references/trigger_reasons.md" \
  --dataset "$WORK/dataset.json" --json "$WORK/rollup.json"
```

Deterministic. Writes `dataset.json` (one record per alert, local only) and prints:

- **Worst severity per user** — each user once, so the rows add up to the user total.
- **Trigger reasons** — alerts, users, users talking about themselves, users who asked for a professional, crisis alerts. A user appears in each reason they triggered.
- **Handoff category → top reasons** — where routing labels and real reasons diverge.
- **Prompt that fired** — alerts, users and severity per prompt file (`coach.coachPrompterGuardrails.a.txt`, and `base prompt` for every alert without the line: the control arm, users outside any experiment, and older alerts). Shown only when some alert carries the line. These are counts, not rates: divide by each arm's assigned users (Mixpanel `Experiment: <name>`) before comparing arms.
- **Duplicate alerts** — more alerts than conversations means the one-alert-per-conversation rule leaked; worth a card on its own.

Unparsed alerts and reasons outside the list are counted and flagged, never dropped.

### Step 5 — Summarise, then follow up

Present the rollup with two or three **generic paraphrased** examples per leading reason ("asked whether their antidepressant dose was right"): never a quote, a name, a place or a UID. State the caveats: one model reviewer on two turns (or the triage skill on the whole conversation, for reason-line alerts), and that only guardrail turns are counted.

If the user asked a specific question, run the matching follow-up on `dataset.json` and present its result after the base rollup.

### Step 6 — Post (only on request, after confirmation)

For a Slack summary use [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md) with `--footer data:analyze-conversation-guardrails`: the headline, one fixed-width table in a code block (Slack has no tables), the implication and a suggested next step. Show the rendered message and target before posting. Counts and generic paraphrases only.

## Rules

- **Read-only.** Never replies on alert threads, never changes a guardrail prompt, never contacts a user. A finding that a prompt should change goes to `eng:update-intelligence-prompt` as a recommendation. The `guardrail-dataset` follow-up replays production calls on the Evaluations key (`store: false`) and drafts candidates only as new Braintrust prompts.
- **Count, don't drop.** Missing transcripts, unparsed alerts and unclassified alerts each show up as a counted row.
- **The dataset stays local.** It holds user ids. Anything shared is aggregate. The one exception is the `guardrail-dataset` follow-up's `rows` output: de-identified, residue-checked, and loaded only through `data:save-to-braintrust-dataset` after a confirmation.

## Files

- [`scripts/sweep_alerts.py`](scripts/sweep_alerts.py) — every alert in the window with its triage severity and reason line.
- [`scripts/extract_turns.py`](scripts/extract_turns.py) — parallel, cached conversation fetch and triggering-turn extraction.
- [`scripts/rollup.py`](scripts/rollup.py) — the per-alert dataset and its rollup.
- [`scripts/reproduce.py`](scripts/reproduce.py) — the `guardrail-dataset` follow-up: `rebuild`, `replay`, `deidentify`, `rows`.
- [`scripts/coach_calls.py`](scripts/coach_calls.py) — canonical coach-call and de-identification helpers, copied into `data:investigate-user-rating` (see `docs/canonical-sources.md`).
- [`scripts/fetch_conversation.py`](scripts/fetch_conversation.py), [`scripts/responses_reader.py`](scripts/responses_reader.py) — byte-identical copies (canonical: `eng:investigate-conversation-guardrail`, `data:analyze-user-conversations`; see `docs/canonical-sources.md`).
- [`references/trigger_reasons.md`](references/trigger_reasons.md) — byte-identical copy of the triage skill's reason list.
- [`references/follow-ups/`](references/follow-ups/) — one file per follow-up question.

## When to use this vs siblings

- **This skill** — why guardrails fire, across many alerts.
- [`eng:investigate-conversation-guardrail`](../../../eng/skills/investigate-conversation-guardrail/SKILL.md) — one alert, safety triage, CS draft. Its `Trigger reason:` line feeds Step 1.
- [`data:run-conversation-guardrails-analysis-cadence`](../run-conversation-guardrails-analysis-cadence/SKILL.md) — drains unanswered alerts through the triage skill.
