# Scenario: QA a `quickProcessingSentinel` run

A verified end-to-end walkthrough of the sentinel flow, captured from a real
production run on 2026-08-31 (IntelligenceService v1.16.6). Use it as the
reference shape when driving or reviewing a sentinel QA run — every response
below is real, trimmed only for length.

The sentinel prompter differs from every other prompter in **how it ends** and
**where its completion screen comes from**. Those two things are what this
scenario exists to exercise; the rest of the loop is identical to
[`SKILL.md`](../SKILL.md) Steps 1–7.

## What a passing run looks like

| # | Check | Expected |
|---|---|---|
| 1 | `/ask` accepts the prompter | HTTP 200, no `DIALOGUE_INVALID_PROMPTER_NAME` |
| 2 | The arc progresses | each turn advances — cost, stakes, then clarity |
| 3 | Close signal | `toolCalls[0].function.name == "endConversation"` |
| 4 | Marker is stripped | no `**ENDCONVERSATION**` anywhere in the visible text |
| 5 | No mid-conversation insight | `createInsightCard` never fires |
| 6 | Completion screen | `/end --quick-processing-done` returns all four keys |
| 7 | `insight` stays null | `/end` ran the QP-done branch, not the insight branch |

A run that ends on turn cap instead of `endConversation` has **not** exercised
the sentinel. Re-drive it with a more cooperative persona rather than recording
a pass.

## Driving it

Use a persona with one concrete stressor who answers the questions rather than
deflecting. The arc needs the user to name the thing and feel its cost; a terse
or evasive persona will hit the turn cap first. Five turns was enough here.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chat-with-aurie/scripts/aura_chat.py \
  ask "$CONVERSATION_ID" --message-file /tmp/turn.txt \
  --prompter quickProcessingSentinel
```

The shape of the drive, turn by turn:

1. **Open with the stressor.** "Running on empty all week, keep promising myself
   the weekend, the weekend disappears too."
2. **Name what it is costing.** Aurie asks what feels most at risk; answer with
   something concrete — snapping at a partner, not who I want to be.
3. **Let the fear surface.** "Scared this is becoming my normal."
4. **Make the stakes specific.** A named person and a finite window works well —
   a six-year-old who will only be six once.
5. **Acknowledge the shift.** "I needed to hear it out loud." This is the turn
   that closed the arc.

## The close signal

On the final turn Aurie delivers the closing move as ordinary text and the
response carries:

```json
{"toolCalls": [{
  "id": "fc_…", "type": "function", "index": 0, "callId": "call_…",
  "function": {"name": "endConversation", "arguments": "{\"reason\":\"sentinel\"}"}
}]}
```

Three things to assert on:

- `function.name` is **`endConversation`**, not `createInsightCard`.
- `arguments` parses to `{"reason": "sentinel"}` — no `affirmation`, no
  `trackId`. Nothing to surface in the readout from the toolCall itself.
- `body.message` is **not** null here, unlike the `quickProcessing`
  `createInsightCard` close. The closing move is real user-visible text and the
  marker has already been stripped from it. Seeing a literal `**ENDCONVERSATION**`
  in the message is a bug in the streaming filter, not a prompt problem.

The dual `id` (`fc_…`) / `callId` (`call_…`) is normal on the direct `/ask`
path. It is the same shape behind the ElevenLabs tool-id re-fire issue
(ENGBACK-1263), so watch it specifically when the same prompter is driven
through the ElevenLabs agent rather than through `/ask`.

## The completion screen

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chat-with-aurie/scripts/aura_chat.py \
  end "$CONVERSATION_ID" --quick-processing-done
```

```json
{
  "insight": null,
  "quickProcessingDone": {
    "header": "You made space for this, <givenName>!",
    "oldLine": "Running on empty has started to feel like it could become my normal.",
    "newLine": "Saying this fear out loud can make its cost clearer, even before anything changes.",
    "rememberQuote": "I can let this matter before another weekend disappears."
  }
}
```

`insight: null` is the pass condition, not a gap — it proves `/end` took the
`createQuickProcessingDone` branch. If `insight` is populated and
`quickProcessingDone` is null, `--quick-processing-done` did not reach the
server and the run tested the wrong path.

The four keys are the mobile contract and are camelCase. `header` interpolates
the user's given name; per
[Compliance/Privacy](https://www.notion.so/35f75e23af20813ea4e7f4165c9e0844)
redact it before pasting a transcript anywhere outside the run.

## Gotchas seen on the first run

- **The `AURA_USER_TOKEN` expiry warning is expected.** The script warns that
  `exp` has long passed and proceeds; the server persists the JWT and accepts
  it. Only an actual 401/403 means the token needs refreshing.
- **`title` and `previewMessage` stay null** on the `/end` response for this
  flow. The summary prompter populates those separately, off the end-side-effect
  path — not a sentinel failure.
