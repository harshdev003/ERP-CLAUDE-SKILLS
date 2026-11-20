# Learnings/Conversations row format

The full row payload (fields + page-body markdown template) and the `shared:record-knowledge` invocation shape for the Step 8 write to `AI Knowledge → Learnings → Conversations`. Referenced from `SKILL.md` Step 8b–8c; apply `Compliance/Privacy` masking when composing (OpenAI logs aren't masked at write time).

## Row payload (Step 8b)

```yaml
path: Learnings/Conversations
action: create  # or update if Step 8a found an existing row
fields:
  Title: "<UID-prefix> — <one-line headline>"
  Conversation ID: "<UUID>"
  User ID: "<full Firebase UID>"
  Conversation Date: <ISO date from the OpenAI completion's `created`>
  Rating: <Positive | Negative | Neutral | N/A>
  Source Type: <Cancellation Feedback | Quick Processing Rated | Coaching Rated | Conversation Rated | Chat Message Rated | Manual>
  Conversation Type: <Quick Processing | Coaching | Other>
  Prompt Version: "<e.g. coach-prompter:0.235.4>"
  Vertical: <canonical Audience value | unknown>
  Themes: [<topic tags>]
  Day in Trial: <int when known>
  Slack Thread URL: "<Mode A permalink, or omitted in Mode B>"
  Steering Feedback: "<original cancellation reason / k8bot feedback text, or empty>"
# Page body (markdown):
body: |
  ## Headline
  <one-line summary>

  ## What worked
  <bullets distilled from "Positive interactions" — one short clause per bullet>

  ## What didn't
  <bullets distilled from "Friction moments" — one short clause per bullet, no full quotes>

  ## Hypotheses to validate
  <bullets from "Patterns / hypotheses" — falsifiable shape>

  ## Recommended follow-up
  <bullets from "Recommended follow-up" — concrete next steps>
sources:
  - label: "OpenAI Conversation [Production] — conversationId <UUID-prefix>"
    url: "openai://proj_esFyd4eq2VIAjKUn69X1ZFfk/chat-completions?metadata.conversationId=<UUID>"   # no public deep link; document the project + filter for auditability
    primary: true
  - label: "Slack — source thread"      # Mode A only
    url: "<permalink>"
  # plus Aura API / Mixpanel entries (real or marked "unavailable")
```

## record-knowledge invocation (Step 8c)

```yaml
shared:record-knowledge
  path: Learnings/Conversations
  action: create  # or update
  page_id: <existing row URL, only on update>
  fields: <above>
  sources: <above>
  body_replace_sections: <only on update, with the 5 allowed sections from Recipe C>
```
