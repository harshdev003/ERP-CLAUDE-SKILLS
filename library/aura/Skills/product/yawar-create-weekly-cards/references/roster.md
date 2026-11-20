# Weekly card roster

The ten cards this skill creates every week, under three parents, and the workspace IDs it keys off. `SKILL.md`
reads this file at Step 0. Title strings and dates come from
[`scripts/resolve_week.py`](../scripts/resolve_week.py); this file owns everything else about
a card. Edit here to add, drop or reshape a card; edit the script only if a title's date
math changes.

---

## Workspace IDs

| Thing | Value |
|---|---|
| Team | Product/Design, `7e95fa4a-7510-447d-adc7-c663612e70c6` |
| Assignee | Yawar, `3fc032ae-2800-43fd-9bf6-d5641b99e76c` |
| Label | `Analytics & Data` (workspace label, resolve by name) |
| Project (Viktor card only) | AI Native, `c110e9d4-1346-49b8-b7e2-44d79ba5bab8` |
| State on create | Todo |

## Parents

All three parents are nested trackers that `product:setup-weekly-cycle` creates each Monday
under `Data Analysis [Cycle N]`. This skill never creates them; if one is missing the rollover
has not run yet and the skill stops. The Growth ROI cards sit under `Sheet Update/Creation/Changes`
because the sheet write is the job; `data:report-growth-roi` closes the same card once the
note is posted. The Viktor card is an investigation, so it sits under `Data Investigation`.

| Parent stem | Full title | Holds |
|---|---|---|
| `Experiment/Data Monitoring` | `Experiment/Data Monitoring [Cycle N]` | the six monitoring cards |
| `Sheet Update/Creation/Changes` | `Sheet Update/Creation/Changes [Cycle N]` | the two Growth ROI cards |
| `Data Investigation` | `Data Investigation [Cycle N]` | the Viktor card |

Recent instances, for orientation only (resolve live, never from this table):

| Cycle | Experiment/Data Monitoring | Sheet Update/Creation/Changes | Data Investigation |
|---|---|---|---|
| 44 | PROD-5613 | PROD-5614 | PROD-5616 |
| 45 | PROD-5978 | PROD-5979 | PROD-5981 |
| 46 | PROD-6282 | PROD-6283 | PROD-6285 |

---

## The ten cards, in creation order

Creation order is sub-issue order: the MCP does not expose `subIssueSortOrder`, so create
sequentially in this order and each parent reads as a checklist.

| # | Key | Title | Parent stem | Priority | Estimate | Labels | Project | Description |
|---|---|---|---|---|---|---|---|---|
| 1 | `data-monitoring` | `Data Monitoring {Sun}` | Experiment/Data Monitoring | High (2) | 5 | Analytics & Data | none | none |
| 2 | `experiment-monitoring` | `Experiment Monitoring {Sun}` | Experiment/Data Monitoring | High (2) | 5 | Analytics & Data | none | none |
| 3 | `experiment-monitoring` | `Experiment Monitoring {Mon}` | Experiment/Data Monitoring | High (2) | 5 | Analytics & Data | none | none |
| 4 | `experiment-monitoring` | `Experiment Monitoring {Tue}` | Experiment/Data Monitoring | High (2) | 5 | Analytics & Data | none | none |
| 5 | `experiment-monitoring` | `Experiment Monitoring {Wed}` | Experiment/Data Monitoring | High (2) | 5 | Analytics & Data | none | none |
| 6 | `experiment-monitoring` | `Experiment Monitoring {Thu}` | Experiment/Data Monitoring | High (2) | 5 | Analytics & Data | none | none |
| 7 | `growth-roi-aura` | `Update and report the Aura Growth ROI sheet (week of {PriorSun}-{PriorSat})` | Sheet Update/Creation/Changes | none (0) | 5 | Analytics & Data | none | [`templates.md` → Aura Growth ROI](templates.md#aura-growth-roi) |
| 8 | `growth-roi-aurie` | `Update and report the Aurie Growth ROI sheet (week of {PriorSun}-{PriorSat})` | Sheet Update/Creation/Changes | none (0) | 5 | Analytics & Data | none | [`templates.md` → Aurie Growth ROI](templates.md#aurie-growth-roi) |
| 9 | `viktor-weekly-review` | `Viktor weekly review [Cycle N]` | Data Investigation | Medium (3) | 5 | Analytics & Data | AI Native | [`templates.md` → Viktor weekly review](templates.md#viktor-weekly-review) |

Nine rows, ten cards: row 2 to 6 is the `experiment-monitoring` key repeated for each of the
five Sunday-to-Thursday dates. Every card takes the Product/Design team, the Yawar assignee,
state Todo, and the target cycle from `SKILL.md` Step 1.

**Date tokens.** `{Sun}` is the Sunday before the target Monday, `{Sat}` the Saturday after
it, both `M/D` with no zero padding. `{PriorSun}`/`{PriorSat}` are the same pair seven days
earlier — the week that just closed.

The monitoring cards carry the cycle's own week; **the two Growth ROI cards carry the prior
week**, because a week's sheet cannot be updated until that week ends, and the card is closed
by the Monday note `data:report-growth-roi` posts for that same week. For the cycle starting
Monday 2026-09-28 the cards are `Data Monitoring 9/27`, `Experiment Monitoring 9/27` through
`10/1`, `(week of 9/20-9/26)` for the two Growth ROI cards, and
`Viktor weekly review [Cycle 46]`. The two Sunday cards are dated the day before the
cycle opens and still sit in the target cycle; that is the established shape (`Experiment
Monitoring 9/13` sat in the cycle that opened Monday 9/14).

**Precedents the shapes come from.** Experiment Monitoring: PROD-6009 to PROD-6013. Growth
ROI: PROD-6323 (Aura) and PROD-6324 (Aurie). Viktor: PROD-4602 (title form) with the
acceptance criteria of PROD-5248. Data Monitoring is new as of 2026-09-22 and mirrors the
Experiment Monitoring metadata.

---

## Idempotency key

A card exists when a child of the target parent has a title that, after trimming and
case-folding, equals the rendered title or starts with it followed by a space. Nothing else
is compared, so a hand-made card with the right title is left alone even if its priority or
estimate differ, and a suffixed variant such as `Experiment Monitoring 9/20 (Partial)`
counts as that day's card. The two Sunday-dated cards are also matched against the previous
cycle's `Experiment/Data Monitoring` parent, because a Sunday card made by hand that evening
lands there (PROD-6013, first run on 2026-09-22).
