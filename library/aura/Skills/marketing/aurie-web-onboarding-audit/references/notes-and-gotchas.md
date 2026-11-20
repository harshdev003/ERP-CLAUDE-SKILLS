# Notes and gotchas

Lookup list of behavioural edge cases and cross-step gotchas for `aurie-web-onboarding-audit`. Consult when reasoning about how the audit, funnel, and grounding interact.

- **The audit is the primary grounding source when Mixpanel is unavailable.** When `funnel_status == skipped` or `lexicon_empty`, ideation skills downstream should set `grounding_source: audit` instead of `grounding_source: funnel`. The audit is designed to stand alone.
- **`measurable_today` is computed twice.** Step 1 sets it to `null`; Step 2 backfills `true`/`false` based on actual funnel data. Don't read it before Step 2 runs.
- **Audit + funnel corroboration is a quality signal.** When a screen is flagged high-risk by Pass 1 AND shows a top-3 Mixpanel drop-off, the cross-validation is strong evidence for ideation. Surface this explicitly in the readout.
- **`focused` mode without Mixpanel falls back to full mode.** `focused` needs Mixpanel drop-offs to pick the top-5 screens. If Mixpanel is skipped, log "focused mode requires funnel data; running full audit instead" and switch.
