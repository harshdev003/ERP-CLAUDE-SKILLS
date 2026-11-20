# Notes and gotchas

Operational constraints and edge-case reminders for the `aurie-web-onboarding-experimentation-ideas` orchestrator. Referenced from the SKILL.md "Notes and gotchas" pointer.

- **Sub-skill failures cascade upward.** If `aurie-web-flow-snapshot` fails (Notion unreachable, no live source, no fallback), `aurie-web-onboarding-audit` reports the failure to this skill, which in turn aborts at Step 2 with the sub-skill's error message.
- **Single happy path.** Each run audits one variant. Multi-variant requires `aiweb-ref=<other-branch>` and a second run.
- **Mixpanel exclusion filter is mandatory.** The sub-skill enforces this — never query unfiltered data. The skill fails fast if the three test/internal-account filters can't be applied.
- **`Learnings/Experiments` is the only destination.** No Excel, no separate tracker DB. The team also has a legacy `Onboarding Experiments Tracker` DB (`32f19ef7d05781b1a742f68e10bf1433`) — this skill does **not** write there.
- **Idea-specific fields live in the body, by design.** 9 fields don't exist as native columns; they're written into `## What was tested` using greppable `<key>: <value>` markers per [`idea-schema.md`](idea-schema.md).
- **`Start Date` is repurposed as `Date Generated`.** The schema requires `Start Date`, but a brand-new idea hasn't started. The skill writes `Start Date = today` on create. The `Phase: Idea` body marker disambiguates "generated, not shipped" from "Running".
- **Append-only writes.** Step 6 only creates new rows. The skill never overwrites existing rows — even on re-run.
- **Aurie Web only.** Don't extend to Aura or Aurie mobile in the same skill — funnel events differ, surfaces differ, brand voice differs. A future `aurie-mobile-onboarding-experimentation-ideas` would be a separate skill.
- **`count` cap of 8.** More ideas per batch dilutes review quality. Run twice with different `focus` flags rather than raising the cap.
- **Hypothesis format is non-negotiable.** Every idea uses `If / then / because`. Step 4 enforces it; ideas that don't conform get reframed before write.
