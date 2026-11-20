# Follow-up: false positives and over-triggering

**Question:** where does the guardrail fire without need, and on which handoff categories? The answer is a recommendation for `eng:update-intelligence-prompt`, never a change made here.

From the dataset:

- **False positives** — `severity == "false-positive"`, or `reason` in `figure-of-speech`, `fragment-or-unclear`. Group by `category`.
- **Third-party questions** — `subject == "other"` with `reason` in `diagnosis`, `substance-use`, `mental-health-struggle`: the person asked about someone else and got a clinical boundary.
- **Boundary on non-explicit relationship talk** — `category == "boundary"` with `reason != "sexual-content"`.

Read a sample of each group's triggering turns (`turns.txt`) before recommending anything: a reason code says why it fired, not whether firing was wrong. Safety fails toward keeping the guardrail: any alert with `severity == "crisis"` is out of scope for a loosening recommendation.
