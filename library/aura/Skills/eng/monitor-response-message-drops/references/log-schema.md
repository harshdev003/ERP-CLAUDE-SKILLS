# The `Dropped extra response message items` log line

Emitted by IntelligenceService `src/services/openai.js` (both `callResponses` and
`streamCallResponses`) since `v1.16.9`, once per turn in which OpenAI's Responses API
returned more than one `message` item in `output[]`. Severity `WARNING`.

## Payload

Shape only. There is no text field, by design: the log ships to Cloud Logging and the text
is a user's conversation. The `responseId` is the handle back to the full reply in OpenAI.

| field | type | meaning |
|---|---|---|
| `droppedCount` | int | number of `message` items discarded (everything after the kept one) |
| `keptChars` | int | length of the text delivered to the user |
| `dropped[]` | list | one entry per discarded item |
| `dropped[].chars` | int | length of the discarded item's text |
| `dropped[].duplicate` | bool | discarded text is **byte-equal** to the kept text |
| `dropped[].looksLikeOpener` | bool | first 60 chars match the opener regex (`hey / hi / hello / i'm here / still here with …`) |
| `dropped[].id` or `outputIndex` | string / int | the item's `msg_…` id (non-streaming) or its `output_index` (streaming) |
| `responseId` | string | the OpenAI `resp_…` id; `GET /v1/responses/{id}` returns every item |
| `component` | string | `openaiService.callResponses` or `openaiService.streamCallResponses` |
| `serviceContext.version` | string | the IntelligenceService build that logged it |

## The three classes the readout reports

`monitor.py` folds each `dropped[]` entry into one of three buckets:

- **exact duplicate** — `duplicate: true`. The reported defect: the same sentence twice,
  spoken twice by TTS.
- **opener** — `looksLikeOpener: true`. A session greeting emitted mid-conversation
  (*"Hey, I'm here with you."*). Second most common shape before the fix, and the one the
  prompt exemplar in `coach-prompter` reproduces near-verbatim.
- **neither** — everything else.

### Why `neither` is not "new content"

The pre-fix analysis called this bucket "genuine continuations" and put it at ~74% of
multi-message turns. That label was inferred from the *absence* of an exact match, and it
is too strong. The first two production drops on `v1.16.9` were both **paraphrases** of the
kept item — the same three or four points restated in different words, in one case ending
on the identical closing line — and `duplicate: false` on both because the wording differed.

So `neither` is a bucket to *investigate*, not a loss to *report*. When the question is
what the drop is costing, spot-check a handful of `responseId`s against OpenAI and record
a count per verdict:

`monitor.py --classify` fetches the stored response and attaches, per extra item, a feature
verdict plus two overlap scores. **Only two verdicts are the script's to make:**

| script label | rule | reliable? |
|---|---|---|
| `repeat` | kept and dropped are equal once whitespace/punctuation are ignored | yes |
| `opener` | under 160 chars and the first 60 match the greeting/presence regex | yes |
| `read` | anything else | **this is a hand-off, not a verdict** |

`coverage` (share of the dropped item's content words already present in the kept reply)
and `jaccard` are printed as evidence. They do **not** decide paraphrase vs continuation:
the two production drops read as paraphrases by hand scored **0.51** and **0.30**, because a
paraphrase restates the *points*, not the *words*. A threshold that caught them would also
catch real continuations on the same topic. So the split below is made by reading:

| verdict | what you see in the two items |
|---|---|
| paraphrase | same points, reworded; often the same closing line |
| genuine continuation | item 1 adds a point, question, or turn item 0 does not contain |
| opener | a greeting or presence line, regardless of the regex |
| repeat | byte-equal or trivially so (punctuation / whitespace) |

Record the counts on the monitoring card. Never paste the text.

## Escalation semantics

| condition | why it escalates |
|---|---|
| any `≥ERROR` from a component starting `openaiService` on the monitored version | the drop code is the only new thing in that module; an error there is a regression until proven otherwise |
| drop rate > `--max-rate` once turns ≥ `--min-turns` | far above the 3.9% / 8.4% baseline means the model changed behaviour or the guard is over-firing |
| any drop with `keptChars == 0` | an empty reply reached the user — the exact failure the textless-claim guard exists to prevent |

A rate *below* baseline does not escalate: it is either a quiet period or a model change in
the good direction, and both are visible in the turn counts.

## Baselines (pre-fix, for comparison)

Measured 2026-09-04 over 998 Responses-API calls across 17 prompters:

- 39 / 998 multi-message turns — **3.9%** of all calls, **8.4%** of `coach-prompter`, 0 elsewhere
- by shape: 29 "continuation" (see above), 7 opener, 3 exact duplicate
- by model: `chat-latest` 32 / 337 (9.5%); `gpt-5.4-2026-03-05` 0 / 391

Full context: [PROD-5554](https://linear.app/aura-health/issue/PROD-5554) (prompt side)
and [ENGBACK-1367](https://linear.app/aura-health/issue/ENGBACK-1367) (fix and release).

## Prompter-name warnings (section 6)

Two `WARNING` lines, each with one field, `prompterName` (capped at 64 chars by the service;
the script masks anything outside `[A-Za-z0-9_.:-]`):

| `jsonPayload.message` | Meaning | Shipped in |
|---|---|---|
| `Unknown prompter name` | A caller named a prompter IntelligenceService does not have | IntelligenceService#944 (1.49.21) |
| `Retired prompter name counted` | A retired name served through its alias | IntelligenceService#959 |
