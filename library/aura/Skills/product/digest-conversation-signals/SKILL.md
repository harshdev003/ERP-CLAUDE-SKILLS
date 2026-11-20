---
name: digest-conversation-signals
description: >
  Rank conversation defects from the signal databases — ratings, cancellations and conversation
  patterns — into a readable weekly digest and into candidate JSON for
  `record-prompt-candidates`. The Conversations loop's ideation leg: where the Features loop
  walks the app with a sim test, this reads what users actually said. Ranks and does not
  diagnose; every emitted candidate carries `diagnosis: not yet diagnosed` on purpose. Use when
  the user says "run the digest", "what should we fix in the prompt", "rank the conversation
  defects", "what are users complaining about", or "what's the weekly signal". Do NOT use to
  write rows (pipe into `record-prompt-candidates`) or to analyse one conversation.

metadata:
  version: "0.2.2"
knowledge:
  reads:
    - Guide/Index                          # baseline routing aid (universal)
    - Learnings/Loop/Aurie Conversations Ideas    # the register's hard stops, the lifecycle
    - Signals/Aurie Conversations           # the rating corpus the cadences populate
    - Tools/Notion                          # NOTION_TOKEN + the REST access pattern
---

# digest-conversation-signals

```bash
P="${CLAUDE_PLUGIN_ROOT}/skills"
S="$P/digest-conversation-signals/scripts/digest_conversation_signals.py"
R="$P/record-prompt-candidates/scripts/record_prompt_candidates.py"

python3 "$S" --days 30 rank                        # the readable digest
python3 "$S" --days 30 --top 5 emit | python3 "$R" plan --from-stdin
```

Both paths resolve from `$CLAUDE_PLUGIN_ROOT`. A relative hop to the sibling skill resolves against the current directory, so it works from this skill's `scripts/` and nowhere else.

Global flags come **before** the subcommand: `--days`, `--top`, `--min-rows`, `--data-source-id`.

## It ranks; it does not diagnose

Every emitted candidate carries `diagnosis: "not yet diagnosed"` and no change class, so `share-prompt-candidate` **refuses to post it** until a human supplies both. That refusal is the discipline, not a gap.

The loop's most expensive available mistake is laundering the loudest thing users say into a proposed change. `question_saturation` is the second-ranked theme in the current corpus at 98% negative — and lowering the question rate is the response the evidence already falsified.

## The register's hard stops are carried inline

A theme whose obvious response is known-wrong is printed with the settled reading and **kept out of the emitted JSON**, because recorded and shared it would only be refused at Gate 1 for a reason the register already holds:

```
question_saturation            46   45  98%   HARD STOP

  HARD STOP · question_saturation
    Question rate is NOT the variable — forward motion is. The build with the
    highest question rate was not the rejected one; the rejected one stalled…
```

`HARD_STOPS` is built from `references/register.yaml` — a byte-identical mirror of the register owned by `product:prompt-knowledge`, checked by `tests/test_prompt_register.py`. The register is the loop's most load-bearing knowledge and lives in one place: add or retire an entry in `product:prompt-knowledge` and copy it here in the same change.

A `known_fyi` entry prints as `KNOWN` rather than `HARD STOP` and is **not** kept out of the emitted JSON. `forced_choice` is the case: the share was measured and Product accepted it, so surfacing it as a stop would teach the reader to discount the label, and dropping it would let it be re-raised as a discovery.

## What it refuses to rank

**Subject themes.** `grief`, `anxiety`, `loneliness`, `self_worth` and their siblings name what the *user* brought, not anything Aurie did. Ranking them proposes "fix grief".

**Themes already covered.** A theme named in an open candidate's text shows its row and is excluded from `emit`; one named in an *answered* candidate is flagged `ANSWERED` with the reason. Coverage is a **literal** match on the theme token across the row's title, defect, evidence and cause — vocabulary overlap was tried and failed both ways, missing the row that answers `coping_tools_gap` because its title says "practical-help gap", and matching `first_conversation` against an unrelated row on the word "conversation".

## Two things the output says about itself

**Tagging coverage.** The digest prints what share of rated conversations in the window carry any theme, and warns below 60% that this is a *ranking of what was tagged*, not a trend. Coverage has been discontinuous before — one month had 0 of 271 rows tagged — and a rank read as a trend across that gap is meaningless.

**Cancellations are context, not a ranking input.** The reason distribution is printed beneath the table and never scored. The complaint is bidirectional: the same corpus carries "asked 1000 questions and no help" *and* "there was no back and forth", so a reason code cannot say which direction a given user meant.

## Sources

Two databases the analysis cadences already populate — **Conversations** (ratings and themes) and **Cancellations** — plus the loop's own database for the coverage check.

**Conversation Patterns is deliberately not read.** Its themes are topical — `relationship_attachment`, `parenting_family`, `grief_loss` — describing what users *brought*, not what Aurie did, so ranking them would propose "fix parenting". It is a coverage corpus, useful for asking whether a change reaches the subjects people actually raise. It is not a defect source, and wiring it in as one would make the ranking worse. All addressed by **data source id**, not database id: under Notion API `2025-09-03` a query against a database id 404s. Resolve a new one with `GET /v1/databases/<id>` and read `data_sources[].id`.

## Setup

`NOTION_TOKEN`. `notion_rest.py` is a per-skill copy of the canonical.

## Related

- `record-prompt-candidates` — consumes `emit` output
- `share-prompt-candidate` — refuses anything this emits until it has a diagnosis
- The register and its hard stops: `Learnings/Loop/Aurie Conversations Ideas`
