# Trigger reasons — why did this guardrail fire?

The shared vocabulary for the reason a conversation guardrail fired. `eng:investigate-conversation-guardrail` writes one `Trigger reason:` line per triage reply in it; `data:analyze-conversation-guardrails` classifies untriaged alerts in it and builds a per-alert dataset from both. Follow-up questions (a product fit, a false-positive review, a guardrail eval dataset) are computed from that dataset, so they never need their own pass over the conversations.

The handoff category (`crisis`, `treatment`, `diagnosis`, `boundary`, `substance`, `complex_condition`) is the conversational prompt's routing label. The reason is a reading of what the person actually said, and the two often differ: a `crisis` handoff can be a figure of speech, a `boundary` handoff can be a disclosure of assault.

## What to judge

The **user message that triggered the alert** (the one right before the alert turn), read with the message before it. Not the whole conversation: the triage skill's risk classification covers that.

**When guardrail turns come in a run, judge the first one.** If the previous assistant turn (the one before the alert's user message) is itself a guardrail-handled reply, walk back to the first guardrail-handled reply of that unbroken run and judge the user message before it. A follow-up like "But I'm not gonna do that" is a reply to the guardrail, not what fired it: judged alone it reads `fragment-or-unclear · subject: self`, while the message that fired the run was `harm-to-others · subject: other`.

## Reasons

When the message carries several, pick the **first** that applies in this order.

| Reason | The triggering message… |
|---|---|
| `suicidal-or-self-harm` | expresses wanting to die or disappear, suicidal thoughts (current or recalled), self-harm or urges |
| `harm-to-others` | expresses intent or urges to hurt another person |
| `abuse-or-violence` | describes abuse, domestic violence, threats, coercion, stalking, or violence against the person or someone in their care |
| `sexual-violence` | describes sexual assault, non-consensual sex or sexual abuse, past or present |
| `therapy-or-treatment` | asks for, or about, a therapist, counsellor, psychiatrist, treatment or a therapy modality (EMDR, hypnotherapy, CBT), or about losing a provider |
| `medication` | asks about a medication: dose, switching, stopping, side effects, whether it works |
| `diagnosis` | asks whether someone has a condition ("am I depressed?", "is he a narcissist?") |
| `mental-health-struggle` | describes depression, anxiety or panic, trauma or PTSD, burnout, being unable to function, without asking for treatment |
| `substance-use` | describes drinking, drug use, relapse or quitting |
| `eating-or-body` | describes disordered eating, body image, weight loss or bingeing |
| `sexual-content` | is sexual or explicit content, or asks for it |
| `physical-health` | is about physical symptoms, illness, injury or non-psychiatric care |
| `figure-of-speech` | uses death or violence language that plainly isn't meant ("kill me now", "it killed me") |
| `fragment-or-unclear` | carries no content of its own ("Yes", "There's more", a pasted message with nothing in it) |
| `other` | none of the above |
| `unknown` | no transcript (not-analysable only) |

## Attributes

- **subject** — `self` (about the person), `other` (about someone else: a partner's drinking, a child's depression, "is he a narcissist?") or `unclear`.
- **asked for** — `professional-help` (asked for, or how to reach, a professional or treatment), `clinical-advice` (asked Aurie itself for a diagnosis, a dose, a treatment plan) or `none`.
- **already in care** — the person says they currently see a therapist, counsellor or psychiatrist.

## The line

One line, plain text, so it can be parsed back from Slack:

```
Trigger reason: <reason> · subject: <self|other|unclear> · asked for: <professional-help|clinical-advice|none>[ · already in care]
```

Examples:

```
Trigger reason: medication · subject: self · asked for: clinical-advice · already in care
Trigger reason: diagnosis · subject: other · asked for: clinical-advice
Trigger reason: figure-of-speech · subject: self · asked for: none
Trigger reason: unknown · subject: unclear · asked for: none
```

Categorical only: never quote or paraphrase the message to justify the reason.
