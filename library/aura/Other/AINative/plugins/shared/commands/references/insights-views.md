# The two Linear insights view URLs

Companion to [`../prepare-monday-deck.md`](../prepare-monday-deck.md), which owns the reading
procedure and the reconciliation rules. This file owns the two URLs themselves and the
identities inside them, so a run never has to ask for them.

Both slides are the **same saved view** — `all-issues-270a13596592`, the workspace "All issues"
view — opened twice with different `filter=` and `insights=` params. Neither configuration is
what the view holds by default, which is why each slide is driven from a full URL rather than
from the view as saved. Open the URL in Claude in Chrome, wait for the insights panel to leave
its `Loading…` state, then `get_page_text`.

## Velocity (WoW)

```
https://linear.app/aura-health/view/all-issues-270a13596592?filter=eyJhbmQiOlt7ImNyZWF0ZWRBdCI6eyJndGUiOiIyMDI2LTAyLTAxIiwibmVxIjoiMjAyNi0wMS0wMSJ9fSx7InN0YXRlIjp7Im5hbWUiOnsiaW4iOlsiRG9uZSJdfX19LHsiYXNzaWduZWUiOnsib3IiOlt7ImlkIjp7ImluIjpbIjY3MDNmYjhmLWQxNjAtNGJkMC1hMGY3LTliZjkwYTBhZDI5OSIsIjE4ZTMyZjcxLWU0ZjQtNGQzNS1hMzc4LTM3ODI1ZDJiMjFjOCIsIjkzNTgyZTc3LTk0ZTUtNDIyMC04Y2IzLTBmZjYyZWFlMTZjMiIsImU2YTU2Y2UwLTUxODgtNGVlMy04MTdmLTk2NWUyODYzZWU0NCIsImZjNDRkOWVhLTIzY2YtNGYyMi04M2M0LTRhNWM5ZmFjZjdmMCJdfX1dfX0seyJsYWJlbHMiOnsib3IiOlt7ImV2ZXJ5Ijp7ImFuZCI6W3sibmFtZSI6eyJuaW4iOlsiQUklMjBGaXJzdCIsIlRlY2glMjBEZWJ0Il19fSx7InBhcmVudCI6eyJuYW1lIjp7Im5pbiI6WyJBSSUyMEZpcnN0IiwiVGVjaCUyMERlYnQiXX19fV19fSx7Imxlbmd0aCI6eyJlcSI6MH19LHsibnVsbCI6dHJ1ZX1dfX1dfQ&insights=eyJtIjoiZWZmb3J0IiwiZCI6ImNvbXBsZXRlZEF0IiwicyI6ImFzc2lnbmVlIiwiZnVsbHNjcmVlbiI6dHJ1ZSwiZW1wdHlTZWciOnRydWUsImRhdGVBZ2ciOiJ3ZWVrIiwiZGlzcGxheSI6ImdyYXBoIiwibFlTY2FsZSI6ImxvZyIsImFnZ3JlZ2F0aW9ucyI6WyJtZWRpYW4iLCJwOTUiXSwiZERpciI6ImFzYyIsImxPcmRlciI6ImRpbWVuc2lvbiIsImxEaXIiOiJhc2MifQ
```

- `insights`: `m=effort`, `d=completedAt`, `dateAgg=week`, `s=assignee`, `lYScale=log`
- `filter`: state `Done` · `createdAt >= 2026-02-01` · **5** assignee ids · labels **excluding**
  `AI First` and `Tech Debt` (issues carrying no labels are included)

`lYScale=log` affects only the on-screen graph, which understates the spread; the data table
`get_page_text` returns is unaffected.

## Effort by Label

```
https://linear.app/aura-health/view/all-issues-270a13596592?filter=eyJhbmQiOlt7InN0YXRlIjp7Im5hbWUiOnsiaW4iOlsiRG9uZSJdfX19LHsiY29tcGxldGVkQXQiOnsiZ3RlIjoiLVAxVyJ9fSx7ImFzc2lnbmVlIjp7Im9yIjpbeyJpZCI6eyJpbiI6WyIxOGUzMmY3MS1lNGY0LTRkMzUtYTM3OC0zNzgyNWQyYjIxYzgiLCI2NzAzZmI4Zi1kMTYwLTRiZDAtYTBmNy05YmY5MGEwYWQyOTkiLCI5MzU4MmU3Ny05NGU1LTQyMjAtOGNiMy0wZmY2MmVhZTE2YzIiLCJlNmE1NmNlMC01MTg4LTRlZTMtODE3Zi05NjVlMjg2M2VlNDQiXX19XX19LHsib3IiOlt7ImxhYmVscyI6eyJvciI6W3sib3IiOlt7Im5hbWUiOnsiZXEiOiJBbmFseXRpY3MlMjAlMjYlMjBEYXRhIn19LHsicGFyZW50Ijp7Im5hbWUiOnsiZXEiOiJBbmFseXRpY3MlMjAlMjYlMjBEYXRhIn19fV19LHsib3IiOlt7Im5hbWUiOnsiZXEiOiJBdXJhJTIwQXBwJTIwSW1wcm92ZW1lbnRzIn19LHsicGFyZW50Ijp7Im5hbWUiOnsiZXEiOiJBdXJhJTIwQXBwJTIwSW1wcm92ZW1lbnRzIn19fV19LHsib3IiOlt7Im5hbWUiOnsiZXEiOiJCdWdzIn19LHsicGFyZW50Ijp7Im5hbWUiOnsiZXEiOiJCdWdzIn19fV19LHsib3IiOlt7Im5hbWUiOnsiZXEiOiJDaGFyYWN0ZXIlMjAlMjYlMjBFbW90aW9uYWwlMjBCb25kaW5nIn19LHsicGFyZW50Ijp7Im5hbWUiOnsiZXEiOiJDaGFyYWN0ZXIlMjAlMjYlMjBFbW90aW9uYWwlMjBCb25kaW5nIn19fV19LHsib3IiOlt7Im5hbWUiOnsiZXEiOiJDb2FjaGluZyUyMCUyNiUyMFByb2Nlc3NpbmclMjBFZmZpY2FjeSJ9fSx7InBhcmVudCI6eyJuYW1lIjp7ImVxIjoiQ29hY2hpbmclMjAlMjYlMjBQcm9jZXNzaW5nJTIwRWZmaWNhY3kifX19XX0seyJvciI6W3sibmFtZSI6eyJlcSI6IkNvcmUlMjBVWCUyMCUyNiUyMEFjdGl2YXRpb24ifX0seyJwYXJlbnQiOnsibmFtZSI6eyJlcSI6IkNvcmUlMjBVWCUyMCUyNiUyMEFjdGl2YXRpb24ifX19XX0seyJvciI6W3sibmFtZSI6eyJlcSI6Ikdyb3d0aCUyMCUyNiUyME1vbmV0aXphdGlvbiJ9fSx7InBhcmVudCI6eyJuYW1lIjp7ImVxIjoiR3Jvd3RoJTIwJTI2JTIwTW9uZXRpemF0aW9uIn19fV19LHsib3IiOlt7Im5hbWUiOnsiZXEiOiJJbmZyYXN0cnVjdHVyZSUyQyUyMFJlbGlhYmlsaXR5JTIwJTI2JTIwUGVyZm9ybWFuY2UifX0seyJwYXJlbnQiOnsibmFtZSI6eyJlcSI6IkluZnJhc3RydWN0dXJlJTJDJTIwUmVsaWFiaWxpdHklMjAlMjYlMjBQZXJmb3JtYW5jZSJ9fX1dfSx7Im9yIjpbeyJuYW1lIjp7ImVxIjoiTWFya2V0aW5nJTIwJTI2JTIwRGlzdHJpYnV0aW9uIn19LHsicGFyZW50Ijp7Im5hbWUiOnsiZXEiOiJNYXJrZXRpbmclMjAlMjYlMjBEaXN0cmlidXRpb24ifX19XX0seyJvciI6W3sibmFtZSI6eyJlcSI6IlBsYXRmb3JtJTIwRXhwYW5zaW9uIn19LHsicGFyZW50Ijp7Im5hbWUiOnsiZXEiOiJQbGF0Zm9ybSUyMEV4cGFuc2lvbiJ9fX1dfSx7Im9yIjpbeyJuYW1lIjp7ImVxIjoiU2VjdXJpdHklMjAlMjYlMjBTYWZldHkifX0seyJwYXJlbnQiOnsibmFtZSI6eyJlcSI6IlNlY3VyaXR5JTIwJTI2JTIwU2FmZXR5In19fV19LHsib3IiOlt7Im5hbWUiOnsiZXEiOiJUZWNoJTIwRGVidCJ9fSx7InBhcmVudCI6eyJuYW1lIjp7ImVxIjoiVGVjaCUyMERlYnQifX19XX0seyJvciI6W3sibmFtZSI6eyJlcSI6IlRlY2glMjBJbnRlZ3JhdGlvbnMifX0seyJwYXJlbnQiOnsibmFtZSI6eyJlcSI6IlRlY2glMjBJbnRlZ3JhdGlvbnMifX19XX0seyJvciI6W3sibmFtZSI6eyJlcSI6IlN1cHBvcnQifX0seyJwYXJlbnQiOnsibmFtZSI6eyJlcSI6IlN1cHBvcnQifX19XX0seyJvciI6W3sibmFtZSI6eyJlcSI6IkVuZ2FnZW1lbnQlMjAlMjYlMjBSZXRlbnRpb24ifX0seyJwYXJlbnQiOnsibmFtZSI6eyJlcSI6IkVuZ2FnZW1lbnQlMjAlMjYlMjBSZXRlbnRpb24ifX19XX1dfX1dfV19&insights=eyJtIjoiZWZmb3J0IiwiZCI6ImxhYmVsIiwiZnVsbHNjcmVlbiI6dHJ1ZSwiZGF0ZUFnZyI6IndlZWsiLCJkaXNwbGF5IjoiZ3JhcGgiLCJsWVNjYWxlIjoibG9nIiwiYWdncmVnYXRpb25zIjpbIm1lZGlhbiIsInA5NSJdLCJkRGlyIjoiYXNjIiwibE9yZGVyIjoiZGltZW5zaW9uIiwibERpciI6ImFzYyJ9
```

- `insights`: `m=effort`, `d=label`
- `filter`: state `Done` · `completedAt >= -P1W` · **4** assignee ids · labels **including** the
  15-label list below

The label clause matches on label **names** (URL-encoded), not ids, and each entry matches the
label itself *or* its parent label.

## Who is in each filter

The two views do **not** carry the same assignees. Check this before reading anything into a
per-person figure or a missing name.

| Assignee id | Name | Handle | Velocity | Effort by Label |
|---|---|---|---|---|
| `18e32f71-e4f4-4d35-a378-37825d2b21c8` | Jerome Curlier | `jerome` | ✓ | ✓ |
| `e6a56ce0-5188-4ee3-817f-965e2863ee44` | Rohan | `rohan` | ✓ | ✓ |
| `93582e77-94e5-4220-8cb3-0ff62eae16c2` | Naveed | `naveed1` | ✓ | ✓ |
| `6703fb8f-d160-4bd0-a0f7-9bf90a0ad299` | Ashish | `ashish` | ✓ | ✓ |
| `fc44d9ea-23cf-4f22-83c4-4a5c9facf7f0` | Farhan | `farhan` | ✓ | — |

**Farhan is in the velocity view but not the label view.** The label slide's totals cover four
people while the velocity slide covers five. Neither slide is wrong, but they are not the same
population — one more reason not to carry a number between them.

**Resolve any assignee sitting at zero before treating it as a finding.** A deactivated Linear
account left in a saved view reads exactly like a member who shipped nothing, and naming a
departed teammate that way on an all-hands slide is the failure mode. Check `isActive`:

```
mcp__linear__get_user(query: "<assignee-id>")
```

If it comes back `isActive: false`, the zero is a stale filter: leave the person off the chart
and out of the insight, and prune the id from the saved view. Pruning a zero-effort id is safe
for the slide — it moves the all-time grand total the page reports, and no week's figures.

## The 15-label include list (Effort by Label)

Analytics & Data · Aura App Improvements · Bugs · Character & Emotional Bonding ·
Coaching & Processing Efficacy · Core UX & Activation · Engagement & Retention ·
Growth & Monetization · Infrastructure, Reliability & Performance · Marketing & Distribution ·
Platform Expansion · Security & Safety · Support · Tech Debt · Tech Integrations

This list is **not** derivable from the command's category mapping, which enumerates 19 labels —
a superset that adds Major Feature, Tech Quality, QA and Experimentation, none of which are in
the filter. That is why the list is written out here rather than left to be reconstructed.

The include list also gates which **issues** count, not which **rows** appear: the insights
dimension slices by every label those issues carry, so a label outside these 15 can still show
up as a row.
