# The idea schema — what step 8 emits

A JSON array at a path you name, in the shape `product:record-ideas` reads. Every field
except the ones marked optional is required for a proposal to count as fully formed.

```json
[
  {
    "proposedBy": "claude",
    "scope": "screen | feature | new",
    "portfolio": "evidence | adjacent | moonshot",
    "betSize": "small | medium | large",
    "confidence": "high | medium | low",
    "evidenceGrade": "A | B | C | D",
    "screen": "<screen id, e.g. settings_screen — for `new`, the screen that opens it, or the new key it will need>",
    "domain": "<one of the Domain select values, verbatim>",
    "category": "feature | friction | ui | copy | navigation | onboarding | paywall | retention | notification",
    "lever": "conversion | activation | retention | engagement",
    "impact": "high | medium | low",
    "effort": "S | M | L",
    "title": "<short imperative, under 60 chars>",
    "description": "<the user problem and the proposed change, under 60 words. For `new`: the job, the belief, and the smallest version that tests it>",
    "rationale": "<the mechanism — why this moves the metric — and the citations: which learning, experiment, rows or capture, and which lens found it>",
    "hypothesis": "If we <change>, then <metric> moves <direction> because <mechanism>.",
    "variants": ["b: control — <what users see today>", "a: <the change>", "c: <optional second arm>"],
    "expectedImpact": {
      "trial": "<up | down | flat> — <why, one clause>",
      "paid": "<up | down | flat> — <why, one clause>",
      "session": "<up | down | flat> — <why, one clause>",
      "retention": "<up | down | flat> — <why, one clause>",
      "risk": "<the metric this could cost, and how. Never empty.>"
    },
    "testPlan": {
      "primaryMetric": "<one experiment-dashboard metric — see below>",
      "direction": "up | down",
      "guardrails": ["<a metric this could hurt>", "<a second one>"],
      "instrumentation": "<how exposure is recorded — the event or flag that marks a user as having seen the change>",
      "decisionWindow": "<earliest read, and the sample limitation that bounds it>",
      "falsification": "<the result that would mean the belief was wrong>"
    },
    "priorArt": "<REQUIRED when anything read touches this lever or job: which row or experiment, and what is different about this version. Empty string when nothing does.>"
  }
]
```

## Field rules

- `proposedBy` is always `"claude"` from this skill. It is how the loop compares this route
  with Viktor's — never leave it off.
- `domain` must be a `Domain` select value verbatim — `onboarding`, `paywall`, `home/feed`,
  `conversation`, `journal`, `grounding`, `affirmations`, `profile`, `settings`,
  `notifications`, `growth/referral`, `other`. A variant spelling lands the row in the wrong
  column and out of the next run's priors.
- `scope`, `portfolio`, `betSize`, `confidence`, `evidenceGrade`, `impact`, `effort` are
  strict enums. The recorder validates and refuses the batch rather than guessing.
- `direction` is a direction, **never an invented lift**. "Paid +12%" is a number nobody
  measured.
- `variants` letters arms as the experiment will: **`b` is control** (the existing screen),
  treatments are `a`, `c`, `d` …, so "ship c" means the same arm on the row, the design
  sheet and the experiment. It names the control and at least one arm. A `new` idea's control is "no such
  surface".
- `decisionWindow` is honest about the sample: a change seen by 3% of trials will not read
  in a week.

## Metrics

Pick `primaryMetric` and `guardrails` from what the experiment dashboard reads. The
definitions and their traps live in `data:analyze-experiment` — do not redefine them here.

| Family | Metrics |
|---|---|
| Conversion | onboarding registration, paywall or modal → trial, trial → paid, paid subscription rate, revenue per assigned user, annual/monthly plan mix |
| Retention | Day 1, Week 1, M1 renewal and cancellation, WAPU, repeat sessions |
| Quality | session rating, conversation helpfulness, PMF "very disappointed" |
| Risk and economics | paid cancellation, refunds, cost-to-serve, margin, safety and privacy |

`expectedImpact` stays the coarse five-way prediction every row carries; `testPlan` is how
this experiment would actually be read. **Do not predict SUL** — it is who was assigned, not
what the change did — and never report a rate over 100% as a lift.

## Then hand it over

This skill does not write. `record-ideas` does, after a `plan` the user reads:

```bash
python3 <record-ideas>/scripts/record_ideas.py plan  --from-json <path>
python3 <record-ideas>/scripts/record_ideas.py apply --from-json <path> --confirm
```
