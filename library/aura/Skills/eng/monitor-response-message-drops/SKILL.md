---
name: monitor-response-message-drops
description: >
  One monitoring tick for IntelligenceService's `Dropped extra response message items` log
  (a `chat-latest` turn returning 2+ `message` items delivers only the first). Reads prod GCP
  Cloud Logging: rollout split, ≥ERROR on the monitored version, drop count/rate with class
  split and sizes, new WARNING signatures; exits 1 on an openaiService error, a rate over
  threshold, or an empty kept reply. --classify labels each dropped item; --post reports each
  new drop to #ai-conversation-message-drops, deduped. Also counts unknown and retired
  prompter names over 24 h. Trigger on "monitor response message drops", "how many
  multi-message drops", "check the drop rate", "is the message-drop fix holding", "what are we
  dropping from chat-latest", "classify the dropped messages", "post the message drops to
  slack", "unknown prompter names", "is the retired prompter alias still used", or a routine
  tick of the v1.16.9 watch. Do NOT use for generic GCP triage (`eng:investigate-gcp-logs`) or
  to read a single conversation.
metadata:
  version: "1.5.0"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Tools/GCP            # SA, project, GCP_KEY env-var, Cloud Logging query shape
    - Tools/OpenAI         # responseId → GET /v1/responses/{id} for spot-checking a drop
    - Tools/Linear         # optional readout comment on the monitoring card
    - Tools/Slack          # --post: one message per drop to #ai-conversation-message-drops
    - Compliance/Privacy   # text goes to one private channel via --post; everything else stays shape-only
---

# monitor-response-message-drops

You are the on-call watcher for one production change: IntelligenceService `v1.16.9`
([IntelligenceService#495](https://github.com/AuraHealth/IntelligenceService/pull/495),
[ENGBACK-1367](https://linear.app/aura-health/issue/ENGBACK-1367)) delivers only the first
`message` item of an OpenAI Responses turn and logs the rest. This skill answers two
questions on a cadence: **is the fix holding**, and **what are we actually discarding**.
Slow down on the second one — a number is not an answer until you know what it counts.

## Goal

Turn the `Dropped extra response message items` log into a readout that is safe to paste
anywhere: counts, rates, class split, size distribution, response ids. The log carries no
text. The text itself — user turn, kept reply, dropped item — goes to exactly one place,
the private `#ai-conversation-message-drops` channel, via `--post`; see *Where text may go*.

## Inputs

- **since** — required. Window start: an ISO-8601 instant (the deploy time is the usual
  anchor) or a shorthand (`30m`, `4h`, `2d`).
- **version** — optional. `serviceContext.version` to monitor. Defaults to the version with
  the most streaming turns in the **recent** window (`--recent-minutes`, default 30), falling
  back to the whole window — so a rollout watch follows the incoming build without an edit,
  instead of the outgoing one that still dominates the window's total.
- **thresholds** — `--max-rate` (default 15%) and `--min-turns` (default 100): escalate on
  rate only once enough turns exist for the rate to mean something.
- **classify** — `--classify` fetches each dropped response from OpenAI
  (`OPENAI_CONVERSATION_API_KEY`) and labels every extra item `repeat` / `opener` / `read`,
  with a word-overlap score as evidence. `--show-text` additionally prints the kept and
  dropped text **for you at the terminal** — never into a card or any other channel.
- **post** — `--post` reports every not-yet-posted drop to `#ai-conversation-message-drops`,
  one message each (category, sizes, OpenAI link, user turn / kept / dropped, the
  `[dialogue, conversation, user, prompt, model]` line). Implies `--classify`. Posted ids are
  kept in the state file, so overlapping windows never double-post. `--verdicts <file>` hands
  the script your verdict on `read` items and summaries for texts over 600 chars;
  `--post-dry-run` renders without posting. Format and file schema:
  [`references/slack-post-format.md`](references/slack-post-format.md).
- **state** — the previous tick is kept per version under
  `${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/monitor-response-message-drops/` so the
  readout can carry a delta. `--no-state` for a one-off; `--json` for a machine-readable tick.

## Prerequisites

`GCP_KEY` in `~/.claude/settings.json` (the `claude@firebase-auratech16` service account,
`roles/logging.viewer`) and `google-cloud-logging` installed:

```bash
python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/monitor-response-message-drops/scripts/requirements.txt
```

No GCP Logging MCP exists, so this is API-first by necessity. A missing key is a hard fail —
there is nothing to degrade to.

## Workflow

### 1. Run the tick

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/monitor-response-message-drops/scripts/monitor.py \
  --since 2026-09-04T22:05:00Z \          # or 4h, 2d
  --classify                              # label each dropped item; add --show-text to read the pair
  # --version 1.16.9                      # omit to follow the busiest version
```

Every drop in section 3 carries its **OpenAI log link** (`platform.openai.com/logs/<responseId>`),
so the stored response — both items, the user's turn, the timing — is one click away.

Exit `0` is a clean tick; exit `1` means at least one `ESCALATE:` line was printed. The
readout has four numbered sections — the field meanings and the class definitions are in
[`references/log-schema.md`](references/log-schema.md).

### 2. Read the four sections in order

1. **Rollout** — turns per version, whole window and recent. `→ other versions DRAINED`
   appears once nothing but the monitored version has served a turn in the recent window.
   Until then a low drop count means little: most traffic may still be on the old build.
2. **Errors** — any `≥ERROR` on the monitored version, grouped by component. An
   `openaiService` error is what a regression in the drop code would look like.
3. **Drops** — total, rate against streaming turns, and the class split. The pre-fix
   production baseline was **3.9% of all calls, 8.4% of `coach-prompter`**; a rate in that
   band means the model is still doing what it did and the code is catching it. Read the
   sizes too: `keptChars` near zero next to a large `droppedChars` is the failure this
   fix must never produce — the guard escalates on `keptChars == 0` for exactly that reason.
4. **New warnings** — signatures on the monitored version not seen last tick. The drop line
   itself appears here on the first tick after a deploy; anything else is worth a look.

### 3. Decide what the drops are — this is the judgement step

The script decides only what features can decide. `repeat` (same text) and `opener`
(short, greeting-shaped) are safe calls. Everything else is labelled **`read`**, and that
label means exactly what it says: **you** decide, from the text, whether the dropped item is

- a **paraphrase** — the same points restated, often landing on the same closing line; the
  reply is *better* without it (this is what the first two production drops were), or
- a **genuine continuation** — a point, question, or turn the kept reply does not contain;
  this is the real cost of the drop.

Word overlap cannot make that call — the two known paraphrases scored 0.51 and 0.30 — so
do not read `coverage` as a verdict; it is context for how close the two items sit.
Read each `read` pair (the `--post` readout prints every held pair; `--show-text` prints all
of them), decide, and record the outcome as a **count** per verdict on the monitoring card.
A `sign-off` — a one-line closer after a complete reply — is its own verdict, neither
paraphrase nor continuation.

### 3b. Post each drop to the channel

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/monitor-response-message-drops/scripts/monitor.py \
  --since 2h --version 1.16.9 --post                       # posts repeat/opener; HOLDS every `read`
python3 ${CLAUDE_PLUGIN_ROOT}/skills/monitor-response-message-drops/scripts/monitor.py \
  --since 2h --version 1.16.9 --post --verdicts <file>     # posts the held ones with your verdicts
```

The first run posts what the script can decide on its own and prints, under section (5),
each **held** drop with its user turn, kept reply and dropped item. For every held id write
one entry in the verdicts file — `verdict`, an optional one-line `note`, and a `summaries`
block for any of the three texts over 600 chars (short texts post verbatim; never summarize
one that fits). Then re-run with `--verdicts`. Both runs are idempotent: an id already in
the state file's `posted` map is skipped, so a wide `--since` costs nothing.

**Where text may go.** The dropped text and the user id line are posted to
`#ai-conversation-message-drops` and nowhere else — a private channel whose owner decided
this on 2026-09-04 ([ENG-1246](https://linear.app/aura-health/issue/ENG-1246)). Cards, other
channels and this skill's readout carry counts, sizes and ids; the OpenAI link is the
durable pointer to the conversation. Summaries you write carry no names or locations.

### 3c. Prompter names (section 6)

Every tick also counts two IntelligenceService warnings over the last 24 h, on every version,
grouped by `prompterName` (a name, never user data):

- **`Unknown prompter name`** — a caller asked for a prompter that does not exist. Every name
  above 0 prints a `FOLLOWUP decision` line, every tick.
- **`Retired prompter name counted`** — a retired name still served through its alias. The
  count is reported every tick: **0 means the alias can be removed** (Jerome, 2026-11-18). A name
  above 0 prints a `FOLLOWUP fyi` line only the first time; `prompters.json` in the state dir
  remembers it. `chat` (aliased to `coach`) stays by decision, so it is counted and printed but
  never raised (`KEPT_ALIASES` in `monitor.py`).

Raise each `FOLLOWUP` block in the same tick with `shared:raise-followup`, passing its fields
as is: the line gives `--kind`, `--key` and `--title`, and the indented `what:` / `why:` / `ask:`
lines under it give the rest (`--json` carries the same in `followups[]`). Raise from the run
that printed it: a retired name is recorded as raised, so the next run prints nothing for it. The text
carries no count, so the same name on the next tick comes back `unchanged`, not as a new reply.
Keys are the lowercased name plus a short hash of it, so they meet the follow-up key rule.
A `FOLLOWUP` line never stops the cadence; only `ESCALATE:` does.

### 4. Escalate, or report

- **`ESCALATE:` present** — stop the cadence and surface the line to a human with the
  `responseId`s. Do not keep ticking past an empty-reply signal.
- **Clean tick** — report the delta line. For a card update, post counts and ids only;
  `Compliance/Privacy` applies to the response ids' *content*, which stays in OpenAI and in
  the one channel `--post` writes to. A `continuation` verdict is the one outcome that
  changes the decision — the readout flags it with a `NOTE:` line; record the count on the card.

### 5. Running as an hourly routine

One tick per invocation, nothing self-scheduled (the host's scheduler owns the cadence, per
`/CLAUDE.md` → *No cadence skill schedules itself*). Each tick: run `--post` with a
`--since` wider than the interval (`2h` for an hourly routine — dedup makes the overlap
free), read every held pair, write the verdicts file, re-run with `--verdicts`, then record
the tick's verdict counts on the monitoring card. Stop and surface the line to a human on
any `ESCALATE:`; do not keep ticking past it.

## Output

A plain-text readout (or `--json`) of the shape:

```
# intelligence-service 1.16.9 · 2026-09-04T22:20:33Z · window since 2026-09-04T22:05:00Z
(1) turns  1.16.9=159  others: 1.16.8=65   | recent: 1.16.9=159 others: 1.16.8=65
(2) ≥ERROR on 1.16.9: 0
(3) drops: 6  rate 3.8% of 159 turns   items: {'neither': 4, 'opener': 2}
    keptChars min 295 · median 458 · max 1252   droppedChars min 23 · median 317 · max 782   keptChars==0: 0
    · 22:09:00Z  kept  826  https://platform.openai.com/logs/resp_0087da92…
        → read          dropped  704 chars · coverage 0.51 · jaccard 0.32
    · 22:11:19Z  kept 1252  https://platform.openai.com/logs/resp_06099187…
        → read          dropped  782 chars · coverage 0.30 · jaccard 0.15
    feature verdicts: {'read': 4, 'opener': 2}   ('read' = paraphrase vs continuation, decide from the text)
(4) new WARNING signatures on 1.16.9 since last tick: ['Dropped extra response message items']
Δ since 2026-09-04T21:50:00Z: turns +159, drops +6, errors +0
(5) posted to C0BV9DHUT8C: 2 {'opener': 2}   already posted: 0   held: 4
    HELD resp_0087da92…: needs a verdict (read the pair below)
    …
```

Section (5) appears only with `--post`; its held-pair block is the operator's reading list.
Section (6) follows section (4) on every tick:

```
(6) prompter names, last 24 h, all versions:
    unknown: 0
    retired: quickProcessingSentinel=3
FOLLOWUP fyi response-drops:retired-prompter:quickprocessingsentinel-781aa900: The retired prompter name quickProcessingSentinel is still called
    what: The `Retired prompter name counted` warning fired for `quickProcessingSentinel` in the last 24 h, …
```

## Edge cases

- **No turns in the window** — rate prints `—` and the version resolves to `?`. Widen
  `--since`; this is a quiet period or a wrong container, not a clean tick.
- **Two versions both busy** — a canary. Pass `--version` explicitly; the auto-pick follows
  whichever is busier in the recent window and will flip as the split moves.
- **State from an older version** — state is keyed per version, so a new release starts
  with no delta on its first tick. That is expected, not a lost file.
- **`403` from GCP** — the SA lost `logging.viewer` or `GCP_KEY` points at the wrong
  project; `gcp.py` names the principal it authenticated as. Not a signal about the service.
- **A drop is held tick after tick** — its verdicts entry is missing or names a text that
  still needs a summary; the `HELD` line says which. A `slack error:` hold retries on its
  own next tick.
- **A stored response with fewer than two `message` items** — the log said it dropped
  something the stored response does not contain; the drop reads `analysis unavailable` and is never
  posted. Note the id on the card rather than forcing a verdict.

## References

- [`references/log-schema.md`](references/log-schema.md) — the log payload, class
  definitions, and why `neither` is not "new content".
- [`references/slack-post-format.md`](references/slack-post-format.md) — the `--post`
  message, the five categories, the verdicts-file schema, dedup semantics.
- `scripts/slack_post.py` — byte-identical copy of the `shared:post-to-slack` canonical
  (`post_message`, `get_permalink`, script-built footer). Edit the canonical, then propagate.
- `scripts/gcp.py` — byte-identical copy of the `investigate-gcp-logs` canonical (auth
  ladder + Cloud Logging wrapper). Edit the canonical, then propagate; see `/CLAUDE.md`.
- Monitoring card: [PROD-5575](https://linear.app/aura-health/issue/PROD-5575). Prompt-side
  context and datasets: [PROD-5554](https://linear.app/aura-health/issue/PROD-5554). Skill
  cards: [ENG-1245](https://linear.app/aura-health/issue/ENG-1245) (tick),
  [ENG-1246](https://linear.app/aura-health/issue/ENG-1246) (`--post`).
