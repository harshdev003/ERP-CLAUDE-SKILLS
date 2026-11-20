# Report format

The findings card is often read by someone who did not watch the QA, including leadership.
Keep it short and make every row stand on its own.

## Rules

- **Where first.** Every row starts with a Where cell: "Screen N · short name", "Landing",
  "Sign-up form (after Screen 33)" or "Plan loader". N is the funnel's own N/total counter.
  Say so once at the top of the card.
- **One line per finding.** Problem in a few words, then the fix. No method narration.
- **Quote the exact copy.** Use the on-screen string in quotes, then the replacement.
- **Fix column.** Add one whenever a fix is proposed. The analytics table always has one.
- **Say what was not tested** near the top, for example account creation or payment.
- **State the live status** in one line once Step 6 has run, with the date it was checked.
- **No user data.** Test answers are fine. Never paste a real user's email, name, ID or the
  analytics project token.

## Card layout

```markdown
QA of the {product} web signup on {env} ({link}): landing page, the {total}-screen quiz, the
sign-up form and analytics. "Screen N" matches the N/{total} counter at the top of the quiz.

**Not tested:** {what was skipped}.

**Live ({link}):** {same issues / which differ}, checked {date}.

## 1. Fix before launch
| Where | Problem | Fix |

## 2. Logic bugs
| Where | Problem |

## 3. Copy
| Where | Now | Change to |

## 4. Needs #ai-compliance review
| Where | Claim |

## 5. Analytics
| Where | Problem | Fix |
```

"Fix before launch" holds broken legal links, wrong product names, missing safety
resources and visible typos on core screens. Leave a section out when it is empty.

## Card structure in Linear

| Card | Assignee | Holds |
|---|---|---|
| Summary: "{product} web signup QA pass: dev and live" | The requester | What was covered, decisions taken, what was not tested. Done once the pass ends |
| Findings: "{product} web signup QA: copy, logic, compliance and analytics findings" | Whoever owns the fixes | The tables above |
| Extra issues the requester reports | The requester | Rows with placeholders they fill in |

The summary card goes under the current cycle's `QA [Cycle N]` umbrella. The findings and
extra cards go under the summary card. Check the current cycle with `list_cycles` first.

Nothing is created until the requester confirms. Show the table above filled in with each
card's title, parent, assignee, labels, priority, estimate and status, then wait.

## Chat reply

Lead with the verdict, then the blockers. Keep the rest to the tables. End with what was not
tested. When the requester trims findings, update the card in place by patching rows, and
record any lasting decision in the "Already decided" list of the funnel's reference file.
