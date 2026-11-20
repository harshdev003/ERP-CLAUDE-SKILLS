---
name: prompt-knowledge
description: >
  What the propose leg reads before it drafts a prompt change: the prompter's architecture,
  its deployed configuration, its change log, and the rejected-hypothesis register — what has
  already been tried and what it cost. Owns the register; `digest-conversation-signals` and
  `record-prompt-candidates` carry mirrored copies and enforce it. Use when the user says
  "what do I need to know before changing the coach prompt", "has this been tried", "check
  this against the register", "why was that rejected", "what model is the coach prompter on",
  or before drafting any candidate. Do NOT use to write a candidate row
  (`record-prompt-candidates`), to draft a diff (`draft-prompt-candidate`), or to decide what
  evidence a change needs (`evaluate-prompt-candidate`).

metadata:
  version: "0.2.1"
knowledge:
  reads:
    - Guide/Index                          # baseline routing aid (universal)
    - Learnings/Loop/Aurie Conversations Ideas   # the register's human mirror
    - Tools/Braintrust                     # where the change log lives
---

# prompt-knowledge

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/prompt-knowledge/scripts/prompt_knowledge.py"
IS="$HOME/Development/Aura/Services/IntelligenceService"

python3 "$S" brief coach --is-repo "$IS"         # all four, before drafting
python3 "$S" register                            # what is settled
python3 "$S" register --entry REG-1              # one entry in full
python3 "$S" check "ask fewer questions per turn"   # exit 1 if it is answered
python3 "$S" config-drift --is-repo "$IS"        # snapshot vs the live config
python3 "$S" process                             # the nine stages, and who does each
python3 "$S" process --stage 4                   # one stage in full
```

## The propose leg does not start from a blank page

Four things, in the order the process document fixes them. The first three say what the prompt **is**. The fourth is the one that saves the week.

1. **Architecture** — the sections and what each is load-bearing for, in dependency order: identity → pacing → tier logic → guardrails → tone, with `tools` outside that chain. A change to a later layer cannot fix a defect that originates in an earlier one; that mismatch is the usual shape of a structural diagnosis.
2. **Deployed configuration** — model, `openaiAPI`, `reasoningEffort`, `verbosity`, tools. A behaviour change is as often a model change as a prompt change: when the Conversation prompt moved from gpt-5.2-chat-latest to GPT-5.4 the body was untouched and question frequency fell from 100% of turns to about 43%.
3. **Change log** — the Braintrust version history with its activity labels, and the IS release that carried each one.
4. **The register** — what has already been tried, what it cost, and what would reopen it.

## The section map is read, the config is recorded

The prompts run to 1500 lines and this loop changes them weekly, so `brief` reads the live `.txt` and annotates its headings rather than trusting a copy. A section list stored in this skill would be wrong within a fortnight and would be trusted anyway.

The **configuration** is recorded, because its entire value is the comparison. "A behaviour change with no prompt change is a model change until proven otherwise" is only answerable against a previously recorded value, so `references/prompters.yaml` holds a dated snapshot and `config-drift` compares it against the live `src/config.js`. When it drifts, update the snapshot in the same change that reads the drift — a snapshot nobody refreshes answers the question wrongly rather than not at all.

Ten prompters are in scope: `coach` (serves chat **and** voice), `companion`, `chat` (near-dormant in production — recorded as such rather than dropped), `onboarding`, `quickProcessing`, `quickProcessingSentinel`, `quickProcessingDone`, `guardrailsHandler`, `userGuardrail`, `assistantGuardrail`.

## The register is the asset that survives a departure

Six entries, four verdicts, and every one of them established by measurement:

| Verdict | What it means |
|---|---|
| `hard_stop` | A candidate proposing this is **refused with the reason quoted**, not debated. |
| `check_first` | Not a refusal. A precondition that must be answered before the candidate can be diagnosed at all. |
| `rewrite_required` | The aim may be sound; the form proposed is known not to work. |
| `known_fyi` | Measured and accepted. Not a defect unless Product says so. |

The three hard stops are the obvious response to a real, loudly reported defect. That is what makes them expensive: the defect is real, and the obvious response is the one that was measured and failed.

`check` is pattern matching, not judgement. It catches phrasings that have actually been proposed; a paraphrase nobody has written yet gets through. **Read the register as well as running the check** — that is what step 4 means.

## Who enforces it

`record-prompt-candidates` refuses to write a candidate that restates a hard stop, and **`--force` does not clear that refusal**. The documented way past an entry is its own `reopens` condition: meet it, retire the entry here, and the refusal goes with it. An override flag on a measured result would make the register advisory, which is the state it exists to end.

`digest-conversation-signals` builds its hard-stop readings from the register instead of a hand-kept dict, so a theme whose obvious response is known-wrong is printed with the reading and kept out of the emitted JSON.

Both carry **byte-identical copies** of `references/register.yaml` and of the reader, because a skill may not import across plugins or skills. `tests/test_prompt_register.py` fails if any copy drifts. Add or retire an entry **here**, then copy it to both.

## Adding an entry

An entry needs the measurement, not the opinion: what was proposed, what was measured, what it cost, what to do instead, and what would reopen it. An entry without a `reopens_if` is a prejudice with a version number. Give it `patterns` that match how the hypothesis is actually phrased when someone proposes it — the phrasing in the candidate that prompted the entry is the best available sample.

## The nine-stage process, mapped

The prompt-engineering process is human-led and predates the loop. `references/process_map.yaml` says, for each of its nine stages, whether a leg runs it, a leg assists a human at it, or a human does it — and which gate that human sits behind.

| | Stage | | Legs |
|---|---|---|---|
| 1 | Product alignment | loop + human | digest → record → share |
| 2 | Roadmap generation | **human** | — |
| 3 | Phased implementation | loop + human | draft |
| 4 | Failure diagnosis and repair | **human** | digest, prompt-knowledge |
| 5 | Overriding model training | **human** | prompt-knowledge |
| 6 | Multi-turn testing | loop | evaluate |
| 7 | Single-turn testing | loop | evaluate |
| 8 | Human testing and final review | **human** | share, harvest |
| 9 | Publication and monitoring | loop + human | ship, learn |

Stage 2 has no leg and says so. The loop ships one change against one defect; a phased roadmap is what a multi-week upgrade needs, and that is the declared exception to the weekly cadence rather than the ordinary case. When a candidate turns out to need one, say so at Gate 1 rather than shipping the first phase and discovering the rest.

`automated` means a leg runs the stage. It never means nobody reads the result — every stage records what stays human, the testing stages included.

## Judgment no leg may assume

Four things the process document assigns to a person. Each names the stage it belongs to, why it is not automated, and the gate it sits behind:

- **Architectural judgment** — local versus structural diagnosis. AI fixes the instance it is shown and is unreliable at telling whether that instance is a symptom. Gate 1, via the Diagnosis field that `share-prompt-candidate` refuses to post without.
- **Final editorial authority** over every word. Generated revisions silently compress language and introduce changes nobody asked for. Gate 2, via the diff.
- **Clinical and relational quality** — whether it felt like Aurie. The voice rows ceiling at 4.90–5.00 across six consecutive measurements; they cannot separate arms on the thing this judgment is about. Gate 2, by reading transcripts.
- **Model-fit calibration.** The loop detects that the model moved (REG-4); it cannot judge what the new model's defaults do to this prompt's intent. Gate 1, via the change class.

A test asserts none of the four sits on a stage the map calls automated. That contradiction is how a leg ends up assuming one.

The process document's own note on pace is carried with them: a complex upgrade may take two weeks or more, and that slowness is the process working correctly. The weekly target is for the ordinary case — a named defect, a scoped change, a measured result — and is not a licence to accelerate past diagnosis.
