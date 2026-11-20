# Description templates

Markdown bodies for the three cards that carry a description. Fill the `{...}` tokens from
the `resolve_week.py` output before passing the body as `description` to `save_issue`. Pass
the text with real newlines, not escape sequences.

| Token | Source field | Example |
|---|---|---|
| `{report_week_label}` | `report_week_label` | `9/20-9/26` |
| `{report_week_closing}` | `report_week_closing` | `2026-09-26` |
| `{report_prev_week_label}` | `report_prev_week_label` | `9/13-9/19` |
| `{cycle}` | `cycle` | `46` |

The bodies are deliberately generic. Week-specific items (an open question carried from last
week's note, a known data seam) are added by hand once the card exists, the way PROD-6323 and
PROD-6324 grew theirs. Do not invent such items.

---

## Aura Growth ROI

```markdown
Weekly update of the Aura Growth ROI tracker for the week closing {report_week_closing}, then the Monday insight note to #data-analysis.

Sheet: `Growth ROI tracker: Marketing Performance & CAC LTV Modeling (Aura)`, tab `Overall by channel weekly (22-26)`. Update via `data:update-growth-roi-sheet`, report via `data:report-growth-roi`.

## Update

- [ ] Write the CR columns (AM-AX) for {report_week_label}, and refresh the week-late columns for {report_prev_week_label} (New Subs, CAC, ASP, trial to paid, Stripe and family subs).
- [ ] Check the previous week's row against what was reported last Monday and note any value that moved.

## Report

- [ ] Run the Step 1 restatement diff against last week's note before drafting.
- [ ] Carry forward any open item from last week's note, or close it explicitly.
- [ ] Put the claims to Viktor in #bot-commands before posting; post as user, no footer.

## Context

Last week's card and note: link once known.
```

---

## Aurie Growth ROI

```markdown
Weekly update of the Aurie Growth tracker for the week closing {report_week_closing}, then the Monday insight note to #ai-analytics.

Sheet: `Growth tracker - Aurie`, tab `Overall by Channel, Weekly` (gid 0). Report via `data:report-growth-roi`.

## Update

- [ ] Fill the {report_week_label} row.
- [ ] Confirm UGC `Total spend` (CR) has landed for {report_prev_week_label}; if it was blank last Monday, verify `J = EV + CR` reconciles and note the corrected CAC and CPR. Do not estimate ahead of the column filling.
- [ ] Check the previous week's row against what was reported last Monday and note any value that moved.

## Report

- [ ] Run the Step 1 restatement diff against last week's note before drafting.
- [ ] Report any corrected prior-week CAC or CPR as a one-line revision.
- [ ] Put the claims to Viktor in #bot-commands before posting; post as user, no footer.

## Context

Last week's card and note: link once known.
```

---

## Viktor weekly review

```markdown
Weekly review of Viktor's credit usage and self-optimization changes for Cycle {cycle}, covering the two reports posted to #ai-native-viktor this week: the weekly self-optimization pass and the weekly credit analysis.

## Acceptance criteria

- [ ] Summarize the main cost drivers and week-over-week usage trend (threads, tool calls, top channel).
- [ ] Assess whether the self-optimization changes were sound and whether any need reverting.
- [ ] Identify follow-up actions for remaining blockers, and open a dedicated card for any pattern flagged two weeks running.

## Resources

- Weekly self-optimization post: link once posted.
- Weekly credit analysis post: link once posted.
```
