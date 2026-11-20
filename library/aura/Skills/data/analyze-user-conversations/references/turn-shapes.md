# Turn shapes — reading `output` correctly

`messages_to_turns` in [`scripts/fetch_logs.py`](../scripts/fetch_logs.py) emits one entry per
user→assistant pair. Two of its shapes have a null `content`, and **neither means the assistant
returned nothing**.

| shape | what it means |
| -- | -- |
| `content: null` + **`tool_calls` populated** | the assistant called a tool (e.g. `createInsightCard`) instead of writing text. `tool_calls[].function.name` **is** the turn's output |
| `output: null` entirely | the user sent a message with no assistant reply between it and the next user message. Normal at the tail of a transcript, and mid-transcript when a user sends twice in a row |

## The rule

Check `content` **and** `tool_calls` before calling a turn empty. Reporting a tool-call turn as
"Aurie returned nothing" is a real and repeated failure mode — it has produced a filed product bug
that dissolved on inspection, and a second one filed against this very file.

## Why `content` is not back-filled

It mirrors OpenAI's field verbatim and is deliberately **not** populated with a synthetic
description like `[tool call: createInsightCard]`. Several downstream steps quote `content` as the
assistant's own words — the Slack reply in Step 7, the `Learnings/Conversations` row in Step 8, the
Braintrust dataset case in Step 9. A synthesised string would surface in those as something Aurie
never said, which is a worse failure than the one it would prevent.

So the fix for "a reader mistook a tool call for silence" is this page, not a change to the data.

## Sentinel markers glued to the end of a reply

An Aurie prompt can close a reply with a **sentinel** — a literal marker glued to the end of the
message, e.g. `That feels like a good place to stop.**ENDCONVERSATION**`. The IntelligenceService
strips it from everything it controls (the streamed tokens, every forwarded voice-proxy event, and
the persisted `dialogue.message`) and emits the matching client-side tool call in its place. It
cannot strip **OpenAI's stored copy**, which is the model's raw output — so a transcript fetched
here still carries the marker on the turn that closed the conversation.

A marker in `output.content` is therefore a **completion signal**: not text the user saw, and not a
defect. The user read the sentence without it and the client got the tool call. Don't quote it back
as something Aurie said, and don't write it up as a formatting bug or a prompt leak — strip it from
any excerpt that reaches the report, a Slack reply, or a `Learnings/Conversations` row, and say what
the turn signalled instead if it matters to the analysis.

Same answer as *Why `content` is not back-filled* above, for the same reason: the field keeps
mirroring OpenAI verbatim, and the stripping belongs at the moment of quoting.

**Expect more than one marker.** `**ENDCONVERSATION**` (→ the `endConversation` tool) is the first,
and only the `quickProcessing` prompter opts in, so quick-processing transcripts see it first. The
registry is `conversation.sentinels` in the IntelligenceService's `src/config.js` and the
per-prompter opt-in is `prompt.<prompter>.sentinels` — read a marker-shaped tail on a final
assistant turn as a sentinel and check the registry, rather than matching the one literal. The
service's README documents the mechanism under "Reply sentinels"
([IntelligenceService#462](https://github.com/AuraHealth/IntelligenceService/pull/462)).

## Where the tool-call turn comes from

The `Conversation [Production]` project hosts prompters that emit tool calls as their terminal
action — `createInsightCard` is the common one on `quick-processing-prompter`, typically on the
final turn of a session. A conversation ending in a tool call is ordinary, not an error.
