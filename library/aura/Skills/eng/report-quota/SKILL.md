---
name: report-quota
description: >
  One-pane answer to "how much of each vendor subscription have we consumed this billing
  period across the voice and LLM stack?" Composes the per-vendor quota skills —
  `report-cartesia-quota` (derived credits), `report-elevenlabs-quota` (actual credits plus
  on-demand overage $), `report-livekit-quota` (derived agent-minutes) and `report-deepgram-quota` (actual
  prepaid balance plus runway) — by running each as a subprocess, taking each verdict from the
  sub-skill's exit code and the numbers from its JSON, then rendering one table with
  per-vendor status and an overall worst-severity. With `--alert` the runner exits with the
  worst vendor severity so a wrapper can gate on it; `--slack` posts the combined table;
  `--only` limits to a subset. Trigger on "vendor subscription usage", "how much of each
  subscription have we used", "voice stack consumption", "all vendor quotas", "unified quota
  report", "combined vendor consumption", or "are we about to blow any vendor plan".

metadata:
  version: "3.0.5"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/ElevenLabs                   # ElevenLabs API access (ELEVENLABS_API_KEY)
    - Tools/Deepgram                 # Deepgram usage API (DEEPGRAM_OWNER_API_KEY)
    - Tools/Cartesia                 # Cartesia usage API (CARTESIA_ADMIN_API_KEY)
---

# Report Subscription Usage (unified)

One table across the voice + LLM vendor stack — Cartesia, ElevenLabs, LiveKit, Deepgram — answering
**how much of each subscription have we consumed this billing period, and is any about to blow its
plan?** It **composes** the per-vendor quota skills rather than reimplementing them; see
[`references/methodology.md`](references/methodology.md).

## Step 0 — Prerequisites

Each vendor sub-skill needs its own credentials (all read from env):

- Cartesia → `CARTESIA_ADMIN_API_KEY`
- ElevenLabs → `ELEVENLABS_ADMIN_API_KEY` / `ELEVENLABS_API_KEY`
- LiveKit → `GCP_KEY` (or ADC) + `google-cloud-logging` installed
- Deepgram → `DEEPGRAM_OWNER_API_KEY` / `DEEPGRAM_API_KEY`

A vendor whose key is missing is shown as **UNAVAILABLE** (not a crash), so a partial run is fine.

## Step 1 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-quota/scripts/report.py
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-quota/scripts/report.py --only cartesia,deepgram
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-quota/scripts/report.py --json out.json
```

Renders a per-vendor table (status + consumption) and an overall worst-severity. Takes ~10-20s (it
runs each sub-skill, and the LiveKit one queries GCP logs).

## Step 1b — Alert mode

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-quota/scripts/report.py --alert
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-quota/scripts/report.py --alert --slack
```

Exits with the **worst** vendor severity — `0`=OK, `10`=WARN, `20`=CRITICAL — so a manual run (or a
future cron/CI wrapper) can gate on `$?`; a bad flag is `2`. An UNAVAILABLE vendor is surfaced but
doesn't set the exit severity. `--slack` posts the combined table to `SLACK_WEBHOOK_URL` when overall
!= OK.

## Step 2 — Read the result

Lead with the overall status and any WARN/CRITICAL vendors, then the per-vendor line. Carry the
per-vendor caveats: Cartesia + LiveKit are DERIVED estimates; ElevenLabs + Deepgram balances are
ACTUAL from the vendor API. For any single vendor's detail/methodology, point at that vendor's own
quota skill.

## Relationship to other skills

- Building blocks: `report-cartesia-quota`, `report-elevenlabs-quota`, `report-livekit-quota`,
  `report-deepgram-quota` (each also runnable standalone).
- The `analyze-*-costs` skills give spend-over-a-window detail per vendor; this is the quota/runway
  roll-up.
