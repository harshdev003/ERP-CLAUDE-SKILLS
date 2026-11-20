---
name: scan-stray-language
description: >
  Read-only scan of Aurie replies for stray words in a script the user never used, such as an
  Arabic "تحمل" dropped into an English sentence. Reads Braintrust Conversation LLM spans over a
  window (default 7 days) and reports, per model and prompter, replies scanned, replies flagged
  and the rate, plus each flagged reply with a short excerpt and its trace link. Covers Arabic,
  Hebrew, Cyrillic, CJK, Hangul, Devanagari, Thai and Greek. Built to verify the guardrails
  stray-script fix (PROD-7912) after release. Trigger on "scan for stray language", "stray
  non-English words in Aurie replies", "random foreign word in the reply", "check the
  guardrails stray-script fix", "how often does Aurie switch script", or "scan stray script".
  Do NOT use for one conversation (eng:investigate-openai-trace) or for reply quality in general.
metadata:
  version: "1.1.0"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Tools/Braintrust     # BRAINTRUST_API_KEY, BTQL, the Conversation project
    - Compliance/Privacy   # excerpts only: no UIDs, emails or transcripts
---

# scan-stray-language

Measures how often an Aurie reply contains a word in a script the user never used, for example "what you can تحمل" in an English reply ([PROD-7912](https://linear.app/aura-health/issue/PROD-7912)). Read-only.

## Run

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/scan-stray-language/scripts"
python3 "$S/scan.py"                                        # last 7 days
python3 "$S/scan.py" --days 30
python3 "$S/scan.py" --since 2026-10-28                     # e.g. since a release
python3 "$S/scan.py" --model gpt-5.4 --prompter guardrails-handler-prompter
python3 "$S/scan.py" --json /tmp/stray.json                 # also write the full result
```

Needs `BRAINTRUST_API_KEY`; the script reads it from the environment or `~/.claude/settings.json`. There is no MCP path: the scan pages BTQL over hundreds of thousands of spans.

## What it reads

Braintrust, Conversation project, spans with `span_attributes.type = 'llm'` and `span_attributes.name = 'openaiStreamResponse'`, grouped by `metadata.model` and `metadata.prompterName`. These are the **inner** spans: on a guardrails handoff, the outer `streamResponse` span is labelled `coach-prompter`, so it would hide the guardrails prompter.

1. **Denominator:** a `count(1)` of the same spans with no script filter.
2. **Candidates:** the same spans whose output holds a frequent letter of one of the eight scripts (a BTQL `ILIKE` pre-filter), paged by halving any time slice that fills BTQL's 1,000-row limit.
3. **Check, locally, on every candidate:** the text of the input and output. Every string in each is read, because a structured output such as `{"affirmation": ...}` hides its text under arbitrary keys.

## When a reply is flagged

A word is a **stray** when both hold:

- **its script appears nowhere in the span's input** (user messages, context and prompt). A reply to a user writing in that script, or quoting the user's own words, never flags;
- **the use is isolated:** at most 2 words of that script, outside quotes, brackets and bold, with no language name nearby ("the Greek word ...").

The second test is needed because a span's input does not carry the whole conversation. On the reference window the first test alone flagged 9 chat-latest replies that were all deliberate: full replies in Arabic, Russian or Chinese the user had asked for, a Persian song title in quotes, and "the Greek word πίστις (faith)". Those appear in the **Deliberate** column: counted, never flagged and never silently dropped. The threshold and word lists are constants at the top of `scripts/scan.py`.

## Output

A table per model and prompter (replies scanned, flagged, rate, deliberate), then each flagged reply: date, model, prompter, script and words, an excerpt of about 40 characters either side, and the Braintrust trace link.

## Reference result

On 2026-09-27 to 2026-10-27 the scan matches the manual review on [ENG-1659](https://linear.app/aura-health/issue/ENG-1659):

| Model / prompter | Flagged | Trace |
|---|---|---|
| gpt-5.4 / guardrails-handler-prompter | 2 (Arabic تحمل) | `7989adc3…`, `e8a3c904…` |
| gpt-5.4 / quick-processing-prompter | 1 (Hindi बिना) | `7a831c2e…` |
| chat-latest / coach-prompter | 0 (9 deliberate) | |

It also flags one the manual review did not cover: gpt-5.4 / chat-prompter, Hebrew עכשיו (`4455351b…`). The guardrails denominator is 3,804 on that window. The card's 3,909 includes part of 2026-10-27 up to its run time.

**PROD-7912 verification:** after the fix ships, run `--since <release date> --model gpt-5.4 --prompter guardrails-handler-prompter`. The target is 0 flagged.

## Privacy

The output carries no user id, email or conversation id, and no conversation text beyond the excerpt around the flagged word. Refer to a reply by its trace id. Never paste more of a transcript into a card, PR or Slack post.
