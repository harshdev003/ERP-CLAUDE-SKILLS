# report-quota — methodology

## Composition, not reimplementation

This skill answers "how much of each vendor subscription have we consumed this billing period?" by
**composing** the per-vendor quota skills, so there is one source of truth per vendor:

| Vendor | Sub-skill | Model |
|---|---|---|
| Cartesia (TTS) | `report-cartesia-quota` | DERIVED credits vs plan allotment |
| ElevenLabs (TTS/agent) | `report-elevenlabs-quota` | ACTUAL credits vs limit + overage $ (overage is normal; alerts per $500 step) |
| LiveKit (transport) | `report-livekit-quota` | DERIVED agent-minutes vs plan |
| Deepgram (STT) | `report-deepgram-quota` | ACTUAL prepaid balance + runway |

For each vendor it runs `python report.py --alert --json <tmp>` as a subprocess, then:

- **verdict** comes from the sub-skill's **exit code** (0=OK, 10=WARN, 20=CRITICAL) — no thresholds
  are re-implemented here;
- **numbers** come from the `--json` payload it wrote.

The coupling is a subprocess + JSON/exit-code contract (not a Python import), so each quota skill
stays self-contained and independently testable.

## Resilience

A vendor whose skill can't run (missing key → exit 2, API failure → exit 1, timeout) is shown as
**❓ UNAVAILABLE** with the reason (its last stderr `ERROR:` line) and **does not** set the overall
severity — one missing credential never sinks the whole report.

## Overall status + exit

Overall = the **worst** vendor verdict among those that ran (UNAVAILABLE is surfaced but unranked).
With `--alert`, the runner exits with that worst severity (`10`=WARN / `20`=CRITICAL; `0`=OK), so a
wrapper can gate on `$?`; a bad flag is exit `2`. `--slack` posts the combined table to
`SLACK_WEBHOOK_URL` when overall != OK. Each sub-skill reads its own credentials from the inherited
environment; this runner holds no secrets.

## Notes

- Requires each sub-skill's deps installed (see `requirements.txt`) — notably `google-cloud-logging`
  for the LiveKit vendor.
- `--only cartesia,deepgram` restricts the run to a subset of vendors.
- Each sub-skill's own methodology doc carries the derivation details + source-of-truth caveats.
