# Interpreting the comparison

Facts about the IntelligenceService data that change how a number reads. Each one was
learnt on the first live prompt experiment (`coachPrompterWarmth`, IS v1.22.5, Oct 2026).

## Experiment source — where the arm lives

| Field | Meaning |
|---|---|
| `dialogue.prompt->'assignments'->>'<experiment>'` | The arm the user was **assigned** for that turn |
| `dialogue.prompt->>'filename'` | The prompt body that **actually ran** — base `<configKey>.txt`, or the arm's variant file |
| `dialogue.prompt->>'version'` | IS release that served the turn |
| `dialogue.prompt->'messages'->0` | The system message sent to the model. Up to IS 1.35 it holds the prompt and the user's context; from 1.36 only the static prompt, with the context in a later developer message (after the `---` pair from 1.40) |
| `userExperiment` (`id = <userId>-<experiment>`) | The IS's stored assignment; `''` means ExperimentService failed and the user is pinned to the base prompt |

Assigned and ran can disagree: a variant whose `filename` does not exist on disk falls back
to the base prompt with a warning. That is what the "Prompt files served" column catches.

## Experiment source — joins and casing

- `llmCall` is read from BigQuery `llm.llm_call`, aggregated per `dialogueId` and joined to
  `dialogue."id"` as text (text vs uuid).
  Do **not** join on `conversationId`: `llmCall` often carries it **uppercase** (iOS-generated
  ids, chat and voice) while `dialogue` stores it lowercase.
- A failed OpenAI call writes **no** `llmCall` row, so "No llmCall row" counts failures.
- `llmCall.platform`: `livekit` = voice, `mobile` = chat.

## Tool-call turns are two OpenAI calls

When the model calls a tool (memory, guardrails, track search), the turn is two calls: the
first returns the tool call, the tool runs, the second writes the reply. The IS still writes
**one** `llmCall` row for the turn (`toolCalls` > 0, tokens combined) — 869 of 874 tool
turns on the first run. On those turns:

- TTFT includes the first call **and** the tool round trip — about +1.7 s.
- Output tokens cover both calls — about double.
- Reply length is longer too.

A small number of turns carry **several** `llmCall` rows (36 of 11.5k on the first run,
up to 5). The script aggregates per dialogue so they are never double-counted, and reports
them as "Several llmCall rows" — a rising count is worth a look (retries, duplicated replies).
Check the user spread before attributing it to an arm: on the first run all 36 sat on arm a,
but came from 2 users in 5 chat conversations — rapid repeat calls seconds apart, i.e. client
re-sends, not the prompt.

So a higher p90 TTFT on one arm is usually a higher **tool-call share**, not a slower prompt.
Compare arms on the no-tool columns; report the tool-call share separately.

## Reading the style table

- It is keyword matching. Trust gaps that are large **and** consistent across chat and voice.
- Voice replies are shaped by the voice guidance in the prompt; length differences usually
  show in chat only.
- Tie each metric to a stated goal of the candidate (e.g. "reduce 'yeah' openers" → `open_yeah`).
  A metric moving against a goal is the most useful finding the report can make.

## Dataset and QA sources

- **Dataset runs against OpenAI** carry tokens and tool calls but **no latency** — the response
  object has no TTFT. Compare latency on live traffic only.
- **One generation per row is noisy** at temperature > 0. For a decision, run each dataset row
  ≥3 times per group (`eng:qa-conversation` dataset replay does this) and keep the rows' ids as
  `pair_key` so every generation of a row pairs with the other group's.
- **Replayed history is not output.** In a multi-turn dataset row, earlier assistant turns come
  from the dataset; the loader marks them `context` so they don't count toward the group's metrics.
- **QA drives use a simulated user.** Style metrics are comparable between groups in the same run,
  not with live traffic: the simulated user's turns shape the replies.
- **Prefer paired review whenever inputs match.** Side-by-side judgement on the same input removes
  the topic confound that limits single-mode reviews of live traffic.

## Reading the blinded review

- Single mode defaults to 6 conversations per group × channel (24 for two groups and two
  channels) — enough to see style clusters, not enough to call a winner. Say so.
- Paired mode defaults to 20 pairs. Report win counts with ties; a 12–8 split on 20 pairs is not
  a clear win.
- Topic drives style: users who bring relationship analysis get analytical replies on any
  arm. Look at which **cluster** each arm's conversations fall into, not only the means.
- An unblinded read by the orchestrating session is biased; on the first run an unblinded
  8-conversation read and the blinded 24-conversation review pointed in opposite directions.
  Report the blinded result.

## Who to exclude

- Internal and test accounts (`--exclude-user`), e.g. accounts forced into an arm with
  `/experiments/:name/set` for QA.
- Users with an override code (`AURIE-*` — see ExperimentService) are forced into arm `a` and
  are not random. The IS database cannot see the code; if the override population is large,
  exclude those users by id.

## Timing

- Turns on the previous IS version during a rollout carry no assignment and are excluded by
  the experiment filter.
- The default window starts at the first `userExperiment` row; QA assignments made before
  release only widen the scan, they don't add turns.
- Runtime grows with the window: the `dialogue` scan alone is ~60 s at 50k turns, and a full
  `health` run ~2 min. Narrow spot checks with `--since`.
