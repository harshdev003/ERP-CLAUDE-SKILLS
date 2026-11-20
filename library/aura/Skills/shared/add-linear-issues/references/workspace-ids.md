# Hardcoded workspace values

Team, project, label, state and user identifiers for the AuraHealth Linear workspace. Cycles
are resolved at runtime, not pinned — see "Cycles" below.

These are inlined rather than looked up per run because the lookup would cost a round-trip on every invocation for values that change perhaps twice a year. That makes them copies — so when one changes, grep the repo for the old value and update every hit in the same PR (`/CLAUDE.md` → "Canonical facts").

Use these directly — no runtime lookup needed. Every ID below was **verified against the
Linear API on 2026-09-02** — resolved by ID and checked against the name and scope in its
row. To re-verify after a workspace change, query `teams`, `projects`, `issueLabels`,
`workflowStates`, or `users` directly (or look one up with the helper, e.g. `find-issue`).
The canonical team list is maintained in AI Knowledge under **Tools/Linear** — re-verify
there if teams are added or removed.

## How a wrong ID fails

Not every row is equally dangerous, and the difference is not obvious from looking at the
table. Read this before trusting a row you have not re-verified.

| Row class | An ID that **no longer exists** | An ID that **still resolves but drifted** |
|-----------|--------------------------------|-------------------------------------------|
| Teams | **Loud** — `Entity not found: Team` | **Silent** — card lands on the wrong team |
| Labels | **Loud** — `Entity not found in validateAccess: labelIds` | **Silent** — card carries the wrong label |
| Projects | **Loud** — `… validateAccess: projectId` | **Silent** — card lands in the wrong project |
| Users | **Loud** — `… validateAccess: assigneeId` | **Silent** — card assigned to the wrong person |
| Cycles | **Loud** — `… validateAccess: cycleId` | **Silent** — card lands in the wrong cycle |
| Statuses | **Loud** — `… validateAccess: stateId`, or `workflow state not in same team as issue` for a valid ID from another team | **Silent, and the most dangerous** — the card is created and looks completely normal; it just sits in the wrong column |

**Loud is the good case.** `issueCreate` validates the whole input before writing, so a
dead ID rejects the entire mutation and **nothing is created** — you get an error, not a
half-built card. Every class above behaves this way (verified by attempting a create with a
deliberately dead ID for each field, 2026-09-02).

**Silent is the case to guard against**, and it has two forms:

1. **A row whose ID is still live but whose meaning moved** — someone renamed a state,
   repurposed a label, archived a project and made a new one with the same name. The
   mutation succeeds. Nothing anywhere reports that the card went to the wrong place.
   Statuses are worst here because a status is the one field a human reader assumes is
   correct precisely *because* the card exists.
2. **A row that is missing from a table this file declares closed.** The Labels table says
   "only use labels from this table", so a request for something with no row leaves no legal
   option, and the nearest lookalike ID in this file becomes tempting. Several names appear
   in more than one table — `QA` and `Support` are each both a team and a label — so the
   lookalike is usually an ID of the wrong *kind*, which Linear rejects.
   **If you need a label, status, project, or team that is not in these tables, stop and say
   so — never substitute an ID from a different table.**

**Statuses therefore get re-verified on a different schedule than everything else.** When
you touch this file for any reason, re-run the status IDs against
`teams { nodes { key states { nodes { id name } } } }` — a stale status is the one error
that will never surface on its own.

### Teams

| Team | Key | ID | Use when |
|------|-----|----|----------|
| Product/Design | PROD | `7e95fa4a-7510-447d-adc7-c663612e70c6` | New features, bugs, data, experiments, improvements, UI/UX — **default** |
| Engineering | ENG | `ffcc6973-8ebf-4f92-b3b2-5568decbbae8` | No product relevance: releases, devops, pure eng execution |
| Backend | ENGBACK | `e25556d5-9f7f-4758-8ac3-4505102aa550` | Backend service work (ENGBACK-* issues) |
| Mobile | ENGMOB | `5497338e-312b-4ddf-ac5f-3339399c956d` | iOS / Android native work |
| Web | ENGWEB | `577a9e8b-f2bc-46e1-b170-30aa13e71ee8` | Web frontend engineering |
| Desktop | ENGDESK | `a2e85af2-36ce-48a8-8a57-31fcaef224c1` | Desktop app (Electron) |
| Prompt | ENGPROM | `8b9add50-f86d-4719-8567-0afc8a063fbb` | Prompt / AI tuning work |
| QA | QA | `e083e29b-c7c0-4f88-9f85-d04f722cd4c2` | QA and testing |
| Data/DS | DATA | `aa5190e7-50c9-4de8-8dd6-a252fd5b78c7` | Data science and analytics |
| Support | ENGSUP | `762a1dc5-c384-4d40-a23d-040c225548ed` | Support engineering |
| Customer Success | CUS | `9b5124d8-7484-4a32-b4d7-6ae3e5838212` | Customer success |
| Webflow | WEBFLOW | `0e07a512-cf23-4ff6-91b0-904aab75d453` | Webflow / marketing site |

> **`QA` is both a team and a label, and they are different IDs.** The team above
> (`e083e29b-…`) goes in `teamId`. The QA **label** (`956999f6-…`, in the Labels table
> below) goes in `labelIds`. Passing the team ID as a label is the exact mistake that
> breaks issue creation — see "How a wrong ID fails" above. The same shape applies to
> `Support`, which is both a team (`ENGSUP`, `762a1dc5-…`) and a label (`d9aab87b-…`).

### Statuses

Workflow states are **team-scoped** in Linear — always use the IDs for the team the issue belongs to.

> **These are the silent rows.** A wrong-but-live status ID creates the card successfully
> and puts it in the wrong column, with no error anywhere. Re-verify them whenever you touch
> this file — see "How a wrong ID fails" above.

**Product/Design team** (`PROD-*`)

| Status | ID | Use when |
|--------|-----|----------|
| Todo | `85f68398-a8b5-4df6-8491-0cfc32279769` | No PR exists yet |
| Eng In Progress | `53c1383f-fb0e-485d-b31a-f730a4b9f39f` | PR is open as a draft |
| In Review | `56f194ce-ab02-48a5-b6e6-75f095ddd6f2` | PR is marked ready for review |
| Done | `530cf975-c1e9-4cf8-95fa-1c0a296e2ecc` | PR is merged |

> Product/Design has **no `To Be Released` state** — the Engineering equivalent below has no
> counterpart here. A PROD card goes `In Review` → `Done` on merge, with no release gate.

**Engineering team** (`ENG-*`)

| Status | ID | Use when |
|--------|-----|----------|
| Todo | `37082212-94ba-468a-b2a3-ef5d9a2f4b59` | No PR exists yet |
| In Progress | `f0e36626-72b4-4cf6-945c-ba58fe55afe2` | PR is open as a draft |
| In Review | `e2f91b3a-4f04-451e-bf21-dca5013af14b` | PR is marked ready for review |
| To Be Released | `8d60b67c-eb93-46ee-aa80-cbd8189c2ef5` | PR is merged, but no build or deploy carries it yet |
| Done | `f4a20e93-5f19-492d-9e61-ad9308ff80f7` | A build or deploy carries the work |

**Mobile team** (`ENGMOB-*`)

| Status | ID | Use when |
|--------|-----|----------|
| Todo | `090653f2-f21a-4718-89cd-0438e781f82c` | No PR exists yet |
| In Progress | `2f3af5c5-ed04-459b-84b0-f377d75141e2` | PR is open as a draft |
| In Review | `7b782b06-d9c5-41ba-8588-35b193d928ae` | PR is marked ready for review |
| To Be Released | `38a0275e-05e9-4f57-8cd7-114917c4ca83` | PR is merged, but no build carries it yet |
| Done | `c6860d45-1749-4d95-8f40-3e509d58862e` | A TestFlight build carries the work |

> Mobile has the same release gate as Engineering: a merged `ENGMOB-*` card waits in
> `To Be Released` and reaches `Done` once a TestFlight build carries it. It is a **separate
> team** from `ENG-*` — its state IDs are its own, and an `ENG-*` lookalike is rejected.

**Backlog — every team** (for cards filed under [Bug Investigations](#standing-parents),
which can land on any team). Verified 2026-09-28.

| Team | Backlog state ID |
|------|------------------|
| PROD | `e55ef188-cea1-4653-8efa-2eb42ed81b60` |
| ENG | `861976bb-d6d7-4fb8-9c12-8059c3e5c816` |
| ENGMOB | `998e7fb7-d5f0-4dc8-be2b-dbc75f12b6d7` |
| ENGBACK | `7f06aeb4-5db6-4251-a54e-206c41bff5d8` |
| ENGWEB | `010bc234-8a46-4cf6-9d57-8a6bd916fdb8` |
| ENGDESK | `fdd3058d-fd48-4057-accd-2d5b3a22f448` |
| ENGPROM | `953f615f-2c89-49cb-ab85-0a1a52bb5fbe` |
| QA | `2534afa2-50f2-43cd-b6fe-7fc2d6887c5d` |
| DATA | `7017f398-1782-4cdc-b71e-9f65a3ebb43c` |
| ENGSUP | `88bb06a5-2105-46db-899a-e322052f633f` |
| CUS | `3b53bc1c-7149-4331-bf68-83d911bdbca6` |
| WEBFLOW | `94392eb0-059c-40c3-8211-a74adae9abe6` |

> **Never cross teams.** Passing a Product/Design state ID on an Engineering issue (or vice
> versa) is rejected. `issueCreate` returns `workflow state not in same team as issue`
> ("The provided Workflow state ‹state› is not associated with the issue's team ‹team›");
> other mutations word the same rejection differently. Match the `stateId` to the issue's
> `teamId`.

### Projects

| Project | ID | Use when |
|---------|-----|----------|
| AI Native | `c110e9d4-1346-49b8-b7e2-44d79ba5bab8` | AI tooling, skills, knowledge, agents |
| Aurie AI - Mobile | `50b657cd-f184-4068-aec5-a1dbcac49d22` | iOS, Aurie, voice assistant, mobile AI features |
| Aurie AI - Desktop/Web | `fdfd427e-d3aa-44f3-b27d-4736a6226776` | Desktop app, Aurie web features |
| Aura - Core App | `a13d07fd-f427-4f35-a5af-fba68b015f95` | General Aura app work |
| Aura - Web | `4ee6841a-f5a7-496e-b546-992cc4ad7cd7` | Aura web portal |
| Aura - Content | `44608cb6-30f7-4747-9686-8a74ccaf1dd5` | Content, meditations, programs |

Default: when still unclear, prefer **Aurie AI - Mobile**.

### Standing parents

Parents that do not rotate with the cycle, so their IDs are safe to pin.

| Parent | Identifier | ID | Holds |
|--------|-----------|-----|-------|
| Bug Investigations | [PROD-6645](https://linear.app/aura-health/issue/PROD-6645/bug-investigations) | `00c4c20e-7cc9-4f0f-8b16-59b357137ca2` | Bugs surfaced as a side effect of other work, not yet investigated or prioritised — always Backlog, no cycle, no priority, label `Bugs` only. Once investigated, a card moves to the current `Bugs [Cycle N]` tracker. |
| Security and Privacy | [PROD-6926](https://linear.app/aura-health/issue/PROD-6926) | `8d74a0ff-bf1e-49a9-9a15-36936226c018` | Security findings — label `Security & Safety`, Backlog, on the team that owns the code. Verified 2026-10-14. |
| Voice & Agent — Bugs / Improvements | [PROD-6647](https://linear.app/aura-health/issue/PROD-6647/voice-and-agent-bugs-improvements) | `0a96aa78-6b37-493e-993a-c693cd851eac` | Voice-agent and LiveKit cards on any team; a mobile-side fix carries the `[Mobile]` title prefix. Verified 2026-10-26. |

### Title prefixes

Square-bracket prefixes mark a card's surface or why it is parked. They stack before the title, e.g. `[On Hold] [Mobile] Voice: …`.

| Prefix | Use when | Also do |
|--------|----------|---------|
| `[Mobile]` | A voice-agent or LiveKit card whose fix is in a mobile app rather than the agent | File it under the Voice & Agent parent above |
| `[On Hold]` | Work is paused for a decision | Prefix every linked PR `[ON HOLD]` (app and agent alike), set an open PR back to draft, and comment on the card with what it waits for |
| `[Livekit Support]` | The card waits on LiveKit's own support | Set it Blocked, with a comment naming the open question |

### Key Users

| User | ID | Use when |
|------|-----|----------|
| Alex (Designer) | `16d710a5-8040-4975-91c6-3e15bc702957` | Any design task |

### Labels

**CRITICAL: Only use labels from this table. Never use archived labels. Never create new labels.**

This table is the **complete set of workspace-scoped labels** — the ones available on every
team. Linear also has team-scoped labels (`Apple`, `Coaching`, `Database`, … — hundreds of
them, most duplicated per team); those are deliberately not listed, because a team-scoped
label ID is only valid on its own team and picking one here would be a coin flip.

**If the label you want is not in this table, stop and tell the user.** Do not substitute an
ID from the Teams or Projects table because the name matches — that is what breaks issue
creation (see "How a wrong ID fails" above).

| Label | ID | Description |
|-------|-----|-------------|
| AI First | `81f82e51-0528-4f9e-8f97-0c8dca59d873` | AI-first initiatives or agent-completed tasks |
| Tech Integrations | `19a6e93d-26c7-4b87-91bc-b01c24f271c1` | New data sources / third-party integrations |
| Aura Integration | `d1f2ca52-c112-4ef9-b0cf-1ca733528f45` | Content, 1-1 coaching, etc |
| Aura App Improvements | `71e0ebae-af23-44e6-8c83-849149315086` | General Aura app improvements |
| Core UX & Activation | `bb0f0d28-e681-4664-a0d6-fc8fbcb739c5` | Onboarding, navigation, first-session value |
| Engagement & Retention | `7f9b28b4-8ef3-43bd-923e-fbd8c048cc18` | Habit loops, nudges, return usage |
| Character & Emotional Bonding | `7509cd32-bd11-4d06-ad14-3643266eab7f` | Persona, warmth, trust-building |
| Coaching & Processing Efficacy | `a41abe99-1963-4737-95f1-78c938b932d2` | Emotional reflection and support |
| Growth & Monetization | `b68d6001-ce21-4af9-80b2-0b6ebf1fe567` | Revenue, growth experiments, B2B |
| Marketing & Distribution | `392eca9e-8f7a-491d-9286-f2936a9ce387` | Awareness, acquisition, referrals |
| Platform Expansion | `b30cef0d-de57-4dc1-bba0-f768b8802379` | New devices, geo, contexts |
| Infrastructure, Reliability & Performance | `6c665b54-87f1-46f6-8016-00575c441d17` | Backend systems, performance |
| Platform Reliability & Performance | `0c7c3393-1151-4488-b0c4-cfdecb41dd7d` | Infra stability and scale |
| Analytics & Data | `db17094a-b8e8-4afb-83d1-dc55c652c2d3` | Insights, data tooling |
| Data Design | `39f17946-9930-4ca1-9fa4-f577b4fa318a` | Data modeling and schema design |
| Experimentation | `62e6ccc0-5b1e-4007-8173-5bed370f3a46` | A/B tests and experiments |
| Tech Quality | `2511d2ae-b39c-4d2b-bc7c-14ba33d8a1b7` | Tech debt, frameworks, packages |
| Tech Debt | `1725ae9b-55d0-45bc-9858-068a877b16a3` | Debt paydown (overlaps `Tech Quality` — prefer whichever the parent card already uses) |
| Security & Safety | `42d2d1b3-7a51-4fa4-bb60-1c29f2fce43b` | Privacy, safeguards, ethics |
| Bugs | `0cdc116b-e9a0-46ee-8133-3481c2b64376` | Bug fixes |
| Eng Blocker | `fb7cf70f-a207-428d-a718-7558fcafd3de` | Engineering blockers |
| QA | `956999f6-b355-43ec-bfb0-804f6f91c978` | QA / test-pass cards — **the label, not the QA team ID** |
| QA Feedback | `a4b3434e-48ce-4661-864d-158f94090308` | Findings raised out of a QA pass |
| Code Review | `23b49226-7917-4dd8-8f0e-0f661e78ad87` | Code-review process work |
| Major Feature | `97d32141-c921-4fe1-b447-602b46d3905b` | Large multi-card feature efforts |
| Team & Process | `3f3a053e-98da-465b-be7a-69f579f78438` | Ways of working, rituals, tooling process |
| User Research | `4a3d82af-dbfc-4531-890f-16034d588df8` | Research tasks |
| Support | `d9aab87b-749a-4991-ac29-1bf0cebf5da3` | Support team tickets — **the label, not the ENGSUP team ID** |

### Cycles

**No cycle IDs are pinned here, on purpose.** Cycles rotate every couple of weeks, so any
inlined cycle ID is stale by definition within days of being written. Resolve the active one
at runtime instead:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/add-linear-issues/scripts/linear_api.py \
  current-cycle --team <team-id>
```

---
