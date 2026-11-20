---
name: harvest-knowledge
description: "Sweeps upstream canonical knowledge sources, finds what's changed since a chosen window, and **updates the AI Knowledge teamspace** so the L4 layer stays in sync. Runs on demand at any cadence (weekly, monthly, after a big project, whenever). Defaults to Notion (canonical teamspaces like Engineering, Prompts, Product, QA), and is designed to extend to Slack, Linear, Drive, and other sources over time. Classifies changes by the knowledge types defined on the Guide page, presents a write plan for approval, then applies create/update edits inside AI Knowledge. Writes only ever land in AI Knowledge — canonical sources are read-only. Complements `ingest-knowledge` (which ingests a single source on demand); `harvest-knowledge` is the periodic sweep. Trigger when the user says \"harvest knowledge\", \"run the knowledge harvest\", \"sync AI Knowledge with upstream\", \"what changed in Notion since {date}\", \"pull latest from Notion for agents\", or asks to refresh the L4 layer against upstream.\n"
metadata:
  version: "3.4.0"
knowledge:
  reads:
  - Guide/Index
  - Guide/Placement Rules
  - Tools/Notion
  - "*"
  writes:
  - "*"
---

(body trimmed — frontmatter copied verbatim from an installed rpm plugin snapshot, 2026-09-30)
