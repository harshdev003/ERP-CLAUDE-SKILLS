# The twelve cards

Run order is the creation order. Create sequentially — `subIssueSortOrder` is not exposed
by the Linear MCP, so creation order is what sets it.

Resolve templates and labels **by name** via `list_templates` / `list_issue_labels`. The
ids below are a cross-check for a fast path, not the source of truth — a renamed or
re-created template changes its id.

| # | Template name | Card title | Epic | Labels | Pts |
|---|---|---|---|---|---|
| 1 | `Monthly Report gift redemption for %month%` | `Report gift redemption for <Month>` | month-end | Accounting | 3 |
| 2 | `Monthly Coach Analytics for %month%` | `Monthly Coach Analytics for <Month>` | month-end | Coaching, Payout | 8 |
| 3 | `Monthly coach payout for %month%` | `Monthly coach payout for <Month>` | month-end | Coaching, Payout | 5 |
| 4 | `Monthly community payout for %month%` | `Monthly community payout for <Month>` | month-end | Coaching, Payout | 5 |
| 5 | `Refund for Coaches` | `Refund for Coaches` | **mid-month** | Coaching, Payout | 3 |
| 6 | `Monthly Iterable Sync` | `Monthly Iterable Sync` | month-end | Iterable, Quality | 5 |
| 7 | `Monthly data tasks for all databases` | `Monthly data tasks for all databases` | month-end | Database | 5 |
| 8 | `Monthly Engineering Budget` | `Monthly Engineering Budget` | month-end | Budget | 5 |
| 9 | `Monthly update to payout for %month%` | `Monthly update to payout for <Month>` | **mid-month** | Coaching, Payout | 3 |
| 10 | `Update nodejs to version x.y.z` | `Update nodejs to version <Target>` | month-end | Infra, Quality | 5 |
| 11 | `Update backend node packages` | `Update node packages` | month-end | Infra, Quality | 5 |
| 12 | `Update Kubernetes cluster` | `Update Kubernetes cluster` | month-end | Infra, Quality | 5 |

`<Target>` on card 10 is the Node version from SKILL.md Step 3b. `<Month>` in every row above is the month being **closed** — including the two mid-month
rows, whose epic is named for the month the pass runs in instead. Only the epic title
takes the run month.

Ten cards go under the month-end epic, two under the mid-month one. Within each epic,
create in the numbered order above — cards 5 and 9 are numbered by their place in the
overall run order, and land as the mid-month epic's first and second children respectively.

**Total: 57 points** (52 when card 10 is skipped). If your total is neither, something in this map drifted from the
templates — reconcile before reporting.

## Two naming quirks, both load-bearing

Preserved from roughly twelve months of history — the card titles do **not** simply copy
the template names:

- Card 1 drops the `Monthly` prefix. The template is `Monthly Report gift redemption for
  %month%`; every card ever created from it is titled `Report gift redemption for <Month>`.
- Card 5, `Refund for Coaches`, never carries a month, even though it runs every month.

Cards 6, 7 and 8 also carry no month, so successive months produce same-titled cards. That
is the convention — the epic they hang off is what disambiguates them.

## Card 9's labels

The `Monthly update to payout for %month%` template declares `Coaching, Payout`, matching its
siblings. Apply them in the second pass like every other card — a template's declared labels
never survive a templated create.

## Dates

Cards **5 and 9** both carry a due date: the **10th** of the current month. Card 9 reads
settled payout numbers, so running it early produces figures that shift underneath it, and
card 5 has to land before it. They are the mid-month epic's only contents.

The runbook once said the 10th "is also the cycle end". That was a July 2026 coincidence,
not a rule — in the 2026-09 pass the cycle ended Sep 7, so the 10th fell after it.

Card 5 blocks card 9: it writes the Adjustment column that card 9 propagates. Record it as
a `blocks` relation, not just adjacent dates.

## Cards 10-12: the monthly runtime and cluster updates

Cards 10, 11 and 12 come from the ENGBACK templates their skills mirror; each template
body says which skill works it (`eng:update-nodejs`, `eng:update-node-packages`,
`eng:update-kubernetes`). Card 11 drops the template's `backend` from its title, matching
every `Update node packages` card ever filed. Like card 6, cards 11 and 12 carry no month.

`Infra` is a retired Backend label, same as the operational labels; it still applies on update.

Prod cluster operations are run by the code owner (Backend), with Jerome's go in that session.

## Ids observed 2026-09-01

Verify by name before use.

```
Backend (ENGBACK) team      e25556d5-9f7f-4758-8ac3-4505102aa550
Product/Design (PROD) team  7e95fa4a-7510-447d-adc7-c663612e70c6
Project "Aura - Core App"   a13d07fd-f427-4f35-a5af-fba68b015f95

templates
  1  34f0555c-b737-45a2-b320-4d9b70eec6f2
  2  7ed47f8c-c0f1-4354-a4b7-65c8cd9e11e1
  3  28a2ddb9-53fb-4018-b37b-85ba91b93e98
  4  dafc66fa-ed72-4be4-9b36-4563e0ec6f23
  5  64d17b9d-57fc-4735-be21-0b5fd90c7067
  6  ddd8bf6a-bf0b-4a03-829f-bb5daa15543a
  7  0abb4c41-7d74-4d7c-b059-53746effefa2
  8  64b9eba7-e16c-4a54-be0b-cfc535f9ff53
  9  444e1e76-bbcd-41ce-9263-804e85740eb5
  10 de428eba-f0ef-4230-ac34-2c23838afe82   (observed 2026-11-02)
  11 f3065ad8-a8ae-4c87-8254-25e06a1af4b0   (observed 2026-11-02)
  12 104f4e62-8db3-4d49-8cf6-6e9416a2a4fa   (created 2026-11-03)

labels (Backend)
  Accounting  48daab5e-c8a1-4a08-b027-86f20f69f636
  Coaching    2e7bdbc4-7b00-49ed-8957-7a90f6bd39a5
  Payout      816359fa-6014-4a86-a558-baa47aff28cd
  Iterable    e0a1b2fc-567c-4f46-b47d-eb120ca2ee89
  Quality     95e346d8-e195-42d7-b39f-5703d86e2bc5
  Infra       4c8c8fff-2643-4ad4-be32-9f88699e368b
  Database    36469165-67aa-4f0c-a95b-ab181e531bb3
  Budget      57deca2d-5548-40ec-b2e1-49ab942cd5ea
```
