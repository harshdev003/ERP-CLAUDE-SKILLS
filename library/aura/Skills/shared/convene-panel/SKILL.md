---
name: convene-panel
description: >
  Convene a multi-persona panel: spawn 3–7 Claude agents with distinct
  personas (defaults: Iris, Pete, Quinn, Ada, Cass — systems thinker,
  pragmatist, edge case finder, user advocate, contrarian), have them debate a
  topic over several rounds via a shared chat.json, then return a synthesised
  answer. Trigger on "convene a panel", "convene panel", "call the panel",
  "run a panel", "panel debate", "agent chat room", "multi-agent debate",
  "have agents debate this", "brainstorm with personas", "creative session",
  "spawn agents to discuss", or whenever the user wants multiple perspectives
  weighed against each other rather than a single-pass answer. Do NOT use for:
  single-question answers, factual lookup, or code review (use
  `eng:review-pr`).
metadata:
  version: "3.0.0"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
---

# convene-panel

## Goal

Produce a synthesised answer to an open-ended problem by orchestrating a
debate between several Claude agents, each with a distinct persona, over
multiple rounds in a shared `chat.json`. Return the synthesis inline and
preserve the full debate on disk for later inspection.

## Inputs

The skill is invoked with a free-form `args` string. Parse:

- **Topic** (required): the problem under debate. May be inline text, or a
  path reference like `@path/to/file.md` which you must read and substitute
  in. Multi-paragraph topics are fine.
- **Personas** (optional, default = 5 defaults): named personas to convene.
  If the user names personas, parse a comma-separated list. Defaults are
  resolved by name to entries in [`references/personas.md`](references/personas.md);
  any unrecognised name is treated as a *custom persona* per the rules in
  that file.
- **Rounds** (optional, default = 3): maximum debate rounds. Convergence
  detection may end the session earlier.
- **Output directory** (optional, default = `outputs/convene-panel/<ISO-timestamp>/`
  in the user's CWD).

If the topic is missing, ask before proceeding. Don't guess.

## Workflow

### 1. Resolve roster and topic

- Parse the args string into `{topic, personas[], rounds, output_dir}`.
- Resolve `@file` references in the topic by reading those files.
- Build the persona roster from three pools in
  [`references/personas.md`](references/personas.md):
  - **Default cast** (Iris, Pete, Quinn, Ada, Cass) — copy `name`, `role`,
    and `brief` verbatim. This is the default roster when the user names
    no personas.
  - **Specialised personas** (Bo, Wren, Sam, Mae) — pre-briefed for
    Aura/Aurie review work (compliance, brand voice, ICP1, crisis-edge).
    Use the hardcoded brief when the user names them.
  - **Custom personas** — when the user names a *role* not covered above
    (e.g. "an SRE," "a security lawyer"), pick a name from the alternate
    pool and auto-generate a brief per the template in `personas.md`.
- **Specialised-persona suggestion heuristic.** If the topic is shaped like
  a review (ad creative, landing page, paywall, onboarding flow, brand
  artifact, marketing copy, strategy deck, compliance question, anything
  user-facing for Aura/Aurie), propose swapping in the relevant specialised
  personas before showing the plan. Common pairings:
  - **Ad / landing page / paywall review:** Bo + Wren + Sam + Mae (replace
    or augment the default cast — usually replace, since the default cast
    debates well in the abstract but doesn't carry the Aurie-specific
    lenses these reviews need).
  - **Brand voice / copy review:** Wren + Ada (and Cass to attack the
    framing).
  - **Compliance question:** Bo as primary, plus Iris for second-order
    effects, Cass to argue the strongest case for shipping anyway.
  - **Strategy / positioning deck:** Iris + Pete + Cass + Wren.
  - The suggestion is a recommendation, not a default — the user can
    accept, swap personas, or override with the original cast.
- Validate: 3 ≤ N ≤ 7. If the user requested fewer or more, follow the
  pushback rules in `references/personas.md`.

### 2. Confirm the panel with the user (blocking gate)

This is a hard stop — never start spawning agents until the user confirms
the panel composition. Spec principle 4: show the plan before any side
effects.

Show the user a one-screen plan:

```
Topic:    <one-line summary>
Personas: Iris (systems-thinker), Pete (pragmatist), Quinn (edge-case-finder),
          Ada (user-advocate), Cass (contrarian)
Rounds:   up to 3 (may end earlier on convergence)
Output:   outputs/convene-panel/<ISO-timestamp>/
```

If the topic triggered a specialised-persona suggestion in step 1, show
both the default panel and the recommended swap, and call out *why*:

```
Topic: Critique the bloom-of-light Aurie ad creative.

Default panel: Iris, Pete, Quinn, Ada, Cass — debates well in the abstract
but won't catch Aurie-specific risks (compliance, brand voice, ICP1).

Recommended for this topic:
  • Bo  (compliance-counsel) — flags banned phrases, FTC framing
  • Wren (brand-voice-guardian) — Aurie ≠ Aura, banned wellness clichés
  • Sam  (icp1-silent-fail-advocate) — speaks for the Quietly Overloaded
                                        Professional
  • Mae  (crisis-edge-reviewer) — reviews against worst-case audience

Pick one: [default] / [recommended] / [custom roster]
```

If any custom persona auto-briefs were generated, show them too:

```
Custom briefs:
- Eva (security-lawyer): "You are Eva, the security lawyer. You push for…"
```

**Then explicitly ask:** *"Does this panel work for the topic, or want to
swap personas before I kick off?"* — and wait for confirmation. Accept
"go" / "yes" as confirmation. Accept overrides like *"swap Pete for Sam,"*
*"add Bo,"* *"use the recommended panel,"* *"drop Cass."* Re-show the plan
after any change.

Only after explicit confirmation, write a `personas.json` file in the
output dir with the resolved roster (each entry: `{name, role, brief}`)
and call:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/convene-panel/scripts/chat_log.py \
  init "<output_dir>" \
  --topic "<topic>" \
  --personas "<output_dir>/personas.json" \
  --rounds <N>
```

The script returns the path to the new `chat.json`.

### 3. Round 1 — parallel cold takes

Each persona speaks once, in parallel. They see only the topic and their own
brief — not each other's turns yet.

Spawn N `Agent` tool calls **in a single message** (so they execute in
parallel), one per persona, with `subagent_type: general-purpose` and a
self-contained prompt of this shape:

```
[universal persona instructions — copy verbatim from references/personas.md]

[this persona's brief — verbatim from references/personas.md or auto-generated]

# Other personas in this session

The other personas you may address by name or yield to are:
- <Name1> (<role1>)
- <Name2> (<role2>)
- …

Only these names are valid yield targets. If you yield, use `[yield →
<Name>]` with a name from this list (not your own).

# Topic

<topic>

# Your task

This is round 1. You have not seen any other persona's turn yet. Give your
independent opening take on the topic — 4–10 sentences. End with one of
[pass], [done], or [yield → <Name>] only if it genuinely applies; otherwise
just end your turn.
```

Collect the N returns. Build a batch JSON file (one entry per return) and
append all turns atomically:

```json
[
  {"name": "Iris", "round": 1, "content": "…", "yield_to": null, "i_am_done": false},
  {"name": "Pete", "round": 1, "content": "…", "yield_to": null, "i_am_done": false},
  …
]
```

Parse trailing markers in each return per [Marker parsing](#marker-parsing)
below. Then:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/convene-panel/scripts/chat_log.py \
  append-batch "<chat_path>" --batch "<batch.json>"
```

Print a one-line progress marker to the user: *"Round 1/N complete — 5
turns recorded."*

### 4. Convergence check

After every round (including round 1), spawn one `Agent` call to judge
convergence:

```
Read this conversation. Decide whether the personas have converged on a clear
answer to the topic. Return strict JSON only:

{"converged": true|false, "reason": "<one sentence>"}

Convergence means the personas substantially agree on the recommended action
or framing, with disagreement only on minor details. Disagreement on the
core question is not convergence.

# Topic
<topic>

# Conversation
<rendered chat.json — output of `chat_log.py render <chat_path>`>
```

Persist the verdict:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/convene-panel/scripts/chat_log.py \
  set-convergence "<chat_path>" --round <k> --converged <true|false> --reason "<…>"
```

If `converged: true`, skip to step 6 (synthesis). Print *"Convergence
detected after round k — moving to synthesis."*

### 5. Rounds 2..N — sequential with yield/pass/done

Each subsequent round is sequential. Personas see all prior turns including
the current round.

**Turn order within the round:**

1. Eligible personas = roster minus anyone who set `i_am_done` in any prior
   round.
2. First turn of round k:
   - If the last turn of round k-1 had `yield_to: <Name>` (and that persona
     is still eligible), they go first.
   - Otherwise, round-robin starting from the persona *after* the last
     speaker of round k-1 (in the original roster order).
3. Subsequent turns of round k:
   - If the previous turn had `yield_to: <Name>` (eligible, hasn't gone yet
     this round) → that persona goes next.
   - Else if `yield_to: pass` → skip the next persona in roster order.
   - Else (yield_to is null, "any", or the named persona already went this
     round) → next eligible persona in roster order who hasn't spoken this
     round.
4. Round complete when every eligible persona has either spoken once or been
   skipped via `pass`.

**Per turn:**

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/convene-panel/scripts/chat_log.py \
  render "<chat_path>" --include-briefs > /tmp/cs-render.md
```

Spawn one `Agent` call (`subagent_type: general-purpose`) with this prompt:

```
[universal persona instructions]

[this persona's brief]

# Other personas in this session

The other personas you may address by name or yield to are:
- <Name1> (<role1>)
- <Name2> (<role2>)
- …

Only these names are valid yield targets. If you yield, use `[yield →
<Name>]` with a name from this list (not your own).

# Topic
<topic>

# Conversation so far
<contents of /tmp/cs-render.md>

# Your task

This is round <k>. The previous turn was by <previous-name>. Give your turn
now — 4–10 sentences. Address other personas by name when reacting to their
points. End with [pass], [done], or [yield → <Name>] only if it genuinely
applies.
```

Parse the return per [Marker parsing](#marker-parsing) and append:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/convene-panel/scripts/chat_log.py \
  append "<chat_path>" \
  --name <Name> --round <k> \
  --content-file <tmp-content-file> \
  [--yield-to <Name|pass>] [--done]
```

After the round completes, run convergence check (step 4). If converged,
skip to synthesis.

Edge cases:

- **All personas mark `i_am_done`** before round N → skip to synthesis.
- **An Agent call fails or returns empty** → record a turn with content
  `"[no response — agent call failed]"` and continue. Don't block.
- **Persona yields to themselves or a non-existent name** → fall back to
  round-robin order, log a one-line note in the user-facing progress.

### 6. Synthesis

Spawn one `Agent` call (`subagent_type: general-purpose`) with this prompt:

```
You are a neutral synthesiser. Read the multi-persona debate below and
produce a synthesis structured exactly as:

## Consensus
- Bullet points the personas substantially agreed on.

## Divergent
- Points the personas disagreed on. For each, state the strongest version of
  each side in one sentence.

## Outliers
- Single-persona ideas worth a second look — name the persona, summarise the
  idea, note why it's worth flagging.

## Recommended next steps
- 3–5 concrete actions, ordered by leverage.

# Topic
<topic>

# Conversation
<rendered chat.json>
```

Persist:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/convene-panel/scripts/chat_log.py \
  set-synthesis "<chat_path>" --content-file <tmp-synthesis-file>
```

### 7. Return to the user

Print the synthesis inline (the four sections, as markdown). Append a
closing line:

> Full debate saved to `<output_dir>/chat.json` (M turns across K rounds).

## Marker parsing

Each persona's return is plain text. Inspect only the **last non-empty line**
for these markers (case-sensitive, exact form):

| Marker | Meaning | Effect on chat.json |
|---|---|---|
| `[pass]` | Persona has nothing useful this round | Record turn as-is; `i_am_done=false`, `yield_to=null` |
| `[done]` | Persona considers their job complete | Record turn; set `i_am_done=true` |
| `[yield → <Name>]` | Hand off to a specific persona | Record turn; set `yield_to=<Name>` |

Multiple markers may appear (e.g. `[done] [yield → Iris]`); treat them as
orthogonal flags. Markers anywhere except the last non-empty line are
ignored.

Always store the persona's full text (including markers) in the turn's
`content` — don't strip them. Markers are extracted only to populate the
turn's metadata fields.

## Output

Inline (returned in the orchestrator's response):

```markdown
## Consensus
- …

## Divergent
- **<question>** — Side A: …; Side B: …

## Outliers
- **<persona>** — …

## Recommended next steps
1. …
2. …

> Full debate saved to outputs/convene-panel/<timestamp>/chat.json
> (M turns across K rounds).
```

On disk:

```
outputs/convene-panel/<ISO-timestamp>/
├── personas.json   # resolved roster (name, role, brief)
└── chat.json       # full session: topic, personas, turns, convergence, synthesis
```

## Edge cases

- **Topic missing or ambiguous** → ask, don't guess.
- **N < 3** → push back; debate collapses below 3 personas.
- **N > 7** → push back; per-round token cost grows superlinearly.
- **`@file.md` reference fails to resolve** → ask the user to confirm the
  path before proceeding.
- **Re-running the skill on the same args** → creates a fresh timestamped
  output dir; no shared state across runs (idempotent by construction).
- **User interrupts mid-session** → the partial `chat.json` on disk is still
  valid; on resume, just point them to the file. v1 doesn't auto-resume
  in-progress sessions.

## Configuration knobs (v1)

| Knob | Default | How to override in args |
|---|---|---|
| Personas | 5 defaults | "with Iris, Pete, and a security lawyer" |
| Rounds (max) | 3 | "for 5 rounds", "up to 2 rounds" |
| Output dir | `outputs/convene-panel/<ISO>/` in CWD | "save to <path>" |

## References

- [`references/personas.md`](references/personas.md) — default cast, brief
  template for custom personas, alternate name pool, roster sizing rules.
- [`scripts/chat_log.py`](scripts/chat_log.py) — chat.json state management.
- Pattern source: ["Agent chat rooms" — YouTube @ 1:07:43](https://youtu.be/EsTrWCV0Ph4?t=4063).
