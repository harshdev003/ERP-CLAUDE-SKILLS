---
name: sync-experiments
description: "Bulk-imports the full Aura experiment catalog from the `GET /experiments` API into the `Learnings/Experiments` Notion database. Creates row stubs for any experiment that doesn't already have one, and updates `End Date` when the API shows it ended. Also reconciles what `endedAt` cannot see: an experiment whose winner shipped to everyone keeps a null `endedAt` and collapses to one variant, so those rows are marked `Stopped splitting` instead of sitting at `Running` forever, and every run reports how many rows still disagree with the live catalogue. Idempotent. Companion to `data:analyze-experiment`. Trigger on \"sync experiments\", \"import experiments to Notion\", \"refresh Learnings/Experiments from the Aura API\", \"sync the experiment catalog\", \"reconcile the experiment catalog\", \"which experiments actually stopped splitting\", \"why does this rolled-out experiment still say Running\", or a question about whether `Learnings/Experiments` is up to date with the live experiment server.\n"
metadata:
  version: "3.3.0"
knowledge:
  reads:
  - Guide/Index
  - Products/Aurie
  - Learnings/Experiments
  - Tools/Aura API
  - Tools/Aura API/Experiments/List Experiments
  - Tools/Slack
  - Tools/Notion
  writes:
  - Learnings/Experiments
---

(body trimmed — frontmatter copied verbatim from an installed rpm plugin snapshot, 2026-09-30)
