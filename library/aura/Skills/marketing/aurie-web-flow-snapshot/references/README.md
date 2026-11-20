# aurie-web-flow-snapshot

A foundation skill that produces a structured `flow_map` of the **Aurie Web onboarding/paywall flow** (a.k.a. AIWeb / AI Web). Other skills call this one to get clean flow data instead of duplicating sync + extraction logic.

## What it does

1. Reads live signup-flow source from `AuraHealth/AIWeb` via a 3-tier cascade:
   1. **GitHub Connector** (when Cowork has the Anthropic GitHub MCP available)
   2. **`git clone`** with the `gh` CLI credential (sparse clone to `/tmp/aiweb`)
   3. **Notion snapshot fallback** (`Aurie Web Signup Flow — Static Snapshot`)
2. Parses the source files following the procedure in `references/flow-extract.md` (R1–R12) to produce a typed `flow_map` with per-screen `shipped_features` annotations across 12 UX taxonomy tags.
3. Spot-checks SSR-rendered pages via `curl` for live drift detection.
4. Optionally refreshes the Notion snapshot page after a successful live build (`refresh-snapshot` flag).

The skill is variant-aware: R1 picks which onboarding variant to audit by reading the `Learnings/Experiments` Notion DB (Notion-first decision; source heuristic is the fallback only).

## What it returns

```yaml
flow_snapshot_run:
  flow_source: "live-github-clone @ a3f9c1d" | "snapshot"
  variant_decision: { variant: "webOnboardingV3", basis: "notion", ... }
  flow_map:                              # variant-filtered list
    - screen_index: 0
      screen_id: landing
      screen_name: Landing
      url_path: /signup
      headline: "Feel lighter in minutes"
      mixpanel_event: { name: "AI Sign Up Start", ... }
      shipped_features:
        - { tag: "trust_signals", mechanism: "...", source: "..." }
      ...
  snapshot_drift: { screens_changed: 0, ... }    # vs Notion snapshot
  live_verification: { ... }                      # SSR curl spot-checks
```

## When to use

The skill triggers on phrases like "build the Aurie Web flow_map", "refresh the Aurie Web flow snapshot", or "what's currently shipped on the Aurie Web `<screen>` screen". It also runs as a sub-call when `aurie-web-onboarding-audit` or `aurie-web-onboarding-experimentation-ideas` need flow data.

See `SKILL.md` → "When to use" for the full trigger phrase list.

## Repository layout

```
AINative/plugins/marketing/skills/aurie-web-flow-snapshot/
├── SKILL.md              # the skill itself (frontmatter declares Notion knowledge)
├── scripts/
│   └── aiweb-clone.sh    # Tier 2 sparse-clone helper (GITHUB_TOKEN, else `gh auth token`)
└── references/
    ├── flow-extract.md   # R1–R12 runtime extraction procedure + maintainer refresh guide
    └── README.md         # you are here
```

## Knowledge sources

```yaml
knowledge:
  reads:
    - Aurie Web Signup Flow — Static Snapshot   # page 35375e23af2080c581a8ebc7bb8c1af8
    - Tools/Mixpanel/Events                     # event lexicon cross-check
  writes:
    - Aurie Web Signup Flow — Static Snapshot   # only when --refresh-snapshot is passed
```

## Sibling skills

| Sibling | Relationship |
|---|---|
| `aurie-web-onboarding-audit` | Calls this skill for `flow_map`, then layers Mixpanel + qualitative friction analysis on top |
| `aurie-web-experiment-history` | Independent peer (reads `Learnings/Experiments`) — both are inputs to ideation |
| `aurie-web-onboarding-experimentation-ideas` | Top-level orchestrator that chains all three |

## Limitations

- **Single happy path per variant.** The `flow_map` captures one variant at a time (R1 picks). Auditing two variants requires two runs with different `aiweb-ref=<branch>` values.
- **No browser-based verification.** SSR pages are spot-checked via `curl`; SPA-rendered quiz pages are trusted to source.
- **Aurie Web only.** Aura experiments and Aurie mobile are out of scope.

## External

- AIWeb repo: <https://github.com/AuraHealth/AIWeb>
- Live signup URL: <https://www.aurie.ai/signup>
- Notion snapshot page: <https://app.notion.com/p/aurahealth/Aurie-Web-Signup-Flow-Static-Snapshot-35375e23af2080c581a8ebc7bb8c1af8>
