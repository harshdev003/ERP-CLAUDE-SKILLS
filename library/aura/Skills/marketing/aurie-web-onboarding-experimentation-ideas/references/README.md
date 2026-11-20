# aurie-web-onboarding-experimentation-ideas

A self-contained Claude Skill that generates A/B experiment ideas for the **Aurie Web onboarding/paywall flow** (a.k.a. AIWeb / AI Web). Built as a thin orchestrator over three sub-skills.

## Architecture

```
aurie-web-onboarding-experimentation-ideas (this skill — orchestrator)
    ├── aurie-web-flow-snapshot         (sub-skill: builds flow_map from AIWeb source)
    ├── aurie-web-onboarding-audit      (sub-skill: per-screen audit + Mixpanel funnel + Noom priors)
    │       └── aurie-web-flow-snapshot (transitive)
    └── aurie-web-experiment-history    (sub-skill: Aurie + Aura Web slices of Learnings/Experiments)
```

This skill owns: **idea generation, dedup, preview, write to Notion, final report.** Everything else delegates.

| Concern | Lives in |
|---|---|
| Source sync (3-tier cascade) + flow_map extraction + SSR verification | `aurie-web-flow-snapshot` |
| Per-screen friction audit + Mixpanel funnel + instrumentation health + Noom UX priors | `aurie-web-onboarding-audit` |
| `Learnings/Experiments` reads (Aurie + Aura slices) | `aurie-web-experiment-history` |
| Idea generation + dedup + preview + write | This skill |

## What it does

1. **Step 0** — Load Aurie brand voice + idea-schema.
2. **Step 1** — Get experiment history → calls `aurie-web-experiment-history`.
3. **Step 2** — Get flow_map + audit + Mixpanel funnel + instrumentation health + Noom priors → calls `aurie-web-onboarding-audit` (which calls `aurie-web-flow-snapshot` transitively).
4. **Step 3** — Generate 5–8 ideas, each grounded in (a) a specific audit `opportunity_id` or `concern_id`, (b) Noom patterns, (c) historical context (Aurie wins/losses + Aura transferable patterns).
5. **Step 4** — Two-pass dedup: Pass A vs `experiment_state` (don't repeat tests we've run), Pass B vs `flow_map[*].shipped_features` (don't propose features that are already shipped).
6. **Step 5** — Print full preview + ask user routing.
7. **Step 6** — Write approved ideas to `Learnings/Experiments` Notion DB (Status blank → "Idea" phase).
8. **Step 7** — Final report with idea URLs.

## Repository layout

```
AINative/plugins/marketing/skills/aurie-web-onboarding-experimentation-ideas/
├── SKILL.md      # the skill itself (frontmatter declares Notion knowledge)
└── references/
    ├── idea-schema.md   # generated-idea field definitions, validation rules, body-stuffing template
    └── README.md        # you are here
```

This skill **no longer carries** `scripts/aiweb-clone.sh` or `references/flow-extract.md` — those moved to `aurie-web-flow-snapshot` since they're concerned with source sync + extraction.

## Knowledge sources

The frontmatter declares only what this skill reads directly:

```yaml
knowledge:
  reads:
    - Products/Aurie               # brand voice priors used by Step 3
  writes:
    - Learnings/Experiments             # new "Idea" rows (Step 6)
```

Sub-skills declare their own reads:

| Sub-skill | Reads from Notion |
|---|---|
| `aurie-web-flow-snapshot` | `Aurie Web Signup Flow — Static Snapshot` (Tier 3 fallback), `Tools/Mixpanel/Events` |
| `aurie-web-onboarding-audit` | `Noom Onboarding — UX Priors`, `Tools/Mixpanel/Events`, `Tools/Mixpanel/Super Properties` |
| `aurie-web-experiment-history` | `Learnings/Experiments` (Aurie + Aura slices) |

## When to use

Trigger phrases (`Aurie Web`, `AIWeb`, and `AI Web` are interchangeable):

- "ideate experiments for Aurie Web"
- "generate Aurie Web onboarding experiment ideas"
- "what should we test next on Aurie Web onboarding"
- "propose A/B tests for Aurie Web onboarding"
- "next batch of Aurie Web experiments"
- "give me fresh ideas for the Aurie Web signup funnel"

## Inputs (optional flags)

- `count=<N>` — how many ideas. Default `5`. Capped at `8`.
- `focus=<screen-name>` — narrow ideation to a specific screen.
- `dry-run` — preview only, no Notion writes.
- `allow-cached-subskills` — opt in to reusing recent sub-skill output instead of fresh invocation. Off by default. See [Strict sub-skill invocation](#strict-sub-skill-invocation).
- Pass-through to sub-skills: `aiweb-ref=<ref>`, `skip-aiweb-sync`, `skip-live-verify`, `skip-mixpanel`, `skip-audit`, `audit-mode=full|focused`.

## Strict sub-skill invocation

By default, the orchestrator runs in **strict mode**: every sub-skill must be invoked fresh on every run, with a unique `run_id` and a real freshness signal (Notion query timestamp, git commit SHA + clone time, Mixpanel query timestamp). The orchestrator captures all sub-skill `run_id`s and prints them in a **Sub-skill ledger** at the end of every run, e.g.:

```
─── Sub-skill ledger (run iter-9f12) ───
  ✓ aurie-web-experiment-history    run_id=ehs-7a3c   notion_query_at=2026-05-01T20:14Z   aurie=47 aura=165
  ✓ aurie-web-onboarding-audit      run_id=aud-9b2e   noom_fetched_at=2026-05-01T20:14Z   funnel_pulled_at=2026-05-01T20:15Z
    ↳ aurie-web-flow-snapshot      run_id=fls-2b8e   commit_sha=5cd8150 cloned_at=2026-05-01T20:15Z
─────────────────────────────────────────
  Strict mode: enforced
```

If the orchestrator can't account for a fresh `run_id` from any sub-skill (because the agent silently shortcut a call), it halts with a `STRICT MODE VIOLATION` and refuses to print the final report. This is the failure mode the skill is hardened against — Cowork agents tend to optimize toward token efficiency by reusing prior context, and silent reuse destroys grounding integrity.

If you genuinely want to reuse recent sub-skill output (e.g. you just ran the skill 5 minutes ago and want to iterate cheaply on idea-generation prompt phrasing), pass `allow-cached-subskills`. The ledger will then show reused entries as `⚠ REUSED` with age + prior `run_id`.

## Prerequisites

### Hard

- **Notion MCP** authenticated AND the integration has read/write access to `Learnings/Experiments` (`2cbe45a3246040658b9f20559b5b452b`). Sub-skills inherit this requirement and add their own (Noom priors page, flow snapshot page).
- `references/idea-schema.md` present in this skill's folder.

### Soft (graceful degradation)

- **Live AIWeb access** (Tier 1 GitHub Connector or Tier 2 git clone with the `gh` CLI credential) — handled by the flow-snapshot sub-skill. Falls back to Notion snapshot when neither is available.
- **Mixpanel MCP** — handled by the audit sub-skill. Without it, ideas use `grounding_source: audit` instead of `grounding_source: funnel`.

## Key design choices

| Decision | Why |
|---|---|
| **Thin orchestrator** | Idea generation + dedup + write are this skill's only domain. Source extraction, audit, and history reads each have plausible standalone consumers — they belong in their own skills. |
| **Audit owns Noom priors** | Noom UX priors describe screen-level patterns. The audit grades screens; it's the natural owner. The orchestrator receives them via `audit_run.noom_priors` for idea generation. |
| **Same trigger phrases as before the split** | User invocations don't change. The split is internal. |
| **`Learnings/Experiments` is the only destination** | Canonical DB shared across team tooling. No Excel, no separate tracker. |
| **Idea-specific fields live in the page body** | `Target Screen`, `Target Metric`, `Proposed Change`, etc. don't have native columns. They go into `## What was tested` as greppable `<key>: <value>` markers per `references/idea-schema.md`. |
| **Append-only writes** | Step 6 only creates rows. Re-runs never overwrite existing rows. |

## Differences from the AINative-plugin sibling

The AINative-plugin skill at `AINative/plugins/data/skills/ideate-experiments/` does the same job for hosts with full Playwright MCP access (Claude Desktop). It crawls the live signup flow.

This Cowork skill makes a different trade-off: instead of crawling, it reads AIWeb source (live, via Connector or clone) and verifies SSR pages via `curl`. Both produce equivalent `flow_map` quality when source is fresh. Both write to the same Notion DB and use compatible idea-schema body markers.

## Limitations

- **Single happy path per run.** Each run audits one variant (the active one, picked Notion-first by `aurie-web-flow-snapshot`'s R1).
- **No browser-based live verification of dynamic copy.** Quiz steps trusted to source extraction; live SSR pages spot-checked via `curl`.
- **Aurie Web only.** Aura experiments and Aurie mobile are out of scope.
- **`count` cap of 8.** More ideas per batch dilutes review quality. Run twice with different `focus` flags rather than raising the cap.

## Sub-skills

| Sub-skill | Plugin path | Role |
|---|---|---|
| [`aurie-web-flow-snapshot`](../../aurie-web-flow-snapshot/SKILL.md) | `marketing:` | Foundation — builds `flow_map` from AIWeb source |
| [`aurie-web-onboarding-audit`](../../aurie-web-onboarding-audit/SKILL.md) | `marketing:` | Diagnosis — friction analysis + Mixpanel + Noom priors |
| [`aurie-web-experiment-history`](../../aurie-web-experiment-history/SKILL.md) | `marketing:` | History — Aurie + Aura Web slices of `Learnings/Experiments` |

## External

- Live signup URL: <https://www.aurie.ai/signup>
- AIWeb repo: <https://github.com/AuraHealth/AIWeb>
- Mixpanel project: `963821` (Project Aura), workspace `30768`
- `Learnings/Experiments` Notion DB: <https://www.notion.so/2cbe45a3246040658b9f20559b5b452b>
