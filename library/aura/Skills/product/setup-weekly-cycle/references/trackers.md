# Tracker roster and workspace IDs

Hardcoded values for the Product/Design weekly rollover. `SKILL.md` reads this file at
Step 0 and keys every later step off the tables here.

---

## Teams

| Team | ID |
|------|----|
| Product/Design | `7e95fa4a-7510-447d-adc7-c663612e70c6` |
| Engineering | `ffcc6973-8ebf-4f92-b3b2-5568decbbae8` |

## Projects

| Project | ID |
|---------|----|
| AI Native | `c110e9d4-1346-49b8-b7e2-44d79ba5bab8` |
| Aurie AI - Mobile | `50b657cd-f184-4068-aec5-a1dbcac49d22` |
| Aura - Core App | `a13d07fd-f427-4f35-a5af-fba68b015f95` |

---

## Top-level trackers (11)

These parent issues are created and rolled over each cycle. All live on **Product/Design**.

| Title stem | Project | Labels | Priority | Assignee |
|------------|---------|--------|----------|----------|
| AI Native | AI Native | AI First | High (2) | Rohan |
| AI Cost Review and Changes | Aurie AI - Mobile | Infrastructure, Reliability & Performance | Medium (3) | Jerome |
| AI Latency Review and Changes | Aurie AI - Mobile | Infrastructure, Reliability & Performance | Medium (3) | Jerome |
| Aurie AI CRO — Conversion Rate Optimization | Aurie AI - Mobile | Growth & Monetization | Medium (3) | Farhan |
| AI Prompt Improvements | Aurie AI - Mobile | Character & Emotional Bonding | Medium (3) | Alyssa |
| Support | Aura - Core App | Support | Low (4) | Rohan |
| Bugs | Aurie AI - Mobile | Bugs | Low (4) | Rohan |
| Quality / Tech Debt | Aurie AI - Mobile | Tech Quality | Low (4) | Rohan |
| Data Analysis | *(none)* | Analytics & Data | *(none)* | Yawar |
| Data Design/Review | *(none)* | Analytics & Data | *(none)* | Yawar |
| QA | *(none)* | Analytics & Data | *(none)* | Yawar |

**Title format:** `<stem> [Cycle N]` — e.g. `AI Native [Cycle 28]`.

Rows marked *(none)* carry no project and no priority. Pass those fields as unset rather
than substituting a default — see `SKILL.md` Step 3 for the metadata-inheritance rule that
governs them.

---

## Nested trackers (5)

These are **sub-issues** of a top-level tracker rather than top-level parents, and roll
over on the same weekly cadence. Each carries the same `[Cycle N]` suffix as its parent —
the Product/Design cycle number.

| Title stem | Parent stem | Team | Project | Labels | Priority | Assignee |
|------------|-------------|------|---------|--------|----------|----------|
| Update Knowledge and Skills | AI Native | Engineering | AI Native | AI First | Medium (3) | Jerome |
| Experiment/Data Monitoring | Data Analysis | Product/Design | *(none)* | Analytics & Data | High (2) | Yawar |
| Sheet Update/Creation/Changes | Data Analysis | Product/Design | *(none)* | Analytics & Data | *(none)* | Yawar |
| Data Changing Requests | Data Analysis | Product/Design | *(none)* | Analytics & Data | *(none)* | Yawar |
| Data Investigation | Data Analysis | Product/Design | *(none)* | Analytics & Data | *(none)* | Yawar |

### Cycle assignment differs by team

The four Data Analysis children live on **Product/Design**, the same team as their
parent, so their `cycle` field takes the Product/Design cycle id directly.

`Update Knowledge and Skills` lives on **Engineering**, so its `cycle` field must be an
Engineering cycle. Engineering cycles are numbered independently of Product/Design but
carry matching **titles**: Engineering `number: 41` is titled `"Cycle 39"`, the same title
as Product/Design cycle `number: 39`. **Resolve the Engineering cycle by matching its
`title` to the Product/Design cycle's title — never by `number`.**

---

## Why the inventory is keyed on cycle id, not title text

Several stems are short, generic strings that appear inside unrelated issue titles —
`QA`, `Bugs`, `Support`, `Analytics`. A `list_issues(query: "QA Cycle 39")` search returns
a large set dominated by incidental matches, and a stem that genuinely has no tracker is
indistinguishable from one the search simply ranked out of the returned page.

`list_issues(cycle: "<cycle-id>", team: "Product/Design")` returns the membership of a cycle,
so a stem absent from a fully-paged list is definitively absent. `SKILL.md` Step 2 builds the
whole inventory from three such calls, paging each one until `hasNextPage` is false — an
active cycle routinely exceeds a single 250-issue page, and a truncated page forfeits exactly
the completeness this approach is chosen for.

One stem also contains an em-dash: `Aurie AI CRO — Conversion Rate Optimization`. Matching
against the inventory by a plain-ASCII fragment (`Aurie AI CRO`), case-insensitively, keeps
the em-dash out of any query round-trip.
